"""VaultTab: the secret list drawn masked, and the add / edit / delete / reveal flows driven
through handle_input with the key names TUIFloxyConsole._parse_key produces (Enter is '\\r',
Escape 'escape', Tab '\\t')."""
import pytest

from common.vault.Vault import Vault
from interfaces.console import TUIState
from interfaces.console.framework.TUIScreen import TUIScreen
from interfaces.console.Theme import COLOR_SECRET
from interfaces.console.tabs.AuthTab import COLOR_GRAY, COLOR_GREEN, COLOR_RED, COLOR_WHITE
from interfaces.console.tabs.TabView import CONTENT_TOP
from interfaces.console.tabs.MarkdownTabView import MARKDOWN_COLORS
from interfaces.console.tabs.VaultTab import (COLOR_HEADER_BG, COLOR_HELP_TITLE, COLOR_SELECTED_BG, COLOR_STRIPE_BG,
                                             HELP, HELP_MAX_WIDTH, HELP_TITLE, LEFT, MIN_LIST_ROWS, VaultTab)

# 0-based indexes into rows(): the title at CONTENT_TOP - 1, a spacer, the header, the secrets
HEADER = CONTENT_TOP + 1
FIRST = HEADER + 1

PHONE = "+46 70-174 06 05"
EMAIL = "m@mlab.test"
PLATE = "KEP 47L"
RAW_VALUES = (PHONE, "0701740605", "46701740605", EMAIL, PLATE, "KEP47L")
ENTER = '\r'


@pytest.fixture
def vault(tmp_path):
    v = Vault(tmp_path / "vault.bin", "test")
    v.add("phone", "Marcus's mobile", PHONE)       # id marcus-s-mobile
    v.add("email", "Work e-mail", EMAIL)           # id work-e-mail
    v.add("plate", "The Model Y", PLATE)           # id the-model-y
    return v


@pytest.fixture
def tab(vault):
    return VaultTab(vault)


def rows(screen: TUIScreen) -> list[str]:
    return ["".join(c.char for c in line).rstrip() for line in screen.next_frame]


def draw(tab: VaultTab, width=110, height=24) -> TUIScreen:
    screen = TUIScreen(width, height)
    tab.draw(screen, 0)
    return screen


def lines(tab: VaultTab, **kwargs) -> list[str]:
    return rows(draw(tab, **kwargs))


def press(tab: VaultTab, *keys: str):
    for key in keys:
        assert tab.handle_input(key)


def type_text(tab: VaultTab, text: str):
    press(tab, *text)


def style_at(screen: TUIScreen, row_1based: int, col_1based: int):
    return screen.next_frame[row_1based - 1][col_1based - 1].style


def color_at(screen: TUIScreen, row_1based: int, col_1based: int):
    return style_at(screen, row_1based, col_1based).fg


def row_with(lines_: list[str], needle: str) -> str:
    matches = [l for l in lines_ if needle in l]
    assert len(matches) == 1, (needle, matches)
    return matches[0]


def at_left(line: str) -> str:
    """The row without the margin the tab draws at column LEFT."""
    return line[LEFT - 1:]


def help_left(screen: TUIScreen) -> int:
    """1-based column of the centred help block's left edge."""
    return VaultTab.help_left(screen.width)


def help_block(screen: TUIScreen) -> tuple[int, list[str]]:
    """(0-based title row, the text rows under its blank row up to the blank row above the hint),
    left edge trimmed."""
    out = rows(screen)
    title = out.index(row_with(out, HELP_TITLE))
    left = help_left(screen) - 1
    return title, [l[left:] for l in out[title + 2:screen.height - 3]]


def list_row(lines_: list[str], secret_id: str) -> str:
    """The list row of `secret_id` (marker, id, columns) — not the status line naming it."""
    matches = [at_left(l) for l in lines_ if at_left(l)[2:].startswith(secret_id + " ")]
    assert len(matches) == 1, (secret_id, matches)
    return matches[0]


@pytest.mark.unit
class TestRender:
    def test_title_counts_the_secrets_and_names_the_file(self, tab):
        screen = draw(tab)
        assert rows(screen)[CONTENT_TOP - 1].strip() == "Vault — 3 secrets in vault.bin"
        assert color_at(screen, CONTENT_TOP, LEFT) == COLOR_WHITE

    def test_header_and_rows_sorted_by_id_with_masked_values(self, tab):
        out = lines(tab)
        header = out[HEADER].split()
        assert header == ["ID", "KIND", "DESCRIPTION", "VALUE"]
        first, second, third = map(at_left, out[FIRST:FIRST + 3])
        assert first.startswith("› marcus-s-mobile") and "phone" in first and "Marcus's mobile" in first
        assert first.rstrip().endswith("•••• 0605")
        assert second.startswith("  the-model-y") and "plate" in second and second.rstrip().endswith("KEP •••")
        assert third.startswith("  work-e-mail") and "email" in third and third.rstrip().endswith("m•••@mlab.test")

    def test_the_header_is_a_bold_plate_and_the_rows_alternate(self, tab):
        screen = draw(tab)
        header = style_at(screen, HEADER + 1, LEFT + 2)               # the I of ID
        assert header.bold and header.bg == COLOR_HEADER_BG
        assert style_at(screen, HEADER + 1, screen.width - 1).bg == COLOR_HEADER_BG   # to the right edge
        first, second, third = (style_at(screen, FIRST + 1 + i, LEFT + 2) for i in range(3))
        assert first.bg == COLOR_SELECTED_BG and first.bold
        assert style_at(screen, FIRST + 1, screen.width - 1).bg == COLOR_SELECTED_BG
        assert second.bg == COLOR_STRIPE_BG and not second.bold
        assert third.bg is None and not third.bold

    def test_a_revealed_value_is_bold_in_the_secret_orange(self, tab):
        press(tab, 'v')
        screen = draw(tab)
        column = rows(screen)[FIRST].index(PHONE) + 1
        assert style_at(screen, FIRST + 1, column).fg == COLOR_SECRET
        assert style_at(screen, FIRST + 1, column).bold
        press(tab, 'v')
        assert style_at(draw(tab), FIRST + 1, column).fg != COLOR_SECRET

    def test_no_raw_value_appears_anywhere_on_screen(self, tab):
        for height in (24, 48):
            frame = "\n".join(lines(tab, height=height))
            for raw in RAW_VALUES:
                assert raw not in frame

    def test_empty_vault_says_so(self, tmp_path):
        tab = VaultTab(Vault(tmp_path / "vault.bin", "test"))
        out = lines(tab)
        assert out[CONTENT_TOP - 1].strip() == "Vault — 0 secrets in vault.bin"
        assert "no secrets yet" in out[FIRST]

    def test_a_narrow_or_tiny_screen_draws_without_raising(self, tab):
        for width, height in ((20, 24), (110, 8), (3, 3), (1, 1)):
            out = lines(tab, width=width, height=height)
            assert all(len(l) <= width for l in out)
        press(tab, 'a', ENTER)
        type_text(tab, "x")
        press(tab, ENTER)
        type_text(tab, "value")
        lines(tab, width=12, height=10)

    def test_long_rows_are_cut_at_the_screen_edge(self, tab):
        out = lines(tab, width=40)
        assert all(len(l) <= 40 for l in out)
        assert at_left(out[FIRST]).startswith("› marcus-s-mobile")


@pytest.mark.unit
class TestHelp:
    """The how-it-works block: paragraphs anchored above the hint row when the list keeps its
    rows, gone on a short or narrow screen."""

    def test_the_paragraphs_sit_above_a_blank_row_and_the_hint(self, tab):
        screen = draw(tab, height=48)
        out = rows(screen)
        title, block = help_block(screen)
        assert out[title - 1].strip() == ""
        assert out[title + 1].strip() == ""                          # the blank row between title and text
        assert out[screen.height - 3].strip() == ""                  # the blank row between text and hint
        assert block[0] and block[-1]
        assert sum(1 for l in block if not l) == len(HELP) - 1       # one blank row between paragraphs
        assert not block[0].startswith(" ")                          # the text starts at the block's edge
        assert all(len(l) <= screen.width for l in out)
        assert "**" not in "\n".join(out)

    def test_the_block_is_centred_on_a_wide_screen_and_at_the_margin_on_a_narrow_one(self, tab):
        screen = draw(tab, width=160, height=48)
        out = rows(screen)
        title, block = help_block(screen)
        assert help_left(screen) == 31                               # 30 columns each side of the 100-wide block
        assert out[title].startswith(" " * 30 + HELP_TITLE)
        assert all(l[:30].strip() == "" for l in out[title + 1:screen.height - 3])
        assert max(len(l) for l in out[title:screen.height - 3]) <= 160 - 30
        narrow = draw(tab, width=80, height=60)
        assert help_left(narrow) == LEFT
        assert at_left(rows(narrow)[help_block(narrow)[0]]).startswith(HELP_TITLE)

    def test_title_in_the_memory_tab_header_blue_bold_pieces_bright_and_the_rest_gray(self, tab):
        screen = draw(tab, height=48)
        out = rows(screen)
        title, left = out.index(row_with(out, HELP_TITLE)) + 1, help_left(screen)   # 1-based
        assert COLOR_HELP_TITLE == MARKDOWN_COLORS['h1']
        assert style_at(screen, title, left).bold and color_at(screen, title, left) == COLOR_HELP_TITLE
        first, row = out[title + 1], title + 2                        # "Secrets are stored encrypted in vault.bin, …"
        plain, bold = first.index("Secrets") + 1, first.index("vault.bin") + 1
        assert color_at(screen, row, plain) == COLOR_GRAY and not style_at(screen, row, plain).bold
        assert color_at(screen, row, bold) == COLOR_WHITE and style_at(screen, row, bold).bold
        after = style_at(screen, row, bold + len("vault.bin"))        # the comma after it
        assert after.fg == COLOR_GRAY and not after.bold

    def test_a_bold_piece_split_over_a_wrap_stays_bold_on_both_rows(self, tab):
        # Find a width where a bold span wraps, then check both halves are bold and the join is exact
        for width in range(60, 140):
            screen = draw(tab, width=width, height=60)
            out = rows(screen)
            title = out.index(row_with(out, HELP_TITLE))
            for row in range(title + 1, screen.height - 3):
                line = out[row].rstrip()
                if not line or row + 1 >= screen.height - 3 or not out[row + 1].strip():
                    continue
                last, first = len(line), help_left(screen)
                if style_at(screen, row + 1, last).bold and style_at(screen, row + 2, first).bold:
                    return
        pytest.fail("no width wrapped a bold span")

    def test_the_text_never_runs_wider_than_the_cap_on_a_wide_screen(self, tab):
        screen = draw(tab, width=200, height=48)
        _, block = help_block(screen)
        assert max(len(l) for l in block) <= HELP_MAX_WIDTH
        assert max(len(l) for l in block) > HELP_MAX_WIDTH - 20        # wrapped near the cap, not far under it

    def test_hidden_when_it_would_squeeze_the_list(self, tab):
        out = lines(tab, height=24)
        assert not any(HELP_TITLE in l for l in out)
        assert out[-2].strip() == "↑↓ select  a add  e edit  d delete  v reveal"

    def test_hidden_on_a_screen_too_narrow_to_read(self, tab):
        out = lines(tab, width=30, height=60)
        assert not any(HELP_TITLE in l for l in out)
        assert at_left(out[FIRST]).startswith("› marcus-s-mobile")

    def test_a_long_list_keeps_scrolling_room_above_the_block(self, vault):
        for i in range(40):
            vault.add("text", f"Secret {i}", f"value-{i:04d}")
        tab = VaultTab(vault)
        press(tab, *['down'] * 50)
        screen = draw(tab, height=48)
        out = rows(screen)
        title = out.index(row_with(out, HELP_TITLE))
        # Blank, status, prompt, blank, then the list ending on the selected row
        assert at_left(out[title - 5]).startswith("› ")
        assert title - 5 - FIRST + 1 >= MIN_LIST_ROWS
        assert "Vault — 43 secrets" in out[CONTENT_TOP - 1]


@pytest.mark.unit
class TestSelectionAndReveal:
    def test_up_and_down_move_the_marker_and_stop_at_the_ends(self, tab):
        press(tab, 'down')
        assert at_left(lines(tab)[FIRST + 1]).startswith("› the-model-y")
        press(tab, 'down', 'down', 'down')
        assert at_left(lines(tab)[FIRST + 2]).startswith("› work-e-mail")
        press(tab, 'up', 'up', 'up', 'up')
        assert at_left(lines(tab)[FIRST]).startswith("› marcus-s-mobile")

    def test_v_reveals_the_selected_row_only_and_toggles_back(self, tab):
        press(tab, 'v')
        out = lines(tab)
        assert out[FIRST].rstrip().endswith(PHONE)
        assert out[FIRST + 1].rstrip().endswith("KEP •••")
        assert out[FIRST + 2].rstrip().endswith("m•••@mlab.test")
        press(tab, 'v')
        assert lines(tab)[FIRST].rstrip().endswith("•••• 0605")

    def test_reveal_follows_the_row_not_the_cursor(self, tab):
        press(tab, 'down', 'v', 'up')
        out = lines(tab)
        assert out[FIRST].rstrip().endswith("•••• 0605")
        assert out[FIRST + 1].rstrip().endswith(PLATE)

    def test_leaving_the_tab_hides_revealed_values(self, tab):
        press(tab, 'v')
        tab.on_deactivate()
        assert PHONE not in "\n".join(lines(tab))

    def test_leaving_the_tab_mid_typing_drops_a_revealed_field(self, tab, vault):
        press(tab, 'a', ENTER)
        type_text(tab, "Anna")
        press(tab, ENTER)
        type_text(tab, "+46 73 123 45 67")
        press(tab, 'tab')                             # reveal the field while typing
        assert "+46 73 123 45 67" in "\n".join(lines(tab))
        tab.on_deactivate()
        assert "+46 73 123 45 67" not in "\n".join(lines(tab))
        assert tab.captures_text is False
        assert len(vault) == 3

    def test_view_keys_it_does_not_know_are_not_consumed(self, tab):
        assert not tab.handle_input('x')
        assert not tab.handle_input('\x18')
        assert not tab.handle_input('escape')


@pytest.mark.unit
class TestAddFlow:
    def test_tab_is_claimed_only_while_the_value_field_is_open(self, tab):
        # The console lets Tab walk the tabs past a text field unless the tab claims it for
        # revealing a masked value; the kind and description steps must not claim it.
        assert not tab.claims_tab
        press(tab, 'a')
        assert not tab.claims_tab
        press(tab, ENTER)
        assert tab.captures_text and not tab.claims_tab
        type_text(tab, "Anna's mobile")
        press(tab, ENTER)
        assert tab.claims_tab
        type_text(tab, "0701234567")
        press(tab, 'tab')                            # the reveal, not a tab switch
        assert "0701234567" in row_with(lines(tab), "Value (phone):") and tab.claims_tab
        press(tab, 'escape')
        assert not tab.claims_tab

    def test_kind_description_value_then_the_row_appears(self, tab, vault):
        press(tab, 'a')
        assert tab.captures_text is False
        prompt = row_with(lines(tab), "Kind:")
        assert "‹ phone ›" in prompt
        press(tab, 'right')
        assert "‹ card ›" in row_with(lines(tab), "Kind:")
        press(tab, 'left', 'left')
        assert "‹ text ›" in row_with(lines(tab), "Kind:")
        press(tab, 'right')                     # wraps back to phone
        press(tab, ENTER)
        assert tab.captures_text is True
        assert "Description:" in row_with(lines(tab), "Description:")
        type_text(tab, "Anna's mobile")
        assert "Anna's mobile" in row_with(lines(tab), "Description:")
        press(tab, ENTER)
        assert "Value (phone):" in row_with(lines(tab), "Value")
        type_text(tab, "+46 73 123 45 67")
        press(tab, ENTER)

        assert tab.captures_text is False
        assert len(vault) == 4
        added = vault.get("anna-s-mobile")
        assert added.kind == "phone" and added.value == "+46 73 123 45 67"
        screen = draw(tab)
        out = rows(screen)
        row = list_row(out, "anna-s-mobile")
        assert row.startswith("›") and row.rstrip().endswith("•••• 4567")
        status_row = out.index(row_with(out, "Added 'anna-s-mobile' (phone)"))
        assert color_at(screen, status_row + 1, LEFT) == COLOR_GREEN
        assert out[CONTENT_TOP - 1].strip() == "Vault — 4 secrets in vault.bin"

    def test_the_value_is_masked_while_typing_until_tab_reveals_it(self, tab):
        press(tab, 'a', ENTER)
        type_text(tab, "Anna")
        press(tab, ENTER)
        type_text(tab, "+46 73 123 45 67")
        frame = "\n".join(lines(tab))
        assert "+46 73 123 45 67" not in frame
        assert "•" * len("+46 73 123 45 67") in row_with(lines(tab), "Value (phone):")
        press(tab, 'tab')
        assert "+46 73 123 45 67" in row_with(lines(tab), "Value (phone):")
        press(tab, 'tab')
        assert "+46 73 123 45 67" not in "\n".join(lines(tab))

    def test_letters_v_a_e_d_go_into_the_field_while_typing(self, tab, vault):
        press(tab, 'a', 'right', 'right', 'right', 'right', 'right', 'right', ENTER)   # password
        type_text(tab, "vault dev")
        press(tab, ENTER)
        type_text(tab, "vade-retro-1")
        press(tab, ENTER)
        assert vault.get("vault-dev").value == "vade-retro-1"

    def test_too_short_value_shows_a_red_error_and_creates_nothing(self, tab, vault):
        press(tab, 'a', ENTER)
        type_text(tab, "Door")
        press(tab, ENTER)
        type_text(tab, "12345")
        press(tab, ENTER)
        assert len(vault) == 3
        assert tab.captures_text is True            # still in the value step, the text kept
        screen = draw(tab)
        out = rows(screen)
        error = row_with(out, "too short")
        assert at_left(error).startswith("Cannot add:")
        assert color_at(screen, out.index(error) + 1, LEFT) == COLOR_RED
        assert "•••••" in row_with(out, "Value (phone):")
        press(tab, 'escape')
        assert tab.captures_text is False
        assert not any("too short" in l for l in lines(tab))

    def test_duplicate_value_names_the_existing_secret(self, tab, vault):
        press(tab, 'a', ENTER)
        type_text(tab, "Same phone")
        press(tab, ENTER)
        type_text(tab, "0701740605")
        press(tab, ENTER)
        assert len(vault) == 3
        assert "marcus-s-mobile" in row_with(lines(tab), "Cannot add:")

    def test_empty_description_is_refused(self, tab):
        press(tab, 'a', ENTER, ENTER)
        assert "description is required" in row_with(lines(tab), "required")
        assert tab.captures_text is True

    def test_escape_cancels_at_every_step(self, tab, vault):
        press(tab, 'a', 'escape')
        assert tab.captures_text is False and not any("Kind:" in l for l in lines(tab))
        press(tab, 'a', ENTER, 'escape')
        assert tab.captures_text is False
        press(tab, 'a', ENTER)
        type_text(tab, "x")
        press(tab, ENTER, 'escape')
        assert tab.captures_text is False
        assert len(vault) == 3

    def test_captures_text_only_while_a_text_field_is_active(self, tab):
        assert tab.captures_text is False
        press(tab, 'a')
        assert tab.captures_text is False
        press(tab, ENTER)
        assert tab.captures_text is True
        type_text(tab, "x")
        press(tab, ENTER)
        assert tab.captures_text is True
        press(tab, 'escape')
        assert tab.captures_text is False
        press(tab, 'd')
        assert tab.captures_text is False
        press(tab, 'escape', 'e')
        assert tab.captures_text is True

    def test_no_key_of_the_add_flow_touches_tui_state(self, tab, vault, monkeypatch):
        def forbidden(*args, **kwargs):
            raise AssertionError("TUIState must never see a vault field")
        monkeypatch.setattr(TUIState, "load", forbidden)
        monkeypatch.setattr(TUIState, "save", forbidden)
        press(tab, 'a', 'right', 'right', 'right', 'right', 'right', ENTER)   # email
        type_text(tab, "Private e-mail")
        press(tab, ENTER)
        type_text(tab, "marcus@example.com")
        press(tab, 'tab', ENTER)
        assert vault.get("private-e-mail").value == "marcus@example.com"
        lines(tab)


@pytest.mark.unit
class TestEditFlow:
    def test_tab_is_claimed_once_the_edit_reaches_the_value_field(self, tab):
        press(tab, 'e')
        assert tab.captures_text and not tab.claims_tab
        press(tab, ENTER)                            # unchanged description: straight to the value
        assert tab.claims_tab
        press(tab, 'escape')
        assert not tab.claims_tab

    def test_description_is_prefilled_and_can_be_changed(self, tab, vault):
        press(tab, 'e')
        assert "Marcus's mobile" in row_with(lines(tab), "Description:")
        press(tab, 'home', '\x0b')                   # clear the prefilled text
        type_text(tab, "Marcus mobile")
        press(tab, ENTER, 'n')                       # the new wording slugs differently: keep the id
        assert "New value (phone):" in row_with(lines(tab), "New value")
        press(tab, ENTER)                            # empty value keeps the old one
        updated = vault.get("marcus-s-mobile")
        assert updated.description == "Marcus mobile" and updated.value == PHONE
        assert "Updated 'marcus-s-mobile' (phone)" in "\n".join(lines(tab))

    def test_value_can_be_changed_and_the_new_one_is_masked(self, tab, vault):
        press(tab, 'down', 'e', ENTER)               # keep the description of the-model-y
        type_text(tab, "ABC 12D")
        press(tab, ENTER)
        assert vault.get("the-model-y").value == "ABC 12D"
        out = lines(tab)
        assert list_row(out, "the-model-y").rstrip().endswith("ABC •••")
        assert "ABC 12D" not in "\n".join(out)

    def test_editing_a_revealed_secret_hides_the_new_value(self, tab, vault):
        # The one place a fresh value could stay painted on the list in the clear.
        press(tab, 'down', 'v')
        assert list_row(lines(tab), "the-model-y").rstrip().endswith(PLATE)
        press(tab, 'e', ENTER)
        type_text(tab, "ABC 12D")
        press(tab, ENTER)
        out = lines(tab)
        assert vault.get("the-model-y").value == "ABC 12D"
        assert "ABC 12D" not in "\n".join(out)
        assert list_row(out, "the-model-y").rstrip().endswith("ABC •••")

    def test_an_invalid_new_value_keeps_the_step_open(self, tab, vault):
        press(tab, 'down', 'e', ENTER)
        type_text(tab, "12")
        press(tab, ENTER)
        assert tab.captures_text is True
        assert "Cannot update:" in row_with(lines(tab), "Cannot update")
        assert vault.get("the-model-y").value == PLATE

    def test_enter_twice_without_changes_changes_nothing(self, tab, vault):
        before = vault.version
        press(tab, 'e', 'home', '\x0b', ENTER, ENTER)
        assert vault.version == before
        assert "Nothing changed" in "\n".join(lines(tab))

    def test_a_changed_description_offers_the_new_id_and_y_renames(self, tab, vault):
        press(tab, 'e', 'home', '\x0b')
        type_text(tab, "Marcus mobile")
        press(tab, ENTER)
        out = lines(tab)
        assert "Adjust id to marcus-mobile? " in row_with(out, "Adjust id")
        assert "y adjust the id  n keep it  Esc cancel" in out[-2]     # the hint row
        assert tab.captures_text is False
        press(tab, 'y')
        assert "New value (phone):" in row_with(lines(tab), "New value")
        press(tab, ENTER)
        assert vault.get("marcus-s-mobile") is None
        renamed = vault.get("marcus-mobile")
        assert renamed.description == "Marcus mobile" and renamed.value == PHONE
        out = lines(tab)
        assert "Renamed 'marcus-s-mobile' → 'marcus-mobile' (phone)" in "\n".join(out)
        assert list_row(out, "marcus-mobile").startswith("›")

    def test_n_keeps_the_id_but_still_saves_the_description(self, tab, vault):
        press(tab, 'e', 'home', '\x0b')
        type_text(tab, "Marcus mobile")
        press(tab, ENTER, 'n', ENTER)
        assert vault.get("marcus-s-mobile").description == "Marcus mobile"
        assert vault.get("marcus-mobile") is None
        assert "Updated 'marcus-s-mobile' (phone)" in "\n".join(lines(tab))

    def test_a_declined_rename_alone_still_counts_as_a_change(self, tab, vault):
        # Only the wording changed and the id was kept: that is still a write
        press(tab, 'e', 'end')
        type_text(tab, "!")
        press(tab, ENTER)
        assert "Adjust id to marcus-s-mobile-2?" not in "\n".join(lines(tab))
        assert "New value" in "\n".join(lines(tab))       # slug unchanged: no question asked
        press(tab, ENTER)
        assert vault.get("marcus-s-mobile").description == "Marcus's mobile!"

    def test_an_unchanged_description_asks_nothing(self, tab, vault):
        press(tab, 'e', ENTER)
        assert "New value" in "\n".join(lines(tab))
        assert "Adjust id" not in "\n".join(lines(tab))

    def test_a_taken_slug_is_offered_with_a_suffix(self, tab, vault):
        press(tab, 'e', 'home', '\x0b')
        type_text(tab, "Work e-mail")                # the-e-mail's id is taken by another secret
        press(tab, ENTER)
        assert "Adjust id to work-e-mail-2? " in row_with(lines(tab), "Adjust id")
        press(tab, 'y', ENTER)
        assert vault.get("work-e-mail-2").value == PHONE and vault.get("work-e-mail").value == EMAIL

    def test_escape_or_other_keys_at_the_rename_question(self, tab, vault):
        press(tab, 'e', 'home', '\x0b')
        type_text(tab, "Marcus mobile")
        press(tab, ENTER, 'x', 'q')
        assert "Adjust id to marcus-mobile? " in row_with(lines(tab), "Adjust id")
        before = vault.version
        press(tab, 'escape')
        assert vault.version == before and tab.captures_text is False
        assert "Adjust id" not in "\n".join(lines(tab))
        assert vault.get("marcus-s-mobile").description == "Marcus's mobile"


@pytest.mark.unit
class TestDeleteFlow:
    def test_d_then_y_deletes_the_selected_secret(self, tab, vault):
        press(tab, 'down', 'd')
        assert "Delete the-model-y? y/n" in row_with(lines(tab), "Delete")
        press(tab, 'y')
        assert len(vault) == 2 and vault.get("the-model-y") is None
        out = lines(tab)
        assert not any("the-model-y" in l for l in out[FIRST:FIRST + 2])
        assert "Deleted 'the-model-y' (plate)" in "\n".join(out)
        assert at_left(out[FIRST + 1]).startswith("› work-e-mail")

    def test_d_then_escape_or_n_keeps_it(self, tab, vault):
        press(tab, 'd', 'escape')
        assert len(vault) == 3
        press(tab, 'd', 'n')
        assert len(vault) == 3
        assert not any("Delete" in l for l in lines(tab))

    def test_other_keys_do_not_confirm(self, tab, vault):
        press(tab, 'd', 'x', ENTER, 'Y')
        assert len(vault) == 3
        assert "Delete marcus-s-mobile? y/n" in row_with(lines(tab), "Delete")


@pytest.mark.unit
class TestEmptyVault:
    def test_commands_needing_a_secret_are_consumed_without_a_crash_and_add_still_works(self, tmp_path):
        # A first-time user has nothing to edit; 'd' without the guard would enter the
        # confirm step with no target and the next frame would die drawing "Delete None?".
        tab = VaultTab(Vault(tmp_path / "vault.bin", "test"))
        for key in ('e', 'd', 'v'):
            assert tab.handle_input(key)
            assert tab.captures_text is False
            out = lines(tab)
            assert not any("Delete" in l for l in out)
            assert "no secrets yet" in out[FIRST]
        press(tab, 'a')
        assert "‹ phone ›" in row_with(lines(tab), "Kind:")


@pytest.mark.unit
class TestLocked:
    @pytest.fixture
    def locked(self, tmp_path):
        return VaultTab(Vault(tmp_path / "vault.bin", None))

    def test_title_says_locked_in_red(self, locked):
        screen = draw(locked)
        assert rows(screen)[CONTENT_TOP - 1].strip() == "Vault — LOCKED: vault.key not set in config.yaml"
        assert color_at(screen, CONTENT_TOP, LEFT) == COLOR_RED

    def test_edit_keys_are_ignored_but_consumed(self, locked):
        for key in ('a', 'e', 'd', 'v'):
            assert locked.handle_input(key)
            assert locked.captures_text is False
        out = lines(locked)
        assert not any("Kind:" in l for l in out)
        assert not any("Delete" in l for l in out)

    def test_wrong_key_reports_the_decrypt_failure(self, tmp_path, vault):
        tab = VaultTab(Vault(vault.path, "wrong"))
        title = lines(tab)[CONTENT_TOP - 1]
        assert "LOCKED" in title and "wrong vault.key" in title


@pytest.mark.unit
class TestConstruction:
    def test_constructing_the_tab_does_not_resolve_the_vault(self, monkeypatch):
        monkeypatch.setattr(Vault, "get_instance", classmethod(lambda cls: (_ for _ in ()).throw(AssertionError("touched"))))
        tab = VaultTab()
        assert tab.name == "Vault"
        assert tab.captures_text is False
