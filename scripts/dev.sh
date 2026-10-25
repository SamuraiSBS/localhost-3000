#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
ENV_FILE=${2:-.state/local/compose.env}
PRODUCT_PYTHON=python3
if [[ -x .venv/bin/python ]]; then PRODUCT_PYTHON=.venv/bin/python; fi
case "${1:-start}" in
  start|update) docker compose --env-file "$ENV_FILE" -f compose.yaml up -d --build --wait ;;
  stop) docker compose --env-file "$ENV_FILE" -f compose.yaml down ;;
  logs) docker compose --env-file "$ENV_FILE" -f compose.yaml logs --tail 100 -f ;;
  status) docker compose --env-file "$ENV_FILE" -f compose.yaml ps ;;
  check) "$PRODUCT_PYTHON" scripts/check.py ;;
  *) echo 'Usage: bash scripts/dev.sh start|stop|logs|status|update|check' >&2; exit 2 ;;
esac
