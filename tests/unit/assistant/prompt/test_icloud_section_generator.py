import pytest
from unittest.mock import Mock, patch, MagicMock


class TestiCloudSectionGenerator:
    @patch('assistant.prompt.sections.iCloudSectionGenerator.iCloudAPI')
    def test_fetch_content_returns_device_list(self, mock_icloud_cls):
        mock_api = Mock()
        mock_api.get_devices_list.return_value = "iPhone, iPad, MacBook"
        mock_icloud_cls.get_instance.return_value = mock_api
        from assistant.prompt.sections.iCloudSectionGenerator import iCloudSectionGenerator
        section = iCloudSectionGenerator(None)
        result = section._fetch_content()
        assert result == "iPhone, iPad, MacBook"

    @patch('assistant.prompt.sections.iCloudSectionGenerator.iCloudAPI')
    def test_fetch_content_on_configuration_error(self, mock_icloud_cls):
        from assistant.integration.integrations.ICloudIntegration import iCloudConfigurationError
        mock_icloud_cls.get_instance.side_effect = iCloudConfigurationError("no config")
        from assistant.prompt.sections.iCloudSectionGenerator import iCloudSectionGenerator
        section = iCloudSectionGenerator(None)
        result = section._fetch_content()
        assert result == "No devices configured"

    @patch('assistant.prompt.sections.iCloudSectionGenerator.iCloudAPI')
    def test_fetch_content_propagates_other_errors(self, mock_icloud_cls):
        mock_icloud_cls.get_instance.side_effect = RuntimeError("unexpected")
        from assistant.prompt.sections.iCloudSectionGenerator import iCloudSectionGenerator
        section = iCloudSectionGenerator(None)
        with pytest.raises(RuntimeError, match="unexpected"):
            section._fetch_content()

    @patch('assistant.prompt.sections.iCloudSectionGenerator.iCloudSectionGenerator._wait', side_effect=StopIteration)
    @patch('assistant.prompt.sections.iCloudSectionGenerator.iCloudAPI')
    @patch('assistant.prompt.PromptSectionGenerator.time.sleep')
    def test_monitoring_updates_before_each_wait(self, mock_base_sleep, mock_icloud_cls, mock_sleep):
        """Re-read every cycle so a device configured through config_set shows up after the restart."""
        mock_api = Mock()
        mock_api.get_devices_list.return_value = "iPhone"
        mock_icloud_cls.get_instance.return_value = mock_api
        from assistant.prompt.sections.iCloudSectionGenerator import iCloudSectionGenerator
        section = iCloudSectionGenerator(None)
        with patch.object(section, '_update_content') as mock_update:
            try:
                section._start_monitoring()
            except StopIteration:
                pass
            mock_update.assert_called_once()
            mock_sleep.assert_called_once_with(60)

    @patch('assistant.prompt.sections.iCloudSectionGenerator.iCloudAPI')
    def test_fetch_content_single_device(self, mock_icloud_cls):
        mock_api = Mock()
        mock_api.get_devices_list.return_value = "iPhone"
        mock_icloud_cls.get_instance.return_value = mock_api
        from assistant.prompt.sections.iCloudSectionGenerator import iCloudSectionGenerator
        section = iCloudSectionGenerator(None)
        result = section._fetch_content()
        assert result == "iPhone"

    @patch('assistant.prompt.sections.iCloudSectionGenerator.iCloudAPI')
    def test_fetch_content_empty_device_list_points_at_the_setup_commands(self, mock_icloud_cls):
        mock_api = Mock()
        mock_api.get_devices_list.return_value = ""
        mock_icloud_cls.get_instance.return_value = mock_api
        from assistant.prompt.sections.iCloudSectionGenerator import iCloudSectionGenerator
        section = iCloudSectionGenerator(None)
        result = section._fetch_content()
        assert result.startswith("none configured yet")
        assert "icloud_devices()" in result and "config keys that add one" in result

    @patch('assistant.prompt.sections.iCloudSectionGenerator.iCloudAPI')
    def test_fetch_content_handles_authentication_error(self, mock_icloud_cls):
        from assistant.integration.integrations.ICloudIntegration import iCloudAuthenticationError
        mock_icloud_cls.get_instance.side_effect = iCloudAuthenticationError("auth failed")
        from assistant.prompt.sections.iCloudSectionGenerator import iCloudSectionGenerator
        section = iCloudSectionGenerator(None)
        with pytest.raises(iCloudAuthenticationError, match="auth failed"):
            section._fetch_content()

    @patch('assistant.prompt.sections.iCloudSectionGenerator.iCloudAPI')
    def test_fetch_content_calls_get_instance(self, mock_icloud_cls):
        mock_api = Mock()
        mock_api.get_devices_list.return_value = "iPhone"
        mock_icloud_cls.get_instance.return_value = mock_api
        from assistant.prompt.sections.iCloudSectionGenerator import iCloudSectionGenerator
        section = iCloudSectionGenerator(None)
        section._fetch_content()
        mock_icloud_cls.get_instance.assert_called_once()
        mock_api.get_devices_list.assert_called_once()

    def test_section_name_is_icloud_devices(self):
        from assistant.prompt.sections.iCloudSectionGenerator import iCloudSectionGenerator
        section = iCloudSectionGenerator(None)
        assert section.section_name == "iCloud Devices"

    @patch('assistant.prompt.sections.iCloudSectionGenerator.iCloudSectionGenerator._wait', side_effect=StopIteration)
    @patch('assistant.prompt.sections.iCloudSectionGenerator.iCloudAPI')
    def test_monitoring_sleeps_60_seconds(self, mock_icloud_cls, mock_sleep):
        mock_api = Mock()
        mock_api.get_devices_list.return_value = "iPhone"
        mock_icloud_cls.get_instance.return_value = mock_api
        from assistant.prompt.sections.iCloudSectionGenerator import iCloudSectionGenerator
        section = iCloudSectionGenerator(None)
        try:
            section._start_monitoring()
        except StopIteration:
            pass
        mock_sleep.assert_called_once_with(60)

    @patch('assistant.prompt.PromptSectionGenerator.time.sleep')
    @patch('assistant.prompt.sections.iCloudSectionGenerator.iCloudAPI')
    def test_update_content_sets_error_after_repeated_failures(self, mock_icloud_cls, mock_sleep):
        mock_icloud_cls.get_instance.side_effect = RuntimeError("connection timeout")
        from assistant.prompt.sections.iCloudSectionGenerator import iCloudSectionGenerator
        section = iCloudSectionGenerator(None)
        section._update_content()
        assert "ERROR" in section.content
        assert "connection timeout" in section.content

    @patch('assistant.prompt.sections.iCloudSectionGenerator.iCloudAPI')
    def test_update_content_notifies_prompt_manager(self, mock_icloud_cls):
        mock_api = Mock()
        mock_api.get_devices_list.return_value = "iPhone, MacBook"
        mock_icloud_cls.get_instance.return_value = mock_api
        from assistant.prompt.sections.iCloudSectionGenerator import iCloudSectionGenerator
        mock_pm = MagicMock()
        section = iCloudSectionGenerator(mock_pm)
        section._update_content()
        mock_pm.on_section_updated.assert_called_with("iCloud Devices")

    @patch('assistant.prompt.sections.iCloudSectionGenerator.iCloudAPI')
    def test_get_content_returns_stored_value(self, mock_icloud_cls):
        mock_api = Mock()
        mock_api.get_devices_list.return_value = "iPhone, iPad"
        mock_icloud_cls.get_instance.return_value = mock_api
        from assistant.prompt.sections.iCloudSectionGenerator import iCloudSectionGenerator
        section = iCloudSectionGenerator(None)
        section._update_content()
        assert section.get_content() == "iPhone, iPad"
