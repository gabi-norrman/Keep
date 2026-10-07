"""Turn a Keep server log into a chat-style HTML transcript: what the user typed, what the assistant
answered, the commands it ran with their replies, the masked prompts, server restarts and first-run
milestones. Stdlib only, needs no config.

usage: python3 -I tools/generate_html_report_from_logs/log_transcript.py <log dir> <out.html> [--assistant NAME]

<log dir> is a copy of a server's `log/` folder: `floxy.log` (the plain log) and `command_replies/`
(the full command replies, linked from the commands that produced them). The user's name comes from
the log; the assistant's does not, hence the option.
"""
import argparse
import html
import re
from datetime import datetime
from pathlib import Path

ANSI = re.compile(r"\x1b\[[0-9;]*m")
LINE = re.compile(r"^(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d\.\d{3})\s+\+[\d.]+\s+\[(\w+)\]\s?(.*)$")
TURN = re.compile(r"^(\w+) \((Console|Telegram|Voice|HTTP)\): ?$")
REPLY = re.compile(r"^Claude \(([^)]+)\):$")
NOISE = (
    "Photos:", "ICloudPhotoDownloader:", "Gemini:", "Crystal:", "Rem:", "Failed after 3 attempts",
    "Error getting status for zone", "Failed to update section", "Telemetry", "First token", "First reply token",
    "Sending to Claude", "No voice", "Config reloaded", "Receiving background command reply",
    "background commands done", "Console chat:", "Console [tui]", "HistoryFeed", "Fetching memory",
    "Claude (", "Command done.", "Usage stats", "Saving command reply", "MessageHistory:", "Watch list",
    "Procedures section", "Vault section", "Killed ", "Killing request", "list_integrations:",
)


def parse(log_path: Path):
    events = []
    state = None          # "turn" | "reply" | "raw" | None
    cur = None
    cmd_group = None      # the commands of the current LLM round, waiting for their reply file
    for raw in log_path.read_text(errors="replace").splitlines():
        line = ANSI.sub("", raw)
        m = LINE.match(line)
        if not m:
            continue
        ts, level, text = m.group(1), m.group(2), m.group(3)
        if level in ("TRACE", "FINE") and text.startswith("  "):
            text = text[2:]
        t = datetime.strptime(ts, "%Y-%m-%d %H:%M:%S.%f")

        if state == "turn":
            if level == "NOTE":
                cur["lines"].append(text)
                continue
            events.append(cur); state = cur = None
        elif state == "reply":
            if level == "NOTE":
                cur["lines"].append(text); continue
            if level in ("TRACE", "FINE"):
                continue   # other threads interleave their trace lines with the streamed reply
            if level == "INFO" and text.startswith("Raw:"):
                events.append(cur); state = "raw"; cur = None; continue
            events.append(cur); state = cur = None
        elif state == "raw":
            if level == "INFO":
                continue
            state = None

        if level == "NOTE" and text.startswith("Running command: "):
            ev = {"kind": "cmd", "t": t, "text": text[len("Running command: "):], "result": []}
            events.append(ev)
            if cmd_group is None or (t - cmd_group["t"]).total_seconds() > 60:
                cmd_group = {"t": t, "cmds": []}
            cmd_group["cmds"].append(ev)
            continue
        if level == "TRACE" and text.startswith("Saving command reply to "):
            name = text.rsplit("/", 1)[-1].rstrip(".")
            if cmd_group:
                cmd_group["cmds"][-1].setdefault("reply_file", name)
                cmd_group = None
            continue
        if level in ("INFO", "NOTE") and text.startswith("Keep AI Server starting"):
            events.append({"kind": "server", "t": t, "text": "Server starting"}); continue
        if level == "NOTE" and text.startswith("Ready to handle requests"):
            events.append({"kind": "server", "t": t, "text": "Server ready"}); continue
        if level == "NOTE" and text.startswith("Received SIGTERM"):
            events.append({"kind": "server", "t": t, "text": "Server shutting down (SIGTERM)"}); continue
        if level == "INFO" and text.startswith("Console chat: showing secret prompt"):
            key = text.split("Setting ", 1)[-1]
            events.append({"kind": "secret", "t": t, "text": key}); continue
        if level == "INFO" and text.startswith("Sending telegram message: "):
            events.append({"kind": "tg_out", "t": t, "lines": [text[len("Sending telegram message: "):]]}); continue
        if level in ("INFO", "NOTE") and (text.startswith("First run:") or text.startswith("In the FTUE:")):
            if not events or events[-1].get("text") != text:
                events.append({"kind": "firstrun", "t": t, "text": text})
            continue
        tm = TURN.match(text)
        if level == "INFO" and tm:
            cur = {"kind": "turn", "t": t, "who": tm.group(1), "channel": tm.group(2), "lines": []}
            state = "turn"; continue
        rm = REPLY.match(text)
        if level == "INFO" and rm:
            cur = {"kind": "reply", "t": t, "model": rm.group(1), "lines": []}
            state = "reply"; cmd_group = cmd_group; continue
        if level in ("INFO", "WARN", "ERROR") and events and events[-1]["kind"] == "cmd" \
                and not text.startswith(NOISE) and text.strip() and len(events[-1]["result"]) < 6 \
                and (t - events[-1]["t"]).total_seconds() < 30:
            events[-1]["result"].append((level, text))
    if cur:
        events.append(cur)
    return events


ALLOWED = re.compile(r"&lt;(/?)(b|i|u|s|code|pre|strong|em)&gt;")


def rich(text: str) -> str:
    out = html.escape(text)
    out = ALLOWED.sub(r"<\1\2>", out)
    out = re.sub(r"&lt;a href=&quot;([^&]*)&quot;&gt;", r'<a href="\1" target="_blank">', out)
    out = out.replace("&lt;/a&gt;", "</a>")
    return out


def para(lines):
    text = "\n".join(lines).strip("\n")
    return rich(text).replace("\n", "<br>")


def fmt_t(t):
    return t.strftime("%H:%M:%S")


def render(events, replies_dir: Path, log_dir: Path, assistant: str) -> str:
    parts = []
    last_t = None
    n_user = n_reply = n_cmd = n_restart = 0
    enabled = []
    first = events[0]["t"]
    last = events[-1]["t"]
    users = [e["who"] for e in events if e["kind"] == "turn" and e["who"] != "System"]
    user = max(set(users), key=users.count) if users else "User"
    for ev in events:
        t = ev["t"]
        if last_t and (t - last_t).total_seconds() > 300:
            mins = int((t - last_t).total_seconds() // 60)
            parts.append(f'<div class="gap">{mins} min later</div>')
        last_t = t
        k = ev["kind"]
        if k == "turn":
            body = para(ev["lines"])
            if ev["who"] == "System":
                head = ev["lines"][0] if ev["lines"] else ""
                parts.append(
                    f'<details class="system"><summary><span class="time">{fmt_t(t)}</span> '
                    f'<b>System brief</b> ({ev["channel"]}) — {html.escape(head[:110])}…</summary>'
                    f'<div class="body">{body}</div></details>')
            else:
                n_user += 1
                parts.append(
                    f'<div class="row user"><div class="bubble"><div class="meta">{html.escape(ev["who"])} '
                    f'· {ev["channel"]} · {fmt_t(t)}</div>{body}</div></div>')
        elif k == "reply":
            n_reply += 1
            if not any(l.strip() for l in ev["lines"]):
                continue
            parts.append(
                f'<div class="row ai"><div class="bubble"><div class="meta">{html.escape(assistant)} · {fmt_t(t)}</div>'
                f'{para(ev["lines"])}</div></div>')
        elif k == "cmd":
            n_cmd += 1
            name = ev["text"].split("(", 1)[0]
            if name == "enable_integration":
                enabled.append((t, ev["text"][len("enable_integration("):-1]))
            res = ""
            if ev["result"]:
                res = "".join(f'<div class="res {lv.lower()}">{html.escape(tx)}</div>' for lv, tx in ev["result"])
            reply = ""
            rf = ev.get("reply_file")
            if rf and (replies_dir / rf).exists():
                content = (replies_dir / rf).read_text(errors="replace")
                shown = content if len(content) <= 6000 else content[:6000] + f"\n… [{len(content) - 6000} more chars]"
                reply = (f'<details class="reply"><summary>command reply · {len(content):,} chars · {rf[:8]}</summary>'
                         f'<pre>{html.escape(shown)}</pre></details>')
            parts.append(
                f'<div class="row cmd"><div class="chip"><span class="time">{fmt_t(t)}</span> '
                f'<code>{html.escape(ev["text"])}</code>{res}{reply}</div></div>')
        elif k == "secret":
            parts.append(f'<div class="row cmd"><div class="chip secret"><span class="time">{fmt_t(t)}</span> '
                         f'🔒 masked prompt shown to the user: <code>{html.escape(ev["text"])}</code></div></div>')
        elif k == "tg_out":
            parts.append(
                f'<div class="row ai"><div class="bubble tg"><div class="meta">{html.escape(assistant)} → Telegram (notification) · {fmt_t(t)}</div>'
                f'{para(ev["lines"])}</div></div>')
        elif k == "server":
            if ev["text"].startswith("Server starting"):
                n_restart += 1
            parts.append(f'<div class="divider"><span>{fmt_t(t)} · {html.escape(ev["text"])}</span></div>')
        elif k == "firstrun":
            parts.append(f'<div class="note"><span class="time">{fmt_t(t)}</span> {html.escape(ev["text"])}</div>')

    dur = last - first
    hours, rem = divmod(int(dur.total_seconds()), 3600)
    enabled_html = "".join(f"<li>{fmt_t(t)} · {html.escape(n)}</li>" for t, n in enabled) or "<li>none</li>"
    stats = f"""
<section class="summary">
  <h2>Overview</h2>
  <ul>
    <li><b>Date:</b> {first:%Y-%m-%d}, {fmt_t(first)} → {fmt_t(last)} ({hours} h {rem // 60} min of log)</li>
    <li><b>User messages:</b> {n_user} &nbsp; <b>AI replies:</b> {n_reply} &nbsp; <b>Commands run:</b> {n_cmd} &nbsp; <b>Server starts:</b> {n_restart}</li>
    <li><b>Integrations enabled, in order:</b><ul>{enabled_html}</ul></li>
  </ul>
  <p class="src">Source: <code>{html.escape(str(log_dir))}/floxy.log</code> and <code>command_replies/</code>.
  Blue bubbles are what {html.escape(user)} typed, grey ones what {html.escape(assistant)} answered, the monospace chips are the commands it ran
  (expand one to see the reply the LLM got back). The system brief is the hidden first-run instruction the server sends.</p>
</section>"""

    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(user)} — chat transcript</title>
<style>
:root {{ --bg:#f5f6f8; --fg:#1d1f23; --muted:#6b7280; --user:#dbeafe; --ai:#ffffff; --cmd:#f1f5f9; --line:#e5e7eb; --sys:#fff7e6; --warn:#b45309; --err:#b91c1c; }}
@media (prefers-color-scheme: dark) {{ :root:not([data-theme="light"]) {{ --bg:#111317; --fg:#e5e7eb; --muted:#9ca3af; --user:#1e3a5f; --ai:#1f2329; --cmd:#181c22; --line:#2a2f37; --sys:#2b2410; --warn:#f59e0b; --err:#f87171; }} }}
:root[data-theme="dark"] {{ --bg:#111317; --fg:#e5e7eb; --muted:#9ca3af; --user:#1e3a5f; --ai:#1f2329; --cmd:#181c22; --line:#2a2f37; --sys:#2b2410; --warn:#f59e0b; --err:#f87171; }}
* {{ box-sizing: border-box; }}
body {{ margin:0; background:var(--bg); color:var(--fg); font: 15px/1.45 -apple-system, "Segoe UI", Helvetica, Arial, sans-serif; }}
main {{ max-width: 860px; margin: 0 auto; padding: 24px 16px 80px; }}
h1 {{ font-size: 22px; margin: 0 0 4px; }}
.summary {{ background: var(--ai); border:1px solid var(--line); border-radius: 12px; padding: 12px 18px; margin: 16px 0 28px; }}
.summary h2 {{ font-size: 15px; margin: 4px 0 8px; }}
.summary ul {{ margin: 0; padding-left: 18px; }}
.summary .src {{ color: var(--muted); font-size: 13px; }}
.row {{ display:flex; margin: 10px 0; }}
.row.user {{ justify-content: flex-end; }}
.row.ai {{ justify-content: flex-start; }}
.row.cmd {{ justify-content: flex-start; margin: 4px 0 4px 24px; }}
.bubble {{ max-width: 78%; padding: 10px 14px; border-radius: 16px; border:1px solid var(--line); white-space: normal; overflow-wrap: anywhere; }}
.user .bubble {{ background: var(--user); border-bottom-right-radius: 4px; }}
.ai .bubble {{ background: var(--ai); border-bottom-left-radius: 4px; }}
.bubble.tg {{ border-style: dashed; }}
.meta {{ font-size: 11.5px; color: var(--muted); margin-bottom: 4px; }}
.bubble pre, .bubble code {{ font-size: 13px; }}
.bubble pre {{ background: var(--cmd); padding: 6px 8px; border-radius: 6px; white-space: pre-wrap; }}
.chip {{ max-width: 86%; background: var(--cmd); border:1px solid var(--line); border-radius: 8px; padding: 6px 10px; font-size: 13px; }}
.chip > code {{ font-weight: 600; overflow-wrap: anywhere; }}
.chip.secret {{ border-color: #c084fc; }}
.time {{ color: var(--muted); font-size: 11.5px; margin-right: 6px; font-variant-numeric: tabular-nums; }}
.res {{ color: var(--muted); font-size: 12.5px; margin-top: 2px; padding-left: 10px; overflow-wrap: anywhere; }}
.res.warn {{ color: var(--warn); }} .res.error {{ color: var(--err); }}
details.reply {{ margin-top: 4px; }}
details.reply summary {{ cursor: pointer; color: var(--muted); font-size: 12px; }}
details.reply pre {{ font-size: 12px; white-space: pre-wrap; max-height: 420px; overflow: auto; background: var(--ai); border:1px solid var(--line); padding: 8px; border-radius: 6px; }}
details.system {{ background: var(--sys); border:1px solid var(--line); border-radius: 10px; padding: 8px 12px; margin: 14px 0; font-size: 13.5px; }}
details.system summary {{ cursor: pointer; }}
details.system .body {{ margin-top: 8px; white-space: normal; }}
.divider {{ text-align:center; margin: 26px 0 14px; position: relative; }}
.divider:before {{ content:""; position:absolute; left:0; right:0; top:50%; border-top:1px solid var(--line); }}
.divider span {{ position: relative; background: var(--bg); padding: 0 12px; color: var(--muted); font-size: 12.5px; font-weight: 600; }}
.gap {{ text-align:center; color: var(--muted); font-size: 12px; margin: 14px 0 6px; }}
.note {{ color: var(--muted); font-size: 12.5px; margin: 6px 0 6px 24px; }}
a {{ color: inherit; }}
</style></head>
<body><main>
<h1>{html.escape(user)} and {html.escape(assistant)} — chat transcript</h1>
<div class="meta">Keep AI server · {first:%Y-%m-%d} · generated from the log bundle</div>
{stats}
{''.join(parts)}
</main></body></html>"""


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("log_dir", type=Path)
    ap.add_argument("out", type=Path)
    ap.add_argument("--assistant", default="Assistant", help="the assistant's name, as the user knows it")
    args = ap.parse_args()
    log_dir, out = args.log_dir, args.out
    events = parse(log_dir / "floxy.log")
    out.write_text(render(events, log_dir / "command_replies", log_dir, args.assistant))
    kinds = {}
    for e in events:
        kinds[e["kind"]] = kinds.get(e["kind"], 0) + 1
    empty = sum(1 for e in events if e["kind"] == "reply" and not any(l.strip() for l in e["lines"]))
    print(out, kinds, "empty replies:", empty)


main()
