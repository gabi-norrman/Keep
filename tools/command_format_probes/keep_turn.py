"""One Keep turn against the test host in the active command format — the end-to-end check of
docs/plans/COMMAND_FORMATS_PLAN.md (step 4: Qwen on Ollama; step 5: DSML on ds4-server).

    source .venv/bin/activate && python tools/command_format_probes/keep_turn.py [ollama|ds4] [runs] [query]

Builds the prompt the way PromptManager does (res/prompt.md with empty sections, the active
format's instructions in {COMMAND_SYSTEM}, every declaration and example rendered), puts the
home toolkit in the history as a load_toolkit round — the call rendered in the format, the
result with a small fictional house —, sends the query (default "Turn on the kitchen lamp")
through the real backend class — OllamaLLM or Ds4LLM, each declaring its static tool and
writing the server's tool_calls back as call text in the active format — and feeds the stream
to the real CommandProcessor. Per run it prints the commands extracted, the reply text and the
host's cache line (Ollama's prefix cache, ds4's `live kv cache`); at the end how many runs
called turn_on with the lamp, how many refused (no turn_on — a load_toolkit or text instead),
then the turns that follow a command round the way Keep's history shapes them (the follow-up
round with the `result:` row, then the next request) — or, when the query asked for no
command, the next request alone — each with its cache line. Exit status 1 when no run
extracted turn_on. Runs from the repository root
(cwd-relative res/); needs config.yaml and the ssh key for the host's log. ds4 binds
127.0.0.1 on the host, so the script opens an ssh tunnel to it (local port 18000) unless
DS4_BASE_URL names another address. One chain at a time on the host.
"""
import os
import re
import socket
import subprocess
import sys
import time
from pathlib import Path
from unittest.mock import Mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
os.chdir(ROOT)

import probe  # noqa: E402  (tools/command_format_probes/probe.py: host, model, cache lines)

HOUSE = ("## HOME AUTOMATION DATA\n"
         "Lights:\n- Kitchen lamp [Kitchen]: off\n- Hall lamp [Hall]: off\n- Reading lamp [Living room]: on, 40%\n")
DS4_TUNNEL_PORT = 18000


def build_messages(query: str) -> list[dict]:
    from assistant.commands import Toolkits
    from assistant.commands.formats import Signatures
    from assistant.commands.formats.Signatures import Value
    from assistant.prompt.PromptManager import PromptManager

    PromptManager._test_suite_template_path = staticmethod(lambda: "res/prompt.md")
    manager = PromptManager(test_suite=True)
    fmt = manager._command_format()
    call = fmt.render_example("load_toolkit", [Value("home", "string")], Signatures.declarations()["load_toolkit"])
    home = Toolkits.render(Toolkits.by_name("home"), manager.render_template)
    return [{"role": "system", "content": manager.prompt},
            {"role": "user", "content": "Load the home toolkit."},
            {"role": "assistant", "content": call},
            {"role": "user", "content": f"result: \n{home}\n\n{HOUSE}"},
            {"role": "user", "content": query}]


def run_once(llm, messages: list[dict]) -> tuple[list[tuple[str, list[str]]], str, str]:
    """The commands the processor extracted from one streamed reply, the reply text, and the
    raw reply without its thinking — what DialogManager stores as the assistant's history row."""
    from assistant.commands.CommandProcessor import CommandProcessor
    processor = CommandProcessor(test_suite_mode=True)
    calls: list[tuple[str, list[str]]] = []
    processor.invoke_command = lambda name, params, dm: calls.append((name, params))
    dialog = Mock(is_killed=False)  # a dialog to report an unterminated command through
    text, raw = "", ""
    try:
        for chunk in llm.create_streaming_completion(messages):
            raw += chunk
            if (out := processor.process(chunk, dialog)) is not None:
                text += out
        if (out := processor.flush_buffer(dialog)) is not None:
            text += out
        if report := processor.take_unterminated_command_report():
            text += f"\n[unterminated: {report}]"
    finally:
        processor.kill()
    return calls, text, re.sub(r"<thinking>.*?</thinking>", "", raw, flags=re.S).strip()


def follow_up(llm, messages: list[dict], raw_reply: str, had_command: bool, cache_line):
    """The turns after the first one the way Keep's history shapes them, to read the cache
    line of each: after a command, the follow-up round (the raw reply as the assistant row, the
    command's result as a `result:` row, nothing else — the model comments on the result), then
    the next request (that comment as a text row and a new query); after a text reply, the next
    request alone. On ds4 the call row must go back as the `tool_calls` entry the server gave an
    id and the result as a tool message, or the whole prompt is prefilled again."""
    second = messages + [{"role": "assistant", "content": raw_reply}]
    if had_command:
        second.append({"role": "user", "content": "result: \nKitchen lamp [Kitchen] is now on."})
        comment = _turn("follow-up round (the call row, then its result)", llm, second, cache_line)
        second.append({"role": "assistant", "content": comment or "Done."})
    second.append({"role": "user", "content": "Thanks. Now the hall lamp too."})
    _turn("next request (a text row, then a new query)", llm, second, cache_line)


def _turn(label: str, llm, messages: list[dict], cache_line) -> str:
    """One turn printed with its cache line; returns the raw reply without its thinking."""
    started = time.monotonic()
    calls, text, raw = run_once(llm, messages)
    print(f"\n== {label}: commands {calls} ({time.monotonic() - started:.1f}s)\n"
          f"   reply: {text.strip()[:300]!r}\n   cache: {cache_line()}")
    return raw


def _wait_for_port(port: int, seconds: float = 10) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        with socket.socket() as s:
            s.settimeout(0.5)
            if s.connect_ex(("127.0.0.1", port)) == 0:
                return True
        time.sleep(0.2)
    return False


def backend(name: str):
    """The LLM of the named backend, the reader of the host's cache line, and the tunnel
    process to end (None but for ds4 through the tunnel)."""
    from assistant.commands.formats.CommandFormats import CommandFormats
    tunnel = None
    if name == "ollama":
        from assistant.llm.OllamaLLM import OllamaLLM
        OllamaLLM._ollama_log_path = probe.OLLAMA_LOG  # the host's log, for the metrics lines
        llm, cache_line = OllamaLLM(model=probe.OLLAMA_MODEL, base_url=probe.OLLAMA), probe.ollama_cache_line
    elif name == "ds4":
        from assistant.llm.Ds4LLM import Ds4LLM
        base_url = os.environ.get("DS4_BASE_URL")
        if not base_url:
            tunnel = subprocess.Popen(["ssh", "-o", "BatchMode=yes", "-N", "-L", f"{DS4_TUNNEL_PORT}:127.0.0.1:8000",
                                       probe.HOST])
            if not _wait_for_port(DS4_TUNNEL_PORT):
                tunnel.kill()
                sys.exit(f"no ssh tunnel to ds4 on {probe.HOST} within 10 s")
            base_url = f"http://127.0.0.1:{DS4_TUNNEL_PORT}/v1"
        llm, cache_line = Ds4LLM(model=probe.DS4_MODEL, base_url=base_url), probe.ds4_cache_line
    else:
        sys.exit(__doc__)
    llm.initialize()
    CommandFormats.set_active(llm.command_format(), "keep_turn")
    return llm, cache_line, tunnel


def main():
    args = sys.argv[1:]
    name = args.pop(0) if args and args[0] in ("ollama", "ds4") else "ollama"
    runs = int(args.pop(0)) if args and args[0].isdigit() else 1
    query = " ".join(args) if args else "Turn on the kitchen lamp"

    from assistant.commands.formats.CommandFormats import CommandFormats
    llm, cache_line, tunnel = backend(name)
    try:
        messages = build_messages(query)
        print(f"backend {name}, format {CommandFormats.active().name}, prompt {len(messages[0]['content'])} chars, "
              f"history {sum(len(m['content']) for m in messages[1:])} chars, query {query!r}, {runs} run(s)")

        hits = refusals = 0
        first = None
        for n in range(1, runs + 1):
            started = time.monotonic()
            calls, text, raw = run_once(llm, messages)
            first = first or (raw, bool(calls))
            turned_on = [params for name_, params in calls if name_ == "turn_on"]
            if turned_on and any("kitchen" in p.lower() for params in turned_on for p in params):
                hits += 1
                verdict = "turn_on"
            else:
                refusals += 1
                verdict = "REFUSED"
            print(f"\n== run {n}: {verdict} ({time.monotonic() - started:.1f}s)\n   commands: {calls}\n"
                  f"   reply: {text.strip()[:300]!r}\n   cache: {cache_line()}")
        print(f"\n{hits}/{runs} run(s) called turn_on on the kitchen lamp, {refusals} refused or did something else")
        if first and first[0]:
            follow_up(llm, messages, first[0], first[1], cache_line)
    finally:
        if tunnel is not None:
            tunnel.kill()
    sys.exit(0 if hits else 1)


if __name__ == "__main__":
    main()
