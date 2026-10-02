"""The Bank tab never shows a vaulted value: each is masked the way the Vault tab lists it as the
text is loaded (before it is laid out or diffed), and in the live view — never over a snapshot diff —
the mask is marked in bold orange."""
import pytest

from common.vault.Redactor import Redactor
from common.vault.Vault import Vault
from interfaces.console.framework.TUIScreen import TUIScreen
from interfaces.console.Theme import COLOR_SECRET
from interfaces.console.tabs.BankTab import BankTab
from interfaces.console.tabs.TabView import SUB_CONTENT_TOP
from interfaces.console.widgets import Markdown
from interfaces.console.widgets.DiffText import (COLOR_ADDED_HIGHLIGHT_BG, COLOR_ADDED_LINE_BG, COLOR_REMOVED,
                                                 COLOR_REMOVED_LINE_BG)

PHONE = "+46701740605"
PHONE_MASK = "•••• 0605"
MAIL_MASK = "m•••@mlab.test"
WIDTH, HEIGHT = 80, 24


@pytest.fixture
def vault(tmp_path):
    return Vault(tmp_path / "vault.bin", "test")


@pytest.fixture
def stocked(vault):
    vault.add("phone", "Marcus's mobile", PHONE, id="marcus-mobile")
    vault.add("email", "Work e-mail", "m@mlab.test", id="work-mail")
    return vault


@pytest.fixture
def tab(tmp_path, stocked):
    t = BankTab(Redactor(stocked))
    t.memory_file_path = str(tmp_path / "memory.md")
    t.log_dir = tmp_path / "log" / "memory"
    return t


def write_memory(tab: BankTab, text: str):
    with open(tab.memory_file_path, "w", encoding="utf-8") as f:
        f.write(text)


def draw(tab: BankTab) -> TUIScreen:
    screen = TUIScreen(WIDTH, HEIGHT)
    tab.draw(screen, 0)
    return screen


def row(screen: TUIScreen, y: int) -> str:
    return screen.row_text(1, y, WIDTH).rstrip()


def rows(screen: TUIScreen) -> list[str]:
    return [row(screen, y) for y in range(SUB_CONTENT_TOP, HEIGHT)]


def find(screen: TUIScreen, needle: str, whole: bool = False) -> int:
    """The first content row showing `needle` (`whole`: showing nothing else)."""
    for y in range(SUB_CONTENT_TOP, HEIGHT):
        text = row(screen, y)
        if (text.strip() == needle) if whole else (needle in text):
            return y
    raise AssertionError(f"{needle!r} is not on screen:\n" + "\n".join(rows(screen)))


def cells_of(screen: TUIScreen, y: int, needle: str):
    text = screen.row_text(1, y, WIDTH)
    start = text.index(needle)
    return [screen.next_frame[y - 1][x] for x in range(start, start + len(needle))]


def fg_of(screen: TUIScreen, y: int, needle: str):
    """The foreground colours of the cells spelling `needle` on row `y`."""
    return [c.style.fg for c in cells_of(screen, y, needle)]


def bg_of(screen: TUIScreen, y: int, needle: str):
    return [c.style.bg for c in cells_of(screen, y, needle)]


class TestLiveView:
    def test_a_vaulted_phone_in_national_form_is_masked_in_bold_orange(self, tab):
        write_memory(tab, "Marcus's number is 070-174 06 05, call after five.\n")
        screen = draw(tab)
        y = find(screen, "Marcus's number")
        assert row(screen, y).strip() == f"Marcus's number is {PHONE_MASK}, call after five."
        assert fg_of(screen, y, PHONE_MASK) == [COLOR_SECRET] * len(PHONE_MASK)
        assert all(c.style.bold for c in cells_of(screen, y, PHONE_MASK))
        assert not any(c.style.bold for c in cells_of(screen, y, "Marcus's number is"))
        assert fg_of(screen, y, "Marcus") == [Markdown.COLOR_NORMAL] * 6
        assert fg_of(screen, y, "call") == [Markdown.COLOR_NORMAL] * 4

    def test_every_occurrence_on_a_row_is_masked(self, tab):
        write_memory(tab, "- mail m@mlab.test or ring +46 70 174 06 05\n")
        screen = draw(tab)
        y = find(screen, "mail ")
        assert row(screen, y).endswith(f"mail {MAIL_MASK} or ring {PHONE_MASK}")
        assert fg_of(screen, y, MAIL_MASK) == [COLOR_SECRET] * len(MAIL_MASK)
        assert fg_of(screen, y, PHONE_MASK) == [COLOR_SECRET] * len(PHONE_MASK)

    def test_a_secret_inside_markdown_markers_is_masked_and_marked(self, tab):
        write_memory(tab, "Bold **0701740605** and code `m@mlab.test` here\n")
        screen = draw(tab)
        y = find(screen, "Bold ")
        assert row(screen, y).strip() == f"Bold {PHONE_MASK} and code {MAIL_MASK} here"
        assert fg_of(screen, y, PHONE_MASK) == [COLOR_SECRET] * len(PHONE_MASK)
        assert all(c.style.bold for c in cells_of(screen, y, PHONE_MASK))
        assert fg_of(screen, y, MAIL_MASK) == [COLOR_SECRET] * len(MAIL_MASK)

    def test_other_text_keeps_its_markdown_colours(self, tab):
        write_memory(tab, "# Contacts\nMobile 0701740605\n")
        screen = draw(tab)
        heading = find(screen, "Contacts", whole=True)
        assert all(c.style.bold for c in cells_of(screen, heading, "Contacts"))
        assert Markdown.COLOR_NORMAL not in fg_of(screen, heading, "Contacts")
        assert COLOR_SECRET not in fg_of(screen, heading, "Contacts")
        y = find(screen, "Mobile")
        assert fg_of(screen, y, "Mobile") == [Markdown.COLOR_NORMAL] * 6
        assert fg_of(screen, y, PHONE_MASK) == [COLOR_SECRET] * len(PHONE_MASK)

    def test_a_value_is_masked_before_word_wrap_so_no_half_leaks(self, tab):
        filler = "x" * 60                         # puts the number over the wrap boundary
        write_memory(tab, f"{filler} +46 70 174 06 05 done\n")
        screen = draw(tab)
        assert find(screen, "done") > find(screen, filler)
        joined = " ".join(rows(screen))
        assert "174 06 05" not in joined and "1740605" not in joined
        assert "••••" in joined and "0605" in joined and "done" in joined

    def test_a_vault_edit_shows_on_the_next_frame(self, tab, stocked):
        write_memory(tab, "Plate KEP 47L\n")
        screen = draw(tab)
        assert row(screen, find(screen, "Plate")).strip() == "Plate KEP 47L"
        stocked.add("plate", "The Model Y", "KEP47L")
        screen = draw(tab)
        y = find(screen, "Plate")
        assert row(screen, y).strip() == "Plate KEP •••"
        assert fg_of(screen, y, "KEP •••") == [COLOR_SECRET] * 7

    def test_a_file_edit_is_masked_too(self, tab):
        write_memory(tab, "nothing yet\n")
        draw(tab)
        write_memory(tab, "Mobile 0701740605\n")
        screen = draw(tab)
        assert row(screen, find(screen, "Mobile")).strip() == f"Mobile {PHONE_MASK}"

    def test_empty_vault_masks_nothing(self, tmp_path, vault):
        tab = BankTab(Redactor(vault))
        tab.memory_file_path = str(tmp_path / "memory.md")
        tab.log_dir = tmp_path / "log"
        write_memory(tab, "Mobile 0701740605\n")
        screen = draw(tab)
        y = find(screen, "Mobile")
        assert row(screen, y).strip() == "Mobile 0701740605"
        assert fg_of(screen, y, "0701740605") == [Markdown.COLOR_NORMAL] * 10


class TestDiffView:
    def _snapshots(self, tab, older: str, newer: str):
        tab.log_dir.mkdir(parents=True)
        (tab.log_dir / "20260101_120000_memory.md").write_text("## Updated Memory\n" + older)
        (tab.log_dir / "20260102_120000_memory.md").write_text("## Updated Memory\n" + newer)
        tab._navigate_history(-1)

    def test_a_diff_is_masked_on_both_sides_but_not_orange(self, tab):
        write_memory(tab, "live\n")
        self._snapshots(tab, "Mobile 0701740605\n", "Mobile 0701740605 (new)\n")
        screen = draw(tab)
        y = find(screen, "- Mobile")
        assert row(screen, y).strip() == f"- Mobile {PHONE_MASK}"
        assert row(screen, y + 1).strip() == f"+ Mobile {PHONE_MASK} (new)"
        assert fg_of(screen, y, PHONE_MASK) == [COLOR_REMOVED] * len(PHONE_MASK)
        assert bg_of(screen, y, "Mobile") == [COLOR_REMOVED_LINE_BG] * 6
        assert COLOR_SECRET not in fg_of(screen, y + 1, PHONE_MASK)
        assert bg_of(screen, y + 1, "Mobile") == [COLOR_ADDED_LINE_BG] * 6

    def test_inline_highlights_line_up_with_the_masked_text(self, tab):
        write_memory(tab, "live\n")
        self._snapshots(tab, "Mobile 0701740605\n", "Mobile 0701740605 (new)\n")
        screen = draw(tab)
        y = find(screen, "+ Mobile")
        assert bg_of(screen, y, "(new)") == [COLOR_ADDED_HIGHLIGHT_BG] * 5
        assert COLOR_ADDED_HIGHLIGHT_BG not in bg_of(screen, y, f"Mobile {PHONE_MASK}")

    def test_inline_highlights_line_up_when_markdown_marks_were_stripped(self, tab):
        write_memory(tab, "live\n")
        self._snapshots(tab, "- **Marcus** likes tea\n", "- **Marcus** likes coffee\n")
        screen = draw(tab)
        y = find(screen, "likes coffee")
        assert "*" not in row(screen, y)
        assert COLOR_ADDED_HIGHLIGHT_BG in bg_of(screen, y, "coffee")
        assert COLOR_ADDED_HIGHLIGHT_BG not in bg_of(screen, y, "Marcus likes")

    def test_a_snapshot_without_a_diff_is_masked_and_marked(self, tab):
        write_memory(tab, "live\n")
        tab.log_dir.mkdir(parents=True)
        (tab.log_dir / "20260102_120000_memory.md").write_text("## Updated Memory\nMobile 0701740605\n")
        tab._navigate_history(-1)
        screen = draw(tab)
        y = find(screen, "Mobile")
        assert row(screen, y).strip() == f"Mobile {PHONE_MASK}"
        assert fg_of(screen, y, PHONE_MASK) == [COLOR_SECRET] * len(PHONE_MASK)

    def test_back_to_live_masks_and_marks_again(self, tab):
        write_memory(tab, "Mobile 0701740605\n")
        self._snapshots(tab, "a\n", "b\n")
        draw(tab)
        assert tab._has_diff
        tab._navigate_history(1)
        screen = draw(tab)
        y = find(screen, "Mobile")
        assert row(screen, y).strip() == f"Mobile {PHONE_MASK}"
        assert fg_of(screen, y, PHONE_MASK) == [COLOR_SECRET] * len(PHONE_MASK)


class TestConstruction:
    """TUIFloxyConsole builds every tab up front; the vault (scrypt, vault.bin) is resolved
    on the first draw, as the Vault tab does, not in the constructor."""

    def test_constructing_the_tab_does_not_resolve_the_vault(self, monkeypatch):
        monkeypatch.setattr(Vault, "get_instance",
                            classmethod(lambda cls: (_ for _ in ()).throw(AssertionError("touched"))))
        tab = BankTab()
        tab._scan_snapshots()
        assert tab._redactor is None

    def test_the_singleton_is_used_on_the_first_draw(self, tmp_path, stocked, monkeypatch):
        monkeypatch.setattr(Vault, "get_instance", classmethod(lambda cls: stocked))
        tab = BankTab()
        tab.memory_file_path = str(tmp_path / "memory.md")
        tab.log_dir = tmp_path / "log" / "memory"
        write_memory(tab, "Mobile 0701740605\n")
        screen = draw(tab)
        assert row(screen, find(screen, "Mobile")).strip() == f"Mobile {PHONE_MASK}"
        assert tab.redactor.vault is stocked
