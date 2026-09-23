import pytest
import requests

from assistant.integration.integrations.XIntegration import XIntegration, XSearchError


class DummyResponse:
    def __init__(self, status_code=200, json_data=None):
        self.status_code = status_code
        self._json = json_data or {}

    def json(self):
        return self._json

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.exceptions.HTTPError(f"HTTP {self.status_code}")


class FakeSession:
    def __init__(self, response=None):
        self.response = response
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append(("GET", url, kwargs))
        return self.response

    def post(self, url, **kwargs):
        self.calls.append(("POST", url, kwargs))
        return self.response


class FakeConfigManager:
    def __init__(self, api_key="test_grok_key", values=None):
        self.api_key = api_key
        self.values = values or {}

    def get_api_key(self, service):
        assert service == "grok"
        if self.api_key is None:
            raise KeyError("api_keys.grok")
        return self.api_key

    def get(self, key_path, default=None):
        return self.values.get(key_path, default)


def url_citation(url):
    return {"type": "url_citation", "url": url, "start_index": 0, "end_index": 10, "title": "1"}


def search_response(text="People like it.", citations=None, annotations=None):
    return DummyResponse(200, {
        "output": [
            {"type": "reasoning", "content": []},
            {"type": "message", "content": [{"type": "output_text", "text": text, "annotations": annotations or []}]},
        ],
        "citations": citations if citations is not None else ["https://x.com/user/status/1"],
    })


@pytest.mark.unit
def test_search_formats_answer_and_citations():
    session = FakeSession(search_response("Everyone loves pizza.", ["https://x.com/a/status/1", "https://x.com/b/status/2"]))
    api = XIntegration(config_manager=FakeConfigManager(), session=session)

    result = api.search("pizza opinions")

    assert result.startswith("X search results for 'pizza opinions':")
    assert "Everyone loves pizza." in result
    assert "Posts:\nhttps://x.com/a/status/1\nhttps://x.com/b/status/2" in result


@pytest.mark.unit
def test_search_request_shape():
    session = FakeSession(search_response())
    api = XIntegration(config_manager=FakeConfigManager(), session=session)

    api.search("test query")

    method, url, kwargs = session.calls[0]
    assert method == "POST"
    assert url == f"{XIntegration.XAI_API_BASE}/responses"
    assert kwargs["headers"]["Authorization"] == "Bearer test_grok_key"
    payload = kwargs["json"]
    assert payload["model"] == XIntegration.MODEL
    assert payload["tools"] == [{"type": "x_search"}]
    assert payload["reasoning"] == {"effort": "low"}
    assert "test query" in payload["input"][0]["content"]


@pytest.mark.unit
def test_search_reasoning_effort_from_config():
    session = FakeSession(search_response())
    config = FakeConfigManager(values={"x.search_reasoning_effort": "high"})
    api = XIntegration(config_manager=config, session=session)

    api.search("test query")

    assert session.calls[0][2]["json"]["reasoning"] == {"effort": "high"}


@pytest.mark.unit
def test_search_without_citations_omits_posts_section():
    session = FakeSession(search_response(citations=[]))
    api = XIntegration(config_manager=FakeConfigManager(), session=session)

    assert "Posts:" not in api.search("query")


@pytest.mark.unit
def test_search_collects_url_citation_annotations():
    # The real Responses API puts citations on the output_text block, not top-level.
    session = FakeSession(search_response(
        "Two posts.",
        citations=[],
        annotations=[url_citation("https://x.com/a/status/1"), url_citation("https://x.com/b/status/2")],
    ))
    api = XIntegration(config_manager=FakeConfigManager(), session=session)

    result = api.search("query")

    assert "Posts:\nhttps://x.com/a/status/1\nhttps://x.com/b/status/2" in result


@pytest.mark.unit
def test_extract_citations_merges_and_dedupes():
    data = {
        "citations": ["https://x.com/a/status/1"],
        "output": [
            {"type": "reasoning", "content": []},
            {"type": "message", "content": [
                {"type": "output_text", "text": "first", "annotations": [
                    url_citation("https://x.com/a/status/1"),
                    url_citation("https://x.com/b/status/2"),
                    {"type": "file_citation", "file_id": "f1"},
                    {"type": "url_citation"},
                ]},
                {"type": "output_text", "text": "second", "annotations": [url_citation("https://x.com/c/status/3")]},
            ]},
        ],
    }

    assert XIntegration._extract_citations(data) == [
        "https://x.com/a/status/1",
        "https://x.com/b/status/2",
        "https://x.com/c/status/3",
    ]


@pytest.mark.unit
def test_extract_citations_tolerates_missing_annotations():
    data = {"output": [{"type": "message", "content": [{"type": "output_text", "text": "no annotations key"}]}]}

    assert XIntegration._extract_citations(data) == []


@pytest.mark.unit
def test_search_placeholder_key_raises():
    api = XIntegration(config_manager=FakeConfigManager(api_key="YOUR_GROK_API_KEY_HERE"), session=FakeSession())
    with pytest.raises(XSearchError, match="not configured"):
        api.search("query")


@pytest.mark.unit
def test_search_http_error_raises():
    session = FakeSession(DummyResponse(500))
    api = XIntegration(config_manager=FakeConfigManager(), session=session)
    with pytest.raises(XSearchError, match="X search failed"):
        api.search("query")


@pytest.mark.unit
def test_search_no_output_text_raises():
    session = FakeSession(DummyResponse(200, {"output": [], "citations": []}))
    api = XIntegration(config_manager=FakeConfigManager(), session=session)
    with pytest.raises(XSearchError, match="no results"):
        api.search("query")


@pytest.mark.unit
def test_check_health_ok():
    session = FakeSession(DummyResponse(200, {"data": []}))
    api = XIntegration(config_manager=FakeConfigManager(), session=session)

    assert api.check_health() is True
    method, url, kwargs = session.calls[0]
    assert method == "GET"
    assert url == f"{XIntegration.XAI_API_BASE}/models"
    assert kwargs["headers"]["Authorization"] == "Bearer test_grok_key"


@pytest.mark.unit
def test_check_health_failure():
    session = FakeSession(DummyResponse(503))
    api = XIntegration(config_manager=FakeConfigManager(), session=session)

    assert api.check_health() is False
