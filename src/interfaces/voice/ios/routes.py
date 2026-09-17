"""The /stream_text endpoint: streams the reply's text token-by-token as NDJSON,
correlated with an in-flight /stream request via request_id, so the iOS app can show
the spoken text as it is generated. Registered by the launcher when voice is enabled."""
import asyncio
import functools
import json
import queue

from quart import Blueprint, Response, request

from common.logging import info, trace
from interfaces.voice.ios.TextStreamRegistry import TextStreamRegistry

IDLE_TIMEOUT_SECONDS = 30.0
# Backstop for any /stream path that returns without ever closing the stream (the
# known early-returns close explicitly, but this guards unforeseen ones too):
# after this many consecutive idle keepalives with no producer, give up so the
# queue + connection + executor thread can't be held open indefinitely.
MAX_IDLE_PINGS = 20  # ~10 minutes of total silence


def build_blueprint(registry: TextStreamRegistry) -> Blueprint:
    bp = Blueprint('ios', __name__)

    @bp.route('/stream_text', methods=['GET'])
    async def stream_text():
        request_id = request.args.get('request_id', '')
        if not request_id:
            return {"error": "request_id required"}, 400

        info(f"Text stream client connected for request {request_id}")
        q = registry.get_or_create(request_id)

        async def generate():
            loop = asyncio.get_event_loop()
            token_count = 0
            idle_pings = 0
            try:
                while True:
                    try:
                        event = await loop.run_in_executor(None, functools.partial(q.get, True, IDLE_TIMEOUT_SECONDS))
                    except queue.Empty:
                        # Producer (the /stream request) may still be running a slow command or
                        # background op; don't tear down the queue. Emit a keepalive so a dead
                        # client surfaces as a write failure, and keep waiting for tokens/done.
                        idle_pings += 1
                        if idle_pings > MAX_IDLE_PINGS:
                            info(f"Text stream for request {request_id} idle past {MAX_IDLE_PINGS} keepalives "
                                 f"({MAX_IDLE_PINGS * IDLE_TIMEOUT_SECONDS:.0f}s) with no producer; closing to avoid a leak.")
                            break
                        trace(f"Text stream for request {request_id} idle for {IDLE_TIMEOUT_SECONDS}s, "
                              f"sending keepalive ({idle_pings}/{MAX_IDLE_PINGS})")
                        yield json.dumps({"type": "ping"}) + "\n"
                        continue
                    idle_pings = 0
                    if event is None:  # end-of-stream sentinel
                        yield json.dumps({"type": "done"}) + "\n"
                        break
                    token_count += 1
                    yield json.dumps(event) + "\n"
            finally:
                registry.discard(request_id)
                info(f"Text stream for request {request_id} ended after {token_count} tokens")

        response = Response(generate(), headers={'Content-Type': 'application/x-ndjson; charset=utf-8',
                                                 'Cache-Control': 'no-cache, no-store, must-revalidate'})
        response.timeout = None  # Disable Quart's 60s RESPONSE_TIMEOUT for streaming
        return response

    return bp


def register(app, registry: TextStreamRegistry) -> None:
    app.register_blueprint(build_blueprint(registry))
    info("iOS text-stream route registered (/stream_text).")
