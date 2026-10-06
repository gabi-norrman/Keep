from unittest.mock import patch, Mock


# ===========================================================================
# TestMusicZonesSectionGenerator
# ===========================================================================

# ===========================================================================
# TestSonosFavoritesSectionGenerator
# ===========================================================================

class TestSonosFavoritesSectionGenerator:
    @patch('integrations.music.SonosFavoritesSectionGenerator.MusicManager')
    def test_fetch_favorites_formats_with_f_ids(self, mock_mm_cls):
        mock_mm = Mock()
        mock_mm.get_favorites.return_value = ["Radio 1", "Jazz FM"]
        mock_mm.get_playlists.return_value = []
        mock_mm_cls.get_instance.return_value = mock_mm
        from integrations.music.SonosFavoritesSectionGenerator import SonosFavoritesSectionGenerator
        section = SonosFavoritesSectionGenerator(None)
        result = section._fetch_content()
        assert "Radio 1 (ID: F-0)" in result
        assert "Jazz FM (ID: F-1)" in result

    @patch('integrations.music.SonosFavoritesSectionGenerator.MusicManager')
    def test_fetch_playlists_formats_with_p_ids(self, mock_mm_cls):
        mock_mm = Mock()
        mock_mm.get_favorites.return_value = []
        mock_mm.get_playlists.return_value = ["Chill", "Workout"]
        mock_mm_cls.get_instance.return_value = mock_mm
        from integrations.music.SonosFavoritesSectionGenerator import SonosFavoritesSectionGenerator
        section = SonosFavoritesSectionGenerator(None)
        result = section._fetch_content()
        assert "Chill (ID: P-0)" in result
        assert "Workout (ID: P-1)" in result

    @patch('integrations.music.SonosFavoritesSectionGenerator.MusicManager')
    def test_favorites_error_returns_empty(self, mock_mm_cls):
        mock_mm = Mock()
        mock_mm.get_favorites.side_effect = RuntimeError("connection failed")
        mock_mm.get_playlists.return_value = []
        mock_mm_cls.get_instance.return_value = mock_mm
        from integrations.music.SonosFavoritesSectionGenerator import SonosFavoritesSectionGenerator
        section = SonosFavoritesSectionGenerator(None)
        result = section._fetch_content()
        assert result == ""

    @patch('integrations.music.SonosFavoritesSectionGenerator.MusicManager')
    def test_playlists_error_preserves_favorites(self, mock_mm_cls):
        mock_mm = Mock()
        mock_mm.get_favorites.return_value = ["Radio 1"]
        mock_mm.get_playlists.side_effect = RuntimeError("connection failed")
        mock_mm_cls.get_instance.return_value = mock_mm
        from integrations.music.SonosFavoritesSectionGenerator import SonosFavoritesSectionGenerator
        section = SonosFavoritesSectionGenerator(None)
        result = section._fetch_content()
        assert "Radio 1 (ID: F-0)" in result

# ===========================================================================
# TestSonosFavoritesSectionGeneratorMonitoring
# ===========================================================================

class TestSonosFavoritesSectionGeneratorMonitoring:
    @patch('integrations.music.SonosFavoritesSectionGenerator.time')
    @patch('integrations.music.SonosFavoritesSectionGenerator.MusicManager')
    def test_start_monitoring_updates_when_data_available_immediately(self, mock_mm_cls, mock_time):
        mock_mm = Mock()
        mock_mm.get_favorites.return_value = ["Radio 1"]
        mock_mm.get_playlists.return_value = []
        mock_mm_cls.get_instance.return_value = mock_mm

        mock_time.time.return_value = 100.0

        def sleep_and_stop(duration):
            if duration == 30 * 60:
                section._stop_event.set()

        mock_time.sleep.side_effect = sleep_and_stop

        from integrations.music.SonosFavoritesSectionGenerator import SonosFavoritesSectionGenerator
        section = SonosFavoritesSectionGenerator(None)
        section._wait = mock_time.sleep  # the loop waits on _wait; the test drives it via the mocked sleep
        section._update_content = Mock()

        section._start_monitoring()

        section._update_content.assert_called_once()

    @patch('integrations.music.SonosFavoritesSectionGenerator.time')
    @patch('integrations.music.SonosFavoritesSectionGenerator.MusicManager')
    def test_start_monitoring_polls_until_data_available(self, mock_mm_cls, mock_time):
        mock_mm = Mock()
        call_count = [0]

        def get_favorites_delayed():
            call_count[0] += 1
            if call_count[0] >= 3:
                return ["Radio 1"]
            return []

        mock_mm.get_favorites.side_effect = get_favorites_delayed
        mock_mm.get_playlists.return_value = []
        mock_mm_cls.get_instance.return_value = mock_mm

        mock_time.time.return_value = 100.0

        def sleep_and_stop(duration):
            if duration == 30 * 60:
                section._stop_event.set()

        mock_time.sleep.side_effect = sleep_and_stop

        from integrations.music.SonosFavoritesSectionGenerator import SonosFavoritesSectionGenerator
        section = SonosFavoritesSectionGenerator(None)
        section._wait = mock_time.sleep  # the loop waits on _wait; the test drives it via the mocked sleep
        section._update_content = Mock()

        section._start_monitoring()

        assert call_count[0] == 3
        section._update_content.assert_called_once()

    @patch('integrations.music.SonosFavoritesSectionGenerator.time')
    @patch('integrations.music.SonosFavoritesSectionGenerator.MusicManager')
    def test_start_monitoring_respects_stop_event_during_polling(self, mock_mm_cls, mock_time):
        mock_mm = Mock()
        mock_mm.get_favorites.return_value = []
        mock_mm.get_playlists.return_value = []
        mock_mm_cls.get_instance.return_value = mock_mm

        mock_time.time.return_value = 100.0
        mock_time.sleep = Mock()

        from integrations.music.SonosFavoritesSectionGenerator import SonosFavoritesSectionGenerator
        section = SonosFavoritesSectionGenerator(None)
        section._wait = mock_time.sleep  # the loop waits on _wait; the test drives it via the mocked sleep
        section._update_content = Mock()
        section._stop_event.set()

        section._start_monitoring()

        section._update_content.assert_not_called()

    @patch('integrations.music.SonosFavoritesSectionGenerator.time')
    @patch('integrations.music.SonosFavoritesSectionGenerator.MusicManager')
    def test_start_monitoring_refresh_cycle_respects_stop_event(self, mock_mm_cls, mock_time):
        mock_mm = Mock()
        mock_mm.get_favorites.return_value = ["Radio 1"]
        mock_mm.get_playlists.return_value = []
        mock_mm_cls.get_instance.return_value = mock_mm

        mock_time.time.return_value = 100.0

        sleep_call_count = [0]

        def sleep_and_stop(duration):
            sleep_call_count[0] += 1
            if sleep_call_count[0] == 1 and duration == 30 * 60:
                section._stop_event.set()

        mock_time.sleep.side_effect = sleep_and_stop

        from integrations.music.SonosFavoritesSectionGenerator import SonosFavoritesSectionGenerator
        section = SonosFavoritesSectionGenerator(None)
        section._wait = mock_time.sleep  # the loop waits on _wait; the test drives it via the mocked sleep
        section._update_content = Mock()

        section._start_monitoring()

        assert section._update_content.call_count == 1

    @patch('integrations.music.SonosFavoritesSectionGenerator.time')
    @patch('integrations.music.SonosFavoritesSectionGenerator.MusicManager')
    def test_start_monitoring_playlists_trigger_update(self, mock_mm_cls, mock_time):
        mock_mm = Mock()
        mock_mm.get_favorites.return_value = []
        mock_mm.get_playlists.return_value = ["Chill Playlist"]
        mock_mm_cls.get_instance.return_value = mock_mm

        mock_time.time.return_value = 100.0

        def sleep_and_stop(duration):
            if duration == 30 * 60:
                section._stop_event.set()

        mock_time.sleep.side_effect = sleep_and_stop

        from integrations.music.SonosFavoritesSectionGenerator import SonosFavoritesSectionGenerator
        section = SonosFavoritesSectionGenerator(None)
        section._wait = mock_time.sleep  # the loop waits on _wait; the test drives it via the mocked sleep
        section._update_content = Mock()

        section._start_monitoring()

        section._update_content.assert_called_once()
