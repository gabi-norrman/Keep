"""A fetched page's tab strip is named, never clicked through: the panels behind the inactive
tabs are not read, the note at the end of the page lists them."""
import pytest
from unittest.mock import patch, Mock
from assistant.tools.WebAccess import WebAccess


@pytest.mark.unit
class TestTabNote:
    def test_names_every_readable_tab(self):
        note = WebAccess.tab_note(["Beskrivning", "Specifikationer", "Recensioner"])
        assert "Beskrivning" in note and "Specifikationer" in note and "Recensioner" in note

    def test_icon_only_and_blank_buttons_are_left_out(self):
        note = WebAccess.tab_note(["", "  ", "→", "Specifikationer", "Recensioner"])
        assert "→" not in note
        assert "Specifikationer" in note and "Recensioner" in note

    def test_fewer_than_two_readable_labels_is_no_note(self):
        assert WebAccess.tab_note(["", "•", "Specifikationer"]) == ""
        assert WebAccess.tab_note([]) == ""

    def test_duplicate_labels_appear_once(self):
        note = WebAccess.tab_note(["Fakta", "Fakta", "Recensioner"])
        assert note.count("Fakta") == 1

    def test_note_points_at_the_browser_toolkit(self):
        note = WebAccess.tab_note(["Fakta", "Recensioner"])
        assert "browser" in note and "browse_act" in note


@pytest.mark.unit
class TestFetchWebpageTabs:
    def _make_wa(self, labels):
        wa = WebAccess.__new__(WebAccess)
        wa._browser = None
        wa._tab = None
        wa._driver_ready = Mock()
        wa._driver_ready.wait.return_value = True
        wa._loop_thread = Mock()

        def _submit(coro, timeout=None):
            name = coro.cr_code.co_name
            coro.close()
            return labels if name == "_async_tab_labels" else None
        wa._loop_thread.submit = Mock(side_effect=_submit)
        return wa

    @patch.object(WebAccess, '_fetch_via_chrome')
    def test_tabbed_page_ends_with_the_tab_names_and_no_panels(self, mock_chrome):
        wa = self._make_wa(["Mest populära", "Bildkablar", "Väggfäste"])
        mock_chrome.return_value = "<html><body><main><p>Samsung The Frame 32 tum, QLED.</p></main></body></html>"
        result = wa.fetch_webpage("https://shop.example/tv")
        assert "Samsung The Frame" in result
        assert result.rstrip().endswith("]")
        assert "Väggfäste" in result and "Bildkablar" in result
        assert result.count("Samsung The Frame") == 1

    @patch.object(WebAccess, '_fetch_via_chrome')
    def test_page_without_tabs_gets_no_note(self, mock_chrome):
        wa = self._make_wa([])
        mock_chrome.return_value = "<html><body><main><p>Plain article text here.</p></main></body></html>"
        result = wa.fetch_webpage("https://news.example/story")
        assert "tabs" not in result.lower()
        assert "browser" not in result
