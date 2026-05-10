#!/bin/bash
# Xero PostgreSQL Sync - Droplet Dependencies Installation
# Run this script on your DigitalOcean Droplet after initial creation.
# Tested on Ubuntu 22.04 and 24.04.

set -e

echo "=================================="
echo "Installing Droplet Dependencies"
echo "=================================="
echo ""

echo "Updating system packages..."
apt update
apt upgrade -y

echo "Installing system Python and venv support..."
# Use the distribution's default python3 (3.10 on 22.04, 3.12 on 24.04)
# rather than pinning a specific minor version that may not exist.
apt install -y python3 python3-venv python3-pip python3-dev

echo "Installing build deps for psycopg2 and other native extensions..."
apt install -y build-essential libpq-dev

echo "Installing PostgreSQL client (for test_db_connection.sh)..."
apt install -y postgresql-client

echo "Installing supporting tools..."
apt install -y git curl

echo ""
echo "=================================="
echo "Dependencies installed successfully"
echo "=================================="
echo ""
echo "Next steps:"
echo "1. Test database connection with test_db_connection.sh"
echo "2. Install GitHub runner with fix_and_install_runner.sh"
