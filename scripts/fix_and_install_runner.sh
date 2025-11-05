#!/bin/bash
# Xero PostgreSQL Sync - Fix and Install GitHub Runner (Non-Root)
# This script creates a non-root user and installs the runner properly

set -e  # Exit on error

echo "=================================="
echo "GitHub Runner Installation Fix"
echo "=================================="
echo ""

# Check if running as root
if [ "$EUID" -ne 0 ]; then 
    echo "Error: This script must be run as root"
    echo "Run: sudo bash fix_and_install_runner.sh"
    exit 1
fi

echo "Creating non-root user for GitHub runner..."
echo ""

# Check if runner user exists
if id "runner" &>/dev/null; then
    echo "User 'runner' already exists"
else
    # Create runner user
    adduser --disabled-password --gecos "" runner
    echo "User 'runner' created"
fi

# Add to sudo group
usermod -aG sudo runner
echo "User 'runner' added to sudo group"

# Set password for runner user
echo ""
echo "Set a password for the 'runner' user:"
passwd runner

echo ""
echo "=================================="
echo "Cleaning up previous attempts..."
echo "=================================="

# Clean up any previous root installations
if [ -d "/root/actions-runner" ]; then
    cd /root/actions-runner
    ./svc.sh stop 2>/dev/null || true
    ./svc.sh uninstall 2>/dev/null || true
    cd /root
    rm -rf actions-runner
    rm -f actions-runner-linux-x64-*.tar.gz
    echo "Cleaned up /root/actions-runner"
fi

# Clean up any previous runner user installations
if [ -d "/home/runner/actions-runner" ]; then
    cd /home/runner/actions-runner
    sudo -u runner ./svc.sh stop 2>/dev/null || true
    sudo -u runner ./svc.sh uninstall 2>/dev/null || true
    cd /home/runner
    rm -rf actions-runner
    rm -f actions-runner-linux-x64-*.tar.gz
    echo "Cleaned up /home/runner/actions-runner"
fi

echo ""
echo "=================================="
echo "Runner Installation"
echo "=================================="
echo ""

# Get GitHub details
echo "You need information from GitHub:"
echo "1. Go to: https://github.com/YOUR_USERNAME/YOUR_REPO/settings/actions/runners"
echo "2. Click 'New self-hosted runner'"
echo "3. Select 'Linux'"
echo ""
echo "IMPORTANT: If you see an old runner, delete it first to get a fresh token"
echo ""

read -p "Enter your GitHub username: " GITHUB_USER
read -p "Enter your repository name: " REPO_NAME
read -p "Enter the runner token from GitHub: " RUNNER_TOKEN

echo ""
echo "Installing runner as 'runner' user..."

# Get latest runner version from GitHub
RUNNER_VERSION="2.321.0"
echo "Using GitHub Actions Runner v${RUNNER_VERSION}"
echo "(Check https://github.com/actions/runner/releases for latest version)"

# Switch to runner user and execute installation
sudo -u runner bash << EOF
set -e

cd /home/runner

# Create directory
mkdir -p actions-runner && cd actions-runner

# Download
echo "Downloading runner..."
curl -o actions-runner-linux-x64-${RUNNER_VERSION}.tar.gz -L \
    https://github.com/actions/runner/releases/download/v${RUNNER_VERSION}/actions-runner-linux-x64-${RUNNER_VERSION}.tar.gz

# Extract
echo "Extracting..."
tar xzf ./actions-runner-linux-x64-${RUNNER_VERSION}.tar.gz

# Configure
echo "Configuring runner..."
./config.sh \
    --url https://github.com/${GITHUB_USER}/${REPO_NAME} \
    --token ${RUNNER_TOKEN} \
    --name xero-sync-runner \
    --work _work \
    --replace

echo "Runner configured successfully"
EOF

# Install and start service (needs sudo)
cd /home/runner/actions-runner

echo ""
echo "Installing runner as system service..."
sudo -u runner ./svc.sh install

echo "Starting runner service..."
sudo -u runner ./svc.sh start

echo ""
echo "Checking runner status..."
sudo -u runner ./svc.sh status

echo ""
echo "=================================="
echo "✓ Installation Complete!"
echo "=================================="
echo ""
echo "Verification:"
echo "1. Go to GitHub: Settings → Actions → Runners"
echo "2. You should see 'xero-sync-runner' with green 'Idle' status"
echo ""
echo "The runner is now installed as user 'runner' (not root)"
echo ""
echo "Runner management commands (run as root or runner user):"
echo "  Status:  cd /home/runner/actions-runner && sudo -u runner ./svc.sh status"
echo "  Stop:    cd /home/runner/actions-runner && sudo -u runner ./svc.sh stop"
echo "  Start:   cd /home/runner/actions-runner && sudo -u runner ./svc.sh start"
echo "  Restart: cd /home/runner/actions-runner && sudo -u runner ./svc.sh restart"
echo ""
echo "Recommended: Reboot the system now"
echo "  sudo reboot"
