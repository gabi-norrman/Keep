from .ChatGPTLLM import ChatGPTLLM


class GrokLLM(ChatGPTLLM):
    """Talks to xAI's Grok models through their OpenAI-compatible API."""

    def __init__(self, model: str = 'grok-4-1-fast-non-reasoning'):
        super().__init__(model=model, api_key_name='grok', base_url="https://api.x.ai/v1")

    def name(self) -> str:
        return "Grok"
