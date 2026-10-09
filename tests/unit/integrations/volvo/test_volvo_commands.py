"""`volvo_login()` posts the link and what to do with it as the command's result and a system
note; `volvo_login_code(pasted)` finishes the flow, posts a note and re-checks the integration."""
from unittest.mock import Mock, patch

import pytest

from assistant.commands.Commands import Commands
from assistant.integration.OAuthPaste import SignIn
from integrations.volvo.VolvoAuth import REDIRECT_URI

MODULE = "integrations.volvo.VolvoCommands"
URL = "https://volvoid.eu.volvocars.com/as/authorization.oauth2?client_id=app-id&state=st-1"
PASTED = f"{REDIRECT_URI}?code=c0de&state=st-1"


def _integration(finish=""):
    integration = Mock()
    integration.login_begin.return_value = SignIn(URL, "st-1", REDIRECT_URI)
    integration.login_finish.return_value = finish
    return integration


def _monitor(integration):
    monitor = Mock()
    monitor.find.return_value = integration
    return monitor


@pytest.mark.unit
class TestCmdVolvoLogin:
    def test_runs_in_the_background_on_the_config_lane(self, commands_instance):
        with patch.object(commands_instance, "background_command") as bg:
            assert commands_instance.run("volvo_login", [""]) == ""
        bg.assert_called_once_with(commands_instance._owner("volvo_login")._run_volvo_login, (False,), lane=Commands.CONFIG_LANE)

    def test_the_link_and_the_paste_instruction_come_back_as_the_result_and_a_note(self, commands_instance):
        integration = _integration()
        with patch.object(Commands, "_integration_monitor", return_value=_monitor(integration)), patch(f"{MODULE}.system_note") as note:
            reply = commands_instance._owner("volvo_login")._run_volvo_login()

        assert URL in reply and "volvo_login_code" in reply and "localhost:18723" in reply and "will not load" in reply
        assert note.call_args.args[1] == reply
        integration.login_begin.assert_called_once_with()

    def test_a_chat_that_asked_gets_the_link_in_the_reply_alone(self, commands_instance):
        integration = _integration()
        with patch.object(Commands, "_integration_monitor", return_value=_monitor(integration)), patch(f"{MODULE}.system_note") as note:
            reply = commands_instance._owner("volvo_login")._run_volvo_login(from_chat=True)
        assert URL in reply
        note.assert_not_called()

    def test_a_missing_client_and_a_disabled_integration_are_errors(self, commands_instance):
        integration = _integration()
        integration.login_begin.side_effect = RuntimeError("volvo.oauth.client_id and volvo.oauth.client_secret are not both set")
        with patch.object(Commands, "_integration_monitor", return_value=_monitor(integration)), patch(f"{MODULE}.system_note") as note:
            text = commands_instance._owner("volvo_login")._run_volvo_login()
        assert text.startswith("volvo_login failed") and "client_secret" in text
        note.assert_not_called()

        disabled = Mock(spec=["state"])
        disabled.state = Mock(value="disabled")
        with patch.object(Commands, "_integration_monitor", return_value=_monitor(disabled)):
            assert 'enable_integration("Volvo")' in commands_instance._owner("volvo_login")._run_volvo_login()

    def test_an_unconfigured_integration_names_its_keys(self, commands_instance):
        not_configured = Mock(spec=["state"])
        not_configured.state = Mock(value="not_configured")
        with patch.object(Commands, "_integration_monitor", return_value=_monitor(not_configured)):
            text = commands_instance._owner("volvo_login")._run_volvo_login()
        assert "volvo.vcc_api_key" in text and "volvo.oauth.client_id" in text


@pytest.mark.unit
class TestCmdVolvoLoginCode:
    def test_the_pasted_address_goes_to_the_background_unquoted_and_whole(self, commands_instance):
        with patch.object(commands_instance, "background_command") as bg:
            assert commands_instance.run("volvo_login_code", [f'"{PASTED}"']) == ""
        bg.assert_called_once_with(commands_instance._owner("volvo_login_code")._run_volvo_login_code, (PASTED, False), lane=Commands.CONFIG_LANE)

    def test_without_anything_pasted_it_refuses(self, commands_instance):
        with patch.object(commands_instance, "background_command") as bg:
            assert commands_instance.run("volvo_login_code", [""]).startswith("volvo_login_code failed")
        bg.assert_not_called()

    def test_a_finished_sign_in_is_a_note_and_a_health_check(self, commands_instance):
        integration = _integration()
        monitor = _monitor(integration)
        with patch.object(Commands, "_integration_monitor", return_value=monitor), patch(f"{MODULE}.system_note") as note:
            reply = commands_instance._owner("volvo_login_code")._run_volvo_login_code(PASTED)

        integration.login_finish.assert_called_once_with(PASTED)
        assert "signed in" in reply and note.call_args.args[1] == reply
        monitor.check_now.assert_called_once_with("Volvo")

    def test_a_failed_exchange_is_the_reason_and_no_note(self, commands_instance):
        integration = _integration(finish="the code expired or was already used — run volvo_login() for a new link")
        monitor = _monitor(integration)
        with patch.object(Commands, "_integration_monitor", return_value=monitor), patch(f"{MODULE}.system_note") as note:
            reply = commands_instance._owner("volvo_login_code")._run_volvo_login_code("c0de")

        assert "expired" in reply and "volvo_login()" in reply and "signed in" not in reply
        note.assert_not_called()
        monitor.check_now.assert_not_called()

    def test_an_exchange_that_raises_is_the_reason_not_an_escaped_exception(self, commands_instance):
        integration = _integration()
        integration.login_finish.side_effect = RuntimeError("token endpoint timed out")
        monitor = _monitor(integration)
        with patch.object(Commands, "_integration_monitor", return_value=monitor), patch(f"{MODULE}.system_note") as note:
            reply = commands_instance._owner("volvo_login_code")._run_volvo_login_code(PASTED)

        assert "token endpoint timed out" in reply and "RuntimeError" in reply and "signed in" not in reply
        note.assert_not_called()
