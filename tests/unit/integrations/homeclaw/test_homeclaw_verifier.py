"""The automation verifier (issue #110): which runs it plans from the app's automations, what
it expects of them, how a reading is judged, and the check of one run against the fake app —
a deviation wakes the assistant with the facts, a run where everything took does not."""
import threading
from datetime import date, datetime, timedelta
from unittest.mock import Mock, patch
from zoneinfo import ZoneInfo

import pytest

from integrations.homeclaw import HomeClawVerifier as verifier_module
from integrations.homeclaw.HomeClawVerifier import (
    AutomationVerifier, Expectation, Run, applies, compare, deviation_report, expectations_of, fire_at,
    normalise, plan, read_state)
from integrations.homeclaw.SunTimes import sun_times
from .conftest import AUTOMATIONS, AWNING, FLOOR_LAMP, HIDDEN_SET, MORNING, SCENE_ACTIONS, THERMOSTAT, ok, settings_patch

TZ = ZoneInfo("Europe/Stockholm")
TUESDAY, SATURDAY, SUNDAY = date(2026, 11, 3), date(2026, 11, 7), date(2026, 11, 8)
SUNRISE, SUNSET = datetime(2026, 11, 3, 7, 12, tzinfo=TZ), datetime(2026, 11, 3, 15, 50, tzinfo=TZ)
DUSK = "A7000003-0000-4000-8000-000000000003"
DUSK_SET = "F2000003-0000-4000-8000-000000000003"
DUSK_AUTOMATION = {"id": DUSK, "name": "Dusk Lights", "enabled": True, "trigger_type": "significant_time",
                   "event_summary": "at sunset-45m", "conditions": [],
                   "action_sets": [{"id": DUSK_SET, "name": "F2000003-0000-4000-8000-0000000000AA", "hidden": True, "action_count": 1}]}
DUSK_ACTIONS = [{"accessory": "Floor Lamp", "accessory_id": FLOOR_LAMP, "characteristic": "power", "value": "1"},
                {"accessory": "Floor Lamp", "accessory_id": FLOOR_LAMP, "characteristic": "hue", "value": "120"}]


def sun(day):
    return (SUNRISE.replace(year=day.year, month=day.month, day=day.day),
            SUNSET.replace(year=day.year, month=day.month, day=day.day))


def no_sun(day):
    return None


def actions_of(action_set_id):
    return {**SCENE_ACTIONS, DUSK_SET: DUSK_ACTIONS}.get(action_set_id, [])


def morning(**overrides):
    return {**next(a for a in AUTOMATIONS if a["id"] == MORNING),
            "conditions": [{"type": "weekdays", "weekdays": [2, 3, 4, 5, 6]}], **overrides}


def lamp_detail(power, brightness):
    return {"id": FLOOR_LAMP, "name": "Floor Lamp", "services": [
        {"id": "S1", "name": "Floor Lamp", "type": "00000043-0000-1000-8000-0026BB765291",
         "characteristics": [{"name": "power", "value": power}, {"name": "brightness", "value": brightness}]}]}


class TestNormalise:
    def test_an_actions_1_and_a_readings_true_are_the_same_switch_state(self):
        assert normalise("power", "1") == normalise("power", "true") == "on"
        assert normalise("active", "0") == normalise("active", "false") == "off"

    def test_a_formatted_temperature_equals_the_bare_number_the_action_wrote(self):
        assert normalise("target_temperature", "21.0°C") == normalise("target_temperature", "21")

    def test_a_heating_mode_number_is_the_name_the_reading_carries(self):
        assert normalise("target_heating_cooling", "1") == normalise("target_heating_cooling", "heat")


class TestApplies:
    def test_a_weekday_automation_fires_on_a_tuesday_and_not_on_a_sunday(self):
        assert applies(morning(), TUESDAY) is True
        assert applies(morning(), SUNDAY) is False

    def test_homekit_counts_sunday_as_weekday_one(self):
        assert applies(morning(conditions=[{"type": "weekdays", "weekdays": [1]}]), SUNDAY) is True

    def test_a_disabled_automation_never_fires(self):
        assert applies(morning(enabled=False), TUESDAY) is False

    def test_a_condition_code_cannot_evaluate_leaves_the_verdict_open(self):
        conditions = [{"type": "weekdays", "weekdays": [3]}, {"type": "characteristic", "summary": "Someone is home"}]
        assert applies(morning(conditions=conditions), TUESDAY) is None


class TestFireAt:
    def test_a_calendar_trigger_fires_at_its_clock_time_that_day(self):
        assert fire_at(morning(), TUESDAY, TZ, sun) == datetime(2026, 11, 3, 6, 30, tzinfo=TZ)

    def test_a_sunset_trigger_applies_its_offset_to_the_days_sunset(self):
        assert fire_at(DUSK_AUTOMATION, TUESDAY, TZ, sun) == SUNSET - timedelta(minutes=45)

    def test_a_sunrise_trigger_with_a_plus_offset(self):
        automation = {**DUSK_AUTOMATION, "event_summary": "at sunrise+15m"}
        assert fire_at(automation, TUESDAY, TZ, sun) == SUNRISE + timedelta(minutes=15)

    def test_a_sun_trigger_without_sun_times_has_no_moment(self):
        assert fire_at(DUSK_AUTOMATION, TUESDAY, TZ, no_sun) is None

    def test_a_button_trigger_has_no_moment(self):
        assert fire_at(next(a for a in AUTOMATIONS if a["id"] != MORNING), TUESDAY, TZ, sun) is None


class TestExpectations:
    def test_the_checked_characteristics_of_a_hidden_action_set(self):
        found = expectations_of(morning(), actions_of)
        assert {(e.accessory_id, e.characteristic, e.value) for e in found} == {
            (FLOOR_LAMP, "power", "on"), (FLOOR_LAMP, "brightness", "30"),
            (AWNING, "target_position", "0"), (THERMOSTAT, "target_temperature", "21")}
        assert all(e.automation == "Weekday Morning" for e in found)

    def test_a_colour_action_is_not_an_expectation(self):
        assert [e.characteristic for e in expectations_of(DUSK_AUTOMATION, actions_of)] == ["power"]


class TestPlan:
    def test_the_next_run_is_the_mornings_with_everything_it_sets(self):
        now = datetime(2026, 11, 3, 5, 0, tzinfo=TZ)
        run = plan([morning(), DUSK_AUTOMATION, *AUTOMATIONS[1:]], now, sun, actions_of)
        assert run.at == datetime(2026, 11, 3, 6, 30, tzinfo=TZ)
        assert run.automations == ["Weekday Morning"]
        assert run.accessory_ids == [FLOOR_LAMP, AWNING, THERMOSTAT]

    def test_once_the_morning_has_fired_the_dusk_run_is_next(self):
        now = datetime(2026, 11, 3, 6, 31, tzinfo=TZ)
        run = plan([morning(), DUSK_AUTOMATION], now, sun, actions_of)
        assert run.at == SUNSET - timedelta(minutes=45) and run.automations == ["Dusk Lights"]

    def test_automations_of_one_minute_are_one_run(self):
        twin = morning(id="A7000009-0000-4000-8000-000000000009", name="Weekday Blinds")
        run = plan([morning(), twin], datetime(2026, 11, 3, 5, 0, tzinfo=TZ), sun, actions_of)
        assert run.automations == ["Weekday Morning", "Weekday Blinds"]
        assert len(run.expectations) == 8

    def test_on_a_saturday_a_weekday_automation_is_planned_for_monday(self):
        run = plan([morning()], datetime(2026, 11, 7, 10, 0, tzinfo=TZ), sun, actions_of)
        assert run.at == datetime(2026, 11, 9, 6, 30, tzinfo=TZ)

    def test_a_run_that_sets_nothing_readable_is_passed_over(self):
        colour_only = {**DUSK_AUTOMATION, "event_summary": "at sunrise", "action_sets": [{"id": "COLOUR", "hidden": True}]}
        run = plan([colour_only, DUSK_AUTOMATION], datetime(2026, 11, 3, 5, 0, tzinfo=TZ), sun,
                   lambda i: DUSK_ACTIONS[1:] if i == "COLOUR" else actions_of(i))
        assert run.automations == ["Dusk Lights"]

    def test_an_automation_with_an_open_verdict_is_not_planned(self):
        guarded = morning(conditions=[{"type": "characteristic", "summary": "Someone is home"}])
        assert plan([guarded], datetime(2026, 11, 3, 5, 0, tzinfo=TZ), sun, actions_of) is None

    def test_nothing_timed_means_no_run(self):
        assert plan(AUTOMATIONS[1:], datetime(2026, 11, 3, 5, 0, tzinfo=TZ), sun, actions_of) is None


class TestCompare:
    EXPECTED = [Expectation(FLOOR_LAMP, "Floor Lamp", "power", "on", "Weekday Morning"),
                Expectation(FLOOR_LAMP, "Floor Lamp", "brightness", "30", "Weekday Morning")]

    def test_the_reading_comes_from_the_first_service_that_has_it(self):
        detail = lamp_detail("true", "30")
        detail["services"].append({"id": "S2", "characteristics": [{"name": "brightness", "value": "100"}]})
        assert read_state(detail) == {"power": "on", "brightness": "30"}

    def test_a_lamp_that_took_the_change_is_no_deviation(self):
        assert compare(self.EXPECTED, {FLOOR_LAMP: {"power": "on", "brightness": "29"}}) == []

    def test_a_lamp_that_flipped_back_deviates_on_what_it_lost(self):
        found = compare(self.EXPECTED, {FLOOR_LAMP: {"power": "off", "brightness": "30"}})
        assert [(d.expectation.characteristic, d.actual) for d in found] == [("power", "off")]

    def test_a_lamp_switched_off_is_held_to_that_alone(self):
        off = [Expectation(FLOOR_LAMP, "Floor Lamp", "power", "off", "Night"),
               Expectation(FLOOR_LAMP, "Floor Lamp", "brightness", "30", "Night")]
        assert compare(off, {FLOOR_LAMP: {"power": "off", "brightness": "70"}}) == []

    def test_an_unread_accessory_confirms_nothing(self):
        found = compare(self.EXPECTED, {FLOOR_LAMP: None})
        assert [d.actual for d in found] == [None, None]

    def test_the_report_states_the_facts_of_the_deviation_and_the_rest(self):
        run = Run(datetime(2026, 11, 3, 6, 30, tzinfo=TZ), ["Weekday Morning"],
                  self.EXPECTED + [Expectation(AWNING, "Awning Roof", "target_position", "0", "Weekday Morning")])
        deviations = compare(run.expectations, {FLOOR_LAMP: {"power": "off", "brightness": "30"}, AWNING: {"target_position": "0"}})
        text = deviation_report(run, {FLOOR_LAMP: {"power": "off", "brightness": "70"}, AWNING: {"target_position": "100"}},
                                deviations, {FLOOR_LAMP: ["06:30:40 power true → false"]}, 180)
        assert "06:30" in text and "Floor Lamp" in text and "reports off" in text and "before the run: off" in text
        assert "true → false" in text and "other accessory reports" in text and "Awning" not in text


def verifier_config(latitude=59.33, longitude=18.07):
    fake = Mock()
    fake.get_home_timezone.return_value = "Europe/Stockholm"
    fake.get_home_latitude.return_value = latitude
    fake.get_home_longitude.return_value = longitude
    return patch.object(verifier_module, "config", fake)


@pytest.fixture
def chat():
    fake = Mock()
    fake.open_conversation.return_value = True
    with patch.object(verifier_module, "notifier", return_value=fake):
        yield fake


@pytest.fixture
def verifier(homeclaw):
    with verifier_config():
        yield AutomationVerifier(homeclaw, settle_seconds=0)


def past_run(expectations=None):
    return Run(datetime.now(TZ) - timedelta(minutes=1), ["Weekday Morning"], expectations or [
        Expectation(FLOOR_LAMP, "Floor Lamp", "power", "on", "Weekday Morning"),
        Expectation(FLOOR_LAMP, "Floor Lamp", "brightness", "30", "Weekday Morning")])


class TestVerify:
    def test_the_accessories_are_read_live_before_and_after_the_run(self, app, verifier, chat):
        app.replies["get_accessory"] = ok(lamp_detail("true", "30"))
        assert verifier.verify(past_run()) == []
        assert app.of("get_accessory") == [{"id": FLOOR_LAMP, "refresh": True}] * 2
        chat.open_conversation.assert_not_called()

    def test_a_lamp_that_flipped_back_wakes_the_assistant_with_the_facts(self, app, verifier, chat):
        readings = iter([lamp_detail("false", "70"), lamp_detail("false", "30")])
        app.replies["get_accessory"] = lambda args: ok(next(readings))
        app.replies["events"] = ok({"events": [
            {"type": "characteristic_change", "timestamp": "2026-11-03T05:30:40Z", "characteristic": "power",
             "previous_value": "true", "value": "false", "accessory": {"id": FLOOR_LAMP, "name": "Floor Lamp"}},
            {"type": "characteristic_change", "timestamp": "2026-11-03T05:30:12Z", "characteristic": "power",
             "previous_value": "false", "value": "true", "accessory": {"id": FLOOR_LAMP, "name": "Floor Lamp"}},
            {"type": "characteristic_change", "timestamp": "2026-11-03T05:30:13Z", "characteristic": "target_position",
             "previous_value": "100", "value": "0", "accessory": {"id": AWNING, "name": "Awning Roof"}}]})
        deviations = verifier.verify(past_run())
        assert [d.expectation.characteristic for d in deviations] == ["power"]
        text = chat.open_conversation.call_args.args[0]
        assert "Floor Lamp" in text and "reports off" in text and "before the run: off" in text
        assert text.index("false → true") < text.index("true → false") and "Awning" not in text
        assert app.of("events")[0]["type"] == "characteristic_change"

    def test_without_an_owner_chat_the_report_is_a_system_note(self, app, verifier, chat):
        app.replies["get_accessory"] = ok(lamp_detail("false", "30"))
        chat.open_conversation.return_value = False
        with patch.object(verifier_module, "system_note") as note:
            verifier.verify(past_run())
        assert note.call_args.args[0] == "HomeClaw" and "Floor Lamp" in note.call_args.args[1]

    def test_an_accessory_the_app_cannot_read_is_reported_unread(self, app, verifier, chat):
        app.replies["get_accessory"] = {"success": False, "error": "Accessory not found"}
        deviations = verifier.verify(past_run())
        assert [d.actual for d in deviations] == [None, None]
        assert "unread" in chat.open_conversation.call_args.args[0]

    def test_the_next_run_comes_from_the_apps_automations(self, app, verifier):
        run = verifier.next_run(datetime(2026, 11, 3, 5, 0, tzinfo=TZ))
        assert run.automations == ["Weekday Morning"] and run.at.hour == 6
        assert app.of("get_scene") == [{"id": HIDDEN_SET}]

    def test_without_coordinates_a_sun_automation_is_not_planned(self, app, verifier):
        app.replies["list_automations"] = ok([DUSK_AUTOMATION])
        app.replies["get_automation"] = ok(DUSK_AUTOMATION)
        with verifier_config(latitude=None, longitude=None):
            assert verifier.next_run(datetime(2026, 11, 3, 5, 0, tzinfo=TZ)) is None

    def test_a_stop_while_waiting_for_the_run_checks_nothing(self, app, verifier, chat):
        app.replies["get_accessory"] = ok(lamp_detail("false", "30"))
        run = past_run()
        run.at = datetime.now(TZ) + timedelta(hours=1)
        threading.Timer(0.02, verifier.stop).start()
        assert verifier.verify(run) == []
        assert len(app.of("get_accessory")) == 1
        chat.open_conversation.assert_not_called()


class TestIntegrationStart:
    def test_the_verifier_runs_from_start_and_stops_with_the_singleton(self, app, homeclaw):
        with settings_patch({"homeclaw.socket": app.path, "homeclaw.verify_after_seconds": 120}), verifier_config():
            homeclaw.start()
            assert homeclaw._verifier.running and homeclaw._verifier.settle_seconds == 120
            homeclaw._on_reset()
        assert homeclaw._verifier is None

    def test_the_config_key_turns_it_off(self, app, homeclaw):
        with settings_patch({"homeclaw.socket": app.path, "homeclaw.verify_automations": False}):
            homeclaw.start()
        assert homeclaw._verifier is None


class TestSunTimes:
    def test_stockholm_midsummer_against_the_published_times(self):
        rise, down = sun_times(date(2024, 6, 21), 59.3293, 18.0686, "Europe/Stockholm")
        assert abs((rise - datetime(2024, 6, 21, 3, 31, tzinfo=TZ)).total_seconds()) < 600
        assert abs((down - datetime(2024, 6, 21, 22, 8, tzinfo=TZ)).total_seconds()) < 600

    def test_stockholm_midwinter(self):
        rise, down = sun_times(date(2024, 12, 21), 59.3293, 18.0686, "Europe/Stockholm")
        assert abs((rise - datetime(2024, 12, 21, 8, 44, tzinfo=TZ)).total_seconds()) < 600
        assert abs((down - datetime(2024, 12, 21, 14, 48, tzinfo=TZ)).total_seconds()) < 600

    def test_a_polar_night_has_no_times(self):
        assert sun_times(date(2024, 12, 21), 69.65, 18.96, "Europe/Oslo") is None
