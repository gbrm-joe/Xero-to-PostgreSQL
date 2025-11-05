# Xero PostgreSQL Sync - Quick Reference

## Files Overview

```
PostgreSQL/xero/
├── xero_sync.py                 # Main Python sync script (uses xero schema)
├── setup_schema.sql             # Database schema (creates xero schema)
├── get_refresh_token.py         # OAuth token generator
├── requirements.txt             # Python dependencies
├── .env.example                 # Environment variables template
├── .gitignore                   # Git ignore file
├── README.md                    # Overview & quick start
├── SETUP_GUIDE.md               # Complete setup instructions
├── QUICK_REFERENCE.md           # This file
└── .github/workflows/
    └── daily-sync.yml           # GitHub Actions workflow
```

## 5-Minute Setup Checklist

- [ ] Register app in [Xero Developer Portal](https://developer.xero.com)
- [ ] Run `python get_refresh_token.py` (with XERO_CLIENT_ID and XERO_CLIENT_SECRET env vars)
- [ ] Get DigitalOcean PostgreSQL connection details (create separate DB per Xero org)
- [ ] Execute `setup_schema.sql` on your database (creates xero schema automatically)
- [ ] Push code to GitHub repository
- [ ] Add all 9 secrets to GitHub Settings → Secrets → Actions
- [ ] Test: Go to Actions tab → Run Daily Xero Sync workflow manually
- [ ] Monitor: Check `xero.sync_log` table for status

## GitHub Secrets Required

```
XERO_CLIENT_ID
XERO_CLIENT_SECRET
XERO_TENANT_ID
XERO_REFRESH_TOKEN
DB_HOST
DB_PORT
DB_NAME              (each Xero org = separate database)
DB_USER
DB_PASSWORD
```

## SQL Queries for Monitoring

### Check Sync Status
```sql
SELECT * FROM xero.sync_log 
ORDER BY completed_at DESC 
LIMIT 5;
```

### Count Synced Records
```sql
SELECT 
  (SELECT COUNT(*) FROM xero.accounts) as accounts,
  (SELECT COUNT(*) FROM xero.contacts) as contacts,
  (SELECT COUNT(*) FROM xero.invoices) as invoices,
  (SELECT COUNT(*) FROM xero.journals) as journals;
```

### Find Failed Syncs
```sql
SELECT sync_type, error_message, completed_at 
FROM xero.sync_log 
WHERE status = 'failed' 
ORDER BY completed_at DESC;
```

### View Recent Invoices
```sql
SELECT invoice_number, total, synced_at 
FROM xero.invoices 
ORDER BY synced_at DESC 
LIMIT 20;
```

### Check Invoice Details with Line Items
```sql
SELECT 
  i.invoice_number,
  i.total,
  COUNT(li.id) as line_items,
  i.synced_at
FROM xero.invoices i
LEFT JOIN xero.invoice_items li ON i.invoice_id = li.invoice_id
GROUP BY i.invoice_id, i.invoice_number, i.total, i.synced_at
ORDER BY i.synced_at DESC
LIMIT 20;
```

## Cron Schedule Examples

Edit `.github/workflows/daily-sync.yml` line 8:

```yaml
# Every day at 2 AM UTC
- cron: '0 2 * * *'

# Every 6 hours
- cron: '0 */6 * * *'

# Every morning at 8 AM UTC (Mon-Fri)
- cron: '0 8 * * MON-FRI'

# Every day at 9 AM and 5 PM UTC
- cron: '0 9,17 * * *'

# Every 4 hours, starting at midnight
- cron: '0 0,4,8,12,16,20 * * *'
```

## Multi-Tenant Configuration

### For Multiple Xero Organizations:

1. **Create separate databases** on DigitalOcean for each org
2. **Run setup_schema.sql** on each database (creates `xero` schema)
3. **Configure GitHub Secrets** per organization:
   - Option A: Create separate repositories per org
   - Option B: Use matrix builds for multiple orgs
   - Option C: Create separate workflows with different secret names

### Example Matrix Build (Advanced)
```yaml
jobs:
  sync:
    strategy:
      matrix:
        include:
          - org: org1
            secrets_prefix: ORG1_
          - org: org2
            secrets_prefix: ORG2_
```

## Cost Breakdown

- GitHub Actions: **FREE** (2,000 min/month)
- DigitalOcean PostgreSQL: One DB per org (already paid)
- Xero API: **FREE** (included)
- **Total: $0/month per organization**

Daily syncs use ~150 minutes/month of free tier.

## Performance Expectations

| Scenario | Time |
|----------|------|
| Initial sync (100k+ rows) | 2-5 minutes |
| Daily incremental sync | 1-2 minutes |
| GitHub free tier limit | 2,000 min/month |
| Capacity remaining | 1,850 min/month |

## Troubleshooting

| Error | Cause | Fix |
|-------|-------|-----|
| `Missing environment variables` | Secret not set | Add all 9 secrets to GitHub Settings |
| `Failed to get access token` | Refresh token expired | Re-run `get_refresh_token.py` |
| `Connection refused` | DB unreachable | Check host/port/firewall |
| `NO_DATA` returned | No active records | Verify Xero has test data |
| `relation "xero.accounts" does not exist` | Schema not created | Run `setup_schema.sql` first |

## Useful Commands

### Local Testing (before GitHub)
```bash
cd PostgreSQL/xero
python get_refresh_token.py
export XERO_CLIENT_ID="your_id"
export XERO_CLIENT_SECRET="your_secret"
export XERO_TENANT_ID="your_tenant"
export XERO_REFRESH_TOKEN="your_token"
export DB_HOST="your_host"
export DB_PORT="5432"
export DB_NAME="your_db"
export DB_USER="your_user"
export DB_PASSWORD="your_password"
python xero_sync.py
```

### PostgreSQL Connection
```bash
psql -h YOUR_HOST -p 25060 -U YOUR_USER -d YOUR_DB
# Then in psql:
\dn  # List schemas
\dt xero.*  # List tables in xero schema
```

## Schema Structure

All data lives in the `xero` schema:

```
Database (per Xero Org)
└── xero schema
    ├── accounts
    ├── contacts
    ├── invoices
    ├── invoice_items
    ├── journals
    ├── journal_lines
    └── sync_log
```

## Next Steps

1. Follow [SETUP_GUIDE.md](SETUP_GUIDE.md)
2. Test with manual workflow run
3. Monitor sync logs
4. Customize schedule if needed
5. For multiple orgs, set up separate DBs with xero schema

## Support Resources

- Xero: https://developer.xero.com/documentation/
- PostgreSQL: https://docs.digitalocean.com/products/databases/postgresql/
- GitHub Actions: https://docs.github.com/en/actions
