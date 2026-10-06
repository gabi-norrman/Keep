from assistant.prompt.PromptSectionGenerator import PromptSectionGenerator
from integrations.icloud.ICloudIntegration import iCloudAPI, iCloudConfigurationError

NO_DEVICES = ("none configured yet — icloud_devices() lists the devices the iCloud account sees "
              "and the config keys that add one")


class iCloudSectionGenerator(PromptSectionGenerator):
    """The configured iCloud devices available to Find My iPhone. Re-read every minute so a
    device configured through config_set shows up once iCloud is restarted."""
    key = "phones"

    def __init__(self, prompt_manager):
        super().__init__(prompt_manager, "iCloud Devices")

    def _start_monitoring(self):
        while not self._stop_event.is_set():
            self._update_content()
            self._wait(60)

    def _fetch_content(self) -> str:
        """The configured device display names, or the setup hint when there are none."""
        try:
            api = iCloudAPI.get_instance()
            return api.get_devices_list() or NO_DEVICES
        except iCloudConfigurationError:
            return "No devices configured"
