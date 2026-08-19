#!/usr/bin/env bash
# Create or update .env with generated ASM_CLEANUP_PASSWORD, JWT secret, and keyring key.
# Secrets are at least 32 bytes (RFC 7518 / HS256; cryptfile passphrase).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

ENV_FILE=".env"
EXAMPLE_FILE=".env.example"

if [[ ! -f "$EXAMPLE_FILE" ]]; then
  echo "Error: missing $EXAMPLE_FILE" >&2
  exit 1
fi

if [[ ! -f "$ENV_FILE" ]]; then
  cp "$EXAMPLE_FILE" "$ENV_FILE"
  echo "==> Created $ENV_FILE from $EXAMPLE_FILE"
fi

# 24 random bytes -> ~32-char base64 password (URL-safe, no padding).
PASSWORD="$(openssl rand -base64 24 | tr -d '/+=' | head -c 32)"
# 32 random bytes as hex -> 64-char secret (well above the 32-byte HS256 minimum).
JWT_SECRET="$(openssl rand -hex 32)"
KEYRING_KEY="$(openssl rand -hex 32)"

upsert_env() {
  local key="$1"
  local value="$2"
  local tmp
  tmp="$(mktemp)"
  awk -v key="$key" -v val="$value" '
    BEGIN { found = 0 }
    index($0, key "=") == 1 {
      print key "=" val
      found = 1
      next
    }
    { print }
    END {
      if (!found) print key "=" val
    }
  ' "$ENV_FILE" >"$tmp"
  mv "$tmp" "$ENV_FILE"
}

upsert_env "ASM_CLEANUP_PASSWORD" "$PASSWORD"
upsert_env "ASM_CLEANUP_JWT_SECRET" "$JWT_SECRET"
upsert_env "ASM_CLEANUP_KEYRING_KEY" "$KEYRING_KEY"

echo "==> Wrote generated auth values to $ENV_FILE"
echo "    ASM_CLEANUP_PASSWORD=${PASSWORD}"
echo "    ASM_CLEANUP_JWT_SECRET=${JWT_SECRET}"
echo "    ASM_CLEANUP_KEYRING_KEY=${KEYRING_KEY}"
echo "==> Restart web / web-demo so containers pick up the new .env."
