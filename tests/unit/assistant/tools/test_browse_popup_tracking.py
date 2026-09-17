import asyncio
import pytest
from unittest.mock import AsyncMock, patch
from assistant.tools.WebAccess import WebAccess
from nodriver import cdp


class FakeTargetInfo:
    def __init__(self, target_id, type_='page', url=''):
        self.target_id = cdp.target.TargetID(target_id)
        self.type_ = type_
        self.url = url


class FakeTab:
    def __init__(self, target_id, url='', type_='page'):
        self.target = FakeTargetInfo(target_id, type_, url)

    async def evaluate(self, *args, **kwargs):  # satisfies _as_tab's Tab check
        return None


class FakeConnection:
    """Answers Target.getTargets from the browser's live-target list and records
    Target.closeTarget calls (removing the closed target from the live list)."""

    def __init__(self, browser):
        self._browser = browser
        self.closed = []

    async def send(self, cdp_obj, **kwargs):
        name = getattr(getattr(cdp_obj, 'gi_code', None), 'co_name', '')
        if name == 'get_targets':
            if self._browser.get_targets_error is not None:
                raise self._browser.get_targets_error
            return [t.target for t in self._browser.live_targets]
        if name == 'close_target':
            tid = str(cdp_obj.send(None)['params']['targetId'])
            self.closed.append(tid)
            self._browser.live_targets = [
                t for t in self._browser.live_targets if str(t.target.target_id) != tid
            ]
            return None
        return None


class FakeBrowser:
    def __init__(self, targets, live=None):
        self.targets = targets  # nodriver registry view (used by _page_targets/_as_tab)
        self.live_targets = list(targets) if live is None else list(live)  # getTargets view
        self.get_targets_error = None
        self.connection = FakeConnection(self)

    async def update_targets(self):
        pass


def make_wa(base_tab, browser):
    wa = WebAccess.__new__(WebAccess)
    wa._browser = browser
    wa._tab = base_tab
    wa._popup_parents = []
    wa._known_page_targets = {str(base_tab.target.target_id)}
    wa._pending_target_notes = []
    wa._dialog_notes = []
    wa._pending_popup_targets = {}
    wa._browse_frame_sessions = {}
    wa._browse_snapshot_frame_ids = []
    wa._browse_snapshot_labels = []
    wa._last_screenshot_bytes = None
    wa._last_screenshot_path = None
    wa._POPUP_BLANK_GRACE = 0.0  # skip the inline blank-target poll in unit tests
    return wa


def run(coro):
    return asyncio.run(coro)


IDLE_PATCH = patch.object(WebAccess, '_async_wait_for_page_ready', new=AsyncMock())


@pytest.mark.unit
class TestSyncPopupTargets:

    def test_new_page_target_becomes_active(self):
        base = FakeTab('base', 'https://shop.example/kassa')
        popup = FakeTab('pop1', 'https://bank.example/pay')
        wa = make_wa(base, FakeBrowser([base, popup]))
        wa._browse_frame_sessions = {'stale': 'session'}
        with IDLE_PATCH:
            run(wa._async_sync_popup_targets())
        assert wa._tab is popup
        assert wa._popup_parents == [base]
        assert wa._browse_frame_sessions == {}
        assert len(wa._pending_target_notes) == 1
        assert 'popup' in wa._pending_target_notes[0]
        assert 'https://bank.example/pay' in wa._pending_target_notes[0]

    def test_popup_close_returns_to_parent(self):
        base = FakeTab('base', 'https://shop.example/kassa')
        popup = FakeTab('pop1', 'https://bank.example/pay')
        wa = make_wa(base, FakeBrowser([base]))
        wa._tab = popup
        wa._popup_parents = [base]
        wa._known_page_targets = {'base', 'pop1'}
        run(wa._async_sync_popup_targets())
        assert wa._tab is base
        assert wa._popup_parents == []
        assert wa._known_page_targets == {'base'}
        assert len(wa._pending_target_notes) == 1
        assert 'closed' in wa._pending_target_notes[0]
        assert 'https://shop.example/kassa' in wa._pending_target_notes[0]

    def test_devtools_target_ignored(self):
        base = FakeTab('base', 'https://shop.example')
        devtools = FakeTab('dt', 'devtools://devtools/inspector.html')
        wa = make_wa(base, FakeBrowser([base, devtools]))
        run(wa._async_sync_popup_targets())
        assert wa._tab is base
        assert wa._popup_parents == []
        assert wa._pending_target_notes == []
        # but it is now accounted for, so it won't be re-inspected next sync
        assert 'dt' in wa._known_page_targets

    def test_iframe_target_not_adopted(self):
        base = FakeTab('base', 'https://shop.example')
        iframe = FakeTab('if1', 'https://pay.klarna.com/frame', type_='iframe')
        wa = make_wa(base, FakeBrowser([base, iframe]))
        run(wa._async_sync_popup_targets())
        assert wa._tab is base
        assert wa._pending_target_notes == []

    def test_no_change_is_noop(self):
        base = FakeTab('base', 'https://shop.example')
        wa = make_wa(base, FakeBrowser([base]))
        run(wa._async_sync_popup_targets())
        assert wa._tab is base
        assert wa._popup_parents == []
        assert wa._pending_target_notes == []

    def test_multiple_popups_form_a_stack(self):
        base = FakeTab('base', 'https://shop.example')
        p1 = FakeTab('pop1', 'https://bank.example/step1')
        p2 = FakeTab('pop2', 'https://bankid.example/auth')
        wa = make_wa(base, FakeBrowser([base, p1, p2]))
        with IDLE_PATCH:
            run(wa._async_sync_popup_targets())
        assert wa._tab is p2
        assert wa._popup_parents == [base, p1]
        assert len(wa._pending_target_notes) == 2

    def test_close_falls_through_dead_parent(self):
        base = FakeTab('base', 'https://shop.example')
        p1 = FakeTab('pop1', 'https://bank.example/step1')
        p2 = FakeTab('pop2', 'https://bankid.example/auth')
        wa = make_wa(base, FakeBrowser([base]))
        wa._tab = p2
        wa._popup_parents = [base, p1]
        wa._known_page_targets = {'base', 'pop1', 'pop2'}
        run(wa._async_sync_popup_targets())
        assert wa._tab is base
        assert wa._popup_parents == []
        assert len(wa._pending_target_notes) == 2

    def test_no_browser_is_noop(self):
        base = FakeTab('base', 'https://shop.example')
        wa = make_wa(base, FakeBrowser([base]))
        wa._browser = None
        run(wa._async_sync_popup_targets())
        assert wa._tab is base


@pytest.mark.unit
class TestClosePopupTargets:

    def test_closes_leftovers_and_restores_base(self):
        base = FakeTab('base', 'https://shop.example')
        p1 = FakeTab('pop1', 'https://bank.example/step1')
        p2 = FakeTab('pop2', 'https://bankid.example/auth')
        browser = FakeBrowser([base, p1, p2])
        wa = make_wa(base, browser)
        wa._tab = p2
        wa._popup_parents = [base, p1]
        wa._pending_target_notes = ['stale note']
        run(wa._async_close_popup_targets())
        assert wa._tab is base
        assert wa._popup_parents == []
        assert wa._pending_target_notes == []
        assert browser.connection.closed == ['pop1', 'pop2']
        assert wa._known_page_targets == {'base'}

    def test_without_popups_just_seeds_known_targets(self):
        base = FakeTab('base', 'https://shop.example')
        browser = FakeBrowser([base])
        wa = make_wa(base, browser)
        wa._known_page_targets = set()
        run(wa._async_close_popup_targets())
        assert wa._tab is base
        assert browser.connection.closed == []
        assert wa._known_page_targets == {'base'}

    def test_no_browser_clears_state(self):
        base = FakeTab('base', 'https://shop.example')
        wa = make_wa(base, FakeBrowser([base]))
        wa._browser = None
        wa._popup_parents = [base]
        wa._pending_target_notes = ['note']
        wa._pending_popup_targets = {'x': 0.0}
        run(wa._async_close_popup_targets())
        assert wa._popup_parents == []
        assert wa._pending_target_notes == []
        assert wa._pending_popup_targets == {}


@pytest.mark.unit
class TestSessionStateReset:

    def test_reset_restores_base_tab_when_last_known_live(self):
        base = FakeTab('base', 'https://shop.example')
        popup = FakeTab('pop1', 'https://bank.example/pay')
        wa = make_wa(base, FakeBrowser([base]))
        wa._tab = popup
        wa._popup_parents = [base]
        wa._pending_target_notes = ['note']
        wa._known_page_targets = {'base', 'pop1'}
        wa._reset_browse_session_state()
        assert wa._tab is base
        assert wa._popup_parents == []
        assert wa._pending_target_notes == []
        assert wa._known_page_targets == set()

    def test_reset_keeps_current_tab_when_base_not_known_live(self):
        base = FakeTab('base', 'https://shop.example')
        popup = FakeTab('pop1', 'https://bank.example/pay')
        wa = make_wa(base, FakeBrowser([popup]))
        wa._tab = popup
        wa._popup_parents = [base]
        wa._known_page_targets = {'pop1'}  # base died since the last sync
        wa._reset_browse_session_state()
        assert wa._tab is popup
        assert wa._popup_parents == []
        assert wa._known_page_targets == set()
        assert wa._pending_popup_targets == {}


@pytest.mark.unit
class TestTargetNotesInSnapshot:

    def test_consume_target_notes_drains(self):
        base = FakeTab('base', 'https://shop.example')
        wa = make_wa(base, FakeBrowser([base]))
        wa._pending_target_notes = ['NOTE: one', 'NOTE: two']
        prefix = wa._consume_target_notes()
        assert prefix == 'NOTE: one\nNOTE: two\n\n'
        assert wa._pending_target_notes == []
        assert wa._consume_target_notes() == ''

    def test_snapshot_includes_pending_note(self):
        popup = FakeTab('pop1', 'https://bank.example/pay')
        wa = make_wa(popup, FakeBrowser([popup]))
        wa._pending_target_notes = ['NOTE: the page opened a popup/new window.']

        async def fake_eval(_tab, expr):
            if 'document.title' in expr:
                return 'Bank'
            return {'records': [], 'crossOriginFrames': []}

        with patch.object(WebAccess, '_evaluate', side_effect=fake_eval), \
                patch.object(WebAccess, '_async_sync_popup_targets', new=AsyncMock()), \
                patch.object(WebAccess, '_async_list_frame_targets', new=AsyncMock(return_value=[])):
            result = run(wa._async_take_snapshot())

        assert result.startswith('NOTE: the page opened a popup/new window.')
        assert 'URL: https://bank.example/pay' in result
        assert wa._pending_target_notes == []


@pytest.mark.unit
class TestPopupAntiBotTweaks:
    """A popup is its own CDP target: the base tab's UA override / client hints
    don't carry over, and SSO/login windows are exactly where a HeadlessChrome
    tell matters (the x.com login rate-limit incident)."""

    def test_adopted_popup_gets_anti_bot_tweaks(self):
        base = FakeTab('base', 'https://shop.example')
        popup = FakeTab('pop1', 'https://accounts.google.com/o/oauth2/v2/auth')
        wa = make_wa(base, FakeBrowser([base, popup]))
        tweaks = AsyncMock()
        with IDLE_PATCH, patch.object(WebAccess, '_async_apply_anti_bot_tweaks', new=tweaks):
            run(wa._async_sync_popup_targets())
        assert wa._tab is popup
        tweaks.assert_awaited_once_with(popup)

    def test_adoption_survives_anti_bot_failure(self):
        base = FakeTab('base', 'https://shop.example')
        popup = FakeTab('pop1', 'https://bank.example/pay')
        wa = make_wa(base, FakeBrowser([base, popup]))
        tweaks = AsyncMock(side_effect=RuntimeError('no transport'))
        with IDLE_PATCH, patch.object(WebAccess, '_async_apply_anti_bot_tweaks', new=tweaks):
            run(wa._async_sync_popup_targets())
        assert wa._tab is popup
        assert wa._popup_parents == [base]
        assert len(wa._pending_target_notes) == 1
