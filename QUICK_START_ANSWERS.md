# Quick Answers to Setup Questions

## 1. Xero App Setup - Redirect URL

When creating your Xero app in the [Xero Developer Portal](https://developer.xero.com/app/manage):

**Redirect URI**: `http://localhost:8888/callback`

**App Configuration**:
- App name: `PostgreSQL Sync` (Xero doesn't allow "Xero" in the name)
- Company or application URL: `https://github.com`
- Redirect URI: `http://localhost:8888/callback`

## 2. How to Push to GitHub for GitHub Actions

### Step 1: Initialize Git (if not already done)
```bash
cd g:\IT\Code\PostgreSQL\xero
git init
```

### Step 2: Add all files
```bash
git add .
```

### Step 3: Commit
```bash
git commit -m "Add Xero PostgreSQL sync with GitHub Actions"
```

### Step 4: Create GitHub repository
- Go to https://github.com/new
- Create a new repository (e.g., `xero-postgresql-sync`)
- Do NOT initialize with README (we already have files)

### Step 5: Push to GitHub
```bash
git remote add origin https://github.com/YOUR_USERNAME/YOUR_REPO_NAME.git
git branch -M main
git push -u origin main
```

### Step 6: Configure GitHub Secrets
Go to your repository on GitHub:
1. Click **Settings** → **Secrets and variables** → **Actions**
2. Click **New repository secret**
3. Add these 9 secrets:

| Secret Name | Where to Get It |
|---|---|
| `XERO_CLIENT_ID` | From Xero Developer Portal |
| `XERO_CLIENT_SECRET` | From Xero Developer Portal |
| `XERO_TENANT_ID` | Run `get_refresh_token.py` |
| `XERO_REFRESH_TOKEN` | Run `get_refresh_token.py` |
| `DB_HOST` | DigitalOcean PostgreSQL connection details |
| `DB_PORT` | DigitalOcean PostgreSQL (usually 25060) |
| `DB_NAME` | Your database name |
| `DB_USER` | Your database user |
| `DB_PASSWORD` | Your database password |

### Step 7: Test the Workflow
1. Go to **Actions** tab in GitHub
2. Click **Daily Xero Sync**
3. Click **Run workflow** → **Run workflow**
4. Monitor the logs to ensure sync works

## Files Ready for GitHub

✅ All files are now ready:
- `xero_sync.py` - Main sync script
- `requirements.txt` - Python dependencies  
- `setup_schema.sql` - Database schema
- `get_refresh_token.py` - Token generator
- `README.md` - Documentation (updated)
- `SETUP_GUIDE.md` - Setup instructions (updated)
- `.github/workflows/daily-sync.yml` - GitHub Actions workflow (created)
- `.gitignore` - Excludes sensitive files (created)

## What Happens After Push

Once you push to GitHub and configure secrets:
- GitHub Actions will automatically sync your Xero data daily at 2 AM UTC
- You can manually trigger syncs anytime from the Actions tab
- All syncs are logged in the `xero.sync_log` table in your database
- The workflow runs free on GitHub's 2,000 free minutes per month
