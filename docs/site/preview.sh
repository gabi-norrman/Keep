#!/bin/bash
# Previews the public readme page: builds docs/site/out/index.html exactly as the Pages workflow
# does and opens it in the browser. Nothing is published — that happens on a push to master.
#
#   docs/site/preview.sh
#
# The build renders through GitHub's Markdown API, which allows an anonymous caller 60 requests
# an hour per IP address; the token of a logged-in `gh` lifts that, so it is used when neither
# GITHUB_TOKEN nor GH_TOKEN is set.
set -euo pipefail
cd "$(dirname "$0")/../.."

if [ -z "${GITHUB_TOKEN:-}${GH_TOKEN:-}" ] && command -v gh >/dev/null; then
  GH_TOKEN=$(gh auth token 2>/dev/null || true)
  export GH_TOKEN
fi
[ -n "${GITHUB_TOKEN:-}${GH_TOKEN:-}" ] || echo "preview: no GitHub token (gh not logged in) — rendering anonymously, 60 requests an hour" >&2

python3 docs/site/build.py
open docs/site/out/index.html
