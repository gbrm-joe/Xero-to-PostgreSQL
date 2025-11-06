-- SQL Migration: Make invoice_number nullable for DELETED invoices
-- Run this on your existing database

ALTER TABLE xero.invoices 
ALTER COLUMN invoice_number DROP NOT NULL;

-- Verify the change
SELECT 
    table_name, 
    column_name, 
    data_type, 
    is_nullable
FROM information_schema.columns
WHERE table_schema = 'xero' 
    AND table_name = 'invoices'
    AND column_name = 'invoice_number';
