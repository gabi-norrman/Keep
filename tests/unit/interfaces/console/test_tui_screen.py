import re

from interfaces.console.framework.TUIScreen import TUIScreen, Color, Style

CURSOR_MOVE_RE = re.compile(r'\x1b\[\d+;\d+H')


def row_text(screen: TUIScreen, y: int) -> str:
    return "".join(cell.char for cell in screen.next_frame[y - 1])


class TestColor:
    def test_equality_same_components(self):
        assert Color(10, 20, 30) == Color(10, 20, 30)

    def test_inequality_different_components(self):
        assert Color(10, 20, 30) != Color(10, 20, 31)

    def test_identity_shortcut(self):
        c = Color(1, 2, 3)
        assert c == c

    def test_non_color_not_equal(self):
        assert (Color(1, 2, 3) == "nope") is False

    def test_equal_colors_hash_equal(self):
        assert hash(Color(5, 6, 7)) == hash(Color(5, 6, 7))

    def test_usable_as_dict_key(self):
        d = {Color(1, 1, 1): "a"}
        assert d[Color(1, 1, 1)] == "a"

    def test_fg_escape_string(self):
        assert str(Color(10, 20, 30)) == "\033[38;2;10;20;30m"

    def test_bg_escape_string(self):
        assert Color(10, 20, 30).bg() == "\033[48;2;10;20;30m"


class TestStyle:
    def test_equality_all_fields(self):
        a = Style(fg=Color(1, 2, 3), bold=True)
        b = Style(fg=Color(1, 2, 3), bold=True)
        assert a == b

    def test_inequality_on_bold(self):
        assert Style(bold=True) != Style(bold=False)

    def test_inequality_on_fg(self):
        assert Style(fg=Color(1, 2, 3)) != Style(fg=Color(3, 2, 1))

    def test_none_fg_vs_set_fg(self):
        assert Style(fg=None) != Style(fg=Color(0, 0, 0))

    def test_copy_is_equal_but_distinct(self):
        a = Style(fg=Color(1, 2, 3), italic=True)
        b = a.copy()
        assert a == b
        assert a is not b

    def test_copy_from_mutates_in_place(self):
        a = Style(fg=Color(1, 2, 3), bold=True, underline=True)
        b = Style()
        b.copy_from(a)
        assert b == a

    def test_reset_clears_all(self):
        s = Style(fg=Color(1, 2, 3), bold=True, italic=True, underline=True)
        s.reset()
        assert s == Style()
        assert s.is_default()

    def test_is_default_true_for_fresh(self):
        assert Style().is_default()

    def test_is_default_false_with_attribute(self):
        assert not Style(bold=True).is_default()

    def test_non_style_not_equal(self):
        assert (Style() == "nope") is False


class TestClampColorComponent:
    def test_in_range(self):
        assert TUIScreen._clamp_color_component(128) == 128

    def test_negative_clamps_to_zero(self):
        assert TUIScreen._clamp_color_component(-5) == 0

    def test_above_max_clamps_to_255(self):
        assert TUIScreen._clamp_color_component(300) == 255

    def test_float_truncated(self):
        assert TUIScreen._clamp_color_component(12.9) == 12

    def test_boundary_values(self):
        assert TUIScreen._clamp_color_component(0) == 0
        assert TUIScreen._clamp_color_component(255) == 255


class TestGetFrame:
    def test_fresh_screen_no_diff(self):
        screen = TUIScreen(20, 5)
        assert screen.get_frame() == b''

    def test_single_write_emits_content(self):
        screen = TUIScreen(20, 5)
        screen.write_at(1, 1, "X")
        out = screen.get_frame(use_sync=False).decode('utf-8')
        assert "X" in out
        assert "\x1b[1;1H" in out  # cursor move to row 1 col 1

    def test_second_call_after_flush_is_empty(self):
        screen = TUIScreen(20, 5)
        screen.write_at(1, 1, "hello")
        assert screen.get_frame() != b''
        # front buffer now matches next_frame; nothing new to send
        assert screen.get_frame() == b''

    def test_sync_markers_present_by_default(self):
        screen = TUIScreen(20, 5)
        screen.write_at(1, 1, "X")
        out = screen.get_frame(use_sync=True).decode('utf-8')
        assert out.startswith(TUIScreen.BEGIN_SYNC)
        assert out.endswith(TUIScreen.END_SYNC)

    def test_sync_markers_absent_when_disabled(self):
        screen = TUIScreen(20, 5)
        screen.write_at(1, 1, "X")
        out = screen.get_frame(use_sync=False).decode('utf-8')
        assert TUIScreen.BEGIN_SYNC not in out
        assert TUIScreen.END_SYNC not in out

    def test_adjacent_same_style_coalesced_into_one_run(self):
        screen = TUIScreen(20, 5)
        screen.write_at(1, 1, "AB")
        out = screen.get_frame(use_sync=False).decode('utf-8')
        # One contiguous run => exactly one cursor-move command
        assert len(CURSOR_MOVE_RE.findall(out)) == 1
        assert "AB" in out

    def test_gap_between_changes_produces_separate_runs(self):
        screen = TUIScreen(20, 5)
        # Leave column 2 as an unchanged space between the two writes.
        screen.write_at(1, 1, "A")
        screen.write_at(3, 1, "C")
        out = screen.get_frame(use_sync=False).decode('utf-8')
        assert len(CURSOR_MOVE_RE.findall(out)) == 2

    def test_invalidate_front_buffer_forces_full_redraw(self):
        screen = TUIScreen(20, 5)
        screen.write_at(1, 1, "hi")
        screen.get_frame()  # flush; front now matches
        assert screen.get_frame() == b''
        screen.invalidate_front_buffer()
        out = screen.get_frame(use_sync=False).decode('utf-8')
        assert "hi" in out

    def test_control_sequences_force_output_even_without_cell_diff(self):
        screen = TUIScreen(20, 5)
        screen.control_sequences.append(TUIScreen.HIDE_CURSOR)  # no cell change
        out = screen.get_frame(use_sync=False).decode('utf-8')
        assert TUIScreen.HIDE_CURSOR in out

    def test_a_control_character_in_text_is_drawn_as_a_space(self):
        # A raw tab would move the terminal's cursor to the next tab stop and shift every
        # cell after it on the row (an Apple order email in the history had 192 of them).
        screen = TUIScreen(20, 5)
        screen.write_at(1, 1, "a\tb\x1bc")
        assert [screen.next_frame[0][i].char for i in range(5)] == ["a", " ", "b", " ", "c"]
        assert screen.cursor_x == 6
        out = screen.get_frame(use_sync=False).decode('utf-8')
        assert "\t" not in out
        assert "\x1bc" not in out


class TestBackground:
    """The screen owns every cell's background: the cells no tab writes are painted once on
    connect/clear/resize, and the diff never repeats them."""

    def test_paint_covers_every_row_with_spaces_on_the_background(self):
        screen = TUIScreen(4, 3)
        paint = screen.paint_background()
        assert paint.startswith(TUIScreen.RESET + TUIScreen.BACKGROUND_SEQ)
        for y in (1, 2, 3):
            assert f"{TUIScreen.CSI}{y};1H    " in paint
        assert paint.endswith(TUIScreen.RESET)

    def test_invalidate_emits_the_paint_once(self):
        screen = TUIScreen(4, 2)
        screen.invalidate_front_buffer()
        out = screen.get_frame(use_sync=False).decode('utf-8')
        assert out.count(f"{TUIScreen.CSI}2;1H    ") == 1
        assert screen.get_frame() == b''

    def test_untouched_cells_are_not_repainted_by_the_diff(self):
        screen = TUIScreen(4, 2)
        screen.invalidate_front_buffer()
        screen.get_frame()
        screen.write_at(1, 1, "a")
        out = screen.get_frame(use_sync=False).decode('utf-8')
        assert TUIScreen.BACKGROUND_SEQ + "a" in out
        assert f"{TUIScreen.CSI}2;1H" not in out

    def test_erase_line_blanks_onto_the_background(self):
        screen = TUIScreen(4, 2)
        assert screen.erase_line(2) == (f"{TUIScreen.CSI}2;1H{TUIScreen.RESET}"
                                        f"{TUIScreen.BACKGROUND_SEQ}{TUIScreen.CLEAR_LINE}")

    def test_raw_ansi_resets_land_on_the_background(self):
        screen = TUIScreen(20, 2)
        screen.write_raw_ansi_at(1, 1, "\x1b[32mok\x1b[0m rest\x1b[m tail")
        out = screen.get_frame(use_sync=False).decode('utf-8')
        assert f"{TUIScreen.CSI}1;1H{TUIScreen.BACKGROUND_SEQ}\x1b[32mok" in out
        assert "\x1b[0m" + TUIScreen.BACKGROUND_SEQ + " rest" in out
        assert "\x1b[m" + TUIScreen.BACKGROUND_SEQ + " tail" in out


class TestClearTerminal:
    def test_wipes_the_client_and_redraws_everything_next_frame(self):
        # A tab switch away from the Log tab: its raw ANSI lines are invisible to the diff, so
        # the terminal is cleared for real and the next frame is drawn from scratch.
        screen = TUIScreen(20, 5)
        screen.write_at(1, 1, "hi")
        screen.get_frame()
        assert screen.get_frame() == b''
        screen.clear_terminal()
        screen.write_at(1, 1, "hi")
        out = screen.get_frame(use_sync=False).decode('utf-8')
        assert out.startswith(TUIScreen.CLEAR_SCREEN + TUIScreen.CURSOR_HOME + screen.paint_background())
        assert "hi" in out

    def test_resize_clears_only_when_the_size_changes(self):
        screen = TUIScreen(20, 5)
        screen.write_at(1, 1, "hi")
        screen.get_frame()
        screen.resize(20, 5)
        assert screen.get_frame() == b''
        screen.resize(30, 6)
        assert (screen.width, screen.height) == (30, 6)
        assert len(screen.next_frame) == 6 and len(screen.next_frame[0]) == 30
        assert TUIScreen.CLEAR_SCREEN in screen.get_frame(use_sync=False).decode('utf-8')


class TestStyleToSequences:
    def test_default_style_is_reset_onto_the_background(self):
        # Never the terminal's default background: Terminal.app's profiles are not black.
        screen = TUIScreen()
        assert screen._style_to_sequences(Style()) == [TUIScreen.RESET, TUIScreen.BACKGROUND_SEQ]

    def test_style_without_background_gets_the_background(self):
        screen = TUIScreen()
        seqs = screen._style_to_sequences(Style(fg=Color(1, 2, 3)))
        assert seqs == [TUIScreen.RESET, str(Color(1, 2, 3)), TUIScreen.BACKGROUND_SEQ]

    def test_bold_appended_after_reset(self):
        screen = TUIScreen()
        seqs = screen._style_to_sequences(Style(bold=True))
        assert seqs == [TUIScreen.RESET, TUIScreen.BOLD, TUIScreen.BACKGROUND_SEQ]

    def test_all_attributes_in_order(self):
        screen = TUIScreen()
        style = Style(fg=Color(1, 2, 3), bg=Color(4, 5, 6),
                      bold=True, italic=True, underline=True)
        seqs = screen._style_to_sequences(style)
        assert seqs == [
            TUIScreen.RESET,
            TUIScreen.BOLD,
            TUIScreen.ITALIC,
            TUIScreen.UNDERLINE,
            str(Color(1, 2, 3)),
            Color(4, 5, 6).bg(),
        ]


class TestDrawProgressBar:
    def test_full_bar_all_blocks(self):
        screen = TUIScreen(20, 3)
        screen.draw_progress_bar(1, 1, 10, 1.0)
        assert row_text(screen, 1).startswith("█" * 10)

    def test_empty_bar_all_spaces(self):
        screen = TUIScreen(20, 3)
        screen.draw_progress_bar(1, 1, 10, 0.0)
        assert row_text(screen, 1)[:10] == " " * 10

    def test_half_bar_five_full_blocks(self):
        screen = TUIScreen(20, 3)
        screen.draw_progress_bar(1, 1, 10, 0.5)
        text = row_text(screen, 1)
        assert text[:5] == "█" * 5
        assert text[5:10] == " " * 5

    def test_fractional_partial_block(self):
        screen = TUIScreen(20, 3)
        # 0.55 * 10 = 5.5 -> 5 full blocks + partial block index int(0.5*8)+1 = 5 -> "▋"
        screen.draw_progress_bar(1, 1, 10, 0.55)
        text = row_text(screen, 1)
        assert text[:5] == "█" * 5
        assert text[5] == "▋"

    def test_progress_clamped_above_one(self):
        screen = TUIScreen(20, 3)
        screen.draw_progress_bar(1, 1, 10, 5.0)
        assert row_text(screen, 1).startswith("█" * 10)


class TestTintRow:
    def test_tints_only_cells_without_a_background(self):
        screen = TUIScreen(10, 2)
        screen.write_at(1, 1, "ab")
        screen.set_bg_color(Color(9, 9, 9))
        screen.write_at(3, 1, "c")
        screen.reset_color()
        screen.tint_row(1, 1, 5, Color(1, 2, 3))
        cells = screen.next_frame[0]
        assert [c.style.bg for c in cells[:6]] == [Color(1, 2, 3)] * 2 + [Color(9, 9, 9)] + [Color(1, 2, 3)] * 2 + [None]

    def test_clips_to_the_screen(self):
        screen = TUIScreen(4, 1)
        screen.tint_row(3, 1, 10, Color(1, 2, 3))
        screen.tint_row(1, 5, 2, Color(1, 2, 3))
        assert [c.style.bg for c in screen.next_frame[0]] == [None, None, Color(1, 2, 3), Color(1, 2, 3)]


class TestRecolorRow:
    def test_changes_only_the_foreground_of_the_range(self):
        screen = TUIScreen(10, 1)
        screen.bold()
        screen.set_color(Color(5, 5, 5))
        screen.set_bg_color(Color(9, 9, 9))
        screen.write_at(1, 1, "abcd")
        screen.recolor_row(2, 1, 2, Color(1, 2, 3))
        cells = screen.next_frame[0]
        assert [c.style.fg for c in cells[:4]] == [Color(5, 5, 5), Color(1, 2, 3), Color(1, 2, 3), Color(5, 5, 5)]
        assert all(c.style.bold and c.style.bg == Color(9, 9, 9) for c in cells[:4])
        assert [c.char for c in cells[:4]] == list("abcd")

    def test_bold_is_added_never_removed(self):
        screen = TUIScreen(10, 1)
        screen.write_at(1, 1, "ab")
        screen.bold()
        screen.write_at(3, 1, "cd")
        screen.recolor_row(2, 1, 2, Color(1, 2, 3), bold=True)
        screen.recolor_row(4, 1, 1, Color(1, 2, 3))
        assert [c.style.bold for c in screen.next_frame[0][:4]] == [False, True, True, True]

    def test_clips_to_the_screen(self):
        screen = TUIScreen(4, 1)
        screen.recolor_row(3, 1, 10, Color(1, 2, 3))
        screen.recolor_row(1, 5, 2, Color(1, 2, 3))
        assert [c.style.fg for c in screen.next_frame[0]] == [None, None, Color(1, 2, 3), Color(1, 2, 3)]


class TestRowText:
    def test_returns_the_cells_of_the_range(self):
        screen = TUIScreen(10, 2)
        screen.write_at(1, 1, "hello")
        assert screen.row_text(1, 1, 5) == "hello"
        assert screen.row_text(2, 1, 3) == "ell"
        assert screen.row_text(1, 1, 7) == "hello  "

    def test_clips_to_the_screen(self):
        screen = TUIScreen(4, 1)
        screen.write_at(1, 1, "abcd")
        assert screen.row_text(3, 1, 10) == "cd"
        assert screen.row_text(1, 5, 2) == ""


class TestWideGlyphs:
    """A wide glyph (emoji, CJK) takes two cells; the buffer keeps the pair whole."""

    @staticmethod
    def chars(screen: TUIScreen, y: int = 1) -> list:
        return [c.char for c in screen.next_frame[y - 1]]

    def test_a_wide_glyph_fills_two_cells(self):
        screen = TUIScreen(6, 1)
        screen.write_at(1, 1, "a\U0001F600b")
        assert self.chars(screen) == ["a", "\U0001F600", "", "b", " ", " "]
        assert screen.cursor_x == 5

    def test_the_diff_sends_the_glyph_once(self):
        screen = TUIScreen(6, 1)
        screen.write_at(1, 1, "a\U0001F600b")
        out = screen.get_frame(use_sync=False).decode('utf-8')
        assert out.count("\U0001F600") == 1 and "a\U0001F600b" in out

    def test_writing_past_the_row_end_is_clipped_not_wrapped(self):
        screen = TUIScreen(4, 2)
        screen.write_at(3, 1, "abcd")
        assert self.chars(screen, 1) == [" ", " ", "a", "b"]
        assert self.chars(screen, 2) == [" "] * 4
        assert screen.cursor_x == 5

    def test_a_wide_glyph_that_would_hang_over_the_edge_becomes_a_space(self):
        screen = TUIScreen(4, 1)
        screen.write_at(4, 1, "\U0001F600")
        assert self.chars(screen) == [" ", " ", " ", " "]

    def test_a_newline_still_moves_to_the_next_row(self):
        screen = TUIScreen(4, 2)
        screen.write_at(1, 1, "a\nb")
        assert self.chars(screen, 2)[0] == "b"

    def test_overwriting_the_head_blanks_the_tail(self):
        screen = TUIScreen(4, 1)
        screen.write_at(1, 1, "\U0001F600")
        screen.write_at(1, 1, "x")
        assert self.chars(screen) == ["x", " ", " ", " "]

    def test_overwriting_the_tail_blanks_the_head(self):
        screen = TUIScreen(4, 1)
        screen.write_at(2, 1, "\U0001F600")
        screen.write_at(3, 1, "x")
        assert self.chars(screen) == [" ", " ", "x", " "]

    def test_a_combining_mark_joins_the_glyph_before_it(self):
        screen = TUIScreen(4, 1)
        screen.write_at(1, 1, "é\U0001F600́")
        assert self.chars(screen) == ["é", "\U0001F600́", "", " "]

    def test_disagreed_sequences_are_normalized_on_the_way_in(self):
        screen = TUIScreen(4, 1)
        screen.write_at(1, 1, "☀️x")
        assert self.chars(screen) == ["☀", "x", " ", " "]

    def test_shifting_a_glyph_one_column_repaints_the_whole_pair(self):
        """Scrolling text by a column: the old head cell gets a narrow char, the glyph moves
        onto its old tail — the diff must rewrite from the old head through the new tail."""
        screen = TUIScreen(6, 1)
        screen.write_at(1, 1, "\U0001F600z")
        screen.get_frame()
        screen.write_at(1, 1, "p\U0001F600")
        out = screen.get_frame(use_sync=False).decode('utf-8')
        assert "\x1b[1;1H" in out and "p\U0001F600" in out
        assert [c.char for c in screen.front_buffer[0]] == ["p", "\U0001F600", "", " ", " ", " "]

    def test_row_text_reads_the_glyph_once(self):
        screen = TUIScreen(6, 1)
        screen.write_at(1, 1, "a\U0001F600b")
        assert screen.row_text(1, 1, 6) == "a\U0001F600b  "
