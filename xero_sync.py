#!/usr/bin/env python3
"""
Xero -> Postgres sync, multi-tenant.

Iterates over org.units WHERE xero_tenant_id IS NOT NULL. For each
tenant: loads the refresh token from finance_<slug>.tokens, refreshes
via Xero, persists the rotated token, and upserts the tenant's data
into finance_<slug>.* tables. Every SQL statement schema-qualifies
its tables explicitly so we don't rely on session search_path (which
is unsafe under PgBouncer transaction-mode pooling).

Per-entity failures are isolated within a tenant — a single broken
endpoint does not block the rest of that tenant's run. Per-tenant
failures are isolated across tenants. The exit code is non-zero only
if at least one (entity, tenant) pair failed.

Usage:
    python xero_sync.py                              # all tenants
    python xero_sync.py --slug mgl                   # one tenant (debug)
    python xero_sync.py --force-full-resync          # weekly journal resync
    python xero_sync.py --force-full-invoice-resync  # full invoice resync
"""

import argparse
import json
import logging
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone

import psycopg2
from psycopg2 import sql
from psycopg2.extras import execute_batch
import requests
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
)
logger = logging.getLogger(__name__)

SLUG_RE = re.compile(r'^[a-z][a-z0-9_]{0,19}$')


def _utcnow():
    """Naive UTC datetime, matching the 'timestamp without time zone' columns.

    The Xero API's UpdatedDateUTC filter is interpreted as UTC, so every
    timestamp we store and compare against the API must also be UTC.
    Using local time (_utcnow()) silently skews the incremental
    watermark by the timezone offset and causes recent updates to be
    excluded from incremental syncs.
    """
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _open_shared_db_conn():
    """Open the single shared DB connection from non-prefixed DB_* env."""
    db_host = os.getenv('DB_HOST')
    db_port = os.getenv('DB_PORT', '5432')
    db_name = os.getenv('DB_NAME')
    db_user = os.getenv('DB_USER')
    db_password = os.getenv('DB_PASSWORD')
    missing = [
        k for k, v in {
            'DB_HOST': db_host, 'DB_NAME': db_name,
            'DB_USER': db_user, 'DB_PASSWORD': db_password,
        }.items() if not v
    ]
    if missing:
        raise RuntimeError(f'Missing required env vars: {", ".join(missing)}')
    return psycopg2.connect(
        host=db_host, port=db_port, dbname=db_name,
        user=db_user, password=db_password,
    )


def _list_tenants(db_conn, slug=None):
    """Return [(unit_id, slug, xero_tenant_id), ...] for tenants to sync."""
    with db_conn.cursor() as cur:
        if slug is not None:
            cur.execute(
                'SELECT unit_id, slug, xero_tenant_id FROM org.units '
                'WHERE slug = %s AND xero_tenant_id IS NOT NULL',
                (slug,),
            )
        else:
            cur.execute(
                'SELECT unit_id, slug, xero_tenant_id FROM org.units '
                'WHERE xero_tenant_id IS NOT NULL ORDER BY slug'
            )
        return cur.fetchall()


class XeroSync:
    def __init__(self, unit, db_conn, force_full_invoice_resync=False):
        """Initialise sync for one tenant.

        Args:
            unit: tuple (unit_id, slug, xero_tenant_id) from org.units.
            db_conn: shared psycopg2 connection. Every SQL statement
                qualifies its tables with self.schema explicitly.
            force_full_invoice_resync: ignore the invoice watermark.
        """
        self.unit_id, self.slug, self.tenant_id = unit
        if not self.slug or not SLUG_RE.match(self.slug):
            raise ValueError(
                f'Invalid slug "{self.slug}" for unit {self.unit_id}.'
            )
        if not self.tenant_id:
            raise ValueError(
                f'Unit {self.unit_id} ({self.slug}) has no xero_tenant_id. '
                f'Bootstrap with get_refresh_token.py --tenant {self.slug}.'
            )

        prefix = self.slug.upper()
        self.client_id = os.getenv(f'{prefix}_XERO_CLIENT_ID')
        self.client_secret = os.getenv(f'{prefix}_XERO_CLIENT_SECRET')
        if not self.client_id or not self.client_secret:
            raise ValueError(
                f'Missing {prefix}_XERO_CLIENT_ID and/or '
                f'{prefix}_XERO_CLIENT_SECRET for slug={self.slug}.'
            )

        self.db_conn = db_conn
        self.batch_size = int(os.getenv('SYNC_BATCH_SIZE', '10'))
        self.force_full_invoice_resync = force_full_invoice_resync

        # Every SQL statement schema-qualifies its tables explicitly.
        # We do NOT rely on `SET search_path` because the production DB
        # is fronted by PgBouncer in transaction mode, which can hand
        # subsequent transactions to a different physical backend that
        # has lost the session's search_path. Slug is validated above,
        # so f-string interpolation is safe.
        self.schema = f'finance_{self.slug}'

        self.refresh_token = None
        self.access_token = None
        self.access_token_expires_at = None

    # --- date / auth helpers ---

    def _parse_xero_date(self, date_string):
        """Parse Xero's /Date(timestamp)/ format to Python datetime."""
        if not date_string:
            return None
        match = re.match(r'/Date\((\d+)([+-]\d{4})?\)/', str(date_string))
        if match:
            timestamp_ms = int(match.group(1))
            return datetime.fromtimestamp(
                timestamp_ms / 1000, tz=timezone.utc
            ).replace(tzinfo=None)
        return date_string

    def _is_token_expired(self, buffer_minutes=0):
        if not self.access_token_expires_at:
            return True
        threshold = _utcnow() + timedelta(minutes=buffer_minutes)
        return self.access_token_expires_at <= threshold

    def _load_tokens_from_db(self):
        try:
            cursor = self.db_conn.cursor()
            cursor.execute(f"""
                SELECT refresh_token, access_token, access_token_expires_at
                FROM {self.schema}.tokens
                ORDER BY updated_at DESC
                LIMIT 1
            """)
            row = cursor.fetchone()
            if row and row[0] and row[0] != 'PLACEHOLDER':
                db_refresh_token, db_access_token, db_expires_at = row
                if db_refresh_token != self.refresh_token:
                    logger.info(f'[{self.slug}] Loaded refresh token from DB')
                    self.refresh_token = db_refresh_token
                if db_access_token and db_expires_at and db_expires_at > _utcnow():
                    self.access_token = db_access_token
                    self.access_token_expires_at = db_expires_at
                    logger.info(
                        f'[{self.slug}] Loaded cached access token '
                        f'(expires at {db_expires_at:%Y-%m-%d %H:%M:%S})'
                    )
        except Exception as e:
            logger.warning(f'[{self.slug}] Failed to load tokens from DB: {e}')

    def _save_tokens_to_db(self):
        try:
            cursor = self.db_conn.cursor()
            cursor.execute(f"""
                INSERT INTO {self.schema}.tokens
                (refresh_token, access_token, access_token_expires_at, updated_at)
                VALUES (%s, %s, %s, NOW())
            """, (self.refresh_token, self.access_token, self.access_token_expires_at))
            self.db_conn.commit()
            logger.info(f'[{self.slug}] Saved rotated refresh token to DB')
        except Exception as e:
            logger.warning(f'[{self.slug}] Failed to save tokens to DB: {e}')

    def get_access_token(self, force_refresh=False):
        """Get a valid access token, refreshing via Xero if needed."""
        try:
            if not force_refresh and self.access_token and not self._is_token_expired():
                logger.debug(f'[{self.slug}] Using cached access token')
                return self.access_token

            # Reload refresh token in case it was rotated by another run
            self._load_tokens_from_db()

            response = requests.post(
                'https://identity.xero.com/connect/token',
                data={
                    'grant_type': 'refresh_token',
                    'client_id': self.client_id,
                    'client_secret': self.client_secret,
                    'refresh_token': self.refresh_token,
                },
                timeout=10,
            )
            response.raise_for_status()
            token_data = response.json()
            self.access_token = token_data['access_token']

            new_refresh_token = token_data.get('refresh_token')
            if new_refresh_token:
                self.refresh_token = new_refresh_token

            expires_in = token_data.get('expires_in', 1800)
            self.access_token_expires_at = _utcnow() + timedelta(seconds=expires_in)

            self._save_tokens_to_db()

            logger.info(
                f'[{self.slug}] Obtained access token '
                f'(expires {self.access_token_expires_at:%Y-%m-%d %H:%M:%S})'
            )
            return self.access_token
        except Exception as e:
            logger.error(f'[{self.slug}] Failed to get access token: {e}')
            raise

    def _make_xero_request(self, endpoint, params=None, retry_count=0, auth_retry=False):
        if not self.access_token or self._is_token_expired(buffer_minutes=5):
            logger.info(f'[{self.slug}] Access token expiring soon, refreshing...')
            self.get_access_token(force_refresh=True)

        headers = {
            'Authorization': f'Bearer {self.access_token}',
            'Xero-Tenant-ID': str(self.tenant_id),
            'Accept': 'application/json',
        }
        url = f'https://api.xero.com/api.xro/2.0/{endpoint}'

        try:
            response = requests.get(url, headers=headers, params=params, timeout=30)

            if response.status_code == 429:
                if retry_count < 3:
                    logger.warning(
                        f'[{self.slug}] Rate limit hit. '
                        f'Waiting 60s before retry {retry_count + 1}/3...'
                    )
                    time.sleep(60)
                    return self._make_xero_request(endpoint, params, retry_count + 1, auth_retry)
                raise Exception('Rate limit exceeded after 3 retries')

            if response.status_code == 401 and not auth_retry:
                logger.warning(f'[{self.slug}] 401 Unauthorized — refreshing token and retrying')
                self.get_access_token(force_refresh=True)
                return self._make_xero_request(endpoint, params, retry_count, auth_retry=True)

            response.raise_for_status()
            return response.json()
        except requests.exceptions.HTTPError as e:
            if e.response.status_code not in (429, 401):
                logger.error(f'[{self.slug}] Xero API request failed for {endpoint}: {e}')
            raise
        except Exception as e:
            logger.error(f'[{self.slug}] Xero API request failed for {endpoint}: {e}')
            raise

    # --- entity syncs ---

    def sync_tracking_categories(self):
        logger.info(f'[{self.slug}] Starting tracking_categories sync...')
        start_time = _utcnow()
        try:
            response = self._make_xero_request('TrackingCategories')
            categories = response.get('TrackingCategories', [])
            if not categories:
                logger.info(f'[{self.slug}] No tracking categories to sync')
                self._log_sync('tracking_categories', 0, 'success', None, start_time)
                return 0

            cursor = self.db_conn.cursor()
            category_insert = f"""
                INSERT INTO {self.schema}.xero_tracking_categories
                (tracking_category_id, name, status, unit_id, synced_at)
                VALUES (%s, %s, %s, %s, NOW())
                ON CONFLICT (tracking_category_id) DO UPDATE SET
                    name = EXCLUDED.name,
                    status = EXCLUDED.status,
                    unit_id = EXCLUDED.unit_id,
                    synced_at = NOW()
            """
            option_insert = f"""
                INSERT INTO {self.schema}.xero_tracking_options
                (tracking_option_id, tracking_category_id, name, status,
                 is_deleted, is_archived, synced_at)
                VALUES (%s, %s, %s, %s, %s, %s, NOW())
                ON CONFLICT (tracking_option_id) DO UPDATE SET
                    tracking_category_id = EXCLUDED.tracking_category_id,
                    name = EXCLUDED.name,
                    status = EXCLUDED.status,
                    is_deleted = EXCLUDED.is_deleted,
                    is_archived = EXCLUDED.is_archived,
                    synced_at = NOW()
            """

            total_options = 0
            for category in categories:
                category_id = category.get('TrackingCategoryID')
                cursor.execute(category_insert, (
                    category_id,
                    category.get('Name'),
                    category.get('Status'),
                    self.unit_id,
                ))
                for option in category.get('Options', []):
                    cursor.execute(option_insert, (
                        option.get('TrackingOptionID'),
                        category_id,
                        option.get('Name'),
                        option.get('Status'),
                        option.get('IsDeleted', False),
                        option.get('IsArchived', False),
                    ))
                    total_options += 1

            self.db_conn.commit()
            logger.info(
                f'[{self.slug}] Synced {len(categories)} tracking categories '
                f'with {total_options} options'
            )
            self._log_sync('tracking_categories', len(categories), 'success', None, start_time)
            return len(categories)
        except Exception as e:
            self.db_conn.rollback()
            logger.error(f'[{self.slug}] tracking_categories sync failed: {e}')
            self._log_sync('tracking_categories', 0, 'failed', str(e), start_time)
            raise

    def sync_accounts(self):
        logger.info(f'[{self.slug}] Starting accounts sync...')
        start_time = _utcnow()
        try:
            response = self._make_xero_request('Accounts')
            accounts = response.get('Accounts', [])
            if not accounts:
                logger.info(f'[{self.slug}] No accounts to sync')
                self._log_sync('accounts', 0, 'success', None, start_time)
                return 0

            cursor = self.db_conn.cursor()
            insert_query = f"""
                INSERT INTO {self.schema}.xero_accounts
                (account_id, code, name, account_type, description,
                 enable_payments, status, updated_at, unit_id, synced_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())
                ON CONFLICT (account_id) DO UPDATE SET
                    code = EXCLUDED.code,
                    name = EXCLUDED.name,
                    account_type = EXCLUDED.account_type,
                    description = EXCLUDED.description,
                    enable_payments = EXCLUDED.enable_payments,
                    status = EXCLUDED.status,
                    updated_at = EXCLUDED.updated_at,
                    unit_id = EXCLUDED.unit_id,
                    synced_at = NOW()
            """
            data = [(
                a.get('AccountID'),
                a.get('Code'),
                a.get('Name'),
                a.get('Type'),
                a.get('Description'),
                a.get('EnablePayments', False),
                a.get('Status'),
                self._parse_xero_date(a.get('UpdatedDateUTC')),
                self.unit_id,
            ) for a in accounts]
            execute_batch(cursor, insert_query, data, page_size=100)
            self.db_conn.commit()

            logger.info(f'[{self.slug}] Synced {len(accounts)} accounts')
            self._log_sync('accounts', len(accounts), 'success', None, start_time)
            return len(accounts)
        except Exception as e:
            self.db_conn.rollback()
            logger.error(f'[{self.slug}] accounts sync failed: {e}')
            self._log_sync('accounts', 0, 'failed', str(e), start_time)
            raise

    def sync_tax_rates(self):
        logger.info(f'[{self.slug}] Starting tax_rates sync...')
        start_time = _utcnow()
        try:
            response = self._make_xero_request('TaxRates')
            tax_rates = response.get('TaxRates', [])
            if not tax_rates:
                logger.info(f'[{self.slug}] No tax rates to sync')
                self._log_sync('tax_rates', 0, 'success', None, start_time)
                return 0

            cursor = self.db_conn.cursor()
            insert_query = f"""
                INSERT INTO {self.schema}.xero_tax_rates
                (tax_type, name, display_tax_rate, effective_rate, status,
                 can_apply_to_assets, can_apply_to_equity, can_apply_to_expenses,
                 can_apply_to_liabilities, can_apply_to_revenue, unit_id, synced_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())
                ON CONFLICT (tax_type) DO UPDATE SET
                    name = EXCLUDED.name,
                    display_tax_rate = EXCLUDED.display_tax_rate,
                    effective_rate = EXCLUDED.effective_rate,
                    status = EXCLUDED.status,
                    can_apply_to_assets = EXCLUDED.can_apply_to_assets,
                    can_apply_to_equity = EXCLUDED.can_apply_to_equity,
                    can_apply_to_expenses = EXCLUDED.can_apply_to_expenses,
                    can_apply_to_liabilities = EXCLUDED.can_apply_to_liabilities,
                    can_apply_to_revenue = EXCLUDED.can_apply_to_revenue,
                    unit_id = EXCLUDED.unit_id,
                    synced_at = NOW()
            """
            data = [(
                t.get('TaxType'),
                t.get('Name'),
                float(t.get('DisplayTaxRate') or 0),
                float(t.get('EffectiveRate') or 0),
                t.get('Status'),
                t.get('CanApplyToAssets', False),
                t.get('CanApplyToEquity', False),
                t.get('CanApplyToExpenses', False),
                t.get('CanApplyToLiabilities', False),
                t.get('CanApplyToRevenue', False),
                self.unit_id,
            ) for t in tax_rates]
            execute_batch(cursor, insert_query, data, page_size=100)
            self.db_conn.commit()

            logger.info(f'[{self.slug}] Synced {len(tax_rates)} tax rates')
            self._log_sync('tax_rates', len(tax_rates), 'success', None, start_time)
            return len(tax_rates)
        except Exception as e:
            self.db_conn.rollback()
            logger.error(f'[{self.slug}] tax_rates sync failed: {e}')
            self._log_sync('tax_rates', 0, 'failed', str(e), start_time)
            raise

    def sync_contacts(self):
        logger.info(f'[{self.slug}] Starting contacts sync...')
        start_time = _utcnow()
        try:
            all_contacts = []
            page = 1
            max_pages = 200
            while page <= max_pages:
                logger.info(f'[{self.slug}] Fetching contacts page {page}...')
                response = self._make_xero_request(
                    'Contacts', params={'page': page, 'pageSize': 100}
                )
                contacts = response.get('Contacts', [])
                if not contacts:
                    break
                all_contacts.extend(contacts)
                page += 1
                time.sleep(1)

            if not all_contacts:
                logger.info(f'[{self.slug}] No contacts to sync')
                self._log_sync('contacts', 0, 'success', None, start_time)
                return 0

            cursor = self.db_conn.cursor()
            insert_query = f"""
                INSERT INTO {self.schema}.xero_contacts
                (contact_id, name, email_address, phones, addresses,
                 tax_number, contact_status, updated_at, unit_id, synced_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())
                ON CONFLICT (contact_id) DO UPDATE SET
                    name = EXCLUDED.name,
                    email_address = EXCLUDED.email_address,
                    phones = EXCLUDED.phones,
                    addresses = EXCLUDED.addresses,
                    tax_number = EXCLUDED.tax_number,
                    contact_status = EXCLUDED.contact_status,
                    updated_at = EXCLUDED.updated_at,
                    unit_id = EXCLUDED.unit_id,
                    synced_at = NOW()
            """
            data = [(
                c.get('ContactID'),
                c.get('Name'),
                c.get('EmailAddress'),
                json.dumps(c.get('Phones', [])),
                json.dumps(c.get('Addresses', [])),
                c.get('TaxNumber'),
                c.get('ContactStatus'),
                self._parse_xero_date(c.get('UpdatedDateUTC')),
                self.unit_id,
            ) for c in all_contacts]
            execute_batch(cursor, insert_query, data, page_size=100)
            self.db_conn.commit()

            logger.info(f'[{self.slug}] Synced {len(all_contacts)} contacts')
            self._log_sync('contacts', len(all_contacts), 'success', None, start_time)
            return len(all_contacts)
        except Exception as e:
            self.db_conn.rollback()
            logger.error(f'[{self.slug}] contacts sync failed: {e}')
            self._log_sync('contacts', 0, 'failed', str(e), start_time)
            raise

    def _build_invoice_item_data(self, invoice, item):
        """Build the row tuple for one invoice line, including tracking IDs."""
        item_id = f'{invoice.get("InvoiceID")}_{item.get("LineItemID")}'
        tracking1_name = tracking1_option = None
        tracking1_category_id = tracking1_option_id = None
        tracking2_name = tracking2_option = None
        tracking2_category_id = tracking2_option_id = None
        tracking_list = item.get('Tracking', []) or []
        if len(tracking_list) > 0:
            t = tracking_list[0]
            tracking1_name = t.get('Name')
            tracking1_option = t.get('Option')
            tracking1_category_id = t.get('TrackingCategoryID')
            tracking1_option_id = t.get('TrackingOptionID')
        if len(tracking_list) > 1:
            t = tracking_list[1]
            tracking2_name = t.get('Name')
            tracking2_option = t.get('Option')
            tracking2_category_id = t.get('TrackingCategoryID')
            tracking2_option_id = t.get('TrackingOptionID')
        return (
            item_id,
            invoice.get('InvoiceID'),
            item.get('Description'),
            float(item.get('Quantity', 0)),
            float(item.get('UnitAmount', 0)),
            item.get('TaxType'),
            float(item.get('TaxAmount', 0)),
            float(item.get('LineAmount', 0)),
            item.get('AccountCode'),
            item.get('AccountID'),
            tracking1_name,
            tracking1_option,
            tracking1_category_id,
            tracking1_option_id,
            tracking2_name,
            tracking2_option,
            tracking2_category_id,
            tracking2_option_id,
        )

    def sync_invoices(self):
        """Sync invoices and line items with batch commits and incremental support."""
        logger.info(f'[{self.slug}] Starting invoices sync...')
        start_time = _utcnow()
        sync_type = 'invoices'
        try:
            progress = self._get_sync_progress(sync_type)
            start_page = progress['last_page'] + 1 if progress['status'] == 'running' else 1
            use_incremental = (
                not self.force_full_invoice_resync
                and progress['last_modified'] is not None
                and progress['status'] == 'completed'
            )
            if use_incremental:
                dt = progress['last_modified']
                modified_after = (
                    f'{dt.year},{dt.month:02d},{dt.day:02d},'
                    f'{dt.hour:02d},{dt.minute:02d},{dt.second:02d}'
                )
                logger.info(
                    f'[{self.slug}] Incremental invoice sync (changes since '
                    f'{dt:%Y-%m-%d %H:%M:%S})'
                )
            elif start_page > 1:
                logger.info(f'[{self.slug}] Resuming invoice sync from page {start_page}')

            self._update_sync_progress(sync_type, page=start_page, status='running')

            invoice_insert = f"""
                INSERT INTO {self.schema}.xero_invoices
                (invoice_id, invoice_number, contact_id, invoice_type, status,
                 line_amount_types, invoice_date, due_date, expected_payment_date,
                 reference, branding_theme_id, sub_total, total_tax, total,
                 currency_code, updated_at, unit_id, synced_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())
                ON CONFLICT (invoice_id) DO UPDATE SET
                    invoice_number = EXCLUDED.invoice_number,
                    status = EXCLUDED.status,
                    total = EXCLUDED.total,
                    sub_total = EXCLUDED.sub_total,
                    total_tax = EXCLUDED.total_tax,
                    unit_id = EXCLUDED.unit_id,
                    synced_at = NOW()
            """
            item_insert = f"""
                INSERT INTO {self.schema}.xero_invoice_items
                (invoice_item_id, invoice_id, description, quantity, unit_amount,
                 tax_type, tax_amount, line_amount, account_code, account_id,
                 tracking1_name, tracking1_option, tracking1_category_id, tracking1_option_id,
                 tracking2_name, tracking2_option, tracking2_category_id, tracking2_option_id,
                 synced_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())
                ON CONFLICT (invoice_item_id) DO UPDATE SET
                    line_amount = EXCLUDED.line_amount,
                    tracking1_name = EXCLUDED.tracking1_name,
                    tracking1_option = EXCLUDED.tracking1_option,
                    tracking1_category_id = EXCLUDED.tracking1_category_id,
                    tracking1_option_id = EXCLUDED.tracking1_option_id,
                    tracking2_name = EXCLUDED.tracking2_name,
                    tracking2_option = EXCLUDED.tracking2_option,
                    tracking2_category_id = EXCLUDED.tracking2_category_id,
                    tracking2_option_id = EXCLUDED.tracking2_option_id,
                    synced_at = NOW()
            """

            cursor = self.db_conn.cursor()
            total_synced = 0
            page = start_page if not use_incremental else 1
            max_pages = 2000
            batch_records = []

            def flush_batch(records, last_page):
                nonlocal total_synced
                if not records:
                    return
                invoice_count = 0
                item_count = 0
                for invoice in records:
                    invoice_data = (
                        invoice.get('InvoiceID'),
                        invoice.get('InvoiceNumber'),
                        invoice.get('Contact', {}).get('ContactID'),
                        invoice.get('Type'),
                        invoice.get('Status'),
                        invoice.get('LineAmountTypes'),
                        self._parse_xero_date(invoice.get('Date')),
                        self._parse_xero_date(invoice.get('DueDate')),
                        self._parse_xero_date(invoice.get('ExpectedPaymentDate')),
                        invoice.get('Reference'),
                        invoice.get('BrandingThemeID'),
                        float(invoice.get('SubTotal', 0)),
                        float(invoice.get('TotalTax', 0)),
                        float(invoice.get('Total', 0)),
                        invoice.get('CurrencyCode'),
                        self._parse_xero_date(invoice.get('UpdatedDateUTC')),
                        self.unit_id,
                    )
                    cursor.execute(invoice_insert, invoice_data)
                    invoice_count += 1
                    for item in invoice.get('LineItems', []):
                        cursor.execute(
                            item_insert,
                            self._build_invoice_item_data(invoice, item),
                        )
                        item_count += 1
                self.db_conn.commit()
                total_synced += invoice_count
                logger.info(
                    f'[{self.slug}] Batch committed: {invoice_count} invoices, '
                    f'{item_count} items (total: {total_synced})'
                )
                self._update_sync_progress(sync_type, page=last_page, status='running')

            while page <= max_pages:
                logger.info(f'[{self.slug}] Fetching invoices page {page}...')
                params = {'page': page, 'pageSize': 100}
                if use_incremental:
                    params['where'] = f'UpdatedDateUTC>=DateTime({modified_after})'
                response = self._make_xero_request('Invoices', params=params)
                invoices = response.get('Invoices', [])
                if not invoices:
                    break
                logger.info(f'[{self.slug}] Retrieved {len(invoices)} invoices')
                batch_records.extend(invoices)

                if len(batch_records) >= (self.batch_size * 100):
                    flush_batch(batch_records, page)
                    batch_records = []

                page += 1
                time.sleep(1)

            # Final partial batch
            if batch_records:
                flush_batch(batch_records, page - 1)

            self._update_sync_progress(
                sync_type, completed=True, modified_after=_utcnow()
            )
            logger.info(f'[{self.slug}] Synced {total_synced} invoices')
            self._log_sync(sync_type, total_synced, 'success', None, start_time)
            return total_synced
        except Exception as e:
            self.db_conn.rollback()
            self._update_sync_progress(sync_type, status='failed')
            logger.error(f'[{self.slug}] invoices sync failed: {e}')
            self._log_sync(sync_type, 0, 'failed', str(e), start_time)
            raise

    def sync_payments(self):
        """Sync payments with batch commits and incremental support.

        Payments have no line items. Incremental syncs filter on
        UpdatedDateUTC via the per-tenant 'payments' watermark in
        sync_progress, mirroring sync_invoices.
        """
        logger.info(f'[{self.slug}] Starting payments sync...')
        start_time = _utcnow()
        sync_type = 'payments'
        try:
            progress = self._get_sync_progress(sync_type)
            use_incremental = (
                progress['last_modified'] is not None
                and progress['status'] == 'completed'
            )
            if use_incremental:
                dt = progress['last_modified']
                modified_after = (
                    f'{dt.year},{dt.month:02d},{dt.day:02d},'
                    f'{dt.hour:02d},{dt.minute:02d},{dt.second:02d}'
                )
                logger.info(
                    f'[{self.slug}] Incremental payment sync (changes since '
                    f'{dt:%Y-%m-%d %H:%M:%S})'
                )

            self._update_sync_progress(sync_type, status='running')

            payment_insert = f"""
                INSERT INTO {self.schema}.xero_payments
                (payment_id, invoice_id, account_id, payment_date, amount,
                 currency_rate, payment_type, status, reference, is_reconciled,
                 updated_at, unit_id, synced_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())
                ON CONFLICT (payment_id) DO UPDATE SET
                    invoice_id = EXCLUDED.invoice_id,
                    account_id = EXCLUDED.account_id,
                    payment_date = EXCLUDED.payment_date,
                    amount = EXCLUDED.amount,
                    currency_rate = EXCLUDED.currency_rate,
                    payment_type = EXCLUDED.payment_type,
                    status = EXCLUDED.status,
                    reference = EXCLUDED.reference,
                    is_reconciled = EXCLUDED.is_reconciled,
                    updated_at = EXCLUDED.updated_at,
                    unit_id = EXCLUDED.unit_id,
                    synced_at = NOW()
            """

            cursor = self.db_conn.cursor()
            total_synced = 0
            page = 1
            max_pages = 2000
            batch_records = []

            def flush_batch(records):
                nonlocal total_synced
                if not records:
                    return
                for payment in records:
                    cursor.execute(payment_insert, (
                        payment.get('PaymentID'),
                        (payment.get('Invoice') or {}).get('InvoiceID'),
                        (payment.get('Account') or {}).get('AccountID'),
                        self._parse_xero_date(payment.get('Date')),
                        float(payment.get('Amount') or 0),
                        float(payment.get('CurrencyRate') or 0),
                        payment.get('PaymentType'),
                        payment.get('Status'),
                        payment.get('Reference'),
                        payment.get('IsReconciled', False),
                        self._parse_xero_date(payment.get('UpdatedDateUTC')),
                        self.unit_id,
                    ))
                self.db_conn.commit()
                total_synced += len(records)
                logger.info(
                    f'[{self.slug}] Batch committed: {len(records)} payments '
                    f'(total: {total_synced})'
                )

            while page <= max_pages:
                logger.info(f'[{self.slug}] Fetching payments page {page}...')
                params = {'page': page, 'pageSize': 100}
                if use_incremental:
                    params['where'] = f'UpdatedDateUTC>=DateTime({modified_after})'
                response = self._make_xero_request('Payments', params=params)
                payments = response.get('Payments', [])
                if not payments:
                    break
                logger.info(f'[{self.slug}] Retrieved {len(payments)} payments')
                batch_records.extend(payments)

                if len(batch_records) >= (self.batch_size * 100):
                    flush_batch(batch_records)
                    batch_records = []

                page += 1
                time.sleep(1)

            # Final partial batch
            if batch_records:
                flush_batch(batch_records)

            self._update_sync_progress(
                sync_type, completed=True, modified_after=_utcnow()
            )
            logger.info(f'[{self.slug}] Synced {total_synced} payments')
            self._log_sync(sync_type, total_synced, 'success', None, start_time)
            return total_synced
        except Exception as e:
            self.db_conn.rollback()
            self._update_sync_progress(sync_type, status='failed')
            logger.error(f'[{self.slug}] payments sync failed: {e}')
            self._log_sync(sync_type, 0, 'failed', str(e), start_time)
            raise

    def _build_journal_line_data(self, journal, line):
        line_id = f'{journal.get("JournalID")}_{line.get("JournalLineID")}'
        tracking1_name = tracking1_option = None
        tracking1_category_id = tracking1_option_id = None
        tracking2_name = tracking2_option = None
        tracking2_category_id = tracking2_option_id = None
        tracking_list = line.get('TrackingCategories', []) or []
        if len(tracking_list) > 0:
            t = tracking_list[0]
            tracking1_name = t.get('Name')
            tracking1_option = t.get('Option')
            tracking1_category_id = t.get('TrackingCategoryID')
            tracking1_option_id = t.get('TrackingOptionID')
        if len(tracking_list) > 1:
            t = tracking_list[1]
            tracking2_name = t.get('Name')
            tracking2_option = t.get('Option')
            tracking2_category_id = t.get('TrackingCategoryID')
            tracking2_option_id = t.get('TrackingOptionID')
        return (
            line_id,
            journal.get('JournalID'),
            line.get('AccountID'),
            line.get('AccountCode'),
            line.get('Description'),
            float(line.get('NetAmount', 0)),
            float(line.get('TaxAmount', 0)),
            tracking1_name,
            tracking1_option,
            tracking1_category_id,
            tracking1_option_id,
            tracking2_name,
            tracking2_option,
            tracking2_category_id,
            tracking2_option_id,
        )

    def sync_journals(self, force_full_resync=False):
        """Sync journals and journal lines using offset-based pagination.

        The Journals API has no reliable change detection (offset only
        scans forward; Xero warns If-Modified-Since can miss journals).
        Journal numbers are sequential and the ledger is append-only, so
        each run pulls new journals forward from MAX(journal_number) and
        then backfills any missing number ranges below the max. That is
        far cheaper than the previous periodic full resync and is
        self-healing for gaps left by a failed run. force_full_resync
        re-pulls everything from offset 0 (manual override).
        See JOURNAL_UPDATE_TRACKING.md.
        """
        logger.info(f'[{self.slug}] Starting journals sync...')
        start_time = _utcnow()
        sync_type = 'journals'
        try:
            cursor = self.db_conn.cursor()
            is_full_sync = False
            current_offset = 0

            if force_full_resync:
                logger.info(f'[{self.slug}] FORCED FULL JOURNAL RESYNC')
                is_full_sync = True
            else:
                logger.info(
                    f'[{self.slug}] Incremental journal sync (forward + gap backfill)'
                )

            if not is_full_sync:
                cursor.execute(
                    f'SELECT MAX(journal_number) FROM {self.schema}.xero_journals'
                )
                row = cursor.fetchone()
                last_journal_number = row[0] if row and row[0] else 0
                if last_journal_number > 0:
                    current_offset = last_journal_number
                    logger.info(
                        f'[{self.slug}] Incremental journals from offset {last_journal_number}'
                    )
                else:
                    logger.info(f'[{self.slug}] No journals in DB — fresh full sync')
                    is_full_sync = True
                    current_offset = 0
            cursor.close()

            self._update_sync_progress(sync_type, status='running')

            journal_insert = f"""
                INSERT INTO {self.schema}.xero_journals
                (journal_id, journal_number, reference, notes, journal_date, status,
                 source_id, source_type, updated_at, unit_id, synced_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())
                ON CONFLICT (journal_id) DO UPDATE SET
                    status = EXCLUDED.status,
                    source_id = EXCLUDED.source_id,
                    source_type = EXCLUDED.source_type,
                    unit_id = EXCLUDED.unit_id,
                    synced_at = NOW()
            """
            line_insert = f"""
                INSERT INTO {self.schema}.xero_journal_lines
                (journal_line_id, journal_id, account_id, account_code, description,
                 net_amount, tax_amount,
                 tracking1_name, tracking1_option, tracking1_category_id, tracking1_option_id,
                 tracking2_name, tracking2_option, tracking2_category_id, tracking2_option_id,
                 synced_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())
                ON CONFLICT (journal_line_id) DO UPDATE SET
                    tracking1_name = EXCLUDED.tracking1_name,
                    tracking1_option = EXCLUDED.tracking1_option,
                    tracking1_category_id = EXCLUDED.tracking1_category_id,
                    tracking1_option_id = EXCLUDED.tracking1_option_id,
                    tracking2_name = EXCLUDED.tracking2_name,
                    tracking2_option = EXCLUDED.tracking2_option,
                    tracking2_category_id = EXCLUDED.tracking2_category_id,
                    tracking2_option_id = EXCLUDED.tracking2_option_id,
                    synced_at = NOW()
            """

            total_synced = 0
            batch_records = []
            consecutive_empty = 0
            max_empty = 3

            def flush_batch(records):
                nonlocal total_synced
                if not records:
                    return
                cur = self.db_conn.cursor()
                journal_count = 0
                line_count = 0
                for journal in records:
                    journal_id = journal.get('JournalID')
                    journal_number = journal.get('JournalNumber')
                    if not journal_id:
                        logger.error(
                            f'[{self.slug}] NULL JournalID for journal_number={journal_number}'
                        )
                        continue
                    journal_data = (
                        journal_id,
                        journal_number,
                        journal.get('Reference'),
                        None,
                        self._parse_xero_date(journal.get('JournalDate')),
                        None,
                        journal.get('SourceID'),
                        journal.get('SourceType'),
                        self._parse_xero_date(journal.get('CreatedDateUTC')),
                        self.unit_id,
                    )
                    try:
                        cur.execute(journal_insert, journal_data)
                        journal_count += 1
                    except Exception as e:
                        logger.error(
                            f'[{self.slug}] Failed to insert journal '
                            f'{journal_number} ({journal_id}): {e}'
                        )
                        continue
                    for line in journal.get('JournalLines', []):
                        cur.execute(line_insert, self._build_journal_line_data(journal, line))
                        line_count += 1
                self.db_conn.commit()
                cur.close()
                total_synced += journal_count
                logger.info(
                    f'[{self.slug}] Batch committed: {journal_count} journals, '
                    f'{line_count} lines (total: {total_synced})'
                )

            while True:
                logger.info(f'[{self.slug}] Fetching journals offset={current_offset}...')
                response = self._make_xero_request('Journals', params={'offset': current_offset})
                journals = response.get('Journals', [])
                if not journals:
                    consecutive_empty += 1
                    logger.info(
                        f'[{self.slug}] Empty response {consecutive_empty}/{max_empty}'
                    )
                    if consecutive_empty >= max_empty:
                        break
                    current_offset += 100
                    time.sleep(1)
                    continue
                consecutive_empty = 0
                last_num = journals[-1].get('JournalNumber')
                if last_num:
                    current_offset = last_num
                batch_records.extend(journals)

                if len(batch_records) >= (self.batch_size * 100):
                    flush_batch(batch_records)
                    batch_records = []
                time.sleep(1)

            if batch_records:
                flush_batch(batch_records)

            # Backfill any gaps below the current max journal number. The
            # forward pass above only catches journals newer than the max
            # we already had; a missing range (from a failed run or the
            # documented If-Modified-Since miss) would otherwise never be
            # refetched. Journal numbers are sequential, so we detect the
            # gaps and re-pull only those ranges.
            if not is_full_sync:
                gap_cursor = self.db_conn.cursor()
                gap_cursor.execute(f"""
                    SELECT journal_number + 1 AS gap_start, next_num - 1 AS gap_end
                    FROM (
                        SELECT journal_number,
                               LEAD(journal_number) OVER (ORDER BY journal_number) AS next_num
                        FROM {self.schema}.xero_journals
                    ) t
                    WHERE next_num IS NOT NULL AND next_num > journal_number + 1
                    ORDER BY gap_start
                """)
                gaps = gap_cursor.fetchall()
                gap_cursor.close()
                if not gaps:
                    logger.info(f'[{self.slug}] No journal gaps to backfill')
                else:
                    total_missing = sum(end - start + 1 for start, end in gaps)
                    logger.info(
                        f'[{self.slug}] Backfilling {len(gaps)} journal gap(s), '
                        f'{total_missing} missing number(s)'
                    )
                    for gap_start, gap_end in gaps:
                        backfill_offset = gap_start - 1
                        while backfill_offset < gap_end:
                            logger.info(
                                f'[{self.slug}] Backfilling journals '
                                f'{gap_start}-{gap_end} from offset={backfill_offset}...'
                            )
                            response = self._make_xero_request(
                                'Journals', params={'offset': backfill_offset}
                            )
                            journals = response.get('Journals', [])
                            if not journals:
                                break
                            in_gap = [
                                j for j in journals
                                if j.get('JournalNumber') is not None
                                and j.get('JournalNumber') <= gap_end
                            ]
                            flush_batch(in_gap)
                            last_num = journals[-1].get('JournalNumber')
                            if not last_num or last_num <= backfill_offset:
                                break
                            backfill_offset = last_num
                            time.sleep(1)

            # Update sync_metadata
            cursor = self.db_conn.cursor()
            cursor.execute(
                f'SELECT COUNT(*), MAX(journal_number) FROM {self.schema}.xero_journals'
            )
            total_count, max_number = cursor.fetchone()
            logger.info(
                f'[{self.slug}] Journal sync complete. DB total: {total_count}, '
                f'max journal_number: {max_number}'
            )
            if is_full_sync:
                cursor.execute(f"""
                    INSERT INTO {self.schema}.sync_metadata
                    (entity_type, last_full_sync, last_incremental_sync, updated_at)
                    VALUES ('journals', NOW(), NOW(), NOW())
                    ON CONFLICT (entity_type) DO UPDATE SET
                        last_full_sync = NOW(),
                        last_incremental_sync = NOW(),
                        updated_at = NOW()
                """)
            else:
                cursor.execute(f"""
                    INSERT INTO {self.schema}.sync_metadata
                    (entity_type, last_incremental_sync, updated_at)
                    VALUES ('journals', NOW(), NOW())
                    ON CONFLICT (entity_type) DO UPDATE SET
                        last_incremental_sync = NOW(),
                        updated_at = NOW()
                """)
            self.db_conn.commit()
            cursor.close()

            self._update_sync_progress(
                sync_type, completed=True, modified_after=_utcnow()
            )
            logger.info(f'[{self.slug}] Synced {total_synced} journals')
            self._log_sync(sync_type, total_synced, 'success', None, start_time)
            return total_synced
        except Exception as e:
            self.db_conn.rollback()
            self._update_sync_progress(sync_type, status='failed')
            logger.error(f'[{self.slug}] journals sync failed: {e}')
            self._log_sync(sync_type, 0, 'failed', str(e), start_time)
            raise

    # --- progress / log ---

    def _get_sync_progress(self, sync_type):
        try:
            cursor = self.db_conn.cursor()
            cursor.execute(f"""
                SELECT last_synced_page, last_sync_completed_at,
                       last_modified_after, sync_status
                FROM {self.schema}.sync_progress
                WHERE sync_type = %s
            """, (sync_type,))
            row = cursor.fetchone()
            if row:
                return {
                    'last_page': row[0] or 0,
                    'last_completed': row[1],
                    'last_modified': row[2],
                    'status': row[3],
                }
            return {'last_page': 0, 'last_completed': None,
                    'last_modified': None, 'status': 'idle'}
        except Exception as e:
            logger.warning(f'[{self.slug}] Failed to read sync_progress for {sync_type}: {e}')
            return {'last_page': 0, 'last_completed': None,
                    'last_modified': None, 'status': 'idle'}

    def _update_sync_progress(self, sync_type, page=None, status=None,
                              completed=False, modified_after=None):
        try:
            cursor = self.db_conn.cursor()
            # Ensure a row exists so UPDATE has something to hit
            cursor.execute(f"""
                INSERT INTO {self.schema}.sync_progress (sync_type, sync_status)
                VALUES (%s, 'idle')
                ON CONFLICT (sync_type) DO NOTHING
            """, (sync_type,))
            if completed:
                cursor.execute(f"""
                    UPDATE {self.schema}.sync_progress
                    SET last_synced_page = 0,
                        last_sync_completed_at = NOW(),
                        last_modified_after = %s,
                        sync_status = 'completed',
                        updated_at = NOW()
                    WHERE sync_type = %s
                """, (modified_after or _utcnow(), sync_type))
            elif page is not None:
                cursor.execute(f"""
                    UPDATE {self.schema}.sync_progress
                    SET last_synced_page = %s,
                        sync_status = %s,
                        updated_at = NOW()
                    WHERE sync_type = %s
                """, (page, status or 'running', sync_type))
            elif status:
                cursor.execute(f"""
                    UPDATE {self.schema}.sync_progress
                    SET sync_status = %s,
                        updated_at = NOW()
                    WHERE sync_type = %s
                """, (status, sync_type))
            self.db_conn.commit()
        except Exception as e:
            logger.warning(f'[{self.slug}] Failed to update sync_progress for {sync_type}: {e}')

    def _log_sync(self, sync_type, records_synced, status, error_message, start_time):
        try:
            cursor = self.db_conn.cursor()
            duration = int((_utcnow() - start_time).total_seconds())
            cursor.execute(f"""
                INSERT INTO {self.schema}.sync_log
                (sync_type, records_synced, status, error_message,
                 started_at, completed_at, duration_seconds)
                VALUES (%s, %s, %s, %s, %s, NOW(), %s)
            """, (sync_type, records_synced, status, error_message, start_time, duration))
            self.db_conn.commit()
        except Exception as e:
            logger.warning(f'[{self.slug}] Failed to write sync_log: {e}')

    # --- orchestration ---

    def run_full_sync(self, force_full_journal_resync=False):
        """Run all entity syncs for this tenant. Returns per-entity results.

        Returns dict {entity_name: ('ok', count) | ('failed', error_str)}.
        """
        self._load_tokens_from_db()
        if not self.refresh_token:
            raise RuntimeError(
                f'No refresh token in finance_{self.slug}.tokens. '
                f'Run `python get_refresh_token.py --tenant {self.slug}` first.'
            )

        results = {}
        entities = [
            ('tracking_categories', self.sync_tracking_categories),
            ('accounts', self.sync_accounts),
            ('tax_rates', self.sync_tax_rates),
            ('contacts', self.sync_contacts),
            ('invoices', self.sync_invoices),
            ('payments', self.sync_payments),
            ('journals', lambda: self.sync_journals(
                force_full_resync=force_full_journal_resync
            )),
        ]
        for name, fn in entities:
            try:
                count = fn()
                results[name] = ('ok', count)
            except Exception as e:
                # _log_sync was already called inside the entity method
                results[name] = ('failed', str(e))
        return results


def main():
    parser = argparse.ArgumentParser(
        description='Sync Xero data to Postgres for every tenant in org.units.'
    )
    parser.add_argument('--slug', default=None,
                        help='Restrict to a single tenant slug (e.g. mgl). '
                             'Default: every tenant with xero_tenant_id set.')
    parser.add_argument('--force-full-resync', action='store_true',
                        help='Force a full journal resync for every tenant.')
    parser.add_argument('--force-full-invoice-resync', action='store_true',
                        help='Force a full invoice resync for every tenant.')
    args = parser.parse_args()

    try:
        db_conn = _open_shared_db_conn()
    except Exception as e:
        logger.error(f'Failed to open DB connection: {e}')
        sys.exit(2)

    try:
        tenants = _list_tenants(db_conn, slug=args.slug)
        if not tenants:
            if args.slug:
                logger.error(
                    f'No tenant with slug={args.slug!r} and xero_tenant_id set.'
                )
            else:
                logger.error('No tenants in org.units have xero_tenant_id set yet.')
            sys.exit(2)

        any_failure = False
        summaries = []

        for unit in tenants:
            unit_id, slug, tenant_id = unit
            logger.info(f'=== Sync start: slug={slug} unit_id={unit_id} ===')
            try:
                syncer = XeroSync(
                    unit, db_conn,
                    force_full_invoice_resync=args.force_full_invoice_resync,
                )
                results = syncer.run_full_sync(
                    force_full_journal_resync=args.force_full_resync,
                )
            except Exception as e:
                logger.error(f'[{slug}] Tenant aborted before any entity ran: {e}')
                summaries.append((slug, {'__init__': ('failed', str(e))}))
                any_failure = True
                continue
            summaries.append((slug, results))
            if any(status == 'failed' for status, _ in results.values()):
                any_failure = True
            logger.info(f'=== Sync end:   slug={slug} ===')

        logger.info('=== Final summary ===')
        for slug, results in summaries:
            parts = []
            for name, (status, value) in results.items():
                if status == 'ok':
                    parts.append(f'{name}=ok({value})')
                else:
                    short = str(value).replace('\n', ' ')[:80]
                    parts.append(f'{name}=FAILED({short})')
            logger.info(f'{slug}: {" ".join(parts)}')

        sys.exit(1 if any_failure else 0)
    finally:
        db_conn.close()


if __name__ == '__main__':
    main()
