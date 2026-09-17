"""The /stream_text NDJSON overlay endpoint, driven through Quart's test client
(moved out of the launcher in Phase 3; it previously had no test)."""
import json

import pytest
from quart import Quart

from interfaces.voice.ios.TextStreamRegistry import TextStreamRegistry
from interfaces.voice.ios.routes import register


@pytest.fixture
def registry():
    TextStreamRegistry.reset_instance()
    yield TextStreamRegistry.get_instance()
    TextStreamRegistry.reset_instance()


@pytest.fixture
def app(registry):
    test_app = Quart(__name__)
    register(test_app, registry)
    return test_app


@pytest.mark.unit
class TestStreamText:
    @pytest.mark.asyncio
    async def test_requires_request_id(self, app):
        response = await app.test_client().get('/stream_text')
        assert response.status_code == 400

    @pytest.mark.asyncio
    async def test_streams_published_events_until_close(self, app, registry):
        registry.publish("req-1", {"type": "user", "name": "Marcus"})
        registry.publish("req-1", {"type": "token", "text": "Hello"})
        registry.close("req-1")

        response = await app.test_client().get('/stream_text?request_id=req-1')
        assert response.status_code == 200
        assert response.headers["Content-Type"].startswith("application/x-ndjson")
        body = (await response.get_data()).decode()
        events = [json.loads(line) for line in body.strip().split("\n")]
        assert events == [{"type": "user", "name": "Marcus"},
                          {"type": "token", "text": "Hello"},
                          {"type": "done"}]
        # The queue is discarded once the consumer has drained it.
        assert "req-1" not in registry._queues
