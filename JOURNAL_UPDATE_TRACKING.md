# Journal Update Tracking Implementation

## Problem
The Xero Journals API doesn't support reliable change detection:
- The `offset` parameter only fetches journals with numbers greater than
  the specified value (a forward-only scan).
- Xero explicitly warns against using Modified After: "The
  If-Modified-Since header may cause missing journals."

So we cannot use an `UpdatedDateUTC` watermark the way invoices and
payments do.

## Key property: the journal ledger is append-only
Xero journals are immutable. Editing, voiding, or deleting a source
transaction (invoice, bill, payment, manual journal) does **not** mutate
an existing journal — Xero posts **new** journals with new, higher
`JournalNumber`s. Journal numbers are sequential and contiguous.

This means a forward incremental scan from the highest journal number we
already hold catches every genuine change. The only thing it cannot heal
on its own is a **gap** — a missing range of numbers left behind by a
failed/partial run or the documented If-Modified-Since miss — because the
forward scan never looks back below the max.

## Solution: forward incremental + gap backfill
`sync_journals` runs in two passes on every (non-forced) sync:

### 1. Forward pass (catches new journals)
- Starts from `offset = MAX(journal_number)` already in the DB.
- Pages forward until Xero returns no more journals.
- Fast: only ever fetches journals newer than what we have.

### 2. Gap backfill (heals holes below the max)
- Detects missing number ranges directly from the DB:
  ```sql
  SELECT journal_number + 1 AS gap_start, next_num - 1 AS gap_end
  FROM (
      SELECT journal_number,
             LEAD(journal_number) OVER (ORDER BY journal_number) AS next_num
      FROM finance_<slug>.xero_journals
  ) t
  WHERE next_num IS NOT NULL AND next_num > journal_number + 1;
  ```
- For each gap, re-fetches only that range (`offset = gap_start - 1`) and
  upserts via `ON CONFLICT DO UPDATE`.
- Cost is O(gaps), not O(entire ledger). With a healthy, contiguous
  ledger this pass is a no-op.

This replaces the previous **periodic (weekly) full resync**, which
re-downloaded the entire ledger to catch edits that — given the
append-only property — show up as new journals anyway. That approach grew
unbounded with ledger size (e.g. tens of thousands of journals re-pulled
weekly, inline with the daily job).

## Manual full resync (override)
A full resync from offset 0 is still available for recovery, e.g. after a
suspected data issue:
- `python xero_sync.py --force-full-resync` (all tenants)
- `python xero_sync.py --slug <slug> --force-full-resync` (one tenant)

A full resync is also performed automatically the first time a tenant is
synced (empty `xero_journals`).

## Monitoring

Check for gaps (should be empty):
```sql
SELECT journal_number + 1 AS gap_start, next_num - 1 AS gap_end
FROM (
    SELECT journal_number,
           LEAD(journal_number) OVER (ORDER BY journal_number) AS next_num
    FROM finance_<slug>.xero_journals
) t
WHERE next_num IS NOT NULL AND next_num > journal_number + 1;
```

Check sync history:
```sql
SELECT sync_type, records_synced, status, started_at, duration_seconds
FROM finance_<slug>.sync_log
WHERE sync_type = 'journals'
ORDER BY started_at DESC LIMIT 10;
```

## Comparison: invoices/payments vs journals

### Invoices & payments (modification-date filtering)
- Use `UpdatedDateUTC>=DateTime(...)` against a per-tenant watermark.
- Catch all changes on the next run.

### Journals (forward + gap backfill)
- Can't filter by modification date reliably.
- Append-only ledger, so forward scan + gap healing is sufficient and
  cheap.

## Important notes
1. Genuine changes to Xero data appear as **new** journals and are caught
   by the next forward pass.
2. The gap backfill is **self-healing** for missed ranges and runs every
   sync at negligible cost when there are no gaps.
3. `--force-full-resync` remains the manual override for full recovery.
4. `ON CONFLICT DO UPDATE` ensures the latest data overwrites old.
