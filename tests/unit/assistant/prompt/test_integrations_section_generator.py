"""IntegrationsSectionGenerator: the enabled integrations by state, in the prompt while setup
is in progress and absent after."""
from unittest.mock import patch

import pytest

from assistant.prompt.sections.IntegrationsSectionGenerator import IntegrationsSectionGenerator

MODULE = "assistant.prompt.sections.IntegrationsSectionGenerator.IntegrationsSectionGenerator"


def _status(name, state, last_error=None):
    return {"name": name, "state": state, "last_error": last_error, "config_keys": []}


STATUSES = [
    _status("Photos", "ok"),
    _status("Arlo", "disabled"),
    _status("CalDAV", "not_configured"),
    _status("iMessage", "ok"),
    _status("Self Improvement", "error", "Claude Code is not installed"),
    _status("Tesla", "unknown"),
    _status("Telegram Feed", "needs_2fa"),
]


@pytest.fixture
def section():
    return IntegrationsSectionGenerator(prompt_manager=None)


@pytest.mark.unit
class TestRender:
    def test_groups_the_enabled_integrations_by_state_and_leaves_the_disabled_out(self):
        text = IntegrationsSectionGenerator.render(STATUSES)
        lines = text.splitlines()
        assert lines[0] == "# INTEGRATIONS"
        assert lines[1] == "On: iMessage, Photos, Tesla."
        assert lines[2] == "On but not configured: CalDAV."
        assert lines[3] == "On with a problem: Self Improvement (error: Claude Code is not installed), Telegram Feed (needs 2FA)."
        assert "Arlo" not in text

    def test_a_long_error_is_cut(self):
        text = IntegrationsSectionGenerator.render([_status("Gmail", "error", "x" * 500)])
        assert "x" * IntegrationsSectionGenerator.ERROR_CHARS + ")" in text
        assert "x" * (IntegrationsSectionGenerator.ERROR_CHARS + 1) not in text

    def test_the_problem_and_unconfigured_lines_appear_only_with_members(self):
        text = IntegrationsSectionGenerator.render([_status("Photos", "ok")])
        assert text.splitlines() == ["# INTEGRATIONS", "On: Photos."]

    def test_nothing_enabled_says_so(self):
        assert "nothing yet" in IntegrationsSectionGenerator.render([_status("Arlo", "disabled")])

    def test_the_section_stays_small(self):
        # The point of names only: a full install's state in well under a kilobyte.
        many = [_status(f"Integration {i}", "ok") for i in range(20)]
        assert len(IntegrationsSectionGenerator.render(many).encode()) < 600


@pytest.mark.unit
class TestFetchContent:
    def test_absent_when_setup_is_not_in_progress(self, section):
        with patch(f"{MODULE}.in_ftue", return_value=False), patch(f"{MODULE}.statuses") as statuses:
            assert section._fetch_content() == ""
        statuses.assert_not_called()

    def test_present_while_setup_is_in_progress(self, section):
        with patch(f"{MODULE}.in_ftue", return_value=True), patch(f"{MODULE}.statuses", return_value=STATUSES):
            assert section._fetch_content().startswith("# INTEGRATIONS\nOn: iMessage, Photos, Tesla.")

    def test_the_predicate_is_first_runs(self, section):
        with patch("assistant.FirstRun.in_ftue", return_value=False) as predicate:
            assert section._fetch_content() == ""
        predicate.assert_called_once()
