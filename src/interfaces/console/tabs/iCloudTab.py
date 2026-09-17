from typing import Optional, Tuple

from interfaces.console.framework.TUIScreen import TUIScreen, Color
from interfaces.console.tabs.AuthTab import AuthTab, COLOR_GRAY, COLOR_GREEN, COLOR_WHITE, COLOR_YELLOW
from assistant.integration.integrations.ICloudIntegration import iCloudAPI, iCloudAuthenticationError
from assistant.integration.IntegrationMonitor import IntegrationMonitor

COLOR_DEVICE_ID = Color(100, 100, 100)
DEVICE_ID_X = 60
VERIFIED_STATES = ("authenticated", "partial_auth")   # partial_auth still serves Find My


class iCloudTab(AuthTab):
    """iCloud login with its 2FA, and the Find My devices the session sees."""

    TITLE = "iCloud / Find My iPhone"
    SUBMITTING_MESSAGE = "Verifying 2FA code..."
    STATUS_LABELS = {
        **AuthTab.STATUS_LABELS,
        "partial_auth": ("Connected (login not fully verified)", COLOR_GREEN),
        "needs_2fa": ("2FA Required", COLOR_YELLOW),
    }

    def __init__(self):
        super().__init__()
        self._icloud_devices: list = []
        self._devices_list: str = ""

    def _connect(self):
        return iCloudAPI.get_instance()

    def _on_status(self, api):
        if self.status not in VERIFIED_STATES:
            return
        try:
            self._icloud_devices = api.get_icloud_devices() or []
        except Exception:
            self._icloud_devices = []
        try:
            self._devices_list = api.get_devices_list() or ""
        except Exception:
            self._devices_list = ""

    def _before_draw(self):
        """The integration verifies codes from iMessage on its own; show the outcome when it has one."""
        api = self._get_api()
        auto_msg = api.auto_2fa_message if api else None
        if auto_msg:
            self._status_message = auto_msg
            self._input_mode = False
            self._input_text = ""
            self._check_auth_status_async()

    def _draw_section(self, screen: TUIScreen, y: int) -> int:
        y += 2
        screen.set_color(COLOR_WHITE)
        screen.write_at(2, y, "iCloud Devices:")
        screen.reset_color()
        if self.status in VERIFIED_STATES and self._icloud_devices:
            for device in self._icloud_devices:
                y += 1
                screen.set_color(COLOR_GRAY)
                name = device.get("name", "Unknown")
                model = device.get("model", "")
                device_str = f"  {name}" + (f" ({model})" if model else "")
                screen.write_at(2, y, device_str[:screen.width - 4])
                screen.set_color(COLOR_DEVICE_ID)
                screen.write_at(DEVICE_ID_X, y, f"{device.get('id', '')}")
        else:
            y += 1
            screen.set_color(COLOR_GRAY)
            screen.write_at(4, y, "(authenticate to see devices)")

        y += 2
        screen.set_color(COLOR_WHITE)
        screen.write_at(2, y, "Configured for voice commands, Find My pings and location detection:")
        screen.reset_color()
        y += 1
        screen.set_color(COLOR_GRAY)
        screen.write_at(4, y, self._devices_list or "(none yet — ask Keep to set one up, or copy an id above into icloud.devices)")
        return y

    def _entry_hint(self) -> Optional[Tuple[str, Color]]:
        if self.status == "partial_auth":
            return "Auto-2FA on standby — Ctrl-E to verify with an SMS code now (optional)", COLOR_GREEN
        if self.status == "needs_2fa":
            return "Auto-2FA in progress — Ctrl-E to enter the code manually", COLOR_YELLOW
        return None

    def _begin_entry(self):
        if self.status in ("needs_2fa", "partial_auth"):
            self._request_sms_and_enter_code()

    def _request_sms_and_enter_code(self):
        """Request an SMS code (unless one is already on its way — a second request would
        invalidate it), then take the code by hand."""
        api = self._get_api()
        if not api:
            self._status_message = "Error: API not initialized"
            return
        if api.sms_request_pending():
            self._status_message = "SMS code already requested — auto-submit armed; type it to enter manually"
            super()._begin_entry()
            return
        try:
            phone = api.request_sms_2fa()
            self._status_message = f"SMS code sent to {phone}"
            super()._begin_entry()
        except Exception as e:
            self._status_message = f"SMS request failed: {e}"

    def _verify_code(self, api, code: str):
        if api.send_2fa_code(code):
            self._status_message = "2FA verification successful!"
            IntegrationMonitor.get_instance().check_now("iCloud")
            self._check_auth_status()
        else:
            self._status_message = "2FA verification failed. Try again."

    def _refresh(self):
        """Refresh the iCloud session; synchronous, like the check that follows it."""
        self._status_message = "Refreshing connection..."
        api = self._get_api()
        if api:
            try:
                api.refresh_connection()
                self._status_message = "Connection refreshed"
            except iCloudAuthenticationError as e:
                self._status_message = f"Auth error: {e}"
            except Exception as e:
                self._status_message = f"Error: {e}"
        self._check_auth_status()
