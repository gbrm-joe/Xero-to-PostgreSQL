-- Xero Sync Schema Setup
-- Run this once to create the required xero schema and tables
-- All Xero data will be organized in the 'xero' schema within this database

CREATE SCHEMA IF NOT EXISTS xero;

-- Tracking Categories (synced from Xero TrackingCategories endpoint)
CREATE TABLE IF NOT EXISTS xero.tracking_categories (
    tracking_category_id VARCHAR(36) PRIMARY KEY,
    name VARCHAR(255) NOT NULL,
    status VARCHAR(20),
    synced_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Tracking Options (nested under categories)
CREATE TABLE IF NOT EXISTS xero.tracking_options (
    tracking_option_id VARCHAR(36) PRIMARY KEY,
    tracking_category_id VARCHAR(36) REFERENCES xero.tracking_categories(tracking_category_id),
    name VARCHAR(255) NOT NULL,
    status VARCHAR(20),
    is_deleted BOOLEAN DEFAULT FALSE,
    is_archived BOOLEAN DEFAULT FALSE,
    synced_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_tracking_options_category ON xero.tracking_options(tracking_category_id);

CREATE TABLE IF NOT EXISTS xero.accounts (
    id SERIAL PRIMARY KEY,
    account_id VARCHAR(36) UNIQUE NOT NULL,
    code VARCHAR(10),
    name VARCHAR(255) NOT NULL,
    account_type VARCHAR(50),
    description TEXT,
    enable_payments BOOLEAN,
    status VARCHAR(20),
    updated_at TIMESTAMP,
    synced_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS xero.contacts (
    id SERIAL PRIMARY KEY,
    contact_id VARCHAR(36) UNIQUE NOT NULL,
    name VARCHAR(255) NOT NULL,
    email_address VARCHAR(255),
    phones TEXT,
    addresses TEXT,
    tax_number VARCHAR(50),
    contact_status VARCHAR(20),
    updated_at TIMESTAMP,
    synced_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_contacts_email ON xero.contacts(email_address);
CREATE INDEX IF NOT EXISTS idx_contacts_name ON xero.contacts(name);

CREATE TABLE IF NOT EXISTS xero.invoices (
    id SERIAL PRIMARY KEY,
    invoice_id VARCHAR(36) UNIQUE NOT NULL,
    invoice_number VARCHAR(255),
    contact_id VARCHAR(36),
    invoice_type VARCHAR(20),
    status VARCHAR(20),
    line_amount_types VARCHAR(20),
    invoice_date DATE,
    due_date DATE,
    expected_payment_date DATE,
    reference VARCHAR(255),
    branding_theme_id VARCHAR(36),
    sub_total DECIMAL(15, 2),
    total_tax DECIMAL(15, 2),
    total DECIMAL(15, 2),
    currency_code VARCHAR(3),
    updated_at TIMESTAMP,
    synced_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_invoices_number ON xero.invoices(invoice_number);
CREATE INDEX IF NOT EXISTS idx_invoices_contact ON xero.invoices(contact_id);
CREATE INDEX IF NOT EXISTS idx_invoices_status ON xero.invoices(status);

CREATE TABLE IF NOT EXISTS xero.invoice_items (
    id SERIAL PRIMARY KEY,
    invoice_item_id VARCHAR(100) UNIQUE NOT NULL,
    invoice_id VARCHAR(36) NOT NULL,
    description TEXT,
    quantity DECIMAL(10, 2),
    unit_amount DECIMAL(15, 2),
    tax_type VARCHAR(50),
    tax_amount DECIMAL(15, 2),
    line_amount DECIMAL(15, 2),
    account_code VARCHAR(10),
    account_id VARCHAR(36),
    -- Tracking category/option names (denormalized for quick access)
    tracking1_name TEXT,
    tracking1_option TEXT,
    tracking2_name TEXT,
    tracking2_option TEXT,
    -- Tracking category/option IDs (normalized references)
    tracking1_category_id VARCHAR(36),
    tracking1_option_id VARCHAR(36),
    tracking2_category_id VARCHAR(36),
    tracking2_option_id VARCHAR(36),
    synced_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_invoice_items_tracking1 ON xero.invoice_items(tracking1_category_id, tracking1_option_id);
CREATE INDEX IF NOT EXISTS idx_invoice_items_tracking2 ON xero.invoice_items(tracking2_category_id, tracking2_option_id);

CREATE TABLE IF NOT EXISTS xero.journals (
    id SERIAL PRIMARY KEY,
    journal_id VARCHAR(36) UNIQUE NOT NULL,
    journal_number BIGINT,
    reference VARCHAR(255),
    notes TEXT,
    journal_date DATE,
    status VARCHAR(20),
    source_id VARCHAR(36),
    source_type VARCHAR(50),
    updated_at TIMESTAMP,
    synced_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_journals_number ON xero.journals(journal_number);
CREATE INDEX IF NOT EXISTS idx_journals_date ON xero.journals(journal_date);

CREATE TABLE IF NOT EXISTS xero.journal_lines (
    id SERIAL PRIMARY KEY,
    journal_line_id VARCHAR(100) UNIQUE NOT NULL,
    journal_id VARCHAR(36) NOT NULL,
    account_id VARCHAR(36),
    account_code VARCHAR(10),
    description TEXT,
    net_amount DECIMAL(15, 2),
    tax_amount DECIMAL(15, 2),
    -- Tracking category/option names (denormalized for quick access)
    tracking1_name TEXT,
    tracking1_option TEXT,
    tracking2_name TEXT,
    tracking2_option TEXT,
    -- Tracking category/option IDs (normalized references)
    tracking1_category_id VARCHAR(36),
    tracking1_option_id VARCHAR(36),
    tracking2_category_id VARCHAR(36),
    tracking2_option_id VARCHAR(36),
    synced_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_journal_lines_tracking1 ON xero.journal_lines(tracking1_category_id, tracking1_option_id);
CREATE INDEX IF NOT EXISTS idx_journal_lines_tracking2 ON xero.journal_lines(tracking2_category_id, tracking2_option_id);

CREATE TABLE IF NOT EXISTS xero.sync_log (
    id SERIAL PRIMARY KEY,
    sync_type VARCHAR(50),
    records_synced INT,
    status VARCHAR(20),
    error_message TEXT,
    started_at TIMESTAMP,
    completed_at TIMESTAMP,
    duration_seconds INT
);

CREATE INDEX IF NOT EXISTS idx_sync_log_date ON xero.sync_log(completed_at);
