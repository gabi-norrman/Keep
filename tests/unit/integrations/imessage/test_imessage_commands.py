from unittest.mock import Mock, patch


class TestCmdSendImessage:
    def test_returns_send_result_synchronously(self, commands_instance):
        with patch("integrations.imessage.IMessageIntegration.IMessageIntegration") as MockCls:
            inst = Mock()
            inst.send.return_value = ""
            MockCls.get_instance.return_value = inst
            result = commands_instance.run("send_imessage", ["Charlotte", "On my way home"])
        assert result == ""
        inst.send.assert_called_once_with(
            "Charlotte", f"On my way home\n\n{commands_instance.AI_MESSAGE_SUFFIX}"
        )

    def test_empty_recipient_returns_error_without_calling_send(self, commands_instance):
        with patch("integrations.imessage.IMessageIntegration.IMessageIntegration") as MockCls:
            result = commands_instance.run("send_imessage", ["", "just a body"])
            MockCls.get_instance.assert_not_called()
        assert "send_imessage failed" in result

    def test_empty_body_returns_error_without_calling_send(self, commands_instance):
        with patch("integrations.imessage.IMessageIntegration.IMessageIntegration") as MockCls:
            result = commands_instance.run("send_imessage", ["Alice", "  "])
            MockCls.get_instance.assert_not_called()
        assert "send_imessage failed" in result

    def test_literal_backslash_n_converted_to_newline(self, commands_instance):
        with patch("integrations.imessage.IMessageIntegration.IMessageIntegration") as MockCls:
            inst = Mock()
            inst.send.return_value = ""
            MockCls.get_instance.return_value = inst
            commands_instance.run("send_imessage", ["Bob", "line one\\nline two"])
        sent = inst.send.call_args[0][1]
        assert "line one\nline two" in sent
        assert "\\n" not in sent.split(commands_instance.AI_MESSAGE_SUFFIX)[0]

    def test_literal_crlf_and_tab_escapes_converted(self, commands_instance):
        with patch("integrations.imessage.IMessageIntegration.IMessageIntegration") as MockCls:
            inst = Mock()
            inst.send.return_value = ""
            MockCls.get_instance.return_value = inst
            commands_instance.run("send_imessage", ["Bob", "a\\r\\nb\\tc"])
        sent = inst.send.call_args[0][1]
        assert "a\nb\tc" in sent
        assert "\\r" not in sent and "\\t" not in sent

    def test_real_newlines_in_body_preserved(self, commands_instance):
        with patch("integrations.imessage.IMessageIntegration.IMessageIntegration") as MockCls:
            inst = Mock()
            inst.send.return_value = ""
            MockCls.get_instance.return_value = inst
            commands_instance.run("send_imessage", ["Bob", "line one\nline two"])
        sent = inst.send.call_args[0][1]
        assert "line one\nline two" in sent
