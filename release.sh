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
#   ./release.sh --dry-run [<commit>]
#                           run the checks and print the what's new, but neither tag nor push
#
# After the push, Claude Code (`claude -p`) reads the released commits and writes a "what's
# new" for the household — Telegram-flavoured, since that is where it will one day be sent.
# For now it is only printed; a missing `claude` or a failed run never fails the release.
set -euo pipefail
cd "$(dirname "$0")"

whats_new() {  # $1..$2: the released commits — prints Claude's user-facing summary of them
  command -v claude >/dev/null || { echo "claude is not on PATH — no what's new." >&2; return 0; }
  local log
  log=$(git log --reverse --format='%h %s%n%b' "$1..$2")
  local prompt
  prompt="You are writing the release note for a home assistant called Keep, sent to the household that talks to it.
The release is $version. Below are the commits it brings, oldest first, as '<hash> <subject>' followed by the body.
Run \`git show <hash>\` or \`git show --stat <hash>\` when a message does not make the user-facing effect clear.

Create a user friendly, fairly punchy and nice \"what's new\" for this release. Format it so that it will look nice
in a Telegram chat: the bot sends HTML, so use <b>…</b> and <i>…</i> only, blank lines between items, an emoji
or two where it helps, nothing else (no markdown, no headers, no code spans, no tables).

The readers are not developers. Describe what they will notice — what Keep can now do, what got better, what
was fixed — never how it was built. Leave out refactors, tests, tooling, docs and anything with no visible
effect; if nothing visible changed, say so in one friendly line. Group related commits into one item; keep
it under 15 lines. Output only the note, nothing before or after it.

Commits:
$log"

  echo
  echo "What's new in $version:"
  echo
  claude -p "$prompt" --allowedTools 'Bash(git show:*) Bash(git log:*) Bash(git diff:*) Read Grep' < /dev/null \
    || echo "claude failed — write the what's new by hand." >&2
}

dry_run=false
if [ "${1:-}" = "--dry-run" ]; then dry_run=true; shift; fi

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

if $dry_run; then
  echo "Dry run: not tagging or pushing."
  [ -n "$current" ] && whats_new "$current" "$commit"
  exit 0
fi

if existing=$(git rev-parse -q --verify "refs/tags/$version^{commit}"); then
  [ "$existing" = "$commit" ] || { echo "Tag $version already names $(git rev-parse --short "$existing")." >&2; exit 1; }
else
  git tag -a "$version" -m "Release $version" "$commit"
fi
git push origin "$commit:refs/heads/release" "refs/tags/$version"
echo "Done: release is $version — installs pick it up on their next restart_server."
if [ -n "$current" ]; then whats_new "$current" "$commit"; fi
