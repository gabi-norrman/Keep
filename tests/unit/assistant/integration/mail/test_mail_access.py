"""MailAccess: the accounts of every enabled and configured MailSource as one mailbox — fan-out
with per-account headers, routing by id prefix, the sending account, and the setup hint when
there is no account at all."""
from typing import List
from unittest.mock import Mock, PropertyMock, patch

import pytest

from assistant.integration.Integration import Integration, IntegrationState
from assistant.integration.IntegrationMonitor import IntegrationMonitor
from assistant.integration.mail.MailAccess import MailAccess, HINT
from assistant.integration.mail.MailAccount import MailAccount
from assistant.integration.mail.MailSource import MailSource


def _account(name: str, id_prefix: str, fetch_result="", search_result="", read_result="", preset="", address=""):
    account = Mock(spec=MailAccount)
    type(account).name = PropertyMock(return_value=name)
    type(account).id_prefix = PropertyMock(return_value=id_prefix)
    type(account).address = PropertyMock(return_value=address)
    account.preset = preset
    account.fetch_latest.return_value = fetch_result
    account.search.return_value = search_result
    account.read.return_value = read_result
    account.move.return_value = ""
    account.list_folders.return_value = ""
    account.send.return_value = ""
    return account


@pytest.mark.unit
class TestFetchLatest:
    def test_merges_results_from_several_accounts_under_headers(self):
        gmail = _account("gmail", "GM", fetch_result="[GM-AA] Mar 15 | From: a@example.com | Subject: Hi")
        icloud = _account("icloud", "IC", fetch_result="[IC-AA] Mar 16 | From: c@example.com | Subject: Hey")

        result = MailAccess([gmail, icloud]).fetch_latest(None)

        assert "--- Gmail ---" in result and "--- Icloud ---" in result
        assert "[GM-AA]" in result and "[IC-AA]" in result
        assert result.index("--- Gmail ---") < result.index("--- Icloud ---")

    def test_forwards_the_arguments_to_every_account(self):
        gmail, icloud = _account("gmail", "GM", fetch_result="x"), _account("icloud", "IC", fetch_result="y")

        MailAccess([gmail, icloud]).fetch_latest("@SaneLater", True)

        gmail.fetch_latest.assert_called_once_with("@SaneLater", True)
        icloud.fetch_latest.assert_called_once_with("@SaneLater", True)

    def test_a_single_account_answers_bare_without_a_header(self):
        gmail = _account("gmail", "GM", fetch_result="[GM-AA] email")

        assert MailAccess([gmail]).fetch_latest(None) == "[GM-AA] email"

    def test_one_failing_account_keeps_the_others(self):
        gmail = _account("gmail", "GM", fetch_result="[GM-AA] email")
        icloud = _account("icloud", "IC")
        icloud.fetch_latest.side_effect = Exception("Connection failed")

        result = MailAccess([gmail, icloud]).fetch_latest(None)

        assert "[GM-AA]" in result
        assert "Error" in result and "Connection failed" in result

    def test_every_account_failing_reports_each(self):
        gmail, icloud = _account("gmail", "GM"), _account("icloud", "IC")
        gmail.fetch_latest.side_effect = Exception("Gmail down")
        icloud.fetch_latest.side_effect = Exception("iCloud down")

        result = MailAccess([gmail, icloud]).fetch_latest(None)

        assert "Gmail down" in result and "iCloud down" in result

    def test_search_and_folders_fan_out_the_same_way(self):
        gmail = _account("gmail", "GM", search_result="[GM-AA] invoice")
        icloud = _account("icloud", "IC", search_result="[IC-AA] invoice")
        gmail.list_folders.return_value = "Archive"
        icloud.list_folders.return_value = "Sent Messages"
        access = MailAccess([gmail, icloud])

        assert "[GM-AA]" in access.search("invoice") and "[IC-AA]" in access.search("invoice")
        folders = access.list_folders()
        assert "Archive" in folders and "Sent Messages" in folders


@pytest.mark.unit
class TestRead:
    def test_routes_by_prefix_and_strips_it(self):
        gmail = _account("gmail", "GM", read_result="Gmail email body")
        icloud = _account("icloud", "IC", read_result="iCloud email body")

        assert MailAccess([gmail, icloud]).read("  GM-AA  ") == "Gmail email body"
        gmail.read.assert_called_once_with("AA")
        icloud.read.assert_not_called()

    def test_a_bare_id_is_tried_on_each_account_until_found(self):
        gmail = _account("gmail", "GM", read_result="Email with ID 42 not found.")
        icloud = _account("icloud", "IC", read_result="Found email body")

        assert MailAccess([gmail, icloud]).read("42") == "Found email body"

    def test_a_bare_id_nowhere_is_not_found(self):
        gmail = _account("gmail", "GM", read_result="Email with ID 42 not found.")
        icloud = _account("icloud", "IC", read_result="Email with ID 42 not found.")

        assert "not found" in MailAccess([gmail, icloud]).read("42")

    def test_an_unknown_prefix_falls_through_to_the_scan(self):
        gmail = _account("gmail", "GM", read_result="Email with ID XX-AA not found.")
        icloud = _account("icloud", "IC", read_result="Found the email!")

        assert MailAccess([gmail, icloud]).read("XX-AA") == "Found the email!"

    def test_a_single_account_gets_the_id_as_given(self):
        gmail = _account("gmail", "GM", read_result="body")

        MailAccess([gmail]).read("42")

        gmail.read.assert_called_once_with("42")


@pytest.mark.unit
class TestMoveAndSubject:
    def test_move_routes_by_prefix(self):
        gmail, icloud = _account("gmail", "GM"), _account("icloud", "IC")
        gmail.move.return_value = "Moved email AA to @SaneBlackHole."

        result = MailAccess([gmail, icloud]).move("GM-AA", "@SaneBlackHole")

        assert result == "Moved email AA to @SaneBlackHole."
        gmail.move.assert_called_once_with("AA", "@SaneBlackHole")
        icloud.move.assert_not_called()

    def test_move_of_an_unroutable_id_across_accounts_is_refused(self):
        gmail, icloud = _account("gmail", "GM"), _account("icloud", "IC")

        result = MailAccess([gmail, icloud]).move("42", "Archive")

        assert "Could not route" in result
        gmail.move.assert_not_called()

    def test_move_with_a_single_account_passes_a_bare_id_through(self):
        gmail = _account("gmail", "GM")
        gmail.move.return_value = "Moved email 42 to Archive."

        assert MailAccess([gmail]).move("42", "Archive") == "Moved email 42 to Archive."

    def test_subject_routes_by_prefix(self):
        gmail, icloud = _account("gmail", "GM"), _account("icloud", "IC")
        gmail.subject_for.return_value = "Order confirmation"

        assert MailAccess([gmail, icloud]).subject_for("GM-AA") == "Order confirmation"
        gmail.subject_for.assert_called_once_with("AA")
        icloud.subject_for.assert_not_called()

    def test_subject_of_a_bare_or_unknown_id_across_accounts_is_none(self):
        gmail, icloud = _account("gmail", "GM"), _account("icloud", "IC")
        access = MailAccess([gmail, icloud])

        assert access.subject_for("42") is None and access.subject_for("XX-AA") is None
        gmail.subject_for.assert_not_called()


@pytest.mark.unit
class TestSender:
    def test_the_icloud_preset_is_preferred_over_the_first_account(self):
        gmail = _account("Gmail", "GM", preset="gmail")
        icloud = _account("iCloud Email", "IC", preset="icloud")

        assert MailAccess([gmail, icloud]).sender() is icloud

    def test_without_an_icloud_account_the_first_one_sends(self):
        gmail = _account("Gmail", "GM", preset="gmail")
        work = _account("Work", "WO")

        assert MailAccess([gmail, work]).sender() is gmail

    def test_a_named_account_wins_whatever_its_preset(self):
        gmail = _account("Gmail", "GM", preset="gmail")
        icloud = _account("iCloud Email", "IC", preset="icloud")

        assert MailAccess([gmail, icloud]).sender("gmail") is gmail

    def test_an_unknown_name_falls_back_to_the_default(self):
        gmail = _account("Gmail", "GM", preset="gmail")
        icloud = _account("iCloud Email", "IC", preset="icloud")

        assert MailAccess([gmail, icloud]).sender("Work") is icloud

    def test_send_goes_through_the_sender(self):
        gmail = _account("Gmail", "GM", preset="gmail")
        icloud = _account("iCloud Email", "IC", preset="icloud")

        assert MailAccess([gmail, icloud]).send("alice@example.com", "Hi", "Hello") == ""
        icloud.send.assert_called_once_with("alice@example.com", "Hi", "Hello")
        gmail.send.assert_not_called()

    def test_account_for_matches_the_address_whatever_its_case(self):
        gmail = _account("Gmail", "GM", address="Marcus@example.com")
        icloud = _account("iCloud Email", "IC", address="m@mlab.test")
        access = MailAccess([gmail, icloud])

        assert access.account_for("marcus@example.com") is gmail
        assert access.account_for("nobody@example.com") is None


@pytest.mark.unit
class TestNoAccount:
    def test_every_operation_answers_with_the_setup_hint(self):
        access = MailAccess([])

        assert access.fetch_latest().startswith("Email is not configured")
        assert "list_integrations" in access.search("x")
        assert access.read("GM-AA") == HINT and access.move("GM-AA", "Archive") == HINT
        assert access.list_folders() == HINT
        assert access.subject_for("GM-AA") is None
        assert access.sender() is None

    def test_sending_without_an_account_is_the_hint_not_an_exception(self):
        assert MailAccess([]).send("alice@example.com", "Hi", "Hello") == HINT


class _Source(Integration, MailSource):
    """A MailSource whose config keys decide whether the monitor sees it as configured."""
    def __init__(self, name: str, keys: List[str], accounts):
        super().__init__()
        self._name, self._keys, self._accounts = name, keys, accounts

    @property
    def name(self) -> str:
        return self._name

    @property
    def config_keys(self) -> List[str]:
        return self._keys

    def check_health(self) -> bool:
        return True

    def mail_accounts(self):
        return self._accounts


class _Plain(Integration):
    """An integration that brings no mail."""
    @property
    def name(self) -> str:
        return "Tesla"

    @property
    def config_keys(self) -> List[str]:
        return []

    def check_health(self) -> bool:
        return True


class _Switchable(Integration, MailSource):
    @property
    def name(self) -> str:
        return "Switchable Mail"

    @property
    def config_keys(self) -> List[str]:
        return []

    def check_health(self) -> bool:
        return True

    def mail_accounts(self):
        return [_account("switchable", "SW")]


@pytest.mark.unit
class TestGatheringThroughTheMonitor:
    @pytest.fixture(autouse=True)
    def monitor(self):
        IntegrationMonitor.reset_instance()
        yield IntegrationMonitor.get_instance()
        IntegrationMonitor.reset_instance()
        _Switchable.reset_instance()

    def test_the_accounts_of_every_configured_source_come_in_registration_order(self, monitor):
        gmail, icloud = _account("Gmail", "GM"), _account("iCloud Email", "IC")
        monitor.register(_Source("Gmail", [], [gmail]))
        monitor.register(_Source("iCloud Email", [], [icloud]))
        monitor.register(_Plain())

        assert MailAccess.current().accounts == [gmail, icloud]

    def test_a_not_configured_source_contributes_nothing(self, monitor):
        gmail = _account("Gmail", "GM")
        unconfigured = _Source("Work Mail", ["fictional_mail_section_nobody_has"], [_account("Work", "WO")])
        monitor.register(_Source("Gmail", [], [gmail]))
        monitor.register(unconfigured)

        assert unconfigured.state is IntegrationState.NOT_CONFIGURED
        assert MailAccess.current().accounts == [gmail]

    def test_a_disabled_source_is_never_built(self, monitor):
        with patch.object(Integration, "enabled", classmethod(lambda cls, config: False)):
            monitor.register_class(_Switchable)

        assert MailAccess.current().accounts == []
        assert MailAccess.current().fetch_latest() == HINT
