#!/usr/bin/env bash
# Daily Postgres dump of the traderkirala database. Keeps 14 days.
# Also run by hand right before `alembic upgrade head` on the monad_cutover migration (irreversible, K10).
# Cron example: 30 3 * * * /home/<user>/traderkirala/backend/scripts/backup.sh
set -euo pipefail
DIR="$(cd "$(dirname "$0")/.." && pwd)"
OUT="$DIR/backups"
mkdir -p "$OUT"
STAMP="$(date +%Y%m%d_%H%M%S)"
sudo docker exec traderkirala-db pg_dump -U trader -d traderkirala --no-owner | gzip > "$OUT/traderkirala_$STAMP.sql.gz"
find "$OUT" -name 'traderkirala_*.sql.gz' -mtime +14 -delete
echo "backup written: $OUT/traderkirala_$STAMP.sql.gz"
