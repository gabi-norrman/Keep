"""browse_open telling a bot wall from the site it asked for. A DataDome
'blocked visitor' page (t=bv) is a dead end the LLM must not retry — the
2026-09-29 Etsy incident burned three attempts on it; a challenge (t=fe,
Cloudflare turnstile) is shown with a note. No real Chrome."""

import asyncio
import threading
import time

import pytest
from unittest.mock import Mock, patch, AsyncMock

from assistant.tools.WebAccess import WebAccess


def run(coro):
    return asyncio.run(coro)


def make_wa(document_status, frames):
    wa = WebAccess.__new__(WebAccess)
    wa._tab = Mock()
    wa._tab.target.target_id = 'basetab0-xxxx'
    wa._network_log = []
    wa._network_requests = {}
    if document_status is not None:
        wa._network_log.append({
            'rid': '1', 'ts': time.time(), 'tab': 'basetab0', 'type': 'Document', 'method': 'GET',
            'url': 'https://shop.example/', 'post': '', 'status': document_status, 'mime': 'text/html',
            'size': 10, 'body': None, 'note': '', 'done': True,
        })
    wa._frames = frames
    return wa


def frame_probe(wa):
    async def _eval(tab, expression, **kwargs):
        return wa._frames
    return patch.object(WebAccess, '_evaluate', staticmethod(_eval))


DD_BLOCK = 'https://geo.captcha-delivery.com/captcha/?initialCid=abc&hash=def&cid=ghi&t=bv&referer=x'
DD_CHALLENGE = 'https://geo.captcha-delivery.com/captcha/?initialCid=abc&hash=def&cid=ghi&t=fe&referer=x'
TURNSTILE = 'https://challenges.cloudflare.com/cdn-cgi/challenge-platform/h/b/turnstile/if/ov2/av0/x'


@pytest.mark.unit
class TestDetectBotWall:

    def test_datadome_blocked_visitor_is_a_hard_wall(self):
        wa = make_wa(403, [DD_BLOCK])
        with frame_probe(wa):
            hard, message = run(wa._async_detect_bot_wall(wa._tab, 'https://shop.example/'))
        assert hard is True
        assert 'DataDome' in message and 't=bv' in message
        assert message.startswith('ERROR')

    def test_datadome_challenge_is_soft(self):
        wa = make_wa(403, ['', DD_CHALLENGE])
        with frame_probe(wa):
            hard, message = run(wa._async_detect_bot_wall(wa._tab, 'https://shop.example/'))
        assert hard is False
        assert 't=fe' in message
        assert not message.startswith('ERROR')

    def test_cloudflare_turnstile_on_an_error_document_is_soft(self):
        wa = make_wa(503, [TURNSTILE])
        with frame_probe(wa):
            hard, message = run(wa._async_detect_bot_wall(wa._tab, 'https://shop.example/'))
        assert hard is False
        assert 'Cloudflare' in message

    def test_turnstile_on_a_normal_page_is_not_a_wall(self):
        # A login form embedding a turnstile is the site, not a wall.
        wa = make_wa(200, [TURNSTILE])
        with frame_probe(wa):
            assert run(wa._async_detect_bot_wall(wa._tab, 'https://shop.example/')) is None

    def test_error_document_without_a_vendor_frame_is_a_plain_error_page(self):
        wa = make_wa(403, ['https://shop.example/help-frame'])
        with frame_probe(wa):
            assert run(wa._async_detect_bot_wall(wa._tab, 'https://shop.example/')) is None

    def test_only_this_tabs_newest_document_counts(self):
        wa = make_wa(403, [DD_BLOCK])
        wa._network_log[0]['tab'] = 'othertab'
        wa._network_log.append(dict(wa._network_log[0], tab='basetab0', status=200, rid='2'))
        with frame_probe(wa):
            assert run(wa._async_detect_bot_wall(wa._tab, 'https://shop.example/')) is None

    def test_redirect_hop_is_skipped_for_the_final_document(self):
        wa = make_wa(302, [DD_BLOCK])
        wa._network_log.append(dict(wa._network_log[0], status=403, rid='1b'))
        with frame_probe(wa):
            hard, _ = run(wa._async_detect_bot_wall(wa._tab, 'https://shop.example/'))
        assert hard is True

    def test_no_capture_means_no_wall(self):
        wa = make_wa(None, [DD_BLOCK])
        with frame_probe(wa):
            assert run(wa._async_detect_bot_wall(wa._tab, 'https://shop.example/')) is None

    def test_probe_failure_falls_through_to_the_snapshot(self):
        wa = make_wa(403, [DD_BLOCK])
        with patch.object(WebAccess, '_evaluate', staticmethod(AsyncMock(side_effect=RuntimeError('gone')))):
            assert run(wa._async_detect_bot_wall(wa._tab, 'https://shop.example/')) is None


def make_open_wa():
    wa = WebAccess.__new__(WebAccess)
    wa._browser = Mock()
    wa._tab = Mock()
    wa._tab.target.target_id = 'basetab0-xxxx'
    wa._chrome_pids = set()
    wa._driver_ready = threading.Event()
    wa._driver_ready.set()
    wa._last_init_failure_ts = 0.0
    wa._init_in_progress = False
    wa._browse_session_url = None
    wa._browse_pdf_text = None
    wa._network_log = []
    wa._network_requests = {}
    wa._dialog_notes = []
    wa._popup_parents = []
    wa._known_page_targets = set()
    wa._pending_popup_targets = {}
    wa._pending_target_notes = []
    wa._browser_generation = 1
    wa._browse_fail_streak = 1
    return wa


def open_patches(wall):
    return (
        patch.object(WebAccess, '_async_browse_ready', new=AsyncMock(return_value=None)),
        patch.object(WebAccess, '_async_close_popup_targets', new=AsyncMock()),
        patch.object(WebAccess, '_async_navigate', new=AsyncMock()),
        patch.object(WebAccess, '_async_wait_for_page_ready', new=AsyncMock()),
        patch.object(WebAccess, '_async_wait_for_settle', new=AsyncMock()),
        patch.object(WebAccess, '_async_detect_bot_wall', new=AsyncMock(return_value=wall)),
        patch.object(WebAccess, '_async_take_snapshot', new=AsyncMock(return_value='URL: x\nSNAPSHOT')),
    )


@pytest.mark.unit
class TestBrowseOpenOnAWall:

    def test_hard_wall_returns_the_error_and_no_snapshot(self):
        wa = make_open_wa()
        cookies = AsyncMock()
        p = open_patches((True, 'ERROR: wall'))
        with p[0], p[1], p[2], p[3], p[4], p[5], p[6], \
                patch.object(WebAccess, '_async_settle_cookie_banner', new=cookies):
            result = run(wa._async_browse_open('https://shop.example/'))
        assert result == 'ERROR: wall'
        cookies.assert_not_awaited()
        # The session still points at the page so a follow-up command has a target.
        assert wa._browse_session_url == 'https://shop.example/'

    def test_challenge_note_precedes_the_snapshot(self):
        wa = make_open_wa()
        p = open_patches((False, 'NOTE: challenge'))
        with p[0], p[1], p[2], p[3], p[4], p[5], p[6], \
                patch.object(WebAccess, '_async_settle_cookie_banner', new=AsyncMock()):
            result = run(wa._async_browse_open('https://shop.example/'))
        assert result.startswith('NOTE: challenge')
        assert result.endswith('SNAPSHOT')

    def test_normal_page_is_untouched(self):
        wa = make_open_wa()
        cookies = AsyncMock()
        p = open_patches(None)
        with p[0], p[1], p[2], p[3], p[4], p[5], p[6], \
                patch.object(WebAccess, '_async_settle_cookie_banner', new=cookies):
            result = run(wa._async_browse_open('https://shop.example/'))
        assert result == 'URL: x\nSNAPSHOT'
        cookies.assert_awaited_once()
