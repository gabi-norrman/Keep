"""The INTEGRATIONS section: what is on, on but unconfigured, and on but broken, in the
prompt while setup is in progress (`FirstRun.in_ftue`) and absent otherwise. Without it the
assistant knows its own state only from a `list_integrations` result in the history window,
which a trim takes away: the first beta install's assistant then told the user to create the
Telegram bot it had set up itself an hour earlier. Names only — which keys are set is
`list_integrations`' business — so it costs a few dozen tokens. Polled, so an integration the
user enables, or breaks, reaches the LLM as a delta note."""
from ..PromptSectionGenerator import PromptSectionGenerator
from common.logging import info, trace


class IntegrationsSectionGenerator(PromptSectionGenerator):
    """Renders the enabled integrations grouped by state while setup is in progress, nothing after."""

    UPDATE_INTERVAL = 20

    HEADING = "# INTEGRATIONS"
    # The health states (IntegrationState values) under each line; "disabled" is left out.
    ON = ("ok", "unknown")
    NOT_CONFIGURED = ("not_configured",)
    PROBLEM = {"warning": "warning", "error": "error", "needs_2fa": "needs 2FA", "rate_limited": "rate limited"}
    ERROR_CHARS = 80

    def __init__(self, prompt_manager):
        super().__init__(prompt_manager, "Integrations")

    @staticmethod
    def in_ftue() -> bool:
        from assistant import FirstRun
        return FirstRun.in_ftue()

    @staticmethod
    def statuses() -> list[dict]:
        from assistant.integration.IntegrationMonitor import IntegrationMonitor
        return IntegrationMonitor.get_instance().get_all_statuses()

    def _fetch_content(self) -> str:
        if not self.in_ftue():
            trace("Integrations section: setup is not in progress — nothing in the prompt")
            return ""
        return self.render(self.statuses())

    @classmethod
    def render(cls, statuses: list[dict]) -> str:
        on, unconfigured, broken = [], [], []
        for status in sorted(statuses, key=lambda s: s["name"].lower()):
            state = status["state"]
            if state in cls.ON:
                on.append(status["name"])
            elif state in cls.NOT_CONFIGURED:
                unconfigured.append(status["name"])
            elif state in cls.PROBLEM:
                detail = cls.PROBLEM[state]
                if status.get("last_error"):
                    detail += f": {status['last_error'][:cls.ERROR_CHARS]}"
                broken.append(f"{status['name']} ({detail})")
        lines = [cls.HEADING]
        lines.append("On: " + (", ".join(on) + "." if on else "nothing yet."))
        if unconfigured:
            lines.append("On but not configured: " + ", ".join(unconfigured) + ".")
        if broken:
            lines.append("On with a problem: " + ", ".join(broken) + ".")
        info(f"Integrations section: {len(on)} on, {len(unconfigured)} not configured, {len(broken)} with a problem")
        return "\n".join(lines)
