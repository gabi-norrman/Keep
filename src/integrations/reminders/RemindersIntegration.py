import subprocess
from typing import List

from common.ConfigManager import config
from assistant.integration.Integration import Integration
from common.logging import error


class RemindersIntegration(Integration):
    """Apple Reminders lists.

    Adds items to Apple Reminders lists, such as the shopping list, when asked.

    Goes through AppleScript."""
    sensitive = True

    @property
    def name(self) -> str:
        return "Reminders"

    @property
    def config_keys(self) -> List[str]:
        return ["reminders"]

    def check_health(self) -> bool:
        result = subprocess.run(
            ["osascript", "-e", 'tell application "Reminders" to get name of default list'],
            capture_output=True, text=True, timeout=10
        )
        return result.returncode == 0

    def add_reminder_item(self, item: str, list_name: str = None) -> str:
        if not list_name:
            list_name = config.get("reminders.shopping_list_name")

        escaped = item.replace("\\", "\\\\").replace('"', '\\"')
        escaped_list = list_name.replace("\\", "\\\\").replace('"', '\\"')
        script = (
            f'tell application "Reminders" to tell list "{escaped_list}" '
            f'to make new reminder with properties {{name:"{escaped}"}}'
        )

        try:
            result = subprocess.run(
                ["osascript", "-e", script],
                capture_output=True, text=True, timeout=10
            )
            if result.returncode != 0:
                error(f"osascript failed: {result.stderr.strip()}")
                return f"Failed to add reminder: {result.stderr.strip()}"
            return ""
        except Exception as e:
            error(f"Failed to add reminder: {e}")
            return f"Failed to add reminder: {e}"


def add_reminder_item(item: str, list_name: str = None) -> str:
    return RemindersIntegration.get_instance().add_reminder_item(item, list_name)
