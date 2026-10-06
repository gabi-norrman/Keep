import threading
from typing import Dict, List, Optional

from assistant.integration.Integration import Integration, IntegrationState
from assistant.integration.IntegrationMonitor import IntegrationMonitor
from common.logging import info
from interfaces.console.framework.TUIScreen import TUIScreen
from interfaces.console.tabs import IntegrationTabs
from interfaces.console.tabs.DisabledTab import DisabledTab
from interfaces.console.tabs.IntegrationTab import IntegrationTab
from interfaces.console.tabs.TabGroup import TabGroup


class IntegrationsGroup(TabGroup):
    """The Integrations tab: a sub-tab per enabled integration that brings one, then the Disabled tab.

    The row follows the IntegrationMonitor: a tab is on it while its integration is registered
    and not disabled (not configured and waiting for 2FA count as enabled — the tab is where
    that gets fixed), in alphabetical order, with the Disabled tab always last so the group is
    never empty. The monitor is read every frame and the row rebuilt only when the set changed,
    which covers a console built before the integrations register as well as
    `enable_integration` / `restart_integration` at any later time. A tab is built the first
    time its integration is enabled and dropped when it is disabled: a disabled integration's
    tab never runs, so it cannot build the integration the monitor left unbuilt."""

    def __init__(self, tab_classes: Optional[List[type[IntegrationTab]]] = None):
        classes = IntegrationTabs.discover() if tab_classes is None else tab_classes
        self._tab_classes: Dict[type[Integration], type[IntegrationTab]] = {cls.integration: cls for cls in classes}
        self._built: Dict[type[Integration], IntegrationTab] = {}
        self._shown: Optional[frozenset] = None
        self._lock = threading.Lock()
        self._on_screen = False
        self._chosen = False     # the user has picked a sub-tab; until then the first one is selected
        self._disabled_tab = DisabledTab(frozenset(self._tab_classes))
        super().__init__("Integrations", [self._disabled_tab])

    def refresh(self):
        """Bring the row in line with the monitor; nothing happens while the enabled set is the same."""
        enabled = frozenset(getattr(integration, "cls", type(integration))
                            for integration in IntegrationMonitor.get_instance().of_type(Integration)
                            if integration.state is not IntegrationState.DISABLED)
        shown = enabled & self._tab_classes.keys()
        if shown == self._shown:
            return
        with self._lock:     # drawing and key handling both come here, on their own threads
            if shown == self._shown:
                return
            self._rebuild(shown)

    def _rebuild(self, shown: frozenset):
        current = self.content_tab()
        for integration in [i for i in self._built if i not in shown]:
            del self._built[integration]
        for integration in shown - self._built.keys():
            try:
                self._built[integration] = self._tab_classes[integration]()
            except Exception as e:
                info(f"Integrations: the {self._tab_classes[integration].__name__} could not be built, left out: "
                     f"{type(e).__name__}: {e}")
        tabs = sorted(self._built.values(), key=lambda tab: tab.name.lower()) + [self._disabled_tab]
        if current not in tabs:
            index = min(self.index, len(tabs) - 1)   # the one that took the lost tab's place
        else:
            index = tabs.index(current) if self._chosen else 0
        # In the order that leaves `tabs[index]` valid for a frame drawn in between
        if index < len(self.tabs):
            self.index, self.tabs = index, tabs
        else:
            self.tabs, self.index = tabs, index
        self._shown = shown
        selected = self.content_tab()
        info(f"Integrations sub-tabs now: {', '.join(tab.name for tab in tabs)}"
             + (f"; {current.name} gave way to {selected.name}." if selected is not current else "."))
        if selected is not current and self._on_screen:
            current.on_deactivate()
            selected.on_activate()

    def select(self, delta: int):
        self.refresh()
        self._chosen = True
        super().select(delta)

    def enter(self, from_end: bool):
        self.refresh()
        self._chosen = True
        super().enter(from_end)

    def draw(self, screen: TUIScreen, frame: int):
        self.refresh()
        super().draw(screen, frame)

    def on_activate(self):
        self.refresh()
        self._on_screen = True
        super().on_activate()

    def on_deactivate(self):
        self._on_screen = False
        super().on_deactivate()
