import importlib.metadata

import pytest

from common import RequirementsChecker as rc_module
from common.RequirementsChecker import (
    RequirementsChecker,
    _REQ_LINE_RE,
    _version_from_direct_ref,
)


# ---- Requirements line regex ----

def test_regex_matches_simple_pinned():
    m = _REQ_LINE_RE.match("anthropic==0.39.0")
    assert m is not None
    assert m.group(1) == "anthropic"
    assert m.group(2) == "0.39.0"


def test_regex_matches_extras_spec():
    m = _REQ_LINE_RE.match("Hypercorn[trio,uvloop]==0.17.3")
    assert m is not None
    assert m.group(1) == "Hypercorn"
    assert m.group(2) == "0.17.3"


def test_regex_tolerates_whitespace():
    m = _REQ_LINE_RE.match("  google-cloud-speech == 2.27.0  ")
    assert m is not None
    assert m.group(1) == "google-cloud-speech"
    assert m.group(2) == "2.27.0"


@pytest.mark.parametrize("line", [
    "anthropic>=0.39.0",        # unpinned lower-bound
    "anthropic~=0.39.0",        # compatible release
    "anthropic",                # bare name
    "anthropic==",              # missing version
    "==0.39.0",                 # missing name
    "git+https://example.com/repo.git",
])
def test_regex_rejects_non_pinned_lines(line):
    assert _REQ_LINE_RE.match(line) is None


# ---- Direct-reference version extraction ----

SPACY_URL = (
    "https://github.com/explosion/spacy-models/releases/download/"
    "en_core_web_sm-3.8.0/en_core_web_sm-3.8.0-py3-none-any.whl"
)


def test_direct_ref_version_from_wheel_url():
    assert _version_from_direct_ref("en_core_web_sm", SPACY_URL) == "3.8.0"


def test_direct_ref_version_from_sdist_url():
    url = "https://example.com/dist/pkg-a-1.2.3.tar.gz"
    assert _version_from_direct_ref("pkg-a", url) == "1.2.3"


def test_direct_ref_version_normalizes_name_separators():
    url = "https://example.com/dist/pkg_a-1.2.3-py3-none-any.whl"
    assert _version_from_direct_ref("pkg-a", url) == "1.2.3"


def test_direct_ref_version_none_when_name_mismatch():
    assert _version_from_direct_ref("other-pkg", SPACY_URL) is None


def test_direct_ref_version_none_for_non_archive_url():
    url = "https://example.com/repo.git"
    assert _version_from_direct_ref("pkg-a", url) is None


# ---- Python version check ----

def test_check_python_version_exits_on_mismatch(monkeypatch):
    monkeypatch.setattr(rc_module, "REQUIRED_PYTHON", (9, 99))
    with pytest.raises(SystemExit) as exc_info:
        RequirementsChecker._check_python_version()
    assert exc_info.value.code == 1


# ---- verify_or_exit() ----

@pytest.fixture(autouse=True)
def _no_voice_group(tmp_path, monkeypatch):
    """The real requirements-voice.txt is not under test here: point the group at a missing file."""
    monkeypatch.setattr(rc_module, "VOICE_REQUIREMENTS_FILE", tmp_path / "no-voice.txt")


def _write_reqs(tmp_path, body: str):
    f = tmp_path / "requirements.txt"
    f.write_text(body)
    return f


def test_verify_or_exit_missing_file_returns_without_exit(tmp_path, monkeypatch):
    monkeypatch.setattr(rc_module, "REQUIREMENTS_FILE", tmp_path / "nope.txt")
    # Should NOT raise SystemExit
    RequirementsChecker.verify_or_exit()


def test_verify_or_exit_all_match_does_not_exit(tmp_path, monkeypatch):
    reqs = _write_reqs(tmp_path, "pkg-a==1.0.0\npkg-b==2.0.0\n")
    monkeypatch.setattr(rc_module, "REQUIREMENTS_FILE", reqs)

    versions = {"pkg-a": "1.0.0", "pkg-b": "2.0.0"}
    monkeypatch.setattr(
        importlib.metadata, "version", lambda name: versions[name]
    )

    RequirementsChecker.verify_or_exit()


def test_verify_or_exit_exits_on_missing_package(tmp_path, monkeypatch):
    reqs = _write_reqs(tmp_path, "pkg-a==1.0.0\n")
    monkeypatch.setattr(rc_module, "REQUIREMENTS_FILE", reqs)

    def _raise(_name):
        raise importlib.metadata.PackageNotFoundError("pkg-a")

    monkeypatch.setattr(importlib.metadata, "version", _raise)

    with pytest.raises(SystemExit) as exc_info:
        RequirementsChecker.verify_or_exit()
    assert exc_info.value.code == 1


def test_verify_or_exit_exits_on_version_mismatch(tmp_path, monkeypatch):
    reqs = _write_reqs(tmp_path, "pkg-a==1.0.0\n")
    monkeypatch.setattr(rc_module, "REQUIREMENTS_FILE", reqs)
    monkeypatch.setattr(importlib.metadata, "version", lambda _name: "9.9.9")

    with pytest.raises(SystemExit) as exc_info:
        RequirementsChecker.verify_or_exit()
    assert exc_info.value.code == 1


def test_verify_or_exit_ignores_blanks_and_comments(tmp_path, monkeypatch):
    body = (
        "\n"
        "# top-level comment\n"
        "pkg-a==1.0.0\n"
        "   \n"
        "# inline-style comment\n"
        "pkg-b==2.0.0\n"
    )
    reqs = _write_reqs(tmp_path, body)
    monkeypatch.setattr(rc_module, "REQUIREMENTS_FILE", reqs)

    seen = []

    def _version(name):
        seen.append(name)
        return {"pkg-a": "1.0.0", "pkg-b": "2.0.0"}[name]

    monkeypatch.setattr(importlib.metadata, "version", _version)

    RequirementsChecker.verify_or_exit()
    assert seen == ["pkg-a", "pkg-b"]


def test_verify_or_exit_extras_spec_validates_against_base_name(tmp_path, monkeypatch):
    reqs = _write_reqs(tmp_path, "Hypercorn[trio,uvloop]==0.17.3\n")
    monkeypatch.setattr(rc_module, "REQUIREMENTS_FILE", reqs)

    captured = []

    def _version(name):
        captured.append(name)
        return "0.17.3"

    monkeypatch.setattr(importlib.metadata, "version", _version)

    RequirementsChecker.verify_or_exit()
    assert captured == ["Hypercorn"]  # extras spec must be stripped


def test_verify_or_exit_skips_unpinned_lines_without_exit(tmp_path, monkeypatch):
    # Unpinned lines should be skipped (logged at trace level) — they must
    # NOT trigger a failure, otherwise legitimate non-pinned lines (e.g. a
    # future git+ URL) would crash startup.
    reqs = _write_reqs(tmp_path, "pkg-a>=1.0.0\npkg-b==2.0.0\n")
    monkeypatch.setattr(rc_module, "REQUIREMENTS_FILE", reqs)

    seen = []

    def _version(name):
        seen.append(name)
        return "2.0.0"

    monkeypatch.setattr(importlib.metadata, "version", _version)

    RequirementsChecker.verify_or_exit()
    assert seen == ["pkg-b"]  # unpinned line never checked


def test_verify_or_exit_checks_direct_reference_version(tmp_path, monkeypatch):
    reqs = _write_reqs(tmp_path, f"en_core_web_sm @ {SPACY_URL}\n")
    monkeypatch.setattr(rc_module, "REQUIREMENTS_FILE", reqs)

    seen = []

    def _version(name):
        seen.append(name)
        return "3.8.0"

    monkeypatch.setattr(importlib.metadata, "version", _version)

    RequirementsChecker.verify_or_exit()
    assert seen == ["en_core_web_sm"]


def test_verify_or_exit_exits_on_direct_reference_mismatch(tmp_path, monkeypatch):
    reqs = _write_reqs(tmp_path, f"en_core_web_sm @ {SPACY_URL}\n")
    monkeypatch.setattr(rc_module, "REQUIREMENTS_FILE", reqs)
    monkeypatch.setattr(importlib.metadata, "version", lambda _name: "3.7.0")

    with pytest.raises(SystemExit) as exc_info:
        RequirementsChecker.verify_or_exit()
    assert exc_info.value.code == 1


def test_verify_or_exit_direct_reference_without_version_checks_presence(tmp_path, monkeypatch):
    reqs = _write_reqs(tmp_path, "pkg-a @ https://example.com/repo.git\n")
    monkeypatch.setattr(rc_module, "REQUIREMENTS_FILE", reqs)

    seen = []

    def _version(name):
        seen.append(name)
        return "0.0.1"  # any version accepted when URL has no version

    monkeypatch.setattr(importlib.metadata, "version", _version)

    RequirementsChecker.verify_or_exit()
    assert seen == ["pkg-a"]


# ---- Grouped (base + voice) checks ----

def _write_voice_reqs(tmp_path, body: str):
    f = tmp_path / "requirements-voice.txt"
    f.write_text("-r requirements.txt\n" + body)
    return f


def _point_at(monkeypatch, base, voice):
    monkeypatch.setattr(rc_module, "REQUIREMENTS_FILE", base)
    monkeypatch.setattr(rc_module, "VOICE_REQUIREMENTS_FILE", voice)


def test_both_groups_are_checked(tmp_path, monkeypatch):
    base = _write_reqs(tmp_path, "pkg-a==1.0.0\n")
    voice = _write_voice_reqs(tmp_path, "torch-ish==2.0.0\n")
    _point_at(monkeypatch, base, voice)

    seen = []

    def _version(name):
        seen.append(name)
        return {"pkg-a": "1.0.0", "torch-ish": "2.0.0"}[name]

    monkeypatch.setattr(importlib.metadata, "version", _version)

    RequirementsChecker.verify_or_exit()
    assert seen == ["pkg-a", "torch-ish"]  # the -r include line is not re-resolved


def test_missing_voice_package_exits_even_with_voice_off(tmp_path, monkeypatch):
    base = _write_reqs(tmp_path, "pkg-a==1.0.0\n")
    voice = _write_voice_reqs(tmp_path, "torch-ish==2.0.0\n")
    _point_at(monkeypatch, base, voice)

    def _version(name):
        if name == "torch-ish":
            raise importlib.metadata.PackageNotFoundError(name)
        return "1.0.0"

    monkeypatch.setattr(importlib.metadata, "version", _version)

    with pytest.raises(SystemExit) as exc_info:
        RequirementsChecker.verify_or_exit()
    assert exc_info.value.code == 1


def test_real_requirements_files_parse_and_voice_includes_base():
    # the autouse fixture points the voice group at a missing file; this test wants the real ones
    voice_lines = (rc_module.AISERVER_DIR / "requirements-voice.txt").read_text().splitlines()
    assert "-r requirements.txt" in voice_lines
    base_names = {l.split("==")[0] for l in (rc_module.AISERVER_DIR / "requirements.txt").read_text().splitlines()
                  if "==" in l}
    voice_names = {l.split("==")[0].split(" @ ")[0] for l in voice_lines
                   if "==" in l or " @ " in l}
    assert not (base_names & voice_names), "a package must live in exactly one group"
    for name in ("torch", "mlx-whisper", "sounddevice", "spacy", "en_core_web_sm"):
        assert name in voice_names
