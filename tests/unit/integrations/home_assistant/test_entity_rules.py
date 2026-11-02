"""EntityRules: the patterns, the verdicts and the file (issue #141)."""
import pytest

from integrations.home_assistant.EntityRules import EXCLUDE, INCLUDE, EntityRules, RuleError, normalize


@pytest.fixture
def rules(tmp_path):
    return EntityRules(tmp_path / "rules.txt")


@pytest.mark.unit
class TestPatterns:
    def test_a_bare_domain_is_the_whole_domain_and_quotes_and_case_do_not_matter(self):
        assert normalize("sensor") == "sensor.*"
        assert normalize(' "Binary_Sensor.HP_*" ') == "binary_sensor.hp_*"
        assert normalize("`light.kitchen`") == "light.kitchen"

    @pytest.mark.parametrize("bad", ["", "the kitchen lamp", "light.kitchen lamp", "light.", "Hall light [Hall]"])
    def test_what_is_not_an_entity_id_pattern_is_refused(self, bad):
        with pytest.raises(RuleError):
            normalize(bad)


@pytest.mark.unit
class TestVerdicts:
    def test_include_wins_over_exclude_and_no_rule_is_none(self, rules):
        rules.set("binary_sensor.hp_*", EXCLUDE)
        rules.set("binary_sensor.hp_compressor", INCLUDE)
        assert rules.verdict("binary_sensor.hp_pump") == EXCLUDE
        assert rules.verdict("binary_sensor.hp_compressor") == INCLUDE
        assert rules.verdict("binary_sensor.front_door") is None

    def test_a_domain_rule_covers_every_entity_of_the_domain(self, rules):
        rules.set("sensor", EXCLUDE)
        assert rules.verdict("sensor.unifi_cpu") == EXCLUDE
        assert rules.verdict("binary_sensor.front_door") is None

    def test_setting_a_pattern_again_replaces_its_rule_and_says_what_it_was(self, rules):
        assert rules.set("sensor.unifi_*", EXCLUDE) == ("sensor.unifi_*", None)
        assert rules.set("sensor.unifi_*", INCLUDE) == ("sensor.unifi_*", EXCLUDE)
        assert rules.rules() == [("sensor.unifi_*", INCLUDE)]

    def test_clearing_says_what_the_rule_was(self, rules):
        rules.set("sensor", EXCLUDE)
        assert rules.clear("sensor.*") == ("sensor.*", EXCLUDE)
        assert rules.clear("sensor") == ("sensor.*", None)
        assert len(rules) == 0

    def test_matches_are_the_ids_the_pattern_covers_sorted(self, rules):
        ids = ["sensor.unifi_memory", "sensor.unifi_cpu", "light.hall"]
        assert EntityRules.matches("sensor.unifi_*", ids) == ["sensor.unifi_cpu", "sensor.unifi_memory"]


@pytest.mark.unit
class TestFile:
    def test_a_missing_file_is_no_rules(self, rules):
        assert rules.load().rules() == []

    def test_rules_round_trip_through_the_file_in_order(self, rules, tmp_path):
        rules.set("binary_sensor.hp_*", EXCLUDE)
        rules.set("sensor", EXCLUDE)
        rules.set("binary_sensor.plejd_hall_is_gateway", INCLUDE)
        again = EntityRules(tmp_path / "rules.txt").load()
        assert again.rules() == [("binary_sensor.hp_*", EXCLUDE), ("sensor.*", EXCLUDE),
                                 ("binary_sensor.plejd_hall_is_gateway", INCLUDE)]

    def test_a_line_that_cannot_be_a_rule_is_skipped_and_the_rest_kept(self, tmp_path):
        path = tmp_path / "rules.txt"
        path.write_text("# a comment\n\nexclude sensor.unifi_*\nforget light.hall\ninclude not an id\nexclude\n")
        assert EntityRules(path).load().rules() == [("sensor.unifi_*", EXCLUDE)]

    def test_clearing_the_last_rule_leaves_a_file_with_no_rule_lines(self, rules, tmp_path):
        rules.set("sensor", EXCLUDE)
        rules.clear("sensor")
        assert EntityRules(tmp_path / "rules.txt").load().rules() == []
