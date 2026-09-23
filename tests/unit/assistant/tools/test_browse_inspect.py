import asyncio
import pytest
from unittest.mock import Mock, patch, AsyncMock
from assistant.tools.WebAccess import WebAccess


@pytest.mark.unit
class TestFormatInspectResult:

    def test_non_dict_returns_error(self):
        result = WebAccess._format_inspect_result(5, None, "garbage")
        assert "ERROR" in result
        assert "[5]" in result

    def test_not_found_error(self):
        result = WebAccess._format_inspect_result(3, None, {'error': 'not_found'})
        assert "no longer in DOM" in result
        assert "[3]" in result

    def test_targeted_attribute_value(self):
        result = WebAccess._format_inspect_result(1, 'href', {'value': 'https://example.com'})
        assert result == 'https://example.com'

    def test_targeted_attribute_missing(self):
        result = WebAccess._format_inspect_result(2, 'data-x', {'missing': True})
        assert "not set" in result
        assert "data-x" in result
        assert "[2]" in result

    def test_full_dump_with_attributes(self):
        result = WebAccess._format_inspect_result(0, None, {
            'tag': 'a',
            'attributes': {'href': 'https://example.com', 'class': 'link'},
            'textContent': 'Click here',
            'outerHTML': '<a href="https://example.com" class="link">Click here</a>',
        })
        assert "[0] <a>" in result
        assert "href='https://example.com'" in result
        assert "class='link'" in result
        assert "textContent='Click here'" in result
        assert "outerHTML=" in result
        assert "truncated" in result

    def test_full_dump_no_attributes(self):
        result = WebAccess._format_inspect_result(0, None, {
            'tag': 'div',
            'attributes': {},
        })
        assert "attributes: (none)" in result

    def test_full_dump_with_value_field(self):
        result = WebAccess._format_inspect_result(0, None, {
            'tag': 'input',
            'attributes': {'type': 'text'},
            'value': 'hello',
        })
        assert "value='hello'" in result

    def test_full_dump_without_text_or_html(self):
        result = WebAccess._format_inspect_result(0, None, {
            'tag': 'br',
            'attributes': {},
        })
        assert "textContent" not in result
        assert "outerHTML" not in result

@pytest.mark.unit
class TestBrowseInspectValidation:

    @pytest.fixture(autouse=True)
    def _healthy_browser(self):
        # The health gate probes/reinits Chrome on every command; keep it a no-op here.
        with patch.object(WebAccess, '_async_ensure_browser', new=AsyncMock(return_value=True)):
            yield

    def _make_wa(self, n_elements=1):
        wa = WebAccess.__new__(WebAccess)
        wa._browser = Mock()
        wa._tab = Mock()
        wa._browse_session_url = "https://example.com"
        wa._browse_snapshot_frame_ids = [None] * n_elements
        wa._browse_pdf_text = None
        wa._browse_lock = asyncio.Lock()
        wa._loop_thread = Mock()
        wa._loop_thread.submit = lambda coro, timeout=None: asyncio.run(coro)
        return wa

    def test_no_snapshot(self):
        wa = self._make_wa(n_elements=0)
        result = wa.browse_inspect("0")
        assert "ERROR" in result and "no current snapshot" in result

    def test_non_integer_index(self):
        wa = self._make_wa()
        result = wa.browse_inspect("abc")
        assert "ERROR" in result and "not a valid index" in result

    def test_negative_index(self):
        wa = self._make_wa()
        result = wa.browse_inspect("-1")
        assert "ERROR" in result and "out of range" in result

    def test_index_out_of_range(self):
        wa = self._make_wa(n_elements=3)
        result = wa.browse_inspect("3")
        assert "ERROR" in result and "out of range" in result

    def test_valid_index_calls_evaluate(self):
        wa = self._make_wa(n_elements=3)
        async def fake_eval(_tab, _expr):
            return {'tag': 'div', 'attributes': {'id': 'test'}}
        with patch.object(WebAccess, '_evaluate', side_effect=fake_eval):
            result = wa.browse_inspect("1")
        assert "[1] <div>" in result

    def test_comma_spec_parses_attribute(self):
        wa = self._make_wa(n_elements=3)
        async def fake_eval(_tab, _expr):
            return {'value': 'https://example.com'}
        with patch.object(WebAccess, '_evaluate', side_effect=fake_eval):
            result = wa.browse_inspect("2, href")
        assert result == "https://example.com"

    def test_evaluate_exception_returns_error(self):
        wa = self._make_wa(n_elements=1)
        async def fake_eval(_tab, _expr):
            raise RuntimeError("element detached")
        with patch.object(WebAccess, '_evaluate', side_effect=fake_eval):
            result = wa.browse_inspect("0")
        assert "ERROR" in result and "element detached" in result
