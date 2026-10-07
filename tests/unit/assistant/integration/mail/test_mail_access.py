"""MailAccess: the accounts of every enabled and configured MailSource as one mailbox — fan-out
with per-account headers, routing by id prefix, the sending account, and the setup hint when
there is no account at all."""
from typing import List
from unittest.mock import Mock, PropertyMock, patch

import pytest

from assistant.integration.Integration import Integration, IntegrationState
from assistant.integration.IntegrationMonitor import IntegrationMonitor
from assistant.integration.mail.MailAccess import MailAccess, HINT, SEND_FROM_KEY
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
    def test_merges_results_from_several_accounts_under_headers_naming_each_with_its_address(self):
        personal = _account("Personal", "PE", fetch_result="[PE-AA] Mar 15 | From: a@example.com | Subject: Hi",
                            address="m@mlab.test")
        work = _account("Work", "WO", fetch_result="[WO-AA] Mar 16 | From: c@example.com | Subject: Hey",
                        address="marcus@example.com")

        result = MailAccess([personal, work]).fetch_latest(None)

        assert "--- Personal (m@mlab.test) ---" in result and "--- Work (marcus@example.com) ---" in result
        assert "[PE-AA]" in result and "[WO-AA]" in result
        assert result.index("--- Personal") < result.index("--- Work")

    def test_an_account_without_a_known_address_is_headed_by_its_name_alone(self):
        result = MailAccess([_account("Personal", "PE", fetch_result="x"), _account("Work", "WO", fetch_result="y")]).fetch_latest(None)
        assert "--- Personal ---" in result and "--- Work ---" in result

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


def _send_from(name):
    """config.yaml's mail.send_from, as MailAccess reads it."""
    config = Mock()
    config.get.side_effect = lambda key, default=None: name if key == SEND_FROM_KEY else default
    return patch("assistant.integration.mail.MailAccess.ConfigManager.get_instance", return_value=config)


@pytest.mark.unit
class TestSender:
    def test_the_account_mail_send_from_names_is_the_default_sender_whatever_its_place(self):
        personal, work = _account("Personal", "PE"), _account("Work", "WO")
        with _send_from("work"):
            assert MailAccess([personal, work]).sender() is work

    def test_without_the_setting_the_first_account_sends(self):
        personal, work = _account("Personal", "PE"), _account("Work", "WO")
        with _send_from(""):
            assert MailAccess([personal, work]).sender() is personal

    def test_a_setting_naming_no_account_falls_back_to_the_first(self):
        personal, work = _account("Personal", "PE"), _account("Work", "WO")
        with _send_from("Gone"):
            assert MailAccess([personal, work]).sender() is personal

    def test_a_named_account_wins_over_the_setting(self):
        personal, work = _account("Personal", "PE"), _account("Work", "WO")
        with _send_from("Personal"):
            assert MailAccess([personal, work]).sender("work") is work

    def test_an_unknown_name_falls_back_to_the_default(self):
        personal, work = _account("Personal", "PE"), _account("Work", "WO")
        with _send_from("Work"):
            assert MailAccess([personal, work]).sender("Gone") is work

    def test_send_goes_through_the_sender_it_was_given(self):
        personal, work = _account("Personal", "PE"), _account("Work", "WO")
        with _send_from(""):
            assert MailAccess([personal, work]).send("alice@example.com", "Hi", "Hello", "Work") == ""
        work.send.assert_called_once_with("alice@example.com", "Hi", "Hello")
        personal.send.assert_not_called()

    def test_named_finds_an_account_whatever_the_case_and_quoting(self):
        personal, work = _account("Personal", "PE"), _account("Work", "WO")
        access = MailAccess([personal, work])
        assert access.named('"work"') is work and access.named("PERSONAL") is personal
        assert access.named("Nope") is None and access.named("") is None

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

    def test_the_hint_names_the_accounts_section_and_the_setup_commands(self):
        assert "imap.accounts" in HINT and "config_request" in HINT and "list_integrations" in HINT


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

    def test_an_account_whose_name_or_prefix_another_integration_took_is_skipped(self, monitor):
        personal = _account("Personal", "PE")
        monitor.register(_Source("IMAP Mail", [], [personal]))
        monitor.register(_Source("Google", [], [_account("personal", "GO"), _account("Pets", "PE"), _account("Work", "WO")]))

        assert [a.name for a in MailAccess.current().accounts] == ["Personal", "Work"]

    def test_a_disabled_source_is_never_built(self, monitor):
        with patch.object(Integration, "enabled", classmethod(lambda cls, config: False)):
            monitor.register_class(_Switchable)

        assert MailAccess.current().accounts == []
        assert MailAccess.current().fetch_latest() == HINT
