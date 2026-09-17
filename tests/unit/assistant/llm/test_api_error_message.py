"""api_error_message turns an SDK exception into the line the user reads — the
server's own words when it sent any, None when the failure never reached a server."""
import httpx
import pytest
from anthropic import APIConnectionError, APITimeoutError, BadRequestError

from assistant.llm.AbstractLLM import api_error_message

BILLING = "Your credit balance is too low to access the Anthropic API. Please go to Plans & Billing to upgrade or purchase credits."


def _anthropic_error(status: int, body, message: str = "Error code: 400 - {...}"):
    request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    response = httpx.Response(status, request=request)
    return BadRequestError(message, response=response, body=body)


@pytest.mark.unit
class TestApiErrorMessage:
    def test_anthropic_status_error_yields_the_servers_message(self):
        """The 2026-09-08 prod error: a 400 whose body says the credit balance is too low."""
        e = _anthropic_error(400, {"type": "error", "error": {"type": "invalid_request_error", "message": BILLING},
                                   "request_id": "req_test_0000000000000000"})
        assert api_error_message(e) == BILLING

    def test_mid_stream_error_event_carries_the_same_body(self):
        """The SDK raises the same class for an `error` SSE event, with str(e) being the raw
        dict — the body, not the string, is what we read."""
        body = {"type": "error", "error": {"details": None, "type": "invalid_request_error", "message": BILLING}}
        e = _anthropic_error(400, body, message=str(body))
        assert api_error_message(e) == BILLING

    def test_body_without_message_falls_back_to_the_sdk_message(self):
        e = _anthropic_error(529, {"error": {"type": "overloaded_error"}}, message="Overloaded")
        assert api_error_message(e) == "Overloaded"

    def test_status_without_any_message_names_the_status(self):
        e = _anthropic_error(502, "<html>Bad Gateway</html>", message="   ")
        assert api_error_message(e) == "HTTP 502"

    def test_gemini_shape_uses_code_and_message(self):
        class GeminiLikeError(Exception):
            code = 429
            message = "Resource has been exhausted"
        assert api_error_message(GeminiLikeError()) == "Resource has been exhausted"

    def test_connection_error_is_not_a_server_message(self):
        request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
        assert api_error_message(APIConnectionError(request=request)) is None
        assert api_error_message(APITimeoutError(request=request)) is None

    def test_transport_and_plain_exceptions_yield_none(self):
        assert api_error_message(httpx.RemoteProtocolError("incomplete chunked read")) is None
        assert api_error_message(RuntimeError("503 UNAVAILABLE")) is None
