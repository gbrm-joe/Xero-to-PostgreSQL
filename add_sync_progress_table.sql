-- Create sync progress tracking table
-- This enables batch commits, resume capability, and incremental syncs

CREATE TABLE IF NOT EXISTS xero.sync_progress (
    sync_type VARCHAR(50) PRIMARY KEY,
    last_synced_page INTEGER DEFAULT 0,
    last_sync_completed_at TIMESTAMP,
    last_modified_after TIMESTAMP,
    total_records_synced INTEGER DEFAULT 0,
    sync_status VARCHAR(20) DEFAULT 'idle',  -- 'idle', 'running', 'completed', 'failed'
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);

-- Create index for faster status checks
CREATE INDEX IF NOT EXISTS idx_sync_progress_status ON xero.sync_progress(sync_status);

-- Insert initial rows for each sync type
INSERT INTO xero.sync_progress (sync_type, sync_status)
VALUES 
    ('accounts', 'idle'),
    ('contacts', 'idle'),
    ('invoices', 'idle'),
    ('journals', 'idle')
ON CONFLICT (sync_type) DO NOTHING;

COMMENT ON TABLE xero.sync_progress IS 'Tracks sync progress for batch commits and incremental syncs';
COMMENT ON COLUMN xero.sync_progress.sync_type IS 'Type of entity being synced (accounts, contacts, invoices, journals)';
COMMENT ON COLUMN xero.sync_progress.last_synced_page IS 'Last successfully synced page (for resume capability)';
COMMENT ON COLUMN xero.sync_progress.last_sync_completed_at IS 'Timestamp when last full/incremental sync completed successfully';
COMMENT ON COLUMN xero.sync_progress.last_modified_after IS 'Timestamp used for next incremental sync (ModifiedAfter parameter)';
COMMENT ON COLUMN xero.sync_progress.total_records_synced IS 'Running total of records synced in current session';
COMMENT ON COLUMN xero.sync_progress.sync_status IS 'Current status: idle, running, completed, failed';
