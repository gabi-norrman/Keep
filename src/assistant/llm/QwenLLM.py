from .OpenRouterLLM import OpenRouterLLM


class QwenLLM(OpenRouterLLM):
    """Runs Qwen models through OpenRouter."""

    def __init__(self, model: str = 'qwen/qwen3.8-27b'):
        super().__init__(model=model)

    def name(self) -> str:
        return "Qwen"
