"""The INTEGRATIONS section: what is on, on but unconfigured, and on but broken, in the
prompt while setup is in progress (`FirstRun.in_ftue`) and absent otherwise. Without it the
assistant knows its own state only from a `list_integrations` result in the history window,
which a trim takes away: the first beta install's assistant then told the user to create the
Telegram bot it had set up itself an hour earlier. Names only — which keys are set is
`list_integrations`' business — so it costs a few dozen tokens. Polled, so an integration the
user enables, or breaks, reaches the LLM as a delta note."""
from ..PromptSectionGenerator import PromptSectionGenerator
from common.logging import info


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
        self._in_ftue = None

    @staticmethod
    def in_ftue() -> bool:
        from assistant import FirstRun
        return FirstRun.in_ftue(verbose=False)

    @staticmethod
    def statuses() -> list[dict]:
        from assistant.integration.IntegrationMonitor import IntegrationMonitor
        return IntegrationMonitor.get_instance().get_all_statuses()

    def _fetch_content(self) -> str:
        """Polled every UPDATE_INTERVAL, so the verdict and the summary are logged only when
        they change — the previous content is the thing to compare with."""
        in_ftue = self.in_ftue()
        if in_ftue != self._in_ftue:
            self._in_ftue = in_ftue
            info("Integrations section: setup is in progress — the section is in the prompt" if in_ftue
                 else "Integrations section: setup is not in progress — nothing in the prompt")
        if not in_ftue:
            return ""
        statuses = self.statuses()
        text = self.render(statuses)
        if text != self.content:
            on, unconfigured, broken = self.group(statuses)
            info(f"Integrations section: {len(on)} on, {len(unconfigured)} not configured, {len(broken)} with a problem")
        return text

    @classmethod
    def group(cls, statuses: list[dict]) -> tuple[list[str], list[str], list[str]]:
        """The names by state: on, on but unconfigured, on with a problem (with its detail)."""
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
        return on, unconfigured, broken

    @classmethod
    def render(cls, statuses: list[dict]) -> str:
        on, unconfigured, broken = cls.group(statuses)
        lines = [cls.HEADING]
        lines.append("On: " + (", ".join(on) + "." if on else "nothing yet."))
        if unconfigured:
            lines.append("On but not configured: " + ", ".join(unconfigured) + ".")
        if broken:
            lines.append("On with a problem: " + ", ".join(broken) + ".")
        return "\n".join(lines)
