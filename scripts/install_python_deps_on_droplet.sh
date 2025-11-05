#!/bin/bash
# One-time setup: Install Python dependencies on Droplet for runner user

echo "Installing Python dependencies for runner user..."

# Install python3-pip if not already installed
apt-get update
apt-get install -y python3-pip python3-venv

# Install dependencies for runner user
su - runner -c "pip3 install xero-python psycopg2-binary python-dotenv"

echo "✓ Dependencies installed for runner user"
echo ""
echo "You can now run workflows without installing dependencies each time"
