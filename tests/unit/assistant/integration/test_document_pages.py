"""DocumentPages: the paging that keeps a page under the history's cap and cuts at a boundary, and
the find that says which page (and PDF page or slide) a phrase is on."""
import pytest

from assistant.integration.DocumentPages import PAGE_CHARS, find, pages, snippet


def paragraphs(count: int, words: int = 60) -> str:
    return "\n\n".join(f"Paragraph {i}: " + " ".join(f"w{i}-{j}" for j in range(words)) for i in range(1, count + 1))


@pytest.mark.unit
class TestPages:
    def test_a_short_text_is_one_page_and_an_empty_one_still_a_page(self):
        assert pages("hello") == ["hello"] and pages("  \n") == [""]

    def test_every_page_is_under_the_limit_and_cut_at_a_paragraph_boundary(self):
        text = paragraphs(60)
        out = pages(text)

        assert len(out) > 1 and all(len(p) <= PAGE_CHARS for p in out)
        for page in out:
            assert page.startswith("Paragraph ") and not page.endswith("-")
        assert "\n\n".join(out).replace("\n\n", " ").split() == text.replace("\n\n", " ").split(), "nothing is lost"

    def test_a_text_without_blank_lines_is_cut_at_a_line_end(self):
        text = "\n".join(f"line {i} " + "x" * 80 for i in range(200))
        out = pages(text, limit=1000)
        assert all(len(p) <= 1000 for p in out) and all(p.endswith("x") for p in out[:-1])

    def test_one_endless_word_is_cut_hard_rather_than_never(self):
        assert [len(p) for p in pages("a" * 2500, limit=1000)] == [1000, 1000, 500]


@pytest.mark.unit
class TestFind:
    TEXT = "[p. 1]\nThe lease begins in March.\nA deposit of two months is payable.\n\n[p. 2]\nThe deposit is returned within 30 days.\nSigned."

    def test_hits_carry_the_page_the_pdf_page_and_the_line(self):
        hits = find(self.TEXT, ["deposit"])
        assert [(h.page, h.marker, h.line) for h in hits] == [
            (1, "[p. 1]", "A deposit of two months is payable."),
            (1, "[p. 2]", "The deposit is returned within 30 days."),
        ]

    def test_a_sheet_marker_carries_the_tab_title_and_is_never_a_hit_itself(self):
        hits = find("[sheet Budget 2026]\nBudget line\n", ["budget"])
        assert [(h.marker, h.line) for h in hits] == [("[sheet Budget 2026]", "Budget line")]

    def test_every_word_must_be_on_the_line_case_aside(self):
        assert [h.line for h in find(self.TEXT, ["DEPOSIT", "returned"])] == ["The deposit is returned within 30 days."]
        assert find(self.TEXT, ["deposit", "march"]) == []

    def test_the_page_number_is_the_page_of_pages(self):
        text = paragraphs(60)
        hits = find(text, ["Paragraph 60:"])
        assert len(hits) == 1 and hits[0].page == len(pages(text)) and hits[0].marker == ""

    def test_the_hits_are_capped(self):
        text = "\n".join(f"needle {i}" for i in range(50))
        assert len(find(text, ["needle"], limit=20)) == 20

    def test_no_words_means_no_hits(self):
        assert find(self.TEXT, ["", "  "]) == []

    def test_a_long_line_is_shown_around_the_word(self):
        line = "x" * 300 + " deposit " + "y" * 300
        shown = snippet(line, "deposit", limit=100)
        assert "deposit" in shown and len(shown) <= 102 and shown.startswith("…") and shown.endswith("…")
        assert snippet("short", "short") == "short"
