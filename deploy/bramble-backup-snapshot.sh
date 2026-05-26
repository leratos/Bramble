#!/bin/sh
# Create a consistent SQLite snapshot of Bramble's live WAL database.
#
# Intended to be called by the existing Borg backup script before
# `borg create`. The script prints the snapshot path on success so the
# caller can include that path in Borg's file list.

set -eu

DB_PATH="${BRAMBLE_DB_PATH:-/opt/bramble/data/bramble.db}"
STAGING_DIR="${BRAMBLE_BACKUP_STAGING:-/opt/bramble/backup-staging}"
SNAPSHOT_PATH="${BRAMBLE_BACKUP_SNAPSHOT:-$STAGING_DIR/bramble.db}"
SQLITE_BIN="${SQLITE_BIN:-sqlite3}"
SERVICE_USER="${BRAMBLE_SERVICE_USER:-bramble}"
SERVICE_GROUP="${BRAMBLE_SERVICE_GROUP:-bramble}"

if ! command -v "$SQLITE_BIN" >/dev/null 2>&1; then
    echo "error: sqlite3 not found; install the sqlite3 package or set SQLITE_BIN" >&2
    exit 2
fi

if [ ! -r "$DB_PATH" ]; then
    echo "error: database is not readable: $DB_PATH" >&2
    exit 2
fi

mkdir -p "$STAGING_DIR"
chmod 0750 "$STAGING_DIR" 2>/dev/null || true

if [ "$(id -u)" -eq 0 ]; then
    chown "$SERVICE_USER:$SERVICE_GROUP" "$STAGING_DIR" 2>/dev/null || true
fi

tmp="${SNAPSHOT_PATH}.tmp.$$"
cleanup() {
    rm -f "$tmp"
}
trap cleanup EXIT INT TERM

rm -f "$tmp"
"$SQLITE_BIN" "$DB_PATH" ".backup '$tmp'"

integrity="$("$SQLITE_BIN" "$tmp" "PRAGMA integrity_check;")"
if [ "$integrity" != "ok" ]; then
    echo "error: snapshot failed integrity_check: $integrity" >&2
    exit 3
fi

mv "$tmp" "$SNAPSHOT_PATH"
trap - EXIT INT TERM

if [ "$(id -u)" -eq 0 ]; then
    chown "$SERVICE_USER:$SERVICE_GROUP" "$SNAPSHOT_PATH" 2>/dev/null || true
fi
chmod 0640 "$SNAPSHOT_PATH" 2>/dev/null || true

printf '%s\n' "$SNAPSHOT_PATH"
