"""ChatTable: pipe tables found in a reply's text and laid out as aligned columns."""
from interfaces.console.framework.TUIScreen import Color
from interfaces.console.framework.TextWidth import display_width
from interfaces.console.widgets.ChatTable import (ALIGN_CENTER, ALIGN_LEFT, ALIGN_RIGHT, GUTTER, MIN_COLUMN,
                                                  Table, column_widths, split_blocks, table_lines)

DIM = Color(1, 2, 3)

TABLE = """| Scope | Photos | Cost |
|---|---|---|
| 3 years | ~21,000 | ~$30 |
| 5 years | ~35,000 | ~$50 |"""


def text_of(line) -> str:
    return "".join(s.text for s in line)


def lines_of(text: str, width: int = 80) -> list[str]:
    (table,) = split_blocks(text)
    return [text_of(line) for line in table_lines(table, width, DIM)]


class TestSplitBlocks:
    def test_prose_only_is_one_block(self):
        assert split_blocks("hello\n\nworld") == ["hello\n\nworld"]

    def test_table_between_prose_keeps_the_blank_lines_around_it(self):
        blocks = split_blocks(f"Totals:\n\n{TABLE}\n\nSo roughly 300 kr.")
        assert blocks[0] == "Totals:\n"
        assert isinstance(blocks[1], Table)
        assert blocks[2] == "\nSo roughly 300 kr."

    def test_table_alone(self):
        (table,) = split_blocks(TABLE)
        assert table.header
        assert table.rows == [["Scope", "Photos", "Cost"], ["3 years", "~21,000", "~$30"], ["5 years", "~35,000", "~$50"]]

    def test_a_lone_pipe_in_prose_starts_no_table(self):
        assert split_blocks("either a | or b") == ["either a | or b"]

    def test_row_without_trailing_pipe_still_counts_as_streaming_reveals_it(self):
        (table,) = split_blocks("| a | b |\n|---|---|\n| 1 | 2")
        assert table.rows == [["a", "b"], ["1", "2"]]

    def test_without_separator_there_is_no_header(self):
        (table,) = split_blocks("| a | b |\n| 1 | 2 |")
        assert not table.header
        assert len(table.rows) == 2

    def test_separator_alignment_markers(self):
        (table,) = split_blocks("| a | b | c |\n|:---|:---:|---:|\n| 1 | 2 | 3 |")
        assert table.aligns == [ALIGN_LEFT, ALIGN_CENTER, ALIGN_RIGHT]

    def test_escaped_pipe_stays_in_the_cell(self):
        (table,) = split_blocks("| a \\| b | c |")
        assert table.rows == [["a | b", "c"]]

    def test_indented_row_counts(self):
        (table,) = split_blocks("  | a | b |")
        assert table.rows == [["a", "b"]]

    def test_two_tables_with_a_blank_line_between(self):
        blocks = split_blocks("| a |\n\n| b |")
        assert [type(b) for b in blocks] == [Table, str, Table]
        assert blocks[1] == ""

    def test_pipes_inside_a_pre_block_start_no_table(self):
        text = "Before\n<pre>| id | name |\n|----|------|\n| 1  | ann  |</pre>\nAfter"
        assert split_blocks(text) == [text]

    def test_a_table_after_a_pre_block_is_still_a_table(self):
        blocks = split_blocks("<pre>| x</pre>\n| a | b |\n|---|---|\n| 1 | 2 |")
        assert blocks[0] == "<pre>| x</pre>" and isinstance(blocks[1], Table) and blocks[1].header

    def test_an_unclosed_pre_keeps_its_lines_prose_while_streaming(self):
        text = "<pre>\n| a | b |\n| 1 |"
        assert split_blocks(text) == [text]

    def test_pre_with_code_class_and_uppercase_tag_counts(self):
        text = '<PRE><code class="language-sh">| grep foo\n| sort</code></PRE>'
        assert split_blocks(text) == [text]


class TestColumnWidths:
    def test_natural_widths_when_they_fit(self):
        assert column_widths([17, 13, 33], 70) == [17, 13, 33]

    def test_widest_columns_shrink_first_to_fit_the_gutters(self):
        widths = column_widths([17, 13, 33], 37)
        assert sum(widths) + 2 * len(GUTTER) == 37
        assert widths[2] <= widths[0] + 1 and widths[0] >= MIN_COLUMN

    def test_past_the_floor_every_column_shrinks_so_the_table_never_overflows(self):
        widths = column_widths([20, 20, 20], 10)
        assert sum(widths) + 2 * len(GUTTER) <= 10
        assert all(w >= 1 for w in widths)


class TestTableLines:
    def test_columns_align_with_the_header_bold_above_a_rule(self):
        (table,) = split_blocks(TABLE)
        lines = table_lines(table, 80, DIM)
        texts = [text_of(l) for l in lines]
        assert texts == [
            "Scope   │ Photos  │ Cost",
            "────────┼─────────┼─────",
            "3 years │ ~21,000 │ ~$30",
            "5 years │ ~35,000 │ ~$50",
        ]
        assert all(s.bold for s in lines[0] if s.text.strip() and s.text != GUTTER)
        assert lines[1][0].fg == DIM
        assert not any(s.bold for s in lines[2])

    def test_every_line_is_the_same_width(self):
        texts = lines_of(TABLE)
        assert len({display_width(t) for t in texts}) == 1

    def test_gutters_are_dim_and_cell_markup_keeps_its_flags(self):
        (table,) = split_blocks("| a | b |\n|---|---|\n| <b>bold</b> | <code>x</code> |")
        lines = table_lines(table, 80, DIM)
        row = lines[2]
        assert [s.fg for s in row if s.text == GUTTER] == [DIM]
        assert next(s for s in row if s.text == "bold").bold
        assert next(s for s in row if s.text == "x").code

    def test_wide_glyph_keeps_the_column_aligned(self):
        texts = lines_of("| a | b |\n|---|---|\n| 🎉 | 1 |\n| xy | 2 |")
        assert len({display_width(t) for t in texts}) == 1
        assert texts[2].startswith("🎉 │") and texts[3].startswith("xy │")

    def test_right_and_centre_alignment(self):
        texts = lines_of("| a | b |\n|---:|:---:|\n| 1 | 2 |\n| 1000 | 2000 |")
        assert texts[2] == "   1 │  2  "
        assert texts[3] == "1000 │ 2000"

    def test_too_wide_a_table_wraps_cells_inside_their_columns(self):
        text = "| Scope | Note |\n|---|---|\n| 3 years | a note long enough to need wrapping |"
        texts = lines_of(text, 30)
        assert all(display_width(t) <= 30 for t in texts)
        assert len(texts) > 3   # the note took more than one line
        assert texts[2].startswith("3 years │ a note")
        assert texts[3].startswith("        │ ")

    def test_short_rows_are_padded_with_empty_cells(self):
        texts = lines_of("| a | b | c |\n|---|---|---|\n| 1 |")
        assert texts[2] == "1 │   │  "

    def test_empty_table_gives_no_lines(self):
        assert table_lines(Table([]), 80, DIM) == []
