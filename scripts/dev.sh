#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
case "${1:-start}" in
  start|update) docker compose up -d --build --wait ;;
  stop) docker compose down ;;
  logs) docker compose logs --tail 100 -f ;;
  status) docker compose ps ;;
  check) python3 scripts/check.py ;;
  *) echo 'Usage: bash scripts/dev.sh start|stop|logs|status|update|check' >&2; exit 2 ;;
esac
