#!/usr/bin/env bash
# Real Docker integration: successful release, rejected release, automatic and manual rollback.
set -euo pipefail
cd "$(dirname "$0")/.."
SOURCE=$PWD
export DEPLOY_PROJECT_NAME=lazyum-deploy-test
ROOT=$(mktemp -d)
cleanup() {
  docker compose --project-name "$DEPLOY_PROJECT_NAME" -f "$SOURCE/compose.yaml" down --volumes >/dev/null 2>&1 || true
  # ROOT is a mktemp-created directory, never an operator-supplied path.
  rm -rf -- "$ROOT"
}
trap cleanup EXIT
mkdir -p "$ROOT/releases/one" "$ROOT/releases/two" "$ROOT/releases/bad"
for release in one two bad; do
  cp compose*.yaml "$ROOT/releases/$release/"
  cp -r infra scripts "$ROOT/releases/$release/"
done
printf 'SITE_ADDRESS=:80\nHTTP_PORT=18880\nBIND_ADDRESS=127.0.0.1\n' > "$ROOT/.env"
FIRST=${REVISION:-1111111111111111111111111111111111111111}
SECOND=2222222222222222222222222222222222222222
BAD=3333333333333333333333333333333333333333
docker build --build-arg VCS_REF="$FIRST" -t lazyum-deploy-test:first .
IMAGE_ONE=$(docker image inspect --format '{{.Id}}' lazyum-deploy-test:first)
docker build --build-arg VCS_REF="$SECOND" -t lazyum-deploy-test:second .
IMAGE_TWO=$(docker image inspect --format '{{.Id}}' lazyum-deploy-test:second)
bash "$ROOT/releases/one/scripts/deploy.sh" "$ROOT" "$IMAGE_ONE" "$FIRST" http://127.0.0.1:18880 local
bash "$ROOT/releases/two/scripts/deploy.sh" "$ROOT" "$IMAGE_TWO" "$SECOND" http://127.0.0.1:18880 local
if bash "$ROOT/releases/bad/scripts/deploy.sh" "$ROOT" "$IMAGE_ONE" "$BAD" http://127.0.0.1:18880 local; then
  echo 'Incorrect release was accepted' >&2; exit 1
fi
python3 scripts/smoke.py http://127.0.0.1:18880 --revision "$SECOND" --attempts 1
test "$(readlink -f "$ROOT/current")" = "$ROOT/releases/two"
bash "$ROOT/current/scripts/rollback.sh" "$ROOT"
python3 scripts/smoke.py http://127.0.0.1:18880 --revision "$FIRST" --attempts 1
echo 'Successful deploy, update, rejection, automatic rollback, manual rollback: PASS'
