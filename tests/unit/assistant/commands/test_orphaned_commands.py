"""Orphaned background command handoff: a killed request's in-flight command must
surface its outcome to the next request instead of vanishing (the 2026-08-31
'server restarted mid-checkout' confabulation)."""

from unittest.mock import patch

import pytest

import assistant.commands.OrphanedCommands as oc
from assistant.commands.CommandProcessor import CommandProcessor
from assistant.commands.OrphanedCommands import OrphanedCommandRegistry


@pytest.fixture
def registry():
    fresh = OrphanedCommandRegistry()
    with patch.object(oc, "_registry", fresh):
        yield fresh


@pytest.mark.unit
class TestOrphanedCommandRegistry:

    def test_empty_registry_drains_to_none(self, registry):
        assert registry.drain_note() is None

    def test_pending_reported_once_then_silent_until_resolved(self, registry):
        entry = registry.register_pending("browse_act(55)")

        first = registry.drain_note()
        assert "browse_act(55)" in first
        assert "still running" in first
        assert registry.drain_note() is None  # not re-reported while pending

        registry.resolve(entry, "ERROR: action failed.", is_error=True)
        resolved = registry.drain_note()
        assert "browse_act(55)" in resolved
        assert "ERROR: action failed." in resolved
        assert registry.drain_note() is None  # consumed

    def test_completed_reported_once(self, registry):
        registry.register_completed("google(label printer)", "6 results", is_error=False)
        note = registry.drain_note()
        assert "google(label printer)" in note
        assert "6 results" in note
        assert registry.drain_note() is None

    def test_outcome_truncated(self, registry):
        registry.register_completed("browse_extract", "x" * 5000, is_error=False)
        note = registry.drain_note()
        assert "[truncated]" in note
        assert len(note) < 1000

    def test_entries_expire(self, registry):
        entry = registry.register_pending("browse_act(1)")
        registry.resolve(entry, "done", is_error=False)
        entry.created_at -= oc._ENTRY_TTL + 1
        assert registry.drain_note() is None

    def test_entry_cap(self, registry):
        for i in range(oc._MAX_ENTRIES + 3):
            registry.register_completed(f"cmd({i})", f"result {i}", is_error=False)
        note = registry.drain_note()
        assert note.count("cmd(") == oc._MAX_ENTRIES
        assert f"cmd({oc._MAX_ENTRIES + 2})" in note  # newest kept
        assert "cmd(0)" not in note  # oldest dropped


@pytest.mark.unit
class TestCommandProcessorOrphanHandoff:

    @pytest.fixture
    def processor(self):
        cp = CommandProcessor(test_suite_mode=True)
        yield cp
        cp.is_killed = True

    def test_kill_with_bg_command_registers_pending_and_reply_resolves(self, registry, processor):
        processor.last_dispatched_display = "browse_act(55)"
        processor.command_running_in_background = True
        processor.kill()

        note = registry.drain_note()
        assert "browse_act(55)" in note and "still running" in note

        with patch.object(processor, "_log_command_reply"):
            processor.set_command_reply("ERROR: action failed.", contains_error=True)
        note = registry.drain_note()
        assert "browse_act(55)" in note and "ERROR: action failed." in note

    def test_kill_with_a_batch_in_flight_names_the_count_and_the_last_dispatched(self, registry, processor):
        """Background commands of one reply run in parallel and publish as one reply, so the
        hand-off is one entry saying how many are still running, not just the last one."""
        processor.last_dispatched_display = "google(label printer)"
        processor.command_running_in_background = True
        with patch.object(processor.commands, "background_outstanding", return_value=3):
            processor.kill()

        note = registry.drain_note()
        assert "3 background commands" in note
        assert "the last one dispatched: google(label printer)" in note

    def test_kill_with_one_command_in_flight_names_it_plainly(self, registry, processor):
        processor.last_dispatched_display = "google(label printer)"
        processor.command_running_in_background = True
        with patch.object(processor.commands, "background_outstanding", return_value=1):
            processor.kill()

        note = registry.drain_note()
        assert "google(label printer)" in note
        assert "background commands" not in note

    def test_kill_with_unconsumed_reply_registers_completed(self, registry, processor):
        processor.last_dispatched_display = "browse_open(https://x)"
        processor.command_reply = "URL: https://x\nTitle: X"
        processor.kill()

        note = registry.drain_note()
        assert "browse_open(https://x)" in note
        assert "Title: X" in note

    def test_kill_with_nothing_in_flight_registers_nothing(self, registry, processor):
        processor.kill()
        assert registry.drain_note() is None

    def test_reply_without_kill_registers_nothing(self, registry, processor):
        with patch.object(processor, "_log_command_reply"):
            processor.set_command_reply("normal result")
        assert registry.drain_note() is None

    def test_list_reply_rendered_as_text(self, registry, processor):
        processor.last_dispatched_display = "browse_screenshot()"
        processor.command_running_in_background = True
        processor.kill()
        registry.drain_note()  # consume the pending report

        reply = [{"type": "image", "source": {}}, {"type": "text", "text": "saved to log/x.png"}]
        with patch.object(processor, "_log_command_reply"):
            processor.set_command_reply(reply)
        note = registry.drain_note()
        assert "[image]" in note
        assert "saved to log/x.png" in note
