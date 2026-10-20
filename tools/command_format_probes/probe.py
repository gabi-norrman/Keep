"""Probes for the command-formats work: what Ollama (Qwen) and ds4-server (DeepSeek) do with a
tool call to a function defined only in the system text — see docs/plans/COMMAND_FORMATS_PLAN.md.

Run from anywhere with the system python (stdlib only):

    python3 tools/command_format_probes/probe.py ollama-tools      # swallowed without a declared tool?
    python3 tools/command_format_probes/probe.py ollama-stream     # how tool_calls arrive chunk by chunk
    python3 tools/command_format_probes/probe.py ollama-replay     # text vs structured history: rendering + cache
    python3 tools/command_format_probes/probe.py ds4-dsml          # DSML parsed without tools? (non-streamed)
    python3 tools/command_format_probes/probe.py ds4-dsml stream   # the streamed deltas (mangled text, then tool_calls)
    python3 tools/command_format_probes/probe.py ds4-replay        # text vs structured history: ds4 cache lines
    python3 tools/command_format_probes/probe.py ds4-cache         # identical prompt twice: live kv cache hit?

Ollama on the test host listens on the network; ds4-server binds 127.0.0.1, so its requests and
both logs go over ssh (BatchMode — the key must be loaded). Results of 2026-10-20 are in the plan.
"""
import json
import re
import subprocess
import sys
import urllib.request

HOST = "100.100.59.111"
OLLAMA = f"http://{HOST}:11434"
OLLAMA_MODEL = "qwen3.8:27b-mlx"
OLLAMA_LOG = "/Users/maciek/Keep/log/ollama.log"
DS4_LOG = "/Users/maciek/Documents/ds4/ds4-server.log"
DS4_MODEL = "deepseek-v4.1-flash"
DS_ENCODER_URL = "https://huggingface.co/deepseek-ai/DeepSeek-V4-Flash/raw/main/encoding/encoding_dsv4.py"

TURN_ON_SCHEMA = {"name": "turn_on", "description": "Turn a light on",
                  "parameters": {"type": "object", "properties": {"light_name": {"type": "string"}},
                                 "required": ["light_name"]}}
QWEN_SYSTEM = ("# Tools\n\nYou have access to the following functions:\n\n<tools>\n"
               + json.dumps({"type": "function", "function": TURN_ON_SCHEMA})
               + "\n</tools>\n\nIf you choose to call a function ONLY reply in the following format with NO suffix:\n\n"
               "<tool_call>\n<function=example_function_name>\n<parameter=example_parameter_1>\nvalue_1\n</parameter>\n"
               "</function>\n</tool_call>\n\nYou are a home assistant.")
DUMMY_TOOL = {"type": "function", "function": {"name": "load_toolkit", "description": "Load a toolkit",
              "parameters": {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]}}}
QWEN_CALL_TEXT = "<tool_call>\n<function=turn_on>\n<parameter=light_name>\nkitchen lamp\n</parameter>\n</function>\n</tool_call>"
DSML = "｜DSML｜"
DSML_CALL_TEXT = (f'<{DSML}tool_calls>\n<{DSML}invoke name="turn_on">\n'
                  f'<{DSML}parameter name="light_name" string="true">kitchen lamp</{DSML}parameter>\n'
                  f'</{DSML}invoke>\n</{DSML}tool_calls>')


def ssh(cmd: str, stdin: bytes | None = None, timeout: int = 200) -> str:
    return subprocess.run(["ssh", "-o", "BatchMode=yes", HOST, cmd], input=stdin,
                          capture_output=True, timeout=timeout).stdout.decode()


def ollama_chat(messages, tools=None, stream=False, think=False, render_only=False):
    req = {"model": OLLAMA_MODEL, "stream": stream, "think": think, "options": {"num_predict": 300},
           "messages": messages}
    if tools is not None:
        req["tools"] = tools
    if render_only:
        req["_debug_render_only"] = True
    r = urllib.request.urlopen(urllib.request.Request(
        f"{OLLAMA}/api/chat", data=json.dumps(req).encode(), headers={"content-type": "application/json"}),
        timeout=170)
    return r if stream else json.load(r)


def ollama_cache_line() -> str:
    out = ssh(f"grep -E 'prefix_cache.go:151' {OLLAMA_LOG} | tail -n 1").strip()
    return out[out.find("msg="):]


def ds4_chat(messages, stream=False, **extra):
    req = {"model": DS4_MODEL, "stream": stream, "max_tokens": 400, "reasoning_effort": "low", "messages": messages}
    req.update(extra)
    flag = "-N " if stream else ""
    return ssh(f"curl -s {flag}-m 170 http://127.0.0.1:8000/v1/chat/completions "
               f"-H 'content-type: application/json' -d @-", stdin=json.dumps(req).encode())


def ds4_log(n: int = 10) -> str:
    return ssh(f"tail -n {n} {DS4_LOG} | grep -E 'kv cache|prompt done|tool calls|finish=|checkpoint'")


def ds4_cache_line() -> str:
    """How ds4-server's log describes the last request's prefill. The server writes a `live kv
    cache miss` line (the live context's size, the prompt's, how much of it the context had —
    `common=`) only on a miss; a hit has none, and its `prompt done` line shows a context range
    starting past 0 (`ctx=4867..4878:11` — 11 tokens prefilled)."""
    lines = ssh(f"grep -E 'live kv cache|prompt done' {DS4_LOG} | tail -n 2").strip().splitlines()
    if not lines:
        return "(no prefill line in the log)"
    done = lines[-1][lines[-1].find("chat ctx="):] if "prompt done" in lines[-1] else lines[-1]
    if (len(lines) > 1 and "live kv cache miss" in lines[-2] and "prompt done" in lines[-1]
            and _seconds_apart(lines[-2], lines[-1]) <= 120):
        return lines[-2][lines[-2].find("live kv cache"):] + " | " + done
    return "live kv cache HIT (no miss line) | " + done


def _seconds_apart(a: str, b: str) -> int:
    """The seconds between two log lines (`MMDD HH:MM:SS …`), so a miss line of an earlier,
    abandoned request is not paired with this request's `prompt done`."""
    def at(line):
        h, m, s = line.split(" ", 2)[1].split(":")
        return int(h) * 3600 + int(m) * 60 + int(s)
    return abs(at(b) - at(a))


def dsml_system() -> str:
    """DeepSeek's own `## Tools` block (from the reference encoder) with the turn_on schema in it."""
    src = urllib.request.urlopen(DS_ENCODER_URL, timeout=30).read().decode()
    template = re.search(r'TOOLS_TEMPLATE = """(.*?)"""', src, re.S).group(1)
    return "You are a home assistant.\n\n" + template.format(
        dsml_token=DSML, thinking_start_token="<think>", thinking_end_token="</think>",
        tool_schemas=json.dumps(TURN_ON_SCHEMA))


def show_ollama(label, d):
    m = d["message"]
    print(f"== {label}: done_reason={d.get('done_reason')}\n   content={m.get('content')!r}\n"
          f"   tool_calls={json.dumps(m.get('tool_calls'))}")


def ollama_tools():
    user = {"role": "user", "content": "Turn on the kitchen lamp."}
    show_ollama("no tools", ollama_chat([{"role": "system", "content": QWEN_SYSTEM}, user]))
    show_ollama("tools=[]", ollama_chat([{"role": "system", "content": QWEN_SYSTEM}, user], tools=[]))
    show_ollama("tools=[dummy load_toolkit], turn_on only in text",
                ollama_chat([{"role": "system", "content": QWEN_SYSTEM}, user], tools=[DUMMY_TOOL]))


def ollama_stream():
    system = QWEN_SYSTEM + " Say one short sentence to the user before calling a function."
    r = ollama_chat([{"role": "system", "content": system}, {"role": "user", "content": "Turn on the kitchen lamp."}],
                    tools=[DUMMY_TOOL], stream=True, think=True)
    content = thinking = ""
    calls = []
    for n, line in enumerate(r, 1):
        d = json.loads(line)
        m = d.get("message", {})
        content += m.get("content", "")
        thinking += m.get("thinking", "")
        if m.get("tool_calls"):
            calls.append((n, m["tool_calls"]))
        if d.get("done"):
            print("done_reason:", d.get("done_reason"), "chunks:", n)
    print("thinking:", repr(thinking)[:200])
    print("content:", repr(content))
    print("tool_calls (chunk no, calls):", json.dumps(calls)[:600])


def ollama_replay():
    turn1 = [{"role": "system", "content": QWEN_SYSTEM}, {"role": "user", "content": "Turn on the kitchen lamp."}]
    d = ollama_chat(turn1, tools=[DUMMY_TOOL])
    call = d["message"]["tool_calls"][0]
    print("turn1 call:", json.dumps(call), "\n   cache:", ollama_cache_line())
    structured = turn1 + [{"role": "assistant", "content": "", "tool_calls": [call]},
                          {"role": "tool", "tool_name": "turn_on", "content": "Kitchen lamp is on"},
                          {"role": "user", "content": "Thanks. Now turn on the hall lamp."}]
    text = turn1 + [{"role": "assistant", "content": QWEN_CALL_TEXT},
                    {"role": "user", "content": "<tool_response>\nKitchen lamp is on\n</tool_response>\n\n"
                                                "Thanks. Now turn on the hall lamp."}]
    for label, msgs in (("structured", structured), ("text", text)):
        rendered = ollama_chat(msgs, tools=[DUMMY_TOOL], render_only=True).get("_debug_info", {}).get("rendered_template")
        print(f"=== rendering of {label} history:\n{rendered}")
    for label, msgs in (("text", text), ("structured", structured)):
        ollama_chat(turn1, tools=[DUMMY_TOOL])  # same starting state for both
        d = ollama_chat(msgs, tools=[DUMMY_TOOL])
        print(f"({label}) replay -> {json.dumps(d['message'].get('tool_calls'))[:200]}\n   cache: {ollama_cache_line()}")


def ds4_dsml(stream: bool):
    msgs = [{"role": "system", "content": dsml_system()}, {"role": "user", "content": "Turn on the kitchen lamp."}]
    out = ds4_chat(msgs, stream=stream)
    if not stream:
        m = json.loads(out)["choices"][0]
        print("finish:", m.get("finish_reason"), "\nmessage:", json.dumps(m["message"], ensure_ascii=False)[:800])
        return
    content, deltas, finish, n = "", [], None, 0
    for line in out.splitlines():
        if not line.startswith("data: ") or line.strip() == "data: [DONE]":
            continue
        n += 1
        for c in json.loads(line[6:])["choices"]:
            content += c["delta"].get("content") or ""
            if c["delta"].get("tool_calls"):
                deltas.append(c["delta"]["tool_calls"])
            finish = c.get("finish_reason") or finish
    print("chunks:", n, "finish:", finish, "\ncontent:", repr(content), "\ntool_call deltas:", json.dumps(deltas)[:600])


def ds4_replay():
    turn1 = [{"role": "system", "content": dsml_system()}, {"role": "user", "content": "Turn on the kitchen lamp."}]
    call = json.loads(ds4_chat(turn1))["choices"][0]["message"]["tool_calls"][0]
    print("turn1 call:", json.dumps(call), "\n", ds4_log())
    text = turn1 + [{"role": "assistant", "content": "\n\n" + DSML_CALL_TEXT},
                    {"role": "user", "content": "<tool_result>Kitchen lamp is on</tool_result>\n\n"
                                                "Thanks. Now turn on the hall lamp."}]
    structured = turn1 + [{"role": "assistant", "content": "", "tool_calls": [call]},
                          {"role": "tool", "tool_call_id": call["id"], "content": "Kitchen lamp is on"},
                          {"role": "user", "content": "Thanks. Now turn on the hall lamp."}]
    for label, msgs in (("text", text), ("structured", structured)):
        ds4_chat(turn1)  # same starting state for both
        m = json.loads(ds4_chat(msgs))["choices"][0]["message"]
        print(f"({label}) replay -> {json.dumps(m.get('tool_calls'))[:200]}\n{ds4_log(6)}")


def ds4_cache():
    msgs = [{"role": "system", "content": "You are a home assistant. Answer in one short sentence."},
            {"role": "user", "content": "What colour is the sky on a clear day?"}]
    for label in ("first", "identical repeat"):
        d = json.loads(ds4_chat(msgs, max_tokens=60))
        print(f"== {label}: {d['choices'][0]['message'].get('content', '')[:60]!r} "
              f"usage={d.get('usage', {}).get('prompt_tokens_details')}\n{ds4_log(6)}")


PROBES = {"ollama-tools": ollama_tools, "ollama-stream": ollama_stream, "ollama-replay": ollama_replay,
          "ds4-dsml": lambda: ds4_dsml("stream" in sys.argv[2:]), "ds4-replay": ds4_replay, "ds4-cache": ds4_cache}

if __name__ == "__main__":
    if len(sys.argv) < 2 or sys.argv[1] not in PROBES:
        sys.exit(__doc__)
    PROBES[sys.argv[1]]()
