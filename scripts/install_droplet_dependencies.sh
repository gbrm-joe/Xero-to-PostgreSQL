#!/bin/bash
# Xero PostgreSQL Sync - Droplet Dependencies Installation
# Run this script on your DigitalOcean Droplet after initial creation

set -e  # Exit on error

echo "=================================="
echo "Installing Droplet Dependencies"
echo "=================================="
echo ""

# Update system
echo "Updating system packages..."
apt update && apt upgrade -y

# Install Python 3.11 and dependencies
echo "Installing Python 3.11 and pip..."
apt install -y python3.11 python3.11-venv python3-pip git curl

# Install PostgreSQL client for testing
echo "Installing PostgreSQL client..."
apt install -y postgresql-client

echo ""
echo "=================================="
echo "✓ Dependencies installed successfully!"
echo "=================================="
echo ""
echo "Next steps:"
echo "1. Test database connection with test_db_connection.sh"
echo "2. Install GitHub runner with install_github_runner.sh"
