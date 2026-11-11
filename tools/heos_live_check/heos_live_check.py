#!/usr/bin/env python3
"""The live check of Keep's HEOS integration: the code the server runs, against the real speakers.

Run by a person — never by the server, never by a test — in Terminal (which has the Local Network
access the speaker search needs), from the root of the checkout under test, with the venv active
and the install's config:

    export FLOXY_CONFIG=~/Keep/config.yaml
    python tools/heos_live_check/heos_live_check.py zones              # rooms, what plays, favorites, Spotify devices
    python tools/heos_live_check/heos_live_check.py login              # the Spotify sign-in with the playback scopes
    python tools/heos_live_check/heos_live_check.py search "query"     # Spotify search; prints the ids play takes
    python tools/heos_live_check/heos_live_check.py play "<room>" <id>  # H-n, S-n, a search id or a spotify: URI
    python tools/heos_live_check/heos_live_check.py pause|resume|next|up|down "<room>"
    python tools/heos_live_check/heos_live_check.py raw system/check_account   # any CLI command, key=value args after it
    python tools/heos_live_check/heos_live_check.py events --seconds 60        # the change events and the status they leave

A search id lives in this process only, so `search` takes `--play "<room>"` to start a hit at
once. The Spotify token is the checkout's own (cache/oauth/spotify.json), so a checkout other
than the install's signs in once with `login`. The exit code is 0 when the step went as expected,
1 when it did not."""
import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
os.chdir(ROOT)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = parser.add_subparsers(dest="step", required=True)
    sub.add_parser("zones")
    sub.add_parser("login")
    search = sub.add_parser("search")
    search.add_argument("query")
    search.add_argument("--play", metavar="ROOM", help="start the first track hit there")
    play = sub.add_parser("play")
    play.add_argument("room")
    play.add_argument("id")
    for step in ("pause", "resume", "next", "up", "down"):
        sub.add_parser(step).add_argument("room")
    events = sub.add_parser("events")
    events.add_argument("--seconds", type=int, default=60)
    raw = sub.add_parser("raw")
    raw.add_argument("command", help="e.g. browse/browse")
    raw.add_argument("params", nargs="*", help="key=value, e.g. sid=1028")
    args = parser.parse_args()

    if not os.environ.get("FLOXY_CONFIG"):
        print("Set FLOXY_CONFIG to the install's config.yaml first (export FLOXY_CONFIG=~/Keep/config.yaml).")
        return 1

    from assistant.integration.music.SpotifyAccount import SpotifyAccount
    from integrations.heos.HeosIntegration import HeosIntegration

    HeosIntegration._schedule_refresh = lambda self, delay: None   # the check reloads when it needs to
    heos = HeosIntegration()
    account = SpotifyAccount.get_instance()

    if args.step == "events":
        import time
        from assistant.integration.music import MusicSystem as music
        heos.zones()   # connects, so the events know the device
        music.add_zone_listener(lambda: print("  zones now: " + " | ".join(heos.zone_status())))
        heos._start_events()
        print(f"Listening {args.seconds} s — play, pause or skip something in the HEOS or Spotify app.")
        time.sleep(args.seconds)
        print("pushes changes:", heos.pushes_changes())
        return 0 if heos.pushes_changes() else 1

    if args.step == "raw":
        import json
        from integrations.heos.HeosClient import HeosError
        params = dict(p.split("=", 1) for p in args.params)
        try:
            print(json.dumps(heos.client().command(args.command, **params), indent=2, ensure_ascii=False))
        except HeosError as e:
            print(e)
            return 1
        return 0

    if args.step == "zones":
        print("Rooms:", ", ".join(heos.zones()) or "none")
        for line in heos.zone_status():
            print("  " + line)
        heos.refresh_library()
        print("Favorites:\n  " + (heos.library_text().replace("\n", "\n  ") or "none"))
        try:
            devices = heos.connect().devices()
            print("Spotify Connect devices:", ", ".join(d.name for d in devices) or "none")
        except Exception as e:
            print(f"Spotify Connect devices not read: {e}")
        return 0

    if args.step == "login":
        sign_in = account.login_begin()
        print(f"{sign_in.instructions}\n")
        pasted = input("The address the browser landed on: ").strip()
        failure = account.login_finish(pasted)
        print(f"Not signed in: {failure}" if failure else f"Signed in as {account.user_name() or '?'}")
        return 1 if failure else 0

    if args.step == "search":
        hits = account.search(args.query)
        print(hits)
        if not args.play:
            return 0
        first = next((line.rsplit("ID: ", 1)[1].strip() for line in hits.splitlines() if "ID: " in line), None)
        if first is None:
            print("No hit to play.")
            return 1
        args.room, args.id, args.step = args.play, first, "play"

    actions = {"play": lambda: heos.play_id(args.room, args.id), "pause": lambda: heos.pause(args.room),
               "resume": lambda: heos.play(args.room), "next": lambda: heos.next_track(args.room),
               "up": lambda: heos.adjust_volume(args.room, 4), "down": lambda: heos.adjust_volume(args.room, -4)}
    if args.step == "play":
        heos.refresh_library()   # the H- ids
    problem = actions[args.step]()
    print(f"{args.step}: " + (problem or "done"))
    return 1 if problem else 0


if __name__ == "__main__":
    sys.exit(main())
