"""Carries the one line that says the server runs a pull request as a trial, and nothing on a normal branch.

The branch a process runs never changes while it runs (a trial begins and ends with a restart), so the
line is built once at start and the section then only waits to be stopped — no polling, no git call at
every prompt build.
"""
from ..PromptSectionGenerator import PromptSectionGenerator
from assistant.agents.PullRequestTrial import PullRequestTrial
from common.logging import info


class TrialSectionGenerator(PromptSectionGenerator):
    key = "trial"
    heading = "## Pull request under trial"

    def __init__(self, prompt_manager):
        super().__init__(prompt_manager, "Trial")

    def _start_monitoring(self):
        self._update_content()
        self._stop_event.wait()

    def _fetch_content(self) -> str:
        line = PullRequestTrial.prompt_line()
        info(f"Trial section: {line.split('.')[0] if line else 'no PR under trial, the section stays out of the prompt'}")
        return line
