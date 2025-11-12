# Journal Update Tracking Implementation

## Problem
The Xero Journals API doesn't support reliable change detection:
- The `offset` parameter only fetches journals with numbers greater than the specified value
- Xero explicitly warns against using Modified After: "The If-Modified-Since header may cause missing journals"
- This means edited journals in Xero wouldn't be synced to PostgreSQL

## Solution: Periodic Full Resync

### 1. Database Schema Changes
Added `sync_metadata` table to track full sync dates:
```sql
CREATE TABLE xero.sync_metadata (
    entity_type VARCHAR(50) PRIMARY KEY,
    last_full_sync TIMESTAMP,
    last_incremental_sync TIMESTAMP,
    metadata JSONB,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);
```

### 2. Sync Logic Changes
The `sync_journals` method now supports two modes:

#### Incremental Sync (Daily)
- Only fetches new journals (journal_number > last synced)
- Fast and efficient
- Doesn't catch updates to existing journals

#### Full Resync (Weekly or Manual)
- Starts from offset 0
- Re-syncs ALL journals from Xero
- Catches any edits/updates made in Xero
- Uses `ON CONFLICT DO UPDATE` to overwrite existing data

### 3. Automatic Full Resync Trigger
The system automatically performs a full resync when:
- No previous full sync recorded
- Last full sync was 7+ days ago
- Manual force flag is set

### 4. Usage

#### Default Daily Sync
```python
syncer = XeroSync()
syncer.run_full_sync()  # Uses incremental for journals
```

#### Force Full Journal Resync
```python
syncer = XeroSync()
syncer.run_full_sync(force_journal_resync=True)  # Forces full journal resync
```

#### Direct Journal Sync
```python
syncer = XeroSync()
syncer.connect_db()
syncer.sync_journals(force_full_resync=True)  # Force full resync
```

### 5. Schedule Recommendations

#### Cron Configuration
```bash
# Daily incremental sync at 2 AM
0 2 * * * python /path/to/xero_sync.py

# Weekly full resync on Sunday at 3 AM
0 3 * * 0 python -c "from xero_sync import XeroSync; XeroSync().run_full_sync(force_journal_resync=True)"
```

### 6. Monitoring

Check sync metadata:
```sql
-- View last full sync dates
SELECT 
    entity_type,
    last_full_sync,
    last_incremental_sync,
    EXTRACT(DAY FROM NOW() - last_full_sync) as days_since_full
FROM xero.sync_metadata;

-- Check if journals need full resync
SELECT 
    CASE 
        WHEN last_full_sync IS NULL THEN 'Never synced - needs full sync'
        WHEN EXTRACT(DAY FROM NOW() - last_full_sync) >= 7 THEN 'Needs full sync'
        ELSE 'OK - incremental sync only'
    END as sync_status
FROM xero.sync_metadata
WHERE entity_type = 'journals';
```

## Comparison: Invoices vs Journals

### Invoices (Real-time Updates)
- Uses `UpdatedDateUTC>=DateTime(...)` filter
- Catches all changes immediately
- Efficient incremental sync

### Journals (Periodic Updates)
- Can't filter by modification date reliably
- Weekly full resync to catch edits
- Trade-off: eventual consistency vs API limitations

## Impact

- **Daily syncs**: Fast (only new journals)
- **Weekly syncs**: Slower but comprehensive
- **Data integrity**: All journal edits caught within 7 days
- **Manual override**: Available for critical updates

## Important Notes

1. **Journal edits in Xero** will not be reflected immediately
2. **Maximum delay** for catching edits is 7 days (configurable)
3. **Full resync performance** depends on total journal count
4. **ON CONFLICT DO UPDATE** ensures latest data overwrites old
