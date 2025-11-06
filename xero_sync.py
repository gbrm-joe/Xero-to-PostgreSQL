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
from datetime import datetime
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
        
        self.access_token = None
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
    
    def get_access_token(self):
        """Get a new access token using the refresh token"""
        try:
            url = 'https://identity.xero.com/connect/token'
            data = {
                'grant_type': 'refresh_token',
                'client_id': self.client_id,
                'client_secret': self.client_secret,
                'refresh_token': self.refresh_token
            }
            
            response = requests.post(url, data=data, timeout=10)
            response.raise_for_status()
            
            self.access_token = response.json()['access_token']
            logger.info("Successfully obtained new access token")
            return self.access_token
        except Exception as e:
            logger.error(f"Failed to get access token: {str(e)}")
            raise
    
    def _make_xero_request(self, endpoint, params=None, retry_count=0):
        """Make a request to the Xero API with rate limit handling"""
        if not self.access_token:
            self.get_access_token()
        
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
                    return self._make_xero_request(endpoint, params, retry_count + 1)
                else:
                    raise Exception("Rate limit exceeded after 3 retries")
            
            response.raise_for_status()
            return response.json()
        except requests.exceptions.HTTPError as e:
            if e.response.status_code != 429:  # Don't log 429 twice
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
            # Fetch ALL accounts from Xero with pagination
            all_accounts = []
            page = 1
            page_size = 100
            max_pages = 200
            
            while page <= max_pages:
                logger.info(f"Fetching accounts page {page}...")
                response = self._make_xero_request('Accounts', params={'page': page})
                accounts = response.get('Accounts', [])
                
                if not accounts:
                    break
                
                all_accounts.extend(accounts)
                logger.info(f"Retrieved {len(accounts)} accounts (total so far: {len(all_accounts)})")
                
                # Stop if we got fewer records than expected (last page)
                if len(accounts) < page_size:
                    break
                
                page += 1
                
                # Add small delay to avoid rate limiting
                if page <= max_pages:
                    time.sleep(1)
            
            if not all_accounts:
                logger.info("No accounts to sync")
                return 0
            
            accounts = all_accounts
            
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
                response = self._make_xero_request('Contacts', params={'page': page})
                contacts = response.get('Contacts', [])
                
                if not contacts:
                    break
                
                all_contacts.extend(contacts)
                logger.info(f"Retrieved {len(contacts)} contacts (total so far: {len(all_contacts)})")
                
                # Stop if we got fewer records than expected (last page)
                if len(contacts) < page_size:
                    break
                
                page += 1
                
                # Add small delay to avoid rate limiting
                if page <= max_pages:
                    time.sleep(1)
            
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
        """Sync invoices and line items from Xero"""
        logger.info("Starting invoice sync...")
        start_time = datetime.now()
        
        try:
            # Fetch ALL invoices from Xero with pagination
            all_invoices = []
            page = 1
            page_size = 100
            max_pages = 2000
            
            while page <= max_pages:
                logger.info(f"Fetching invoices page {page}...")
                response = self._make_xero_request('Invoices', params={'page': page})
                invoices = response.get('Invoices', [])
                
                if not invoices:
                    break
                
                all_invoices.extend(invoices)
                logger.info(f"Retrieved {len(invoices)} invoices (total so far: {len(all_invoices)})")
                
                # Stop if we got fewer records than expected (last page)
                if len(invoices) < page_size:
                    break
                
                page += 1
                
                # Add small delay to avoid rate limiting
                if page <= max_pages:
                    time.sleep(1)
            
            if not all_invoices:
                logger.info("No invoices to sync")
                return 0
            
            invoices = all_invoices
            
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
                 tax_amount, line_amount, account_code, account_id, synced_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())
                ON CONFLICT (invoice_item_id) DO UPDATE SET
                    line_amount = EXCLUDED.line_amount,
                    synced_at = NOW()
            """
            
            invoice_count = 0
            item_count = 0
            
            for invoice in invoices:
                # Insert invoice
                invoice_data = [
                    invoice.get('InvoiceID'),
                    invoice.get('InvoiceNumber'),
                    invoice.get('Contact', {}).get('ContactID'),
                    invoice.get('Type'),
                    invoice.get('Status'),
                    invoice.get('LineAmountTypes'),
                    self._parse_xero_date(invoice.get('InvoiceDate')),
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
                        item.get('AccountID')
                    ]
                    
                    cursor.execute(item_insert, item_data)
                    item_count += 1
            
            self.db_conn.commit()
            
            logger.info(f"Successfully synced {invoice_count} invoices with {item_count} line items")
            self._log_sync('invoices', invoice_count, 'success', None, start_time)
            
            return invoice_count
        
        except Exception as e:
            self.db_conn.rollback()
            logger.error(f"Failed to sync invoices: {str(e)}")
            self._log_sync('invoices', 0, 'failed', str(e), start_time)
            raise
    
    def sync_journals(self):
        """Sync journals and journal lines from Xero"""
        logger.info("Starting journal sync...")
        start_time = datetime.now()
        
        try:
            # Fetch ALL journals from Xero with pagination
            all_journals = []
            page = 1
            page_size = 100
            max_pages = 2000
            
            while page <= max_pages:
                logger.info(f"Fetching journals page {page}...")
                response = self._make_xero_request('Journals', params={'page': page})
                journals = response.get('Journals', [])
                
                if not journals:
                    break
                
                all_journals.extend(journals)
                logger.info(f"Retrieved {len(journals)} journals (total so far: {len(all_journals)})")
                
                # Stop if we got fewer records than expected (last page)
                if len(journals) < page_size:
                    break
                
                page += 1
                
                # Add small delay to avoid rate limiting
                if page <= max_pages:
                    time.sleep(1)
            
            if not all_journals:
                logger.info("No journals to sync")
                return 0
            
            journals = all_journals
            
            cursor = self.db_conn.cursor()
            
            journal_insert = """
                INSERT INTO xero.journals
                (journal_id, journal_number, reference, notes, journal_date, status, updated_at, synced_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, NOW())
                ON CONFLICT (journal_id) DO UPDATE SET
                    status = EXCLUDED.status,
                    synced_at = NOW()
            """
            
            line_insert = """
                INSERT INTO xero.journal_lines
                (journal_line_id, journal_id, account_id, account_code, description, net_amount,
                 tax_amount, tracking_name, tracking_option, synced_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())
                ON CONFLICT (journal_line_id) DO UPDATE SET
                    synced_at = NOW()
            """
            
            journal_count = 0
            line_count = 0
            
            for journal in journals:
                # Insert journal
                journal_data = [
                    journal.get('JournalID'),
                    journal.get('JournalNumber'),
                    journal.get('Reference'),
                    None,  # Journals don't have Notes field
                    self._parse_xero_date(journal.get('JournalDate')),
                    None,  # Journals don't have Status field
                    self._parse_xero_date(journal.get('CreatedDateUTC'))
                ]
                
                cursor.execute(journal_insert, journal_data)
                journal_count += 1
                
                # Insert journal lines
                for line in journal.get('JournalLines', []):
                    line_id = f"{journal.get('JournalID')}_{line.get('JournalLineID')}"
                    tracking_name = ''
                    tracking_option = ''
                    
                    if 'Tracking' in line and line['Tracking']:
                        tracking_list = line['Tracking']
                        if tracking_list:
                            tracking_name = tracking_list[0].get('Name', '')
                            tracking_option = tracking_list[0].get('Option', '')
                    
                    line_data = [
                        line_id,
                        journal.get('JournalID'),
                        line.get('AccountID'),
                        line.get('AccountCode'),
                        line.get('Description'),
                        float(line.get('NetAmount', 0)),
                        float(line.get('TaxAmount', 0)),
                        tracking_name,
                        tracking_option
                    ]
                    
                    cursor.execute(line_insert, line_data)
                    line_count += 1
            
            self.db_conn.commit()
            
            logger.info(f"Successfully synced {journal_count} journals with {line_count} lines")
            self._log_sync('journals', journal_count, 'success', None, start_time)
            
            return journal_count
        
        except Exception as e:
            self.db_conn.rollback()
            logger.error(f"Failed to sync journals: {str(e)}")
            self._log_sync('journals', 0, 'failed', str(e), start_time)
            raise
    
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
    
    def run_full_sync(self):
        """Run full sync of all data"""
        try:
            self.connect_db()
            
            logger.info("Starting full Xero sync...")
            total_records = 0
            
            total_records += self.sync_accounts()
            total_records += self.sync_contacts()
            total_records += self.sync_invoices()
            total_records += self.sync_journals()
            
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
