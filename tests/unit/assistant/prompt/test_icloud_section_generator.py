import pytest
from unittest.mock import Mock, patch


class TestiCloudSectionGenerator:
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
    def test_fetch_content_empty_device_list_points_at_the_setup_commands(self, mock_icloud_cls):
        mock_api = Mock()
        mock_api.get_devices_list.return_value = ""
        mock_icloud_cls.get_instance.return_value = mock_api
        from assistant.prompt.sections.iCloudSectionGenerator import iCloudSectionGenerator
        section = iCloudSectionGenerator(None)
        result = section._fetch_content()
        assert result.startswith("none configured yet")
        assert "icloud_devices()" in result and "config keys that add one" in result
