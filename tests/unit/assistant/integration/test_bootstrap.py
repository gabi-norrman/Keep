"""bootstrap: the launcher's integration wiring. Every integration is registered by class
so one whose constructor raises (a missing config section) lands as a NOT_CONFIGURED
placeholder the assistant can list and restart, instead of being skipped at startup
(plans/SELF_SETUP_PLAN.md §3.3)."""
from unittest.mock import Mock, patch

import pytest

from assistant.integration import bootstrap
from assistant.integration.Integration import Integration
from assistant.integration.IntegrationMonitor import IntegrationMonitor


@pytest.fixture
def monitor():
    fake = Mock(spec=IntegrationMonitor)
    with patch.object(IntegrationMonitor, "get_instance", return_value=fake):
        yield fake


@pytest.mark.unit
class TestDiscoverIntegrations:
    def test_registers_every_integration_by_class_plus_web_access(self, monitor):
        from assistant.integration.integrations.ICloudIntegration import ICloudIntegration
        from assistant.integration.integrations.TeslaIntegration import TeslaIntegration
        from assistant.integration.integrations.cameras.RingIntegration import RingIntegration
        from assistant.integration.integrations.email.GmailIntegration import GmailAccess
        from assistant.tools.WebAccess import WebAccess

        assert bootstrap.discover_integrations() is monitor

        registered = [call.args[0] for call in monitor.register_class.call_args_list]
        for cls in (ICloudIntegration, TeslaIntegration, RingIntegration, GmailAccess, WebAccess):
            assert registered.count(cls) == 1, cls.__name__
        # Only concrete integrations: never the base class, and none twice even though
        # subpackages re-export what they import.
        assert Integration not in registered
        assert len(registered) == len(set(registered))
        # Nothing is registered by instance — that path skipped a raising constructor.
        monitor.register.assert_not_called()

    def test_feeds_are_left_to_start_feeds(self, monitor):
        from assistant.integration.integrations.IMessageIntegration import IMessageIntegration
        from assistant.integration.integrations.TelegramFeedIntegration import TelegramFeedIntegration
        from assistant.integration.integrations.photos.PhotosIntegration import PhotosIntegration
        bootstrap.discover_integrations()
        registered = [call.args[0] for call in monitor.register_class.call_args_list]
        assert IMessageIntegration not in registered
        assert TelegramFeedIntegration not in registered
        assert PhotosIntegration not in registered


@pytest.mark.unit
class TestStartFeeds:
    def test_feeds_are_restartable_and_start_on_the_runner(self, monitor):
        from assistant.integration.FeedRunner import Feeds
        from assistant.integration.integrations.IMessageIntegration import IMessageIntegration
        from assistant.integration.integrations.TelegramFeedIntegration import TelegramFeedIntegration
        from assistant.integration.integrations.photos.PhotosIntegration import PhotosIntegration
        from assistant.integration.integrations.home.IndigoIntegration import IndigoIntegration
        from assistant.integration.integrations.home.HomeAssistantIntegration import HomeAssistantIntegration
        history = Mock(name="message_history")

        bootstrap.start_feeds(history)

        by_class = {call.args[0]: call.kwargs["start"] for call in monitor.register_class.call_args_list}
        assert set(by_class) == {IMessageIntegration, TelegramFeedIntegration, PhotosIntegration,
                                 IndigoIntegration, HomeAssistantIntegration}
        # The hook is what register_class runs now and again on every restart_integration.
        with patch.object(Feeds, "get_instance") as feeds:
            for cls, start in by_class.items():
                feed = Mock(name=cls.__name__)
                start(feed)
                feeds.return_value.start.assert_called_with(feed, history)
