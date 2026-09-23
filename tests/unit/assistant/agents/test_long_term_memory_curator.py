import threading
from datetime import date, datetime, timedelta
from unittest.mock import Mock, call, patch

import pytest

from assistant import MemoryFile
from assistant.agents.LongTermMemoryCurator import HISTORY_SOURCE, MEMORY_WORD_LIMIT, LongTermMemoryCurator
from common.FloxyDatabase import NO_INTERACTIONS


class TestLongTermMemoryCurator:
    def test_default_llm_created_with_prompt_cache_disabled(self):
        # The nightly curation call is one-shot: a prompt cache written for it is
        # never read again, so the curator must opt out of caching (the cache-write
        # surcharge on ~128k input tokens would be pure cost).
        with patch('assistant.agents.LongTermMemoryCurator.parse_llm_config',
                   return_value=('ClaudeLLM', 'some-model')), \
             patch('assistant.agents.LongTermMemoryCurator.create_llm') as mock_create:
            curator = LongTermMemoryCurator(test_mode=True)
        mock_create.assert_called_once_with('ClaudeLLM', 'some-model', prompt_cache=False)
        mock_create.return_value.initialize.assert_called_once()
        assert curator.llm is mock_create.return_value

    def test_curate_memory_with_data_passes_data_to_llm(self):
        mock_llm = Mock()
        mock_llm.think.return_value = "# Memory\nUpdated"
        curator = LongTermMemoryCurator(llm=mock_llm, test_mode=True)

        curator.curate_memory_with_data(
            interaction_history="test history",
            calendar_data="",
            current_memory="test memory"
        )

        call_args = mock_llm.think.call_args
        prompt = call_args[1].get('system_message', call_args[0][0] if call_args[0] else '')
        assert "test history" in prompt
        assert "test memory" in prompt

    def test_extract_memory_from_markdown_code_block(self):
        response = "Here is the memory:\n```markdown\n# Memory\nContent here\n```\nDone."
        result = LongTermMemoryCurator._extract_memory_content(response)
        assert result == "# Memory\nContent here"

    def test_extract_memory_from_plain_code_block(self):
        response = "```\n# Memory\nPlain block\n```"
        result = LongTermMemoryCurator._extract_memory_content(response)
        assert result == "# Memory\nPlain block"

    def test_extract_memory_from_plain_text(self):
        response = "# Memory\nJust plain text"
        result = LongTermMemoryCurator._extract_memory_content(response)
        assert result == "# Memory\nJust plain text"

    def test_validation_rejects_short_memory(self):
        mock_llm = Mock()
        # Return suspiciously short memory (less than 30% of current)
        mock_llm.think.return_value = "x"
        curator = LongTermMemoryCurator(llm=mock_llm, test_mode=True)

        current = "A" * 1000  # 1000 chars
        memory, _ = curator.curate_memory_with_data("history", "calendar", current)
        # Should keep old memory
        assert memory == current

    def test_reply_cut_off_at_max_tokens_keeps_the_previous_bank(self):
        """A truncated bank is a bank missing its tail; saving it forgets every section
        past the cut for good, so the old one stays."""
        mock_llm = Mock()
        mock_llm.think.return_value = "# Memory\n" + "A" * 900
        mock_llm.last_stop_reason = 'max_tokens'
        curator = LongTermMemoryCurator(llm=mock_llm, test_mode=True)

        current = "B" * 1000
        with patch('assistant.agents.LongTermMemoryCurator.error') as mock_error:
            memory, metadata = curator.curate_memory_with_data("history", "calendar", current)

        assert memory == current
        assert metadata['truncated'] is True
        assert "cut short" in mock_error.call_args_list[0].args[0]
        assert "unchanged" in LongTermMemoryCurator.completion_note(metadata)

    def test_finished_reply_is_not_flagged_as_truncated(self):
        mock_llm = Mock()
        mock_llm.think.return_value = "# Memory\n" + "A" * 900
        mock_llm.last_stop_reason = 'end_turn'
        curator = LongTermMemoryCurator(llm=mock_llm, test_mode=True)

        memory, metadata = curator.curate_memory_with_data("history", "calendar", "B" * 1000)
        assert memory.startswith("# Memory")
        assert metadata['truncated'] is False
        assert "memory updated" in LongTermMemoryCurator.completion_note(metadata)

    def test_validation_accepts_normal_length_memory(self):
        mock_llm = Mock()
        new_content = "# Memory\n" + "A" * 500
        mock_llm.think.return_value = new_content
        curator = LongTermMemoryCurator(llm=mock_llm, test_mode=True)

        current = "B" * 600
        memory, _ = curator.curate_memory_with_data("history", "calendar", current)
        assert memory == new_content

    def test_save_memory_writes_file(self, tmp_path):
        curator = LongTermMemoryCurator(llm=Mock(), test_mode=True,
                                         memory_file_path=str(tmp_path / "memory.md"))
        path = curator.save_memory("# Test Memory\nContent")
        assert (tmp_path / "memory.md").read_text() == "# Test Memory\nContent"
        assert path == str(tmp_path / "memory.md")

    def test_load_memory_returns_empty_for_missing(self, tmp_path):
        curator = LongTermMemoryCurator(llm=Mock(), test_mode=True,
                                         memory_file_path=str(tmp_path / "nonexistent.md"))
        assert curator._load_memory() == ""

class TestCurationPeriod:
    """curate_memory takes an explicit period so a first-run curation can cover
    "since 14:03" instead of the nightly two-day lookback."""

    def _curator(self, history="(No interactions in this period)"):
        llm = Mock()
        llm.think.return_value = "# Memory\nA bank long enough to pass the size check for sure"
        curator = LongTermMemoryCurator(llm=llm, test_mode=True)
        curator.floxy_db = Mock()
        curator.floxy_db.get_interaction_history.return_value = history
        curator._load_memory = Mock(return_value="# Memory\nA bank long enough")
        return curator

    def test_explicit_datetimes_reach_the_history_query_untouched(self):
        curator = self._curator()
        start, end = datetime(2026, 9, 10, 14, 3), datetime(2026, 9, 10, 16, 30)
        with patch('assistant.agents.LongTermMemoryCurator.CalDAVIntegration.fetch_calendar_events',
                   return_value="(No upcoming events)"):
            _, metadata = curator.curate_memory(start, end)
        curator.floxy_db.get_interaction_history.assert_called_once_with(start, end)
        assert metadata['period_start'] == "2026-09-10T14:03"
        assert metadata['period_end'] == "2026-09-10T16:30"

    def test_period_is_told_to_the_llm(self):
        curator = self._curator(history="[2026-09-10 14:10:00] User: hi")
        with patch('assistant.agents.LongTermMemoryCurator.CalDAVIntegration.fetch_calendar_events',
                   return_value="(No upcoming events)"):
            curator.curate_memory(datetime(2026, 9, 10, 14, 3), datetime(2026, 9, 10, 16, 30))
        prompt = curator.llm.think.call_args.kwargs['system_message']
        assert "This run covers 2026-09-10 14:03 to 2026-09-10 16:30." in prompt
        assert "{PERIOD}" not in prompt

    @pytest.mark.parametrize("voice_enabled", [True, False])
    def test_stt_note_only_when_the_voice_interface_is_enabled(self, voice_enabled):
        config = Mock()
        config.get.side_effect = lambda key, default=None: voice_enabled if key == 'interfaces.voice.enabled' else default
        curator = LongTermMemoryCurator(llm=Mock(), test_mode=True)
        curator.llm.think.return_value = "# Memory"
        with patch('assistant.agents.LongTermMemoryCurator.ConfigManager.get_instance', return_value=config):
            curator.curate_memory_with_data(interaction_history="", calendar_data="", current_memory="")
        prompt = curator.llm.think.call_args.kwargs['system_message']
        assert ("misheard phrases" in prompt) is voice_enabled
        assert "{STT_NOTE}" not in prompt

    def test_dates_mean_midnight(self):
        curator = self._curator()
        with patch('assistant.agents.LongTermMemoryCurator.CalDAVIntegration.fetch_calendar_events',
                   return_value="(No upcoming events)"):
            curator.curate_memory(date(2026, 9, 8), date(2026, 9, 10))
        curator.floxy_db.get_interaction_history.assert_called_once_with(datetime(2026, 9, 8), datetime(2026, 9, 10))

    def test_default_period_is_lookback_midnight_until_now(self):
        curator = self._curator()
        with patch('assistant.agents.LongTermMemoryCurator.CalDAVIntegration.fetch_calendar_events',
                   return_value="(No upcoming events)"):
            before = datetime.now()
            curator.curate_memory(lookback_days=2)
            after = datetime.now()
        start, end = curator.floxy_db.get_interaction_history.call_args.args
        assert start == datetime.combine(before.date() - timedelta(days=2), datetime.min.time())
        assert before <= end <= after

    def test_empty_period_is_refused(self):
        curator = self._curator()
        with pytest.raises(ValueError):
            curator.curate_memory(datetime(2026, 9, 10, 16), datetime(2026, 9, 10, 14))

    def test_curate_with_data_has_no_period_placeholder_left(self):
        curator = self._curator()
        curator.curate_memory_with_data("history", "calendar", "# Memory\nA bank long enough")
        prompt = curator.llm.think.call_args.kwargs['system_message']
        assert "{PERIOD}" not in prompt
        assert "the recent interactions below" in prompt


class TestMemoryWordCount:
    """The LLM cannot count the words of a 40 KB bank, so the prompt states the measured count."""

    def _curator(self):
        llm = Mock()
        llm.think.return_value = "# Memory\nUpdated bank with enough words to pass the shrink check"
        return LongTermMemoryCurator(llm=llm, test_mode=True)

    def test_prompt_states_measured_word_count_and_limit(self):
        curator = self._curator()
        memory = "# Memory\n" + " ".join(["word"] * 499)
        curator.curate_memory_with_data("history", "", memory)
        prompt = curator.llm.think.call_args.kwargs['system_message']
        assert "The current memory bank is 501 words" in prompt
        assert f"no more than {MEMORY_WORD_LIMIT} words" in prompt
        assert "{MEMORY_SIZE}" not in prompt and "{WORD_LIMIT}" not in prompt

    def test_under_limit_reports_headroom(self):
        note = LongTermMemoryCurator.memory_size_note(MEMORY_WORD_LIMIT - 900)
        assert "900 words of headroom" in note
        assert "OVER" not in note

    def test_over_limit_demands_a_cut_of_the_excess(self):
        note = LongTermMemoryCurator.memory_size_note(MEMORY_WORD_LIMIT + 2162)
        assert "OVER the limit by 2162 words" in note
        assert "at least 2162 words shorter" in note


class TestRun:
    def test_run_curates_saves_and_logs(self, tmp_path):
        curator = Mock()
        curator.curate_memory.return_value = ("new bank", {'interaction_count': 3, 'new_memory_size': 8})
        metadata = LongTermMemoryCurator.run(datetime(2026, 9, 10, 14), datetime(2026, 9, 10, 16), curator=curator)
        curator.curate_memory.assert_called_once_with(datetime(2026, 9, 10, 14), datetime(2026, 9, 10, 16))
        curator.save_memory.assert_called_once_with("new bank", metadata)
        curator.save_debug_log.assert_called_once_with("new bank", metadata)

    def test_run_records_the_completion_in_the_history(self):
        curator = Mock()
        curator.curate_memory.return_value = ("bank", {
            'interaction_count': 17, 'new_memory_size': 4,
            'period_start': '2026-09-08T00:00', 'period_end': '2026-09-10T04:00'})
        history = Mock()
        LongTermMemoryCurator.run(curator=curator, history=history)
        history.add_context_message.assert_called_once()
        role, content, _ = history.add_context_message.call_args.args
        assert role == "system" and history.add_context_message.call_args.kwargs['source'] == HISTORY_SOURCE
        assert content == ("Long term memory curation complete - memory updated "
                           "(17 interactions, 2026-09-08T00:00 .. 2026-09-10T04:00).")

    def test_second_run_while_one_is_in_progress_is_skipped(self):
        started, release = threading.Event(), threading.Event()
        slow = Mock()

        def curate(*_):
            started.set()
            release.wait(5)
            return "bank", {'interaction_count': 0, 'new_memory_size': 4}
        slow.curate_memory.side_effect = curate
        first = threading.Thread(target=LongTermMemoryCurator.run, kwargs={'curator': slow})
        first.start()
        assert started.wait(5)
        try:
            assert LongTermMemoryCurator.run(curator=Mock()) is None
        finally:
            release.set()
            first.join(5)
        assert slow.save_memory.called

    def test_lock_is_released_after_a_failure(self):
        failing = Mock()
        failing.curate_memory.side_effect = RuntimeError("boom")
        with pytest.raises(RuntimeError):
            LongTermMemoryCurator.run(curator=failing)
        ok = Mock()
        ok.curate_memory.return_value = ("bank", {'interaction_count': 0, 'new_memory_size': 4})
        assert LongTermMemoryCurator.run(curator=ok) is not None


class TestSaveMemoryCarriesOverAppends:
    """A `remember` that lands while the LLM is thinking must survive the rewrite."""

    def test_appended_note_is_kept(self, tmp_path):
        memory_file = tmp_path / "memory.md"
        memory_file.write_text("# Bank\nold\n")
        curator = LongTermMemoryCurator(llm=Mock(), test_mode=True, memory_file_path=str(memory_file))
        loaded = curator._load_memory()
        MemoryFile.append("Marcus likes tea", str(memory_file))
        curator.save_memory("# Bank\ncurated\n", {'old_memory': loaded})
        assert memory_file.read_text() == "# Bank\ncurated\n\nMarcus likes tea\n"

    def test_unchanged_file_is_replaced_verbatim(self, tmp_path):
        memory_file = tmp_path / "memory.md"
        memory_file.write_text("# Bank\nold\n")
        curator = LongTermMemoryCurator(llm=Mock(), test_mode=True, memory_file_path=str(memory_file))
        curator.save_memory("# Bank\ncurated\n", {'old_memory': curator._load_memory()})
        assert memory_file.read_text() == "# Bank\ncurated\n"

    def test_rewritten_file_is_replaced_by_the_curated_bank(self, tmp_path):
        memory_file = tmp_path / "memory.md"
        memory_file.write_text("# Bank\nold\n")
        curator = LongTermMemoryCurator(llm=Mock(), test_mode=True, memory_file_path=str(memory_file))
        loaded = curator._load_memory()
        memory_file.write_text("# Something else entirely\n")
        curator.save_memory("# Bank\ncurated\n", {'old_memory': loaded})
        assert memory_file.read_text() == "# Bank\ncurated\n"

    def test_no_temp_file_is_left_behind(self, tmp_path):
        memory_file = tmp_path / "memory.md"
        curator = LongTermMemoryCurator(llm=Mock(), test_mode=True, memory_file_path=str(memory_file))
        curator.save_memory("# Bank\n")
        assert sorted(p.name for p in tmp_path.iterdir()) == ["memory.md"]


# --- chunked spans (run_span) --------------------------------------------------------------

class TestChunks:
    """`chunks` cuts a span of days into CURATION_CHUNK_DAYS-day periods, day-aligned with
    exclusive ends, the last one clamped to now."""

    NOW = datetime(2026, 9, 14, 11, 0)

    def _chunks(self, start, end, now=None):
        return LongTermMemoryCurator.chunks(start, end, now=now or self.NOW)

    def test_one_day_is_one_chunk(self):
        assert self._chunks(date(2026, 8, 1), date(2026, 8, 1)) == \
            [(datetime(2026, 8, 1), datetime(2026, 8, 2))]

    def test_exactly_four_days_is_one_chunk(self):
        assert self._chunks(date(2026, 8, 1), date(2026, 8, 4)) == \
            [(datetime(2026, 8, 1), datetime(2026, 8, 5))]

    def test_five_days_is_four_plus_one(self):
        assert self._chunks(date(2026, 8, 1), date(2026, 8, 5)) == [
            (datetime(2026, 8, 1), datetime(2026, 8, 5)),
            (datetime(2026, 8, 5), datetime(2026, 8, 6)),
        ]

    def test_nine_days_is_four_four_one(self):
        assert self._chunks(date(2026, 8, 1), date(2026, 8, 9)) == [
            (datetime(2026, 8, 1), datetime(2026, 8, 5)),
            (datetime(2026, 8, 5), datetime(2026, 8, 9)),
            (datetime(2026, 8, 9), datetime(2026, 8, 10)),
        ]

    def test_a_span_ending_today_stops_now(self):
        periods = self._chunks(date(2026, 9, 12), date(2026, 9, 14))
        assert periods == [(datetime(2026, 9, 12), self.NOW)]

    def test_an_end_in_the_future_is_clamped_to_today(self):
        periods = self._chunks(date(2026, 9, 13), date(2026, 9, 30))
        assert periods == [(datetime(2026, 9, 13), self.NOW)]

    def test_end_before_start_is_refused(self):
        with pytest.raises(ValueError):
            self._chunks(date(2026, 8, 5), date(2026, 8, 1))
        with pytest.raises(ValueError):   # start in the future, once the end is clamped
            self._chunks(date(2026, 9, 20), date(2026, 9, 25))

def _span_curator(metadata_for=None):
    """A curator whose curate_memory answers ("bank", metadata) per call; `metadata_for(start)`
    picks the metadata by the chunk's start (default: a normal curated chunk)."""
    curator = Mock()

    def curate(period_start, period_end):
        base = {'interaction_count': 2, 'new_memory_size': 8,
                'period_start': period_start.isoformat(timespec='minutes'),
                'period_end': period_end.isoformat(timespec='minutes')}
        extra = metadata_for(period_start) if metadata_for else {}
        if isinstance(extra, Exception):
            raise extra
        return "bank", {**base, **extra}
    curator.curate_memory.side_effect = curate
    return curator


class TestRunSpan:
    START, END = date(2026, 8, 1), date(2026, 8, 9)     # three chunks: 4 + 4 + 1 days

    def test_one_curation_per_chunk_in_order(self):
        curator = _span_curator()
        summary = LongTermMemoryCurator.run_span(self.START, self.END, curator=curator)
        assert [c.args for c in curator.curate_memory.call_args_list] == \
            LongTermMemoryCurator.chunks(self.START, self.END)
        assert curator.save_memory.call_count == 3 and curator.save_debug_log.call_count == 3
        assert summary == {'chunks': 3, 'curated': 3, 'empty': 0, 'failed': 0, 'aborted': False, 'interaction_count': 6,
                           'period_start': '2026-08-01T00:00', 'period_end': '2026-08-10T00:00'}

    def test_empty_chunk_is_counted_and_not_saved(self):
        curator = _span_curator(lambda start: {'empty': True, 'interaction_count': 0}
                                if start == datetime(2026, 8, 5) else {})
        summary = LongTermMemoryCurator.run_span(self.START, self.END, curator=curator)
        assert summary['curated'] == 2 and summary['empty'] == 1 and summary['interaction_count'] == 4
        assert curator.save_memory.call_count == 2

    def test_failed_chunk_does_not_stop_the_span(self):
        curator = _span_curator(lambda start: RuntimeError("boom") if start == datetime(2026, 8, 1) else {})
        summary = LongTermMemoryCurator.run_span(self.START, self.END, curator=curator)
        assert summary['failed'] == 1 and summary['curated'] == 2
        assert curator.curate_memory.call_count == 3

    def test_truncated_chunk_counts_as_failed(self):
        curator = _span_curator(lambda start: {'truncated': True} if start == datetime(2026, 8, 9) else {})
        summary = LongTermMemoryCurator.run_span(self.START, self.END, curator=curator)
        assert summary['failed'] == 1 and summary['curated'] == 2
        assert curator.save_memory.call_count == 3   # a truncated reply saves the unchanged bank, as run does

    def test_on_chunk_is_called_one_based_with_none_on_failure(self):
        curator = _span_curator(lambda start: RuntimeError("boom") if start == datetime(2026, 8, 5) else {})
        seen = []
        LongTermMemoryCurator.run_span(self.START, self.END, curator=curator,
                                       on_chunk=lambda index, total, metadata: seen.append((index, total, metadata)))
        assert [(i, t) for i, t, _ in seen] == [(1, 3), (2, 3), (3, 3)]
        assert seen[1][2] is None
        assert seen[0][2]['period_start'] == '2026-08-01T00:00' and seen[2][2]['period_start'] == '2026-08-09T00:00'

    def test_first_chunk_resumes_a_span(self):
        curator = _span_curator()
        seen = []
        summary = LongTermMemoryCurator.run_span(self.START, self.END, curator=curator, first_chunk=2,
                                                 on_chunk=lambda i, t, m: seen.append((i, t)))
        assert curator.curate_memory.call_args_list == [call(datetime(2026, 8, 9), datetime(2026, 8, 10))]
        assert seen == [(3, 3)]
        assert summary['chunks'] == 3 and summary['curated'] == 1

    def test_on_chunk_returning_false_stops_the_span_without_a_row(self):
        curator = _span_curator()
        history = Mock()
        summary = LongTermMemoryCurator.run_span(self.START, self.END, curator=curator, history=history,
                                                 on_chunk=lambda i, t, m: i < 2)
        assert curator.curate_memory.call_count == 2
        assert summary['aborted'] is True and summary['curated'] == 2 and summary['chunks'] == 3
        history.add_context_message.assert_not_called()

    def test_on_chunk_returning_none_lets_the_span_run(self):
        curator = _span_curator()
        summary = LongTermMemoryCurator.run_span(self.START, self.END, curator=curator, on_chunk=lambda i, t, m: None)
        assert curator.curate_memory.call_count == 3 and summary['aborted'] is False

    def test_multi_chunk_span_records_one_span_row_and_no_chunk_rows(self):
        history = Mock()
        LongTermMemoryCurator.run_span(self.START, self.END, curator=_span_curator(), history=history)
        rows = [c.args[1] for c in history.add_context_message.call_args_list]
        assert rows == ["Long term memory curated over 1 August .. 10 August from 6 interactions."]
        assert history.add_context_message.call_args.kwargs['source'] == HISTORY_SOURCE

    def test_span_note_mentions_failed_chunks_only_when_there_were_any(self):
        summary = {'chunks': 3, 'curated': 1, 'empty': 0, 'failed': 2, 'interaction_count': 6,
                   'period_start': '2026-08-01T00:00', 'period_end': '2026-08-10T23:59'}
        assert LongTermMemoryCurator.span_note(summary) == ("Long term memory curated over 1 August .. 10 August from "
                                                            "6 interactions; 2 of its 3 chunks failed and left nothing.")

    def test_single_chunk_span_records_only_the_completion_row(self):
        history = Mock()
        LongTermMemoryCurator.run_span(date(2026, 8, 1), date(2026, 8, 3), curator=_span_curator(), history=history)
        assert history.add_context_message.call_count == 1

    def test_span_is_skipped_while_another_curation_runs(self):
        started, release = threading.Event(), threading.Event()
        slow = Mock()

        def curate(*_):
            started.set()
            release.wait(5)
            return "bank", {'interaction_count': 0, 'new_memory_size': 4}
        slow.curate_memory.side_effect = curate
        first = threading.Thread(target=LongTermMemoryCurator.run_span,
                                 args=(self.START, self.END), kwargs={'curator': slow})
        first.start()
        assert started.wait(5)
        try:
            assert LongTermMemoryCurator.run_span(self.START, self.END, curator=_span_curator()) is None
            assert LongTermMemoryCurator.run(curator=Mock()) is None
        finally:
            release.set()
            first.join(5)
        assert slow.curate_memory.call_count == 3


class TestEmptyPeriod:
    """A period with no interactions costs no LLM call and changes nothing."""

    def _curator(self):
        llm = Mock()
        curator = LongTermMemoryCurator(llm=llm, test_mode=True)
        curator.floxy_db = Mock()
        curator.floxy_db.get_interaction_history.return_value = NO_INTERACTIONS
        curator._load_memory = Mock(return_value="# Memory\nA bank long enough")
        return curator

    def test_curate_memory_returns_the_bank_untouched(self):
        curator = self._curator()
        with patch('assistant.agents.LongTermMemoryCurator.CalDAVIntegration.fetch_calendar_events') as calendar:
            memory, metadata = curator.curate_memory(datetime(2026, 9, 10, 14), datetime(2026, 9, 10, 16))
        assert memory == "# Memory\nA bank long enough"
        assert metadata['empty'] is True and metadata['interaction_count'] == 0
        assert metadata['period_start'] == '2026-09-10T14:00' and metadata['period_end'] == '2026-09-10T16:00'
        assert metadata['old_memory_size'] == metadata['new_memory_size'] == len(memory)
        curator.llm.think.assert_not_called()
        calendar.assert_not_called()

    def test_run_does_not_save_or_record_an_empty_period(self):
        curator = Mock()
        curator.curate_memory.return_value = ("bank", {'empty': True, 'interaction_count': 0})
        history = Mock()
        metadata = LongTermMemoryCurator.run(curator=curator, history=history)
        assert metadata['empty'] is True
        curator.save_memory.assert_not_called()
        curator.save_debug_log.assert_not_called()
        history.add_context_message.assert_not_called()
