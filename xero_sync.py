#!/usr/bin/env python3
"""
Xero to PostgreSQL Sync Script
Syncs invoices, contacts, accounts, and journals from Xero API to PostgreSQL
All data is stored in the 'xero' schema within the specified database
Supports multi-tenant setup: each Xero org uses a separate database
"""

import os
import json
import sys
import logging
from datetime import datetime, timedelta
import re
import time
import psycopg2
from psycopg2.extras import execute_batch
import requests
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

class XeroSync:
    def __init__(self):
        # Xero API configuration
        self.client_id = os.getenv('XERO_CLIENT_ID')
        self.client_secret = os.getenv('XERO_CLIENT_SECRET')
        self.tenant_id = os.getenv('XERO_TENANT_ID')
        self.refresh_token = os.getenv('XERO_REFRESH_TOKEN')
        
        # PostgreSQL configuration
        self.db_host = os.getenv('DB_HOST')
        self.db_port = os.getenv('DB_PORT', '5432')
        self.db_name = os.getenv('DB_NAME')
        self.db_user = os.getenv('DB_USER')
        self.db_password = os.getenv('DB_PASSWORD')
        
        # Sync configuration
        self.batch_size = int(os.getenv('SYNC_BATCH_SIZE', '10'))  # Pages per batch (default: 10 pages = 1000 records)
        self.force_full_sync = os.getenv('FORCE_FULL_SYNC', 'false').lower() == 'true'
        
        self.access_token = None
        self.access_token_expires_at = None
        self.db_conn = None
        self.sync_log = {}
        
        # Validate configuration
        self._validate_config()
    
    def _parse_xero_date(self, date_string):
        """Parse Xero's /Date(timestamp)/ format to Python datetime"""
        if not date_string:
            return None
        
        # Match /Date(1690484980033+0000)/
        match = re.match(r'/Date\((\d+)([+-]\d{4})?\)/', str(date_string))
        if match:
            timestamp_ms = int(match.group(1))
            # Convert milliseconds to seconds
            timestamp_s = timestamp_ms / 1000
            return datetime.fromtimestamp(timestamp_s)
        
        # If it's already a proper date string, return as-is
        return date_string
    
    def _validate_config(self):
        """Validate that all required environment variables are set"""
        required_vars = [
            'XERO_CLIENT_ID', 'XERO_CLIENT_SECRET', 'XERO_TENANT_ID',
            'XERO_REFRESH_TOKEN', 'DB_HOST', 'DB_NAME', 'DB_USER', 'DB_PASSWORD'
        ]
        missing = [var for var in required_vars if not os.getenv(var)]
        if missing:
            raise ValueError(f"Missing required environment variables: {', '.join(missing)}")
    
    def get_access_token(self, force_refresh=False):
        """Get a new access token using the refresh token
        
        Args:
            force_refresh: If True, always get a new token. If False, use cached token if valid.
        """
        try:
            # Check if we have a valid cached token (unless force_refresh is True)
            if not force_refresh and self.access_token and not self._is_token_expired():
                logger.debug("Using cached access token")
                return self.access_token
            
            # Load current refresh token from database (it may have been updated)
            if self.db_conn:
                self._load_tokens_from_db()
            
            url = 'https://identity.xero.com/connect/token'
            data = {
                'grant_type': 'refresh_token',
                'client_id': self.client_id,
                'client_secret': self.client_secret,
                'refresh_token': self.refresh_token
            }
            
            response = requests.post(url, data=data, timeout=10)
            response.raise_for_status()
            
            token_data = response.json()
            self.access_token = token_data['access_token']
            
            # Xero returns a NEW refresh token each time - we must save it!
            new_refresh_token = token_data.get('refresh_token')
            if new_refresh_token:
                self.refresh_token = new_refresh_token
                logger.info("Received new refresh token from Xero")
            
            # Access tokens expire in 30 minutes (1800 seconds)
            expires_in = token_data.get('expires_in', 1800)
            self.access_token_expires_at = datetime.now() + timedelta(seconds=expires_in)
            
            # Save tokens to database for persistence
            if self.db_conn:
                self._save_tokens_to_db()
            
            logger.info(f"Successfully obtained new access token (expires at {self.access_token_expires_at.strftime('%Y-%m-%d %H:%M:%S')})")
            
            return self.access_token
        except Exception as e:
            logger.error(f"Failed to get access token: {str(e)}")
            raise
    
    def _make_xero_request(self, endpoint, params=None, retry_count=0, auth_retry=False):
        """Make a request to the Xero API with rate limit and auth error handling
        
        Args:
            endpoint: The Xero API endpoint to call
            params: Query parameters
            retry_count: Current retry count for rate limiting
            auth_retry: Whether this is a retry after refreshing auth token
        """
        # Check if token is expired or about to expire (within 5 minutes)
        if not self.access_token or self._is_token_expired(buffer_minutes=5):
            logger.info("Access token expired or expiring soon, refreshing...")
            self.get_access_token(force_refresh=True)
        
        headers = {
            'Authorization': f'Bearer {self.access_token}',
            'Xero-Tenant-ID': self.tenant_id,
            'Accept': 'application/json'
        }
        
        url = f'https://api.xero.com/api.xro/2.0/{endpoint}'
        
        try:
            response = requests.get(url, headers=headers, params=params, timeout=30)
            
            # Handle rate limiting
            if response.status_code == 429:
                if retry_count < 3:
                    logger.warning(f"Rate limit hit. Waiting 60 seconds before retry {retry_count + 1}/3...")
                    time.sleep(60)
                    return self._make_xero_request(endpoint, params, retry_count + 1, auth_retry)
                else:
                    raise Exception("Rate limit exceeded after 3 retries")
            
            # Handle authorization errors (token expired mid-request)
            if response.status_code == 401 and not auth_retry:
                logger.warning("Received 401 Unauthorized - refreshing token and retrying...")
                self.get_access_token(force_refresh=True)
                return self._make_xero_request(endpoint, params, retry_count, auth_retry=True)
            
            response.raise_for_status()
            return response.json()
        except requests.exceptions.HTTPError as e:
            if e.response.status_code not in [429, 401]:  # Don't log 429/401 twice
                logger.error(f"Xero API request failed for {endpoint}: {str(e)}")
            raise
        except Exception as e:
            logger.error(f"Xero API request failed for {endpoint}: {str(e)}")
            raise
    
    def connect_db(self):
        """Connect to PostgreSQL database"""
        try:
            self.db_conn = psycopg2.connect(
                host=self.db_host,
                port=self.db_port,
                database=self.db_name,
                user=self.db_user,
                password=self.db_password
            )
            logger.info("Successfully connected to PostgreSQL")
            
            # Load any existing tokens from database
            self._load_tokens_from_db()
        except Exception as e:
            logger.error(f"Failed to connect to PostgreSQL: {str(e)}")
            raise
    
    def close_db(self):
        """Close database connection"""
        if self.db_conn:
            self.db_conn.close()
            logger.info("Database connection closed")
    
    def sync_accounts(self):
        """Sync accounts from Xero"""
        logger.info("Starting account sync...")
        start_time = datetime.now()
        
        try:
            # Fetch accounts from Xero (Accounts endpoint doesn't support paging)
            logger.info("Fetching accounts...")
            response = self._make_xero_request('Accounts')
            accounts = response.get('Accounts', [])
            
            if not accounts:
                logger.info("No accounts to sync")
                return 0
            
            logger.info(f"Retrieved {len(accounts)} accounts")
            
            cursor = self.db_conn.cursor()
            
            # Prepare insert/update query
            insert_query = """
                INSERT INTO xero.accounts 
                (account_id, code, name, account_type, description, enable_payments, status, updated_at, synced_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, NOW())
                ON CONFLICT (account_id) DO UPDATE SET
                    code = EXCLUDED.code,
                    name = EXCLUDED.name,
                    account_type = EXCLUDED.account_type,
                    description = EXCLUDED.description,
                    enable_payments = EXCLUDED.enable_payments,
                    status = EXCLUDED.status,
                    updated_at = EXCLUDED.updated_at,
                    synced_at = NOW()
            """
            
            # Prepare data
            data = []
            for account in accounts:
                data.append((
                    account.get('AccountID'),
                    account.get('Code'),
                    account.get('Name'),
                    account.get('Type'),
                    account.get('Description'),
                    account.get('EnablePayments', False),
                    account.get('Status'),
                    self._parse_xero_date(account.get('UpdatedDateUTC'))
                ))
            
            # Execute batch insert
            execute_batch(cursor, insert_query, data, page_size=100)
            self.db_conn.commit()
            
            logger.info(f"Successfully synced {len(accounts)} accounts")
            self._log_sync('accounts', len(accounts), 'success', None, start_time)
            
            return len(accounts)
        
        except Exception as e:
            self.db_conn.rollback()
            logger.error(f"Failed to sync accounts: {str(e)}")
            self._log_sync('accounts', 0, 'failed', str(e), start_time)
            raise
    
    def sync_contacts(self):
        """Sync contacts from Xero"""
        logger.info("Starting contact sync...")
        start_time = datetime.now()
        
        try:
            # Fetch ALL contacts from Xero with pagination
            all_contacts = []
            page = 1
            page_size = 100
            max_pages = 200
            
            while page <= max_pages:
                logger.info(f"Fetching contacts page {page}...")
                response = self._make_xero_request('Contacts', params={'page': page, 'pageSize': 100})
                contacts = response.get('Contacts', [])
                
                if not contacts:
                    break
                
                all_contacts.extend(contacts)
                logger.info(f"Retrieved {len(contacts)} contacts (total so far: {len(all_contacts)})")
                
                page += 1
                time.sleep(1)  # Delay to avoid rate limiting
            
            if not all_contacts:
                logger.info("No contacts to sync")
                return 0
            
            contacts = all_contacts
            
            cursor = self.db_conn.cursor()
            
            insert_query = """
                INSERT INTO xero.contacts 
                (contact_id, name, email_address, phones, addresses, tax_number, contact_status, updated_at, synced_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, NOW())
                ON CONFLICT (contact_id) DO UPDATE SET
                    name = EXCLUDED.name,
                    email_address = EXCLUDED.email_address,
                    phones = EXCLUDED.phones,
                    addresses = EXCLUDED.addresses,
                    tax_number = EXCLUDED.tax_number,
                    contact_status = EXCLUDED.contact_status,
                    updated_at = EXCLUDED.updated_at,
                    synced_at = NOW()
            """
            
            data = []
            for contact in contacts:
                # Serialize phones and addresses as JSON
                phones = json.dumps(contact.get('Phones', []))
                addresses = json.dumps(contact.get('Addresses', []))
                
                data.append((
                    contact.get('ContactID'),
                    contact.get('Name'),
                    contact.get('EmailAddress'),
                    phones,
                    addresses,
                    contact.get('TaxNumber'),
                    contact.get('ContactStatus'),
                    self._parse_xero_date(contact.get('UpdatedDateUTC'))
                ))
            
            execute_batch(cursor, insert_query, data, page_size=100)
            self.db_conn.commit()
            
            logger.info(f"Successfully synced {len(contacts)} contacts")
            self._log_sync('contacts', len(contacts), 'success', None, start_time)
            
            return len(contacts)
        
        except Exception as e:
            self.db_conn.rollback()
            logger.error(f"Failed to sync contacts: {str(e)}")
            self._log_sync('contacts', 0, 'failed', str(e), start_time)
            raise
    
    def sync_invoices(self):
        """Sync invoices and line items from Xero with batch commits and incremental sync"""
        logger.info("Starting invoice sync...")
        start_time = datetime.now()
        sync_type = 'invoices'
        
        try:
            # Get sync progress
            progress = self._get_sync_progress(sync_type)
            start_page = progress['last_page'] + 1 if progress['status'] == 'running' else 1
            
            # Determine if we should do incremental sync
            use_incremental = (
                not self.force_full_sync and 
                progress['last_modified'] is not None and
                progress['status'] == 'completed'
            )
            
            if use_incremental:
                dt = progress['last_modified']
                # Xero API DateTime format: DateTime(year,month,day,hour,minute,second)
                modified_after = f"{dt.year},{dt.month:02d},{dt.day:02d},{dt.hour:02d},{dt.minute:02d},{dt.second:02d}"
                logger.info(f"Using incremental sync (changes since {dt.strftime('%Y-%m-%d %H:%M:%S')})")
            elif start_page > 1:
                logger.info(f"Resuming from page {start_page} (previous sync was interrupted)")
            
            # Mark sync as running
            self._update_sync_progress(sync_type, page=start_page, status='running')
            
            total_synced = 0
            page = start_page if not use_incremental else 1
            max_pages = 2000
            batch_records = []
            
            cursor = self.db_conn.cursor()
            
            invoice_insert = """
                INSERT INTO xero.invoices 
                (invoice_id, invoice_number, contact_id, invoice_type, status, line_amount_types,
                 invoice_date, due_date, expected_payment_date, reference, branding_theme_id,
                 sub_total, total_tax, total, currency_code, updated_at, synced_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())
                ON CONFLICT (invoice_id) DO UPDATE SET
                    invoice_number = EXCLUDED.invoice_number,
                    status = EXCLUDED.status,
                    total = EXCLUDED.total,
                    sub_total = EXCLUDED.sub_total,
                    total_tax = EXCLUDED.total_tax,
                    synced_at = NOW()
            """
            
            item_insert = """
                INSERT INTO xero.invoice_items
                (invoice_item_id, invoice_id, description, quantity, unit_amount, tax_type,
                 tax_amount, line_amount, account_code, account_id, 
                 tracking1_name, tracking1_option, tracking2_name, tracking2_option, synced_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())
                ON CONFLICT (invoice_item_id) DO UPDATE SET
                    line_amount = EXCLUDED.line_amount,
                    tracking1_name = EXCLUDED.tracking1_name,
                    tracking1_option = EXCLUDED.tracking1_option,
                    tracking2_name = EXCLUDED.tracking2_name,
                    tracking2_option = EXCLUDED.tracking2_option,
                    synced_at = NOW()
            """
            
            while page <= max_pages:
                logger.info(f"Fetching invoices page {page}...")
                
                # Build params with ModifiedAfter for incremental sync
                params = {'page': page, 'pageSize': 100}
                if use_incremental:
                    params['where'] = f'UpdatedDateUTC>=DateTime({modified_after})'
                
                response = self._make_xero_request('Invoices', params=params)
                invoices = response.get('Invoices', [])
                
                if not invoices:
                    break
                
                logger.info(f"Retrieved {len(invoices)} invoices")
                batch_records.extend(invoices)
                
                # Process batch when we reach batch_size pages
                if len(batch_records) >= (self.batch_size * 100):
                    # Process current batch
                    invoice_count = 0
                    item_count = 0
                    
                    for invoice in batch_records:
                        # Insert invoice
                        invoice_data = [
                            invoice.get('InvoiceID'),
                            invoice.get('InvoiceNumber'),
                            invoice.get('Contact', {}).get('ContactID'),
                            invoice.get('Type'),
                            invoice.get('Status'),
                            invoice.get('LineAmountTypes'),
                            self._parse_xero_date(invoice.get('Date')),  # Fixed: Date not InvoiceDate
                            self._parse_xero_date(invoice.get('DueDate')),
                            self._parse_xero_date(invoice.get('ExpectedPaymentDate')),
                            invoice.get('Reference'),
                            invoice.get('BrandingThemeID'),
                            float(invoice.get('SubTotal', 0)),
                            float(invoice.get('TotalTax', 0)),
                            float(invoice.get('Total', 0)),
                            invoice.get('CurrencyCode'),
                            self._parse_xero_date(invoice.get('UpdatedDateUTC'))
                        ]
                        
                        cursor.execute(invoice_insert, invoice_data)
                        invoice_count += 1
                        
                        # Insert line items
                        for item in invoice.get('LineItems', []):
                            item_id = f"{invoice.get('InvoiceID')}_{item.get('LineItemID')}"
                            
                            # Extract tracking categories (up to 2)
                            tracking1_name = None
                            tracking1_option = None
                            tracking2_name = None
                            tracking2_option = None
                            
                            tracking_list = item.get('Tracking', [])
                            if tracking_list and len(tracking_list) > 0:
                                tracking1_name = tracking_list[0].get('Name')
                                tracking1_option = tracking_list[0].get('Option')
                            if tracking_list and len(tracking_list) > 1:
                                tracking2_name = tracking_list[1].get('Name')
                                tracking2_option = tracking_list[1].get('Option')
                            
                            item_data = [
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
                                tracking2_name,
                                tracking2_option
                            ]
                            
                            cursor.execute(item_insert, item_data)
                            item_count += 1
                    
                    # COMMIT BATCH
                    self.db_conn.commit()
                    total_synced += invoice_count
                    
                    logger.info(f"✓ Batch committed: {invoice_count} invoices, {item_count} items (total: {total_synced})")
                    
                    # Update progress
                    self._update_sync_progress(sync_type, page=page, status='running')
                    
                    # Clear batch for next iteration
                    batch_records = []
                
                page += 1
                time.sleep(1)  # Delay to avoid rate limiting
            
            # Process any remaining records in final batch
            if batch_records:
                invoice_count = 0
                item_count = 0
                
                for invoice in batch_records:
                    # Insert invoice
                    invoice_data = [
                        invoice.get('InvoiceID'),
                        invoice.get('InvoiceNumber'),
                        invoice.get('Contact', {}).get('ContactID'),
                        invoice.get('Type'),
                        invoice.get('Status'),
                        invoice.get('LineAmountTypes'),
                        self._parse_xero_date(invoice.get('Date')),  # Fixed: Date not InvoiceDate
                        self._parse_xero_date(invoice.get('DueDate')),
                        self._parse_xero_date(invoice.get('ExpectedPaymentDate')),
                        invoice.get('Reference'),
                        invoice.get('BrandingThemeID'),
                        float(invoice.get('SubTotal', 0)),
                        float(invoice.get('TotalTax', 0)),
                        float(invoice.get('Total', 0)),
                        invoice.get('CurrencyCode'),
                        self._parse_xero_date(invoice.get('UpdatedDateUTC'))
                    ]
                    
                    cursor.execute(invoice_insert, invoice_data)
                    invoice_count += 1
                    
                    # Insert line items
                    for item in invoice.get('LineItems', []):
                        item_id = f"{invoice.get('InvoiceID')}_{item.get('LineItemID')}"
                        
                        # Extract tracking categories (up to 2)
                        tracking1_name = None
                        tracking1_option = None
                        tracking2_name = None
                        tracking2_option = None
                        
                        tracking_list = item.get('Tracking', [])
                        if tracking_list and len(tracking_list) > 0:
                            tracking1_name = tracking_list[0].get('Name')
                            tracking1_option = tracking_list[0].get('Option')
                        if tracking_list and len(tracking_list) > 1:
                            tracking2_name = tracking_list[1].get('Name')
                            tracking2_option = tracking_list[1].get('Option')
                        
                        item_data = [
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
                            tracking2_name,
                            tracking2_option
                        ]
                        
                        cursor.execute(item_insert, item_data)
                        item_count += 1
                
                # COMMIT FINAL BATCH
                self.db_conn.commit()
                total_synced += invoice_count
                
                logger.info(f"✓ Final batch committed: {invoice_count} invoices, {item_count} items (total: {total_synced})")
                
                # Update progress with last page processed
                self._update_sync_progress(sync_type, page=page-1, status='running')
            
            # Mark sync as completed
            sync_timestamp = datetime.now()
            self._update_sync_progress(sync_type, completed=True, modified_after=sync_timestamp)
            
            logger.info(f"Successfully synced {total_synced} invoices")
            self._log_sync(sync_type, total_synced, 'success', None, start_time)
            
            return total_synced
        
        except Exception as e:
            self.db_conn.rollback()
            self._update_sync_progress(sync_type, status='failed')
            logger.error(f"Failed to sync invoices: {str(e)}")
            self._log_sync(sync_type, 0, 'failed', str(e), start_time)
            raise
    
    def sync_journals(self, force_full_resync=False):
        """Sync journals and journal lines from Xero using offset-based pagination
        
        Args:
            force_full_resync: If True, forces a complete resync of all journals (catches updates)
        """
        logger.info("Starting journal sync...")
        start_time = datetime.now()
        sync_type = 'journals'
        
        try:
            cursor = self.db_conn.cursor()
            
            # Check if we need a full resync
            is_full_sync = False
            current_offset = 0
            
            if force_full_resync:
                logger.info("FORCED FULL RESYNC: Will resync all journals to catch updates")
                is_full_sync = True
                current_offset = 0
            else:
                # Check last full sync date
                cursor.execute("""
                    SELECT last_full_sync, last_incremental_sync
                    FROM xero.sync_metadata
                    WHERE entity_type = 'journals'
                """)
                result = cursor.fetchone()
                
                if result:
                    last_full_sync = result[0]
                    if last_full_sync:
                        days_since_full = (datetime.now() - last_full_sync).days
                        if days_since_full >= 7:
                            logger.info(f"Last full sync was {days_since_full} days ago - performing FULL RESYNC")
                            is_full_sync = True
                            current_offset = 0
                        else:
                            logger.info(f"Last full sync was {days_since_full} days ago - performing incremental sync")
                    else:
                        logger.info("No previous full sync recorded - performing FULL RESYNC")
                        is_full_sync = True
                        current_offset = 0
                else:
                    logger.info("No sync metadata found - performing FULL RESYNC")
                    is_full_sync = True
                    current_offset = 0
            
            # If incremental sync, get the highest journal number
            if not is_full_sync:
                cursor.execute("SELECT MAX(journal_number) FROM xero.journals")
                result = cursor.fetchone()
                last_journal_number = result[0] if result[0] else 0
                current_offset = last_journal_number
                
                if last_journal_number > 0:
                    logger.info(f"Incremental sync: Starting from journal number {last_journal_number}")
                else:
                    logger.info("No journals in database - starting fresh sync")
                    is_full_sync = True
                    current_offset = 0
            
            cursor.close()
            
            # Mark sync as running (journals don't use page tracking anymore)
            self._update_sync_progress(sync_type, status='running')
            
            total_synced = 0
            # current_offset is already set from the full/incremental sync logic above
            batch_records = []
            consecutive_empty_responses = 0
            max_empty_responses = 3  # Stop after 3 consecutive empty responses
            
            # Don't create cursor here - will create fresh cursor for each batch
            
            journal_insert = """
                INSERT INTO xero.journals
                (journal_id, journal_number, reference, notes, journal_date, status, 
                 source_id, source_type, updated_at, synced_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())
                ON CONFLICT (journal_id) DO UPDATE SET
                    status = EXCLUDED.status,
                    source_id = EXCLUDED.source_id,
                    source_type = EXCLUDED.source_type,
                    synced_at = NOW()
            """
            
            line_insert = """
                INSERT INTO xero.journal_lines
                (journal_line_id, journal_id, account_id, account_code, description, net_amount,
                 tax_amount, tracking1_name, tracking1_option, tracking2_name, tracking2_option, synced_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())
                ON CONFLICT (journal_line_id) DO UPDATE SET
                    tracking1_name = EXCLUDED.tracking1_name,
                    tracking1_option = EXCLUDED.tracking1_option,
                    tracking2_name = EXCLUDED.tracking2_name,
                    tracking2_option = EXCLUDED.tracking2_option,
                    synced_at = NOW()
            """
            
            # Counter for diagnostic logging of tracking data
            tracking_found_count = 0
            
            while True:
                logger.info(f"Fetching journals with offset {current_offset}...")
                
                # Use offset parameter as per Xero API documentation
                response = self._make_xero_request('Journals', params={'offset': current_offset})
                journals = response.get('Journals', [])
                
                if not journals:
                    consecutive_empty_responses += 1
                    logger.info(f"No journals returned (empty response {consecutive_empty_responses}/{max_empty_responses})")
                    if consecutive_empty_responses >= max_empty_responses:
                        logger.info("Reached end of journals after multiple empty responses")
                        break
                    # Try incrementing offset by 100 in case there's a gap
                    current_offset += 100
                    time.sleep(1)
                    continue
                
                consecutive_empty_responses = 0  # Reset counter on successful response
                logger.info(f"Retrieved {len(journals)} journals")
                
                # Log the journal number range
                if journals:
                    first_num = journals[0].get('JournalNumber')
                    last_num = journals[-1].get('JournalNumber')
                    logger.info(f"Journal number range: {first_num} to {last_num}")
                    
                    # Update current_offset to the last journal number for next iteration
                    if last_num:
                        current_offset = last_num
                
                batch_records.extend(journals)
                
                # Process batch when we reach batch_size pages
                if len(batch_records) >= (self.batch_size * 100):
                    # Create fresh cursor for this batch
                    cursor = self.db_conn.cursor()
                    
                    # Process current batch
                    journal_count = 0
                    line_count = 0
                    
                    for journal in batch_records:
                        # Insert journal
                        journal_id = journal.get('JournalID')
                        journal_number = journal.get('JournalNumber')
                        
                        # DIAGNOSTIC: Log if journal_id is NULL or empty
                        if not journal_id:
                            logger.error(f"WARNING: NULL/empty JournalID for journal number {journal_number}")
                            logger.error(f"  Full journal object: {journal}")
                        
                        journal_data = [
                            journal_id,
                            journal_number,
                            journal.get('Reference'),
                            None,  # Journals don't have Notes field
                            self._parse_xero_date(journal.get('JournalDate')),
                            None,  # Journals don't have Status field
                            journal.get('SourceID'),  # Source transaction ID (e.g., InvoiceID)
                            journal.get('SourceType'),  # Source type (e.g., ACCINVOICE)
                            self._parse_xero_date(journal.get('CreatedDateUTC'))
                        ]
                        
                        try:
                            cursor.execute(journal_insert, journal_data)
                            journal_count += 1
                        except Exception as e:
                            logger.error(f"Failed to insert journal {journal_number}: {str(e)}")
                            logger.error(f"  JournalID: {journal_id}")
                            logger.error(f"  Journal data: {journal_data}")
                            # Continue with next journal rather than failing entire batch
                            continue
                        
                        # Insert journal lines
                        for line in journal.get('JournalLines', []):
                            line_id = f"{journal.get('JournalID')}_{line.get('JournalLineID')}"
                            
                            # Extract tracking categories (up to 2)
                            tracking1_name = None
                            tracking1_option = None
                            tracking2_name = None
                            tracking2_option = None
                            
                            tracking_list = line.get('Tracking', [])
                            if tracking_list and len(tracking_list) > 0:
                                tracking1_name = tracking_list[0].get('Name')
                                tracking1_option = tracking_list[0].get('Option')
                                tracking_found_count += 1
                                # Log first few tracking entries found for diagnostic purposes
                                if tracking_found_count <= 5:
                                    logger.info(f"TRACKING FOUND in journal {journal_number}: {tracking_list}")
                            if tracking_list and len(tracking_list) > 1:
                                tracking2_name = tracking_list[1].get('Name')
                                tracking2_option = tracking_list[1].get('Option')
                            
                            line_data = [
                                line_id,
                                journal.get('JournalID'),
                                line.get('AccountID'),
                                line.get('AccountCode'),
                                line.get('Description'),
                                float(line.get('NetAmount', 0)),
                                float(line.get('TaxAmount', 0)),
                                tracking1_name,
                                tracking1_option,
                                tracking2_name,
                                tracking2_option
                            ]
                            
                            cursor.execute(line_insert, line_data)
                            line_count += 1
                    
                    # Log journal number range for this batch
                    if batch_records:
                        first_num = batch_records[0].get('JournalNumber', 'NULL')
                        last_num = batch_records[-1].get('JournalNumber', 'NULL')
                        logger.info(f"Processing batch with journal numbers: {first_num} to {last_num}")
                    
                    # COMMIT BATCH
                    logger.info(f"About to commit batch: {journal_count} journals, {line_count} lines...")
                    
                    # Check DB state before commit (get actual total for verification)
                    cursor.execute("SELECT COUNT(*) FROM xero.journals")
                    count_before = cursor.fetchone()[0]
                    logger.info(f"Journals in DB before commit: {count_before}")
                    
                    try:
                        self.db_conn.commit()
                        logger.info("Commit executed successfully")
                    except Exception as e:
                        logger.error(f"Commit failed: {str(e)}")
                        raise
                    
                    # Close cursor after commit - critical for avoiding cursor state issues
                    cursor.close()
                    
                    # Create new cursor to verify commit
                    cursor = self.db_conn.cursor()
                    cursor.execute("SELECT COUNT(*) FROM xero.journals")
                    count_after = cursor.fetchone()[0]
                    logger.info(f"Journals in DB after commit: {count_after} (expected: {count_before + journal_count})")
                    cursor.close()
                    
                    if count_after != count_before + journal_count:
                        logger.warning(f"COMMIT VERIFICATION WARNING: Expected {count_before + journal_count}, got {count_after}")
                    else:
                        logger.info(f"✓ Commit verified: {journal_count} new journals added")
                    
                    total_synced += journal_count
                    
                    logger.info(f"✓ Batch committed: {journal_count} journals, {line_count} lines (session total: {total_synced})")
                    
                    # Clear batch for next iteration
                    batch_records = []
                
                time.sleep(1)  # Delay to avoid rate limiting
            
            # Process any remaining records in final batch
            if batch_records:
                # Create fresh cursor for final batch
                cursor = self.db_conn.cursor()
                
                journal_count = 0
                line_count = 0
                
                for journal in batch_records:
                    # Insert journal
                    journal_id = journal.get('JournalID')
                    journal_number = journal.get('JournalNumber')
                    
                    # DIAGNOSTIC: Log if journal_id is NULL or empty
                    if not journal_id:
                        logger.error(f"WARNING: NULL/empty JournalID for journal number {journal_number}")
                        logger.error(f"  Full journal object: {journal}")
                    
                    journal_data = [
                        journal_id,
                        journal_number,
                        journal.get('Reference'),
                        None,
                        self._parse_xero_date(journal.get('JournalDate')),
                        None,
                        journal.get('SourceID'),  # Source transaction ID (e.g., InvoiceID)
                        journal.get('SourceType'),  # Source type (e.g., ACCINVOICE)
                        self._parse_xero_date(journal.get('CreatedDateUTC'))
                    ]
                    
                    try:
                        cursor.execute(journal_insert, journal_data)
                        journal_count += 1
                    except Exception as e:
                        logger.error(f"Failed to insert journal {journal_number}: {str(e)}")
                        logger.error(f"  JournalID: {journal_id}")
                        logger.error(f"  Journal data: {journal_data}")
                        # Continue with next journal rather than failing entire batch
                        continue
                    
                    # Insert journal lines
                    for line in journal.get('JournalLines', []):
                        line_id = f"{journal.get('JournalID')}_{line.get('JournalLineID')}"
                        
                        # Extract tracking categories (up to 2)
                        tracking1_name = None
                        tracking1_option = None
                        tracking2_name = None
                        tracking2_option = None
                        
                        tracking_list = line.get('Tracking', [])
                        if tracking_list and len(tracking_list) > 0:
                            tracking1_name = tracking_list[0].get('Name')
                            tracking1_option = tracking_list[0].get('Option')
                            tracking_found_count += 1
                            # Log first few tracking entries found for diagnostic purposes
                            if tracking_found_count <= 5:
                                logger.info(f"TRACKING FOUND in journal {journal_number}: {tracking_list}")
                        if tracking_list and len(tracking_list) > 1:
                            tracking2_name = tracking_list[1].get('Name')
                            tracking2_option = tracking_list[1].get('Option')
                        
                        line_data = [
                            line_id,
                            journal.get('JournalID'),
                            line.get('AccountID'),
                            line.get('AccountCode'),
                            line.get('Description'),
                            float(line.get('NetAmount', 0)),
                            float(line.get('TaxAmount', 0)),
                            tracking1_name,
                            tracking1_option,
                            tracking2_name,
                            tracking2_option
                        ]
                        
                        cursor.execute(line_insert, line_data)
                        line_count += 1
                
                # COMMIT FINAL BATCH
                self.db_conn.commit()
                cursor.close()  # Close cursor after final commit
                total_synced += journal_count
                
                logger.info(f"✓ Final batch committed: {journal_count} journals, {line_count} lines (total: {total_synced})")
                logger.info(f"TRACKING SUMMARY: Found tracking data on {tracking_found_count} journal lines")
            
            # Mark sync as completed
            sync_timestamp = datetime.now()
            self._update_sync_progress(sync_type, completed=True, modified_after=sync_timestamp)
            
            # Update sync metadata for periodic resync tracking
            cursor = self.db_conn.cursor()
            
            # Log final journal count first
            cursor.execute("SELECT COUNT(*), MAX(journal_number) FROM xero.journals")
            total_count, max_number = cursor.fetchone()
            logger.info(f"Journal sync complete. Total in DB: {total_count}, Max journal number: {max_number}")
            
            # Update metadata based on sync type
            if is_full_sync:
                cursor.execute("""
                    INSERT INTO xero.sync_metadata (entity_type, last_full_sync, last_incremental_sync, updated_at)
                    VALUES ('journals', NOW(), NOW(), NOW())
                    ON CONFLICT (entity_type) DO UPDATE SET
                        last_full_sync = NOW(),
                        last_incremental_sync = NOW(),
                        updated_at = NOW()
                """)
                logger.info("Updated sync metadata: Full sync completed")
            else:
                cursor.execute("""
                    INSERT INTO xero.sync_metadata (entity_type, last_incremental_sync, updated_at)
                    VALUES ('journals', NOW(), NOW())
                    ON CONFLICT (entity_type) DO UPDATE SET
                        last_incremental_sync = NOW(),
                        updated_at = NOW()
                """)
                logger.info("Updated sync metadata: Incremental sync completed")
            
            self.db_conn.commit()
            cursor.close()
            
            logger.info(f"Successfully synced {total_synced} journals")
            self._log_sync(sync_type, total_synced, 'success', None, start_time)
            
            return total_synced
        
        except Exception as e:
            self.db_conn.rollback()
            self._update_sync_progress(sync_type, status='failed')
            logger.error(f"Failed to sync journals: {str(e)}")
            self._log_sync(sync_type, 0, 'failed', str(e), start_time)
            raise
    
    def _load_tokens_from_db(self):
        """Load tokens from database"""
        try:
            cursor = self.db_conn.cursor()
            cursor.execute("""
                SELECT refresh_token, access_token, access_token_expires_at
                FROM xero.tokens
                ORDER BY updated_at DESC
                LIMIT 1
            """)
            
            row = cursor.fetchone()
            if row and row[0] and row[0] != 'PLACEHOLDER':
                db_refresh_token, db_access_token, db_expires_at = row
                
                # Update refresh token if it's different from what we have
                if db_refresh_token != self.refresh_token:
                    logger.info("Loaded updated refresh token from database")
                    self.refresh_token = db_refresh_token
                
                # Load cached access token if still valid
                if db_access_token and db_expires_at and db_expires_at > datetime.now():
                    self.access_token = db_access_token
                    self.access_token_expires_at = db_expires_at
                    logger.info(f"Loaded cached access token from database (expires at {db_expires_at.strftime('%Y-%m-%d %H:%M:%S')})")
        except Exception as e:
            logger.warning(f"Failed to load tokens from database: {str(e)}")
    
    def _save_tokens_to_db(self):
        """Save current tokens to database"""
        try:
            cursor = self.db_conn.cursor()
            
            # Update or insert the latest tokens
            cursor.execute("""
                INSERT INTO xero.tokens (refresh_token, access_token, access_token_expires_at, updated_at)
                VALUES (%s, %s, %s, NOW())
                ON CONFLICT (id) DO UPDATE SET
                    refresh_token = EXCLUDED.refresh_token,
                    access_token = EXCLUDED.access_token,
                    access_token_expires_at = EXCLUDED.access_token_expires_at,
                    updated_at = NOW()
                WHERE xero.tokens.id = (SELECT MAX(id) FROM xero.tokens)
            """, (self.refresh_token, self.access_token, self.access_token_expires_at))
            
            self.db_conn.commit()
            logger.info("Saved tokens to database")
            
            # Log the new refresh token for updating GitHub Secrets if needed
            logger.info(f"IMPORTANT: New refresh token (update GitHub Secret if needed): {self.refresh_token[:20]}...")
        except Exception as e:
            logger.warning(f"Failed to save tokens to database: {str(e)}")
    
    def _is_token_expired(self, buffer_minutes=0):
        """Check if access token is expired or will expire soon
        
        Args:
            buffer_minutes: Consider token expired if it expires within this many minutes
        """
        if not self.access_token_expires_at:
            return True
        
        # Add buffer time to check if token is expiring soon
        expiry_threshold = datetime.now() + timedelta(minutes=buffer_minutes)
        return self.access_token_expires_at <= expiry_threshold
    
    def _get_sync_progress(self, sync_type):
        """Get sync progress for a specific entity type"""
        try:
            cursor = self.db_conn.cursor()
            cursor.execute("""
                SELECT last_synced_page, last_sync_completed_at, last_modified_after, sync_status
                FROM xero.sync_progress
                WHERE sync_type = %s
            """, (sync_type,))
            
            row = cursor.fetchone()
            if row:
                return {
                    'last_page': row[0] or 0,
                    'last_completed': row[1],
                    'last_modified': row[2],
                    'status': row[3]
                }
            return {'last_page': 0, 'last_completed': None, 'last_modified': None, 'status': 'idle'}
        except Exception as e:
            logger.warning(f"Failed to get sync progress for {sync_type}: {str(e)}")
            return {'last_page': 0, 'last_completed': None, 'last_modified': None, 'status': 'idle'}
    
    def _update_sync_progress(self, sync_type, page=None, status=None, completed=False, modified_after=None):
        """Update sync progress for a specific entity type"""
        try:
            cursor = self.db_conn.cursor()
            
            if completed:
                # Mark sync as completed
                cursor.execute("""
                    UPDATE xero.sync_progress
                    SET last_synced_page = 0,
                        last_sync_completed_at = NOW(),
                        last_modified_after = %s,
                        sync_status = 'completed',
                        updated_at = NOW()
                    WHERE sync_type = %s
                """, (modified_after or datetime.now(), sync_type))
            elif page is not None:
                # Update progress mid-sync
                cursor.execute("""
                    UPDATE xero.sync_progress
                    SET last_synced_page = %s,
                        sync_status = %s,
                        updated_at = NOW()
                    WHERE sync_type = %s
                """, (page, status or 'running', sync_type))
            elif status:
                # Update status only
                cursor.execute("""
                    UPDATE xero.sync_progress
                    SET sync_status = %s,
                        updated_at = NOW()
                    WHERE sync_type = %s
                """, (status, sync_type))
            
            self.db_conn.commit()
        except Exception as e:
            logger.warning(f"Failed to update sync progress for {sync_type}: {str(e)}")
    
    def _log_sync(self, sync_type, records_synced, status, error_message, start_time):
        """Log sync operation to database"""
        try:
            cursor = self.db_conn.cursor()
            duration = (datetime.now() - start_time).total_seconds()
            
            cursor.execute("""
                INSERT INTO xero.sync_log
                (sync_type, records_synced, status, error_message, started_at, completed_at, duration_seconds)
                VALUES (%s, %s, %s, %s, %s, NOW(), %s)
            """, (sync_type, records_synced, status, error_message, start_time, int(duration)))
            
            self.db_conn.commit()
        except Exception as e:
            logger.warning(f"Failed to log sync operation: {str(e)}")
    
    def run_full_sync(self, force_journal_resync=False):
        """Run full sync of all data
        
        Args:
            force_journal_resync: If True, forces a complete resync of all journals (catches updates)
        """
        try:
            self.connect_db()
            
            logger.info("Starting full Xero sync...")
            total_records = 0
            
            total_records += self.sync_accounts()
            total_records += self.sync_contacts()
            total_records += self.sync_invoices()
            total_records += self.sync_journals(force_full_resync=force_journal_resync)
            
            logger.info(f"Full sync completed. Total records synced: {total_records}")
            print(f"SUCCESS: Synced {total_records} total records")
            
            return True
        
        except Exception as e:
            logger.error(f"Full sync failed: {str(e)}")
            print(f"ERROR: Sync failed - {str(e)}")
            return False
        
        finally:
            self.close_db()


def main():
    try:
        syncer = XeroSync()
        success = syncer.run_full_sync()
        sys.exit(0 if success else 1)
    except Exception as e:
        logger.error(f"Unexpected error: {str(e)}")
        print(f"ERROR: {str(e)}")
        sys.exit(1)


if __name__ == '__main__':
    main()
