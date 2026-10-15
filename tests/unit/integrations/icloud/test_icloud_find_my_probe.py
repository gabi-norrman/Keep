"""The read-only Find My probe behind the iCloud health check, the TUI status and the device
listing (2026-10-15): it never wakes a device and never signs in; the re-sign-in Find My
sometimes wants is deliberate and spaced."""
from unittest.mock import Mock, patch

import pytest
from pyicloud.exceptions import (PyiCloudAPIResponseException, PyiCloudAuthRequiredException,
                                 PyiCloudFailedLoginException)

from integrations.icloud.ICloudIntegration import iCloudAPI, _brief

CONFIG = {"apple_id": "m@mlab.test", "password": "secret",
          "devices": [{"id": "fmip-dad", "display_name": "dad"}]}
NOTIFY_PATH = 'assistant.Notifier.NullNotifier.send_message'
LOCK_BODY = ('{"serviceErrors": [{"code": "-20209", "message": "This Apple Account has been locked '
             'for security reasons. Visit iForgot to reset your account (https://iforgot.apple.com)."}]}')


def _record(device_id, name="iPhone"):
    return {"id": device_id, "baUUID": "", "name": name, "deviceDisplayName": "iPhone 15", "deviceClass": "iPhone"}


def _service(answers=None, exc=None):
    """A pyicloud mock whose Find My requests answer in order with `answers` (dicts), or raise `exc`."""
    service = Mock()
    service.requires_2fa = False
    service.get_webservice_url.return_value = "https://p1-fmipweb.icloud.example"
    if exc is not None:
        service.session.post.side_effect = exc
    else:
        responses = [Mock(json=Mock(return_value=answer)) for answer in answers]
        service.session.post.side_effect = responses
    return service


def _reauth_wanted():
    return PyiCloudAuthRequiredException("m@mlab.test", Mock())


@pytest.fixture(autouse=True)
def reset_singleton():
    iCloudAPI.reset_instance()
    yield
    iCloudAPI.reset_instance()


@pytest.fixture
def api():
    return iCloudAPI(config=CONFIG)


class TestProbeRequest:
    def test_probe_asks_for_the_device_list_without_waking_a_device(self, api):
        api._api = _service([{"content": [_record("fmip-dad")]}])
        assert api.check_health() is True
        (url,), kwargs = api._api.session.post.call_args
        assert url.endswith("/fmipservice/client/web/initClient")
        body = kwargs["json"]
        assert "isUpdatingAllLocations" not in body
        assert "shouldLocate" not in body["clientContext"]
        assert "selectedDevice" not in body["clientContext"]

    def test_one_request_serves_status_checks_inside_the_ttl(self, api):
        api._api = _service([{"content": [_record("fmip-dad")]}])
        assert api.get_auth_status()["status"] == "authenticated"
        assert api.get_auth_status()["status"] == "authenticated"
        assert api._api.session.post.call_count == 1

    def test_a_new_session_drops_the_cached_verdict(self, api):
        api._api = _service(exc=_reauth_wanted())
        assert api.get_auth_status()["status"] == "needs_reauth"
        api._api = _service([{"content": [_record("fmip-dad")]}])
        assert api.get_auth_status()["status"] == "authenticated"

    def test_listing_merges_family_devices_that_load_after_the_first_answer(self, api):
        api.FAMILY_POLL_DELAY_SECONDS = 0
        first = {"content": [_record("fmip-dad", "Dad's iPhone")], "serverContext": {"ctx": 1},
                 "userInfo": {"hasMembers": True, "membersInfo": {"charlotte": {"deviceFetchStatus": "LOADING"}}}}
        second = {"content": [_record("fmip-dad", "Dad's iPhone"), _record("fmip-charlotte", "Charlotte's iPhone")],
                  "userInfo": {"hasMembers": True, "membersInfo": {"charlotte": {"deviceFetchStatus": "DONE"}}}}
        api._api = _service([first, second])
        with patch.object(api, "_ensure_authenticated"):
            names = [d["name"] for d in api.get_icloud_devices()]
        assert names == ["Dad's iPhone", "Charlotte's iPhone"]
        (url,), kwargs = api._api.session.post.call_args
        assert url.endswith("/refreshClient")
        assert kwargs["json"]["serverContext"] == {"ctx": 1}, "the poll continues the server's context"
        assert "shouldLocate" not in kwargs["json"]["clientContext"]

    def test_family_polling_stops_at_the_cap(self, api):
        api.FAMILY_POLL_DELAY_SECONDS = 0
        loading = {"content": [_record("fmip-dad")], "serverContext": {},
                   "userInfo": {"hasMembers": True, "membersInfo": {"x": {"deviceFetchStatus": "LOADING"}}}}
        api._api = _service([loading] * 10)
        with patch.object(api, "_ensure_authenticated"):
            assert len(api.get_icloud_devices()) == 1
        assert api._api.session.post.call_count == 1 + api.FAMILY_POLLS


class TestReauthVerdict:
    def test_status_says_needs_reauth_and_never_signs_in(self, api):
        api._api = _service(exc=_reauth_wanted())
        assert api.get_auth_status()["status"] == "needs_reauth"
        api._api.authenticate.assert_not_called()

    def test_apples_authentication_required_reason_counts_as_reauth(self, api):
        api._api = _service(exc=PyiCloudAPIResponseException("Authentication required for Account.", 421, Mock(text="")))
        assert api.get_auth_status()["status"] == "needs_reauth"

    def test_other_failures_show_as_an_error_with_the_reason(self, api):
        api._api = _service(exc=PyiCloudAPIResponseException("Service Temporarily Unavailable", 503, Mock(text="")))
        status = api.get_auth_status()
        assert status["status"] == "error"
        assert "503" in status["message"]

    def test_health_check_reauthenticates_once_per_cooldown(self, api):
        service = _service(exc=_reauth_wanted())
        api._api = service
        assert api.check_health() is False
        service.authenticate.assert_called_once_with(force_refresh=True)
        assert api.check_health() is False
        service.authenticate.assert_called_once(), "inside the cooldown the monitor waits"

    def test_health_check_passes_once_the_reauth_restores_find_my(self, api):
        service = _service([])
        service.session.post.side_effect = [_reauth_wanted(), Mock(json=Mock(return_value={"content": [_record("fmip-dad")]}))]
        api._api = service
        assert api.check_health() is True
        service.authenticate.assert_called_once_with(force_refresh=True)

    def test_manual_refresh_reauthenticates_regardless_of_the_cooldown(self, api):
        service = _service([])
        service.session.post.side_effect = [_reauth_wanted(), _reauth_wanted(), _reauth_wanted(),
                                            Mock(json=Mock(return_value={"content": [_record("fmip-dad")]}))]
        api._api = service
        assert api.check_health() is False          # one automatic attempt, cooldown armed
        with patch.object(api, "_ensure_authenticated", side_effect=lambda: setattr(api, "_api", service)):
            api.refresh_connection()
        assert service.authenticate.call_count == 2

    def test_a_reauth_that_ends_in_a_2fa_challenge_hands_over_to_the_2fa_flow(self, api):
        service = _service(exc=_reauth_wanted())

        def challenged(force_refresh=False):
            service.requires_2fa = True
        service.authenticate.side_effect = challenged
        api._api = service
        assert api.check_health() is False
        assert api.get_auth_status()["status"] == "needs_2fa", "the 2FA flow owns the session from here"
        assert service.session.post.call_count == 1, "no probe is retried against a session Apple is still challenging"


class TestAccountLock:
    def _locked_login(self):
        try:
            raise PyiCloudAPIResponseException("", 403, Mock(text=LOCK_BODY))
        except PyiCloudAPIResponseException as cause:
            try:
                raise PyiCloudFailedLoginException("Invalid email/password combination.") from cause
            except PyiCloudFailedLoginException as e:
                return e

    def test_brief_follows_the_cause_to_apples_answer(self):
        line = _brief(self._locked_login())
        assert line.startswith("Invalid email/password combination. <- ")
        assert "(403)" in line and "locked for security reasons" in line
        assert "\n" not in line

    def test_brief_still_caps_a_huge_chained_body(self):
        try:
            raise PyiCloudAPIResponseException("x", 409, Mock(text="<html>" + "\n<div>row</div>" * 900))
        except PyiCloudAPIResponseException as cause:
            try:
                raise PyiCloudFailedLoginException("wrapped") from cause
            except PyiCloudFailedLoginException as e:
                line = _brief(e)
        assert len(line) < 400 and "\n" not in line
        assert "chars, PyiCloudFailedLoginException]" in line

    def test_a_locked_account_is_reported_once_with_the_unlock_page(self, api):
        service = _service(exc=_reauth_wanted())
        service.authenticate.side_effect = self._locked_login()
        api._api = service
        with patch(NOTIFY_PATH) as notify:
            for _ in range(2):
                assert api._reauthenticate_find_my("test", automatic=False).verdict == "error"
        notify.assert_called_once()
        assert "iforgot.apple.com" in notify.call_args.args[0]
        status = api.get_auth_status()
        assert status["status"] == "error" and "locked" in status["message"]
