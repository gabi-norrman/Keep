from typing import List, Optional, Tuple

from interfaces.console import Theme
from interfaces.console.framework.TUIScreen import Color
from interfaces.console.tabs.AuthTab import AuthTab, PENDING
from interfaces.console.widgets.Board import SummaryTile, Table
from common.logging import info, trace
from integrations.hyundai.HyundaiIntegration import HyundaiIntegration


class HyundaiTab(AuthTab):
    """The Bluelink / Kia Connect sign-in — the one-time code some regions ask for, sent by
    e-mail or SMS and typed in here — and the cars the account has with their last state."""

    integration = HyundaiIntegration

    HELP = "Ctrl-R sign in / refresh  Ctrl-A code by e-mail  Ctrl-S code by SMS  Ctrl-E enter the code"
    SUBMITTING_MESSAGE = "Verifying the one-time code..."
    STATUS_LABELS = {
        **AuthTab.STATUS_LABELS,
        "needs_auth": ("Code needed", Theme.COLOR_YELLOW),
    }

    def __init__(self):
        super().__init__()
        self._cars: list = []

    def _on_status(self, api):
        manager = getattr(api, "_manager_instance", None)
        self._cars = list(manager.vehicles.values()) if manager is not None and getattr(manager, "token", None) else []

    @staticmethod
    def _charge(vehicle) -> str:
        battery, fuel = getattr(vehicle, "ev_battery_percentage", None), getattr(vehicle, "fuel_level", None)
        parts = [f"{battery}% battery" for battery in [battery] if battery is not None]
        parts += [f"{fuel:g}% fuel" for fuel in [fuel] if fuel is not None]
        return ", ".join(parts) or PENDING

    @staticmethod
    def _updated(vehicle) -> str:
        when = getattr(vehicle, "last_updated_at", None)
        return when.strftime("%Y-%m-%d %H:%M") if when is not None else PENDING

    def _table(self) -> Table:
        rows = [(Theme.COLOR_GREEN, [(v.name or PENDING, Theme.COLOR_TEXT), (v.model or PENDING, Theme.COLOR_LABEL),
                                     (v.VIN or PENDING, Theme.COLOR_DIM), (self._charge(v), Theme.COLOR_TEXT),
                                     (self._updated(v), Theme.COLOR_LABEL)])
                for v in self._cars]
        return Table("Cars", [("Car", 18), ("Model", 16), ("VIN", 18), ("Charge", 20), ("Car data from", None)],
                     rows, "sign in to see the cars")

    def _tiles(self) -> List[SummaryTile]:
        if not self._cars:
            return [("Cars", PENDING, Theme.COLOR_DIM, [("sign in to see them", Theme.COLOR_DIM)])]
        return [("Cars", str(len(self._cars)), Theme.COLOR_BRIGHT, [("on the account", Theme.COLOR_LABEL)])]

    def _entry_hint(self) -> Optional[Tuple[str, Color]]:
        if self.status == "needs_auth":
            return "Ctrl-A / Ctrl-S sends the code, Ctrl-E enters it", Theme.COLOR_YELLOW
        return None

    def _handle_key(self, key: str) -> bool:
        if key == '\x01':            # Ctrl-A: the code to the account's e-mail address
            self._send_code("email")
            return True
        if key == '\x13':            # Ctrl-S: the code by SMS
            self._send_code("sms")
            return True
        return super()._handle_key(key)

    def _send_code(self, channel: str):
        def body(api):
            self._status_message = api.send_otp(channel)
            self._check_auth_status()
        self._act(f"Sending the code by {channel}...", body)

    def _verify_code(self, api, code: str):
        self._status_message = api.verify_otp(code)
        self._check_auth_status()
        if self.status == "authenticated":
            self._check_health_now()

    def _refresh(self):
        self._status_message = "Signing in..."

        def refresh():
            api = self._get_api()
            if api:
                try:
                    api.refresh_now()
                    self._status_message = "Connected — cached state refreshed"
                    self._check_health_now()
                except Exception as e:
                    trace(f"Hyundai tab: refresh did not complete: {e}")
                    self._status_message = f"{e}"
            self._check_auth_status()
            info(f"Hyundai tab: refresh done, status {self.status}")

        self._run(refresh)
