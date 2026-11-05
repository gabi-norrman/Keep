import pytest

from assistant.AssistantEvents import AssistantObserver, observers
from assistant.llm.CacheBreakTracker import CHANGE_LINES, CacheBreak, CacheBreakTracker
from assistant.prompt.PromptManager import PromptManager


class Recorder(AssistantObserver):
    def __init__(self):
        self.reports = []

    def on_cache_break(self, report):
        self.reports.append(report)


@pytest.fixture
def recorder():
    observer = Recorder()
    observers().subscribe(observer)
    yield observer
    observers().unsubscribe(observer)


@pytest.fixture(autouse=True)
def fresh(monkeypatch):
    monkeypatch.setattr(PromptManager, "last_prompt_file", None)
    CacheBreakTracker._last_requester.clear()


def prompt(lines: int, changed: dict[int, str] = {}) -> list[dict]:
    """One system message of `lines` numbered lines, `changed` replacing some (1-based)."""
    body = [changed.get(n, f"line {n} of the prompt") for n in range(1, lines + 1)]
    return [{"role": "system", "content": "\n".join(body)}]


@pytest.mark.unit
class TestCacheBreakTracker:
    def test_first_request_has_nothing_to_compare(self, recorder):
        assert CacheBreakTracker("Claude").record(prompt(10), cached_tokens=0, prompt_tokens=100) is None
        assert recorder.reports == []

    def test_a_follow_up_that_reads_back_what_was_sent_before_is_no_break(self, recorder):
        tracker = CacheBreakTracker("Claude")
        tracker.record(prompt(50), cached_tokens=0, prompt_tokens=1000)
        # The new round adds 300 uncached tokens: 77 % of its own prompt but all of the previous one.
        assert tracker.record(prompt(60), cached_tokens=1000, prompt_tokens=1300) is None
        assert recorder.reports == []

    def test_a_trimmed_prompt_is_measured_against_its_own_size(self, recorder):
        tracker = CacheBreakTracker("Claude")
        tracker.record(prompt(100), cached_tokens=0, prompt_tokens=1000)
        assert tracker.record(prompt(50), cached_tokens=480, prompt_tokens=500) is None

    def test_a_change_before_the_read_stopped_is_the_cause_and_shown_as_a_diff(self, recorder):
        tracker = CacheBreakTracker("Claude")
        tracker.record(prompt(100), cached_tokens=0, prompt_tokens=1000)
        PromptManager.last_prompt_file = "log/prompts/abc.md"
        # The system block's header takes two lines, so prompt line 30 is line 32 of the dump.
        report = tracker.record(prompt(100, {30: "the car is parked"}), cached_tokens=350, prompt_tokens=1000)
        assert isinstance(report, CacheBreak)
        assert recorder.reports == [report]
        assert report.first_diff_line == 32
        assert report.read_line == 36
        assert report.cause == ("The cache covered the prompt to about line 36 of 102; the prompt changed at "
                                "line 32, so the change broke it.")
        assert report.dump == "log/prompts/abc.md"
        assert report.diff.split("\n") == ["  line 29 of the prompt", "-line 30 of the prompt",
                                            "+the car is parked", "  line 31 of the prompt"]
        assert report.pct == 35

    def test_a_prompt_nothing_was_read_back_from_says_so(self, recorder):
        tracker = CacheBreakTracker("DS4")
        tracker.record(prompt(100), cached_tokens=0, prompt_tokens=1000)
        report = tracker.record(prompt(100, {7: "changed"}), cached_tokens=0, prompt_tokens=1000)
        assert report.cause == ("Nothing was read back; the prompt changed at line 9 of 102, so the cache could "
                                "not reach past it.")

    def test_an_unchanged_prefix_blames_the_other_user_of_a_single_context_server(self, recorder):
        chat = CacheBreakTracker("DS4", scope="ds4 http://127.0.0.1:8000/v1")
        check = CacheBreakTracker("DS4 (check)", scope="ds4 http://127.0.0.1:8000/v1")
        chat.record(prompt(100), cached_tokens=0, prompt_tokens=1000)
        check.record(prompt(5, {1: "a different prompt"}), cached_tokens=0, prompt_tokens=50)
        report = chat.record(prompt(101), cached_tokens=100, prompt_tokens=1010)
        assert report.first_diff_line is None
        assert report.diff == ""
        assert "DS4 (check) used the server in between" in report.cause
        assert "did not break it" in report.cause

    def test_an_unchanged_prefix_with_nobody_else_falls_back_to_the_backend_note_or_expiry(self, recorder):
        tracker = CacheBreakTracker("Claude")
        tracker.record(prompt(100), cached_tokens=0, prompt_tokens=1000)
        report = tracker.record(prompt(101), cached_tokens=100, prompt_tokens=1010, note_text="the TTL ran out")
        assert "the TTL ran out" in report.cause
        tracker.record(prompt(102), cached_tokens=0, prompt_tokens=1000)
        report = tracker.record(prompt(103), cached_tokens=100, prompt_tokens=1010)
        assert "expired or was evicted" in report.cause

    def test_a_change_after_the_read_stopped_is_not_blamed(self, recorder):
        tracker = CacheBreakTracker("Claude")
        tracker.record(prompt(100), cached_tokens=0, prompt_tokens=1000)
        report = tracker.record(prompt(100, {90: "late change"}), cached_tokens=100, prompt_tokens=1000)
        assert report.first_diff_line == 92
        assert report.diff == ""
        assert "did not break it" in report.cause

    def test_a_long_change_is_cut_and_counted(self, recorder):
        tracker = CacheBreakTracker("Claude")
        tracker.record(prompt(100), cached_tokens=0, prompt_tokens=1000)
        changed = {n: f"new line {n}" for n in range(10, 30)}
        report = tracker.record(prompt(100, changed), cached_tokens=200, prompt_tokens=1000)
        lines = report.diff.split("\n")
        assert sum(line.startswith("-") for line in lines) == CHANGE_LINES
        assert sum(line.startswith("+") for line in lines) == CHANGE_LINES
        assert lines[-1].strip() == "… (20 removed, 20 added lines in all)"

    def test_a_report_from_a_broken_observer_still_returns(self, recorder):
        class Exploding(AssistantObserver):
            def on_cache_break(self, report):
                raise RuntimeError("boom")
        observers().subscribe(Exploding())
        try:
            tracker = CacheBreakTracker("Claude")
            tracker.record(prompt(10), cached_tokens=0, prompt_tokens=100)
            assert tracker.record(prompt(10, {2: "x"}), cached_tokens=10, prompt_tokens=100) is not None
        finally:
            observers().unsubscribe(next(o for o in observers().observers if isinstance(o, Exploding)))


@pytest.mark.unit
class TestFlatten:
    def test_lines_are_the_prompt_dump_lines_so_the_numbers_can_be_looked_up_there(self):
        messages = [{"role": "system", "content": "rules\nmore rules"},
                    {"role": "user", "content": [{"type": "text", "text": "hi\nthere"}]},
                    {"role": "assistant", "content": "hello"}]
        assert CacheBreakTracker.flatten(messages) == PromptManager._format_messages(messages).split("\n")

    def test_an_image_block_is_one_placeholder_line_like_the_dump(self):
        messages = [{"role": "user", "content": [{"type": "image", "source": {"data": "..."}},
                                                 {"type": "text", "text": "what is this"}]}]
        assert CacheBreakTracker.flatten(messages) == ["# [USER]", "", "[IMAGE]", "what is this"]


@pytest.mark.unit
class TestCacheBreakText:
    def test_the_row_carries_the_summary_and_the_diff_under_the_header(self):
        report = CacheBreak(model="DS4", cached_tokens=100, prompt_tokens=1000, previous_tokens=990, lines=50,
                            read_line=5, first_diff_line=4, cause="The change broke the cache.",
                            diff="-old\n+new", seconds_since_previous=200, dump="log/prompts/x.md")
        text = report.text()
        assert text.splitlines()[0].startswith("100/1000 tokens read back (10%)")
        assert "3 min before" in text
        assert "log/prompts/x.md" not in text   # the dump is for the log line, the header names the model
        assert text.endswith("-old\n+new")


@pytest.mark.unit
class TestReportCacheUsageGuard:
    def test_a_failing_tracker_never_fails_the_request(self, monkeypatch):
        """The report runs inside the request, before the first token: a bug in it is logged, not raised."""
        from assistant.llm.AbstractLLM import AbstractLLM

        class Backend(AbstractLLM):
            def __init__(self):
                pass

            def name(self):
                return "Claude"

        Backend.__abstractmethods__ = frozenset()   # the request methods play no part here

        monkeypatch.setattr(CacheBreakTracker, "record", lambda *a, **k: (_ for _ in ()).throw(ValueError("boom")))
        backend = Backend()
        assert backend.report_cache_usage(prompt(3), cached_tokens=0, prompt_tokens=30) is None
        assert backend.report_cache_usage(prompt(3), cached_tokens=0, prompt_tokens=30) is None
