-- Create sync_metadata table to track full sync dates
-- This helps manage periodic full resyncs to catch updates that incremental syncs miss

CREATE TABLE IF NOT EXISTS xero.sync_metadata (
    entity_type VARCHAR(50) PRIMARY KEY,
    last_full_sync TIMESTAMP,
    last_incremental_sync TIMESTAMP,
    metadata JSONB,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);

-- Insert initial records for tracking
INSERT INTO xero.sync_metadata (entity_type, last_full_sync, last_incremental_sync)
VALUES 
    ('journals', NULL, NULL),
    ('invoices', NULL, NULL),
    ('contacts', NULL, NULL),
    ('accounts', NULL, NULL)
ON CONFLICT (entity_type) DO NOTHING;

-- Add comment
COMMENT ON TABLE xero.sync_metadata IS 'Tracks sync metadata including last full sync dates for periodic resync strategy';
COMMENT ON COLUMN xero.sync_metadata.entity_type IS 'Type of entity being synced (journals, invoices, etc.)';
COMMENT ON COLUMN xero.sync_metadata.last_full_sync IS 'Timestamp of last full resync (all records)';
COMMENT ON COLUMN xero.sync_metadata.last_incremental_sync IS 'Timestamp of last incremental sync (new/modified records only)';
COMMENT ON COLUMN xero.sync_metadata.metadata IS 'Additional metadata in JSON format';
