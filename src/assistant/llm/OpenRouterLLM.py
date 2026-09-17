from .ChatGPTLLM import ChatGPTLLM


class OpenRouterLLM(ChatGPTLLM):
    """Base for backends reached through OpenRouter's OpenAI-compatible API."""

    def __init__(self, model: str):
        super().__init__(model=model, api_key_name='openrouter', base_url="https://openrouter.ai/api/v1")

    def name(self) -> str:
        return "OpenRouter"
