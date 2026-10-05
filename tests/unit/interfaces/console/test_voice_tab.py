"""VoiceTab: the live STT board — the candidates and the accepted phrases as tables that keep
the newest phrase in view, whatever the client's size."""
from types import SimpleNamespace

import pytest

from interfaces.console import Theme
from interfaces.console.framework.TUIScreen import TUIScreen
from interfaces.console.tabs.VoiceTab import VoiceTab
from interfaces.console.widgets import Board


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


def draw(analyzer, width=160, height=30) -> list[str]:
    screen = TUIScreen(width, height)
    make_tab(analyzer).draw(screen, 0)
    return rows(screen)


def row_with(lines: list[str], text: str) -> int:
    """The last row showing `text`: its table row, under any summary tile that repeats it."""
    return max(i for i, line in enumerate(lines) if text in line)


@pytest.mark.unit
class TestMic:
    def test_names_the_best_mic_the_extra_mics_and_the_engine(self):
        assert VoiceTab._mic(phrase("x", 1, source="kitchen", engine="scribe", mic_count=3)) == "kitchen +2·S"

    def test_a_single_mic_without_an_engine_tag_is_just_its_name(self):
        assert VoiceTab._mic(phrase("x", 1, source="hallway", engine="")) == "hallway"

    def test_a_long_mic_name_is_cut(self):
        assert VoiceTab._mic(phrase("x", 1, source="living-room-ceiling", engine="whisper")) == "living-room-·W"

    def test_a_phrase_without_a_source_names_no_mic(self):
        assert "W" not in VoiceTab._mic(phrase("x", 1, source=""))


@pytest.mark.unit
class TestDraw:
    def test_candidates_are_listed_oldest_first_with_their_numbers(self):
        analyzer = FakeAnalyzer(candidates=[phrase("newest", 2, count=3), phrase("older", 1)],
                                scores={"newest": (1.5, 0.0)})
        lines = draw(analyzer)
        assert row_with(lines, "older") < row_with(lines, "newest")
        newest = lines[row_with(lines, "newest")]
        assert "1.5" in newest and "90%" in newest and "kitchen·W" in newest

    def test_the_accepted_phrases_sit_beside_the_candidates_on_a_wide_screen(self):
        analyzer = FakeAnalyzer(candidates=[phrase("turn the lights on", 1)],
                                accepted=[phrase("lights on", 1)], last_accepted="lights on")
        lines = draw(analyzer, width=Board.SIDE_BY_SIDE_MIN)
        assert row_with(lines, "turn the lights on") == row_with(lines, "  lights on")

    def test_the_accepted_phrases_sit_under_the_candidates_on_a_narrow_screen(self):
        analyzer = FakeAnalyzer(candidates=[phrase("turn the lights on", 1)],
                                accepted=[phrase("lights on", 1)], last_accepted="lights on")
        lines = draw(analyzer, width=100)
        assert row_with(lines, "turn the lights on") < row_with(lines, "  lights on")

    def test_more_candidates_than_rows_keeps_the_newest_in_view(self):
        analyzer = FakeAnalyzer(candidates=[phrase(f"phrase number {i:02d}", i) for i in range(60)])
        text = "\n".join(draw(analyzer))
        assert "phrase number 59" in text and "phrase number 00" not in text

    def test_the_last_accepted_candidate_stands_out(self):
        analyzer = FakeAnalyzer(candidates=[phrase("lights on", 1), phrase("lights off", 2)],
                                last_accepted="lights on")
        screen = TUIScreen(100, 30)
        make_tab(analyzer).draw(screen, 0)
        lines = rows(screen)

        def cell(text):
            y = row_with(lines, text)
            return screen.next_frame[y][lines[y].index(text)]

        assert cell("lights on").style.bold and not cell("lights off").style.bold
        assert cell("lights on").style.fg == Theme.COLOR_BRIGHT

    def test_summary_tiles_count_the_candidates_and_name_the_best_scored(self):
        analyzer = FakeAnalyzer(candidates=[phrase("alpha", 1, count=2), phrase("bravo", 2, count=3, source="hallway")],
                                scores={"alpha": (0.4, 0.0), "bravo": (2.5, 0.0)})
        summary = "\n".join(draw(analyzer)[:Board.SUMMARY_H + 6])
        assert "5 votes" in summary and "2 mics" in summary
        assert "2.5" in summary and summary.count("bravo") == 1 and "alpha" not in summary

    @pytest.mark.parametrize("width,height", [(80, 12), (40, 8), (20, 7), (160, 9)])
    def test_a_small_client_still_draws(self, width, height):
        # A client smaller than the board has room for must keep rendering, not raise on every frame.
        analyzer = FakeAnalyzer(candidates=[phrase("hello there", 1)], accepted=[phrase("hello there", 1)],
                                last_accepted="hello there", scores={"hello there": (1.0, 0.0)})
        draw(analyzer, width, height)

    def test_a_narrow_client_shows_the_candidates(self):
        analyzer = FakeAnalyzer(candidates=[phrase("hello there", 1)], accepted=[phrase("hello there", 1)])
        assert any("hello there" in line for line in draw(analyzer, 80, 16))

    def test_without_the_voice_interface_the_tab_says_so(self):
        lines = draw(None)
        assert any("interfaces.voice.enabled" in line for line in lines)
