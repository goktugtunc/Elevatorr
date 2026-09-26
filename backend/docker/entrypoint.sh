#!/usr/bin/env bash
# Entrypoint: waits for Postgres, applies migrations (api only), then runs the requested role.
set -euo pipefail

ROLE="${1:-api}"
shift || true

wait_for_db() {
  python - <<'PY'
import asyncio, os, sys, time
import asyncpg
from urllib.parse import urlparse
url = os.environ["DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://")
async def main():
    for i in range(60):
        try:
            conn = await asyncpg.connect(url, timeout=5)
            await conn.close()
            return
        except Exception as e:
            print(f"[entrypoint] db not ready ({e.__class__.__name__}); retry {i+1}/60", flush=True)
            await asyncio.sleep(2)
    sys.exit("database never became ready")
asyncio.run(main())
PY
}

case "$ROLE" in
  api)
    wait_for_db
    echo "[entrypoint] running migrations"
    alembic upgrade head
    if [ -f scripts/seed_assets.py ]; then echo "[entrypoint] seeding assets"; python -m scripts.seed_assets; fi
    # --timeout-worker-healthcheck: the supervisor pings workers; importing the app can exceed the 5 s default on a loaded host
    exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers "${UVICORN_WORKERS:-2}" --timeout-worker-healthcheck 60 --proxy-headers --forwarded-allow-ips='*' --no-server-header
    ;;
  worker)
    wait_for_db
    exec python -m app.worker.main
    ;;
  migrate)
    wait_for_db
    exec alembic "$@"
    ;;
  test)
    wait_for_db
    exec pytest "$@"
    ;;
  shell)
    exec bash
    ;;
  *)
    exec "$ROLE" "$@"
    ;;
esac
