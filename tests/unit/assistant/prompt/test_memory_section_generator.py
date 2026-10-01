from unittest.mock import patch
class TestMemorySectionGenerator:
    def test_returns_empty_for_missing_file(self):
        from assistant.prompt.sections.MemorySectionGenerator import MemorySectionGenerator
        section = MemorySectionGenerator(None)
        section.file_path = "/nonexistent/memory.md"
        result = section._fetch_content()
        assert result == ""

    @patch('assistant.prompt.sections.MemorySectionGenerator.MemorySectionGenerator._wait', side_effect=StopIteration)
    @patch('assistant.prompt.sections.MemorySectionGenerator.os.path.getmtime')
    @patch('assistant.prompt.sections.MemorySectionGenerator.os.path.exists', return_value=True)
    @patch('assistant.prompt.PromptSectionGenerator.time.sleep')
    def test_monitoring_updates_on_mtime_change(self, mock_base_sleep, mock_exists, mock_getmtime, mock_sleep):
        from assistant.prompt.sections.MemorySectionGenerator import MemorySectionGenerator
        section = MemorySectionGenerator(None)
        section.file_path = "/fake/memory.md"
        mock_getmtime.return_value = 100.0
        section.last_mtime = 50.0
        with patch.object(section, '_update_content') as mock_update:
            try:
                section._start_monitoring()
            except StopIteration:
                pass
            mock_update.assert_called_once()

    @patch('assistant.prompt.sections.MemorySectionGenerator.MemorySectionGenerator._wait', side_effect=StopIteration)
    @patch('assistant.prompt.sections.MemorySectionGenerator.os.path.getmtime', return_value=100.0)
    @patch('assistant.prompt.sections.MemorySectionGenerator.os.path.exists', return_value=True)
    def test_monitoring_skips_when_mtime_unchanged(self, mock_exists, mock_getmtime, mock_sleep):
        from assistant.prompt.sections.MemorySectionGenerator import MemorySectionGenerator
        section = MemorySectionGenerator(None)
        section.file_path = "/fake/memory.md"
        section.last_mtime = 100.0
        with patch.object(section, '_update_content') as mock_update:
            try:
                section._start_monitoring()
            except StopIteration:
                pass
            mock_update.assert_not_called()

    def test_reads_full_file_content(self, tmp_path):
        from assistant.prompt.sections.MemorySectionGenerator import MemorySectionGenerator
        section = MemorySectionGenerator(None)
        memory_file = tmp_path / "memory.md"
        content = "Line 1\nLine 2\nLine 3"
        memory_file.write_text(content)
        section.file_path = str(memory_file)
        result = section._fetch_content()
        assert result == content

    def test_reads_unicode_content(self, tmp_path):
        from assistant.prompt.sections.MemorySectionGenerator import MemorySectionGenerator
        section = MemorySectionGenerator(None)
        memory_file = tmp_path / "memory.md"
        memory_file.write_text("Köksö lampa, Björk, Möte")
        section.file_path = str(memory_file)
        result = section._fetch_content()
        assert "Köksö" in result
        assert "Björk" in result

    @patch('assistant.prompt.sections.MemorySectionGenerator.MemorySectionGenerator._wait', side_effect=StopIteration)
    @patch('assistant.prompt.sections.MemorySectionGenerator.os.path.getmtime')
    @patch('assistant.prompt.sections.MemorySectionGenerator.os.path.exists', return_value=True)
    def test_monitoring_updates_last_mtime(self, mock_exists, mock_getmtime, mock_sleep):
        from assistant.prompt.sections.MemorySectionGenerator import MemorySectionGenerator
        section = MemorySectionGenerator(None)
        section.file_path = "/fake/memory.md"
        mock_getmtime.return_value = 200.0
        section.last_mtime = 0
        with patch.object(section, '_fetch_content', return_value="content"):
            try:
                section._start_monitoring()
            except StopIteration:
                pass
        assert section.last_mtime == 200.0

    @patch('assistant.prompt.sections.MemorySectionGenerator.MemorySectionGenerator._wait', side_effect=StopIteration)
    @patch('assistant.prompt.sections.MemorySectionGenerator.os.path.exists', return_value=False)
    def test_monitoring_skips_when_file_missing(self, mock_exists, mock_sleep):
        from assistant.prompt.sections.MemorySectionGenerator import MemorySectionGenerator
        section = MemorySectionGenerator(None)
        section.file_path = "/nonexistent/memory.md"
        with patch.object(section, '_update_content') as mock_update:
            try:
                section._start_monitoring()
            except StopIteration:
                pass
            mock_update.assert_not_called()

    @patch('assistant.prompt.sections.MemorySectionGenerator.MemorySectionGenerator._wait', side_effect=StopIteration)
    @patch('assistant.prompt.sections.MemorySectionGenerator.os.path.getmtime', side_effect=OSError("permission denied"))
    @patch('assistant.prompt.sections.MemorySectionGenerator.os.path.exists', return_value=True)
    def test_monitoring_handles_getmtime_error(self, mock_exists, mock_getmtime, mock_sleep):
        from assistant.prompt.sections.MemorySectionGenerator import MemorySectionGenerator
        section = MemorySectionGenerator(None)
        section.file_path = "/fake/memory.md"
        with patch.object(section, '_update_content') as mock_update:
            try:
                section._start_monitoring()
            except StopIteration:
                pass
            mock_update.assert_not_called()

class TestFirstCurationNotice:
    """While the first-run pipeline curates the first bank the section opens with the heads-up,
    the prompt's counterpart of the Memory tab's band; the loop refreshes when the state flips."""

    def _section(self, tmp_path, text="# The Family\n* Lives in Gothenburg"):
        from assistant.prompt.sections.MemorySectionGenerator import MemorySectionGenerator
        section = MemorySectionGenerator(None)
        memory_file = tmp_path / "memory.md"
        memory_file.write_text(text)
        section.file_path = str(memory_file)
        return section

    @patch('assistant.prompt.sections.MemorySectionGenerator.FirstRun.curation_progress', return_value=object())
    def test_notice_opens_the_section_while_curating(self, _progress, tmp_path):
        from assistant.prompt.sections.MemorySectionGenerator import FIRST_CURATION_NOTICE
        result = self._section(tmp_path)._fetch_content()
        assert result.startswith(FIRST_CURATION_NOTICE + "\n\n")
        assert result.endswith("# The Family\n* Lives in Gothenburg")

    @patch('assistant.prompt.sections.MemorySectionGenerator.FirstRun.curation_progress', return_value=object())
    def test_notice_alone_when_the_bank_is_empty(self, _progress, tmp_path):
        from assistant.prompt.sections.MemorySectionGenerator import FIRST_CURATION_NOTICE
        assert self._section(tmp_path, "")._fetch_content() == FIRST_CURATION_NOTICE

    @patch('assistant.prompt.sections.MemorySectionGenerator.FirstRun.curation_progress', return_value=None)
    def test_no_notice_outside_the_first_curation(self, _progress, tmp_path):
        assert self._section(tmp_path)._fetch_content() == "# The Family\n* Lives in Gothenburg"

    @patch('assistant.prompt.sections.MemorySectionGenerator.MemorySectionGenerator._wait', side_effect=StopIteration)
    @patch('assistant.prompt.sections.MemorySectionGenerator.FirstRun.curation_progress', return_value=None)
    def test_loop_refreshes_when_the_curation_ends_without_a_file_change(self, _progress, _wait, tmp_path):
        """The last chunk writes memory.md before the progress clears, so the mtime check alone
        would keep the notice until the next edit."""
        section = self._section(tmp_path)
        import os
        section.last_mtime = os.path.getmtime(section.file_path)
        section._curating = True
        with patch.object(section, '_update_content') as mock_update:
            try:
                section._start_monitoring()
            except StopIteration:
                pass
            mock_update.assert_called_once()
        assert section._curating is False


class TestRememberedNotes:
    """A `remember` must leave the memory section — the top of the prompt — untouched and
    surface in the notes section instead."""

    @staticmethod
    def _section(tmp_path, prompt_manager=None):
        from assistant import MemoryFile
        from assistant.prompt.sections.MemorySectionGenerator import MemorySectionGenerator
        bank = str(tmp_path / "bank.md")
        MemoryFile.write("# The Family\n* Charlotte plays the cello\n", bank)
        section = MemorySectionGenerator(prompt_manager)
        section.file_path = bank
        section._refresh()
        return section, bank

    @patch('assistant.prompt.sections.MemorySectionGenerator.FirstRun.curation_progress', return_value=None)
    def test_a_remembered_note_lands_in_the_notes_section_only(self, _progress, tmp_path):
        from assistant import MemoryFile
        section, bank = self._section(tmp_path)
        top_before = section.get_content()
        MemoryFile.append("Frances moved to Lund", bank)
        section._refresh()
        assert section.get_content() == top_before
        assert section.recent.get_content() == "Frances moved to Lund"

    @patch('assistant.prompt.sections.MemorySectionGenerator.FirstRun.curation_progress', return_value=None)
    def test_a_remembered_note_notifies_for_the_notes_section_not_the_bank(self, _progress, tmp_path):
        from unittest.mock import Mock
        from assistant import MemoryFile
        manager = Mock()
        section, bank = self._section(tmp_path, manager)
        manager.reset_mock()
        MemoryFile.append("Frances moved to Lund", bank)
        section._refresh()
        assert [c.args[0] for c in manager.on_section_updated.call_args_list] == [section.recent.section_name]

    @patch('assistant.prompt.sections.MemorySectionGenerator.FirstRun.curation_progress', return_value=None)
    def test_a_curation_moves_the_notes_into_the_bank_and_empties_the_notes_section(self, _progress, tmp_path):
        from assistant import MemoryFile
        section, bank = self._section(tmp_path)
        empty = section.recent.get_content()
        MemoryFile.append("Frances moved to Lund", bank)
        section._refresh()
        MemoryFile.write("# The Family\n* Charlotte plays the cello\n* Frances lives in Lund\n", bank)
        section._refresh()
        assert "Frances lives in Lund" in section.get_content()
        assert section.recent.get_content() == empty

    @patch('assistant.prompt.sections.MemorySectionGenerator.FirstRun.curation_progress', return_value=None)
    def test_the_bank_is_notified_before_the_notes_so_a_rebuild_sees_both_halves(self, _progress, tmp_path):
        from unittest.mock import Mock
        from assistant import MemoryFile
        manager = Mock()
        section, bank = self._section(tmp_path, manager)
        MemoryFile.append("Frances moved to Lund", bank)
        section._refresh()
        seen = []
        manager.on_section_updated.side_effect = lambda name: seen.append((name, section.recent.get_content()))
        MemoryFile.write("# The Family\n* Frances lives in Lund\n", bank)
        section._refresh()
        assert seen[0][0] == section.section_name
        assert "Frances moved to Lund" not in seen[0][1]
