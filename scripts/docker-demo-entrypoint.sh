#!/bin/sh
# Copy the committed demo SQLite into a writable /data path, then start the web UI.
# SQLite needs a writable directory for journal/WAL files next to the DB.
set -eu

SOURCE_DB="${DEMO_DB_SOURCE:-/demo/asm_cleanup_demo.db}"
# Absolute destination used by web-demo DATABASE_URL (sqlite:////data/asm_cleanup_demo.db).
DEST_DB="${DEMO_DB_DEST:-/data/asm_cleanup_demo.db}"

if [ ! -f "$SOURCE_DB" ]; then
  echo "Error: demo database not found at $SOURCE_DB" >&2
  exit 1
fi

mkdir -p "$(dirname "$DEST_DB")"
cp -f "$SOURCE_DB" "$DEST_DB"
echo "Copied demo database to $DEST_DB"
exec asm-cleanup web --host 0.0.0.0 --port 8000
