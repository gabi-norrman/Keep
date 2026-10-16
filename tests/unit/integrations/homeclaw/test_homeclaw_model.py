"""What the prompt shows and the commands resolve: UUID-keyed targets, the Hue and Plejd
accessories left out, rooms as the grouping, and names that never get guessed."""
import pytest

from integrations.homeclaw.HomeClawModel import (
    COVER, DEFAULT_BRIDGE_FILTERS, GARAGE, IRRIGATION, SENSOR, SWITCHED, Index, Target, Unresolved, build_targets,
    event_entry, excluded, real_scenes, render, state_text, zones_of)
from .conftest import (
    ACCESSORIES, AWNING, FLOOR_LAMP, GARAGE_DOOR, HUE_LAMP, PLEJD_LAMP, SCENES, SENSOR_A, SENSOR_B, SENSOR_C, WATER_CONTROL,
    WATER_CONTROL_DETAIL, ZONE_GREENHOUSE, ZONE_PATIO)

BY_ID = {a["id"]: a for a in ACCESSORIES}


def _kept(include=(), exclude=()):
    return [a for a in ACCESSORIES if not excluded(a, DEFAULT_BRIDGE_FILTERS, include, exclude)]


def _index(include=(), exclude=()):
    kept = _kept(include, exclude)
    return Index(build_targets(kept, {WATER_CONTROL: zones_of(WATER_CONTROL_DETAIL)}))


class TestExclusion:
    def test_hue_and_plejd_accessories_and_every_bridge_are_left_out_by_default(self):
        kept = {a["id"] for a in _kept()}
        assert PLEJD_LAMP not in kept and HUE_LAMP not in kept
        assert not any(a.get("is_bridge") for a in _kept())
        assert {FLOOR_LAMP, AWNING, SENSOR_A} <= kept

    def test_include_brings_back_one_accessory_by_uuid_whatever_its_case(self):
        kept = {a["id"] for a in _kept(include=[PLEJD_LAMP.lower()])}
        assert PLEJD_LAMP in kept and HUE_LAMP not in kept

    def test_exclude_drops_one_accessory_no_filter_would(self):
        assert FLOOR_LAMP not in {a["id"] for a in _kept(exclude=[FLOOR_LAMP])}

    def test_no_bridge_filter_keeps_the_hue_and_plejd_lights(self):
        assert excluded(BY_ID[HUE_LAMP], [], [], []) is False


def test_the_uuid_named_scenes_are_hidden_and_the_named_ones_kept():
    assert [s["name"] for s in real_scenes(SCENES)] == ["Good Night", "Movie Evening"]


class TestIndex:
    def test_three_sensors_sharing_a_name_and_room_are_refused_by_name_with_each_ref(self):
        index = _index()
        with pytest.raises(Unresolved) as caught:
            index.resolve("Climate Sensor T1 [Crawl space]", {SENSOR}, "sensor")
        for uid in (SENSOR_A, SENSOR_B, SENSOR_C):
            assert uid[:8] in str(caught.value)

    def test_each_of_them_resolves_by_its_ref_or_full_uuid(self):
        index = _index()
        assert index.resolve(SENSOR_B[:8], {SENSOR}, "sensor").id == SENSOR_B
        assert index.resolve(SENSOR_C.lower(), {SENSOR}, "sensor").id == SENSOR_C
        assert index.resolve(f"[{SENSOR_A[:8]}]", {SENSOR}, "sensor").id == SENSOR_A

    def test_a_unique_name_resolves_with_or_without_its_room(self):
        index = _index()
        assert index.resolve("awning roof", {COVER}, "cover").id == AWNING
        assert index.resolve("Awning Roof [Patio]", {COVER}, "cover").id == AWNING
        with pytest.raises(Unresolved):
            index.resolve("Awning Roof [Garage]", {COVER}, "cover")

    def test_refs_that_would_collide_are_lengthened_until_they_do_not(self):
        twins = [Target(id="ABCDEF12-1111-4000-8000-000000000001", name="One", room="", kind=SENSOR),
                 Target(id="ABCDEF12-2222-4000-8000-000000000002", name="Two", room="", kind=SENSOR)]
        index = Index(twins)
        refs = [index.ref(t) for t in twins]
        assert len(set(refs)) == 2 and all(len(r) > 8 for r in refs)
        assert index.resolve(refs[1], {SENSOR}, "sensor").name == "Two"

    def test_the_zones_of_an_irrigation_control_are_targets_keyed_on_their_service(self):
        index = _index()
        patio = index.resolve("Patio", {IRRIGATION}, "irrigation valve")
        assert (patio.id, patio.service_id) == (WATER_CONTROL, ZONE_PATIO)
        assert index.resolve(ZONE_GREENHOUSE[:8], {IRRIGATION}, "valve").zone == "Greenhouse"

    def test_the_accessory_ref_of_a_zoned_control_asks_which_zone(self):
        with pytest.raises(Unresolved, match="several"):
            _index().resolve(WATER_CONTROL[:8], {IRRIGATION}, "irrigation valve")

    def test_an_empty_ref_is_the_only_one_of_its_kind(self):
        assert _index().resolve("", {GARAGE}, "garage door").name == "Garage Door"
        with pytest.raises(Unresolved):
            _index().resolve("", {SENSOR}, "sensor")

    def test_a_name_of_another_kind_is_refused_naming_what_it_is(self):
        with pytest.raises(Unresolved, match="sensor"):
            _index().resolve(SENSOR_A[:8], SWITCHED, "light")


class TestRendering:
    def test_rooms_are_blocks_each_line_ending_in_its_ref(self):
        text = render(_index(), real_scenes(SCENES))
        crawl = text.split("## Crawl space")[1].split("##")[0]
        assert crawl.count("Climate Sensor T1") == 3
        assert all(f"[{uid[:8]}]" in crawl for uid in (SENSOR_A, SENSOR_B, SENSOR_C))
        assert text.index("## Crawl space") < text.index("## Garage") < text.index("## Patio")
        assert "Movie Evening" in text.split("## Scenes")[1] and "F1000003" not in text

    def test_an_unread_value_is_left_out_not_shown_as_dashes(self):
        line = state_text(_index().resolve(SENSOR_C[:8], {SENSOR}, "sensor"))
        assert "--" not in line and "18.4 °C" in line and "humidity" not in line

    def test_a_cover_on_its_way_says_where_it_is_going(self):
        assert "40" in state_text(_index().resolve("Awning Roof", {COVER}, "cover"))

    def test_an_unreachable_accessory_says_so(self):
        target = Target(id="X", name="Lost", room="", kind=SENSOR, state={"current_temperature": "20°C"}, reachable=False)
        assert state_text(target) == "unreachable"

    def test_zones_show_whether_they_water(self):
        index = _index()
        assert state_text(index.resolve("Patio", {IRRIGATION}, "v")).startswith("watering")
        assert state_text(index.resolve("Greenhouse", {IRRIGATION}, "v")).startswith("off")


class TestEvents:
    def _targets(self):
        targets = {}
        for t in _index().targets:
            targets.setdefault(t.id, []).append(t)
        return targets

    def test_a_change_becomes_a_row_naming_the_accessory_and_its_room_at_the_events_time(self):
        event = {"type": "characteristic_change", "timestamp": "2026-03-01T10:00:05Z", "characteristic": "current_door_state",
                 "previous_value": "closed", "value": "open", "accessory": {"id": GARAGE_DOOR}}
        entry = event_entry(event, self._targets())
        assert "Garage Door [Garage]" in entry.content and "closed → open" in entry.content
        assert entry.timestamp == pytest.approx(1772359205)

    def test_a_zone_event_names_the_zone_by_its_service(self):
        event = {"type": "characteristic_change", "timestamp": "2026-03-01T10:00:00Z", "characteristic": "active",
                 "service": "Greenhouse", "value": "1", "accessory": {"id": WATER_CONTROL}}
        assert "zone Greenhouse" in event_entry(event, self._targets()).content

    def test_other_types_and_left_out_accessories_make_no_row(self):
        assert event_entry({"type": "homes_updated", "timestamp": "2026-03-01T10:00:00Z"}, self._targets()) is None
        plejd = {"type": "characteristic_change", "timestamp": "2026-03-01T10:00:00Z", "characteristic": "power",
                 "value": "true", "accessory": {"id": PLEJD_LAMP}}
        assert event_entry(plejd, self._targets()) is None
