"""The read-only Find My probe behind the TUI's device table and the device listing, and the
rules since the lockouts of 2026-10-15/16: nothing reads Find My on a timer, a session Find My
wants re-signed is idle, the one sign-in a command earns is deliberate and completed, and a
refusal from Apple parks every sign-in — across restarts — until the user confirms."""
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


def _answer(devices):
    return Mock(json=Mock(return_value={"content": devices}))


def _service(answers=None, exc=None):
    """A pyicloud mock whose Find My requests answer in order with `answers` (dicts), or raise `exc`."""
    service = Mock()
    service.requires_2fa = False
    service._auth_data = {}
    service.get_webservice_url.return_value = "https://p1-fmipweb.icloud.example"
    if exc is not None:
        service.session.post.side_effect = exc
    else:
        service.session.post.side_effect = [Mock(json=Mock(return_value=answer)) for answer in answers]
    return service


def _reauth_wanted():
    return PyiCloudAuthRequiredException("m@mlab.test", Mock())


def _locked_login():
    try:
        raise PyiCloudAPIResponseException("", 403, Mock(text=LOCK_BODY))
    except PyiCloudAPIResponseException as cause:
        try:
            raise PyiCloudFailedLoginException("Invalid email/password combination.") from cause
        except PyiCloudFailedLoginException as e:
            return e


@pytest.fixture(autouse=True)
def reset_singleton():
    iCloudAPI.reset_instance()
    yield
    iCloudAPI.reset_instance()


@pytest.fixture
def api():
    return iCloudAPI(config=CONFIG)


class TestProbeRequest:
    def test_listing_asks_for_the_device_list_without_waking_a_device(self, api):
        api._api = _service([{"content": [_record("fmip-dad")]}])
        assert [d["id"] for d in api.get_icloud_devices()] == ["fmip-dad"]
        (url,), kwargs = api._api.session.post.call_args
        assert url.endswith("/fmipservice/client/web/initClient")
        body = kwargs["json"]
        assert "isUpdatingAllLocations" not in body
        assert "shouldLocate" not in body["clientContext"]
        assert "selectedDevice" not in body["clientContext"]

    def test_one_request_serves_listings_inside_the_ttl(self, api):
        api._api = _service([{"content": [_record("fmip-dad")]}])
        assert len(api.get_icloud_devices()) == 1
        assert len(api.get_icloud_devices()) == 1
        assert api._api.session.post.call_count == 1

    def test_status_checks_make_no_request_of_their_own(self, api):
        api._api = _service([{"content": [_record("fmip-dad")]}])
        for _ in range(3):
            assert api.get_auth_status()["status"] == "authenticated"
        api._api.session.post.assert_not_called()

    def test_a_new_session_drops_the_cached_verdict(self, api):
        api._api = _service(exc=_reauth_wanted())
        assert api.get_icloud_devices() == []
        assert api.get_auth_status()["status"] == "needs_reauth"
        api._api = _service([{"content": [_record("fmip-dad")]}])
        assert api.get_auth_status()["status"] == "authenticated"
        assert len(api.get_icloud_devices()) == 1

    def test_listing_merges_family_devices_that_load_after_the_first_answer(self, api):
        api.FAMILY_POLL_DELAY_SECONDS = 0
        first = {"content": [_record("fmip-dad", "Dad's iPhone")], "serverContext": {"ctx": 1},
                 "userInfo": {"hasMembers": True, "membersInfo": {"charlotte": {"deviceFetchStatus": "LOADING"}}}}
        second = {"content": [_record("fmip-dad", "Dad's iPhone"), _record("fmip-charlotte", "Charlotte's iPhone")],
                  "userInfo": {"hasMembers": True, "membersInfo": {"charlotte": {"deviceFetchStatus": "DONE"}}}}
        api._api = _service([first, second])
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
        assert len(api.get_icloud_devices()) == 1
        assert api._api.session.post.call_count == 1 + api.FAMILY_POLLS


class TestReauthVerdict:
    def test_the_tuis_listing_says_needs_reauth_and_never_signs_in(self, api):
        api._api = _service(exc=_reauth_wanted())
        assert api.get_icloud_devices() == []
        assert api.get_auth_status()["status"] == "needs_reauth"
        api._api.authenticate.assert_not_called()

    def test_apples_authentication_required_reason_counts_as_reauth(self, api):
        api._api = _service(exc=PyiCloudAPIResponseException("Authentication required for Account.", 421, Mock(text="")))
        assert api.get_icloud_devices() == []
        assert api.get_auth_status()["status"] == "needs_reauth"

    def test_other_failures_show_as_an_error_with_the_reason(self, api):
        api._api = _service(exc=PyiCloudAPIResponseException("Service Temporarily Unavailable", 503, Mock(text="")))
        assert api.get_icloud_devices() == []
        status = api.get_auth_status()
        assert status["status"] == "error"
        assert "503" in status["message"]

    def test_the_commands_listing_signs_in_once_and_lists(self, api):
        service = _service([])
        service.session.post.side_effect = [_reauth_wanted(), _answer([_record("fmip-dad")])]
        api._api = service
        assert [d["id"] for d in api.get_icloud_devices(sign_in=True)] == ["fmip-dad"]
        service.authenticate.assert_called_once_with(force_refresh=True)

    def test_the_health_check_leaves_a_rejected_session_alone(self, api):
        """Idle, not unhealthy: the hourly re-sign-in this used to do is gone."""
        service = _service(exc=_reauth_wanted())
        api._api = service
        for _ in range(3):
            assert api.check_health() is True
        service.authenticate.assert_not_called()
        service.session.post.assert_not_called()

    def test_a_users_refresh_signs_in_for_find_my(self, api):
        service = _service([])
        service.session.post.side_effect = [_reauth_wanted(), _answer([_record("fmip-dad")])]
        api._api = service
        with patch.object(api, "_ensure_authenticated", side_effect=lambda: setattr(api, "_api", service)):
            api.refresh_connection(by_user=True)
        service.authenticate.assert_called_once_with(force_refresh=True)
        assert api.get_auth_status()["status"] == "authenticated"

    def test_a_signin_that_ends_in_a_2fa_challenge_hands_over_to_the_2fa_flow(self, api):
        service = _service([])
        service.session.post.side_effect = [_reauth_wanted(), _answer([_record("fmip-dad")])]

        def challenged(force_refresh=False):
            service.requires_2fa = True
            service._auth_data = {"phoneNumberVerification": {"trustedPhoneNumber": {
                "id": 1, "pushMode": "sms", "nonFTEU": False, "obfuscatedNumber": "•••05"}}}
        service.authenticate.side_effect = challenged
        api._api = service
        assert api.get_icloud_devices(sign_in=True) == []
        assert api.get_auth_status()["status"] == "needs_2fa", "the 2FA flow owns the session from here"
        assert api.sms_request_pending(), "the code Apple texts on its own is the pending request"
        assert service.session.post.call_count == 1, "no probe is retried against a session Apple is still challenging"


class TestAccountLock:
    def test_brief_follows_the_cause_to_apples_answer(self):
        line = _brief(_locked_login())
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

    def test_a_locked_account_is_reported_once_with_the_unlock_page_and_parks(self, api, icloud_session_dir):
        service = _service(exc=_reauth_wanted())
        service.authenticate.side_effect = _locked_login()
        api._api = service
        with patch(NOTIFY_PATH) as notify:
            verdicts = [api._sign_in_for_find_my("test") for _ in range(3)]
        assert all(verdicts), "Find My stays unavailable"
        notify.assert_called_once()
        assert "iforgot.apple.com" in notify.call_args.args[0]
        service.authenticate.assert_called_once(), "the refusal parks the sign-ins; no retry"
        assert (icloud_session_dir / api.PARK_FILE).exists()
        status = api.get_auth_status()
        assert status["status"] == "parked" and "locked" in status["message"].lower()

    def _parked(self, api):
        service = _service(exc=_reauth_wanted())
        service.authenticate.side_effect = _locked_login()
        api._api = service
        with patch(NOTIFY_PATH):
            api._sign_in_for_find_my("test")
        return service

    def test_the_park_survives_a_new_instance(self, api):
        """17-23 restarts a day on the production machine, each a sign-in attempt before this."""
        from integrations.icloud.ICloudIntegration import iCloudAuthenticationError
        self._parked(api)
        iCloudAPI.reset_instance()
        fresh = iCloudAPI(config=CONFIG)
        with patch('integrations.icloud.ICloudIntegration.PyiCloudService') as mock_pyicloud:
            with pytest.raises(iCloudAuthenticationError, match="parked"):
                fresh.check_health()
            assert fresh.get_auth_status()["status"] == "parked"
        mock_pyicloud.assert_not_called()

    def test_the_users_refresh_lifts_the_park_and_signs_in(self, api, icloud_session_dir):
        self._parked(api)
        with patch('integrations.icloud.ICloudIntegration.PyiCloudService') as mock_pyicloud:
            mock_pyicloud.return_value = _service([{"content": [_record("fmip-dad")]}])
            api.refresh_connection(by_user=True)
        assert not (icloud_session_dir / api.PARK_FILE).exists()
        mock_pyicloud.return_value.authenticate.assert_called_once()
        assert api.get_auth_status()["status"] == "authenticated"

    def test_an_automatic_refresh_keeps_the_park(self, api, icloud_session_dir):
        from integrations.icloud.ICloudIntegration import iCloudAuthenticationError
        self._parked(api)
        with patch('integrations.icloud.ICloudIntegration.PyiCloudService') as mock_pyicloud:
            with pytest.raises(iCloudAuthenticationError, match="parked"):
                api.refresh_connection()
        mock_pyicloud.assert_not_called()
        assert (icloud_session_dir / api.PARK_FILE).exists()

    def test_the_auto_2fa_flow_requests_nothing_while_parked(self, api):
        self._parked(api)
        api._api.requires_2fa = True
        api._challenge_from_fresh_signin = False
        with patch.object(api, "_fresh_password_signin") as fresh, patch.object(api, "request_sms_2fa") as sms:
            api.on_needs_2fa()
        fresh.assert_not_called()
        sms.assert_not_called()
