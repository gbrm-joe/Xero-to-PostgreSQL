# Authentication Fix Migration Guide

This guide will help you deploy the authentication fixes that resolve the token refresh issues.

## What Was Fixed

1. **Token Persistence**: Refresh tokens are now stored in PostgreSQL and automatically updated after each refresh
2. **Proactive Token Refresh**: Access tokens are refreshed automatically before they expire
3. **401 Error Recovery**: Automatic retry with token refresh when authentication fails mid-sync
4. **Long-Running Sync Support**: Syncs can now run for hours without auth failures

## Migration Steps

### Step 1: Apply Database Schema Changes

Run the new SQL migration to create the tokens table:

```bash
psql -h $DB_HOST -U $DB_USER -d $DB_NAME -f add_tokens_table.sql
```

Or if using a password-protected connection:

```bash
PGPASSWORD=$DB_PASSWORD psql -h $DB_HOST -U $DB_USER -d $DB_NAME -f add_tokens_table.sql
```

This creates the `xero.tokens` table that will store and manage OAuth tokens automatically.

### Step 2: Update Your Code

The updated `xero_sync.py` file has already been modified with all the fixes. No manual code changes needed.

### Step 3: Initial Token Setup

On the first run after migration, the application will:
1. Use your existing `XERO_REFRESH_TOKEN` from environment variables
2. Exchange it for new tokens from Xero
3. Save both the new refresh token AND access token to the database
4. Log the new refresh token in case you need to update GitHub Secrets

### Step 4: Update GitHub Secrets (Important!)

After the first successful sync, check the logs for a line like:

```
IMPORTANT: New refresh token (update GitHub Secret if needed): abcd1234efgh5678...
```

Update your GitHub Secret `XERO_REFRESH_TOKEN` with this new value. This is the last manual update you'll need to do - after this, tokens will be managed automatically!

To update the GitHub Secret:
1. Go to your repository on GitHub
2. Navigate to Settings → Secrets and variables → Actions
3. Click on `XERO_REFRESH_TOKEN`
4. Replace with the new token from the logs
5. Click "Update secret"

### Step 5: Test the Fix

Run a manual sync to verify everything works:

```bash
python3 xero_sync.py
```

You should see logs indicating:
- Database connection successful
- Tokens loaded from database (after first run)
- Token refresh happening proactively
- Successful sync completion

## How It Works Now

### Token Lifecycle

1. **Initial Connection**: App connects to database and loads any cached tokens
2. **Access Token Check**: Before each API request, checks if token expires in < 5 minutes
3. **Proactive Refresh**: If token expiring soon, refreshes automatically
4. **Token Storage**: After refresh, saves BOTH access and refresh tokens to database
5. **401 Recovery**: If 401 error occurs, refreshes token and retries the request once

### Token Storage

Tokens are stored in the `xero.tokens` table:
- `refresh_token`: The current refresh token (updated on each refresh)
- `access_token`: The current access token (valid for 30 minutes)
- `access_token_expires_at`: When the access token expires
- `updated_at`: Last time tokens were updated

### Long-Running Syncs

For syncs that take > 30 minutes:
- Access token is checked before each API request
- Automatically refreshes when it gets close to expiring
- No interruption to the sync process
- Journals sync (which can take hours) will now complete successfully

## Troubleshooting

### Issue: "Failed to load tokens from database"

**Solution**: Run the `add_tokens_table.sql` migration script. The tokens table may not exist yet.

### Issue: "400 Bad Request" on first run after migration

**Solution**: Your current refresh token may be invalid. Generate a new one:

```bash
python3 get_refresh_token.py
```

Then update both your `.env` file and GitHub Secret with the new `XERO_REFRESH_TOKEN`.

### Issue: Tokens not persisting between runs

**Solution**: Check that:
1. Database connection is successful
2. The `xero.tokens` table exists
3. Database user has INSERT/UPDATE permissions on the table

### Issue: Need to see full refresh token in logs

The logs show only the first 20 characters for security. To see the full token:

```sql
SELECT refresh_token FROM xero.tokens ORDER BY updated_at DESC LIMIT 1;
```

## Benefits

✅ **No More Manual Token Updates**: Tokens refresh automatically and persist across runs

✅ **No More Mid-Sync Failures**: Long-running syncs won't fail due to token expiry

✅ **Automatic Recovery**: 401 errors are caught and handled automatically

✅ **Better Logging**: Clear visibility into when tokens are refreshed

✅ **Production Ready**: Designed for automated daily syncs via GitHub Actions

## Monitoring

Watch the logs for these key indicators:

- ✅ "Loaded cached access token from database" - Using cached token
- ✅ "Access token expired or expiring soon, refreshing..." - Proactive refresh
- ✅ "Received new refresh token from Xero" - Token successfully refreshed
- ✅ "Saved tokens to database" - Persistence working correctly
- ⚠️ "Received 401 Unauthorized - refreshing token and retrying..." - Recovery in action

## Next Steps

After successful migration:

1. The next scheduled sync will use the new token management automatically
2. Monitor the first few syncs to ensure tokens are being refreshed
3. After confirming it works, you can delete your local `.env` file's `XERO_REFRESH_TOKEN` entry (it's now managed by the database)
4. Enjoy worry-free syncing! 🎉

## Rollback (if needed)

If you need to rollback these changes:

1. Keep your current `XERO_REFRESH_TOKEN` in GitHub Secrets
2. Revert to the previous version of `xero_sync.py`
3. Optionally drop the tokens table: `DROP TABLE xero.tokens;`

However, this is not recommended as the old system has the auth expiry issues.
