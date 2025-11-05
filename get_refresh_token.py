#!/usr/bin/env python3
"""
Xero OAuth 2.0 Token Generator
Use this script to obtain your refresh token for the Xero API
"""

import os
import json
import webbrowser
from urllib.parse import urlencode
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
import requests
from dotenv import load_dotenv

load_dotenv()

CLIENT_ID = os.getenv('XERO_CLIENT_ID', '')
CLIENT_SECRET = os.getenv('XERO_CLIENT_SECRET', '')
REDIRECT_URI = 'http://localhost:8888/callback'

class CallbackHandler(BaseHTTPRequestHandler):
    auth_code = None
    
    def do_GET(self):
        """Handle the OAuth callback"""
        parsed_url = urlparse(self.path)
        query_params = parse_qs(parsed_url.query)
        
        if 'code' in query_params:
            CallbackHandler.auth_code = query_params['code'][0]
            self.send_response(200)
            self.send_header('Content-type', 'text/html')
            self.end_headers()
            self.wfile.write(b"""
            <html>
                <body>
                    <h1>Success!</h1>
                    <p>Authorization successful. You can close this window.</p>
                    <p>Check your terminal for the refresh token.</p>
                </body>
            </html>
            """)
        else:
            self.send_response(400)
            self.send_header('Content-type', 'text/html')
            self.end_headers()
            self.wfile.write(b"<html><body><h1>Error</h1><p>No authorization code received</p></body></html>")
    
    def log_message(self, format, *args):
        """Suppress server logs"""
        pass


def get_authorization_code():
    """Step 1: Get authorization code from user"""
    params = {
        'response_type': 'code',
        'client_id': CLIENT_ID,
        'redirect_uri': REDIRECT_URI,
        'scope': 'payroll.payroll payroll.settings',
        'state': 'security_token_string'
    }
    
    auth_url = f'https://login.xero.com/identity/connect/authorize?{urlencode(params)}'
    
    print("\n" + "="*60)
    print("XERO OAUTH TOKEN GENERATOR")
    print("="*60)
    print("\nOpening Xero login in your browser...")
    print(f"If browser doesn't open, visit: {auth_url}")
    
    webbrowser.open(auth_url)
    
    # Start local callback server
    server = HTTPServer(('localhost', 8888), CallbackHandler)
    server.timeout = 120  # 2 minute timeout
    
    print("\nWaiting for authorization (timeout in 120 seconds)...")
    
    while CallbackHandler.auth_code is None:
        server.handle_request()
    
    server.server_close()
    
    if CallbackHandler.auth_code:
        print("✓ Authorization code received")
        return CallbackHandler.auth_code
    else:
        raise Exception("Failed to obtain authorization code")


def get_refresh_token(auth_code):
    """Step 2: Exchange authorization code for tokens"""
    print("\nExchanging authorization code for tokens...")
    
    url = 'https://identity.xero.com/connect/token'
    data = {
        'grant_type': 'authorization_code',
        'code': auth_code,
        'redirect_uri': REDIRECT_URI,
        'client_id': CLIENT_ID,
        'client_secret': CLIENT_SECRET
    }
    
    response = requests.post(url, data=data, timeout=10)
    
    if response.status_code != 200:
        print(f"Error: {response.status_code}")
        print(response.text)
        raise Exception("Failed to obtain refresh token")
    
    tokens = response.json()
    
    print("✓ Tokens obtained successfully")
    
    return {
        'access_token': tokens.get('access_token'),
        'refresh_token': tokens.get('refresh_token'),
        'expires_in': tokens.get('expires_in')
    }


def get_tenant_id(access_token):
    """Step 3: Get Xero Tenant ID"""
    print("\nFetching Xero Tenant ID...")
    
    url = 'https://api.xero.com/connections'
    headers = {
        'Authorization': f'Bearer {access_token}',
        'Accept': 'application/json'
    }
    
    response = requests.get(url, headers=headers, timeout=10)
    
    if response.status_code != 200:
        print(f"Warning: Could not fetch Tenant ID automatically")
        print("You can find it in your Xero account settings")
        return None
    
    connections = response.json()
    if connections and len(connections) > 0:
        tenant_id = connections[0].get('tenantId')
        print(f"✓ Tenant ID: {tenant_id}")
        return tenant_id
    
    return None


def main():
    if not CLIENT_ID or not CLIENT_SECRET:
        print("\nError: XERO_CLIENT_ID and XERO_CLIENT_SECRET environment variables not set")
        print("\nPlease set these variables first:")
        print("  export XERO_CLIENT_ID='your_client_id'")
        print("  export XERO_CLIENT_SECRET='your_client_secret'")
        return 1
    
    try:
        # Step 1: Get authorization code
        auth_code = get_authorization_code()
        
        # Step 2: Exchange for refresh token
        tokens = get_refresh_token(auth_code)
        
        # Step 3: Get tenant ID
        tenant_id = get_tenant_id(tokens['access_token'])
        
        # Display results
        print("\n" + "="*60)
        print("CONFIGURATION COMPLETE")
        print("="*60)
        print("\nAdd these to your GitHub Secrets:")
        print(f"XERO_REFRESH_TOKEN: {tokens['refresh_token']}")
        if tenant_id:
            print(f"XERO_TENANT_ID: {tenant_id}")
        else:
            print("XERO_TENANT_ID: <Find in Xero account settings>")
        
        print("\nAdd these to your .env file (for local testing):")
        print(f"XERO_REFRESH_TOKEN={tokens['refresh_token']}")
        if tenant_id:
            print(f"XERO_TENANT_ID={tenant_id}")
        
        print("\n✓ Token generation complete!")
        
    except KeyboardInterrupt:
        print("\n\nCancelled by user")
    except Exception as e:
        print(f"\nError: {str(e)}")
        return 1
    
    return 0


if __name__ == '__main__':
    exit(main())
