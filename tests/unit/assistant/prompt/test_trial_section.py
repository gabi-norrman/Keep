"""TrialSectionGenerator: one line while the server runs a PR as a trial, nothing otherwise, read
once at start since the branch cannot change under a running process."""
from unittest.mock import Mock, patch

from assistant.agents.PullRequestTrial import PullRequestTrial
from assistant.prompt.PromptManager import PromptManager
from assistant.prompt.sections.TrialSectionGenerator import TrialSectionGenerator


def test_the_section_is_empty_on_a_normal_branch():
    with patch.object(PullRequestTrial, "prompt_line", return_value=""):
        assert TrialSectionGenerator(Mock())._fetch_content() == ""


def test_the_section_carries_the_trial_line():
    with patch.object(PullRequestTrial, "prompt_line", return_value="Running PR #204 as a trial since 14:02, normally on release."):
        assert "PR #204" in TrialSectionGenerator(Mock())._fetch_content()


def test_the_line_is_read_once_and_the_section_then_waits_to_be_stopped():
    section = TrialSectionGenerator(Mock())
    with patch.object(PullRequestTrial, "prompt_line", return_value="Running PR #204 as a trial.") as line:
        section.start()
        section.stop()
    line.assert_called_once()
    assert section.content == "Running PR #204 as a trial."


def test_the_section_has_its_place_in_the_block_order_and_a_heading():
    assert "trial" in PromptManager.SECTION_ORDER
    assert TrialSectionGenerator.key == "trial" and TrialSectionGenerator.heading.startswith("## ")
