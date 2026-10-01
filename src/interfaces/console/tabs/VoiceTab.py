from typing import TYPE_CHECKING
from .TabView import TabView, SUB_CONTENT_TOP
from interfaces.console import Theme
from interfaces.console.Theme import LEFT_MARGIN
from interfaces.console.framework.TUIScreen import TUIScreen
from interfaces.console.framework.TextWidth import fit, ljust

if TYPE_CHECKING:
    from interfaces.console.TUIFloxyConsole import TUIFloxyConsole
    from interfaces.voice.microphones.speech_analysis.SpeechAnalyzer import Phrase

TOP_ROW = SUB_CONTENT_TOP

# Column geometry of the three plates, relative to the console's LEFT_MARGIN
SCORE_WIDTH = 32       # score / votes / confidence / bar
CANDIDATE_X = 34       # the candidate phrases with their [mic +extra·engine] tag
CANDIDATE_WIDTH = 55
ACCEPTED_X = 91        # the phrases the analyzer accepted, to the right edge


class VoiceTab(TabView):
    """Developer ▸ Voice: the live STT view — the analyzer's candidate phrases with their
    vote scores on the left, the accepted phrases on the right. Live while an utterance is in
    flight, otherwise the last utterance's snapshot."""

    def __init__(self, console: 'TUIFloxyConsole'):
        self.console = console

    def draw(self, screen: TUIScreen, frame: int):
        x0 = LEFT_MARGIN

        if self.console.speech_analyzer is None:
            screen.reset_color()
            screen.set_color(Theme.COLOR_GREEN)
            screen.write_at(x0, TOP_ROW, "  voice interface disabled (interfaces.voice.enabled: false)")
            return

        screen.reset_color()
        screen.set_color(Theme.COLOR_DIM)
        screen.write_at(x0, TOP_ROW, f"{' SCORE  VOTES CONF':<{SCORE_WIDTH}}")
        screen.write_at(x0 + CANDIDATE_X, TOP_ROW, "  CANDIDATES  [mic +extra mics ·W=Whisper ·S=Scribe]")
        screen.write_at(x0 + ACCEPTED_X, TOP_ROW, "  ACCEPTED")

        top = TOP_ROW + 1
        rows = screen.height - top - 1
        if rows <= 0:
            return

        # The analyzer owns the display state: live phrases while an utterance is in
        # flight, otherwise its last-utterance snapshot (preserved by clean(), so it stays
        # correct even when this tab wasn't visible during the utterance).
        phrases_to_show, accepted_to_show, last_accepted, phrase_scores = \
            self.console.speech_analyzer.get_display_state()
        self._draw_accepted(screen, top, rows, accepted_to_show, last_accepted)
        self._draw_candidates(screen, top, rows, phrases_to_show, last_accepted, phrase_scores)

    def handle_input(self, key: str) -> bool:
        return False

    @staticmethod
    def _newest(phrases: dict, rows: int) -> list:
        """The newest `rows` phrases, oldest first, so they sit bottom-anchored."""
        return sorted(phrases.values(), key=lambda pr: pr.timestamp)[-rows:]

    def _draw_accepted(self, screen: TUIScreen, top: int, rows: int, accepted: dict, last_accepted: str):
        x = LEFT_MARGIN + ACCEPTED_X
        width = screen.width - x - 3
        phrases = self._newest(accepted, rows)
        y_start = top + rows - len(phrases)

        screen.reset_color()
        screen.set_bg_color(Theme.COLOR_GREEN_DIM)
        for empty_y in range(top, y_start):
            screen.write_at(x, empty_y, " " * width)

        y = y_start
        for p in phrases:
            p: 'Phrase'
            f = min(1.0, p.count / 15.0 + 0.25)
            screen.reset_color()
            screen.set_bg_color(Theme.COLOR_GREEN_DIM)
            if last_accepted == p.phrase:
                screen.set_color(Theme.COLOR_GREEN)
                screen.bold()
            else:
                c = Theme.COLOR_GREEN
                screen.set_color_rgb(c.r * f, c.g * f, c.b * f)
            screen.write_at(x, y, self._fit(f"  {p.phrase}", width))
            y += 1

    def _draw_candidates(self, screen: TUIScreen, top: int, rows: int, candidates: dict,
                         last_accepted: str, phrase_scores: dict):
        x = LEFT_MARGIN
        phrases = self._newest(candidates, rows)
        y_start = top + rows - len(phrases)

        screen.reset_color()
        screen.set_bg_color(Theme.COLOR_YELLOW_DIM)
        for empty_y in range(top, y_start):
            screen.write_at(x, empty_y, " " * SCORE_WIDTH)
            screen.write_at(x + CANDIDATE_X, empty_y, " " * CANDIDATE_WIDTH)

        y = y_start
        for p in phrases:
            p: 'Phrase'
            f = min(1.0, p.count / 15.0 + 0.25)
            score, _ = phrase_scores.get(p.phrase, (0.0, 0.0))
            bar = '■' * min(10, int(score * 6))

            screen.reset_color()
            screen.set_color(Theme.mix(Theme.COLOR_YELLOW_DIM, Theme.COLOR_YELLOW, 0.5))
            screen.set_bg_color(Theme.COLOR_YELLOW_DIM)
            screen.write_at(x, y, f"{score:6.1f} ({p.count:2.0f}v) {p.confidence * 100:3.0f}% {bar:<10}")

            screen.reset_color()
            screen.set_bg_color(Theme.COLOR_YELLOW_DIM)
            if last_accepted == p.phrase:
                screen.set_color(Theme.COLOR_YELLOW)
                screen.bold()
            else:
                c = Theme.COLOR_YELLOW
                screen.set_color_rgb(c.r * f, c.g * f, c.b * f)

            if p.source:
                extra_mics = p.mic_count - 1
                extra = f" +{extra_mics}" if extra_mics > 0 else ""
                # Compact STT-engine tag for the winning vote: W=Whisper, S=Scribe
                # (empty for votes with no engine tag, e.g. phone outposts).
                engine_tag = f"·{p.best_engine[0].upper()}" if p.best_engine else ""
                mic_prefix = f"[{p.best_mic[:12]}{extra}{engine_tag}] "
            else:
                mic_prefix = ""
            screen.write_at(x + CANDIDATE_X, y, self._fit(f"  {mic_prefix}{p.phrase}", CANDIDATE_WIDTH))
            y += 1

    @staticmethod
    def _fit(s: str, width: int) -> str:
        """`s` truncated with an ellipsis or padded to exactly `width` columns; "" when the
        plate has no room at all (a client narrower than the fixed column geometry)."""
        if width <= 0:
            return ""
        return ljust(fit(s, width), width)
