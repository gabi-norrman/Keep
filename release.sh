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
# Before tagging, Claude Code (`claude -p`) reads the released commits and writes a "what's
# new" for the household, which becomes the body of the annotated tag. An install's pull
# brings the tag along with the commit, and the server's first start on it announces the
# note (common/StartupTracker.py reads it through Version.release_notes()). A missing
# `claude` or a failed run leaves the tag without a note and never fails the release.
#
# The commits listed and summarised reach back to the last release tagged on an earlier day,
# not merely to the previous release: installs update nightly, so a household that gets the
# evening release never saw the midday one, and its note has to cover the whole day.
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
or two where it helps, nothing else (no markdown, no headers, no code spans, no tables). Do not open with a
title or the version: the message that carries the note already says which release it is.

The readers are not developers. Describe what they will notice — what Keep can now do, what got better, what
was fixed — never how it was built. Leave out refactors, tests, tooling, docs and anything with no visible
effect; if nothing visible changed, say so in one friendly line. Group related commits into one item; keep
it under 15 lines. Output only the note, nothing before or after it.

Commits:
$log"

  claude -p "$prompt" --allowedTools 'Bash(git show:*) Bash(git log:*) Bash(git diff:*) Read Grep' < /dev/null \
    || echo "claude failed — the tag gets no what's new." >&2
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

# The baseline the listing and the note start from: the newest release tag from before today
# that is behind the target. The day's first release has none newer than origin/release itself.
baseline="$current"
today=$(date +%Y-%m-%d)
while read -r day tag; do
  [[ "$day" < "$today" ]] || continue
  tagged=$(git rev-parse -q --verify "refs/tags/$tag^{commit}") || continue
  if git merge-base --is-ancestor "$tagged" "$commit"; then baseline="$tagged"; break; fi
done < <(git for-each-ref --sort=-taggerdate 'refs/tags/v*' --format='%(taggerdate:format-local:%Y-%m-%d) %(refname:short)')

echo "Releasing $version ($short)"
if [ -n "$baseline" ]; then
  [ "$baseline" != "$current" ] && echo "  (since $(git describe --tags --exact-match "$baseline" 2>/dev/null || git rev-parse --short "$baseline"), the last release before today — the ones released earlier today are included)"
  git log --oneline "$baseline..$commit" | sed 's/^/  /'
fi

existing=$(git rev-parse -q --verify "refs/tags/$version^{commit}" || true)
if [ -n "$existing" ] && [ "$existing" != "$commit" ]; then
  echo "Tag $version already names $(git rev-parse --short "$existing")." >&2; exit 1
fi

note=""
if [ -n "$existing" ]; then
  note=$(git tag -l --format='%(contents:body)' "$version")
  echo "Tag $version exists already; keeping its what's new."
elif [ -n "$baseline" ]; then
  note=$(whats_new "$baseline" "$commit")
fi
echo; echo "What's new in $version:"; echo; echo "${note:-(none)}"; echo

if $dry_run; then
  echo "Dry run: not tagging or pushing."
  exit 0
fi
if [ -t 0 ]; then
  read -r -p "Tag $version with this note and push the release? [y/N] " answer
  [[ "$answer" =~ ^[Yy] ]] || { echo "Not released."; exit 1; }
fi
if [ -z "$existing" ]; then
  printf 'Release %s\n\n%s\n' "$version" "$note" | git tag -a "$version" -F - "$commit"
fi
git push origin "$commit:refs/heads/release" "refs/tags/$version"
echo "Done: release is $version — installs pick it up on their next restart_server."
