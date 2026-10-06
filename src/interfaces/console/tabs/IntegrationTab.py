from typing import ClassVar

from assistant.integration.Integration import Integration
from interfaces.console.tabs.TabView import TabView


class IntegrationTab(TabView):
    """A sub-tab of Integrations that belongs to one integration and shows only while it is enabled.

    A subclass lives in its integration's folder (`src/integrations/<name>/<Label>Tab.py`) and
    names the integration's class; `IntegrationTabs.discover` finds it and `IntegrationsGroup`
    builds it the first time the integration is enabled — so it may take the integration for
    built and reach it with `self.integration.get_instance()`."""

    integration: ClassVar[type[Integration]]
