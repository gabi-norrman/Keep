import pytest

from interfaces.console.framework.TUIScreen import TUIScreen
from interfaces.console.tabs import TabBar

ROW = 2
NAMES = [f"T{i:02d}" for i in range(20)]
HINT = "press Tab to move "
BEFORE, AFTER = TabBar.MORE_BEFORE.strip(), TabBar.MORE_AFTER.strip()


def draw(width, names, active, **kwargs) -> TUIScreen:
    screen = TUIScreen(width=width, height=5)
    TabBar.draw_tab_bar(screen, ROW, names, active, True, TabBar.SUB_BAR, **kwargs)
    return screen


def row(width, names, active, **kwargs) -> str:
    return draw(width, names, active, **kwargs).row_text(1, ROW, width)


def shown(text: str, names=NAMES) -> list:
    return [name for name in names if name in text]


def test_a_row_that_fits_shows_every_tab_and_no_marker():
    text = row(80, NAMES[:5], 2, indent=2, hint=HINT, center="mid")
    assert shown(text) == NAMES[:5]
    assert BEFORE not in text and AFTER not in text
    assert HINT.strip() in text and "mid" in text


def test_a_row_that_fits_is_laid_out_the_same_whatever_room_is_left_over():
    narrow, wide = draw(60, NAMES[:5], 2, indent=2), draw(140, NAMES[:5], 2, indent=2)
    used = len(row(60, NAMES[:5], 2, indent=2).rstrip())
    cells = lambda screen: [(c.char, c.style.fg, c.style.bg, c.style.bold) for c in screen.next_frame[ROW - 1][:used]]
    assert cells(narrow) == cells(wide)


@pytest.mark.parametrize("width", [24, 37, 60, 150])
@pytest.mark.parametrize("walk", [range(len(NAMES)), reversed(range(len(NAMES)))], ids=["forwards", "backwards"])
def test_the_active_tab_is_on_the_row_wherever_the_selection_walks(width, walk):
    for active in walk:
        screen = draw(width, NAMES, active, indent=2)
        text = screen.row_text(1, ROW, width)
        visible = shown(text)
        assert NAMES[active] in visible, f"{NAMES[active]} missing at {width} columns: {text!r}"
        first = NAMES.index(visible[0])
        assert visible == NAMES[first:first + len(visible)], "the row shows one run of neighbouring tabs"
        cell = screen.next_frame[ROW - 1][text.index(NAMES[active])]
        assert cell.style.bg == TabBar.SUB_BAR.active_bg and cell.style.bold


@pytest.mark.parametrize("hint", ["", HINT], ids=["no hint", "hint"])
def test_a_marker_stands_exactly_where_tabs_are_hidden_at_every_width(hint):
    for width in range(20, 90):
        for active in (0, 7, len(NAMES) - 1):
            text = row(width, NAMES, active, indent=2, hint=hint)
            visible = shown(text)
            where = f"{width} columns, {NAMES[active]} selected: {text!r}"
            assert (BEFORE in text) == (NAMES[0] not in visible), where
            assert (AFTER in text) == (NAMES[-1] not in visible), where
            if BEFORE in text:
                assert text.index(BEFORE) < text.index(visible[0]), where
            if AFTER in text:
                assert text.index(AFTER) > text.index(visible[-1]), where


@pytest.mark.parametrize("width", [37, 60, 150])
def test_the_row_stands_still_while_the_selection_stays_on_it(width):
    previous = shown(row(width, NAMES, 0, indent=2))
    turns = 0
    for active in range(1, len(NAMES)):
        visible = shown(row(width, NAMES, active, indent=2))
        if NAMES[active] in previous:
            assert visible == previous, f"the row moved though {NAMES[active]} was already on it"
        else:
            turns += 1
        previous = visible
    assert 0 < turns < len(NAMES) - 1


def test_wide_glyph_names_keep_the_row_in_its_cells():
    names = ["写真", "カメラ", "音楽ライブラリ", "天気", "家", "電話", "メール", "予定", "車"]
    width = 40
    for active in range(len(names)):
        screen = draw(width, names, active, indent=2, hint=HINT)
        text = screen.row_text(1, ROW, width)
        assert names[active] in text
        cells = screen.next_frame[ROW - 1]
        assert len(cells) == width and sum(TabBar.display_width(c.char) for c in cells) == width


def test_a_screen_too_narrow_for_the_active_tab_still_draws_what_fits():
    text = row(8, ["Greenhouse", "Lamp"], 0, indent=2, hint=HINT)
    assert "Green" in text and "Lamp" not in text


def test_the_hint_gives_way_to_the_tabs_for_every_selection_before_any_tab_is_hidden():
    names = NAMES[:6]
    beside, instead, paged = 90, 70, 40
    assert all(HINT.strip() in row(beside, names, active, hint=HINT) for active in range(len(names)))
    for active in range(len(names)):
        text = row(instead, names, active, hint=HINT)
        assert HINT.strip() not in text and shown(text, names) == names, f"{names[active]} selected: {text!r}"
        assert BEFORE not in text and AFTER not in text
    assert not any(HINT.strip() in row(paged, names, active, hint=HINT) for active in range(len(names)))
    assert AFTER in row(paged, names, 0, hint=HINT)


def test_center_gives_way_when_the_tabs_are_paged():
    assert "mid" not in row(60, NAMES, 0, hint=HINT, center="mid")
