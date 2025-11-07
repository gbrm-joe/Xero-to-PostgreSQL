-- Create tokens table for storing Xero OAuth tokens
-- This allows the application to persist and automatically refresh tokens

CREATE TABLE IF NOT EXISTS xero.tokens (
    id SERIAL PRIMARY KEY,
    refresh_token TEXT NOT NULL,
    access_token TEXT,
    access_token_expires_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);

-- Create index for faster lookups
CREATE INDEX IF NOT EXISTS idx_tokens_updated_at ON xero.tokens(updated_at DESC);

-- Insert a placeholder row that will be updated with actual tokens
-- The application will update this row with real tokens on first run
INSERT INTO xero.tokens (refresh_token, access_token, access_token_expires_at)
VALUES ('PLACEHOLDER', NULL, NULL)
ON CONFLICT DO NOTHING;

COMMENT ON TABLE xero.tokens IS 'Stores Xero OAuth tokens for automatic refresh';
COMMENT ON COLUMN xero.tokens.refresh_token IS 'Current refresh token from Xero (updated on each token refresh)';
COMMENT ON COLUMN xero.tokens.access_token IS 'Current access token (valid for 30 minutes)';
COMMENT ON COLUMN xero.tokens.access_token_expires_at IS 'Timestamp when access token expires';
