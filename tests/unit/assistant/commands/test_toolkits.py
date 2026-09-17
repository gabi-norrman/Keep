"""The toolkit registry and its files (plans/TOOLKITS_PLAN.md): every command has exactly one
home, every file documents exactly the commands it owns, and the rendering the prompt and
load_toolkit share is stable."""
import re
from unittest.mock import patch

import pytest

from assistant.commands import Toolkits
from assistant.commands.Commands import Commands
from assistant.commands.Toolkits import TOOLKITS, Toolkit


def _documented(text: str) -> set[str]:
    return set(re.findall(r"`([a-z_0-9]+)\(", text))


@pytest.mark.unit
def test_every_command_has_exactly_one_home():
    commands = set(Commands(None).list_commands())
    owned = [c for t in TOOLKITS for c in t.commands]
    assert len(owned) == len(set(owned)), "a command is listed in two toolkits"
    assert commands - set(owned) - Toolkits.CORE_COMMANDS - Toolkits.UNDOCUMENTED == set(), \
        "a cmd_* method belongs to no toolkit"
    assert Toolkits.UNDOCUMENTED <= commands and not Toolkits.UNDOCUMENTED & set(owned)
    assert set(owned) - commands == set(), "a toolkit lists a command that does not exist"
    assert Toolkits.CORE_COMMANDS - commands == set()


@pytest.mark.unit
@pytest.mark.parametrize("toolkit", TOOLKITS, ids=[t.name for t in TOOLKITS])
def test_file_documents_its_commands_and_no_others(toolkit: Toolkit):
    assert toolkit.path.exists(), f"res/toolkits/{toolkit.name}.md is missing"
    text = toolkit.text()
    assert len(text) <= Toolkits.MAX_FILE_CHARS
    documented = _documented(text)
    for command in toolkit.commands:
        assert command in documented, f"{toolkit.name}.md does not show `{command}(`"
    foreign = {c for c in documented if Toolkits.owner_of(c) not in (None, toolkit)}
    # A file may point at another toolkit's command in prose or an example (the x toolkit names
    # open_url, location shows a config_set call); it must not carry a second reference for it,
    # which a signature line — a line that is nothing but the backticked call — would be.
    for command in foreign:
        assert not re.search(rf"^`{command}\([^`]*\)`\s*$", text, flags=re.M), \
            f"{toolkit.name}.md documents `{command}` which belongs to {Toolkits.owner_of(command).name}"


@pytest.mark.unit
def test_carried_sections_are_real_sections_with_a_heading():
    from assistant.prompt.PromptManager import PromptManager
    with patch("assistant.prompt.PromptSectionGenerator.PromptSectionGenerator.start"):
        keys = set(PromptManager(test_suite=True).sections)
    for key, name in Toolkits.CARRIED_SECTIONS.items():
        assert key in keys and Toolkits.by_name(name) is not None
        assert key in Toolkits.SECTION_HEADINGS
    assert Toolkits.render_sections(Toolkits.by_name("web"), {"home": "x"}) == ""
    rendered = Toolkits.render_sections(Toolkits.by_name("home"), {"home": "## LIGHTS\nlamp: off\n\n"})
    assert rendered == "## HOME AUTOMATION DATA\n## LIGHTS\nlamp: off\n"


@pytest.mark.unit
def test_by_name_is_tolerant():
    assert Toolkits.by_name("Email").name == "email"
    assert Toolkits.by_name('"email"').name == "email"
    assert Toolkits.by_name("the email toolkit").name == "email"
    assert Toolkits.by_name("nope") is None
    assert Toolkits.by_name("") is None


@pytest.mark.unit
def test_index_lists_every_toolkit_once():
    index = Toolkits.render_index()
    for toolkit in TOOLKITS:
        assert index.count(f"`{toolkit.name}`") == 1, toolkit.name
        assert toolkit.summary in index


@pytest.mark.unit
def test_render_carries_the_header_and_substitutes():
    toolkit = Toolkits.by_name("timers")
    rendered = Toolkits.render(toolkit, lambda text: text.replace("{WAKE_INTERVAL_MINUTES}", "7"))
    assert rendered.startswith("# Toolkit: timers\n")
    assert "wake up every 7 minutes" in rendered
    assert "{WAKE_INTERVAL_MINUTES}" not in rendered
    assert Toolkits.loaded_name(rendered) == "timers"
    assert Toolkits.loaded_name("result: \n# Toolkit: timers") is None
    assert Toolkits.loaded_name("# Toolkit: ") is None
