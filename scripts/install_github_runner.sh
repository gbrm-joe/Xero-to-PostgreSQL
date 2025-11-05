#!/bin/bash
# Xero PostgreSQL Sync - GitHub Actions Runner Installation
# Run this script on your Droplet to install and configure the GitHub Actions runner

set -e  # Exit on error

echo "=================================="
echo "GitHub Actions Runner Setup"
echo "=================================="
echo ""

# Check if already installed
if [ -d "$HOME/actions-runner" ]; then
    echo "Warning: actions-runner directory already exists"
    read -p "Do you want to reinstall? This will remove the existing installation (y/N): " -n 1 -r
    echo
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        cd $HOME/actions-runner
        sudo ./svc.sh stop 2>/dev/null || true
        sudo ./svc.sh uninstall 2>/dev/null || true
        cd $HOME
        rm -rf actions-runner
    else
        echo "Installation cancelled"
        exit 0
    fi
fi

# Get GitHub details
echo "You'll need information from GitHub:"
echo "1. Go to your repository on GitHub"
echo "2. Click Settings → Actions → Runners"
echo "3. Click 'New self-hosted runner'"
echo "4. Select 'Linux'"
echo ""

read -p "Enter your GitHub username: " GITHUB_USER
read -p "Enter your repository name: " REPO_NAME
read -p "Enter the runner token from GitHub: " RUNNER_TOKEN

echo ""
echo "Installing runner..."

# Create directory
mkdir -p $HOME/actions-runner && cd $HOME/actions-runner

# Download latest runner (check GitHub for current version)
RUNNER_VERSION="2.321.0"
echo "Downloading GitHub Actions Runner v${RUNNER_VERSION}..."
curl -o actions-runner-linux-x64-${RUNNER_VERSION}.tar.gz -L \
    https://github.com/actions/runner/releases/download/v${RUNNER_VERSION}/actions-runner-linux-x64-${RUNNER_VERSION}.tar.gz

# Extract
echo "Extracting..."
tar xzf ./actions-runner-linux-x64-${RUNNER_VERSION}.tar.gz

# Configure
echo ""
echo "Configuring runner..."
./config.sh \
    --url https://github.com/${GITHUB_USER}/${REPO_NAME} \
    --token ${RUNNER_TOKEN} \
    --name xero-sync-runner \
    --work _work \
    --replace

# Install as service
echo ""
echo "Installing as system service..."
sudo ./svc.sh install

# Start service
echo "Starting runner service..."
sudo ./svc.sh start

# Check status
echo ""
echo "Checking runner status..."
sudo ./svc.sh status

echo ""
echo "=================================="
echo "✓ GitHub Runner installed successfully!"
echo "=================================="
echo ""
echo "Verification:"
echo "1. Check GitHub: Settings → Actions → Runners"
echo "2. You should see 'xero-sync-runner' with a green 'Idle' status"
echo ""
echo "Next steps:"
echo "1. Add secrets to GitHub repository"
echo "2. Push code changes to GitHub"
echo "3. Test workflow manually"
echo ""
echo "Runner management commands:"
echo "  Status:  cd ~/actions-runner && sudo ./svc.sh status"
echo "  Stop:    cd ~/actions-runner && sudo ./svc.sh stop"
echo "  Start:   cd ~/actions-runner && sudo ./svc.sh start"
echo "  Restart: cd ~/actions-runner && sudo ./svc.sh restart"
