#!/usr/bin/env python3
"""Dump every PostHog event of one Keep install and print a readable digest of it.

usage: python3 -I fetch_events.py <distinct_id> <out_dir>

Writes <out_dir>/events.json (uuid, event, timestamp, properties — chronological) and prints
the digest to stdout: every event in local Swedish time with its properties, the repeated
FTUE prompt collapsed, and a few derived facts at the end. Read the digest before writing
the analysis; it is the whole story of the install.
"""
import json
import os
import re
import statistics
import sys
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import phq  # noqa: E402

TZ = ZoneInfo("Europe/Stockholm")
TAG = re.compile(r"<[^>]+>")


def is_ftue_prompt(ev, p):
    return ev == "user_turn" and p.get("query", "").startswith("The server has just started")


def main(distinct_id, out_dir):
    os.makedirs(out_dir, exist_ok=True)
    d = phq.query(f"""
        select uuid, event, timestamp, properties from events
        where distinct_id = '{distinct_id}' order by timestamp asc limit 5000""")
    rows = d.get("results", [])
    if not rows:
        sys.exit(f"No events for distinct_id {distinct_id} in project {phq.PROJECT}.")
    json.dump({"columns": d.get("columns"), "rows": rows}, open(os.path.join(out_dir, "events.json"), "w"), ensure_ascii=False)

    events = []
    for uuid, ev, ts, props in rows:
        p = json.loads(props) if isinstance(props, str) else props
        t = datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone(TZ)
        events.append((ev, t, p))

    print(f"# {len(events)} events for {distinct_id}, {events[0][1]:%Y-%m-%d %H:%M} → {events[-1][1]:%Y-%m-%d %H:%M} (Europe/Stockholm)\n")
    prompt_shown = False
    for ev, t, p in events:
        if is_ftue_prompt(ev, p):
            if prompt_shown:
                print(f"[{t:%a %d %b %H:%M:%S}] user_turn — FTUE prompt injected again (same text as above)")
            else:
                prompt_shown = True
                print(f"[{t:%a %d %b %H:%M:%S}] user_turn — FTUE prompt injected (first time; full text):\n{p['query']}\n")
            continue
        print(f"[{t:%a %d %b %H:%M:%S}] {ev}")
        for k, v in p.items():
            if k.startswith("$") or k == "version":
                continue
            print(f"   {k}: {json.dumps(v, ensure_ascii=False)}")

    human = [(t, p) for ev, t, p in events if ev == "user_turn" and not is_ftue_prompt(ev, p)]
    assistant = [(t, p) for ev, t, p in events if ev == "assistant_turn"]
    starts = [(t, p) for ev, t, p in events if ev == "heartbeat" and p.get("startup")]
    versions = []
    for ev, t, p in events:
        v = p.get("version")
        if v and (not versions or versions[-1][1] != v):
            versions.append((t, v))
    sittings, last = 0, None
    for t, _ in human:
        if last is None or t - last > timedelta(minutes=30):
            sittings += 1
        last = t
    a_words = [len(TAG.sub("", p.get("reply", "")).split()) for _, p in assistant] or [0]
    print("\n# Derived facts")
    print(f"human messages: {len(human)}, sittings (gap > 30 min): {sittings}")
    print(f"assistant replies: {len(assistant)}, proactive: {sum(1 for _, p in assistant if p.get('proactive'))}, "
          f"median words {int(statistics.median(a_words))}, max {max(a_words)}")
    print(f"server starts: {len(starts)}; versions: " + ", ".join(f"{v} from {t:%d %b %H:%M}" for t, v in versions))
    print("integrations switched: " + ", ".join(f"{p.get('integration')} {ev.split('_')[1]} {t:%d %b %H:%M} (state {p.get('state')})"
                                               for ev, t, p in events if ev.startswith("integration_")))
    last_hb = next((p for ev, t, p in reversed(events) if ev == "heartbeat"), {})
    print(f"integrations on at last heartbeat: {last_hb.get('integrations')}")
    print(f"memory_size events: {sum(1 for ev, _, _ in events if ev == 'memory_size')}")
    feeds = [p.get("feeds") for ev, t, p in events if ev == "status" and p.get("feeds")]
    print(f"status events with a non-empty feeds map: {len(feeds)}" + (f", last: {feeds[-1]}" if feeds else ""))
    if human:
        print(f"last human message: {human[-1][0]:%a %d %b %H:%M}; last event: {events[-1][1]:%a %d %b %H:%M}")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    main(sys.argv[1], sys.argv[2])
