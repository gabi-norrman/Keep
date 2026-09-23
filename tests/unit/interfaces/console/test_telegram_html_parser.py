from interfaces.console.tabs.TelegramHtmlParser import (
    StyledSpan, parse, split_paragraphs, wrap_spans,
)


def _flat_text(spans):
    return "".join(s.text for s in spans)


class TestParseInlineTags:
    def test_plain_text_no_tags(self):
        spans = parse("hello world")
        assert len(spans) == 1
        assert spans[0].text == "hello world"
        assert not any([spans[0].bold, spans[0].italic, spans[0].underline, spans[0].strike,
                        spans[0].code, spans[0].pre, spans[0].quote, spans[0].spoiler])

    def test_bold_b_and_strong(self):
        for tag in ("b", "strong"):
            spans = parse(f"<{tag}>x</{tag}>")
            assert spans[0].text == "x" and spans[0].bold

    def test_italic_i_and_em(self):
        for tag in ("i", "em"):
            spans = parse(f"<{tag}>x</{tag}>")
            assert spans[0].text == "x" and spans[0].italic

    def test_underline_u_and_ins(self):
        for tag in ("u", "ins"):
            spans = parse(f"<{tag}>x</{tag}>")
            assert spans[0].text == "x" and spans[0].underline

    def test_strike_s_strike_del(self):
        for tag in ("s", "strike", "del"):
            spans = parse(f"<{tag}>x</{tag}>")
            assert spans[0].text == "x" and spans[0].strike

    def test_code(self):
        spans = parse("<code>x = 1</code>")
        assert spans[0].text == "x = 1" and spans[0].code

    def test_pre(self):
        spans = parse("<pre>print(1)</pre>")
        assert spans[0].text == "print(1)" and spans[0].pre

    def test_pre_code_combo(self):
        spans = parse('<pre><code class="language-python">x</code></pre>')
        # All inner text should carry both pre and code flags.
        for s in spans:
            assert s.pre and s.code
            assert s.text == "x"

    def test_blockquote(self):
        spans = parse("<blockquote>cite</blockquote>")
        assert spans[0].text == "cite" and spans[0].quote

    def test_tg_spoiler_tag(self):
        spans = parse("<tg-spoiler>secret</tg-spoiler>")
        assert spans[0].text == "secret" and spans[0].spoiler

    def test_span_class_is_carried_on_the_span(self):
        spans = parse('<span class="short">[A shape]</span><span class="description"> a cube</span> tail')
        assert [(s.text, s.cls) for s in spans] == [("[A shape]", "short"), (" a cube", "description"),
                                                    (" tail", None)]
        assert not spans[0].spoiler

    def test_span_class_spoiler(self):
        spans = parse('<span class="tg-spoiler">secret</span>')
        assert spans[0].text == "secret" and spans[0].spoiler

    def test_a_href(self):
        spans = parse('<a href="https://example.com">click</a>')
        assert spans[0].text == "click" and spans[0].href == "https://example.com"

class TestEntities:
    def test_basic_entities(self):
        spans = parse("a &amp; b &lt; c &gt; d &quot;e&quot;")
        assert _flat_text(spans) == 'a & b < c > d "e"'

class TestNesting:
    def test_bold_italic_nested(self):
        spans = parse("<b><i>x</i></b>")
        assert spans[0].text == "x"
        assert spans[0].bold and spans[0].italic

    def test_nested_same_tag_counter(self):
        # <b><b>x</b>y</b>: y should still be bold after the inner </b>.
        spans = parse("<b><b>x</b>y</b>")
        # Concat all spans, all should be bold.
        for s in spans:
            assert s.bold
        assert _flat_text(spans) == "xy"

    def test_partial_styling_in_run(self):
        spans = parse("plain <b>bold</b> tail")
        joined = _flat_text(spans)
        assert joined == "plain bold tail"
        # Find which span is bold.
        bolds = [s for s in spans if s.bold]
        assert len(bolds) == 1 and bolds[0].text == "bold"


class TestUnknownAndMalformed:
    def test_unknown_tag_stripped(self):
        spans = parse("<marquee>hello</marquee>")
        assert _flat_text(spans) == "hello"
        assert not any(s.bold or s.italic for s in spans)

    def test_unclosed_tag_does_not_raise(self):
        # No </b> — close() should auto-undo. Should not raise.
        spans = parse("<b>x")
        assert spans[0].text == "x" and spans[0].bold

    def test_unmatched_end_tag_ignored(self):
        spans = parse("hello</b>")
        # </b> with no opener — ignored quietly.
        assert _flat_text(spans) == "hello"

    def test_interleaved_tags_unwind(self):
        # Malformed: <b><i>x</b>y</i>. Parser pops to matching </b>, undoing both.
        spans = parse("<b><i>x</b>y</i>")
        # We just want it to not crash and emit the text.
        assert _flat_text(spans) == "xy"


class TestSplitParagraphs:
    def test_double_newline_creates_blank_para(self):
        spans = [StyledSpan(text="a\n\nb")]
        paras = split_paragraphs(spans)
        # 'a', '', 'b'
        assert [_flat_text(p) for p in paras] == ["a", "", "b"]

    def test_newline_inside_styled_span_preserves_style(self):
        spans = [StyledSpan(text="a\nb", bold=True)]
        paras = split_paragraphs(spans)
        assert len(paras) == 2
        assert paras[0][0].text == "a" and paras[0][0].bold
        assert paras[1][0].text == "b" and paras[1][0].bold


class TestWrapSpans:
    def test_word_wrap_at_width(self):
        spans = [StyledSpan(text="aaa bbb ccc ddd")]
        lines = wrap_spans(spans, 7)
        # 7-wide lines: "aaa bbb" then "ccc ddd"
        assert [_flat_text(l) for l in lines] == ["aaa bbb", "ccc ddd"]

    def test_long_word_hard_split(self):
        spans = [StyledSpan(text="a" * 25)]
        lines = wrap_spans(spans, 10)
        assert [_flat_text(l) for l in lines] == ["a" * 10, "a" * 10, "a" * 5]

    def test_paragraph_break_on_double_newline(self):
        spans = [StyledSpan(text="line one\n\nline two")]
        lines = wrap_spans(spans, 80)
        # paragraph1, blank, paragraph2
        assert [_flat_text(l) for l in lines] == ["line one", "", "line two"]

    def test_style_preserved_across_wrap(self):
        spans = [
            StyledSpan(text="plain "),
            StyledSpan(text="bold text here", bold=True),
            StyledSpan(text=" tail"),
        ]
        lines = wrap_spans(spans, 12)
        # The originally bold non-space words must still be bold somewhere in output.
        bold_text_concat = "".join(s.text for line in lines for s in line if s.bold)
        for word in ("bold", "text", "here"):
            assert word in bold_text_concat
        # Originally non-bold "plain" and "tail" must NOT appear in any bold span.
        assert "plain" not in bold_text_concat
        assert "tail" not in bold_text_concat

    def test_empty_input(self):
        assert wrap_spans([], 80) == [[]]

class TestWrapSpansWidth:
    def test_wide_glyphs_count_two_cells(self):
        lines = wrap_spans([StyledSpan(text="\U0001F600\U0001F600 ab")], 4)
        assert ["".join(s.text for s in line) for line in lines] == ["\U0001F600\U0001F600", "ab"]

    def test_a_long_word_of_glyphs_is_split_between_glyphs(self):
        lines = wrap_spans([StyledSpan(text="\U0001F600" * 3)], 3)
        assert ["".join(s.text for s in line) for line in lines] == ["\U0001F600", "\U0001F600", "\U0001F600"]

    def test_selector_sequences_are_normalized(self):
        lines = wrap_spans([StyledSpan(text="☀️")], 10)
        assert lines[0][0].text == "☀"


def test_the_space_before_a_link_keeps_the_style_it_had_in_the_source():
    html = ('Store link: <a href="https://www.apple.com/se/x">apple.com/se/x</a> — the rest, '
            'and <u>an underlined phrase</u> too')
    (line,) = wrap_spans(parse(html), 200)
    assert [(s.text, s.href, s.underline) for s in line] == [
        ("Store link: ", None, False),
        ("apple.com/se/x", "https://www.apple.com/se/x", False),
        (" — the rest, and ", None, False),
        ("an underlined phrase", None, True),
        (" too", None, False),
    ]


class TestPreBlocks:
    def test_newlines_framing_the_block_belong_to_the_markup(self):
        spans = parse("before\n<pre>\nx = 1\n</pre>\nafter")
        assert [s.text for s in spans] == ["before\n", "x = 1", "\nafter"]

    def test_runs_of_spaces_survive_the_wrap(self):
        spans = parse("<pre>│ a   │  1 │\n│ bb  │ 22 │</pre>")
        lines = wrap_spans(spans, 80)
        assert ["".join(s.text for s in l) for l in lines] == ["│ a   │  1 │", "│ bb  │ 22 │"]

    def test_a_line_too_long_is_cut_at_the_width_not_reflowed(self):
        spans = parse("<pre>┌──────────┐ tail</pre>")
        lines = wrap_spans(spans, 12)
        assert ["".join(s.text for s in l) for l in lines] == ["┌──────────┐", " tail"]

    def test_a_wide_glyph_is_never_split_by_the_cut(self):
        spans = parse("<pre>ab🎉cd</pre>")
        lines = wrap_spans(spans, 3)
        assert ["".join(s.text for s in l) for l in lines] == ["ab", "🎉c", "d"]

    def test_prose_still_collapses_spaces(self):
        lines = wrap_spans(parse("a    b"), 80)
        assert "".join(s.text for s in lines[0]) == "a b"
