#!/usr/bin/env python3
"""Run one HogQL query against Keep's PostHog project and print or save the result.

usage: python3 -I phq.py [out.json] < query.sql
The project id is the environment variable POSTHOG_PROJECT_ID; the personal API key is read from the
macOS keychain item `posthog-personal2` and is never printed.
"""
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request

HOST = "https://eu.posthog.com"
KEYCHAIN_ITEM = "posthog-personal2"


def project() -> str:
    pid = os.environ.get("POSTHOG_PROJECT_ID")
    if not pid:
        sys.exit("POSTHOG_PROJECT_ID is not set: export the PostHog project id this install's telemetry goes to.")
    return pid


def key() -> str:
    try:
        return subprocess.check_output(["security", "find-generic-password", "-s", KEYCHAIN_ITEM, "-w"]).decode().strip()
    except subprocess.CalledProcessError:
        sys.exit(f"No keychain item '{KEYCHAIN_ITEM}'. Add a PostHog personal API key with read access:\n"
                 f"  security add-generic-password -s {KEYCHAIN_ITEM} -a posthog -w <phx_...>")


def query(hogql: str) -> dict:
    req = urllib.request.Request(
        f"{HOST}/api/projects/{project()}/query/",
        data=json.dumps({"query": {"kind": "HogQLQuery", "query": hogql}}).encode(),
        headers={"Authorization": f"Bearer {key()}", "Content-Type": "application/json"})
    try:
        return json.load(urllib.request.urlopen(req, timeout=120))
    except urllib.error.HTTPError as e:
        sys.exit(f"HTTP {e.code}: {e.read()[:500].decode(errors='replace')}")


if __name__ == "__main__":
    d = query(sys.stdin.read())
    out = {"columns": d.get("columns"), "rows": d.get("results", [])}
    if len(sys.argv) > 1:
        json.dump(out, open(sys.argv[1], "w"), ensure_ascii=False)
        print(f"rows: {len(out['rows'])} cols: {out['columns']}")
    else:
        json.dump(out, sys.stdout, ensure_ascii=False, indent=1)
