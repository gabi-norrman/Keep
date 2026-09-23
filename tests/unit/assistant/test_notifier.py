"""Notifier: the core's outbound side channel. A missing notifier must drop everything
quietly — and must never approve a sensitive command."""
from unittest.mock import Mock

from assistant.Notifier import Notifier, NullNotifier, notifier, set_notifier


class TestNullNotifier:
    def test_send_methods_report_the_drop(self):
        # False = nothing reached the user, so commands can tell the LLM instead of claiming success.
        n = NullNotifier()
        assert n.send_message("hi") is False
        assert n.send_message("hi", disable_notification=True, disable_web_page_preview=True) is False
        assert n.send_command_notification("Turning on kitchen", ephemeral=True) is None
        assert n.send_command_notification("Turning on kitchen", source="Console", conversation="tui") is None
        assert n.complete_command_notification(None, "done after 1s") is None
        assert n.send_photo(b"jpeg", caption="cap") is False
        assert n.send_photo_album([(b"a", "one"), (b"b", "two")]) is False
        assert n.send_video(b"mp4", caption="clip", thumbnail=b"jpg") is False
        assert n.send_video(b"mp4") is False

    def test_request_approval_is_unavailable_not_approve(self):
        # CommandProcessor only executes on "approve"; "unavailable" blocks the command
        # with an explanatory error — so no notifier == sensitive commands never run.
        assert NullNotifier().request_approval("send_imessage", details="x", timeout=1) == "unavailable"

    def test_request_secret_is_unavailable_and_never_calls_accept(self):
        # vault_request / config_request answer "unavailable" with a hint to open a chat UI;
        # the accept callback is what would store the value, so it must not run.
        accept = Mock()
        assert NullNotifier().request_secret("vault_request", details="x", accept=accept, timeout=1) == "unavailable"
        accept.assert_not_called()

class TestSetNotifier:
    def test_set_and_get(self):
        fake = Mock()
        set_notifier(fake)
        assert notifier() is fake
        notifier().send_message("hello")
        fake.send_message.assert_called_once_with("hello")

    def test_none_restores_null_notifier(self):
        set_notifier(Mock())
        set_notifier(None)
        assert isinstance(notifier(), NullNotifier)


class TestChatHubSatisfiesProtocol:
    def test_chat_hub_instance_passes_runtime_check(self):
        from interfaces.chat.ChatHub import ChatHub
        hub = ChatHub.__new__(ChatHub)
        assert isinstance(hub, Notifier)
