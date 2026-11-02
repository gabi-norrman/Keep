"""The commands that edit which Home Assistant entities Keep sees (EntityRules): exclude a
pattern, include one, clear its rule, list the rules. Each answers with what the pattern
matches right now, so the LLM can tell a typo from an empty domain."""
from assistant.commands.CommandSet import CommandSet
from integrations.home_assistant.HomeAssistantIntegration import HomeAssistantIntegration


class HomeAssistantCommands(CommandSet):
    """The owner's include / exclude rules on Home Assistant's entities, edited by the assistant."""

    PURE_COMMANDS = frozenset({"entity_rules"})

    @staticmethod
    def _run(method: str, parameter_string: str) -> str:
        return getattr(HomeAssistantIntegration.get_instance(), method)(parameter_string)

    def cmd_exclude_entities(self, parameter_string: str):
        self.background_command(lambda p: self._run("exclude", p), parameter_string)

    def cmd_include_entities(self, parameter_string: str):
        self.background_command(lambda p: self._run("include", p), parameter_string)

    def cmd_clear_entity_rule(self, parameter_string: str):
        self.background_command(lambda p: self._run("clear_rule", p), parameter_string)

    def cmd_entity_rules(self, parameter_string: str = ""):
        self.background_command(lambda _p: HomeAssistantIntegration.get_instance().rules_text(), parameter_string)

    def describe_exclude_entities(self, p):  return f"Excluding {p.strip()} from the house data" if p.strip() else "Excluding entities"
    def describe_include_entities(self, p):  return f"Including {p.strip()} in the house data" if p.strip() else "Including entities"
    def describe_clear_entity_rule(self, p): return f"Clearing the rule for {p.strip()}" if p.strip() else "Clearing an entity rule"
    def describe_entity_rules(self, p):      return "Listing the entity rules"
