"""Offline checks against the EXACT stub-handling methods in DialogManager.py.

No Keep import, cloud credentials, LLM calls, third-party dependencies, or production writes.
This is deliberately not the full Keep pytest suite or an end-to-end Michelle test.
"""
import ast
import re
import textwrap
import time
import types
import unittest
from pathlib import Path
from typing import Generator

SOURCE = Path("src/assistant/DialogManager.py").read_text(encoding="utf-8")
TREE = ast.parse(SOURCE)
ORIGINAL = next(n for n in TREE.body if isinstance(n, ast.ClassDef) and n.name == "DialogManager")
METHODS = {"_could_become_stub", "_without_stub", "_cut_stub_from_raw", "_stream_with_retry_and_fallback", "process_llm_stream"}
FIELDS = {"STUB_LINE", "STUB_HOLD_LIMIT"}
extracted = []
for node in ORIGINAL.body:
    if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id in FIELDS for t in node.targets):
        extracted.append(textwrap.dedent(ast.get_source_segment(SOURCE, node)))
    if isinstance(node, ast.FunctionDef) and node.name in METHODS:
        extracted.append(textwrap.dedent(ast.get_source_segment(SOURCE, node)))
assert len(extracted) == len(METHODS) + len(FIELDS), "Did not find each original method and field"
class_source = "class Guard:\n" + "\n\n".join(textwrap.indent(block, "    ") for block in extracted)

class LLMRefusalError(Exception):
    pass

class APIConnectionError(Exception):
    pass

class GeminiServerError(Exception):
    pass

module_ns = dict(
    re=re, note=lambda *_: None, trace=lambda *_: None, info=lambda *_: None,
    error=lambda *_: None, Generator=Generator,
    LLMRefusalError=LLMRefusalError, APIConnectionError=APIConnectionError,
    GeminiServerError=GeminiServerError,
    httpx=types.SimpleNamespace(TransportError=OSError),
    time=time,
    RequestKilledException=RuntimeError,
    strip_backticks=lambda content, before, in_span: (content, in_span),
    observers=lambda: types.SimpleNamespace(
        on_first_token=lambda *_: None,
        on_reply_started=lambda *_: None,
        on_tokens=lambda *_: None),
    TTFTMonitor=types.SimpleNamespace(get_instance=lambda: types.SimpleNamespace(record=lambda *_: None)),
)
exec(compile(class_source, "DialogManager.py:stub-extract", "exec"), module_ns)
Guard = module_ns["Guard"]


class StubGuard(Guard):
    def __init__(self, raw=""):
        self.llm_response_raw = raw
        self.stub_dropped = ""


class FakeAI:
    def __init__(self, responses):
        self.responses = responses
        self.calls = 0

    def create_streaming_completion(self, *_args, **_kwargs):
        result = self.responses[self.calls]
        self.calls += 1
        return result

    def name(self):
        return "offline-fake-model"


class RetryGuard(StubGuard):
    def __init__(self, responses, dispatched=0):
        super().__init__("")
        self.ai = FakeAI(responses)
        self.llm_response = ""
        self.command_processor = types.SimpleNamespace(
            commands_dispatched=dispatched, reset_parse_state=lambda: None,
        )
        self.thinking = False
        self.follow_up = False
        self.notices = []

    def process_llm_stream(self, response, *_args):
        self.stub_dropped = "_toolkit_placeholder"
        self.llm_response_raw += response
        if False:
            yield ""

    def _surface_notice(self, notice):
        self.notices.append(notice)


class StreamingGuard(StubGuard):
    """Exercise the extracted ORIGINAL process_llm_stream path with an offline fake transport."""
    def __init__(self):
        super().__init__("")
        self.llm_response = ""
        self.is_killed = False
        self.ai = types.SimpleNamespace(name=lambda: "fake-offline-model", model="offline-model")
        self.command_processor = types.SimpleNamespace(
            commands_dispatched=0, reply_cut=False, process=lambda chunk, dm: chunk,
            flush_buffer=lambda dm: None)
        self.published = []
        self.web_request_handler = types.SimpleNamespace(
            ctx=types.SimpleNamespace(text_only=True),
            publish_text_event=lambda _id, payload: self.published.append(payload["text"]))
        self.drain_events = lambda: []




class OriginalCodeTests(unittest.TestCase):
    def test_sixty_repeated_placeholders_then_real_text(self):
        sample = "_toolkit_placeholder\n" * 60 + "Source ID: checkpoint."
        g = StubGuard(sample)
        self.assertEqual(g._without_stub(sample, complete=False), "Source ID: checkpoint.")
        self.assertNotIn("_toolkit_placeholder", g.llm_response_raw)
        self.assertEqual(g.stub_dropped, "_toolkit_placeholder")

    def test_hundred_placeholder_lines_are_held_until_complete(self):
        sample = "_toolkit_placeholder\n" * 100
        g = StubGuard(sample)
        self.assertIsNone(g._without_stub(sample, complete=False))
        self.assertEqual(g.stub_dropped, "")
        self.assertEqual(g._without_stub(sample, complete=True), "")
        self.assertEqual(g.llm_response_raw.strip(), "")

    def test_chunk_boundary_preserves_real_reply(self):
        chunks = ["_tool", "kit_placeholder\n", "_toolkit_placeholder\n", "Confirmed source."]
        g = StubGuard()
        held = ""
        shown = []
        for chunk in chunks:
            held += chunk
            g.llm_response_raw += chunk
            safe = g._without_stub(held, complete=False)
            if safe is not None:
                shown.append(safe)
                held = ""
        self.assertEqual("".join(shown), "Confirmed source.")
        self.assertNotIn("_toolkit_placeholder", g.llm_response_raw)

    def test_multiple_stub_tokens_disappear(self):
        sample = "_url_placeholder\n_toolkit_placeholder\nActual answer."
        g = StubGuard(sample)
        self.assertEqual(g._without_stub(sample, complete=False), "Actual answer.")
        self.assertNotIn("placeholder", g.llm_response_raw)

    def test_normal_text_with_placeholder_word_is_not_filtered(self):
        sample = "The form's label is _toolkit_placeholder, not a command."
        g = StubGuard(sample)
        self.assertEqual(g._without_stub(sample, complete=True), sample)
        self.assertEqual(g.llm_response_raw, sample)

    def test_real_stream_pipeline_suppresses_leading_stubs_in_ui_and_history(self):
        g = StreamingGuard()
        result = "".join(g.process_llm_stream(
            ["_toolkit_placeholder\n" * 60, "Source evidence verified."], time.time(), False))
        self.assertEqual(result, "Source evidence verified.")
        self.assertEqual("".join(g.published), result)
        self.assertEqual(g.llm_response, result)
        self.assertNotIn("_toolkit_placeholder", g.llm_response_raw)

    def test_real_stream_pipeline_repeated_stub_only_turn_is_blank_and_retryable(self):
        g = StreamingGuard()
        result = "".join(g.process_llm_stream(["_toolkit_placeholder\n" * 100], time.time(), False))
        self.assertEqual(result, "")
        self.assertEqual(g.published, [])
        self.assertEqual(g.llm_response, "")
        self.assertEqual(g.stub_dropped, "_toolkit_placeholder")
        self.assertNotIn("_toolkit_placeholder", g.llm_response_raw)

    def test_retry_is_bounded_and_reports_readable_failure(self):
        g = RetryGuard(["_toolkit_placeholder\n" * 100] * 2)
        output = "".join(g._stream_with_retry_and_fallback([], 1.0, True, ""))
        self.assertEqual(g.ai.calls, 2)
        self.assertIn("couldn't produce a readable response", output)
        self.assertNotIn("_toolkit_placeholder", output)
        self.assertEqual(g.notices, [output])

    def test_dispatched_command_does_not_regenerate_or_claim_failure(self):
        g = RetryGuard(["_toolkit_placeholder\n" * 100], dispatched=1)
        output = "".join(g._stream_with_retry_and_fallback([], 1.0, True, ""))
        self.assertEqual(g.ai.calls, 1)
        self.assertEqual(output, "")
        self.assertEqual(g.notices, [])


if __name__ == "__main__":
    unittest.main()
