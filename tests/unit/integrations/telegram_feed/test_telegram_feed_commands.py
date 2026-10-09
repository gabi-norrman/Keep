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


class TestCmdSendTelegram:
    """`send_telegram(recipient, message)`: the body with the AI footer goes to the feed's `send`."""

    @staticmethod
    def _with_feed(feed):
        monitor = Mock()
        monitor.find.return_value = feed
        return patch.object(Commands, "_integration_monitor", return_value=monitor)

    def test_sends_with_the_ai_suffix(self, commands_instance):
        feed = Mock()
        feed.send.return_value = ""
        with self._with_feed(feed):
            assert commands_instance.run("send_telegram", ["Charlotte", "On my way home"]) == ""
        recipient, body = feed.send.call_args.args
        assert recipient == "Charlotte"
        assert body.startswith("On my way home") and "AI" in body

    def test_an_unquoted_body_is_rejoined_on_its_commas_and_escapes_become_newlines(self, commands_instance):
        feed = Mock()
        feed.send.return_value = ""
        with self._with_feed(feed):
            commands_instance.run("send_telegram", ["Bob", "late", "sorry\\nsee you"])
        body = feed.send.call_args.args[1]
        assert body.startswith("late, sorry\nsee you")

    def test_the_feeds_refusal_is_the_reply(self, commands_instance):
        feed = Mock()
        feed.send.return_value = "unknown recipient: Zed (not among the account's chats or contacts)"
        with self._with_feed(feed):
            assert commands_instance.run("send_telegram", ["Zed", "hi"]) == feed.send.return_value

    def test_missing_parts_send_nothing(self, commands_instance):
        feed = Mock()
        with self._with_feed(feed):
            assert "send_telegram failed" in commands_instance.run("send_telegram", ["", "body"])
            assert "send_telegram failed" in commands_instance.run("send_telegram", ["Bob", "  "])
            assert "send_telegram failed" in commands_instance.run("send_telegram", ["Bob"])
        feed.send.assert_not_called()

    def test_a_disabled_or_missing_feed_names_its_state(self, commands_instance):
        disabled = Mock(spec=["state"])
        disabled.state = Mock(value="disabled")
        with self._with_feed(disabled):
            assert "Telegram Feed is disabled" in commands_instance.run("send_telegram", ["Bob", "hi"])
        with self._with_feed(None):
            assert "Telegram Feed is not available" in commands_instance.run("send_telegram", ["Bob", "hi"])

    def test_the_approval_card_shows_the_resolved_chat(self, commands_instance):
        owner = commands_instance._owner("send_telegram")
        feed = Mock()
        feed.resolve_recipient.return_value = (Mock(username="bobby"), "Bob Example", None)
        with self._with_feed(feed):
            assert owner.describe_send_telegram('"Bob", "hi"') == "Sending Telegram message to Bob Example (@bobby)"
        feed.resolve_recipient.return_value = (Mock(username=None), "Family", None)
        with self._with_feed(feed):
            assert owner.describe_send_telegram("Family, hi") == "Sending Telegram message to Family"
        feed.resolve_recipient.return_value = (None, None, "unknown recipient: Carol")
        with self._with_feed(feed):
            assert owner.describe_send_telegram("Carol, hi") == "Sending Telegram message to Carol — unknown recipient: Carol"

    def test_the_card_survives_a_lookup_failure_and_an_absent_feed(self, commands_instance):
        owner = commands_instance._owner("send_telegram")
        feed = Mock()
        feed.resolve_recipient.side_effect = RuntimeError("loop gone")
        with self._with_feed(feed):
            assert owner.describe_send_telegram("Bob, hi") == "Sending Telegram message to Bob"
        with self._with_feed(None):
            assert owner.describe_send_telegram("Bob, hi") == "Sending Telegram message to Bob"
        assert owner.describe_send_telegram("") == "Sending Telegram message"
