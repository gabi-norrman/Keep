"""The IntegrationMonitor as the one answer to "is this integration on": the verdict per
folder and per contract that gates the prompt sections and the toolkits, and the listeners
told when it may have changed."""
from typing import List
from unittest.mock import patch

import pytest

from assistant.integration.Integration import Integration
from assistant.integration.IntegrationMonitor import IntegrationMonitor, folder_of
from assistant.integration.SingletonMixin import SingletonMixin


class Brewing:
    """A contract more than one integration may implement."""


class KettleIntegration(Integration, SingletonMixin, Brewing):
    __module__ = "integrations.kettle.KettleIntegration"
    on = True

    @property
    def name(self) -> str:
        return "Kettle"

    @property
    def config_keys(self) -> List[str]:
        return ["kettle"]

    def check_health(self) -> bool:
        return True

    @classmethod
    def enabled(cls, config) -> bool:
        return cls.on


class UrnIntegration(KettleIntegration):
    __module__ = "integrations.urn.UrnIntegration"

    @property
    def name(self) -> str:
        return "Urn"


class BrokenKettleIntegration(KettleIntegration):
    __module__ = "integrations.kettle.BrokenKettleIntegration"

    def __init__(self):
        raise RuntimeError("Missing 'kettle' section in configuration.")


class ToasterIntegration(Integration, SingletonMixin):
    """Not a Brewing integration, and not in a folder either."""
    __module__ = "assistant.tools.Toaster"
    on = True

    @property
    def name(self) -> str:
        return "Toaster"

    @property
    def config_keys(self) -> List[str]:
        return ["toaster"]

    def check_health(self) -> bool:
        return True


@pytest.fixture
def monitor():
    for cls in (KettleIntegration, UrnIntegration, BrokenKettleIntegration, ToasterIntegration):
        cls.reset_instance()
        cls.on = True
    with patch("assistant.integration.IntegrationMonitor.ConfigManager") as config:
        config.return_value.get.return_value = "set"
        yield IntegrationMonitor()
    for cls in (KettleIntegration, UrnIntegration, BrokenKettleIntegration, ToasterIntegration):
        cls.reset_instance()


@pytest.mark.unit
def test_the_folder_of_a_class_is_the_integrations_package_folder_it_lives_in():
    assert folder_of(KettleIntegration) == "kettle"
    assert folder_of(ToasterIntegration) is None


@pytest.mark.unit
def test_a_folder_nothing_is_registered_for_has_no_verdict(monitor):
    assert monitor.folder_enabled("kettle") is None
    assert monitor.contract_enabled(Brewing) is None
    assert monitor.folder_integrations("kettle") == []


@pytest.mark.unit
def test_a_disabled_integration_turns_its_folder_and_its_contract_off(monitor):
    KettleIntegration.on = False
    monitor.register_class(KettleIntegration)
    assert monitor.folder_enabled("kettle") is False
    assert monitor.contract_enabled(Brewing) is False
    assert [i.name for i in monitor.folder_integrations("kettle")] == ["Kettle"]
    assert [i.name for i in monitor.contract_integrations(Brewing)] == ["Kettle"]


@pytest.mark.unit
def test_one_built_integration_turns_the_folder_on_and_a_built_one_elsewhere_the_contract(monitor):
    KettleIntegration.on = False
    monitor.register_class(KettleIntegration)
    monitor.register_class(UrnIntegration)
    assert monitor.folder_enabled("kettle") is False
    assert monitor.folder_enabled("urn") is True
    assert monitor.contract_enabled(Brewing) is True, "the urn brews, so the contract is served"
    assert {i.name for i in monitor.contract_integrations(Brewing)} == {"Kettle", "Urn"}


@pytest.mark.unit
def test_an_integration_that_cannot_be_built_is_on_but_unconfigured_so_its_folder_counts_as_on(monitor):
    monitor.register_class(BrokenKettleIntegration)
    assert monitor.folder_enabled("kettle") is True


@pytest.mark.unit
def test_an_integration_outside_the_folders_belongs_to_no_folder(monitor):
    monitor.register_class(ToasterIntegration)
    assert monitor.folder_integrations("kettle") == []
    assert monitor.contract_enabled(Brewing) is None


@pytest.mark.unit
def test_listeners_hear_notify_and_every_restart_and_one_raising_does_not_silence_the_rest(monitor):
    heard = []
    monitor.add_listener(lambda: heard.append("first"))
    monitor.add_listener(lambda: 1 / 0)
    monitor.add_listener(lambda: heard.append("third"))
    monitor.register_class(KettleIntegration)
    assert heard == [], "a registration is announced by the registrar, in batches"
    monitor.notify()
    assert heard == ["first", "third"]
    monitor.restart("Kettle")
    assert heard == ["first", "third", "first", "third"]


@pytest.mark.unit
def test_a_restart_flips_the_verdict_with_the_config(monitor):
    monitor.register_class(KettleIntegration)
    assert monitor.folder_enabled("kettle") is True
    KettleIntegration.on = False
    monitor.restart("Kettle")
    assert monitor.folder_enabled("kettle") is False
    KettleIntegration.on = True
    monitor.restart("Kettle")
    assert monitor.folder_enabled("kettle") is True


@pytest.mark.unit
def test_a_removed_listener_hears_nothing_more(monitor):
    heard = []
    listener = lambda: heard.append(1)
    monitor.add_listener(listener)
    monitor.remove_listener(listener)
    monitor.notify()
    assert heard == []
