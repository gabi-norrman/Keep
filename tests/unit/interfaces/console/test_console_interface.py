"""ConsoleInterface wires the TUI's chat transport onto the ChatHub and installs the hub as
the Notifier, exactly like the Telegram interface does."""
from unittest.mock import MagicMock, patch

import pytest

from assistant.Notifier import NullNotifier, notifier, set_notifier
from interfaces.chat.ChatHub import ChatHub
from interfaces.console.ConsoleInterface import ConsoleInterface
from interfaces.console.ConsoleTransport import ConsoleTransport


@pytest.fixture(autouse=True)
def clean_singletons():
    ChatHub.reset_instance()
    yield
    ChatHub.reset_instance()
    set_notifier(NullNotifier())


def test_start_registers_the_console_transport_and_the_notifier():
    assistant = MagicMock()
    history = assistant.prompt_manager.message_history
    history.get_merged_entries.return_value = [
        {"role": "system", "content": "Server started up successfully.", "timestamp": 1000.0, "source": "startup"}]
    interface = ConsoleInterface()
    with patch("interfaces.console.framework.TUIServer.TUIServer") as server_cls, \
            patch("interfaces.console.TUIFloxyConsole.TUIFloxyConsole.get_instance") as get_console:
        interface.start(assistant, app=None)

        hub = ChatHub.get_instance()
        assert hub.assistant is assistant
        assert isinstance(interface.transport, ConsoleTransport)
        assert hub.transports == [interface.transport]
        assert notifier() is hub
        assert interface.transport.owner_conversations() == ["tui"]
        # The console got the same pane the transport draws into
        assert get_console.call_args.args[3] is interface.transport.pane
        # The console (dashboard numbers) and the feed (incoming voice requests) both observe
        assert [c.args[0] for c in assistant.subscribe.call_args_list] == [get_console.return_value, interface.feed]
        server_cls.return_value.start.assert_called_once()
        # The transport is attended only while a TUI client is connected to the server
        server_cls.return_value.clients = set()
        assert interface.transport.attended() is False
        server_cls.return_value.clients = {"a socket"}
        assert interface.transport.attended() is True
        # The history feed backfilled the pane and follows the history from now on
        assert interface.transport.feed is interface.feed
        assert [e.body.text for e in interface.transport.pane.entries] == ["Server started up successfully."]
        history.subscribe.assert_called_once_with(interface.feed._on_change)

        interface.stop()
        assert hub.transports == []
        history.unsubscribe.assert_called_once_with(interface.feed._on_change)
        server_cls.return_value.stop.assert_called_once()
