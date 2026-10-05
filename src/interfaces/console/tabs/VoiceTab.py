from typing import TYPE_CHECKING, Dict, List, Tuple

from .TabView import TabView, SUB_CONTENT_TOP
from interfaces.console import Theme
from interfaces.console.framework.TUIScreen import TUIScreen, Color
from interfaces.console.widgets import Board, Tile
from interfaces.console.widgets.Board import Meter, SummaryTile, Table

if TYPE_CHECKING:
    from interfaces.console.TUIFloxyConsole import TUIFloxyConsole
    from interfaces.voice.microphones.speech_analysis.SpeechAnalyzer import Phrase

PENDING = "–"
DISABLED = "voice interface disabled (interfaces.voice.enabled: false)"
HINTS = "Mic: the best microphone  +n more microphones  ·W Whisper  ·S Scribe"

SCORE_METER_W = 10
SCORE_FULL = 10 / 6            # the score that fills the meter
SCORE_W = SCORE_METER_W + 5
VOTES_W, CONFIDENCE_W, MIC_W = 5, 4, 18
MIC_NAME_MAX = 12
FULL_VOTES = 15                # a phrase with this many votes is drawn in full text colour
MIN_TABLE_H = Tile.CARD_CHROME + 2


class VoiceTab(TabView):
    """Developer ▸ Voice: the live STT view — the analyzer's candidate phrases with their
    vote scores, and the phrases it accepted in a tile beside them. Live while an utterance
    is in flight, otherwise the last utterance's snapshot. The newest phrase is the last row."""

    def __init__(self, console: 'TUIFloxyConsole'):
        self.console = console
        self._cards: List[Tile.Card] = []

    def draw(self, screen: TUIScreen, frame: int):
        self._cards = []
        available = screen.width - Board.LEFT - Board.RIGHT_MARGIN
        bottom = screen.height - 1                # the last row is the key hints'
        y = SUB_CONTENT_TOP

        if self.console.speech_analyzer is None:
            Board.draw_notice(screen, self._cards, Board.LEFT, y, available, bottom - y, "Voice", DISABLED)
            Tile.tint_cards(screen, self._cards)
            return

        # The analyzer owns the display state: live phrases while an utterance is in
        # flight, otherwise its last-utterance snapshot (preserved by clean(), so it stays
        # correct even when this tab wasn't visible during the utterance).
        candidates, accepted, last_accepted, scores = self.console.speech_analyzer.get_display_state()
        candidates, accepted = self._ordered(candidates), self._ordered(accepted)

        side_by_side = screen.width >= Board.SIDE_BY_SIDE_MIN
        tables_h = MIN_TABLE_H if side_by_side else 2 * MIN_TABLE_H + 1
        if bottom - y - Board.SUMMARY_H - 1 >= tables_h:
            Board.draw_summary(screen, self._cards, y, available,
                               [self._candidates_tile(candidates), self._score_tile(candidates, scores),
                                self._accepted_tile(accepted, last_accepted)])
            y += Board.SUMMARY_H + 1
        height = bottom - y

        candidates_table = self._candidates_table(candidates, accepted, last_accepted, scores)
        accepted_table = self._accepted_table(accepted, last_accepted)
        if side_by_side:
            table_w, side_w = Board.split(available)
            self._draw_newest(screen, Board.LEFT, y, table_w, height, candidates_table)
            self._draw_newest(screen, Board.LEFT + table_w + Board.GAP, y, side_w, height, accepted_table)
        else:
            accepted_h = min(max(1, len(accepted)) + Tile.CARD_CHROME + 1, max(MIN_TABLE_H, height // 2))
            candidates_h = height - accepted_h - 1
            self._draw_newest(screen, Board.LEFT, y, available, candidates_h, candidates_table)
            self._draw_newest(screen, Board.LEFT, y + candidates_h + 1, available, accepted_h, accepted_table)
        Tile.tint_cards(screen, self._cards)
        Board.draw_hints(screen, HINTS)

    def handle_input(self, key: str) -> bool:
        return False

    def _draw_newest(self, screen: TUIScreen, x: int, y: int, width: int, height: int, table: Table):
        """`table` scrolled to its end: the newest phrases are the ones that stay in view."""
        Board.draw_table(screen, self._cards, x, y, width, height, table, top=len(table.rows))

    @staticmethod
    def _ordered(phrases: dict) -> list:
        """The phrases oldest first."""
        return sorted(phrases.values(), key=lambda p: p.timestamp)

    @staticmethod
    def _text_color(phrase: 'Phrase') -> Color:
        """A phrase's colour by its votes: one heard once is dim, a well-voted one full text."""
        return Theme.mix(Theme.COLOR_DIM, Theme.COLOR_TEXT, min(1.0, phrase.count / FULL_VOTES + 0.25))

    @staticmethod
    def _mic(phrase: 'Phrase') -> str:
        """The microphone behind the winning vote, how many more heard the phrase, and the
        STT engine's initial (none for a vote without an engine tag, e.g. a phone outpost)."""
        if not phrase.source:
            return PENDING
        extra = f" +{phrase.mic_count - 1}" if phrase.mic_count > 1 else ""
        engine = f"·{phrase.best_engine[0].upper()}" if phrase.best_engine else ""
        return f"{phrase.best_mic[:MIC_NAME_MAX]}{extra}{engine}"

    # --- Summary tiles ---

    @staticmethod
    def _candidates_tile(candidates: List['Phrase']) -> SummaryTile:
        if not candidates:
            return "Candidates", PENDING, Theme.COLOR_DIM, [("nothing heard yet", Theme.COLOR_DIM)]
        votes = sum(p.count for p in candidates)
        mics = len({p.best_mic for p in candidates if p.source})
        return ("Candidates", str(len(candidates)), Theme.COLOR_BRIGHT,
                [(f"{votes:.0f} votes", Theme.COLOR_LABEL), (f"{mics} mic{'' if mics == 1 else 's'}", Theme.COLOR_LABEL)])

    @staticmethod
    def _score_tile(candidates: List['Phrase'], scores: Dict[str, Tuple[float, float]]) -> SummaryTile:
        scored = [(scores[p.phrase][0], p.phrase) for p in candidates if p.phrase in scores]
        if not scored:
            return "Best score", PENDING, Theme.COLOR_DIM, [("nothing scored yet", Theme.COLOR_DIM)]
        score, phrase = max(scored)
        return "Best score", f"{score:.1f}", Theme.COLOR_YELLOW, [(phrase, Theme.COLOR_TEXT)]

    @staticmethod
    def _accepted_tile(accepted: List['Phrase'], last_accepted: str) -> SummaryTile:
        if not accepted:
            return "Accepted", PENDING, Theme.COLOR_DIM, [("nothing accepted yet", Theme.COLOR_DIM)]
        return "Accepted", str(len(accepted)), Theme.COLOR_GREEN, [(last_accepted or PENDING, Theme.COLOR_GREEN)]

    # --- Tables ---

    def _candidates_table(self, candidates: List['Phrase'], accepted: List['Phrase'], last_accepted: str,
                          scores: Dict[str, Tuple[float, float]]) -> Table:
        accepted_phrases = {p.phrase for p in accepted}
        rows = []
        for p in candidates:
            score, _ = scores.get(p.phrase, (0.0, 0.0))
            is_last = p.phrase == last_accepted
            is_accepted = is_last or p.phrase in accepted_phrases
            accent = Theme.COLOR_GREEN if is_accepted else Theme.COLOR_YELLOW
            rows.append((accent, [
                Meter(score / SCORE_FULL, accent, SCORE_METER_W, f"{score:.1f}", Theme.COLOR_TEXT),
                (f"{p.count:.0f}", Theme.COLOR_LABEL),
                (f"{p.confidence * 100:.0f}%", Theme.COLOR_LABEL),
                (self._mic(p), Theme.COLOR_TEAL if p.source else Theme.COLOR_FAINT),
                (p.phrase, Theme.COLOR_BRIGHT, True) if is_last else (p.phrase, self._text_color(p))]))
        return Table("Candidates",
                     [("Score", SCORE_W), ("Votes", VOTES_W), ("Conf", CONFIDENCE_W), ("Mic", MIC_W), ("Phrase", None)],
                     rows, "nothing heard yet")

    def _accepted_table(self, accepted: List['Phrase'], last_accepted: str) -> Table:
        rows = []
        for p in accepted:
            is_last = p.phrase == last_accepted
            rows.append((Theme.COLOR_GREEN if is_last else Theme.COLOR_FAINT, [
                (f"{p.count:.0f}", Theme.COLOR_LABEL),
                (p.phrase, Theme.COLOR_GREEN, True) if is_last else (p.phrase, self._text_color(p))]))
        return Table("Accepted", [("Votes", VOTES_W), ("Phrase", None)], rows, "nothing accepted yet",
                     value_color=Theme.COLOR_GREEN)
