from .OpenRouterLLM import OpenRouterLLM


class DeepSeekLLM(OpenRouterLLM):
    """Runs DeepSeek models through OpenRouter."""
    def __init__(self, model: str = 'deepseek/deepseek-v4-flash'):
        super().__init__(model=model)

    def name(self) -> str:
        return "DeepSeek"
