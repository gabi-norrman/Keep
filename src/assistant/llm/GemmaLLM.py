from .OpenRouterLLM import OpenRouterLLM


class GemmaLLM(OpenRouterLLM):
    """Runs Google Gemma models through OpenRouter."""

    def __init__(self, model: str = 'google/gemma-4-26b-a4b-it'):
        super().__init__(model=model)

    def name(self) -> str:
        return "Gemma"
