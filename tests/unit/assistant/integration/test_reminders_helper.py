from unittest.mock import patch, Mock


class TestRemindersHelper:
    @patch('assistant.integration.integrations.RemindersIntegration.subprocess')
    @patch('assistant.integration.integrations.RemindersIntegration.config')
    def test_success_returns_empty_string(self, mock_config, mock_subprocess):
        mock_config.get.return_value = "Shoppinglista"
        mock_subprocess.run.return_value = Mock(returncode=0, stderr="")

        from assistant.integration.integrations.RemindersIntegration import add_reminder_item
        result = add_reminder_item("Milk")

        assert result == ""
        mock_subprocess.run.assert_called_once()
        call_args = mock_subprocess.run.call_args
        assert call_args[0][0] == ["osascript", "-e"].__class__.__name__ or "osascript" in call_args[0][0]

    @patch('assistant.integration.integrations.RemindersIntegration.subprocess')
    @patch('assistant.integration.integrations.RemindersIntegration.config')
    def test_failure_returns_error_message(self, mock_config, mock_subprocess):
        mock_config.get.return_value = "Shoppinglista"
        mock_subprocess.run.return_value = Mock(returncode=1, stderr="execution error")

        from assistant.integration.integrations.RemindersIntegration import add_reminder_item
        result = add_reminder_item("Milk")

        assert "Failed to add reminder" in result
        assert "execution error" in result

    @patch('assistant.integration.integrations.RemindersIntegration.subprocess')
    @patch('assistant.integration.integrations.RemindersIntegration.config')
    def test_exception_returns_error_message(self, mock_config, mock_subprocess):
        mock_config.get.return_value = "Shoppinglista"
        mock_subprocess.run.side_effect = TimeoutError("timed out")

        from assistant.integration.integrations.RemindersIntegration import add_reminder_item
        result = add_reminder_item("Milk")

        assert "Failed to add reminder" in result

    @patch('assistant.integration.integrations.RemindersIntegration.subprocess')
    @patch('assistant.integration.integrations.RemindersIntegration.config')
    def test_escapes_double_quotes(self, mock_config, mock_subprocess):
        mock_config.get.return_value = "Shoppinglista"
        mock_subprocess.run.return_value = Mock(returncode=0, stderr="")

        from assistant.integration.integrations.RemindersIntegration import add_reminder_item
        add_reminder_item('Item with "quotes"')

        script = mock_subprocess.run.call_args[0][0][2]
        assert r'\"quotes\"' in script
        assert '"quotes"' not in script.replace(r'\"', '')

    @patch('assistant.integration.integrations.RemindersIntegration.subprocess')
    @patch('assistant.integration.integrations.RemindersIntegration.config')
    def test_escapes_backslashes(self, mock_config, mock_subprocess):
        mock_config.get.return_value = "Shoppinglista"
        mock_subprocess.run.return_value = Mock(returncode=0, stderr="")

        from assistant.integration.integrations.RemindersIntegration import add_reminder_item
        add_reminder_item("path\\to\\item")

        script = mock_subprocess.run.call_args[0][0][2]
        assert "path\\\\to\\\\item" in script

    @patch('assistant.integration.integrations.RemindersIntegration.subprocess')
    def test_uses_custom_list_name(self, mock_subprocess):
        mock_subprocess.run.return_value = Mock(returncode=0, stderr="")

        from assistant.integration.integrations.RemindersIntegration import add_reminder_item
        add_reminder_item("Bread", list_name="Groceries")

        script = mock_subprocess.run.call_args[0][0][2]
        assert 'tell list "Groceries"' in script

    @patch('assistant.integration.integrations.RemindersIntegration.subprocess')
    @patch('assistant.integration.integrations.RemindersIntegration.config')
    def test_reads_list_name_from_config(self, mock_config, mock_subprocess):
        mock_config.get.return_value = "MyList"
        mock_subprocess.run.return_value = Mock(returncode=0, stderr="")

        from assistant.integration.integrations.RemindersIntegration import add_reminder_item
        add_reminder_item("Eggs")

        mock_config.get.assert_called_with("reminders.shopping_list_name")
        script = mock_subprocess.run.call_args[0][0][2]
        assert 'tell list "MyList"' in script
