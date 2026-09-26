#!/usr/bin/env bash
# Dev loop helper: runs tooling inside the api image with the working tree live-mounted.
#   scripts/dev.sh test [pytest args]     -> pytest against the traderkirala_test database
#   scripts/dev.sh ruff [args]            -> ruff check app tests scripts (default)
#   scripts/dev.sh alembic <args>         -> e.g. alembic upgrade head
#   scripts/dev.sh py <module or file>    -> python ...
#   scripts/dev.sh shell                  -> bash
# ANVIL_RPC_URL (optional) is forwarded for the `chain` marked tests (local anvil with the contracts deployed).
set -euo pipefail
DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$DIR"
set -a; # shellcheck disable=SC1091
source .env; set +a
NET=traderkirala_traderkirala_net
IMAGE=traderkirala-api:latest
sudo docker compose up -d db >/dev/null 2>&1
CMD="${1:-ruff}"; shift || true
run() {
  sudo docker run --rm -i --user "$(id -u):$(id -g)" -v "$DIR":/app -w /app --network "$NET" \
    --env-file .env -e HOME=/tmp -e ANVIL_RPC_URL="${ANVIL_RPC_URL:-}" \
    -e DATABASE_URL="postgresql+asyncpg://trader:${POSTGRES_PASSWORD}@db:5432/traderkirala" \
    -e DATABASE_URL_TEST="postgresql+asyncpg://trader:${POSTGRES_PASSWORD}@db:5432/traderkirala_test" \
    "$@"
}
case "$CMD" in
  test)    run -e APP_ENV=test --entrypoint pytest "$IMAGE" -q "$@" ;;
  ruff)    if [ $# -eq 0 ]; then set -- check app tests scripts; fi; run --entrypoint ruff "$IMAGE" "$@" ;;
  alembic) run --entrypoint alembic "$IMAGE" "$@" ;;
  py)      run --entrypoint python "$IMAGE" "$@" ;;
  shell)   sudo docker run --rm -it --user "$(id -u):$(id -g)" -v "$DIR":/app -w /app --network "$NET" --env-file .env -e HOME=/tmp --entrypoint bash "$IMAGE" ;;
  *) echo "unknown command: $CMD" >&2; exit 2 ;;
esac
