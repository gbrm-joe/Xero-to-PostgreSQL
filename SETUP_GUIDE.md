# Xero to PostgreSQL Sync - Setup Guide

This guide walks through setting up automated daily syncing of Xero data to your DigitalOcean PostgreSQL database using GitHub Actions.

## Architecture

- **Scheduler**: GitHub Actions (Free - 2,000 minutes/month)
- **Sync Script**: Python with Xero API integration
- **Database**: DigitalOcean Managed PostgreSQL (separate DB per Xero org)
- **Schema**: All Xero data stored in `xero` schema within each database
- **Tables**: accounts, contacts, invoices, invoice_items, journals, journal_lines, sync_log
- **Frequency**: Daily (customizable)
- **Cost**: $0/month (uses free GitHub Actions tier)
- **Multi-tenant**: Reuse same code for multiple Xero organizations

## Prerequisites

1. **Xero Business Account** with API access
2. **DigitalOcean PostgreSQL Database** (one per Xero organization)
3. **GitHub Repository** to store the code
4. Xero OAuth 2.0 credentials

## Step-by-Step Setup

### 1. Register a Xero App

1. Go to [Xero Developer Portal](https://developer.xero.com/app/manage)
2. Create a new app:
   - App name: `PostgreSQL Sync` (Note: Xero doesn't allow "Xero" in app names)
   - Company or application URL: `https://github.com`
   - Redirect URI: `http://localhost:8888/callback`
3. Copy and save:
   - **Client ID**
   - **Client Secret**

### 2. Get Xero OAuth Tokens

1. Set environment variables:
```bash
export XERO_CLIENT_ID="your_client_id"
export XERO_CLIENT_SECRET="your_client_secret"
```

2. Run the token generator:
```bash
cd PostgreSQL/xero
python get_refresh_token.py
```

3. Save the output:
   - **XERO_REFRESH_TOKEN**
   - **XERO_TENANT_ID**

### 3. Get DigitalOcean PostgreSQL Details

1. Log into DigitalOcean console
2. Go to your PostgreSQL cluster
3. Click "Connection Details" to get:
   - **DB_HOST**: Database host
   - **DB_PORT**: Database port (usually 25060)
   - **DB_NAME**: Your database name
   - **DB_USER**: Database user
   - **DB_PASSWORD**: Database password

Note: Each Xero organization should have its own separate database. The `xero` schema will be created automatically by the setup script.

### 4. Set Up Database Schema

1. Connect to your PostgreSQL using a client (pgAdmin, DBeaver, psql, etc.)
2. Copy the contents of `setup_schema.sql`
3. Execute it to create the `xero` schema and all required tables

The script will automatically:
- Create the `xero` schema if it doesn't exist
- Create all 7 data tables with proper indexes
- Create the sync log table

### 5. Push Code to GitHub

1. Create or use an existing GitHub repository
2. Add these files:
   - `xero_sync.py`
   - `requirements.txt`
   - `.github/workflows/daily-sync.yml`
   - `get_refresh_token.py`
   - `setup_schema.sql`

3. Commit and push:
```bash
git add .
git commit -m "Add Xero PostgreSQL sync workflow"
git push origin main
```

### 6. Configure GitHub Secrets

In your GitHub repository:

1. Go to **Settings** → **Secrets and variables** → **Actions**
2. Click **New repository secret** and add each:

| Secret Name | Value |
|---|---|
| `XERO_CLIENT_ID` | Your Xero app Client ID |
| `XERO_CLIENT_SECRET` | Your Xero app Client Secret |
| `XERO_TENANT_ID` | Your Xero Tenant ID |
| `XERO_REFRESH_TOKEN` | Your Xero Refresh Token |
| `DB_HOST` | DigitalOcean PostgreSQL host |
| `DB_PORT` | DigitalOcean PostgreSQL port |
| `DB_NAME` | Your database name (each org = separate DB) |
| `DB_USER` | Your database user |
| `DB_PASSWORD` | Your database password |

### 7. Test the Workflow

1. Go to **Actions** tab in GitHub
2. Select **Daily Xero Sync**
3. Click **Run workflow** → **Run workflow**
4. Monitor the logs

## Multi-Tenant Setup

For managing multiple Xero organizations:

1. **Create separate databases** on DigitalOcean for each organization
2. **Run setup_schema.sql** on each database
3. **Create separate GitHub workflows** or use environment-specific secrets:
   - Use different secret sets for each org
   - Create matrix builds to run multiple syncs
   - Or deploy multiple instances with different GitHub Actions workflows

Each database will have:
- Dedicated `xero` schema
- All data isolated from other organizations
- Independent sync logs

## Data Schema

All data is stored in the `xero` schema:

### Core Tables
- `xero.accounts` - Chart of accounts
- `xero.contacts` - Customers and vendors
- `xero.invoices` - Invoice headers
- `xero.invoice_items` - Invoice line items
- `xero.journals` - Manual journal entries
- `xero.journal_lines` - Journal entry lines
- `xero.sync_log` - Sync operation logs

### Query Example
```sql
-- Query from xero schema
SELECT invoice_number, total, synced_at 
FROM xero.invoices 
ORDER BY synced_at DESC 
LIMIT 20;
```

## Customization

### Change Sync Schedule

Edit `.github/workflows/daily-sync.yml` and modify the cron schedule:

```yaml
on:
  schedule:
    - cron: '0 2 * * *'  # Change this line
```

Cron format: `minute hour day month day-of-week`

Examples:
- `0 2 * * *` = Every day at 2 AM UTC
- `0 */6 * * *` = Every 6 hours
- `0 9,17 * * MON-FRI` = 9 AM and 5 PM on weekdays

### Filter Which Data Gets Synced

In `xero_sync.py`, modify the Xero API where clauses to filter data.

### Add More Data Types

To sync additional Xero entities:

1. Create new tables in `setup_schema.sql` (in `xero` schema)
2. Add new sync methods to `XeroSync` class
3. Call them in `run_full_sync()`

## Monitoring & Troubleshooting

### Check Sync Logs

```sql
-- Check sync status
SELECT * FROM xero.sync_log 
ORDER BY completed_at DESC 
LIMIT 20;

-- Find failed syncs
SELECT sync_type, error_message, completed_at 
FROM xero.sync_log 
WHERE status = 'failed' 
ORDER BY completed_at DESC;
```

### View GitHub Actions Logs

1. Go to **Actions** tab in your GitHub repository
2. Click on the latest workflow run
3. Click **sync** job to see detailed logs

### Common Issues

**Issue**: `Missing required environment variables`
- **Solution**: Ensure all secrets are configured in GitHub Settings

**Issue**: `Failed to get access token`
- **Solution**: Refresh token may have expired. Run `get_refresh_token.py` again

**Issue**: `Connection refused` to database
- **Solution**: Check DB_HOST and DB_PORT are correct. Ensure DigitalOcean firewall allows GitHub Actions IPs

**Issue**: `NO_DATA` in Xero API response
- **Solution**: Verify you have the correct Xero permissions and tenant

## Costs

| Component | Cost |
|---|---|
| GitHub Actions | FREE (2,000 minutes/month) |
| DigitalOcean PostgreSQL | One DB per org (already budgeted) |
| Xero API | FREE (included with Xero account) |
| **Total** | **$0/month per org** |

## Next Steps

1. Follow Setup Guide above
2. Run the initial test sync
3. Monitor the logs
4. Adjust the cron schedule if needed
5. (Optional) For multiple orgs, create separate workflows/configs

## Support

For issues with:
- **Xero API**: Visit [Xero Developer Docs](https://developer.xero.com/documentation/guides/get-started/auth)
- **PostgreSQL**: Check [DigitalOcean Documentation](https://docs.digitalocean.com/products/databases/postgresql/)
- **GitHub Actions**: See [Actions Documentation](https://docs.github.com/actions)
