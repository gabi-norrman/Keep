"""HomeAssistantModel: states and registries → the four lists, the values, HA's exposure rule,
areas through devices, the name index and the feed wording (plans/HOME_ASSISTANT_PLAN.md)."""
import json
from datetime import datetime
from pathlib import Path

import pytest

from assistant.integration.Feed import FeedEntry
from integrations.home_assistant.EntityRules import EntityRules
from integrations.home_assistant.HomeAssistantModel import (
    NameIndex, Registries, Unresolved, area_of, build, drop_reason, feed_entry, format_value, outage_row, recovery_row,
    should_expose, tracks_availability)
from assistant.integration.HomeAutomation import render

DATA = Path(__file__).parents[2] / "data" / "home" / "ha"


def load_house():
    states = {s["entity_id"]: s for s in json.loads((DATA / "states.json").read_text())}
    raw = json.loads((DATA / "registries.json").read_text())
    registries = Registries(areas={a["area_id"]: a for a in raw["areas"]},
                            entities={e["entity_id"]: e for e in raw["entities"]},
                            devices={d["id"]: d for d in raw["devices"]},
                            exposed=raw["exposed"], expose_new=raw["expose_new"])
    return states, registries


@pytest.fixture
def house():
    return load_house()


def state(entity_id, value, **attributes):
    return {"entity_id": entity_id, "state": value, "attributes": attributes,
            "last_changed": "2026-09-15T10:00:00+00:00", "last_updated": "2026-09-15T10:00:00+00:00"}


@pytest.mark.unit
class TestBuild:
    def test_golden_rendering(self, house):
        snapshot, _ = build(*house, exposed_only=True)
        assert render(snapshot) == (DATA / "rendered.md").read_text()

    def test_domains_land_in_their_lists(self, house):
        snapshot, _ = build(*house, exposed_only=False)
        assert {e.name for e in snapshot.lights} == {"Kitchen spots light", "Kitchen spots switch", "Bedroom lamp",
                                                    "Hall light", "Garage light", "Bedroom fan", "Blinds",
                                                    "Front door lock"}
        assert "Thermostat" in {e.name for e in snapshot.sensors}
        assert {e.name for e in snapshot.variables} == {"Night", "Pool target", "Marcus"}
        assert {e.name for e in snapshot.actions} == {"Movie night", "Goodnight", "Sunset lights"}
        # sun.* and zone.* have no list
        names = {e.name for s in ("lights", "sensors", "variables", "actions") for e in getattr(snapshot, s)}
        assert "Sun" not in names and "Home" not in names

    def test_exposed_only_applies_choices_and_the_default_rule(self, house):
        snapshot, index = build(*house, exposed_only=True)
        assert "light.bedroom" not in index                 # chosen not exposed, though a light
        assert "sensor.power" in index                      # chosen exposed, though a power sensor
        assert "sensor.pressure" not in index               # default rule: not a listed class
        assert "sensor.hue_signal" not in index             # diagnostic category
        assert "binary_sensor.hidden_motion" not in index   # hidden
        assert "binary_sensor.front_door" in index          # door class is exposed by default
        assert "scene.movie_night" in index                 # scene domain is exposed by default
        assert "lock.front" in index                        # a lock is not exposed by default; this one was chosen
        assert len(index) == 19

    def test_everything_but_the_hidden_when_not_exposed_only(self, house):
        _, index = build(*house, exposed_only=False)
        assert "light.bedroom" in index and "sensor.hue_signal" in index
        assert "binary_sensor.hidden_motion" not in index
        assert len(index) == 22


@pytest.mark.unit
class TestDropReason:
    def test_hidden_and_disabled_are_dropped_whatever_exposed_only_says(self, house):
        states, registries = house
        registries.entities["light.hall"]["disabled_by"] = "user"
        for exposed_only in (True, False):
            assert drop_reason(states["binary_sensor.hidden_motion"], registries, exposed_only) == "hidden"
            assert drop_reason(states["light.hall"], registries, exposed_only) == "disabled"
        assert drop_reason(states["sun.sun"], registries, False) == "domain"
        assert drop_reason(states["sensor.pressure"], registries, True) == "not exposed"
        assert drop_reason(states["sensor.pressure"], registries, False) is None

    def test_an_exclude_rule_drops_and_an_include_rule_wins(self, house, tmp_path):
        states, registries = house
        rules = EntityRules(tmp_path / "rules.txt")
        rules.set("sensor", "exclude")
        rules.set("binary_sensor.hidden_*", "include")
        assert drop_reason(states["sensor.power"], registries, True, rules) == "excluded"
        assert drop_reason(states["binary_sensor.hidden_motion"], registries, True, rules) is None
        rules.set("binary_sensor.*", "exclude")
        assert drop_reason(states["binary_sensor.hidden_motion"], registries, True, rules) is None   # include wins
        assert drop_reason(states["binary_sensor.front_door"], registries, True, rules) == "excluded"
        rules.set("sun.sun", "include")
        assert drop_reason(states["sun.sun"], registries, False, rules) == "domain"            # never a list for it
        _, index = build(states, registries, exposed_only=True, rules=rules)
        assert "binary_sensor.hidden_motion" in index and "sensor.power" not in index

    def test_lists_are_sorted_by_name(self, house):
        snapshot, _ = build(*house, exposed_only=True)
        names = [e.name.lower() for e in snapshot.lights]
        assert names == sorted(names)

    def test_same_name_same_area_gets_the_domain_appended(self, house):
        snapshot, index = build(*house, exposed_only=True)
        assert {e.name for e in snapshot.lights if e.name.startswith("Kitchen spots")} == \
            {"Kitchen spots light", "Kitchen spots switch"}
        assert index.resolve("Kitchen spots light (Kök)") == "light.kitchen_spots"
        with pytest.raises(Unresolved, match="could be Kitchen spots light \\[Kök\\], Kitchen spots switch \\[Kök\\]"):
            index.resolve("Kitchen spots")


@pytest.mark.unit
class TestAreas:
    def test_entity_area_then_device_area_then_parent_device(self, house):
        _, registries = house
        assert area_of("light.bedroom", registries)["name"] == "Sovrum"          # its own
        assert area_of("light.kitchen_spots", registries)["name"] == "Kök"       # the device's
        assert area_of("binary_sensor.front_door", registries)["name"] == "Hall" # the child device's parent's
        assert area_of("input_boolean.night", registries) is None
        assert area_of("sun.sun", registries) is None                             # not in the registry


@pytest.mark.unit
class TestShouldExpose:
    def test_stored_choice_wins_over_the_domain_rule(self):
        registries = Registries(exposed={"light.a": {"conversation": False}, "sensor.b": {"conversation": True}})
        assert not should_expose(state("light.a", "on"), registries)
        assert should_expose(state("sensor.b", "1"), registries)

    def test_default_rule_by_domain_and_device_class(self):
        registries = Registries()
        assert should_expose(state("light.a", "on"), registries)
        assert should_expose(state("cover.a", "open"), registries)
        assert not should_expose(state("input_boolean.a", "on"), registries)
        assert not should_expose(state("automation.a", "on"), registries)
        assert should_expose(state("binary_sensor.a", "on", device_class="motion"), registries)
        assert not should_expose(state("binary_sensor.a", "on", device_class="battery"), registries)
        assert should_expose(state("sensor.a", "1", device_class="temperature"), registries)
        assert not should_expose(state("sensor.a", "1", device_class="power"), registries)

    def test_device_class_from_the_registry_entry(self):
        registries = Registries(entities={"sensor.a": {"entity_id": "sensor.a", "original_device_class": "humidity"}})
        assert should_expose(state("sensor.a", "40"), registries)

    def test_hidden_disabled_and_categorised_never(self):
        registries = Registries(entities={"light.h": {"entity_id": "light.h", "hidden_by": "user"},
                                          "light.d": {"entity_id": "light.d", "disabled_by": "integration"},
                                          "light.c": {"entity_id": "light.c", "entity_category": "config"}},
                                exposed={"light.h": {"conversation": True}})
        assert not should_expose(state("light.h", "on"), registries)
        assert not should_expose(state("light.d", "on"), registries)
        assert not should_expose(state("light.c", "on"), registries)

    def test_expose_new_off_hides_what_has_no_choice(self):
        registries = Registries(expose_new=False, exposed={"light.b": {"conversation": True}})
        assert not should_expose(state("light.a", "on"), registries)
        assert should_expose(state("light.b", "on"), registries)


@pytest.mark.unit
class TestFormatValue:
    @pytest.mark.parametrize("entity, expected", [
        (state("light.a", "on", brightness=255), "100%"),
        (state("light.a", "on", brightness=1), "1%"),
        (state("light.a", "on", brightness=0), "1%"),
        (state("light.a", "on"), "on"),
        (state("light.a", "off", brightness=200), "off"),
        (state("light.a", "unavailable"), "unavailable"),
        (state("switch.a", "on"), "on"),
        (state("fan.a", "on", percentage=33.3), "33%"),
        (state("fan.a", "off", percentage=33), "off"),
        (state("cover.a", "closed", current_position=0), "closed 0%"),
        (state("cover.a", "opening"), "opening"),
        (state("lock.a", "unlocked"), "unlocked"),
        (state("sensor.a", "21.456", unit_of_measurement="°C"), "21.456 °C"),
        (state("sensor.a", "1013"), "1013"),
        (state("binary_sensor.a", "on", device_class="door"), "open"),
        (state("binary_sensor.a", "off", device_class="motion"), "clear"),
        (state("binary_sensor.a", "on", device_class="lock"), "unlocked"),
        (state("binary_sensor.a", "on"), "on"),
        (state("climate.a", "cool", current_temperature=24.5, temperature=21), "cool, 24.5, target 21"),
        (state("climate.a", "off"), "off"),
        (state("media_player.a", "playing", media_title="Song"), "playing: Song"),
        (state("media_player.a", "playing", media_title="Song", media_artist="Band"), "playing: Song – Band"),
        (state("media_player.a", "paused", media_title="Song"), "paused"),
        (state("weather.a", "sunny", temperature=18, temperature_unit="°C"), "sunny, 18 °C"),
        (state("input_boolean.a", "off"), "off"),
        (state("input_select.a", "Away"), "Away"),
        (state("person.a", "not_home"), "not_home"),
        (state("scene.a", "2026-09-14T20:00:00+00:00"), "scene"),
        (state("script.a", "off"), "script"),
        (state("automation.a", "off"), "automation, off"),
        (state("button.a", "unknown"), "button"),
        (state("input_button.a", "2026-09-14T20:00:00+00:00"), "button"),
    ])
    def test_values(self, entity, expected):
        assert format_value(entity) == expected

    def test_sensor_display_precision_from_the_registry(self):
        entry = {"options": {"sensor": {"display_precision": 1}}}
        assert format_value(state("sensor.a", "21.456", unit_of_measurement="°C"), entry) == "21.5 °C"
        assert format_value(state("sensor.a", "unknown"), entry) == "unknown"
        assert format_value(state("sensor.a", "Rainy"), entry) == "Rainy"

    def test_no_value_contains_parens(self, house):
        snapshot, _ = build(*house, exposed_only=False)
        for section in ("lights", "sensors", "variables", "actions"):
            for entity in getattr(snapshot, section):
                assert "(" not in entity.value and ")" not in entity.value, entity


@pytest.mark.unit
class TestNameIndex:
    @pytest.fixture
    def index(self, house):
        return build(*house, exposed_only=True)[1]

    def test_name_with_area_as_from_display_hands_it_over(self, index):
        assert index.resolve("Temperature (Kök)") == "sensor.kitchen_temperature"
        assert index.resolve("Blinds (Sovrum)") == "cover.blinds"

    def test_brackets_case_and_spacing_do_not_matter(self, index):
        assert index.resolve("temperature [SOVRUM]") == "sensor.bedroom_temperature"
        assert index.resolve("  blinds   (sovrum) ") == "cover.blinds"

    def test_bare_name_when_unique(self, index):
        assert index.resolve("Blinds") == "cover.blinds"
        assert index.resolve("Night") == "input_boolean.night"

    def test_bare_name_when_ambiguous_says_which(self, index):
        with pytest.raises(Unresolved) as e:
            index.resolve("Temperature")
        assert str(e.value) == '"Temperature" could be Temperature [Sovrum], Temperature [Kök] — say which.'

    def test_aliases_of_the_entity_and_of_the_area(self, index):
        assert index.resolve("Spotlights") == "light.kitchen_spots"
        assert index.resolve("Spotlights (Kök)") == "light.kitchen_spots"
        assert index.resolve("Kitchen spots light (Kitchen)") == "light.kitchen_spots"

    def test_a_wrong_or_invented_place_falls_back_to_the_bare_name(self, index):
        assert index.resolve("Blinds (Kitchen)") == "cover.blinds"
        assert index.resolve("Movie night (scene)") == "scene.movie_night"

    def test_unknown_name_suggests_close_ones(self, index):
        with pytest.raises(Unresolved) as e:
            index.resolve("Nigth")
        assert str(e.value) == 'No entity named "Nigth". Did you mean: Night?'
        with pytest.raises(Unresolved) as e:
            index.resolve("Flux capacitor")
        assert str(e.value) == 'No entity named "Flux capacitor".'
        with pytest.raises(Unresolved, match="No entity name given"):
            index.resolve("  ")

    def test_domain_and_display(self, index):
        assert index.domain("cover.blinds") == "cover"
        assert index.display("cover.blinds") == "Blinds [Sovrum]"
        assert index.display("input_boolean.night") == "Night"
        assert index.display("sun.sun") is None

@pytest.mark.unit
class TestFeedEntry:
    @pytest.fixture
    def index(self, house):
        return build(*house, exposed_only=True)[1]

    def test_light_on_with_brightness_and_off(self, index):
        entry = feed_entry(state("light.hall", "off"), state("light.hall", "on", brightness=128), index)
        assert isinstance(entry, FeedEntry)
        assert entry.content == "Hall light [Hall] turned on 50%"
        assert entry.timestamp == 1789466400.0     # 2026-09-15T10:00:00+00:00
        assert feed_entry(state("light.hall", "on"), state("light.hall", "off"), index).content == "Hall light [Hall] turned off"

    def test_who_did_it(self, index):
        entry = feed_entry(state("light.hall", "off"), state("light.hall", "on"), index, user="Marcus")
        assert entry.content == "Hall light [Hall] turned on by Marcus"

    @pytest.mark.parametrize("old, new, expected", [
        (state("cover.blinds", "closed"), state("cover.blinds", "open", current_position=100), "Blinds [Sovrum] opened"),
        (state("cover.blinds", "open"), state("cover.blinds", "closing"), "Blinds [Sovrum] is closing"),
        (state("lock.front", "locked"), state("lock.front", "unlocked"), "Front door lock [Hall] unlocked"),
        (state("binary_sensor.front_door", "off"), state("binary_sensor.front_door", "on", device_class="door"), "Front door [Hall] opened"),
        (state("binary_sensor.front_door", "on"), state("binary_sensor.front_door", "off", device_class="door"), "Front door [Hall] closed"),
        (state("person.marcus", "home"), state("person.marcus", "not_home"), "Marcus left home"),
        (state("person.marcus", "not_home"), state("person.marcus", "home"), "Marcus arrived home"),
        (state("person.marcus", "home"), state("person.marcus", "Work"), "Marcus is at Work"),
        (state("input_boolean.night", "off"), state("input_boolean.night", "on"), "Night turned on"),
        (state("input_number.pool_target", "27.0"), state("input_number.pool_target", "28.0"), "Pool target changed to 28.0"),
        (state("fan.bedroom", "off"), state("fan.bedroom", "on", percentage=40), "Bedroom fan [Sovrum] turned on 40%"),
        (state("light.hall", "on"), state("light.hall", "unavailable"), "Hall light [Hall] became unavailable"),
        (state("automation.sunset_lights", "on", last_triggered="2026-09-14T18:45:00+00:00"),
         state("automation.sunset_lights", "on", last_triggered="2026-09-15T18:45:00+00:00"), "Sunset lights [automation] triggered"),
        (state("automation.sunset_lights", "on"), state("automation.sunset_lights", "off"), "Sunset lights [automation] disabled"),
        (state("script.goodnight", "off", last_triggered=None), state("script.goodnight", "on", last_triggered="2026-09-15T22:00:00+00:00"), "Goodnight [script] ran"),
        (state("scene.movie_night", "2026-09-14T20:00:00+00:00"), state("scene.movie_night", "2026-09-15T20:00:00+00:00"), "Movie night [scene] activated"),
    ])

    def test_wording(self, index, old, new, expected):
        assert feed_entry(old, new, index).content == expected

    @pytest.mark.parametrize("old, new, expected", [
        (state("switch.kitchen_spots_plug", "off"), state("switch.kitchen_spots_plug", "on"), "Kitchen spots switch [Kök] turned on"),
        (state("switch.kitchen_spots_plug", "on"), state("switch.kitchen_spots_plug", "off"), "Kitchen spots switch [Kök] turned off"),
        (state("lock.front", "unlocked"), state("lock.front", "locking"), "Front door lock [Hall] is locking"),
        (state("lock.front", "locked"), state("lock.front", "jammed"), "Front door lock [Hall] jammed"),
        (state("automation.sunset_lights", "off"), state("automation.sunset_lights", "on"), "Sunset lights [automation] enabled"),
    ])
    def test_switch_lock_and_automation_wording(self, index, old, new, expected):
        assert feed_entry(old, new, index).content == expected

    @pytest.mark.parametrize("old, new", [
        (state("light.hall", "on", brightness=100), state("light.hall", "on", brightness=200)),     # attributes only
        (state("sensor.kitchen_temperature", "21.4"), state("sensor.kitchen_temperature", "21.5")),  # sensors tick
        (state("climate.house", "heat"), state("climate.house", "cool")),
        (state("media_player.living", "playing"), state("media_player.living", "paused")),
        (state("light.bedroom", "off"), state("light.bedroom", "on")),                              # not in the snapshot
        (state("sun.sun", "above_horizon"), state("sun.sun", "below_horizon")),                    # no list
        (state("light.hall", "on"), state("light.hall", "unknown")),
        (state("script.goodnight", "off", last_triggered="2026-09-14T22:30:00+00:00"),
         state("script.goodnight", "off", last_triggered="2026-09-14T22:30:00+00:00")),            # nothing ran
        (None, state("light.hall", "on")),                                                        # appeared
        (state("light.hall", "on"), None),                                                        # vanished
    ])
    def test_silence(self, index, old, new):
        assert feed_entry(old, new, index) is None

    def test_a_recovery_from_unavailable_is_never_worded_as_an_action(self, index):
        """A hub that drops for half a minute takes every cover behind it through unavailable;
        the covers never moved, so "closed" would be a lie — the integration words a recovery
        against the pre-outage state instead."""
        assert feed_entry(state("cover.blinds", "unavailable"), state("cover.blinds", "closed"), index) is None
        assert feed_entry(state("light.hall", "unavailable"), state("light.hall", "on"), index) is None

    def test_a_button_or_a_diagnostic_entity_going_unavailable_is_silent(self, index):
        assert feed_entry(state("button.reboot", "unknown"), state("button.reboot", "unavailable"), NameIndex()) is None
        assert feed_entry(state("input_button.ring", "2026-09-15T09:00:00+00:00"),
                          state("input_button.ring", "unavailable"), NameIndex()) is None
        diagnostic = {"entity_category": "diagnostic"}
        assert feed_entry(state("light.hall", "on"), state("light.hall", "unavailable"), index, entry=diagnostic) is None
        assert feed_entry(state("light.hall", "on"), state("light.hall", "unavailable"), index, entry={}) is not None

    def test_tracks_availability_follows_the_feed_rules(self, index):
        assert tracks_availability("light.hall", index, {}) is True
        assert tracks_availability("light.hall", index, {"entity_category": "config"}) is False
        assert tracks_availability("button.reboot", NameIndex(), None) is False
        assert tracks_availability("sensor.kitchen_temperature", index, {}) is False, "a sensor's changes are never rows"
        assert tracks_availability("light.bedroom", index, {}) is False, "not in the snapshot"

    def test_outage_and_recovery_rows_name_the_device_the_entities_and_the_time(self):
        since = 1789466400.0        # 2026-09-15T10:00:00+00:00
        row = outage_row(["Hall light [Hall]", "Blinds [Sovrum]"], "Hue bridge", since, since + 400)
        assert row.timestamp == since + 400
        assert row.content.startswith("Hue bridge")
        assert "Hall light [Hall]" in row.content and "Blinds [Sovrum]" in row.content
        assert datetime.fromtimestamp(since).strftime("%H:%M") in row.content
        loose = outage_row(["Night"], None, since, since + 400)
        assert loose.content.startswith("Night") and "None" not in loose.content
        back = recovery_row(["Hall light [Hall]"], "Hue bridge", since, since + 900)
        assert back.timestamp == since + 900
        assert "Hall light [Hall]" in back.content and back.content.startswith("Hue bridge")
        assert back.content != row.content, "a recovery reads differently from an outage"

    def test_an_entity_not_yet_indexed_uses_its_friendly_name_when_no_index(self):
        entry = feed_entry(state("light.new", "off"), state("light.new", "on", friendly_name="New (lamp)"), NameIndex())
        assert entry.content == "New [lamp] turned on"

    def test_a_trigger_is_stamped_with_last_updated_not_last_changed(self, index):
        """An automation staying "on" keeps its last_changed across triggers; the row must
        carry the trigger's own time or repeated triggers collide in the history."""
        old = state("automation.sunset_lights", "on", last_triggered="2026-09-14T18:45:00+00:00")
        new = state("automation.sunset_lights", "on", last_triggered="2026-09-15T18:45:00+00:00")
        new["last_updated"] = "2026-09-15T18:45:00+00:00"
        entry = feed_entry(old, new, index)
        assert entry.content == "Sunset lights [automation] triggered"
        assert entry.timestamp == 1789497900.0     # 2026-09-15T18:45:00+00:00, not 10:00

    def test_timestamp_falls_back_to_now_without_a_parseable_time(self, index):
        new = state("light.hall", "on")
        new["last_changed"] = "yesterday"
        new["last_updated"] = None
        entry = feed_entry(state("light.hall", "off"), new, index)
        assert entry.timestamp > 1_700_000_000
