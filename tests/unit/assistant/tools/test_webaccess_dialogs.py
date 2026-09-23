"""JavaScript dialogs (alert/confirm/prompt) under headless=new: auto-accepted via
Page.handleJavaScriptDialog and reported in the command's reply, plus the typed
error wording that replaced the empty str() of a py3.12 TimeoutError."""
import asyncio
import pytest
from unittest.mock import AsyncMock, Mock, patch
from assistant.tools.WebAccess import WebAccess
from nodriver import cdp


def bare_wa():
    wa = WebAccess.__new__(WebAccess)
    wa._tab = Mock()
    wa._tab.send = AsyncMock(return_value=None)
    wa._dialog_notes = []
    return wa


def dialog_event(kind='confirm', message='Är du säker på att du vill ta bort?', default_prompt=None):
    return cdp.page.JavascriptDialogOpening(
        url='https://bord.example/x', frame_id=cdp.page.FrameId('f1'), message=message,
        type_=cdp.page.DialogType(kind), has_browser_handler=False, default_prompt=default_prompt)


def sent_method(tab):
    gen = tab.send.await_args.args[0]
    return gen.send(None)


@pytest.mark.unit
class TestDialogHandler:
    def test_install_survives_a_registration_failure(self):
        wa = bare_wa()
        tab = Mock()
        tab.add_handler.side_effect = RuntimeError("no connection")
        wa._install_dialog_handler(tab)  # must not raise

    def test_confirm_is_accepted_and_noted(self):
        wa = bare_wa()
        asyncio.run(wa._on_javascript_dialog(dialog_event(), wa._tab))
        msg = sent_method(wa._tab)
        assert msg['method'] == 'Page.handleJavaScriptDialog'
        assert msg['params'] == {'accept': True}
        assert len(wa._dialog_notes) == 1
        assert 'confirm dialog' in wa._dialog_notes[0]
        assert 'Är du säker på att du vill ta bort?' in wa._dialog_notes[0]
        assert 'accepted' in wa._dialog_notes[0]

    def test_prompt_is_answered_with_its_default_text(self):
        wa = bare_wa()
        asyncio.run(wa._on_javascript_dialog(
            dialog_event('prompt', 'Name?', default_prompt='anon'), wa._tab))
        assert sent_method(wa._tab)['params'] == {'accept': True, 'promptText': 'anon'}

    def test_answers_on_the_connection_that_fired_the_event(self):
        wa = bare_wa()
        popup = Mock()
        popup.send = AsyncMock(return_value=None)
        asyncio.run(wa._on_javascript_dialog(dialog_event('alert', 'hi'), popup))
        popup.send.assert_awaited_once()
        wa._tab.send.assert_not_awaited()

    def test_falls_back_to_the_active_tab_without_a_connection(self):
        wa = bare_wa()
        asyncio.run(wa._on_javascript_dialog(dialog_event('alert', 'hi')))
        wa._tab.send.assert_awaited_once()

    def test_failed_accept_leaves_no_note(self):
        wa = bare_wa()
        wa._tab.send = AsyncMock(side_effect=RuntimeError("gone"))
        asyncio.run(wa._on_javascript_dialog(dialog_event(), wa._tab))
        assert wa._dialog_notes == []

    def test_take_notes_drains_as_a_prefix(self):
        wa = bare_wa()
        assert wa._take_dialog_notes() == ''
        wa._dialog_notes = ['NOTE: a', 'NOTE: b']
        assert wa._take_dialog_notes() == 'NOTE: a\nNOTE: b\n\n'
        assert wa._take_dialog_notes() == ''

    def test_session_reset_drops_stale_notes(self):
        wa = bare_wa()
        wa._popup_parents = []
        wa._dialog_notes = ['NOTE: old']
        wa._reset_browse_session_state()
        assert wa._dialog_notes == []


@pytest.mark.unit
class TestDescribeError:
    def test_timeout_names_the_type_and_the_cap(self):
        text = WebAccess._describe_error(asyncio.TimeoutError())
        assert text.startswith('TimeoutError')
        assert f"{WebAccess._EVAL_TIMEOUT:.0f}s" in text

    def test_other_errors_keep_type_and_message(self):
        assert WebAccess._describe_error(ValueError("bad")) == 'ValueError: bad'

    def test_messageless_error_is_just_the_type(self):
        assert WebAccess._describe_error(RuntimeError()) == 'RuntimeError'


@pytest.mark.unit
class TestScreenshotIsBounded:
    def test_hung_capture_fails_within_the_eval_cap(self):
        wa = bare_wa()
        wa._browser = None
        wa._browse_session_url = 'https://example.com'
        wa._browse_pdf_text = None
        wa._last_screenshot_bytes = None
        wa._last_screenshot_path = None
        wa._popup_parents = []
        wa._known_page_targets = set()
        wa._pending_target_notes = []
        wa._tab.target = Mock(url='https://example.com')

        async def hang(*a, **k):
            await asyncio.sleep(60)
        wa._tab.send = AsyncMock(side_effect=hang)

        with patch.object(WebAccess, '_EVAL_TIMEOUT', 0.05), \
             patch.object(WebAccess, '_async_browse_ready_midsession', new=AsyncMock(return_value=None)):
            result = asyncio.run(wa._async_browse_screenshot())
        assert result.startswith('ERROR: screenshot capture failed: TimeoutError')
