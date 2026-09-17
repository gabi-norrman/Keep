
from ..PromptSectionGenerator import PromptSectionGenerator
from common.logging import trace, info
from common.vault.Redactor import placeholder
from common.vault.Vault import Vault


class VaultSectionGenerator(PromptSectionGenerator):
    """Lists the vault's secrets as `{{secret:id}}` placeholders — id, description and kind, never a value —
    and re-renders as soon as `vault.version` moves, so a TUI edit is live for the next request. Rendered
    at the end of the prompt's `## Vault` subsection, whose prose introduces the list; no heading of its own."""

    LOCKED = ("The vault is LOCKED: {error}. Its secrets cannot be read, so there are no placeholders and "
              "nothing is hidden from you. Tell the user, so they can fix vault.key in config.yaml.")

    def __init__(self, prompt_manager, vault: Vault | None = None):
        super().__init__(prompt_manager, "Vault")
        # Resolved lazily so constructing a PromptManager never reads config.yaml / vault.bin.
        self._vault = vault
        self.last_version: int | None = None

    @property
    def vault(self) -> Vault:
        if self._vault is None:
            self._vault = Vault.get_instance()
        return self._vault

    def _start_monitoring(self):
        """Check the vault's version counter every second."""
        while not self._stop_event.is_set():
            try:
                self._refresh_if_changed()
            except Exception as e:
                trace(f"Error checking vault version: {e}")
            self._wait(1)

    def _refresh_if_changed(self):
        # Read the version before rendering: an edit that lands mid-render bumps it again,
        # so the next tick re-renders instead of being skipped.
        version = self.vault.version
        if version != self.last_version:
            self._update_content()
            self.last_version = version

    def _fetch_content(self) -> str:
        if self.vault.locked:
            # Fail-open must not be silent: with the vault locked every request goes out
            # unredacted, and this is how the user gets to hear about it.
            info(f"Vault section: vault LOCKED ({self.vault.error}) — the prompt says so")
            return self.LOCKED.format(error=self.vault.error)
        secrets = sorted(self.vault.secrets(), key=lambda s: s.id)
        if not secrets:
            state = "empty" if self.vault.enabled else f"disabled ({self.vault.error})"
            info(f"Vault section: vault {state}")
            return "The vault is empty."
        info(f"Vault section: {len(secrets)} secret(s)")
        trace("Vault section ids: " + ", ".join(s.id for s in secrets))
        lines = ["The placeholders that exist, each with its description and kind:"]
        lines += [f"- {placeholder(s.id)} — {s.description} ({s.kind})" for s in secrets]
        return "\n".join(lines)
