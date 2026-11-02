import pytest

from common import IntegrationDirs
from common.ConfigManager import config
from common.RequirementsChecker import RequirementsChecker


def _folder(root, name, *files):
    folder = root / name
    folder.mkdir(parents=True)
    (folder / "__init__.py").write_text("")
    for file in files:
        (folder / file).write_text("x")
    return folder


@pytest.fixture
def layout(tmp_path, monkeypatch):
    """A repository integrations dir with `tesla` and `weather`, and the config listing *dirs*."""
    repo = tmp_path / "repo" / "integrations"
    _folder(repo, "tesla", "toolkit.md")
    _folder(repo, "weather")
    monkeypatch.setattr(IntegrationDirs, "INTEGRATIONS_DIR", repo)
    monkeypatch.setattr(IntegrationDirs, "AISERVER_DIR", tmp_path / "repo")
    real_get = config.get
    listed = []
    monkeypatch.setattr(config, "get", lambda key, *a, **kw: listed if key == IntegrationDirs.EXTRA_DIRS_KEY
                        else real_get(key, *a, **kw))
    IntegrationDirs.reset()
    yield tmp_path, listed
    IntegrationDirs.reset()


def test_a_listed_directory_is_a_root_and_its_folders_are_found_after_the_repositorys(layout):
    tmp_path, listed = layout
    local = tmp_path / "KeepLocal"
    _folder(local, "podcasts", "toolkit.md")
    listed.append(str(local))

    assert IntegrationDirs.roots()[1:] == (local.resolve(),)
    assert [p.parent.name for p in IntegrationDirs.files("toolkit.md")] == ["tesla", "podcasts"]
    assert [p.parent.name for p in IntegrationDirs.files("toolkit.md", local_only=True)] == ["podcasts"]


def test_a_relative_entry_is_read_from_the_repository_root(layout):
    tmp_path, listed = layout
    _folder(tmp_path / "repo" / "local", "podcasts")
    listed.append("local")

    assert IntegrationDirs.extra_roots() == ((tmp_path / "repo" / "local").resolve(),)


def test_missing_directories_and_integration_folders_listed_by_themselves_are_left_out(layout):
    tmp_path, listed = layout
    single = _folder(tmp_path / "KeepLocal", "podcasts")
    listed.extend([str(tmp_path / "nowhere"), str(single)])

    assert IntegrationDirs.extra_roots() == ()


def test_a_local_folder_named_like_a_repository_folder_is_shadowed(layout):
    tmp_path, listed = layout
    local = tmp_path / "KeepLocal"
    _folder(local, "tesla", "toolkit.md")
    _folder(local, "podcasts", "toolkit.md")
    listed.append(str(local))

    found = IntegrationDirs.files("toolkit.md")
    assert [p.parent for p in found] == [tmp_path / "repo" / "integrations" / "tesla", local.resolve() / "podcasts"]


def test_a_directory_without_an_init_is_not_an_integration_folder(layout):
    tmp_path, listed = layout
    local = tmp_path / "KeepLocal"
    (local / "notes").mkdir(parents=True)
    (local / "notes" / "toolkit.md").write_text("x")
    listed.append(str(local))

    assert IntegrationDirs.files("toolkit.md", local_only=True) == []


def test_a_local_folders_requirements_are_a_group_pip_installs(layout, monkeypatch):
    tmp_path, listed = layout
    local = tmp_path / "KeepLocal"
    requirements = _folder(local, "podcasts", "requirements.txt") / "requirements.txt"
    listed.append(str(local))
    commands = []
    monkeypatch.setattr(RequirementsChecker, "_log_tail", staticmethod(lambda lines=3: ""))
    monkeypatch.setattr("common.RequirementsChecker.PIP_LOG", tmp_path / "pip.log")
    monkeypatch.setattr("common.RequirementsChecker.subprocess.run",
                        lambda cmd, **kw: commands.append(cmd) or type("R", (), {"returncode": 0})())

    assert requirements.resolve() in [path for _, path in RequirementsChecker._groups()]
    assert RequirementsChecker.install() == ""
    assert str(requirements.resolve()) in commands[0]


def test_a_config_without_the_key_has_no_extra_roots(monkeypatch):
    """An install whose config.yaml predates `extra_integration_dirs` boots: the server read it
    before anything else and a missing key used to stop the start."""
    monkeypatch.setattr(config, "_config", {"owner_name": "Marcus"})
    IntegrationDirs.reset()
    try:
        assert IntegrationDirs.extra_roots() == ()
    finally:
        IntegrationDirs.reset()
