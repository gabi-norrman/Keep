from unittest.mock import Mock, patch

import pytest

from integrations.icloud.ICloudIntegration import iCloudAPI


@pytest.fixture
def icloud_api():
    with patch.object(iCloudAPI, "get_instance", return_value=Mock()) as get_instance:
        yield get_instance.return_value


class TestCmdIcloudDevices:
    def test_lists_ids_slots_and_the_next_index(self, commands_instance, icloud_api):
        icloud_api.get_icloud_devices.return_value = [
            {"id": "uuid-dad", "name": "Dad's iPhone", "model": "iPhone 15", "class": "iPhone", "configured_as": "dad", "slot": 0},
            {"id": "uuid-anna", "name": "Anna's iPad", "model": "", "class": "iPad", "configured_as": None, "slot": None},
        ]
        icloud_api.configured_device_count.return_value = 1
        text = commands_instance._owner("icloud_devices")._run_icloud_devices()
        lines = text.splitlines()
        assert lines[0] == "Dad's iPhone (iPhone 15) — id uuid-dad — configured as 'dad' (icloud.devices[0])"
        assert lines[1] == "Anna's iPad — id uuid-anna — not configured"
        assert lines[-1] == "icloud.devices has 1 entry; the next one is icloud.devices[1] (keys id and display_name)."

    def test_empty_listing_points_at_the_login(self, commands_instance, icloud_api):
        icloud_api.get_icloud_devices.return_value = []
        assert "login may be incomplete" in commands_instance._owner("icloud_devices")._run_icloud_devices()
