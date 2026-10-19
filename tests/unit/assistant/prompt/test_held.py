"""Held.py: readings rounded to a step and held until a full step away, states shown once they
have lasted — the hysteresis every sensor-rendering section leans on so a hovering reading
never rewrites a prompt line."""
import pytest

from assistant.prompt.Held import HeldStates, HeldValues


@pytest.mark.unit
class TestHeldValues:
    def test_a_reading_hovering_on_a_boundary_keeps_its_shown_value(self):
        held = HeldValues(1)
        assert [held.show("cabin", t) for t in (20.4, 20.6, 20.4, 20.9)] == [20, 20, 20, 20]

    def test_a_full_step_away_moves_the_shown_value(self):
        held = HeldValues(1)
        assert [held.show("cabin", t) for t in (20.4, 21.0, 21.9, 22.4)] == [20, 21, 21, 22]

    def test_moving_down_takes_a_full_step_too(self):
        held = HeldValues(5)
        assert [held.show("charge", p) for p in (88, 91, 94, 95, 91, 90)] == [90, 90, 90, 95, 95, 90]

    def test_an_integer_step_gives_integers(self):
        shown = HeldValues(50).show("range", 412.4)
        assert shown == 400 and isinstance(shown, int)

    def test_a_fractional_step_gives_a_clean_float(self):
        held = HeldValues(0.5)
        assert held.show("t", 21.26) == 21.5
        assert held.show("t", 21.49) == 21.5
        assert held.show("t", 21.76) == 21.5, "a quarter of a degree is not a full step"
        assert held.show("t", 22.0) == 22.0
        assert HeldValues(0.5).show("u", 0.1 + 0.2) == 0.5

    def test_a_lost_reading_forgets_the_key(self):
        held = HeldValues(1)
        held.show("cabin", 25.4)
        assert held.show("cabin", None) is None
        assert held.show("cabin", 25.9) == 26, "a fresh start rounds the first reading, no hold"

    def test_forget_starts_the_key_afresh(self):
        held = HeldValues(1)
        held.show("cabin", 25.4)
        held.forget("cabin")
        assert held.show("cabin", 25.9) == 26

    def test_keys_are_held_on_their_own(self):
        held = HeldValues(1)
        held.show("a", 25.4)
        assert held.show("b", 25.9) == 26
        assert held.show("a", 25.9) == 25


@pytest.mark.unit
class TestHeldStates:
    def test_the_first_state_shows_at_once(self):
        assert HeldStates(600).show("compressor", True, now=1000.0) is True

    def test_a_flip_shorter_than_the_hold_never_shows(self):
        held = HeldStates(600)
        held.show("compressor", True, now=1000.0)
        assert held.show("compressor", False, now=1010.0) is True
        assert held.show("compressor", False, now=1030.0) is True
        assert held.show("compressor", True, now=1040.0) is True

    def test_a_state_that_lasts_the_hold_shows(self):
        held = HeldStates(600)
        held.show("compressor", True, now=1000.0)
        held.show("compressor", False, now=1100.0)
        assert held.show("compressor", False, now=1699.0) is True
        assert held.show("compressor", False, now=1700.0) is False

    def test_a_different_candidate_restarts_the_clock(self):
        held = HeldStates(600)
        held.show("mode", "heat", now=0.0)
        held.show("mode", "cool", now=100.0)
        held.show("mode", "off", now=500.0)
        assert held.show("mode", "off", now=1000.0) == "heat", "off has only lasted 500 s"
        assert held.show("mode", "off", now=1100.0) == "off"

    def test_going_back_to_the_shown_state_drops_the_candidate(self):
        held = HeldStates(600)
        held.show("compressor", True, now=0.0)
        held.show("compressor", False, now=100.0)
        held.show("compressor", True, now=200.0)
        held.show("compressor", False, now=300.0)
        assert held.show("compressor", False, now=800.0) is True, "the second rest started at 300, not 100"
        assert held.show("compressor", False, now=900.0) is False

    def test_a_lost_state_forgets_the_key(self):
        held = HeldStates(600)
        held.show("compressor", True, now=0.0)
        assert held.show("compressor", None, now=10.0) is None
        assert held.show("compressor", False, now=20.0) is False

    def test_keys_are_held_on_their_own(self):
        held = HeldStates(600)
        held.show("a", True, now=0.0)
        held.show("b", False, now=0.0)
        assert held.show("a", False, now=10.0) is True
        assert held.show("b", False, now=10.0) is False
