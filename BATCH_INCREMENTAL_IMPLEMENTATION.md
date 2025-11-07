# Batch Commits and Incremental Sync Implementation

## Current Status

### ✅ Completed
1. **Authentication Fix** - Tokens automatically refresh and persist
2. **Database Schema** - Added `xero.tokens` and `xero.sync_progress` tables
3. **Helper Methods** - Added `_get_sync_progress()` and `_update_sync_progress()`
4. **Configuration** - Added `SYNC_BATCH_SIZE` and `FORCE_FULL_SYNC` environment variables

### 🚧 In Progress
Implementing batch commits and incremental sync for all entity types

## Implementation Strategy

### 1. Batch Commits

**Current Problem:**
- Fetches ALL records into memory
- Commits once at the end
- If sync fails, NO data is saved

**New Approach:**
- Fetch records in batches (e.g., 10 pages = 1,000 records)
- Process and insert each batch
- **Commit after each batch**
- Update progress tracker after each batch
- Resume from last successful batch if sync fails

### 2. Incremental Sync

**Current Problem:**
- Every sync fetches ALL records from Xero
- Wastes time, API calls, and bandwidth
- Initial sync: 126,800 journals takes hours
- Daily sync: Still fetches all 126,800+ journals

**New Approach:**
- First run: Full sync (fetch everything)
- Subsequent runs: Use `ModifiedAfter` parameter
- Only fetch records changed since last sync
- Daily syncs take minutes instead of hours

## Xero API Parameters

### ModifiedAfter (for Incremental Sync)
```python
params = {
    'page': 1,
    'ModifiedAfter': '2025-11-06T00:00:00'  # ISO 8601 format
}
```

Works for:
- ✅ Invoices
- ✅ Contacts  
- ✅ Journals (via CreatedDate filtering)
- ❌ Accounts (doesn't support filtering - always full sync)

## Implementation for Journals (Largest Dataset)

```python
def sync_journals(self):
    """Sync journals with batch commits and incremental sync"""
    logger.info("Starting journal sync...")
    start_time = datetime.now()
    sync_type = 'journals'
    
    try:
        # Get sync progress
        progress = self._get_sync_progress(sync_type)
        start_page = progress['last_page'] + 1 if progress['status'] == 'running' else 1
        
        # Determine if we should do incremental sync
        use_incremental = (
            not self.force_full_sync and 
            progress['last_modified'] is not None and
            progress['status'] == 'completed'
        )
        
        if use_incremental:
            logger.info(f"Using incremental sync (changes since {progress['last_modified']})")
            # Note: Journals don't support ModifiedAfter, so we'll use CreatedDateUTC filtering
            # This means we need a different approach for journals
            
        # Mark sync as running
        self._update_sync_progress(sync_type, status='running')
        
        total_synced = 0
        page = start_page
        max_pages = 2000
        batch_records = []
        
        cursor = self.db_conn.cursor()
        
        journal_insert = """
            INSERT INTO xero.journals
            (journal_id, journal_number, reference, notes, journal_date, status, updated_at, synced_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, NOW())
            ON CONFLICT (journal_id) DO UPDATE SET
                status = EXCLUDED.status,
                synced_at = NOW()
        """
        
        line_insert = """
            INSERT INTO xero.journal_lines
            (journal_line_id, journal_id, account_id, account_code, description, net_amount,
             tax_amount, tracking_name, tracking_option, synced_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())
            ON CONFLICT (journal_line_id) DO UPDATE SET
                synced_at = NOW()
        """
        
        while page <= max_pages:
            logger.info(f"Fetching journals page {page}...")
            
            # Build params
            params = {'page': page, 'pageSize': 100}
            
            response = self._make_xero_request('Journals', params=params)
            journals = response.get('Journals', [])
            
            if not journals:
                break
            
            logger.info(f"Retrieved {len(journals)} journals")
            batch_records.extend(journals)
            
            # Process batch when we reach batch_size pages
            if len(batch_records) >= (self.batch_size * 100) or page == max_pages:
                # Process current batch
                journal_count = 0
                line_count = 0
                
                for journal in batch_records:
                    # Insert journal
                    journal_data = [
                        journal.get('JournalID'),
                        journal.get('JournalNumber'),
                        journal.get('Reference'),
                        None,
                        self._parse_xero_date(journal.get('JournalDate')),
                        None,
                        self._parse_xero_date(journal.get('CreatedDateUTC'))
                    ]
                    
                    cursor.execute(journal_insert, journal_data)
                    journal_count += 1
                    
                    # Insert journal lines
                    for line in journal.get('JournalLines', []):
                        line_id = f"{journal.get('JournalID')}_{line.get('JournalLineID')}"
                        tracking_name = ''
                        tracking_option = ''
                        
                        if 'Tracking' in line and line['Tracking']:
                            tracking_list = line['Tracking']
                            if tracking_list:
                                tracking_name = tracking_list[0].get('Name', '')
                                tracking_option = tracking_list[0].get('Option', '')
                        
                        line_data = [
                            line_id,
                            journal.get('JournalID'),
                            line.get('AccountID'),
                            line.get('AccountCode'),
                            line.get('Description'),
                            float(line.get('NetAmount', 0)),
                            float(line.get('TaxAmount', 0)),
                            tracking_name,
                            tracking_option
                        ]
                        
                        cursor.execute(line_insert, line_data)
                        line_count += 1
                
                # COMMIT BATCH
                self.db_conn.commit()
                total_synced += journal_count
                
                logger.info(f"Batch committed: {journal_count} journals, {line_count} lines (total: {total_synced})")
                
                # Update progress
                self._update_sync_progress(sync_type, page=page, status='running')
                
                # Clear batch for next iteration
                batch_records = []
            
            page += 1
            time.sleep(1)  # Rate limiting
        
        # Mark sync as completed
        sync_timestamp = datetime.now()
        self._update_sync_progress(sync_type, completed=True, modified_after=sync_timestamp)
        
        logger.info(f"Successfully synced {total_synced} journals")
        self._log_sync(sync_type, total_synced, 'success', None, start_time)
        
        return total_synced
        
    except Exception as e:
        self.db_conn.rollback()
        self._update_sync_progress(sync_type, status='failed')
        logger.error(f"Failed to sync journals: {str(e)}")
        self._log_sync(sync_type, 0, 'failed', str(e), start_time)
        raise
```

## Implementation for Invoices (Second Largest)

Similar approach but with `ModifiedAfter` support:

```python
def sync_invoices(self):
    """Sync invoices with batch commits and incremental sync"""
    logger.info("Starting invoice sync...")
    start_time = datetime.now()
    sync_type = 'invoices'
    
    try:
        # Get sync progress
        progress = self._get_sync_progress(sync_type)
        
        # Determine if we should do incremental sync
        use_incremental = (
            not self.force_full_sync and 
            progress['last_modified'] is not None and
            progress['status'] == 'completed'
        )
        
        if use_incremental:
            modified_after = progress['last_modified'].strftime('%Y-%m-%dT%H:%M:%S')
            logger.info(f"Using incremental sync (changes since {modified_after})")
        
        # Mark sync as running
        self._update_sync_progress(sync_type, status='running')
        
        total_synced = 0
        page = 1
        max_pages = 2000
        batch_records = []
        
        cursor = self.db_conn.cursor()
        
        # ... (insert queries)
        
        while page <= max_pages:
            logger.info(f"Fetching invoices page {page}...")
            
            # Build params with ModifiedAfter for incremental sync
            params = {'page': page, 'pageSize': 100}
            if use_incremental:
                params['ModifiedAfter'] = modified_after
            
            response = self._make_xero_request('Invoices', params=params)
            invoices = response.get('Invoices', [])
            
            if not invoices:
                break
            
            batch_records.extend(invoices)
            
            # Process batch when we reach batch_size pages
            if len(batch_records) >= (self.batch_size * 100):
                # Process and commit batch
                # ... (similar to journals)
                
                batch_records = []
            
            page += 1
            time.sleep(1)
        
        # Process any remaining records
        # ... (final batch)
        
        # Mark sync as completed
        sync_timestamp = datetime.now()
        self._update_sync_progress(sync_type, completed=True, modified_after=sync_timestamp)
        
        return total_synced
        
    except Exception as e:
        # Handle error
```

## Benefits After Implementation

### Batch Commits
- ✅ Partial progress saved (if sync fails at page 1000, you keep pages 1-999)
- ✅ Resume from last successful point
- ✅ Lower memory usage
- ✅ More resilient to failures

### Incremental Sync
- ✅ **Massive time savings** on daily syncs
  - Initial: 126,800 journals (hours)
  - Daily: Maybe 100-500 changed journals (minutes)
- ✅ Reduced API usage
- ✅ Less bandwidth
- ✅ More efficient overall

## Configuration

Add to `.env` file:
```bash
# Batch size in pages (default: 10 pages = 1000 records per batch)
SYNC_BATCH_SIZE=10

# Force full sync instead of incremental (default: false)
FORCE_FULL_SYNC=false
```

## Testing Plan

### Test 1: Batch Commits
1. Start initial sync with large dataset
2. Manually kill process mid-sync
3. Restart sync
4. Verify it resumes from last batch instead of starting over

### Test 2: Incremental Sync
1. Complete initial full sync
2. Make a few changes in Xero (add invoice, modify contact, etc.)
3. Run sync again
4. Verify only changed records are fetched
5. Check sync_progress table for correct timestamps

### Test 3: Error Recovery
1. Simulate database error mid-batch
2. Verify transaction rolls back
3. Verify progress is updated correctly
4. Verify resume works

## Next Steps

1. **Apply database migration:**
   ```bash
   psql -f add_sync_progress_table.sql
   ```

2. **Toggle to Act mode** to implement the full batch commit and incremental sync code

3. **Test thoroughly** with your dataset

4. **Monitor performance** on first few runs

Would you like me to implement the complete batch commit and incremental sync code for all sync methods?
