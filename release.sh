#!/bin/bash
# Publishes master as the release: fast-forwards origin's `release` branch to master's tip
# and tags the commit with its version (the vYYYY.MM.N of common/Version.py). Installs track
# `release` — install.sh clones it and restart_server pulls it — while the development
# machine stays on master, so a release is nothing more than moving that branch.
#
#   ./release.sh            release what origin/master is at
#   ./release.sh <commit>   release an earlier master commit instead (must still be ahead
#                           of the current release; a rollback is a deliberate
#                           `git push -f origin <commit>:release`)
set -euo pipefail
cd "$(dirname "$0")"

git fetch -q origin
target="${1:-origin/master}"
commit=$(git rev-parse --verify "$target^{commit}")
short=$(git rev-parse --short "$commit")
version="v$(git log -1 --format=%cd --date=format:%Y.%m "$commit").$(git rev-list --count "$commit")"

git merge-base --is-ancestor "$commit" origin/master \
  || { echo "$target is not on origin/master — push it first." >&2; exit 1; }

current=$(git rev-parse -q --verify origin/release 2>/dev/null || true)
if [ "$current" = "$commit" ]; then
  echo "release is already at $version ($short)"
  exit 0
fi
if [ -n "$current" ] && ! git merge-base --is-ancestor "$current" "$commit"; then
  echo "origin/release ($(git rev-parse --short "$current")) is not behind $target — refusing to move it backwards." >&2
  exit 1
fi

echo "Releasing $version ($short)"
[ -n "$current" ] && git log --oneline "$current..$commit" | sed 's/^/  /'

if existing=$(git rev-parse -q --verify "refs/tags/$version^{commit}"); then
  [ "$existing" = "$commit" ] || { echo "Tag $version already names $(git rev-parse --short "$existing")." >&2; exit 1; }
else
  git tag -a "$version" -m "Release $version" "$commit"
fi
git push origin "$commit:refs/heads/release" "refs/tags/$version"
echo "Done: release is $version — installs pick it up on their next restart_server."
