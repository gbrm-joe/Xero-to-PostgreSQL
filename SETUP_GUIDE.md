# Xero to PostgreSQL Sync - Complete Setup Guide

This guide walks through setting up automated daily syncing of Xero data to your DigitalOcean PostgreSQL database using a self-hosted GitHub Actions runner.

## Why Self-Hosted Runner?

GitHub Actions uses dynamic IP addresses that change with each run. DigitalOcean PostgreSQL requires whitelisted IPs for security. The solution is to run GitHub Actions on your own DigitalOcean Droplet with a fixed IP address that you can whitelist.

## Architecture

```
Xero API
   ↓
GitHub Repository (code + workflow)
   ↓
Self-Hosted Runner on DO Droplet (fixed IP)
   ↓
DigitalOcean PostgreSQL (whitelisted IP)
```

## Cost

- **Droplet**: $6/month (Basic - 1GB RAM, 1 vCPU)
- **GitHub Actions**: FREE (runs on your Droplet)
- **Total**: $6/month

---

## Part 1: Create Xero App and Get Credentials

### Step 1: Register Xero App

1. Go to [Xero Developer Portal](https://developer.xero.com/app/manage)
2. Click "New app"
3. Fill in:
   - **App name**: `PostgreSQL Sync` (Note: Cannot include "Xero" in name)
   - **Company URL**: `https://github.com`
   - **Redirect URI**: `http://localhost:8888/callback`
4. Click "Create app"
5. **Save these values** (you'll need them):
   - Client ID
   - Client Secret

### Step 2: Get Xero Tokens Locally

**On your local machine:**

1. Create a `.env` file in the project folder:
   ```
   XERO_CLIENT_ID=your_client_id_from_step1
   XERO_CLIENT_SECRET=your_client_secret_from_step1
   ```

2. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```

3. Run the token generator:
   ```bash
   python get_refresh_token.py
   ```

4. Your browser will open - login to Xero and authorize
5. **Save the output** - you'll need:
   - `XERO_TENANT_ID`
   - `XERO_REFRESH_TOKEN`

---

## Part 2: Set Up Database Schema

### Step 3: Create Database Schema

1. Connect to your DigitalOcean PostgreSQL using your preferred client (pgAdmin, DBeaver, etc.)
2. Open and execute the `setup_schema.sql` file
3. This creates the `xero` schema and all required tables

---

## Part 3: Create and Configure DigitalOcean Droplet

### Step 4: Create Droplet

1. Go to https://cloud.digitalocean.com
2. Click **"Create"** → **"Droplets"**
3. Configure:
   - **Image**: Ubuntu 24.04 LTS
   - **Plan**: Basic ($6/month)
     - Regular: 1GB RAM / 1 vCPU / 25GB SSD
   - **Datacenter**: Choose same region as your database if possible
   - **Authentication**: Choose "Password" and set a root password
   - **Hostname**: `xero-sync-runner` (optional)
4. Click **"Create Droplet"**
5. **Copy the IP address** shown (e.g., `178.62.93.165`)

### Step 5: Whitelist Droplet IP in Database

1. Go to your PostgreSQL database in DigitalOcean
2. Click **"Settings"** tab
3. Scroll to **"Trusted Sources"**
4. Click **"Edit"**
5. Add your Droplet's IP address (from Step 4)
6. Click **"Save"**

### Step 6: Connect to Droplet

**Option A: Use DigitalOcean Web Console (Easiest)**

1. In DigitalOcean, go to your Droplet
2. Click the **"Console"** button (top right)
3. Browser terminal will open
4. Login:
   - Username: `root`
   - Password: (the password you set in Step 4)

**Option B: Use SSH Client**

If you have SSH installed:
```bash
ssh root@YOUR_DROPLET_IP
```

### Step 7: Install Dependencies on Droplet

**Option A: Using Setup Script (Recommended)**

```bash
# Download the repository to get scripts
git clone https://github.com/YOUR_USERNAME/YOUR_REPO_NAME.git
cd YOUR_REPO_NAME/scripts
chmod +x *.sh

# Run installation script
sudo bash install_droplet_dependencies.sh
```

**Option B: Manual Installation**

```bash
# Update system
apt update && apt upgrade -y

# Install Python 3.11 and dependencies
apt install -y python3.11 python3.11-venv python3-pip git curl

# Install PostgreSQL client (for testing)
apt install -y postgresql-client
```

### Step 8: Test Database Connection

**Option A: Using Test Script (Recommended)**

```bash
# In the scripts directory
bash test_db_connection.sh
```

The script will prompt you for your database details and test the connection.

**Option B: Manual Testing**

```bash
# Replace with YOUR actual connection details from DigitalOcean
psql "postgresql://USERNAME:PASSWORD@HOST:25060/DATABASE?sslmode=require"

# If successful, you'll see a postgres prompt
# Type \q and press Enter to exit
```

---

## Part 4: Set Up GitHub Self-Hosted Runner

### Step 9: Get Runner Token from GitHub

1. Go to your GitHub repository
2. Click **"Settings"** → **"Actions"** → **"Runners"**
3. Click **"New self-hosted runner"**
4. Select **"Linux"**
5. **Keep this page open** - you'll need the commands shown

### Step 10: Install GitHub Runner on Droplet

**Option A: Using Setup Script (Recommended)**

```bash
# In the scripts directory
bash install_github_runner.sh
```

The script will prompt you for:
- Your GitHub username
- Your repository name
- Runner token from GitHub

**Option B: Manual Installation**

```bash
# Create a directory for the runner
mkdir actions-runner && cd actions-runner

# Download the latest runner package (use version shown in GitHub)
curl -o actions-runner-linux-x64-2.321.0.tar.gz -L https://github.com/actions/runner/releases/download/v2.321.0/actions-runner-linux-x64-2.321.0.tar.gz

# Extract the installer
tar xzf ./actions-runner-linux-x64-2.321.0.tar.gz

# Configure the runner (use YOUR token from GitHub)
./config.sh --url https://github.com/YOUR_USERNAME/YOUR_REPO_NAME --token YOUR_RUNNER_TOKEN

# When prompted:
# - Enter name of runner [press Enter to use default]: xero-sync-runner
# - Runner group [press Enter for Default]: [Press Enter]
# - Labels [press Enter to skip]: [Press Enter]
# - Work folder [press Enter for _work]: [Press Enter]

# Install as a service (so it runs automatically)
sudo ./svc.sh install

# Start the runner service
sudo ./svc.sh start

# Check it's running
sudo ./svc.sh status
```

**Note:** The automated script (Option A) is recommended as it handles all steps and error checking automatically. See [scripts/README.md](scripts/README.md) for detailed documentation.

### Step 11: Verify Runner is Connected

1. Go back to GitHub → Settings → Actions → Runners
2. You should see your runner listed as **"Idle"** (green dot)
3. If you see it, the runner is connected successfully!

---

## Part 5: Configure GitHub Secrets

### Step 12: Add Secrets to GitHub

1. In your GitHub repository, go to **"Settings"** → **"Secrets and variables"** → **"Actions"**
2. Click **"New repository secret"** for each of these:

| Secret Name | Value |
|---|---|
| `XERO_CLIENT_ID` | From Step 1 |
| `XERO_CLIENT_SECRET` | From Step 1 |
| `XERO_TENANT_ID` | From Step 2 (get_refresh_token.py output) |
| `XERO_REFRESH_TOKEN` | From Step 2 (get_refresh_token.py output) |
| `DB_HOST` | DigitalOcean PostgreSQL host |
| `DB_PORT` | `25060` (usually) |
| `DB_NAME` | Your database name |
| `DB_USER` | Your database username |
| `DB_PASSWORD` | Your database password |

---

## Part 6: Update Workflow to Use Self-Hosted Runner

### Step 13: Update Workflow File

The workflow file needs one simple change:

**Edit `.github/workflows/daily-sync.yml`:**

Change this line:
```yaml
runs-on: ubuntu-latest
```

To:
```yaml
runs-on: self-hosted
```

**Complete updated workflow:**
```yaml
name: Daily Xero Sync

on:
  schedule:
    - cron: '0 2 * * *'  # Daily at 2 AM UTC
  workflow_dispatch:  # Allow manual trigger

jobs:
  sync:
    runs-on: self-hosted  # <-- Changed from ubuntu-latest
    
    steps:
    - name: Checkout code
      uses: actions/checkout@v3
    
    - name: Set up Python
      uses: actions/setup-python@v4
      with:
        python-version: '3.11'
    
    - name: Install dependencies
      run: |
        python3 -m pip install --upgrade pip
        pip install -r requirements.txt
    
    - name: Run Xero sync
      env:
        XERO_CLIENT_ID: ${{ secrets.XERO_CLIENT_ID }}
        XERO_CLIENT_SECRET: ${{ secrets.XERO_CLIENT_SECRET }}
        XERO_TENANT_ID: ${{ secrets.XERO_TENANT_ID }}
        XERO_REFRESH_TOKEN: ${{ secrets.XERO_REFRESH_TOKEN }}
        DB_HOST: ${{ secrets.DB_HOST }}
        DB_PORT: ${{ secrets.DB_PORT }}
        DB_NAME: ${{ secrets.DB_NAME }}
        DB_USER: ${{ secrets.DB_USER }}
        DB_PASSWORD: ${{ secrets.DB_PASSWORD }}
      run: |
        python3 xero_sync.py
    
    - name: Sync complete
      if: success()
      run: echo "Xero sync completed successfully"
    
    - name: Sync failed
      if: failure()
      run: echo "Xero sync failed - check logs"
```

Commit and push this change to GitHub.

---

## Part 7: Test the Setup

### Step 14: Run Manual Test

1. Go to your GitHub repository
2. Click **"Actions"** tab
3. Click **"Daily Xero Sync"** workflow
4. Click **"Run workflow"** dropdown
5. Click **"Run workflow"** button
6. Watch the workflow execute

**If successful, you'll see:**
- Green checkmark next to the workflow run
- Data synced to your PostgreSQL database

**Check your database:**
```sql
-- Check sync log
SELECT * FROM xero.sync_log ORDER BY completed_at DESC LIMIT 10;

-- Check data
SELECT COUNT(*) FROM xero.invoices;
SELECT COUNT(*) FROM xero.contacts;
SELECT COUNT(*) FROM xero.accounts;
```

---

## Maintenance

### View Runner Status

**On your Droplet:**
```bash
cd ~/actions-runner
sudo ./svc.sh status
```

### View Runner Logs

```bash
cd ~/actions-runner
tail -f _diag/Runner_*.log
```

### Restart Runner

```bash
cd ~/actions-runner
sudo ./svc.sh stop
sudo ./svc.sh start
```

### Update Runner

```bash
cd ~/actions-runner
sudo ./svc.sh stop
./config.sh remove --token YOUR_NEW_TOKEN
# Then repeat installation steps with new version
```

---

## Troubleshooting

### Runner Shows Offline

1. Check service status: `sudo ./svc.sh status`
2. Restart service: `sudo ./svc.sh restart`
3. Check runner logs in `_diag/` folder

### Database Connection Failed

1. Verify Droplet IP is whitelisted in PostgreSQL
2. Test connection from Droplet:
   ```bash
   psql "postgresql://USER:PASS@HOST:25060/DB?sslmode=require"
   ```
3. Check firewall rules on both Droplet and Database

### Workflow Fails

1. Check GitHub Actions logs in your repository
2. Verify all secrets are set correctly
3. Check runner is connected (Settings → Actions → Runners)

### Sync Takes Too Long

- The initial sync may take 2-5 minutes for 100k+ rows
- Subsequent syncs are much faster (1-2 minutes)
- Check sync logs: `SELECT * FROM xero.sync_log`

---

## Multi-Tenant Setup

For multiple Xero organizations:

### Option 1: Separate Repositories
- One repo per Xero org
- Each with its own runner and secrets

### Option 2: Multiple Workflows
- One repository
- Multiple workflow files (e.g., `org1-sync.yml`, `org2-sync.yml`)
- Different secret names per org

### Option 3: One Runner, Multiple Databases
- One runner can sync multiple orgs
- Use environment-specific secrets
- Run workflows in sequence

---

## Schedule Customization

Edit the cron schedule in `.github/workflows/daily-sync.yml`:

```yaml
# Daily at 2 AM UTC
- cron: '0 2 * * *'

# Every 6 hours
- cron: '0 */6 * * *'

# 9 AM and 5 PM on weekdays
- cron: '0 9,17 * * MON-FRI'

# Every hour
- cron: '0 * * * *'
```

---

## Support Resources

- [Xero API Documentation](https://developer.xero.com/documentation/)
- [DigitalOcean Droplets](https://docs.digitalocean.com/products/droplets/)
- [GitHub Self-Hosted Runners](https://docs.github.com/en/actions/hosting-your-own-runners)
- [PostgreSQL Documentation](https://www.postgresql.org/docs/)

---

## Summary Checklist

- [ ] Create Xero app and get credentials
- [ ] Run get_refresh_token.py locally to get tokens
- [ ] Set up database schema with setup_schema.sql
- [ ] Create DigitalOcean Droplet ($6/month)
- [ ] Whitelist Droplet IP in PostgreSQL
- [ ] Install dependencies on Droplet
- [ ] Install GitHub self-hosted runner on Droplet
- [ ] Add all 9 secrets to GitHub
- [ ] Update workflow to use `runs-on: self-hosted`
- [ ] Test with manual workflow run
- [ ] Verify data in database

**Estimated setup time**: 30-45 minutes

---

**You're all set!** The workflow will now run daily at 2 AM UTC, syncing your Xero data to PostgreSQL automatically.
