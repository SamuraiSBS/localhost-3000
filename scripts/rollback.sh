#!/usr/bin/env bash
set -Eeuo pipefail
ROOT=${1:?Usage: rollback.sh /opt/lazyum}
ROOT=$(realpath "$ROOT")
TARGET=$(readlink -f "$ROOT/previous")
[[ "$TARGET" = "$ROOT"/releases/* && -f "$TARGET/image.env" ]] || { echo 'No accepted previous release' >&2; exit 1; }
# A new immutable release directory keeps original rollback metadata intact.
REVISION=$(cat "$TARGET/revision")
NEW="$ROOT/releases/rollback-${REVISION}-$(date +%s)"
mkdir "$NEW"
cp -a "$TARGET/." "$NEW/"
IMAGE=$(sed -n 's/^APP_IMAGE=//p' "$TARGET/image.env")
bash "$NEW/scripts/deploy.sh" "$ROOT" "$IMAGE" "$REVISION" "$(cat "$TARGET/public-url")" "$(cat "$TARGET/deploy-mode")"
