#!/usr/bin/env bash
# Daily backup of areview data (database + manuscripts) to ~/backups/paper-review (this laptop only).
#   - consistent SQLite copy taken by SQLite itself while the app runs
#   - manuscripts, converted PDFs, figures (data/files)
# Keeps the last 14 backups. Restore: see ~/backups/paper-review/RESTORE.md
set -euo pipefail
cd "$(dirname "$0")/.."
DEST="$HOME/backups/paper-review"
mkdir -p "$DEST" && chmod 700 "$DEST"
STAMP=$(date +%Y%m%d-%H%M)
sg docker -c "docker compose exec -T app python -c \"
import sqlite3
s = sqlite3.connect('/data/review.sqlite3'); d = sqlite3.connect('/data/backup-snapshot.sqlite3')
s.backup(d); d.close(); s.close()\""
OUT="$DEST/areview-$STAMP.tar.gz"
tar czf "$OUT" --exclude='data/review.sqlite3*' \
  --transform 's|data/backup-snapshot.sqlite3|data/review.sqlite3|' data
rm -f data/backup-snapshot.sqlite3
chmod 600 "$OUT"
echo "$(date -Is) backup: $OUT ($(du -h "$OUT" | cut -f1))"
ls -1t "$DEST"/areview-*.tar.gz | tail -n +15 | xargs -r rm -f
