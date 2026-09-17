"""VoiceTab: the live STT plates — bottom-anchored newest-first ordering and the column fit
that must survive a client narrower than the fixed plate geometry."""
from types import SimpleNamespace

import pytest

from interfaces.console.framework.TUIScreen import TUIScreen
from interfaces.console.tabs.VoiceTab import ACCEPTED_X, TOP_ROW, VoiceTab
from interfaces.console.Theme import LEFT_MARGIN


def phrase(text, ts, count=1, source="kitchen", confidence=0.9, engine="whisper", mic_count=1):
    return SimpleNamespace(phrase=text, timestamp=ts, count=count, source=source, confidence=confidence,
                           best_engine=engine, best_mic=source, mic_count=mic_count)


class FakeAnalyzer:
    def __init__(self, candidates=(), accepted=(), last_accepted="", scores=None):
        self._state = ({p.phrase: p for p in candidates}, {p.phrase: p for p in accepted},
                       last_accepted, scores or {})

    def get_display_state(self):
        return self._state


def make_tab(analyzer):
    return VoiceTab(SimpleNamespace(speech_analyzer=analyzer))


def rows(screen: TUIScreen) -> list[str]:
    return ["".join(c.char for c in line).rstrip() for line in screen.next_frame]


@pytest.mark.unit
class TestNewest:
    def test_keeps_the_newest_rows_oldest_first(self):
        phrases = {f"p{i}": phrase(f"p{i}", ts) for i, ts in enumerate([5, 1, 4, 2, 3])}
        assert [p.timestamp for p in VoiceTab._newest(phrases, 3)] == [3, 4, 5]

    def test_fewer_phrases_than_rows_keeps_them_all(self):
        phrases = {"a": phrase("a", 2), "b": phrase("b", 1)}
        assert [p.phrase for p in VoiceTab._newest(phrases, 10)] == ["b", "a"]


@pytest.mark.unit
class TestFit:
    def test_pads_and_truncates_with_an_ellipsis(self):
        assert VoiceTab._fit("ab", 5) == "ab   "
        assert VoiceTab._fit("abcdef", 5) == "ab..."
        assert VoiceTab._fit("abcde", 5) == "abcde"

    def test_never_overruns_a_tiny_or_missing_column(self):
        # A client narrower than the plate geometry gives a zero or negative width; that
        # must not raise (a format spec cannot take a negative width) nor spill over.
        assert VoiceTab._fit("abcdef", 0) == ""
        assert VoiceTab._fit("abcdef", -19) == ""
        for width in (1, 2, 3):
            assert len(VoiceTab._fit("abcdef", width)) == width


@pytest.mark.unit
class TestDraw:
    def test_disabled_voice_interface_says_so(self):
        screen = TUIScreen(120, 20)
        make_tab(None).draw(screen, 0)
        assert "voice interface disabled" in rows(screen)[TOP_ROW - 1]

    def test_plates_are_bottom_anchored_with_the_newest_last(self):
        screen = TUIScreen(140, 12)
        analyzer = FakeAnalyzer(candidates=[phrase("older", 1), phrase("newest", 2)],
                                accepted=[phrase("lights on", 1)], last_accepted="lights on",
                                scores={"newest": (1.5, 0.0)})
        make_tab(analyzer).draw(screen, 0)
        lines = rows(screen)
        last = TOP_ROW + 1 + (screen.height - (TOP_ROW + 1) - 1) - 1  # the plates' bottom row, 1-based
        newest, older = lines[last - 1], lines[last - 2]
        x0 = LEFT_MARGIN - 1  # screen columns are 1-based
        assert newest[x0:].startswith("   1.5 ( 1v)  90% ■■■■■■■■■")
        assert "[kitchen·W] newest" in newest
        assert "older" in older and "lights on" not in older
        assert newest[x0 + ACCEPTED_X:].strip() == "lights on"

    def test_a_narrow_client_still_draws_the_plates(self):
        # 80 columns leaves the accepted plate no room at all; the tab must keep rendering
        # the candidates rather than raise on every frame.
        screen = TUIScreen(80, 12)
        analyzer = FakeAnalyzer(candidates=[phrase("hello there", 1)], accepted=[phrase("hello there", 1)])
        make_tab(analyzer).draw(screen, 0)
        assert any("hello there" in line for line in rows(screen))
