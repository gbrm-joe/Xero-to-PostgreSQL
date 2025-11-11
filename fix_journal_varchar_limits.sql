-- Fix VARCHAR(255) constraint issues in journal tables
-- Changes character varying columns to TEXT to handle longer values from Xero

-- Fix journals table
ALTER TABLE xero.journals 
ALTER COLUMN reference TYPE TEXT,
ALTER COLUMN status TYPE TEXT;

-- Fix journal_lines table  
ALTER TABLE xero.journal_lines
ALTER COLUMN description TYPE TEXT,
ALTER COLUMN tracking_name TYPE TEXT,
ALTER COLUMN tracking_option TYPE TEXT;

-- Note: journal_id, account_id, account_code are left as character varying
-- as these are IDs/codes that won't exceed 255 characters
