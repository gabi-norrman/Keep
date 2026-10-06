from unittest.mock import Mock, patch

import pytest

from integrations.x.XIntegration import XIntegration


@pytest.fixture
def mock_x_integration():
    with patch.object(XIntegration, "get_instance", return_value=Mock()) as get_instance:
        yield get_instance.return_value


class TestSearchX:
    def test_search_x(self, commands_instance, mock_x_integration):
        with patch.object(commands_instance, 'background_command') as mock_bg:
            commands_instance.run("search_x", ["starship launch"])
            mock_bg.assert_called_once_with(mock_x_integration.search, "starship launch")
