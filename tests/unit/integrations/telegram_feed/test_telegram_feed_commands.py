from unittest.mock import Mock, patch

from assistant.commands.Commands import Commands


class TestCmdTelegramLogin:
    """`telegram_login(phone)`: console only, the login itself on the integration."""

    @staticmethod
    def _monitor(integration, restart=None):
        monitor = Mock()
        monitor.find.return_value = integration
        monitor.restart.return_value = restart
        return monitor

    def test_runs_in_the_background_from_the_console(self, commands_instance):
        commands_instance._current_context = Mock(source="Console", conversation="tui")
        with patch.object(commands_instance, "background_command") as bg:
            assert commands_instance.run("telegram_login", ['"+46 70 123 45 67"']) == ""
        bg.assert_called_once_with(commands_instance._owner("telegram_login")._run_telegram_login, ("+46701234567", "Console", "tui"),
                                   lane=Commands.CONFIG_LANE)

    def test_refused_from_telegram_and_without_a_chat(self, commands_instance):
        with patch.object(commands_instance, "background_command") as bg:
            commands_instance._current_context = Mock(source="Telegram", conversation=123)
            text = commands_instance.run("telegram_login", ["+46701234567"])
            assert text.startswith("telegram_login failed: it only works from the console chat")
            assert "invalidates a login code" in text
            commands_instance._current_context = None
            assert commands_instance.run("telegram_login", ["+46701234567"]).startswith("telegram_login failed")
        bg.assert_not_called()

    def test_phone_needs_a_country_code(self, commands_instance):
        commands_instance._current_context = Mock(source="Console", conversation="tui")
        with patch.object(commands_instance, "background_command") as bg:
            for bad in ("0701234567", "+46", "", "+46abc"):
                assert commands_instance.run("telegram_login", [bad]).startswith(
                    "telegram_login failed: expected the phone number with its country code")
        bg.assert_not_called()

    def test_run_signs_in_then_restarts_the_integration(self, commands_instance):
        integration = Mock(name="Telegram Feed")
        integration.name = "Telegram Feed"
        integration.login.return_value = None
        status = {"name": "Telegram Feed", "state": "ok", "last_error": None, "config_keys": []}
        monitor = self._monitor(integration, {"restartable": True, "status": status})
        fake = Mock()
        fake.request_secret.return_value = "accepted"
        with patch.object(Commands, "_integration_monitor", return_value=monitor), \
             patch("integrations.telegram_feed.TelegramFeedCommands.notifier", return_value=fake):
            reply = commands_instance._owner("telegram_login")._run_telegram_login("+46701234567", "Console", "tui")
            phone, ask = integration.login.call_args.args
            # The prompt the integration opens is addressed to the asking chat.
            ask("Telegram login code", "details", lambda v: "ok")
        assert reply == "Telegram Feed signed in. Telegram Feed restarted: ok"
        assert phone == "+46701234567"
        fake.request_secret.assert_called_once()
        assert fake.request_secret.call_args.kwargs["source"] == "Console"
        assert fake.request_secret.call_args.kwargs["conversation"] == "tui"
        monitor.restart.assert_called_once_with("Telegram Feed")

    def test_run_reports_a_failed_login_without_restarting(self, commands_instance):
        integration = Mock()
        integration.login.return_value = "Telegram Feed login failed: the user cancelled the code prompt — nothing was signed in"
        monitor = self._monitor(integration)
        with patch.object(Commands, "_integration_monitor", return_value=monitor):
            assert commands_instance._owner("telegram_login")._run_telegram_login("+46701234567") == integration.login.return_value
        monitor.restart.assert_not_called()

    def test_run_on_a_disabled_or_missing_integration(self, commands_instance):
        from assistant.integration.Integration import IntegrationState
        placeholder = Mock(spec=["state"])
        placeholder.state = IntegrationState.DISABLED
        with patch.object(Commands, "_integration_monitor", return_value=self._monitor(placeholder)):
            assert commands_instance._owner("telegram_login")._run_telegram_login("+46701234567") == \
                'Telegram Feed is disabled — enable_integration("Telegram Feed") first.'
        with patch.object(Commands, "_integration_monitor", return_value=self._monitor(None)):
            assert commands_instance._owner("telegram_login")._run_telegram_login("+46701234567") == \
                "Telegram Feed is not available — set telegram_feed.api_id and api_hash and enable it first."
