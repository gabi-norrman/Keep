from unittest.mock import Mock

import pytest

from integrations.icloud.iCloudTab import iCloudTab


def make_tab(api):
    tab = iCloudTab()
    tab._api = api
    return tab


class TestRequestSmsAndEnterCode:
    def test_pending_request_arms_manual_entry_without_new_sms(self):
        # A second SMS request would invalidate the code already in flight
        # (the stale-code cascade) — Ctrl-E must arm manual entry instead.
        api = Mock()
        api.sms_request_pending.return_value = True
        tab = make_tab(api)

        tab._request_sms_and_enter_code()

        api.request_sms_2fa.assert_not_called()
        assert tab._input_mode is True
        assert tab._input_text == ""
        assert "already requested" in tab._status_message

    def test_no_pending_request_sends_sms_and_arms_entry(self):
        api = Mock()
        api.sms_request_pending.return_value = False
        api.request_sms_2fa.return_value = "•••05"
        tab = make_tab(api)

        tab._request_sms_and_enter_code()

        api.request_sms_2fa.assert_called_once()
        assert tab._input_mode is True
        assert "•••05" in tab._status_message

    def test_sms_request_failure_reports_error(self):
        api = Mock()
        api.sms_request_pending.return_value = False
        api.request_sms_2fa.side_effect = Exception("boom")
        tab = make_tab(api)

        tab._request_sms_and_enter_code()

        assert tab._input_mode is False
        assert "SMS request failed" in tab._status_message


class TestCheckAuthStatus:
    def test_partial_auth_still_fetches_devices(self):
        # partial_auth serves Find My fine — the tab must show the device list,
        # not "(authenticate to see devices)".
        api = Mock()
        api.get_auth_status.return_value = {"status": "partial_auth", "message": ""}
        api.get_icloud_devices.return_value = [{"id": "x", "name": "iPhone"}]
        api.get_devices_list.return_value = "iPhone"
        tab = make_tab(api)

        tab._check_auth_status()

        assert tab._icloud_devices == [{"id": "x", "name": "iPhone"}]
        assert tab._devices_list == "iPhone"

    def test_needs_2fa_does_not_fetch_devices(self):
        api = Mock()
        api.get_auth_status.return_value = {"status": "needs_2fa", "message": ""}
        tab = make_tab(api)

        tab._check_auth_status()

        api.get_icloud_devices.assert_not_called()

    def test_device_fetch_failure_leaves_empty_lists(self):
        api = Mock()
        api.get_auth_status.return_value = {"status": "authenticated", "message": ""}
        api.get_icloud_devices.side_effect = Exception("network")
        api.get_devices_list.side_effect = Exception("network")
        tab = make_tab(api)

        tab._check_auth_status()

        assert tab._icloud_devices == []
        assert tab._devices_list == ""


class TestCtrlE:
    @pytest.mark.parametrize("status, opens", [
        ("needs_2fa", True), ("partial_auth", True), ("authenticated", False), ("error", False),
        ("not_initialized", False),
    ])
    def test_ctrl_e_only_starts_the_code_entry_when_a_second_factor_is_wanted(self, status, opens):
        # Requesting an SMS on an already verified session would invalidate a code in flight.
        api = Mock()
        api.sms_request_pending.return_value = False
        api.request_sms_2fa.return_value = "•••05"
        tab = make_tab(api)
        tab._auth_status = {"status": status, "message": ""}
        assert tab.handle_input('\x05') is True
        assert tab._input_mode is opens
        assert api.request_sms_2fa.called is opens


class TestAutoTwoFactor:
    def test_an_auto_verified_code_cancels_a_half_typed_one_and_rechecks(self):
        api = Mock()
        api.auto_2fa_message = "2FA auto-verified from iMessage"
        api.get_auth_status.return_value = {"status": "authenticated", "message": ""}
        tab = make_tab(api)
        tab._run = lambda fn: fn()
        tab._auth_status = {"status": "needs_2fa", "message": ""}
        tab._input_mode, tab._input_text = True, "123"
        tab._before_draw()
        assert tab._status_message == "2FA auto-verified from iMessage"
        assert tab._input_mode is False and tab._input_text == ""
        api.get_auth_status.assert_called_once()
        assert tab.status == "authenticated"

    def test_no_api_and_no_message_leave_the_tab_alone(self):
        tab = iCloudTab()
        tab._connect = lambda: (_ for _ in ()).throw(RuntimeError("not configured"))
        tab._input_mode, tab._input_text = True, "12"
        tab._before_draw()
        assert tab._input_mode is True and tab._input_text == "12"
        api = Mock(auto_2fa_message=None)
        tab = make_tab(api)
        tab._input_mode, tab._input_text = True, "12"
        tab._before_draw()
        assert tab._input_mode is True and tab._input_text == "12"
        api.get_auth_status.assert_not_called()
