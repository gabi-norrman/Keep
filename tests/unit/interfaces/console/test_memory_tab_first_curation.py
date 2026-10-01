"""While the first-run pipeline curates the first bank, the Memory tab puts a two-row orange
heads-up band above the text — the label centred on one row, the sentence on the next, what it
shows is a work in progress — and takes it down when the curation ends."""
import pytest

from assistant import FirstRun
from assistant.FirstRun import CurationProgress
from common.vault.Redactor import Redactor
from common.vault.Vault import Vault
from interfaces.console.Theme import COLOR_YELLOW_BG, COLOR_YELLOW_FG
from interfaces.console.framework.TUIScreen import TUIScreen
from interfaces.console.tabs.BankTab import BankTab
from interfaces.console.tabs.MemoryTab import FIRST_CURATION_LABEL, FIRST_CURATION_NOTICE
from interfaces.console.tabs.TabView import SUB_CONTENT_TOP

WIDTH, HEIGHT = 160, 24


@pytest.fixture
def tab(tmp_path):
    t = BankTab(Redactor(Vault(tmp_path / "vault.bin", "test")))
    t.memory_file_path = str(tmp_path / "memory.md")
    t.log_dir = tmp_path / "log" / "memory"
    with open(t.memory_file_path, "w", encoding="utf-8") as f:
        f.write("# Memory\n\nMarcus likes tea.\n")
    return t


@pytest.fixture
def curating(monkeypatch):
    monkeypatch.setattr(FirstRun.Pipeline, "curation", CurationProgress.begin(10, 3, now=0.0))


def draw(tab: BankTab) -> TUIScreen:
    screen = TUIScreen(WIDTH, HEIGHT)
    tab.draw(screen, 0)
    return screen


def row(screen: TUIScreen, y: int) -> str:
    return screen.row_text(1, y, WIDTH).rstrip()


def cells(screen: TUIScreen, y: int, needle: str):
    start = screen.row_text(1, y, WIDTH).index(needle)
    return [screen.next_frame[y - 1][x] for x in range(start, start + len(needle))]


class TestFirstCurationNotice:
    def test_no_band_on_an_established_install(self, tab, monkeypatch):
        monkeypatch.setattr(FirstRun.Pipeline, "curation", None)
        screen = draw(tab)
        assert row(screen, SUB_CONTENT_TOP) == "# Memory"

    def test_the_band_sits_above_the_text_while_the_first_curation_runs(self, tab, curating):
        screen = draw(tab)
        assert row(screen, SUB_CONTENT_TOP).strip() == FIRST_CURATION_LABEL
        assert row(screen, SUB_CONTENT_TOP + 1).strip() == FIRST_CURATION_NOTICE
        assert row(screen, SUB_CONTENT_TOP + 2) == "# Memory"
        assert "Marcus likes tea." in row(screen, SUB_CONTENT_TOP + 4)

    def test_both_rows_are_centred(self, tab, curating):
        screen = draw(tab)
        for y, text in ((SUB_CONTENT_TOP, FIRST_CURATION_LABEL), (SUB_CONTENT_TOP + 1, FIRST_CURATION_NOTICE)):
            raw = screen.row_text(1, y, WIDTH)
            left = raw.index(text)
            right = WIDTH - 2 - left - len(text)   # the band spans columns 1..WIDTH-2
            assert abs(left - right) <= 1, (y, left, right)

    def test_the_band_is_in_the_home_tabs_progress_bar_colours(self, tab, curating):
        screen = draw(tab)
        label = cells(screen, SUB_CONTENT_TOP, FIRST_CURATION_LABEL)
        assert all(c.style.fg == COLOR_YELLOW_FG and c.style.bold for c in label)
        sentence = cells(screen, SUB_CONTENT_TOP + 1, "work in progress")
        assert all(c.style.fg == COLOR_YELLOW_FG and not c.style.bold for c in sentence)
        assert all(c.style.bg == COLOR_YELLOW_BG for c in label + sentence)
        for y in (SUB_CONTENT_TOP, SUB_CONTENT_TOP + 1):   # both rows tinted edge to edge
            assert screen.next_frame[y - 1][0].style.bg == COLOR_YELLOW_BG
            assert screen.next_frame[y - 1][WIDTH - 3].style.bg == COLOR_YELLOW_BG

    def test_a_narrow_screen_clips_the_sentence_inside_the_band(self, tab, curating):
        screen = TUIScreen(40, HEIGHT)
        tab.draw(screen, 0)
        assert screen.row_text(1, SUB_CONTENT_TOP, 40).strip() == FIRST_CURATION_LABEL
        assert screen.row_text(1, SUB_CONTENT_TOP + 1, 40).strip().startswith("Keep is building")
        assert row(screen, SUB_CONTENT_TOP + 2) == "# Memory"
