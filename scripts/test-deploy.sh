#!/usr/bin/env bash
# Real Docker integration: successful release, rejected release, automatic and manual rollback.
set -euo pipefail
cd "$(dirname "$0")/.."
SOURCE=$PWD
export DEPLOY_PROJECT_NAME=localhost-3000-deploy-test
DELIVERY_ROOT=$(mktemp -d)
cleanup() {
  docker compose --project-name "$DEPLOY_PROJECT_NAME" --env-file "$DELIVERY_ROOT/.env" -f "$SOURCE/compose.yaml" down --volumes >/dev/null 2>&1 || true
  # ROOT is a mktemp-created directory, never an operator-supplied path.
  rm -rf -- "$DELIVERY_ROOT"
}
trap cleanup EXIT
mkdir -p "$DELIVERY_ROOT/releases/one" "$DELIVERY_ROOT/releases/two" "$DELIVERY_ROOT/releases/bad"
for release in one two bad; do
  cp compose*.yaml "$DELIVERY_ROOT/releases/$release/"
  cp -r infra scripts "$DELIVERY_ROOT/releases/$release/"
done
python3 scripts/configure_local.py --ephemeral --directory "$DELIVERY_ROOT/private" --origin http://127.0.0.1:18880 --project "$DEPLOY_PROJECT_NAME"
cp "$DELIVERY_ROOT/private/compose.env" "$DELIVERY_ROOT/.env"
FIRST=${REVISION:-1111111111111111111111111111111111111111}
SECOND=2222222222222222222222222222222222222222
BAD=3333333333333333333333333333333333333333
docker build --build-arg VCS_REF="$FIRST" -t localhost-3000-deploy-test:first .
IMAGE_ONE=$(docker image inspect --format '{{.Id}}' localhost-3000-deploy-test:first)
docker build --build-arg VCS_REF="$SECOND" -t localhost-3000-deploy-test:second .
IMAGE_TWO=$(docker image inspect --format '{{.Id}}' localhost-3000-deploy-test:second)
bash "$DELIVERY_ROOT/releases/one/scripts/deploy.sh" "$DELIVERY_ROOT" "$IMAGE_ONE" "$FIRST" http://127.0.0.1:18880 local
test ! -e "$DELIVERY_ROOT/previous"
bash "$DELIVERY_ROOT/releases/two/scripts/deploy.sh" "$DELIVERY_ROOT" "$IMAGE_TWO" "$SECOND" http://127.0.0.1:18880 local
if bash "$DELIVERY_ROOT/releases/bad/scripts/deploy.sh" "$DELIVERY_ROOT" "$IMAGE_ONE" "$BAD" http://127.0.0.1:18880 local; then
  echo 'Incorrect release was accepted' >&2; exit 1
fi
python3 scripts/smoke.py http://127.0.0.1:18880 --revision "$SECOND" --attempts 1
test "$(readlink -f "$DELIVERY_ROOT/current")" = "$DELIVERY_ROOT/releases/two"
bash "$DELIVERY_ROOT/current/scripts/rollback.sh" "$DELIVERY_ROOT"
python3 scripts/smoke.py http://127.0.0.1:18880 --revision "$FIRST" --attempts 1
echo 'Successful deploy, update, rejection, automatic rollback, manual rollback: PASS'
