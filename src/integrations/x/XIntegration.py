from typing import List, Optional

import requests

from common.ConfigManager import ConfigManager
from common.logging import info, note, trace
from assistant.integration.Integration import Integration


class XSearchError(Exception):
    """Raised when an X search cannot be performed or the xAI API call fails."""


class XIntegration(Integration):
    """Search of X (Twitter) posts.

    Searches the posts on X, so Keep can find what is being said about a topic.

    Goes through xAI's server-side x_search tool. Reading individual posts needs no code here:
    the prompt instructs the LLM to rewrite x.com status URLs to api.fxtwitter.com and fetch
    them with open_url()."""

    XAI_API_BASE = "https://api.x.ai/v1"
    MODEL = "grok-4.6"
    REQUEST_TIMEOUT = 90
    # The search itself runs server-side; high effort only adds tens of seconds of
    # reasoning before the tool call, so keep it low unless config says otherwise.
    DEFAULT_REASONING_EFFORT = "low"

    def __init__(
        self,
        config_manager: Optional[ConfigManager] = None,
        session: Optional[requests.Session] = None,
    ):
        super().__init__()
        self._config_manager = config_manager or ConfigManager()
        self._session = session or requests.Session()

    @property
    def name(self) -> str:
        return "X"

    @property
    def config_keys(self) -> List[str]:
        return ["api_keys.grok"]

    def check_health(self) -> bool:
        response = self._session.get(
            f"{self.XAI_API_BASE}/models",
            headers={"Authorization": f"Bearer {self._api_key()}"},
            timeout=15,
        )
        return response.status_code == 200

    def _api_key(self) -> str:
        try:
            api_key = self._config_manager.get_api_key("grok")
        except KeyError:
            api_key = None
        if not api_key or "YOUR_" in api_key:
            raise XSearchError("xAI (grok) API key not configured.")
        return api_key

    def search(self, query: str) -> str:
        query = (query or "").strip()
        if not query:
            raise XSearchError("search_x requires a non-empty query.")

        effort = self._reasoning_effort()
        info(f"Searching X via xAI x_search for '{query}' (reasoning effort: {effort})")
        payload = {
            "model": self.MODEL,
            "input": [{
                "role": "user",
                "content": (
                    f"Search X for: {query}\n"
                    "Report what you find concisely, and include the URLs of the most relevant posts."
                ),
            }],
            "tools": [{"type": "x_search"}],
            "reasoning": {"effort": effort},
        }
        try:
            response = self._session.post(
                f"{self.XAI_API_BASE}/responses",
                headers={"Authorization": f"Bearer {self._api_key()}"},
                json=payload,
                timeout=self.REQUEST_TIMEOUT,
            )
            response.raise_for_status()
        except requests.exceptions.RequestException as e:
            note(f"xAI x_search request failed: {e}")
            raise XSearchError(f"X search failed: {e}") from e

        data = response.json()
        trace(f"xAI x_search raw response: {data}")

        answer = self._extract_output_text(data)
        if not answer:
            note("xAI x_search returned no output text.")
            raise XSearchError("X search returned no results.")

        citations = self._extract_citations(data)
        info(f"X search for '{query}' returned {len(answer)} chars, {len(citations)} cited posts.")

        result = f"X search results for '{query}':\n\n{answer}"
        if citations:
            result += "\n\nPosts:\n" + "\n".join(citations)
        return result

    @staticmethod
    def _extract_output_text(data: dict) -> str:
        texts = []
        for item in data.get("output") or []:
            if item.get("type") != "message":
                continue
            for content in item.get("content") or []:
                if content.get("type") == "output_text" and content.get("text"):
                    texts.append(content["text"])
        return "\n".join(texts).strip()

    def _reasoning_effort(self) -> str:
        return self._config_manager.get("x.search_reasoning_effort", self.DEFAULT_REASONING_EFFORT)

    @staticmethod
    def _extract_citations(data: dict) -> List[str]:
        """Cited post URLs: the top-level `citations` list plus the `url_citation`
        annotations on each output_text block (where the Responses API actually puts them),
        de-duplicated in order of appearance."""
        urls = [c for c in data.get("citations") or [] if isinstance(c, str)]
        for item in data.get("output") or []:
            if item.get("type") != "message":
                continue
            for content in item.get("content") or []:
                for annotation in content.get("annotations") or []:
                    if annotation.get("type") == "url_citation" and annotation.get("url"):
                        urls.append(annotation["url"])
        return list(dict.fromkeys(urls))
