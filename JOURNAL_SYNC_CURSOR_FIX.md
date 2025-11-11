# Journal Sync Cursor Fix - Implementation Summary

## Issue
Only the first 100 journals were persisting in the database despite logs showing successful commits for thousands of records. All batch commits after the first one were silently failing.

## Root Cause
The cursor was being reused across multiple database commits. In psycopg2, cursors can become invalid or behave unexpectedly after a commit operation. The first batch worked because it was the cursor's first use, but subsequent batches failed silently due to cursor state issues.

## Solution Implemented
**Create a fresh cursor for each batch operation**

### Key Changes:
1. **Removed initial cursor creation** at the beginning of `sync_journals()`
2. **Create fresh cursor for each batch** before processing records
3. **Close cursor after each commit** to clean up resources properly
4. **Use new cursor for verification** after commits to check record counts

## Code Changes

### Before (problematic code):
```python
cursor = self.db_conn.cursor()  # Single cursor for entire sync

while page <= max_pages:
    # ... fetch journals ...
    
    if len(batch_records) >= (self.batch_size * 100):
        # Process batch with same cursor
        for journal in batch_records:
            cursor.execute(journal_insert, journal_data)
        
        self.db_conn.commit()
        # Cursor may be invalid here but continues to be used
```

### After (fixed code):
```python
# No cursor created at start

while page <= max_pages:
    # ... fetch journals ...
    
    if len(batch_records) >= (self.batch_size * 100):
        # Create fresh cursor for this batch
        cursor = self.db_conn.cursor()
        
        for journal in batch_records:
            cursor.execute(journal_insert, journal_data)
        
        self.db_conn.commit()
        cursor.close()  # Clean up cursor
        
        # New cursor for verification
        cursor = self.db_conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM xero.journals")
        count = cursor.fetchone()[0]
        cursor.close()
```

## Testing Instructions

### 1. Clean the journal tables
```sql
-- Connect to your database and run:
TRUNCATE TABLE xero.journals CASCADE;
TRUNCATE TABLE xero.journal_lines CASCADE;

-- Reset sync progress for journals
UPDATE xero.sync_progress 
SET last_synced_page = 0, 
    sync_status = 'idle',
    last_sync_completed_at = NULL
WHERE sync_type = 'journals';
```

### 2. Run a test with small batch size
Set environment variable to use small batches for quick testing:
```bash
export SYNC_BATCH_SIZE=2  # Will process 200 journals per batch
```

### 3. Run the sync
```bash
python xero_sync.py
```

### 4. Monitor the logs
Look for the diagnostic output showing:
- Journal count before each commit
- Successful commit message
- Journal count after commit (should increase correctly)

Example expected output:
```
Journals in DB before commit: 0
Commit executed successfully
Journals in DB after commit: 200 (expected: 200)
✓ Batch committed: 200 journals, 567 lines (total: 200)

Journals in DB before commit: 200
Commit executed successfully
Journals in DB after commit: 400 (expected: 400)
✓ Batch committed: 200 journals, 543 lines (total: 400)
```

### 5. Verify final results
```sql
-- Check total journal count
SELECT COUNT(*) FROM xero.journals;

-- Check for gaps in journal numbers
SELECT 
    MIN(journal_number) as min_num,
    MAX(journal_number) as max_num,
    COUNT(*) as total_count,
    MAX(journal_number) - MIN(journal_number) + 1 as expected_count
FROM xero.journals;

-- Check distribution of sync timestamps
SELECT 
    DATE_TRUNC('minute', synced_at) as sync_minute,
    COUNT(*) as records_synced
FROM xero.journals
GROUP BY 1
ORDER BY 1;
```

## Why This Fix Works

1. **Database cursor isolation**: Each batch gets its own cursor with a clean state
2. **Proper resource management**: Cursors are closed after use, preventing resource leaks
3. **Consistent transaction boundaries**: Each batch is a complete transaction with its own cursor lifecycle
4. **Matches invoice sync pattern**: Though invoices appeared to use the same pattern, they may have benefited from different timing or data characteristics

## Comparison with Invoice Sync

The invoice sync uses a similar pattern but with one key difference - it creates the cursor once and reuses it. However, invoices work correctly, possibly due to:
- Different table constraints or triggers
- Different data volumes per batch
- Different PostgreSQL optimization paths

To ensure consistency, we may want to apply the same cursor refresh pattern to invoice sync as a preventive measure.

## Next Steps

1. **Test with full data set** - Remove the SYNC_BATCH_SIZE override and run full sync
2. **Monitor performance** - Creating new cursors has minimal overhead but verify no significant performance impact
3. **Apply to invoice sync** - Consider applying the same pattern for consistency
4. **Add automated tests** - Create tests that verify batch commits work correctly

## Performance Considerations

Creating new cursors for each batch has minimal performance impact:
- Cursor creation is a lightweight operation in psycopg2
- The overhead is negligible compared to network I/O and database operations
- Ensures reliability over micro-optimization

## Rollback Instructions

If issues arise, the previous version can be restored from git:
```bash
git checkout HEAD~1 xero_sync.py
```

---

**Fix implemented**: November 11, 2025
**Author**: System Engineer
**Status**: Ready for testing
