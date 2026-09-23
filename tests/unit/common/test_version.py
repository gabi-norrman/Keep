from unittest.mock import patch

import pytest

from common import Version


@pytest.fixture(autouse=True)
def fresh_cache():
    Version.version.cache_clear()
    Version.detail.cache_clear()
    yield
    Version.version.cache_clear()
    Version.detail.cache_clear()


def fake_git(answers):
    """Answers keyed by the git subcommand, or by the full argument tuple where one
    subcommand is asked two things (`rev-parse` gives both the hash and the branch)."""
    return patch.object(Version, "_git", side_effect=lambda *args: answers.get(args, answers.get(args[0])))


BRANCH = ("rev-parse", "--abbrev-ref", "HEAD")


@pytest.mark.unit
def test_version_is_the_commit_month_and_the_commit_count():
    with fake_git({"log": "2026.09", "rev-list": "3868"}):
        assert Version.version() == "v2026.09.3868"


@pytest.mark.unit
def test_detail_adds_the_hash_the_branch_and_flags_local_edits():
    with fake_git({"log": "2026.09", "rev-list": "3868", "rev-parse": "1b6c9ce7", BRANCH: "release", "status": " M file.py"}):
        assert Version.detail() == "v2026.09.3868 (1b6c9ce7, release, dirty)"
    Version.detail.cache_clear()
    with fake_git({"log": "2026.09", "rev-list": "3868", "rev-parse": "1b6c9ce7", BRANCH: "master", "status": ""}):
        assert Version.detail() == "v2026.09.3868 (1b6c9ce7, master)"


@pytest.mark.unit
def test_detail_leaves_the_branch_out_of_a_detached_checkout():
    with fake_git({"log": "2026.09", "rev-list": "3868", "rev-parse": "1b6c9ce7", BRANCH: "HEAD", "status": ""}):
        assert Version.detail() == "v2026.09.3868 (1b6c9ce7)"


@pytest.mark.unit
def test_outside_a_git_checkout_the_version_is_unknown():
    with fake_git({}):
        assert Version.version() == Version.UNKNOWN and Version.detail() == Version.UNKNOWN


@pytest.mark.unit
def test_a_missing_git_binary_is_logged_not_raised():
    with patch.object(Version.subprocess, "run", side_effect=FileNotFoundError("git")):
        assert Version.version() == Version.UNKNOWN
