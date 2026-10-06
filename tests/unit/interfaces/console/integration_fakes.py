"""A fictional set of integrations and their tabs for the Integrations group's tests."""
from assistant.integration.Feed import Feed
from assistant.integration.Integration import Integration
from assistant.integration.IntegrationMonitor import DisabledIntegration, IntegrationMonitor
from interfaces.console.tabs.IntegrationTab import IntegrationTab


def named(label: str) -> property:
    """A constant `name`, the kind `Integration.config_name` can read off the class."""
    return property(lambda self: label)


class FakeIntegration(Integration):
    @property
    def config_keys(self):
        return []

    def check_health(self):
        return True


class LampIntegration(FakeIntegration):
    """Switches the reading lamp.

    Turns it on at dusk and off when the room is empty.

    It also knows how long it has been on."""
    name = named("Lamp")


class KettleIntegration(FakeIntegration):
    """Boils water
    on request.

    The second paragraph is not part of the description."""
    sensitive = True
    name = named("Kettle")


class RadioIntegration(FakeIntegration, Feed):
    name = named("Radio")
    feed_name = "radio"
    config_slug = "radio"

    def initial_cursor(self, horizon_ts):
        return None

    def fetch(self, cursor, backfill):
        raise NotImplementedError


class FakeTab(IntegrationTab):
    built: list = []

    def __init__(self):
        self.events = []
        FakeTab.built.append(self)

    def draw(self, screen, frame):
        self.events.append("draw")

    def handle_input(self, key):
        return False

    def on_activate(self):
        self.events.append("activate")

    def on_deactivate(self):
        self.events.append("deactivate")


class LampTab(FakeTab):
    integration = LampIntegration


class KettleTab(FakeTab):
    integration = KettleIntegration


class RadioTab(FakeTab):
    integration = RadioIntegration


def enable(monitor: IntegrationMonitor, cls: type):
    monitor.register(cls())


def disable(monitor: IntegrationMonitor, cls: type):
    monitor.register(DisabledIntegration(cls))
