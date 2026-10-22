#!/usr/bin/env bash
# Run from an extracted release directory on the VPS; never executes git pull.
set -Eeuo pipefail
ROOT=${1:?Usage: deploy.sh ROOT IMAGE REVISION PUBLIC_URL [local]}
IMAGE=${2:?immutable image required}
REVISION=${3:?commit SHA required}
PUBLIC_URL=${4:?external URL required}
MODE=${5:-production}
[[ "$ROOT" = /* && "$ROOT" != / && "$ROOT" != *[[:space:]]* ]] || exit 2
[[ "$REVISION" =~ ^[a-f0-9]{40}$ ]] || exit 2
[[ "$IMAGE" =~ ^(ghcr.io/[a-z0-9._/-]+@)?sha256:[a-f0-9]{64}$ ]] || { echo 'Immutable digest required' >&2; exit 2; }
[[ "$MODE" = production || "$MODE" = local ]] || exit 2
[[ "$PUBLIC_URL" =~ ^https?:// ]] || exit 2
ROOT=$(realpath "$ROOT")
RELEASE=$(cd "$(dirname "$0")/.." && pwd)
[[ "$RELEASE" = "$ROOT"/releases/* ]] || { echo 'Release must be inside ROOT/releases' >&2; exit 2; }
test -f "$ROOT/.env"
mkdir -p "$ROOT/state"
exec 9>"$ROOT/state/deploy.lock"
flock -n 9 || { echo 'Another deployment is running' >&2; exit 1; }
PREVIOUS=
if [[ -L "$ROOT/current" ]]; then
  PREVIOUS=$(readlink -f "$ROOT/current")
  [[ "$PREVIOUS" = "$ROOT"/releases/* && -f "$PREVIOUS/image.env" ]] || { echo 'Invalid current release pointer' >&2; exit 2; }
fi
[[ "$PREVIOUS" != "$RELEASE" ]] || { echo 'Release directory already current' >&2; exit 2; }
umask 077
printf 'APP_IMAGE=%s\n' "$IMAGE" > "$RELEASE/image.env"
printf '%s\n' "$MODE" > "$RELEASE/deploy-mode"
printf '%s\n' "$REVISION" > "$RELEASE/revision"
printf '%s\n' "$PUBLIC_URL" > "$RELEASE/public-url"
dc() {
  local release=$1; shift
  local files=(-f "$release/compose.yaml")
  if [[ "$(cat "$release/deploy-mode")" = production ]]; then
    files+=(-f "$release/compose.production.yaml")
  fi
  docker compose --project-name "${DEPLOY_PROJECT_NAME:-localhost-3000}" --env-file "$ROOT/.env" --env-file "$release/image.env" "${files[@]}" "$@"
}
dc "$RELEASE" config --quiet
# Pull/inspect before touching the running application.
if ! docker image inspect "$IMAGE" >/dev/null 2>&1; then docker pull "$IMAGE"; fi
dc "$RELEASE" pull proxy
restore() {
  local result=$?
  trap - ERR INT TERM
  echo 'Deployment failed; restoring the last accepted release' >&2
  if [[ -n "$PREVIOUS" && -f "$PREVIOUS/image.env" ]]; then
    if dc "$PREVIOUS" up -d --no-build --pull never --wait --wait-timeout 90 &&
       python3 "$PREVIOUS/scripts/smoke.py" "$(cat "$PREVIOUS/public-url")" --revision "$(cat "$PREVIOUS/revision")"; then
      echo 'Previous release restored and verified' >&2
    else
      echo 'ROLLBACK FAILED: inspect docker compose logs on the server' >&2
      exit 2
    fi
  else
    dc "$RELEASE" down || true
    echo 'First deployment failed; no prior release exists' >&2
  fi
  exit "$((result == 0 ? 1 : result))"
}
trap restore ERR INT TERM
dc "$RELEASE" up -d --no-build --pull never --wait --wait-timeout 90
python3 "$RELEASE/scripts/smoke.py" "$PUBLIC_URL" --revision "$REVISION"
if [[ -n "$PREVIOUS" ]]; then
  ln -sfn "$PREVIOUS" "$ROOT/state/previous.next"
  mv -Tf "$ROOT/state/previous.next" "$ROOT/previous"
fi
ln -sfn "$RELEASE" "$ROOT/state/current.next"
mv -Tf "$ROOT/state/current.next" "$ROOT/current"
trap - ERR INT TERM
echo "Accepted release $REVISION"
