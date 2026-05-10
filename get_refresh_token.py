#!/usr/bin/env python3
"""
Xero OAuth bootstrap for one tenant slug.

Usage:
    python get_refresh_token.py --tenant <slug>

Reads Xero client ID/secret from <SLUG_UPPER>_XERO_CLIENT_ID and
<SLUG_UPPER>_XERO_CLIENT_SECRET in env. Writes the refresh token,
access token, and expiry into finance_<slug>.tokens, and writes the
matching Xero tenant UUID into org.units.xero_tenant_id for the row
with slug=<slug>.
"""

import argparse
import os
import re
import sys
import webbrowser
from datetime import datetime, timedelta
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import parse_qs, urlencode, urlparse

import psycopg2
from psycopg2 import sql
import requests
from dotenv import load_dotenv

load_dotenv()

REDIRECT_URI = 'http://localhost:8888/callback'
SCOPES = (
    'offline_access '
    'accounting.transactions '
    'accounting.contacts '
    'accounting.settings '
    'accounting.journals.read'
)
SLUG_RE = re.compile(r'^[a-z][a-z0-9_]{0,19}$')


class CallbackHandler(BaseHTTPRequestHandler):
    auth_code = None

    def do_GET(self):
        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)
        if 'code' in params:
            CallbackHandler.auth_code = params['code'][0]
            self.send_response(200)
            self.send_header('Content-type', 'text/html')
            self.end_headers()
            self.wfile.write(
                b'<html><body><h1>Success.</h1>'
                b'<p>You can close this tab.</p></body></html>'
            )
        else:
            self.send_response(400)
            self.send_header('Content-type', 'text/html')
            self.end_headers()
            self.wfile.write(b'<html><body><h1>No code received.</h1></body></html>')

    def log_message(self, fmt, *args):
        pass


def get_authorization_code(client_id):
    params = {
        'response_type': 'code',
        'client_id': client_id,
        'redirect_uri': REDIRECT_URI,
        'scope': SCOPES,
        'state': 'security_token_string',
    }
    auth_url = f'https://login.xero.com/identity/connect/authorize?{urlencode(params)}'
    print('Opening Xero login in your browser...')
    print(f'If it does not open, visit: {auth_url}')
    webbrowser.open(auth_url)

    server = HTTPServer(('localhost', 8888), CallbackHandler)
    server.timeout = 120
    print('Waiting for authorization (timeout 120s)...')
    while CallbackHandler.auth_code is None:
        server.handle_request()
    server.server_close()
    return CallbackHandler.auth_code


def exchange_code_for_tokens(auth_code, client_id, client_secret):
    response = requests.post(
        'https://identity.xero.com/connect/token',
        data={
            'grant_type': 'authorization_code',
            'code': auth_code,
            'redirect_uri': REDIRECT_URI,
            'client_id': client_id,
            'client_secret': client_secret,
        },
        timeout=10,
    )
    if not response.ok:
        sys.exit(
            f'Token exchange failed ({response.status_code}): {response.text}'
        )
    return response.json()


def fetch_connections(access_token):
    response = requests.get(
        'https://api.xero.com/connections',
        headers={
            'Authorization': f'Bearer {access_token}',
            'Accept': 'application/json',
        },
        timeout=10,
    )
    response.raise_for_status()
    return response.json()


def pick_tenant(connections, expected_tenant_id):
    if expected_tenant_id:
        for conn in connections:
            if conn.get('tenantId') == str(expected_tenant_id):
                return conn
        sys.exit(
            f'Expected tenant {expected_tenant_id} not present in /connections '
            f'response. Make sure the user authorised the right Xero org.'
        )
    if len(connections) == 1:
        return connections[0]
    print('\nMultiple connections returned by Xero. Pick one:')
    for idx, conn in enumerate(connections, 1):
        print(f'  [{idx}] {conn.get("tenantName")}  {conn.get("tenantId")}')
    while True:
        choice = input('Which connection? (number): ').strip()
        try:
            return connections[int(choice) - 1]
        except (ValueError, IndexError):
            print('Invalid choice.')


def main():
    parser = argparse.ArgumentParser(
        description='Bootstrap Xero OAuth for one tenant slug.'
    )
    parser.add_argument(
        '--tenant', required=True,
        help='Tenant slug (e.g. gbrm, mgl, mgi). Must exist in org.units.slug.'
    )
    args = parser.parse_args()

    slug = args.tenant.strip().lower()
    if not SLUG_RE.match(slug):
        sys.exit(
            f'Invalid slug "{slug}". '
            f'Must be lowercase alphanumeric/underscore, 1-20 chars, starting with a letter.'
        )
    prefix = slug.upper()

    client_id = os.getenv(f'{prefix}_XERO_CLIENT_ID')
    client_secret = os.getenv(f'{prefix}_XERO_CLIENT_SECRET')
    if not client_id or not client_secret:
        sys.exit(
            f'Missing {prefix}_XERO_CLIENT_ID and/or {prefix}_XERO_CLIENT_SECRET in env.'
        )

    db_host = os.getenv('DB_HOST')
    db_port = os.getenv('DB_PORT', '5432')
    db_name = os.getenv('DB_NAME')
    db_user = os.getenv('DB_USER')
    db_password = os.getenv('DB_PASSWORD')
    if not all([db_host, db_name, db_user, db_password]):
        sys.exit('Missing DB_HOST / DB_NAME / DB_USER / DB_PASSWORD in env.')

    conn = psycopg2.connect(
        host=db_host, port=db_port, dbname=db_name, user=db_user, password=db_password
    )
    try:
        with conn.cursor() as cur:
            cur.execute(
                'SELECT unit_id, xero_tenant_id FROM org.units WHERE slug = %s',
                (slug,),
            )
            row = cur.fetchone()
        if not row:
            sys.exit(
                f'No row in org.units with slug = "{slug}". '
                f'Backfill the slug column first.'
            )
        unit_id, existing_tenant_id = row

        print(f'Bootstrapping OAuth for slug={slug} (unit_id={unit_id})')

        auth_code = get_authorization_code(client_id)
        tokens = exchange_code_for_tokens(auth_code, client_id, client_secret)
        access_token = tokens['access_token']
        refresh_token = tokens['refresh_token']
        expires_in = tokens.get('expires_in', 1800)
        access_token_expires_at = datetime.now() + timedelta(seconds=expires_in)

        connections = fetch_connections(access_token)
        if not connections:
            sys.exit(
                'Xero returned no connections. '
                'The user must grant access to at least one org.'
            )

        chosen = pick_tenant(connections, existing_tenant_id)
        tenant_id = chosen['tenantId']
        tenant_name = chosen.get('tenantName', '?')

        schema = f'finance_{slug}'
        with conn.cursor() as cur:
            cur.execute(
                'UPDATE org.units SET xero_tenant_id = %s, updated_at = NOW() '
                'WHERE unit_id = %s',
                (tenant_id, unit_id),
            )
            cur.execute(
                sql.SQL(
                    'INSERT INTO {}.tokens '
                    '(refresh_token, access_token, access_token_expires_at) '
                    'VALUES (%s, %s, %s)'
                ).format(sql.Identifier(schema)),
                (refresh_token, access_token, access_token_expires_at),
            )
        conn.commit()

        print()
        print('Bootstrap complete:')
        print(f'  org.units.xero_tenant_id -> {tenant_id} ({tenant_name})')
        print(f'  {schema}.tokens          -> 1 row inserted')
        print(f'  access token expires at  -> {access_token_expires_at:%Y-%m-%d %H:%M:%S}')
    finally:
        conn.close()


if __name__ == '__main__':
    main()
