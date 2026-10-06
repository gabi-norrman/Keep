from typing import List

from assistant.integration.Integration import Integration
from assistant.agents.CodeQualityMaintainer import claude_status
from common.logging import trace


class SelfImprovementIntegration(Integration):
    """The switch and health check for Floxy working on its own code through the Claude Code CLI:
    the nightly code-quality runs and the self_analysis / self_improvement commands
    (CodeQualityMaintainer) run only while this integration is enabled, the nightly runs also
    only while `self_improvement.nightly_runs` is on. No key is needed — the setup is on the
    server machine: install the CLI and log in (config.example.yaml, section self_improvement).
    The health check asks `claude auth status`,
    a free local call, so a missing or logged-out CLI shows in list_integrations within a cycle."""

    @property
    def name(self) -> str:
        return "Self Improvement"

    @property
    def config_keys(self) -> List[str]:
        return []

    def check_health(self) -> bool:
        status = claude_status()
        if not status.ok:
            raise RuntimeError(status.detail)
        trace(f"Self Improvement: Claude Code {status.detail}")
        return True
