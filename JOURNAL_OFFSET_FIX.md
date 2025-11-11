# Journal Sync Fix - Using Offset Instead of Page Parameter

## Problem Identified
The journal sync was failing because we were using the wrong parameter for pagination:
- **Our code**: Used `page` and `pageSize` parameters (incorrect for journals)
- **Xero API**: Expects `offset` parameter with a journal number value

This caused the API to ignore our pagination and always return journals 1-100, resulting in:
- Only 100 journals in the database
- Endless loop processing the same journals
- `ON CONFLICT` preventing duplicates from being inserted

## The Fix
Changed from page-based to offset-based pagination according to Xero API documentation.

### Key Changes

1. **Query highest journal number in database**
```python
cursor.execute("SELECT MAX(journal_number) FROM xero.journals")
last_journal_number = result[0] if result[0] else 0
```

2. **Use offset parameter instead of page**
```python
# OLD (incorrect)
params={'page': page, 'pageSize': 100}

# NEW (correct)  
params={'offset': current_offset}
```

3. **Update offset based on last journal retrieved**
```python
if journals:
    last_num = journals[-1].get('JournalNumber')
    if last_num:
        current_offset = last_num
```

## How It Works Now

1. **Initial sync**: Starts with `offset=0` to get journals 1-100
2. **Next batch**: Uses `offset=100` to get journals 101-200
3. **Continues**: Until empty response indicates all journals synced
4. **Resume capability**: Checks max journal_number in DB and resumes from there

## Testing Instructions

### 1. Reset the journal tables (optional for clean test)
```sql
TRUNCATE TABLE xero.journals CASCADE;
TRUNCATE TABLE xero.journal_lines CASCADE;
```

### 2. Run the sync
```bash
python xero_sync.py
```

### 3. Monitor the logs
You should see:
```
Starting journal sync...
Starting fresh journal sync from the beginning
Fetching journals with offset 0...
Retrieved 100 journals
Journal number range: 1 to 100
...
Fetching journals with offset 100...
Retrieved 100 journals  
Journal number range: 101 to 200
```

### 4. Verify in database
```sql
-- Check total count and max journal number
SELECT COUNT(*), MAX(journal_number) 
FROM xero.journals;

-- Check for proper sequential numbering
SELECT COUNT(DISTINCT journal_number) as unique_numbers,
       MIN(journal_number) as min_num,
       MAX(journal_number) as max_num
FROM xero.journals;
```

## Why This Fix Works

1. **Correct API usage**: Follows Xero's documented approach for journal pagination
2. **Efficient resumption**: Can resume from any point based on journal numbers
3. **No duplicates**: Each journal number is unique, properly incremental
4. **Proper termination**: Stops when API returns empty response

## Comparison: Page vs Offset

| Parameter | Invoices/Contacts | Journals |
|-----------|------------------|----------|
| Pagination | `page=1,2,3...` | `offset=0,100,200...` |
| Page Size | `pageSize=100` | N/A (always 100) |
| Resume From | Page number | Journal number |

## Performance Impact

- **Before**: Infinite loop processing same 100 journals
- **After**: Efficiently syncs all journals sequentially
- **Time**: Depends on total journals (approx 1 second per 100 journals)

## Additional Improvements

1. **Empty response handling**: Stops after 3 consecutive empty responses
2. **Gap handling**: Increments offset by 100 if empty response to handle potential gaps
3. **Better logging**: Shows journal number ranges for each batch
4. **Final verification**: Logs total count and max journal number after sync

---

**Fix implemented**: November 11, 2025
**Root cause**: Incorrect pagination parameter (page instead of offset)
**Status**: Fixed and ready for testing
