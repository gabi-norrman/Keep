"""SecretMarks colours a vault mask where it is drawn, in cells: a wide glyph before the
mask shifts it by two columns, not by the one character it is."""
import pytest

from common.vault.Redactor import Redactor
from common.vault.Vault import Vault
from interfaces.console.Theme import COLOR_SECRET
from interfaces.console.framework.TUIScreen import TUIScreen
from interfaces.console.widgets.SecretMarks import mark_masks

PHONE_MASK = "•••• 0605"


@pytest.fixture
def redactor(tmp_path):
    vault = Vault(tmp_path / "vault.bin", "test")
    vault.add("phone", "Marcus's mobile", "+46701740605", id="marcus-mobile")
    return Redactor(vault)


def _marked_columns(screen: TUIScreen, y: int) -> list[int]:
    def is_secret(style):
        fg = style.fg
        return fg is not None and (fg.r, fg.g, fg.b) == (COLOR_SECRET.r, COLOR_SECRET.g, COLOR_SECRET.b) and style.bold
    return [x + 1 for x, c in enumerate(screen.next_frame[y - 1]) if is_secret(c.style)]


def test_a_mask_after_a_wide_glyph_is_marked_where_it_is_drawn(redactor):
    screen = TUIScreen(40, 1)
    screen.write_at(1, 1, f"\U0001F600 {PHONE_MASK} end")
    mark_masks(screen, redactor, 1, 1, 40)
    # The emoji takes columns 1-2 and the space 3; the mask starts at 4 and is nine cells wide.
    assert _marked_columns(screen, 1) == list(range(4, 13))


def test_a_row_without_a_mask_is_left_alone(redactor):
    screen = TUIScreen(40, 1)
    screen.write_at(1, 1, "\U0001F600 call me on 0605")
    mark_masks(screen, redactor, 1, 1, 40)
    assert _marked_columns(screen, 1) == []
