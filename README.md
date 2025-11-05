# Xero to PostgreSQL Sync

Automated, cost-effective syncing of Xero accounting data to DigitalOcean PostgreSQL using GitHub Actions.

Multi-tenant ready: One code base, multiple organizations (each in separate database with `xero` schema).

## Quick Start

- **Cost**: $0/month (uses free GitHub Actions tier)
- **Frequency**: Daily (customizable)
- **Data**: Invoices, Contacts, Accounts, Journals + Line Items
- **Setup Time**: ~15 minutes
- **Multi-tenant**: Scales to multiple Xero organizations

## What This Does

Automatically syncs data from Xero to your PostgreSQL database:
- ✅ All chart of accounts (100k+ rows supported)
- ✅ Active contacts and customers
- ✅ Invoices with detailed line items
- ✅ Posted manual journals with entries
- ✅ Error logging and notifications
- ✅ Incremental updates (no duplicate syncing)
- ✅ All data in dedicated `xero` schema

## Architecture

```
Xero API (Org 1, 2, 3...)
   ↓
GitHub Actions (Daily triggers)
   ↓
Python Sync Script
   ↓
DigitalOcean PostgreSQL
   └─ Database 1 (Org 1)
      └─ xero schema
   └─ Database 2 (Org 2)
      └─ xero schema
```

## Files

| File | Purpose |
|------|---------|
| `xero_sync.py` | Main sync script |
| `setup_schema.sql` | Database schema (creates xero schema) |
| `get_refresh_token.py` | OAuth token generator |
| `requirements.txt` | Python dependencies |
| `.github/workflows/daily-sync.yml` | GitHub Actions workflow |
| `SETUP_GUIDE.md` | Complete setup instructions |

## Quick Setup

1. **Get Xero Credentials**
   - Register app in [Xero Developer Portal](https://developer.xero.com) with:
     - App name: `PostgreSQL Sync` (Note: Xero doesn't allow "Xero" in app names)
     - Redirect URI: `http://localhost:8888/callback`
   - Run `get_refresh_token.py` to obtain refresh token

2. **Set Up Database Schema**
   - Run `setup_schema.sql` on your DigitalOcean PostgreSQL (creates `xero` schema automatically)

3. **Configure GitHub**
   - Push code to your repository
   - Add secrets to GitHub (see SETUP_GUIDE.md)
   - GitHub Actions will run daily automatically

4. **Monitor**
   - Check `xero.sync_log` table for sync status
   - View GitHub Actions logs for details

## Full Setup Guide

See [SETUP_GUIDE.md](SETUP_GUIDE.md) for complete step-by-step instructions.

## Database Structure

### Schema Organization
```
Your Database
└── xero schema
    ├── accounts
    ├── contacts
    ├── invoices
    ├── invoice_items
    ├── journals
    ├── journal_lines
    └── sync_log
```

### Query Examples
```sql
-- All tables are in the xero schema
SELECT * FROM xero.invoices WHERE synced_at > NOW() - INTERVAL '1 day';
SELECT * FROM xero.sync_log ORDER BY completed_at DESC LIMIT 10;
SELECT COUNT(*) FROM xero.accounts;
```

## Customization

### Change Sync Schedule

Edit `.github/workflows/daily-sync.yml`:
```yaml
cron: '0 2 * * *'  # Daily at 2 AM UTC
```

Examples:
- `0 */6 * * *` = Every 6 hours
- `0 9 * * MON-FRI` = 9 AM weekdays

### Filter Data

Modify the where clauses in `xero_sync.py`:
```python
# Only sync recent invoices
response = self._make_xero_request('Invoices', 
    params={'where': 'InvoiceDate >= DateTime(2024,1,1)'})
```

### Add More Entities

1. Create tables in `setup_schema.sql` (use `xero.` prefix)
2. Add sync methods to `XeroSync` class
3. Call them in `run_full_sync()`

## Multi-Tenant Setup

For multiple Xero organizations:

1. Create separate databases on DigitalOcean (one per org)
2. Run `setup_schema.sql` on each database
3. Configure separate secrets for each org
4. Either:
   - Create separate GitHub workflows per org
   - Use matrix builds to sync multiple orgs
   - Deploy multiple action instances

Each database will have its own `xero` schema with isolated data.

## Monitoring

### Check Sync Status

```sql
-- Latest sync results
SELECT * FROM xero.sync_log 
ORDER BY completed_at DESC 
LIMIT 10;

-- Failed syncs
SELECT * FROM xero.sync_log 
WHERE status = 'failed' 
ORDER BY completed_at DESC;
```

### View Latest Records

```sql
-- Most recently synced invoices
SELECT invoice_number, total, synced_at 
FROM xero.invoices 
ORDER BY synced_at DESC 
LIMIT 20;
```

## Costs

| Component | Cost |
|---|---|
| GitHub Actions | FREE* |
| DigitalOcean PostgreSQL | Already budgeted |
| Xero API | FREE (included) |
| **Total** | **$0/month** |

*2,000 free minutes per month. Daily 5-minute syncs = ~150 minutes/month

## Performance

| Operation | Time |
|---|---|
| Initial sync (100k+ rows) | 2-5 minutes |
| Daily incremental sync | 1-2 minutes |
| Data freshness | 24 hours |

## Troubleshooting

### "Missing required environment variables"
- Check all GitHub Secrets are configured
- Verify secret names match exactly

### "Failed to get access token"
- Refresh token may have expired
- Run `get_refresh_token.py` again

### "Connection refused" to database
- Verify DB_HOST and DB_PORT are correct
- Check DigitalOcean firewall settings

### GitHub Actions taking too long
- Check GitHub Actions logs for API rate limits
- Xero API may have throttled requests
- Retry syncs are automatic

## Support Resources

- [Xero Developer Documentation](https://developer.xero.com/documentation/)
- [DigitalOcean PostgreSQL Guide](https://docs.digitalocean.com/products/databases/postgresql/)
- [GitHub Actions Documentation](https://docs.github.com/en/actions)

## License

This project is open source and available for your use.

---

**Next Step**: Follow the [SETUP_GUIDE.md](SETUP_GUIDE.md) to get started!
