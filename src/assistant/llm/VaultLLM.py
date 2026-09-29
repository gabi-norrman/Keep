"""The `AbstractLLM` wrapper that keeps the vault's secrets away from the provider
(VAULT_PLAN §3.6): messages are redacted on the way in, responses restored on the way
out. `create_llm()` wraps every backend in one; it sits *inside* `CachedLLM`, so cache
rows hold restored text. With a disabled or empty vault it is a plain pass-through.
"""
from typing import Generator

from common.logging import fine, info
from common.vault.Redactor import Redactor, RestoringStream
from common.vault.Vault import Vault
from .AbstractLLM import AbstractLLM


class VaultLLM(AbstractLLM):
    def __init__(self, llm: AbstractLLM, vault: Vault | None = None):
        self.llm = llm
        # `is None`, not `or`: Vault defines __len__, so an empty vault is falsy.
        self.redactor = Redactor(Vault.get_instance() if vault is None else vault)

    def __getattr__(self, name: str):
        # Reached only when normal lookup fails: pass `.model`, `.client`, … through to the
        # wrapped LLM. Private names stay ours, and a half-built instance (no `llm` yet)
        # must raise instead of recursing.
        if name.startswith("_") or "llm" not in self.__dict__:
            raise AttributeError(f"'{type(self).__name__}' object has no attribute '{name}'")
        return getattr(self.llm, name)

    def create_streaming_completion(self, messages: list[dict], thinking: bool = False) -> Generator[str, None, None]:
        if not self.redactor.active:
            vault = self.redactor.vault
            if vault.locked:
                info(f"Vault: LOCKED ({vault.error}) — {self.llm.name()} stream passed through unredacted")
            else:
                fine(f"Vault: empty — {self.llm.name()} stream passed through without hold-back")
            yield from self.llm.create_streaming_completion(messages, thinking=thinking)
            return
        inner = self.llm.create_streaming_completion(self.redactor.redact_messages(messages), thinking=thinking)
        yield from RestoringStream(inner, self.redactor)

    def create_completion(self, messages: list[dict], thinking: bool = False) -> str:
        return self._restored(self.llm.create_completion(self.redactor.redact_messages(messages), thinking=thinking))

    def create_completion_with_images(self, messages: list[dict], images: list) -> str:
        return self._restored(self.llm.create_completion_with_images(self.redactor.redact_messages(messages), images))

    def think_with_video(self, system_message: str, user_message: str, video_bytes: bytes,
                         mime_type: str, fps: float | None = None) -> str:
        system_message, _ = self.redactor.redact(system_message)
        user_message, _ = self.redactor.redact(user_message)
        return self._restored(self.llm.think_with_video(system_message, user_message, video_bytes, mime_type, fps=fps))

    def pre_heat_cache(self, messages: list[dict]):
        self.llm.pre_heat_cache(self.redactor.redact_messages(messages))

    def _restored(self, response):
        return self.redactor.restore(response)[0] if isinstance(response, str) else response

    def initialize(self):
        return self.llm.initialize()

    def api_key_name(self) -> str | None:
        return self.llm.api_key_name()

    def missing_api_key(self) -> str | None:
        return self.llm.missing_api_key()

    def name(self) -> str:
        return self.llm.name()

    def provider(self) -> str:
        return self.llm.provider()

    def display_model(self) -> str:
        return self.llm.display_model()

    def refusal_fallback(self) -> AbstractLLM | None:
        """The inner LLM's fallback, wrapped in this vault: the request it answers is the
        one the vault just redacted, and the fallback's provider must not see the secrets
        either. Explicit, not via __getattr__ — the AbstractLLM default (None) would win."""
        fallback = self.llm.refusal_fallback()
        return None if fallback is None else VaultLLM(fallback, self.redactor.vault)

    @property
    def last_stop_reason(self) -> str | None:
        """Explicit like refusal_fallback: the AbstractLLM property would read
        `_last_stop_reason` off this wrapper, where it never lands, and __getattr__ refuses
        private names — so the curator's truncation check would always see None."""
        return self.llm.last_stop_reason

    def prefers_prepended_user_query(self) -> bool:
        return self.llm.prefers_prepended_user_query()

    def prefers_byte_prefix_tail(self) -> bool:
        # Explicit like refusal_fallback: the AbstractLLM default (False) wins over
        # __getattr__, and create_llm wraps every backend in a VaultLLM.
        return self.llm.prefers_byte_prefix_tail()

    def set_prompt_cache(self, enabled: bool):
        self.llm.set_prompt_cache(enabled)

    def supports_native_thinking(self) -> bool:
        return self.llm.supports_native_thinking()

    def handles_thinking_internally(self) -> bool:
        return self.llm.handles_thinking_internally()

    def supports_video(self) -> bool:
        return self.llm.supports_video()

    def max_video_bytes(self) -> int:
        return self.llm.max_video_bytes()
