"""A toolkit whose integration is off is offered nowhere: out of the index, answered by
load_toolkit and the preloads with the integration's name, its commands refused."""
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from assistant.ChannelProfile import VOICE_TO_VOICE
from assistant.commands import ToolkitPreloader, Toolkits
from assistant.commands.Commands import Commands
from assistant.integration.HomeAutomation import HomeAutomation
from assistant.integration.VehicleProvider import VehicleProvider
from dataclasses import replace


class FakeMonitor:
    """The IntegrationMonitor's verdicts, scripted: folder/contract -> True, False or None."""

    def __init__(self, folders=None, contracts=None):
        self.folders = folders or {}
        self.contracts = contracts or {}

    def folder_enabled(self, folder):
        return self.folders.get(folder)

    def contract_enabled(self, contract):
        return self.contracts.get(contract)

    def folder_integrations(self, folder):
        return [SimpleNamespace(name=folder.title())] if folder in self.folders else []

    def contract_integrations(self, contract):
        return [SimpleNamespace(name="Indigo"), SimpleNamespace(name="Home Assistant")] if contract in self.contracts else []


@pytest.fixture
def commands():
    cp = Mock(command_reply=None, command_reply_contains_error=False)
    return Commands(cp)


@pytest.mark.unit
def test_without_a_monitor_or_a_verdict_every_toolkit_is_offered():
    photos = Toolkits.by_name("photos")
    assert photos.folder == "photos" and photos.offered_by(None)
    assert photos.offered_by(FakeMonitor()), "nothing registered for the folder yet — still offered"
    assert set(Toolkits.names(FakeMonitor())) == set(Toolkits.names())


@pytest.mark.unit
def test_a_folder_toolkit_follows_its_folder_and_a_core_one_its_contract():
    monitor = FakeMonitor(folders={"photos": False, "music": True}, contracts={HomeAutomation: False})
    offered = Toolkits.names(monitor)
    assert "photos" not in offered and "home" not in offered
    assert "music" in offered and "web" in offered and "setup" in offered
    index = Toolkits.render_index(monitor)
    assert "`photos`" not in index and "`home`" not in index and "`music`" in index


@pytest.mark.unit
def test_a_core_toolkit_without_a_contract_is_always_offered():
    monitor = FakeMonitor(folders={f: False for f in ("music", "photos", "icloud", "imessage", "x")},
                          contracts={HomeAutomation: False})
    assert {"web", "browser", "timers", "history", "vault", "setup", "developer"} <= set(Toolkits.names(monitor))


@pytest.mark.unit
def test_load_toolkit_of_an_off_integration_names_it_and_the_switch(commands):
    monitor = FakeMonitor(folders={"photos": False})
    pm = Mock()
    pm.message_history.loaded_toolkits.return_value = {}
    with patch.object(Commands, "_integration_monitor", staticmethod(lambda: monitor)), \
            patch.object(Commands, "_prompt_manager", staticmethod(lambda: pm)):
        answer = commands._run_load_toolkit("photos")
    assert "# Toolkit: photos" not in answer
    assert "Photos" in answer and 'enable_integration("Photos")' in answer


@pytest.mark.unit
def test_load_toolkit_of_a_contract_toolkit_with_every_provider_off_lists_them(commands):
    monitor = FakeMonitor(contracts={HomeAutomation: False})
    pm = Mock()
    pm.message_history.loaded_toolkits.return_value = {}
    with patch.object(Commands, "_integration_monitor", staticmethod(lambda: monitor)), \
            patch.object(Commands, "_prompt_manager", staticmethod(lambda: pm)):
        answer = commands._run_load_toolkit("home")
    assert "# Toolkit: home" not in answer
    assert "Indigo" in answer and "Home Assistant" in answer and "enable_integration" in answer


@pytest.mark.unit
def test_a_command_of_an_off_integration_is_refused_before_it_runs(commands):
    monitor = FakeMonitor(folders={"photos": False}, contracts={HomeAutomation: True, VehicleProvider: False})
    with patch.object(Commands, "_integration_monitor", staticmethod(lambda: monitor)):
        refusal = commands.run("search_photos", ["sunset"])
        assert refusal.startswith("search_photos failed") and "Photos" in refusal
        car_refusal = commands.run("car_max_defrost", ["Model Y"])
        assert car_refusal.startswith("car_max_defrost failed") and "enable_integration" in car_refusal
        assert commands.refused("turn_on") is None, "home automation is on"
        assert commands.refused("remember") is None, "a core command belongs to no toolkit"
        assert commands.refused("load_toolkit") is None


@pytest.mark.unit
def test_the_preload_skips_a_toolkit_whose_integration_is_off():
    profile = replace(VOICE_TO_VOICE, preload_toolkits=("cars", "web"), fixed_toolkits=())
    pm = Mock()
    pm.render_template = lambda text: text
    pm.message_history.loaded_toolkits.return_value = {}
    pm.untracked_sections.return_value = []
    pm.carried_data = Mock(return_value="")
    with patch.object(ToolkitPreloader, "_monitor", lambda: FakeMonitor(contracts={VehicleProvider: False})), \
            patch.object(ToolkitPreloader, "config") as config:
        config.get.side_effect = lambda key, default=None: default
        assert ToolkitPreloader.preload(pm, profile) == ["web"]


def _config(**values):
    return patch.object(Toolkits, "config", SimpleNamespace(get=lambda key, default=None: values.get(key, default)))


@pytest.mark.unit
def test_a_config_gated_toolkit_follows_its_key_not_the_monitor():
    gated = [t for t in Toolkits.TOOLKITS if t.requires]
    assert gated, "the self_improvement toolkit is gated by is_developer"
    toolkit = gated[0]
    with _config(**{toolkit.requires: False}):
        assert not toolkit.offered_by(None) and not toolkit.offered_by(FakeMonitor())
        assert toolkit.name not in Toolkits.names() and f"`{toolkit.name}`" not in Toolkits.render_index()
    with _config(**{toolkit.requires: True}):
        assert toolkit.offered_by(None) and toolkit.offered_by(FakeMonitor())
        assert toolkit.name in Toolkits.names(FakeMonitor())


@pytest.mark.unit
def test_the_local_integrations_toolkit_is_offered_by_the_listed_directories():
    """`extra_integration_dirs` is a list: the toolkit is offered while it names a directory,
    so an install without local folders never sees improve_local_integration."""
    toolkit = Toolkits.by_name("local_integrations")
    assert toolkit.requires == "extra_integration_dirs"
    with _config(extra_integration_dirs=[]):
        assert not toolkit.offered_by(FakeMonitor())
    with _config(extra_integration_dirs=["~/KeepLocal"]):
        assert toolkit.offered_by(FakeMonitor())


@pytest.mark.unit
def test_load_toolkit_of_a_config_gated_toolkit_names_the_key(commands):
    toolkit = next(t for t in Toolkits.TOOLKITS if t.requires)
    pm = Mock()
    pm.message_history.loaded_toolkits.return_value = {}
    with _config(**{toolkit.requires: False}), \
            patch.object(Commands, "_integration_monitor", staticmethod(lambda: FakeMonitor())), \
            patch.object(Commands, "_prompt_manager", staticmethod(lambda: pm)):
        answer = commands._run_load_toolkit(toolkit.name)
    assert f"# Toolkit: {toolkit.name}" not in answer
    assert toolkit.requires in answer and "enable_integration" not in answer


@pytest.mark.unit
def test_a_command_of_a_config_gated_toolkit_is_refused_while_the_key_is_off(commands):
    toolkit = next(t for t in Toolkits.TOOLKITS if t.requires)
    command = toolkit.commands[0]
    with _config(**{toolkit.requires: False}), \
            patch.object(Commands, "_integration_monitor", staticmethod(lambda: FakeMonitor())):
        assert commands.refused(command).startswith(f"{command} failed")
    with _config(**{toolkit.requires: True}), \
            patch.object(Commands, "_integration_monitor", staticmethod(lambda: FakeMonitor())):
        assert commands.refused(command) is None
