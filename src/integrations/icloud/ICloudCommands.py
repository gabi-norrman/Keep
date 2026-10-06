from assistant.commands.CommandSet import CommandSet
from common.logging import info
from integrations.icloud.ICloudIntegration import iCloudAPI


class ICloudCommands(CommandSet):
    """The Find My commands: ring a phone, locate its owner, list the account's devices."""

    @staticmethod
    def _get_icloud_api() -> iCloudAPI:
        return iCloudAPI.get_instance()

    def cmd_find_phone(self, parameter_string: str):
        self.fire_and_forget(self._get_icloud_api().play_sound, parameter_string)

    def cmd_locate_user(self, parameter_string: str):
        self.background_command(self._get_icloud_api().get_location, parameter_string)

    def cmd_icloud_devices(self, parameter_string: str = ""):
        self.background_command(self._run_icloud_devices, "")

    def _run_icloud_devices(self, _="") -> str:
        """Every device the iCloud account sees, with its id and the icloud.devices entry it
        is configured in — the material for finishing the setup with config_set."""
        api = self._get_icloud_api()
        devices = api.get_icloud_devices()
        if not devices:
            info("icloud_devices: the account listed no devices")
            return ("iCloud listed no devices — the login may be incomplete (list_integrations shows the "
                    "iCloud state; a 2FA code is entered in the console's iCloud tab).")
        lines = []
        for d in devices:
            model = f" ({d['model']})" if d.get("model") else ""
            if d.get("configured_as"):
                status = f"configured as '{d['configured_as']}' (icloud.devices[{d['slot']}])"
            else:
                status = "not configured"
            lines.append(f"{d['name']}{model} — id {d['id']} — {status}")
        n = api.configured_device_count()
        info(f"icloud_devices: {len(lines)} device(s), {n} configured")
        return "\n".join(lines) + (f"\n\nicloud.devices has {n} entr{'y' if n == 1 else 'ies'}; the next one is "
                                    f"icloud.devices[{n}] (keys id and display_name).")

    def describe_find_phone(self, p):             return "Pinging phone"
    def describe_locate_user(self, p):            return f"Locating {p.strip()}" if p.strip() else "Locating user"
    def describe_icloud_devices(self, p):         return "Listing iCloud devices"
