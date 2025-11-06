-- SQL Migration to fix VARCHAR column lengths
-- Run this on your existing database to update the column sizes

-- Update invoice_items table
ALTER TABLE xero.invoice_items 
ALTER COLUMN invoice_item_id TYPE VARCHAR(100);

-- Update journal_lines table  
ALTER TABLE xero.journal_lines 
ALTER COLUMN journal_line_id TYPE VARCHAR(100);

-- Verify the changes
SELECT 
    table_name, 
    column_name, 
    data_type, 
    character_maximum_length
FROM information_schema.columns
WHERE table_schema = 'xero' 
    AND table_name IN ('invoice_items', 'journal_lines')
    AND column_name IN ('invoice_item_id', 'journal_line_id')
ORDER BY table_name, column_name;
