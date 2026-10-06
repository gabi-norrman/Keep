from typing import List

from assistant.integration.Integration import Integration
from assistant.agents.CodeQualityMaintainer import claude_status
from common.logging import trace


class SelfImprovementIntegration(Integration):
    """Keep working on its own code.

    Lets Keep analyse and improve its own code through the Claude Code CLI: the nightly
    code-quality runs and the self_analysis / self_improvement commands. It needs no key, only
    the CLI installed and logged in on the server machine.

    The switch and the health check: the runs (CodeQualityMaintainer) happen only while this
    integration is enabled, the nightly ones also only while `self_improvement.nightly_runs` is
    on. The setup is in the config.example.yaml beside this file, section self_improvement. The
    health check asks `claude auth status`, a free local call, so a missing or logged-out CLI
    shows in list_integrations within a cycle."""

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
