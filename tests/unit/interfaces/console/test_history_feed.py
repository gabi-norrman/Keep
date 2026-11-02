"""HistoryFeed: the dialog history flows into the chat pane — backfill, live inserts by
timestamp, headers per role — and the console's own turns are adopted, not duplicated."""
import time
from unittest.mock import patch

import pytest

from assistant.commands.StoredReplyReader import StoredReplyReader
from interfaces.chat.RichText import command_line
from interfaces.console.HistoryFeed import HistoryFeed, normalize
from interfaces.console.widgets.ChatPane import (KIND_ASSISTANT, KIND_DIFF, KIND_NOTICE, KIND_SYSTEM, KIND_USER,
                                                  ChatPane)

T0 = 1_700_000_000.0


def entry(role, content, ts, source=None, location=None):
    return {"role": role, "content": content, "timestamp": ts, "source": source, "location": location}


@pytest.fixture
def history():
    from assistant.prompt.MessageHistory import MessageHistory
    with patch.object(MessageHistory, '_load_history'):
        return MessageHistory()


@pytest.fixture
def pane():
    return ChatPane()


@pytest.fixture
def feed(history, pane):
    feed = HistoryFeed(history, pane)
    feed.start()
    return feed


def land(history, *entries):
    """Persisted rows arriving in the history (what add_exchange / the log monitor do)."""
    history.history = list(history.history) + list(entries)
    history._notify_changed()


def shown(pane):
    return [(e.kind, e.header, e.body.text) for e in pane.entries]


def stream_echoes(pane, call):
    """The notice the console's stream shows as it runs `call`."""
    return pane.add_notice(command_line(StoredReplyReader().read(call).before[0]))


def kinds(pane):
    return [e.kind for e in pane.entries]


class TestBackfill:
    def test_every_role_lands_with_its_header(self, history, pane):
        history.history = [
            entry("system", "Server started up successfully.", T0, source="startup"),
            entry("marcus", "Where's my Model X?", T0 + 10, source="VoiceToVoice", location="Kitchen"),
            entry("assistant", "Parked at <b>Kyrkogatan</b>.", T0 + 10.001),
            entry("system", "Charlotte got home.", T0 + 20),
        ]
        HistoryFeed(history, pane).start()
        assert shown(pane) == [
            (KIND_SYSTEM, "System (startup)", "Server started up successfully."),
            (KIND_USER, "Marcus (VoiceToVoice in Kitchen)", "Where's my Model X?"),
            (KIND_ASSISTANT, "Assistant", "Parked at <b>Kyrkogatan</b>."),
            (KIND_SYSTEM, "System", "Charlotte got home."),
        ]
        assert all(not e.body.animated for e in pane.entries)

    def test_stop_unsubscribes(self, history, pane, feed):
        feed.stop()
        land(history, entry("system", "late", T0))
        assert pane.entries == []


    def test_a_command_round_is_drawn_as_the_stream_showed_it(self, history, pane):
        """A notice per command, the prose without the calls, no result row — not the stored text."""
        history.history = [
            entry("marcus", "any news on the ferry?", T0, source="Console"),
            entry("assistant", 'load_toolkit("web")\n\nsave_watch("ferry-strike", """Strike (day 2), "no boats"\n'
                               'until further notice.""", "2026-10-07", "6h")', T0 + 1),
            entry("command", "result:\n# Toolkit: web\nopen_url(url: str)", T0 + 2),
            entry("assistant", 'google("ferry strike")', T0 + 3),
            entry("command", "result:\nFerries are back on Friday.", T0 + 4),
            entry("assistant", "Back on <b>Friday</b>.\n\nremember(Marcus follows the ferry strike)", T0 + 5),
        ]
        HistoryFeed(history, pane).start()
        assert kinds(pane) == [KIND_USER, KIND_NOTICE, KIND_NOTICE, KIND_NOTICE, KIND_ASSISTANT, KIND_NOTICE]
        notices = [e.body.text for e in pane.entries if e.kind == KIND_NOTICE]
        assert "web" in notices[0] and "ferry-strike" in notices[1] and "ferry strike" in notices[2]
        assert not any("no boats" in text or "Toolkit" in text for _, _, text in shown(pane))
        assert shown(pane)[4] == (KIND_ASSISTANT, "Assistant", "Back on <b>Friday</b>.")

    def test_the_clock_note_and_the_opening_brief_are_left_out(self, history, pane):
        history.history = [entry("note", "It is 14:05.", T0),
                           entry("system", "The server has just started…", T0 + 1, source="Console"),
                           entry("assistant", "Hi, I'm Floxy.", T0 + 2)]
        HistoryFeed(history, pane).start()
        assert [text for _, _, text in shown(pane)] == ["Hi, I'm Floxy."]

    def test_a_markdown_assistant_row_is_shown_repaired(self, history, pane):
        history.history = [entry("assistant", "Parked at **Kyrkogatan**, spot `B4`.", T0)]
        HistoryFeed(history, pane).start()
        text = pane.entries[0].body.text
        assert "**" not in text and "`" not in text and "<b>Kyrkogatan</b>" in text


class TestLiveInserts:
    def test_new_rows_are_added_once_in_timestamp_order(self, history, pane, feed):
        land(history, entry("system", "second", T0 + 2))
        land(history, entry("system", "second", T0 + 2), entry("system", "first", T0 + 1))
        assert [e.body.text for e in pane.entries] == ["first", "second"]

    def test_prompt_section_note_is_hidden_by_default(self, history, pane, feed):
        history.add_transient_entry("system", "Prompt section `TeslaInfo` updated:\n-Charlottes <Model 3>",
                                    T0, source="prompt_delta", section="tesla")
        assert pane.entries == []

    def test_prompt_section_note_shows_as_a_diff_entry_when_enabled(self, history, pane, feed):
        with patch('interfaces.console.HistoryFeed.SHOW_PROMPT_DELTAS', True):
            history.add_transient_entry("system", "Prompt section `TeslaInfo` updated:\n-Charlottes <Model 3>",
                                        T0, source="prompt_delta", section="tesla")
            assert shown(pane)[0] == (KIND_DIFF, "System (prompt_delta)",
                                      "Prompt section `TeslaInfo` updated:\n-Charlottes <Model 3>")
            # A later note for the same section is a second entry: the chat is a log of what happened
            history.add_transient_entry("system", "Prompt section `TeslaInfo` updated:\n+Charlottes <Model 3>",
                                        T0 + 1, source="prompt_delta", section="tesla")
            assert len(pane.entries) == 2

    def test_messaging_rows_are_marked_up_for_the_pane(self, history, pane, feed):
        land(history, entry("system", "iMessage received:\nFrom Charlotte to Marcus: hej", T0, source="iMessage"))
        assert shown(pane) == [(KIND_SYSTEM, "System (iMessage)", "<b>Charlotte → Marcus</b> hej")]

class TestAdoption:
    """The console's own turns stream live; their history rows must not show twice."""

    def test_console_turn_is_adopted_and_a_second_reply_bubble_too(self, history, pane, feed):
        pane.add_user("dim the kitchen lamp")
        feed.user_submitted("dim the kitchen lamp")
        feed.turn_started()
        first = pane.add_assistant("Dimming.", final=True)
        echo = stream_echoes(pane, "dim(Köksbord lampa [Kök], 50)")
        second = pane.add_assistant("Done, kitchen table lamp at <b>50</b> percent.", final=True)
        now = time.time()
        land(history, entry("Marcus", "dim the kitchen lamp", now, source="Console"),
             entry("assistant", "dim(Köksbord lampa [Kök], 50)\n\nDimming.", now + 0.001))
        land(history, entry("assistant", "Done, kitchen table lamp at <b>50</b> percent.", now + 5))
        assert pane.entries == [pane.entries[0], first, echo, second]   # nothing added while streaming
        feed.turn_finished()
        assert pane.entries == [pane.entries[0], first, echo, second]

    def test_rows_that_match_no_bubble_are_shown_once_the_reply_is_over(self, history, pane, feed):
        pane.add_user("hello")
        feed.user_submitted("hello")
        feed.turn_started()
        pane.add_assistant("Hi there.", final=True)
        now = time.time()
        land(history, entry("frances", "lights please", now, source="VoiceToVoice", location="Hall"),
             entry("assistant", "Lights on in the hall.", now + 0.001))
        assert [e.kind for e in pane.entries] == [KIND_USER, KIND_ASSISTANT, KIND_USER]  # voice turn shown at once
        feed.turn_finished()
        assert [e.body.text for e in pane.entries] == ["hello", "Hi there.", "lights please", "Lights on in the hall."]

    def test_a_bubble_carrying_a_fallback_notice_adopts_its_row(self, history, pane, feed):
        """The fallback notice is appended to the live reply but kept out of the history,
        so the bubble is longer than its row; it must still count as the same reply."""
        pane.add_user("what other integrations are available")
        feed.user_submitted("what other integrations are available")
        feed.turn_started()
        bubble = pane.add_assistant("Here's what's available.\n\n"
                                    "(Fable refused this request, had to use Opus fallback)", final=True)
        now = time.time()
        land(history, entry("Marcus", "what other integrations are available", now, source="Console"),
             entry("assistant", "Here's what's available.", now + 0.001))
        feed.turn_finished()
        assert pane.entries == [pane.entries[0], bubble]

    def test_a_row_with_inline_code_adopts_its_bubble(self, history, pane, feed):
        """The stream drops the backticks the model wrote; the stored row keeps them."""
        pane.add_user("is it there?")
        feed.user_submitted("is it there?")
        feed.turn_started()
        bubble = pane.add_assistant("Yep, the placeholder  649436  is listed.\n\n"
                                    "(Fable refused this request, had to use Opus fallback)", final=True)
        now = time.time()
        land(history, entry("Marcus", "is it there?", now, source="Console"),
             entry("assistant", "Yep, the placeholder `649436` is listed.", now + 0.001))
        feed.turn_finished()
        assert pane.entries == [pane.entries[0], bubble]

    @pytest.mark.parametrize("live", ["The note pool-temp . The text I saved is in it.",   # every backtick a space
                                      "The note pool-temp. The text I saved is in it."])   # the closing one dropped
    def test_a_span_ending_at_a_full_stop_adopts_its_bubble(self, history, pane, feed, live):
        """#143: the stream drops the model's backticks, the row keeps them, and a span cut
        at a full stop must still find its bubble whatever the stream left where the
        backtick stood — or the reply is drawn a second time under its own header."""
        pane.add_user("why twice?")
        feed.user_submitted("why twice?")
        feed.turn_started()
        echo = stream_echoes(pane, 'load_watch("pool-temp")')
        bubble = pane.add_assistant(live, final=True)
        now = time.time()
        land(history, entry("Marcus", "why twice?", now, source="Console"),
             entry("assistant", 'load_watch("pool-temp")\n\nThe note `pool-temp`. The text I saved is in it.',
                   now + 0.001))
        feed.turn_finished()
        assert pane.entries == [pane.entries[0], echo, bubble]

    def test_a_markdown_row_adopts_the_bubble_the_stream_repaired(self, history, pane, feed):
        """ChatSession turns the reply's **bold** into <b> as it streams; the stored row keeps
        the asterisks, so it must still match instead of showing a raw second copy."""
        pane.add_user("which phones?")
        feed.user_submitted("which phones?")
        feed.turn_started()
        bubble = pane.add_assistant("I'd register the <b>phones</b>:\n- <b>Frances</b> → her iPhone", final=True)
        now = time.time()
        land(history, entry("Marcus", "which phones?", now, source="Console"),
             entry("assistant", "I'd register the **phones**:\n- **Frances** → her iPhone", now + 0.001))
        feed.turn_finished()
        assert pane.entries == [pane.entries[0], bubble]

    def test_a_fallback_round_mixing_a_command_with_its_prose_adopts_its_bubble(self, history, pane, feed):
        """The row keeps the command line the stream stripped, the bubble the notice the
        history dropped: neither text contains the other, both must be removed first."""
        pane.add_user("so do the cost tracking analysis please")
        feed.user_submitted("so do the cost tracking analysis please")
        feed.turn_started()
        echo = stream_echoes(pane, 'self_analysis("Cost tracking: where are usage stats stored?")')
        bubble = pane.add_assistant("Running. Claude Code will report back when it's done.\n\n"
                                    "(Fable refused this request, had to use Opus fallback)", final=True)
        now = time.time()
        land(history, entry("Marcus", "so do the cost tracking analysis please", now, source="Console"),
             entry("assistant", "`self_analysis(\"Cost tracking: where are usage stats stored?\")`\n\n"
                                "Running. Claude Code will report back when it's done.", now + 0.001))
        feed.turn_finished()
        assert pane.entries == [pane.entries[0], echo, bubble]

    def test_a_command_round_the_stream_showed_adds_nothing(self, history, pane, feed):
        feed.turn_started()
        echoes = [stream_echoes(pane, call) for call in ("`list_integrations()`", 'google("Mac Studio M5 news")',
                                                         "<code>open_url(https://example.com/a)</code>")]
        now = time.time()
        land(history, entry("assistant", "`list_integrations()`", now),
             entry("assistant", 'google("Mac Studio M5 news")\n<code>open_url(https://example.com/a)</code>', now + 1))
        feed.turn_finished()
        assert pane.entries == echoes

    def test_another_channels_command_round_shows_its_notices(self, history, pane, feed):
        """No stream drew it here, so the row is all the console has: a notice per command."""
        now = time.time()
        land(history, entry("assistant", 'search_x("M5 Ultra Mac Studio")\ngoogle("Mac Studio M5 news")', now),
             entry("assistant", "dim(Köksbord lampa [Kök], 50)\n\nDimming.", now + 1))
        assert kinds(pane) == [KIND_NOTICE, KIND_NOTICE, KIND_NOTICE, KIND_ASSISTANT]
        assert pane.entries[3].body.text == "Dimming."

    def test_a_notice_stands_for_one_command_only(self, history, pane, feed):
        stream_echoes(pane, 'google("Mac Studio M5 news")')
        now = time.time()
        land(history, entry("assistant", 'google("Mac Studio M5 news")', now))
        land(history, entry("assistant", 'google("Mac Studio M5 news")', now + 1))
        assert kinds(pane) == [KIND_NOTICE, KIND_NOTICE]

    def test_the_calls_are_shown_as_written_when_the_flag_is_on(self, history, pane, feed):
        with patch('interfaces.console.HistoryFeed.SHOW_COMMAND_ROUNDS', True):
            land(history, entry("assistant", 'google("Mac Studio M5 news")', time.time()))
        assert [e.body.text for e in pane.entries] == ['google("Mac Studio M5 news")']

    def test_notifier_broadcast_is_adopted_by_its_history_row(self, history, pane, feed):
        pane.add_notice("Rain in <b>10 minutes</b> — closing the parasol.")
        land(history, entry("assistant", "Rain in 10 minutes — closing the parasol.", time.time()))
        assert [e.kind for e in pane.entries] == [KIND_NOTICE]

    def test_a_bubble_adopts_only_one_row(self, history, pane, feed):
        pane.add_assistant("Done.", final=True)
        now = time.time()
        land(history, entry("assistant", "Done.", now))
        land(history, entry("assistant", "Done.", now + 1))
        assert [e.kind for e in pane.entries] == [KIND_ASSISTANT, KIND_ASSISTANT]

    def test_old_bubbles_are_no_longer_candidates(self, history, pane, feed):
        old = pane.add_assistant("Yesterday's answer.", final=True)
        old.timestamp = time.time() - HistoryFeed.MATCH_WINDOW - 1
        land(history, entry("assistant", "Yesterday's answer.", time.time()))
        assert len(pane.entries) == 2

    def test_console_user_row_without_a_pending_turn_is_shown(self, history, pane, feed):
        land(history, entry("Marcus", "typed in another console", time.time(), source="Console"))
        assert [e.kind for e in pane.entries] == [KIND_USER]

    def test_two_overlapping_turns_settle_together(self, history, pane, feed):
        feed.turn_started()
        feed.turn_started()
        land(history, entry("assistant", "reply", time.time()))
        feed.turn_finished()
        assert pane.entries == []
        feed.turn_finished()
        assert [e.body.text for e in pane.entries] == ["reply"]

    def test_a_turn_the_server_opened_hides_its_brief(self, history, pane, feed):
        # The first-run brief: no user line was typed, the reply streams live, and the
        # history row of the brief itself (role "system", source Console) is not shown.
        feed.turn_started()
        bubble = pane.add_assistant("Hi, I'm Floxy. Let's start with iMessage.", final=True)
        now = time.time()
        land(history, entry("system", "The server has just started…", now, source="Console"),
             entry("assistant", "Hi, I'm Floxy. Let's start with iMessage.", now + 0.001))
        feed.turn_finished()
        assert pane.entries == [bubble]   # the reply row adopted the live bubble, the brief stays hidden

    def test_the_brief_is_shown_when_the_flag_is_on(self, history, pane, feed):
        with patch('interfaces.console.HistoryFeed.SHOW_OPENING_BRIEFS', True):
            land(history, entry("system", "The server has just started…", time.time(), source="Console"))
        assert shown(pane) == [(KIND_SYSTEM, "System (Console)", "The server has just started…")]

class TestIncomingRequests:
    """A voice request is drawn as a system row as it reaches the assistant; the chat
    channels' turns stream live already, so theirs are not."""

    @staticmethod
    def request(source, text, user=None, location=None):
        from assistant.AssistantRequest import RequestContext
        return RequestContext(source=source, user=user, user_input=text, location=location)

    def test_voice_request_lands_at_once_under_a_system_header(self, pane, feed):
        feed.on_request_received(self.request("VoiceToVoice", "turn on the kitchen", user="Marcus",
                                              location="Kitchen"))
        assert shown(pane) == [(KIND_SYSTEM, "System (VoiceToVoice in Kitchen)",
                                "Incoming voice request from Marcus: \u201cturn on the kitchen\u201d")]
        assert pane.entries[0].detail is None

    def test_mic_engine_and_confidence_name_the_origin(self, pane, feed):
        from dataclasses import replace
        ctx = replace(self.request("VoiceToVoice", "turn on the kitchen"), engine="whisper")
        feed.on_request_received(ctx, mic="kitchen", confidence=0.9)
        assert pane.entries[0].body.text == \
            "Incoming voice request (kitchen mic, whisper, c0.90): \u201cturn on the kitchen\u201d"

    def test_a_rejected_transcription_is_shown_with_the_reason_as_detail(self, pane, feed):
        feed.on_request_received(self.request("VoiceToVoice", "turn on the kitchen"), mic="hall", confidence=0.7,
                                 rejected="empty/duplicate/non-best transcription")
        assert pane.entries[0].body.text == "Incoming voice request (hall mic, c0.70): \u201cturn on the kitchen\u201d"
        assert pane.entries[0].detail == "rejected: empty/duplicate/non-best transcription"

    def test_unidentified_speaker_is_left_out(self, pane, feed):
        feed.on_request_received(self.request("VoiceToVoice", "what time is it"))
        assert shown(pane) == [(KIND_SYSTEM, "System (VoiceToVoice)",
                                "Incoming voice request: \u201cwhat time is it\u201d")]

    def test_markup_in_the_query_is_escaped(self, pane, feed):
        feed.on_request_received(self.request("VoiceToVoice", "is 3 < 4 & 5 > 2"))
        assert pane.entries[0].body.text == "Incoming voice request: \u201cis 3 &lt; 4 &amp; 5 &gt; 2\u201d"

    def test_chat_channel_requests_are_not_shown(self, pane, feed):
        feed.on_request_received(self.request("Console", "hello"))
        feed.on_request_received(self.request("Telegram", "hello"))
        assert shown(pane) == []

    def test_the_user_row_still_lands_after_the_exchange(self, history, pane, feed):
        feed.on_request_received(self.request("VoiceToVoice", "turn on the kitchen", user="Marcus"))
        land(history, entry("Marcus", "turn on the kitchen", time.time() + 1, source="VoiceToVoice"))
        assert [k for k, _, _ in shown(pane)] == [KIND_SYSTEM, KIND_USER]


def test_normalize_ignores_tags_backticks_and_spacing():
    assert normalize("  Done,\n<b>kitchen</b>   lamp ") == normalize("Done, `kitchen` lamp") == "Done,kitchenlamp"


class TestWipe:
    def wipe(self, history, remaining):
        """What MessageHistory.wipe_today leaves behind."""
        history.history = list(remaining)
        history.transient_entries = []
        history.wipes += 1
        history._notify_changed()

    def test_the_pane_starts_over_from_what_remains(self, history, pane, feed):
        yesterday = entry("user", "old", T0 - 86400)
        land(history, yesterday, entry("user", "today", T0), entry("assistant", "reply", T0 + 1))
        assert len(pane.entries) == 3
        self.wipe(history, [yesterday])
        assert [e.body.text for e in pane.entries] == ["old"]

    def test_rows_landing_after_a_wipe_are_shown_again(self, history, pane, feed):
        land(history, entry("user", "hi", T0))
        self.wipe(history, [])
        assert pane.entries == []
        land(history, entry("user", "hi", T0))   # same key as the wiped row
        assert [e.body.text for e in pane.entries] == ["hi"]

    def test_a_feed_started_after_a_wipe_does_not_rebuild(self, history, pane):
        history.wipes = 2
        feed = HistoryFeed(history, pane)
        feed.start()
        land(history, entry("user", "hi", T0))
        assert [e.body.text for e in pane.entries] == ["hi"]


class TestCommandResultRows:
    def test_hidden_by_default(self, history, pane, feed):
        land(history, entry("command", "result: \niMessage — disabled", T0 + 30))
        assert shown(pane) == []

    def test_shown_when_enabled(self, history, pane, feed):
        with patch('interfaces.console.HistoryFeed.SHOW_COMMAND_RESULTS', True):
            land(history, entry("command", "result: \niMessage — disabled", T0 + 30))
        assert shown(pane) == [(KIND_SYSTEM, "Command result", "result: \niMessage — disabled")]


class TestClockNoteRows:
    def test_a_byte_prefix_requests_clock_note_is_hidden_but_its_exchange_shows(self, history, pane, feed):
        land(history,
             entry("note", "(Current time: 14:40, Saturday, 2025-03-15)", T0 + 29.999),
             entry("marcus", "what's on today?", T0 + 30, source="Console"),
             entry("assistant", "Nothing booked.", T0 + 30.001))
        assert [(kind, text) for kind, _, text in shown(pane)] == [(KIND_USER, "what's on today?"),
                                                                    (KIND_ASSISTANT, "Nothing booked.")]
