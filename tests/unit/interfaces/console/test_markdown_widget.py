"""widgets/Markdown: markdown laid out as rows of styled glyphs — no marker survives, every
line is wrapped, the sections take accents in turn, and a glyph remembers where in its source
line it came from so a diff can light the laid-out rows."""
import pytest

from interfaces.console.framework.TUIScreen import Color, TUIScreen
from interfaces.console.widgets import Markdown
from interfaces.console.widgets.Markdown import cells, draw_row, inline, layout, plain, text_of, wrap

WIDTH = 40


def texts(doc) -> list[str]:
    return [text_of(row.glyphs) for row in doc.rows]


def glyph(glyphs, char: str):
    return next(g for g in glyphs if g.char == char)


@pytest.mark.unit
class TestInline:
    def test_bold_loses_its_marks_and_only_the_marked_word_is_bold(self):
        glyphs = inline("a **b** c")
        assert text_of(glyphs) == "a b c"
        assert glyph(glyphs, "b").ink.bold
        assert not glyph(glyphs, "a").ink.bold and not glyph(glyphs, "c").ink.bold

    def test_a_glyph_knows_its_place_in_the_source_line(self):
        glyphs = inline("a **b** `c`", offset=10)
        assert [(g.char, g.src) for g in glyphs if g.char in "abc"] == [("a", 10), ("b", 14), ("c", 19)]

    def test_code_is_set_apart_by_colour_and_keeps_what_is_inside_literal(self):
        glyphs = inline("run `my_var * 2` now")
        assert text_of(glyphs) == "run my_var * 2 now"
        assert glyph(glyphs, "m").ink.color != glyph(glyphs, "r").ink.color
        assert not any(g.ink.italic for g in glyphs)

    @pytest.mark.parametrize("text", ["*hi* there", "_hi_ there"])
    def test_emphasis_is_italic_without_its_marks(self, text):
        glyphs = inline(text)
        assert text_of(glyphs) == "hi there"
        assert glyph(glyphs, "i").ink.italic and not glyph(glyphs, "t").ink.italic

    @pytest.mark.parametrize("text", ["use my_var_name here", "2 * 3 * 4", "a * b", "__init__", "**unclosed",
                                      "5 * 3*", "~~unclosed", "`unclosed", "[not a link]"])
    def test_what_is_not_a_mark_is_left_as_written(self, text):
        glyphs = inline(text)
        assert text_of(glyphs) == text
        assert not any(g.ink.italic or g.ink.bold or g.ink.underline for g in glyphs)

    def test_struck_text_is_dimmed_out_of_the_running_text(self):
        glyphs = inline("~~old~~ new")
        assert text_of(glyphs) == "old new"
        assert glyph(glyphs, "o").ink.color != glyph(glyphs, "n").ink.color

    def test_a_link_shows_its_text_and_carries_its_target(self):
        glyphs = inline("see [the shop](https://shop.example/cart) now")
        assert text_of(glyphs) == "see the shop now"
        assert {g.ink.href for g in glyphs if g.char in "thop"} >= {"https://shop.example/cart"}
        assert glyph(glyphs, "n").ink.href is None

    def test_a_bare_url_is_a_link_without_the_sentence_punctuation_after_it(self):
        glyphs = inline("at https://shop.example/cart.")
        assert text_of(glyphs) == "at https://shop.example/cart."
        assert glyphs[3].ink.href == "https://shop.example/cart"
        assert glyphs[-1].ink.href is None

    def test_an_escaped_mark_is_drawn_and_marks_nothing(self):
        glyphs = inline(r"\*not\* so")
        assert text_of(glyphs) == "*not* so"
        assert not any(g.ink.italic for g in glyphs)

    def test_bold_inside_bold_text_keeps_the_colour_it_stands_in(self):
        base = Markdown.Ink(Color(1, 2, 3), bold=True)
        glyphs = inline("a **b**", base)
        assert glyph(glyphs, "b").ink == glyph(glyphs, "a").ink == base


@pytest.mark.unit
class TestWrap:
    def rows(self, text: str, width: int) -> list[str]:
        return [text_of(row) for row in wrap(plain(text, Markdown.NORMAL), width)]

    def test_rows_break_at_spaces(self):
        assert self.rows("aaa bbb ccc", 7) == ["aaa bbb", "ccc"]

    def test_a_space_at_the_edge_is_where_the_row_breaks(self):
        assert self.rows("aaaa bbbb", 4) == ["aaaa", "bbbb"]

    def test_a_word_longer_than_a_row_is_cut(self):
        assert self.rows("abcdefghij", 4) == ["abcd", "efgh", "ij"]

    def test_a_long_word_after_short_ones_keeps_every_character(self):
        rows = self.rows("to abcdefghijkl end", 5)
        assert all(len(row) <= 5 for row in rows)
        assert "".join(rows).replace(" ", "") == "toabcdefghijklend"

    def test_a_wide_glyph_counts_two_cells(self):
        rows = wrap(plain("好好好好", Markdown.NORMAL), 5)
        assert [text_of(row) for row in rows] == ["好好", "好好"]
        assert all(cells(row) <= 5 for row in rows)

    def test_text_that_fits_is_one_row(self):
        assert self.rows("fits", 10) == ["fits"]


@pytest.mark.unit
class TestHeadings:
    def test_a_heading_is_drawn_without_its_hashes_over_a_rule(self):
        doc = layout(["# The Family", "Marcus likes tea."], WIDTH)
        rows = texts(doc)
        assert rows[0] == "The Family"
        assert set(rows[1]) == {Markdown.RULE_CHAR} and len(rows[1]) == WIDTH
        assert rows[2] == "Marcus likes tea."
        assert not any("#" in row for row in rows)

    def test_closing_hashes_are_dropped_too(self):
        assert texts(layout(["## Contacts ##"], WIDTH))[0].strip("▌ ") == "Contacts"

    def test_each_section_takes_the_next_accent_and_its_bullets_carry_it(self):
        doc = layout(["# A", "- one", "# B", "- two"], WIDTH)
        first, second = doc.headings
        assert first.accent != second.accent
        bullets = [row.glyphs[0].ink.color for row in doc.rows if row.glyphs and row.glyphs[0].char == "•"]
        assert bullets == [first.accent, second.accent]

    def test_the_sections_are_the_shallowest_level_that_repeats(self):
        doc = layout(["# Bank", "## People", "### Marcus", "## Rules"], WIDTH)
        assert [(h.title, h.depth) for h in doc.headings] == [("Bank", -1), ("People", 0), ("Marcus", 1), ("Rules", 0)]

    def test_without_a_repeated_level_the_shallowest_one_divides(self):
        doc = layout(["## Only", "### Part"], WIDTH)
        assert [(h.title, h.depth) for h in doc.headings] == [("Only", 0), ("Part", 1)]

    def test_a_heading_records_the_row_it_landed_on(self):
        doc = layout(["intro", "", "# A", "text", "", "# B"], WIDTH)
        for heading in doc.headings:
            assert text_of(doc.rows[heading.row].glyphs) == heading.title

    def test_a_section_runs_to_the_next_heading_at_its_level_or_above(self):
        lines = ["# A", "one", "## A1", "two", "# B", "three"]
        a, a1, b = layout(lines, WIDTH).headings
        assert (a.line, a.until) == (0, 4) and (a1.line, a1.until) == (2, 4) and (b.line, b.until) == (4, 6)
        assert a.size == sum(len(line) for line in lines[:4])
        assert a1.size == len("## A1") + len("two")

    def test_a_title_in_the_outline_has_no_inline_marks(self):
        assert layout(["# The **big** `plan`"], WIDTH).headings[0].title == "The big plan"

    def test_blank_lines_around_headings_do_not_pile_up(self):
        rows = texts(layout(["# A", "", "", "## Part", "", "text", "", "", "", "more"], WIDTH))
        assert rows[2].endswith("Part")                   # right under the section's rule
        assert rows[3:] == ["text", "", "more"]

    def test_a_hash_line_inside_a_fence_is_code_not_a_heading(self):
        doc = layout(["```", "# not a heading", "```"], WIDTH)
        assert doc.headings == []
        assert texts(doc) == [" # not a heading"]

    def test_a_long_heading_wraps_instead_of_running_off(self):
        doc = layout(["# " + "word " * 20], 20)
        assert all(cells(row.glyphs) <= 20 for row in doc.rows)
        assert " ".join(texts(doc)[:-1]).split() == ["word"] * 20


@pytest.mark.unit
class TestBlocks:
    def test_a_bullet_becomes_a_dot_with_a_hanging_indent(self):
        rows = texts(layout(["- " + "word " * 12], 20))
        assert rows[0].startswith("• word")
        assert len(rows) > 1 and all(row.startswith("  word") for row in rows[1:])
        assert all(len(row) <= 20 for row in rows)

    def test_a_nested_bullet_is_indented_under_its_parent_with_another_mark(self):
        parent, child = texts(layout(["- tea", "  - Earl Grey"], WIDTH))
        assert parent == "• tea"
        assert child.startswith("  ") and child.endswith(" Earl Grey") and "•" not in child and "-" not in child

    @pytest.mark.parametrize("mark", ["-", "*", "+"])
    def test_every_bullet_mark_is_a_bullet(self, mark):
        assert texts(layout([f"{mark} tea"], WIDTH)) == ["• tea"]

    def test_a_task_shows_whether_it_is_done(self):
        done, todo = layout(["- [x] Passport renewed", "- [ ] Driving licence"], WIDTH).rows
        assert text_of(done.glyphs)[2:] == "Passport renewed" and text_of(todo.glyphs)[2:] == "Driving licence"
        assert done.glyphs[0].char != todo.glyphs[0].char
        assert "[" not in text_of(done.glyphs) + text_of(todo.glyphs)

    def test_a_label_at_the_head_of_a_bullet_stands_out_from_its_value(self):
        glyphs = layout(["- Birthday: 3 February"], WIDTH).rows[0].glyphs
        assert glyph(glyphs, "B").ink != glyph(glyphs, "F").ink
        assert glyph(glyphs, "F").ink == Markdown.NORMAL

    def test_a_time_at_the_head_of_a_bullet_is_not_a_label(self):
        glyphs = layout(["- 18:30 dinner"], WIDTH).rows[0].glyphs
        assert {g.ink for g in glyphs[2:]} == {Markdown.NORMAL}

    def test_a_numbered_item_keeps_its_number_and_hangs_under_its_text(self):
        rows = texts(layout(["12. " + "word " * 12], 20))
        assert rows[0].startswith("12. word")
        assert all(row.startswith("    word") for row in rows[1:])

    def test_a_quote_gets_a_bar_instead_of_its_angle(self):
        row = layout(["> never before seven"], WIDTH).rows[0]
        assert text_of(row.glyphs).endswith(" never before seven") and ">" not in text_of(row.glyphs)
        assert glyph(row.glyphs, "n").ink.italic

    @pytest.mark.parametrize("line", ["---", "***", "* * *", "_____"])
    def test_a_rule_spans_the_width(self, line):
        rows = texts(layout(["a", line, "b"], WIDTH))
        assert len(rows) == 3 and len(set(rows[1])) == 1 and len(rows[1]) == WIDTH
        assert rows[1][0] not in "-*_•"

    def test_a_fence_is_a_plate_without_its_fence_lines(self):
        doc = layout(["before", "```python", "x = **1**", "", "y = 2", "```", "after"], WIDTH)
        assert texts(doc) == ["before", " x = **1**", " ", " y = 2", "after"]
        assert [row.fill is not None for row in doc.rows] == [False, True, True, True, False]

    def test_a_long_code_line_is_cut_not_dropped(self):
        doc = layout(["```", "x" * 50, "```"], 20)
        assert "".join(texts(doc)).replace(" ", "") == "x" * 50
        assert all(cells(row.glyphs) <= 20 for row in doc.rows)

    def test_a_paragraph_keeps_its_indent_on_every_row(self):
        rows = texts(layout(["    " + "word " * 10], 20))
        assert len(rows) > 1 and all(row.startswith("    word") for row in rows)

    def test_a_tab_does_not_survive_into_a_row(self):
        assert "\t" not in "".join(texts(layout(["\t- tea\twith milk"], WIDTH)))


@pytest.mark.unit
class TestTables:
    LINES = ["| Who | What |", "|-----|:----:|", "| Alice | **plumber** |", "| Bob | |"]

    def test_columns_line_up_and_no_pipe_is_left(self):
        head, rule, alice, bob = texts(layout(self.LINES, WIDTH))
        assert "|" not in head + rule + alice + bob and "*" not in alice
        assert head.split("│")[0].strip() == "Who" and alice.split("│")[1].strip() == "plumber"
        assert head.index("│") == alice.index("│") == bob.index("│") == rule.index("┼")
        assert len(head) == len(alice) == len(bob) == len(rule)

    def test_the_header_row_is_set_apart(self):
        head, _, alice, _ = layout(self.LINES, WIDTH).rows
        assert glyph(head.glyphs, "W").ink.bold and not glyph(alice.glyphs, "A").ink.bold

    def test_a_wide_cell_is_cut_to_keep_the_table_inside_the_width(self):
        rows = layout(["| a | " + "x" * 60 + " |", "| b | short |"], 24).rows
        assert all(cells(row.glyphs) <= 24 for row in rows)
        assert "…" in text_of(rows[0].glyphs) and "short" in text_of(rows[1].glyphs)

    def test_a_cell_knows_its_place_in_the_source_line(self):
        line = "| Alice | plumber |"
        glyphs = layout([line], WIDTH).rows[0].glyphs
        assert glyph(glyphs, "p").src == line.index("plumber")


@pytest.mark.unit
class TestRemovedLines:
    """A diff's removed lines are drawn but are no part of the document's structure."""

    def test_a_removed_heading_is_drawn_but_not_in_the_outline(self):
        doc = layout(["# A", "# Old", "text", "# B"], WIDTH, removed=frozenset({1}))
        assert [h.title for h in doc.headings] == ["A", "B"]
        assert "Old" in texts(doc)

    def test_a_removed_heading_takes_no_accent_from_the_sections(self):
        with_removed = layout(["# A", "# Old", "# B"], WIDTH, removed=frozenset({1}))
        without = layout(["# A", "# B"], WIDTH)
        assert [h.accent for h in with_removed.headings] == [h.accent for h in without.headings]

    def test_a_removed_heading_does_not_decide_what_the_sections_are(self):
        doc = layout(["# Bank", "# Old", "## A", "## B"], WIDTH, removed=frozenset({1}))
        assert [(h.title, h.depth) for h in doc.headings] == [("Bank", -1), ("A", 0), ("B", 0)]

    def test_a_removed_fence_opens_no_code_block(self):
        doc = layout(["```", "- tea"], WIDTH, removed=frozenset({0}))
        assert texts(doc)[-1] == "• tea" and doc.rows[-1].fill is None

    def test_removed_lines_do_not_count_towards_a_sections_size(self):
        doc = layout(["# A", "gone gone gone", "kept"], WIDTH, removed=frozenset({1}))
        assert doc.headings[0].size == len("# A") + len("kept")

    def test_every_row_names_the_line_it_renders(self):
        doc = layout(["# A", "- " + "word " * 20, "", "end"], 20)
        assert {row.line for row in doc.rows if "word" in text_of(row.glyphs)} == {1}
        assert doc.rows[-1].line == 3


@pytest.mark.unit
class TestBands:
    def test_a_band_is_a_filled_strip_before_its_line(self):
        doc = layout(["# A", "curated", "note"], WIDTH, bands={2: ("New notes", "1 note since")})
        rows = texts(doc)
        band = next(k for k, row in enumerate(doc.rows) if "New notes" in rows[k])
        assert "1 note since" in rows[band] and doc.rows[band].fill is not None
        assert rows.index("curated") < band < rows.index("note")

    def test_a_band_is_in_the_outline_under_its_title_alone_and_ends_the_section_before_it(self):
        doc = layout(["# A", "curated", "note one", "note two"], WIDTH, bands={2: ("New notes", "2 notes")})
        section, band = doc.headings
        assert band.title == "New notes" and band.depth == 0
        assert text_of(doc.rows[band.row].glyphs).strip().startswith("New notes")
        assert section.until == 2 and (band.line, band.until) == (2, 4)
        assert band.size == len("note one") + len("note two")

    def test_a_band_without_a_detail_shows_its_title_only(self):
        doc = layout(["note"], WIDTH, bands={0: ("New notes", "")})
        assert texts(doc)[0].strip() == "New notes"


@pytest.mark.unit
class TestDrawRow:
    LIT = Color(9, 9, 9)

    def screen(self) -> TUIScreen:
        return TUIScreen(WIDTH, 3)

    def cell(self, screen, x):
        return screen.next_frame[0][x - 1]

    def test_the_row_is_written_where_asked_in_its_inks(self):
        row = layout(["plain **bold**"], WIDTH).rows[0]
        screen = self.screen()
        draw_row(screen, 3, 1, 30, row)
        assert screen.row_text(3, 1, 30).rstrip() == "plain bold"
        assert self.cell(screen, 9).style.bold and not self.cell(screen, 3).style.bold
        assert self.cell(screen, 3).style.fg == Markdown.COLOR_NORMAL

    def test_a_lit_source_range_lands_on_the_glyphs_it_became(self):
        line = "a **bold** z"
        row = layout([line], WIDTH).rows[0]
        screen = self.screen()
        start = line.index("bold")
        draw_row(screen, 1, 1, WIDTH, row, lit=[(start, start + 4)], lit_bg=self.LIT)
        lit = [x for x in range(1, WIDTH + 1) if self.cell(screen, x).style.bg == self.LIT]
        assert "".join(self.cell(screen, x).char for x in lit) == "bold"

    def test_what_the_layout_added_is_never_lit(self):
        row = layout(["- tea"], WIDTH).rows[0]
        screen = self.screen()
        draw_row(screen, 1, 1, WIDTH, row, lit=[(0, 99)], lit_bg=self.LIT)
        assert self.cell(screen, 1).style.bg != self.LIT and self.cell(screen, 3).style.bg == self.LIT

    def test_a_tint_recolours_every_glyph(self):
        row = layout(["- **Marcus** likes `tea`"], WIDTH).rows[0]
        screen = self.screen()
        tinted = Color(1, 2, 3)
        draw_row(screen, 1, 1, WIDTH, row, tint=lambda color: tinted)
        assert {self.cell(screen, x).style.fg for x in range(1, cells(row.glyphs) + 1)} == {tinted}

    def test_the_row_is_cut_at_the_width(self):
        row = layout(["hello world"], WIDTH).rows[0]
        screen = self.screen()
        draw_row(screen, 1, 1, 5, row)
        assert screen.row_text(1, 1, WIDTH).rstrip() == "hello"

    def test_a_wide_glyph_that_does_not_fit_is_left_out_whole(self):
        row = layout(["ab好"], WIDTH).rows[0]
        screen = self.screen()
        draw_row(screen, 1, 1, 3, row)
        assert screen.row_text(1, 1, WIDTH).rstrip() == "ab"

    def test_a_filled_row_is_filled_to_the_width_unless_told_not_to(self):
        row = layout(["```", "x = 1", "```"], WIDTH).rows[0]
        filled, bare = self.screen(), self.screen()
        draw_row(filled, 1, 1, 20, row)
        draw_row(bare, 1, 1, 20, row, fill=False)
        assert {self.cell(filled, x).style.bg for x in range(1, 21)} == {row.fill}
        assert self.cell(filled, 21).style.bg is None
        assert {self.cell(bare, x).style.bg for x in range(1, 21)} == {None}

    def test_a_link_is_clickable_on_screen(self):
        row = layout(["[shop](https://shop.example)"], WIDTH).rows[0]
        screen = self.screen()
        draw_row(screen, 1, 1, WIDTH, row)
        assert self.cell(screen, 1).style.href == "https://shop.example"
        assert self.cell(screen, 6).style.href is None
