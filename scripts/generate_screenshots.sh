#!/usr/bin/env bash
# Rebuild docs/images/*.png (1920x1080) against the fictional web-demo service.
# Does not touch the real web / asm_cleanup.db volume.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

BASE_URL="${ASM_CLEANUP_DEMO_URL:-http://127.0.0.1:8001}"
DEMO_DB="docs/demo/asm_cleanup_demo.db"
STARTED_DEMO=0

cleanup() {
  if [[ "$STARTED_DEMO" -eq 1 ]]; then
    echo "==> Stopping web-demo"
    docker compose stop web-demo >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT

if [[ ! -f .env ]]; then
  echo "Error: .env not found. Run ./scripts/setup_env.sh first." >&2
  exit 1
fi

# shellcheck disable=SC1091
set -a
source .env
set +a

if [[ -z "${ASM_CLEANUP_PASSWORD:-}" || -z "${ASM_CLEANUP_JWT_SECRET:-}" || -z "${ASM_CLEANUP_KEYRING_KEY:-}" ]]; then
  echo "Error: ASM_CLEANUP_PASSWORD, ASM_CLEANUP_JWT_SECRET, and ASM_CLEANUP_KEYRING_KEY must be set in .env." >&2
  exit 1
fi

if [[ ! -f "$DEMO_DB" ]]; then
  echo "Error: missing $DEMO_DB. Rebuild with: uv run --extra web asm-cleanup db build-demo" >&2
  exit 1
fi

if ! command -v docker >/dev/null 2>&1; then
  echo "Error: docker is required to start web-demo." >&2
  exit 1
fi

if ! command -v uv >/dev/null 2>&1; then
  echo "Error: uv is required." >&2
  exit 1
fi

echo "==> Syncing web + docs dependencies"
uv sync --extra web --group docs

echo "==> Ensuring Playwright Chromium is installed"
uv run --group docs playwright install chromium

echo "==> Starting web-demo (port ${ASM_CLEANUP_DEMO_PORT:-8001})"
docker compose up --build -d web-demo
STARTED_DEMO=1

echo "==> Capturing screenshots at $BASE_URL"
uv run --group docs python scripts/capture_docs_screenshots.py --base-url "$BASE_URL"

echo "==> Done. Wrote docs/images/01-login.png … 05-generated-sql.png"
