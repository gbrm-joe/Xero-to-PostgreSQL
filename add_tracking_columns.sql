-- Migration: Add tracking category support to invoice_items and enhance journal_lines
-- Run this to add multiple tracking category support

-- Add tracking columns to invoice_items table
-- Xero supports up to 2 tracking categories per line item
ALTER TABLE xero.invoice_items 
ADD COLUMN IF NOT EXISTS tracking1_name TEXT,
ADD COLUMN IF NOT EXISTS tracking1_option TEXT,
ADD COLUMN IF NOT EXISTS tracking2_name TEXT,
ADD COLUMN IF NOT EXISTS tracking2_option TEXT;

-- Rename existing journal_lines tracking columns to match new naming convention
-- First, check if the new columns already exist (idempotent migration)
DO $$
BEGIN
    -- Check if tracking1_name exists
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns 
                   WHERE table_schema = 'xero' AND table_name = 'journal_lines' AND column_name = 'tracking1_name') THEN
        -- If new columns don't exist, add them
        ALTER TABLE xero.journal_lines ADD COLUMN tracking1_name TEXT;
        ALTER TABLE xero.journal_lines ADD COLUMN tracking1_option TEXT;
        ALTER TABLE xero.journal_lines ADD COLUMN tracking2_name TEXT;
        ALTER TABLE xero.journal_lines ADD COLUMN tracking2_option TEXT;
        
        -- Migrate existing data from old columns to new columns
        UPDATE xero.journal_lines SET tracking1_name = tracking_name, tracking1_option = tracking_option;
        
        -- Drop old columns (optional - uncomment if you want to clean up)
        -- ALTER TABLE xero.journal_lines DROP COLUMN IF EXISTS tracking_name;
        -- ALTER TABLE xero.journal_lines DROP COLUMN IF EXISTS tracking_option;
    END IF;
END $$;

-- Add indexes for tracking queries
CREATE INDEX IF NOT EXISTS idx_invoice_items_tracking1 ON xero.invoice_items(tracking1_name, tracking1_option);
CREATE INDEX IF NOT EXISTS idx_invoice_items_tracking2 ON xero.invoice_items(tracking2_name, tracking2_option);
CREATE INDEX IF NOT EXISTS idx_journal_lines_tracking1 ON xero.journal_lines(tracking1_name, tracking1_option);
CREATE INDEX IF NOT EXISTS idx_journal_lines_tracking2 ON xero.journal_lines(tracking2_name, tracking2_option);

-- Verify the changes
SELECT table_name, column_name, data_type 
FROM information_schema.columns 
WHERE table_schema = 'xero' 
AND table_name IN ('invoice_items', 'journal_lines')
AND column_name LIKE 'tracking%'
ORDER BY table_name, column_name;
