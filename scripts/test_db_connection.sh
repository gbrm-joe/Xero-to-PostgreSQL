#!/bin/bash
# Xero PostgreSQL Sync - Test Database Connection
# Run this script to verify database connectivity from Droplet

echo "=================================="
echo "Testing Database Connection"
echo "=================================="
echo ""

# Check if psql is installed
if ! command -v psql &> /dev/null; then
    echo "Error: PostgreSQL client not installed"
    echo "Run install_droplet_dependencies.sh first"
    exit 1
fi

# Prompt for connection details
echo "Enter your DigitalOcean PostgreSQL connection details:"
echo "(You can find these in your DO database 'Connection Details')"
echo ""

read -p "Database Host: " DB_HOST
read -p "Database Port (usually 25060): " DB_PORT
read -p "Database Name: " DB_NAME
read -p "Database User: " DB_USER
read -sp "Database Password: " DB_PASSWORD
echo ""
echo ""

# Build connection string
CONNECTION_STRING="postgresql://${DB_USER}:${DB_PASSWORD}@${DB_HOST}:${DB_PORT}/${DB_NAME}?sslmode=require"

echo "Connecting to database..."
echo ""

# Test connection
if psql "$CONNECTION_STRING" -c "SELECT version();" ; then
    echo ""
    echo "=================================="
    echo "✓ Database connection successful!"
    echo "=================================="
    echo ""
    echo "Your Droplet can connect to PostgreSQL."
    echo "Next step: Install GitHub runner with install_github_runner.sh"
else
    echo ""
    echo "=================================="
    echo "✗ Database connection failed!"
    echo "=================================="
    echo ""
    echo "Troubleshooting:"
    echo "1. Verify connection details are correct"
    echo "2. Check Droplet IP is whitelisted in DO PostgreSQL settings"
    echo "3. Check firewall rules on both Droplet and Database"
    exit 1
fi
