import importlib

from assistant.integration.Integration import Integration
from common.Paths import SRC_DIR
from interfaces.console.tabs import IntegrationTabs


def test_every_tab_module_of_the_folders_brings_a_tab_for_an_integration_of_its_own():
    tabs = IntegrationTabs.discover()

    modules = {path.relative_to(SRC_DIR).with_suffix("").as_posix().replace("/", ".")
               for path in (SRC_DIR / "integrations").rglob("*Tab.py")}
    assert {tab.__module__ for tab in tabs} == modules
    assert all(issubclass(tab.integration, Integration) for tab in tabs)
    claimed = [tab.integration for tab in tabs]
    assert len(set(claimed)) == len(claimed)


def test_only_tab_modules_are_imported_and_a_broken_one_is_skipped(tmp_path, monkeypatch):
    root = tmp_path / "gadgets"
    for name, source in {
        "__init__.py": "",
        "lamp/__init__.py": "",
        "lamp/LampIntegration.py": "raise RuntimeError('the assistant side is not the console\\'s to import')\n",
        "lamp/LampTab.py": ("from interfaces.console.tabs.IntegrationTab import IntegrationTab\n"
                            "from interfaces.console.tabs.TabView import TabView\n"
                            "class LampTab(IntegrationTab):\n    pass\n"),
        "kettle/__init__.py": "",
        "kettle/KettleTab.py": "import a_module_that_is_not_installed\n",
    }.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source, encoding="utf-8")
    monkeypatch.syspath_prepend(str(tmp_path))

    tabs = IntegrationTabs.discover(importlib.import_module("gadgets"))

    assert [tab.__qualname__ for tab in tabs] == ["LampTab"]
