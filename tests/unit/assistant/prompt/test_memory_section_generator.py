from unittest.mock import patch
import threading


class TestMemorySectionGenerator:
    def test_reads_memory_file(self, tmp_path):
        from assistant.prompt.sections.MemorySectionGenerator import MemorySectionGenerator
        section = MemorySectionGenerator(None)
        memory_file = tmp_path / "memory.md"
        memory_file.write_text("# Memory\nTest memory content")
        section.file_path = str(memory_file)
        result = section._fetch_content()
        assert "Test memory content" in result

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

    def test_reads_empty_file(self, tmp_path):
        from assistant.prompt.sections.MemorySectionGenerator import MemorySectionGenerator
        section = MemorySectionGenerator(None)
        memory_file = tmp_path / "memory.md"
        memory_file.write_text("")
        section.file_path = str(memory_file)
        result = section._fetch_content()
        assert result == ""

    def test_reads_unicode_content(self, tmp_path):
        from assistant.prompt.sections.MemorySectionGenerator import MemorySectionGenerator
        section = MemorySectionGenerator(None)
        memory_file = tmp_path / "memory.md"
        memory_file.write_text("Köksö lampa, Björk, Möte")
        section.file_path = str(memory_file)
        result = section._fetch_content()
        assert "Köksö" in result
        assert "Björk" in result

    def test_reads_large_file(self, tmp_path):
        from assistant.prompt.sections.MemorySectionGenerator import MemorySectionGenerator
        section = MemorySectionGenerator(None)
        memory_file = tmp_path / "memory.md"
        content = "x" * 100_000
        memory_file.write_text(content)
        section.file_path = str(memory_file)
        result = section._fetch_content()
        assert len(result) == 100_000

    def test_default_file_path(self):
        from assistant.prompt.sections.MemorySectionGenerator import MemorySectionGenerator
        section = MemorySectionGenerator(None)
        assert section.file_path == "memory.md"

    def test_default_last_mtime_is_zero(self):
        from assistant.prompt.sections.MemorySectionGenerator import MemorySectionGenerator
        section = MemorySectionGenerator(None)
        assert section.last_mtime == 0

    def test_section_name_is_memory(self):
        from assistant.prompt.sections.MemorySectionGenerator import MemorySectionGenerator
        section = MemorySectionGenerator(None)
        assert section.section_name == "Memory"

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

    def test_concurrent_reads_return_consistent_content(self, tmp_path):
        from assistant.prompt.sections.MemorySectionGenerator import MemorySectionGenerator
        section = MemorySectionGenerator(None)
        memory_file = tmp_path / "memory.md"
        memory_file.write_text("stable content")
        section.file_path = str(memory_file)

        results = []
        def read_content():
            results.append(section._fetch_content())

        threads = [threading.Thread(target=read_content) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert all(r == "stable content" for r in results)

    def test_file_updated_between_reads(self, tmp_path):
        from assistant.prompt.sections.MemorySectionGenerator import MemorySectionGenerator
        section = MemorySectionGenerator(None)
        memory_file = tmp_path / "memory.md"
        memory_file.write_text("version 1")
        section.file_path = str(memory_file)
        assert section._fetch_content() == "version 1"
        memory_file.write_text("version 2")
        assert section._fetch_content() == "version 2"


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

    @patch('assistant.prompt.sections.MemorySectionGenerator.MemorySectionGenerator._wait', side_effect=StopIteration)
    @patch('assistant.prompt.sections.MemorySectionGenerator.FirstRun.curation_progress', return_value=None)
    def test_loop_leaves_the_content_alone_while_nothing_changed(self, _progress, _wait, tmp_path):
        import os
        section = self._section(tmp_path)
        section.last_mtime = os.path.getmtime(section.file_path)
        with patch.object(section, '_update_content') as mock_update:
            try:
                section._start_monitoring()
            except StopIteration:
                pass
            mock_update.assert_not_called()
