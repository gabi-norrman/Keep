"""open_url and browse_extract hand a long page back in pages: a text that fits comes back
whole and unheaded, a longer one is cut on line breaks, headed with its number and the
count, and a later page is served from the kept text without a second fetch."""
from unittest.mock import patch

import pytest

from assistant.tools.WebAccess import WebAccess


def _wa():
    return WebAccess.__new__(WebAccess)


def _long_text(lines: int = 3000) -> str:
    return "\n".join(f"line {i} " + "x" * 20 for i in range(lines))


class TestSplitPages:
    def test_a_text_that_fits_is_one_page(self):
        assert WebAccess.split_pages("short") == ["short"]

    def test_pages_cut_on_line_breaks_and_lose_nothing(self):
        text = _long_text()
        pages = WebAccess.split_pages(text)
        assert len(pages) > 1
        assert all(len(p) <= WebAccess.PAGE_CHARS for p in pages)
        assert all(not p.startswith("\n") and not p.endswith("\n") for p in pages)
        assert "\n".join(pages) == text

    def test_a_text_without_line_breaks_is_cut_at_the_limit(self):
        pages = WebAccess.split_pages("x" * (WebAccess.PAGE_CHARS * 2 + 1))
        assert [len(p) for p in pages] == [WebAccess.PAGE_CHARS, WebAccess.PAGE_CHARS, 1]


class TestFetchWebpagePaging:
    def test_a_short_page_comes_back_as_it_is(self):
        wa = _wa()
        with patch.object(wa, "_fetch_webpage_text", return_value="hello"):
            assert wa.fetch_webpage("https://news.example") == "hello"

    def test_a_long_page_is_headed_and_a_later_page_is_not_fetched_again(self):
        wa = _wa()
        text = _long_text()
        pages = WebAccess.split_pages(text)
        with patch.object(wa, "_fetch_webpage_text", return_value=text) as fetch:
            first = wa.fetch_webpage("https://news.example")
            second = wa.fetch_webpage("https://news.example", "2")
        assert fetch.call_count == 1
        assert first.startswith(f"[Page 1 of {len(pages)}") and first.endswith(pages[0])
        assert second.startswith(f"[Page 2 of {len(pages)}") and second.endswith(pages[1])
        assert str(len(text)) in first

    def test_a_page_past_the_end_is_an_error_naming_the_count(self):
        wa = _wa()
        pages = len(WebAccess.split_pages(_long_text()))
        with patch.object(wa, "_fetch_webpage_text", return_value=_long_text()):
            result = wa.fetch_webpage("https://news.example", pages + 1)
        assert result.startswith("ERROR") and str(pages) in result

    def test_a_later_page_of_an_unknown_url_fetches_it(self):
        wa = _wa()
        with patch.object(wa, "_fetch_webpage_text", return_value=_long_text()) as fetch:
            result = wa.fetch_webpage("https://other.example", 2)
        assert fetch.call_count == 1
        assert result.startswith("[Page 2 of")

    @pytest.mark.parametrize("value", ["", None, "abc", "0", "-3"])
    def test_anything_but_a_whole_number_is_page_one(self, value):
        assert WebAccess._page_number(value) == 1

    def test_only_the_last_few_long_texts_are_kept(self):
        wa = _wa()
        with patch.object(wa, "_fetch_webpage_text", return_value=_long_text()):
            for i in range(WebAccess._PAGE_TEXTS_KEPT + 2):
                wa.fetch_webpage(f"https://site{i}.example")
        assert len(wa._page_texts) == WebAccess._PAGE_TEXTS_KEPT
        assert "https://site0.example" not in wa._page_texts


class TestBrowseExtractPaging:
    def test_a_long_extract_is_paged_from_one_read(self):
        wa = _wa()
        with patch.object(wa, "_run_browse_op", return_value=_long_text()) as read, \
                patch.object(wa, "_async_browse_extract", return_value=None):
            first = wa.browse_extract("")
            second = wa.browse_extract("2")
        assert read.call_count == 1
        assert first.startswith("[Page 1 of") and second.startswith("[Page 2 of")

    def test_an_error_comes_back_as_it_is(self):
        wa = _wa()
        with patch.object(wa, "_run_browse_op", return_value="ERROR: extraction failed."), \
                patch.object(wa, "_async_browse_extract", return_value=None):
            assert wa.browse_extract("") == "ERROR: extraction failed."
