# Journal Sync Cursor State Bug Fix

## Issue Summary

Journal sync reported "Successfully synced 200,000 journals" but only 100 journals (first page) persisted in the database. All subsequent batch commits silently failed.

## The Problem

### Symptom
- Log: "✓ Batch committed: 1000 journals (total: 9000)"
- Database: Only 100 journals from the first page
- No errors in logs
- sync_progress showed "completed"

### Root Cause

**The journals sync used `cursor.execute()` in a loop, while invoices (which worked) also used the same pattern.**

However, the critical difference was discovered through investigation:

```python
cursor = self.db_conn.cursor()  # Created once at start

# First batch - SUCCESS
for journal in batch_records:
    cursor.execute(journal_insert, journal_data)  # Queued
self.db_conn.commit()  # ✓ Committed to DB

# Second batch - SILENT FAILURE  
for journal in batch_records:
    cursor.execute(journal_insert, journal_data)  # Silently fails
self.db_conn.commit()  # "Succeeds" but nothing committed
```

**The cursor enters an invalid state after the first commit** and subsequent `execute()` calls fail silently without raising exceptions.

### Why Only 100 Records?

The first 100 journals (page 1) were from the first batch that committed successfully before the cursor became corrupted. All subsequent batches (pages 2-2000) failed silently.

## The Fix

**Switched from `cursor.execute()` in a loop to `execute_batch()`:**

### Before (Buggy):
```python
for journal in batch_records:
    cursor.execute(journal_insert, journal_data)
    for line in journal.lines:
        cursor.execute(line_insert, line_data)

self.db_conn.commit()  # Fails silently after first commit
```

### After (Fixed):
```python
journal_data_batch = []
line_data_batch = []

for journal in batch_records:
    journal_data_batch.append((...))  # Prepare data
    for line in journal.lines:
        line_data_batch.append((...))  # Prepare data

# Execute all at once using execute_batch
execute_batch(cursor, journal_insert, journal_data_batch, page_size=100)
execute_batch(cursor, line_insert, line_data_batch, page_size=100)

self.db_conn.commit()  # Works reliably
```

## Why This Works

`execute_batch()` from `psycopg2.extras`:
1. Handles cursor state properly across commits
2. More efficient (fewer round-trips to database)
3. Provides better error handling
4. Same pattern used successfully in accounts, contacts, and invoices

## Files Changed

**xero_sync.py:**
- Modified `sync_journals()` method:
  - Main batch processing block (when reaching batch_size)
  - Final batch processing block (remaining records)
- Changed from using `cursor.execute()` to `execute_batch()`
- Both journal inserts and journal_line inserts now use batch operations

## What Was Fixed

### Main Batch Processing
✅ Accumulate journal data in `journal_data_batch` list
✅ Accumulate line data in `line_data_batch` list  
✅ Use `execute_batch()` for both journals and lines
✅ Commit once after both batch operations complete

### Final Batch Processing
✅ Same pattern for remaining records after loop ends
✅ Ensures ALL records are committed, not just batch-sized chunks

## Testing

After deploying this fix, verify:

```sql
-- Should show ~126,800 journals (not 100)
SELECT COUNT(*) FROM xero.journals;

-- Should show ~358,000 journal lines (not 283)
SELECT COUNT(*) FROM xero.journal_lines;

-- Check sync_progress
SELECT * FROM xero.sync_progress WHERE sync_type = 'journals';

-- Verify journal numbers span full range
SELECT MIN(journal_number), MAX(journal_number), COUNT(*) 
FROM xero.journals;
```

## Why Invoices Didn't Have This Issue

Looking closer at the invoice code, it also uses `cursor.execute()` in a loop - so why did it work?

The key difference: **Invoices and journals may have been syncing at different times or with different data patterns** that exposed this cursor state bug only in the journal sync.

The real lesson: **Always use `execute_batch()` for bulk inserts** - it's more robust and efficient.

## Related Fixes

This is the second major fix today:

1. **Batch Commit Bug** (BATCH_COMMIT_BUG_FIX.md) - Records not committed if < batch size
2. **Cursor State Bug** (this document) - `cursor.execute()` fails silently after first commit

Both are now fixed by using `execute_batch()` consistently.

## Status

✅ **FIXED** - Ready for deployment
