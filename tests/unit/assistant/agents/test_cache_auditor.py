import pytest
from unittest.mock import Mock, patch
import threading


@pytest.mark.unit
class TestCacheAuditorInit:
    """Tests for CacheAuditor initialization"""

    @pytest.fixture
    def mock_gemini_llm(self):
        with patch('assistant.agents.CacheAuditor.create_llm') as mock:
            mock_instance = Mock()
            mock_instance.missing_api_key.return_value = None
            mock.return_value = mock_instance
            yield mock

    @pytest.fixture
    def mock_commands(self):
        with patch('assistant.agents.CacheAuditor.Commands') as mock:
            mock.cmd_turn_on = lambda: None
            mock.cmd_turn_off = lambda: None
            mock.cmd_play = lambda: None
            yield mock

    @pytest.fixture
    def mock_command_parser(self):
        with patch('assistant.agents.CacheAuditor.CommandParser') as mock:
            yield mock

    def test_creates_gemini_llm(self, mock_gemini_llm, mock_commands, mock_command_parser):
        from assistant.agents.CacheAuditor import CacheAuditor
        auditor = CacheAuditor()
        mock_gemini_llm.assert_called_once()
        mock_gemini_llm.return_value.initialize.assert_called_once()

    def test_creates_command_parser(self, mock_gemini_llm, mock_commands, mock_command_parser):
        from assistant.agents.CacheAuditor import CacheAuditor
        CacheAuditor()
        mock_command_parser.assert_called_once()

    def test_extracts_commands_from_class(self, mock_gemini_llm, mock_command_parser):
        with patch('assistant.agents.CacheAuditor.Commands') as mock_commands:
            mock_commands.cmd_turn_on = lambda: None
            mock_commands.cmd_turn_off = lambda: None
            mock_commands.cmd_play = lambda: None
            mock_commands.helper_method = lambda: None
            from assistant.agents.CacheAuditor import CacheAuditor
            CacheAuditor()
            call_args = mock_command_parser.call_args[0][0]
            assert 'turn_on' in call_args
            assert 'turn_off' in call_args
            assert 'play' in call_args
            assert 'helper_method' not in call_args

    def test_init_not_running(self, mock_gemini_llm, mock_commands, mock_command_parser):
        from assistant.agents.CacheAuditor import CacheAuditor
        auditor = CacheAuditor()
        assert auditor._running is False
        assert auditor._thread is None


@pytest.mark.unit
class TestCacheAuditorHasCommands:
    """Tests for _has_commands() command detection"""

    @pytest.fixture
    def mock_gemini_llm(self):
        with patch('assistant.agents.CacheAuditor.create_llm') as mock:
            mock_instance = Mock()
            mock_instance.missing_api_key.return_value = None
            mock.return_value = mock_instance
            yield mock

    @pytest.fixture
    def mock_commands(self):
        with patch('assistant.agents.CacheAuditor.Commands') as mock:
            mock.cmd_turn_on = lambda: None
            yield mock

    @pytest.fixture
    def cache_auditor(self, mock_gemini_llm, mock_commands):
        with patch('assistant.agents.CacheAuditor.CommandParser') as mock_parser_class:
            mock_parser = Mock()
            mock_parser_class.return_value = mock_parser
            from assistant.agents.CacheAuditor import CacheAuditor
            auditor = CacheAuditor()
            auditor._parser = mock_parser
            return auditor

    def test_returns_true_when_command_found(self, cache_auditor):
        cache_auditor._parser.extract_all_commands.return_value = [("turn_on", "lights")]

        result = cache_auditor._has_commands("turn_on(lights)")
        assert result is True

    def test_returns_false_when_no_command(self, cache_auditor):
        cache_auditor._parser.extract_all_commands.return_value = []

        result = cache_auditor._has_commands("just some text")
        assert result is False

    def test_handles_backticks(self, cache_auditor):
        cache_auditor._parser.extract_all_commands.return_value = [("turn_on", "lights")]

        result = cache_auditor._has_commands("`turn_on(lights)`")
        assert result is True

    def test_returns_false_when_no_complete_command(self, cache_auditor):
        cache_auditor._parser.extract_all_commands.return_value = []

        result = cache_auditor._has_commands("turn_")
        assert result is False

    def test_returns_false_when_no_extractable_command(self, cache_auditor):
        cache_auditor._parser.extract_all_commands.return_value = []

        result = cache_auditor._has_commands("turn_on()")
        assert result is False

    def test_detects_command_with_prefix_text(self, cache_auditor):
        cache_auditor._parser.extract_all_commands.return_value = [("execute", "action")]

        result = cache_auditor._has_commands("Sure! Here's execute(action)")
        assert result is True


@pytest.mark.unit
class TestCacheAuditorAuditPass:
    """Tests for _audit_pass() verdict handling"""

    @pytest.fixture
    def mock_gemini_llm(self):
        with patch('assistant.agents.CacheAuditor.create_llm') as mock:
            mock_instance = Mock()
            mock_instance.missing_api_key.return_value = None
            mock.return_value = mock_instance
            yield mock_instance, mock

    @pytest.fixture
    def mock_database(self):
        with patch('assistant.agents.CacheAuditor.FloxyDatabase') as mock:
            mock_db = Mock()
            mock.get_instance.return_value = mock_db
            yield mock_db

    @pytest.fixture
    def mock_commands(self):
        with patch('assistant.agents.CacheAuditor.Commands') as mock:
            mock.cmd_turn_on = lambda: None
            yield mock

    @pytest.fixture
    def mock_logging(self):
        with patch('assistant.agents.CacheAuditor.info') as mock_info, \
             patch('assistant.agents.CacheAuditor.error') as mock_error, \
             patch('assistant.agents.CacheAuditor.trace') as mock_trace, \
             patch('assistant.agents.CacheAuditor.fine') as mock_fine:
            yield {
                'info': mock_info,
                'error': mock_error,
                'trace': mock_trace,
                'fine': mock_fine
            }

    @pytest.fixture
    def cache_auditor(self, mock_gemini_llm, mock_commands, mock_logging):
        mock_llm, _ = mock_gemini_llm
        with patch('assistant.agents.CacheAuditor.CommandParser') as mock_parser_class:
            mock_parser = Mock()
            mock_parser_class.return_value = mock_parser
            from assistant.agents.CacheAuditor import CacheAuditor
            auditor = CacheAuditor()
            auditor._llm = mock_llm
            auditor._parser = mock_parser
            auditor._running = True
            return auditor

    def test_no_entries_returns_early(self, cache_auditor, mock_database, mock_logging):
        mock_database.get_unaudited_cache_entries.return_value = []
        cache_auditor._audit_pass()
        mock_logging['trace'].assert_called()
        mock_database.mark_audited.assert_not_called()

    def test_marks_not_cachable_if_no_commands(self, cache_auditor, mock_database, mock_logging):
        mock_database.get_unaudited_cache_entries.return_value = [
            (1, "query", "response without commands")
        ]
        cache_auditor._parser.extract_all_commands.return_value = []

        cache_auditor._audit_pass()
        mock_database.mark_not_cachable.assert_called_with(1)

    def test_marks_audited_on_safe_verdict(self, cache_auditor, mock_database, mock_logging):
        mock_database.get_unaudited_cache_entries.return_value = [
            (1, "turn off lights", "execute(TurnOffLights)")
        ]
        cache_auditor._parser.extract_all_commands.return_value = [("execute", "TurnOffLights")]
        cache_auditor._llm.think.return_value = "This is safe to cache.\nSAFE"

        cache_auditor._audit_pass()
        mock_database.mark_audited.assert_called_with(1)

    def test_marks_not_cachable_on_unsafe_verdict(self, cache_auditor, mock_database, mock_logging):
        mock_database.get_unaudited_cache_entries.return_value = [
            (1, "what time is it", "It's 3pm execute(ShowTime)")
        ]
        cache_auditor._parser.extract_all_commands.return_value = [("execute", "ShowTime")]
        cache_auditor._llm.think.return_value = "Time-dependent response.\nUNSAFE"

        cache_auditor._audit_pass()
        mock_database.mark_not_cachable.assert_called_with(1)

    def test_handles_exception_during_audit(self, cache_auditor, mock_database, mock_logging):
        mock_database.get_unaudited_cache_entries.return_value = [
            (1, "query", "execute(action)")
        ]
        cache_auditor._parser.extract_all_commands.return_value = [("execute", "action")]
        cache_auditor._llm.think.side_effect = Exception("API error")

        cache_auditor._audit_pass()
        mock_logging['error'].assert_called()

    def test_stops_early_when_not_running(self, cache_auditor, mock_database, mock_logging):
        mock_database.get_unaudited_cache_entries.return_value = [
            (1, "q1", "execute(a)"),
            (2, "q2", "execute(b)"),
        ]
        cache_auditor._running = False

        cache_auditor._audit_pass()
        mock_database.mark_audited.assert_not_called()
        mock_database.mark_not_cachable.assert_not_called()

    def test_processes_multiple_entries(self, cache_auditor, mock_database, mock_logging):
        mock_database.get_unaudited_cache_entries.return_value = [
            (1, "q1", "execute(a)"),
            (2, "q2", "execute(b)"),
        ]
        cache_auditor._parser.extract_all_commands.return_value = [("execute", "action")]
        cache_auditor._llm.think.return_value = "Safe.\nSAFE"

        cache_auditor._audit_pass()
        assert mock_database.mark_audited.call_count == 2

    def test_verdict_case_insensitive(self, cache_auditor, mock_database, mock_logging):
        mock_database.get_unaudited_cache_entries.return_value = [
            (1, "query", "execute(action)")
        ]
        cache_auditor._parser.extract_all_commands.return_value = [("execute", "action")]
        cache_auditor._llm.think.return_value = "Reasoning here.\nsafe"

        cache_auditor._audit_pass()
        mock_database.mark_audited.assert_called_with(1)


@pytest.mark.unit
class TestCacheAuditorStartStop:
    """Tests for start() and stop() thread lifecycle"""

    @pytest.fixture
    def mock_gemini_llm(self):
        with patch('assistant.agents.CacheAuditor.create_llm') as mock:
            mock_instance = Mock()
            mock_instance.missing_api_key.return_value = None
            mock.return_value = mock_instance
            yield mock

    @pytest.fixture
    def mock_commands(self):
        with patch('assistant.agents.CacheAuditor.Commands') as mock:
            mock.cmd_turn_on = lambda: None
            yield mock

    @pytest.fixture
    def mock_command_parser(self):
        with patch('assistant.agents.CacheAuditor.CommandParser') as mock:
            yield mock

    @pytest.fixture
    def mock_logging(self):
        with patch('assistant.agents.CacheAuditor.info') as mock_info, \
             patch('assistant.agents.CacheAuditor.error') as mock_error, \
             patch('assistant.agents.CacheAuditor.trace') as mock_trace, \
             patch('assistant.agents.CacheAuditor.fine') as mock_fine:
            yield

    def test_start_sets_running_flag(self, mock_gemini_llm, mock_commands, mock_command_parser, mock_logging):
        from assistant.agents.CacheAuditor import CacheAuditor
        auditor = CacheAuditor()
        with patch.object(threading.Thread, 'start'):
            auditor.start()
        assert auditor._running is True

    def test_start_creates_daemon_thread(self, mock_gemini_llm, mock_commands, mock_command_parser, mock_logging):
        from assistant.agents.CacheAuditor import CacheAuditor
        auditor = CacheAuditor()
        with patch.object(threading.Thread, 'start'):
            auditor.start()
        assert auditor._thread is not None
        assert auditor._thread.name == "CacheAuditor"
        assert auditor._thread.daemon is True

    def test_stop_clears_running_flag(self, mock_gemini_llm, mock_commands, mock_command_parser, mock_logging):
        from assistant.agents.CacheAuditor import CacheAuditor
        auditor = CacheAuditor()
        auditor._running = True
        auditor.stop()
        assert auditor._running is False


@pytest.mark.unit
class TestCacheAuditorWithoutApiKey:
    """Without its tier's API key the auditor is disabled instead of failing the start."""

    @pytest.fixture
    def keyless_llm(self):
        with patch('assistant.agents.CacheAuditor.create_llm') as mock:
            mock_instance = Mock()
            mock_instance.missing_api_key.return_value = "api_keys.gemini"
            mock.return_value = mock_instance
            yield mock_instance

    @pytest.fixture
    def auditor(self, keyless_llm):
        with patch('assistant.agents.CacheAuditor.Commands'), \
             patch('assistant.agents.CacheAuditor.CommandParser'), \
             patch('assistant.agents.CacheAuditor.info') as mock_info:
            from assistant.agents.CacheAuditor import CacheAuditor
            auditor = CacheAuditor()
            auditor._logged = mock_info
            yield auditor

    def test_does_not_initialize_the_llm(self, auditor, keyless_llm):
        keyless_llm.initialize.assert_not_called()
        assert auditor.disabled_reason == "api_keys.gemini"
        auditor._logged.assert_called_once_with("Cache auditor disabled: no api_keys.gemini")

    def test_start_is_a_no_op(self, auditor):
        with patch('assistant.agents.CacheAuditor.threading.Thread') as mock_thread:
            auditor.start()
        mock_thread.assert_not_called()
        assert auditor._running is False
        assert auditor._thread is None
