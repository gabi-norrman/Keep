"""Process-level integration wiring for the launcher: discover every Integration under
assistant/integration/integrations (plus WebAccess) into the IntegrationMonitor, and start
the feeds (iMessage, Telethon, Photos, Indigo, Home Assistant) that pour into the history through FeedRunner. Chat interfaces
(the Telegram bot) register themselves from interfaces/*."""
import importlib
import pkgutil

from common.logging import info


def discover_integrations():
    """Register every Integration subclass found in the integrations package; returns the monitor."""
    from assistant.integration.Feed import Feed
    from assistant.integration.Integration import Integration
    from assistant.integration.IntegrationMonitor import IntegrationMonitor
    import assistant.integration.integrations as integrations_pkg
    from assistant.tools.WebAccess import WebAccess

    monitor = IntegrationMonitor.get_instance()
    seen_classes = set()
    feeds = 0
    for _finder, fqn, _ispkg in pkgutil.walk_packages(integrations_pkg.__path__, integrations_pkg.__name__ + "."):
        module = importlib.import_module(fqn)
        for attr in vars(module).values():
            if isinstance(attr, type) and issubclass(attr, Integration) and attr is not Integration and attr not in seen_classes:
                seen_classes.add(attr)
                if issubclass(attr, Feed):
                    feeds += 1   # registered by start_feeds, with the hook that runs it
                    continue
                monitor.register_class(attr)
    monitor.register_class(WebAccess)
    info(f"Discovered {len(seen_classes)} integrations (+ WebAccess), {feeds} of them feeds.")
    return monitor


def integration_classes() -> list[type]:
    """Every Integration subclass in the integrations package, in the package's walk order."""
    from assistant.integration.Integration import Integration
    import assistant.integration.integrations as integrations_pkg
    found = []
    for _finder, fqn, _ispkg in pkgutil.walk_packages(integrations_pkg.__path__, integrations_pkg.__name__ + "."):
        module = importlib.import_module(fqn)
        for attr in vars(module).values():
            if isinstance(attr, type) and issubclass(attr, Integration) and attr is not Integration \
                    and attr not in found:
                found.append(attr)
    return found


def feed_classes() -> list[type]:
    """The Integration classes that are also Feeds, in the package's walk order."""
    from assistant.integration.Feed import Feed
    return [cls for cls in integration_classes() if issubclass(cls, Feed)]


def start_feeds(message_history):
    """Register every Feed integration with a start hook that hands the instance to
    FeedRunner — run now and again on every restart_integration."""
    from assistant.integration.FeedRunner import Feeds
    from assistant.integration.IntegrationMonitor import IntegrationMonitor
    monitor = IntegrationMonitor.get_instance()
    classes = feed_classes()
    for cls in classes:
        monitor.register_class(cls, start=lambda feed: Feeds.get_instance().start(feed, message_history))
    info(f"Feeds registered: {', '.join(c.__name__.removesuffix('Integration') for c in classes)} "
         f"(each runs only when enabled and configured)")
