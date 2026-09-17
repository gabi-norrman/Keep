from unittest.mock import patch, Mock


# ===========================================================================
# TestMusicZonesSectionGenerator
# ===========================================================================

class TestMusicZonesSectionGenerator:
    @patch('assistant.prompt.sections.MusicZonesSectionGenerator.MusicManager')
    def test_fetch_content_returns_zones_status(self, mock_mm_cls):
        mock_mm = Mock()
        mock_mm.get_all_zones_status.return_value = "Kitchen: Playing\nLiving Room: Idle"
        mock_mm_cls.get_instance.return_value = mock_mm
        from assistant.prompt.sections.MusicZonesSectionGenerator import MusicZonesSectionGenerator
        section = MusicZonesSectionGenerator(None)
        result = section._fetch_content()
        assert result == "Kitchen: Playing\nLiving Room: Idle"

    @patch('assistant.prompt.sections.MusicZonesSectionGenerator.MusicManager')
    def test_polls_every_three_seconds(self, mock_mm_cls):
        mock_mm_cls.get_instance.return_value = Mock()
        from assistant.prompt.sections.MusicZonesSectionGenerator import MusicZonesSectionGenerator
        section = MusicZonesSectionGenerator(None)
        assert section.UPDATE_INTERVAL == 3

    @patch('assistant.prompt.sections.MusicZonesSectionGenerator.MusicManager')
    def test_section_name(self, mock_mm_cls):
        # Must NOT collide with SonosFavoritesSectionGenerator's display name —
        # PromptManager routes delta notifications by section_name, so a duplicate
        # silently mis-attributes one section's updates to the other's baseline.
        mock_mm_cls.get_instance.return_value = Mock()
        from assistant.prompt.sections.MusicZonesSectionGenerator import MusicZonesSectionGenerator
        section = MusicZonesSectionGenerator(None)
        assert section.section_name == "MusicZones"


# ===========================================================================
# TestSonosFavoritesSectionGenerator
# ===========================================================================

class TestSonosFavoritesSectionGenerator:
    @patch('assistant.prompt.sections.SonosFavoritesSectionGenerator.MusicManager')
    def test_fetch_favorites_formats_with_f_ids(self, mock_mm_cls):
        mock_mm = Mock()
        mock_mm.get_favorites.return_value = ["Radio 1", "Jazz FM"]
        mock_mm.get_playlists.return_value = []
        mock_mm_cls.get_instance.return_value = mock_mm
        from assistant.prompt.sections.SonosFavoritesSectionGenerator import SonosFavoritesSectionGenerator
        section = SonosFavoritesSectionGenerator(None)
        result = section._fetch_content()
        assert "Radio 1 (ID: F-0)" in result
        assert "Jazz FM (ID: F-1)" in result

    @patch('assistant.prompt.sections.SonosFavoritesSectionGenerator.MusicManager')
    def test_fetch_playlists_formats_with_p_ids(self, mock_mm_cls):
        mock_mm = Mock()
        mock_mm.get_favorites.return_value = []
        mock_mm.get_playlists.return_value = ["Chill", "Workout"]
        mock_mm_cls.get_instance.return_value = mock_mm
        from assistant.prompt.sections.SonosFavoritesSectionGenerator import SonosFavoritesSectionGenerator
        section = SonosFavoritesSectionGenerator(None)
        result = section._fetch_content()
        assert "Chill (ID: P-0)" in result
        assert "Workout (ID: P-1)" in result

    @patch('assistant.prompt.sections.SonosFavoritesSectionGenerator.MusicManager')
    def test_fetch_content_combines_both(self, mock_mm_cls):
        mock_mm = Mock()
        mock_mm.get_favorites.return_value = ["Radio 1"]
        mock_mm.get_playlists.return_value = ["Chill"]
        mock_mm_cls.get_instance.return_value = mock_mm
        from assistant.prompt.sections.SonosFavoritesSectionGenerator import SonosFavoritesSectionGenerator
        section = SonosFavoritesSectionGenerator(None)
        result = section._fetch_content()
        assert "Radio 1 (ID: F-0)" in result
        assert "Chill (ID: P-0)" in result

    @patch('assistant.prompt.sections.SonosFavoritesSectionGenerator.MusicManager')
    def test_favorites_error_returns_empty(self, mock_mm_cls):
        mock_mm = Mock()
        mock_mm.get_favorites.side_effect = RuntimeError("connection failed")
        mock_mm.get_playlists.return_value = []
        mock_mm_cls.get_instance.return_value = mock_mm
        from assistant.prompt.sections.SonosFavoritesSectionGenerator import SonosFavoritesSectionGenerator
        section = SonosFavoritesSectionGenerator(None)
        result = section._fetch_content()
        assert result == ""

    @patch('assistant.prompt.sections.SonosFavoritesSectionGenerator.MusicManager')
    def test_playlists_error_preserves_favorites(self, mock_mm_cls):
        mock_mm = Mock()
        mock_mm.get_favorites.return_value = ["Radio 1"]
        mock_mm.get_playlists.side_effect = RuntimeError("connection failed")
        mock_mm_cls.get_instance.return_value = mock_mm
        from assistant.prompt.sections.SonosFavoritesSectionGenerator import SonosFavoritesSectionGenerator
        section = SonosFavoritesSectionGenerator(None)
        result = section._fetch_content()
        assert "Radio 1 (ID: F-0)" in result

    @patch('assistant.prompt.sections.SonosFavoritesSectionGenerator.MusicManager')
    def test_empty_favorites_and_playlists(self, mock_mm_cls):
        mock_mm = Mock()
        mock_mm.get_favorites.return_value = []
        mock_mm.get_playlists.return_value = []
        mock_mm_cls.get_instance.return_value = mock_mm
        from assistant.prompt.sections.SonosFavoritesSectionGenerator import SonosFavoritesSectionGenerator
        section = SonosFavoritesSectionGenerator(None)
        result = section._fetch_content()
        assert result == ""

    @patch('assistant.prompt.sections.SonosFavoritesSectionGenerator.MusicManager')
    def test_section_name(self, mock_mm_cls):
        mock_mm_cls.get_instance.return_value = Mock()
        from assistant.prompt.sections.SonosFavoritesSectionGenerator import SonosFavoritesSectionGenerator
        section = SonosFavoritesSectionGenerator(None)
        assert section.section_name == "SonosFavorites"

    @patch('assistant.prompt.sections.SonosFavoritesSectionGenerator.MusicManager')
    def test_fetch_names_no_zone(self, mock_mm_cls):
        mock_mm = Mock()
        mock_mm.get_favorites.return_value = ["Radio 1"]
        mock_mm.get_playlists.return_value = ["Chill"]
        mock_mm_cls.get_instance.return_value = mock_mm
        from assistant.prompt.sections.SonosFavoritesSectionGenerator import SonosFavoritesSectionGenerator
        section = SonosFavoritesSectionGenerator(None)
        section._fetch_content()
        mock_mm.get_favorites.assert_called_with()
        mock_mm.get_playlists.assert_called_with()


# ===========================================================================
# TestSonosFavoritesSectionGeneratorMonitoring
# ===========================================================================

class TestSonosFavoritesSectionGeneratorMonitoring:
    @patch('assistant.prompt.sections.SonosFavoritesSectionGenerator.time')
    @patch('assistant.prompt.sections.SonosFavoritesSectionGenerator.MusicManager')
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

        from assistant.prompt.sections.SonosFavoritesSectionGenerator import SonosFavoritesSectionGenerator
        section = SonosFavoritesSectionGenerator(None)
        section._wait = mock_time.sleep  # the loop waits on _wait; the test drives it via the mocked sleep
        section._update_content = Mock()

        section._start_monitoring()

        section._update_content.assert_called_once()

    @patch('assistant.prompt.sections.SonosFavoritesSectionGenerator.time')
    @patch('assistant.prompt.sections.SonosFavoritesSectionGenerator.MusicManager')
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

        from assistant.prompt.sections.SonosFavoritesSectionGenerator import SonosFavoritesSectionGenerator
        section = SonosFavoritesSectionGenerator(None)
        section._wait = mock_time.sleep  # the loop waits on _wait; the test drives it via the mocked sleep
        section._update_content = Mock()

        section._start_monitoring()

        assert call_count[0] == 3
        section._update_content.assert_called_once()

    @patch('assistant.prompt.sections.SonosFavoritesSectionGenerator.time')
    @patch('assistant.prompt.sections.SonosFavoritesSectionGenerator.MusicManager')
    def test_start_monitoring_timeout_when_no_data(self, mock_mm_cls, mock_time):
        mock_mm = Mock()
        mock_mm.get_favorites.return_value = []
        mock_mm.get_playlists.return_value = []
        mock_mm_cls.get_instance.return_value = mock_mm

        time_values = [100.0, 100.0, 161.0]
        mock_time.time.side_effect = time_values
        mock_time.sleep = Mock()

        from assistant.prompt.sections.SonosFavoritesSectionGenerator import SonosFavoritesSectionGenerator
        section = SonosFavoritesSectionGenerator(None)
        section._wait = mock_time.sleep  # the loop waits on _wait; the test drives it via the mocked sleep
        section._update_content = Mock()
        section._stop_event.set()

        section._start_monitoring()

        section._update_content.assert_not_called()

    @patch('assistant.prompt.sections.SonosFavoritesSectionGenerator.time')
    @patch('assistant.prompt.sections.SonosFavoritesSectionGenerator.MusicManager')
    def test_start_monitoring_respects_stop_event_during_polling(self, mock_mm_cls, mock_time):
        mock_mm = Mock()
        mock_mm.get_favorites.return_value = []
        mock_mm.get_playlists.return_value = []
        mock_mm_cls.get_instance.return_value = mock_mm

        mock_time.time.return_value = 100.0
        mock_time.sleep = Mock()

        from assistant.prompt.sections.SonosFavoritesSectionGenerator import SonosFavoritesSectionGenerator
        section = SonosFavoritesSectionGenerator(None)
        section._wait = mock_time.sleep  # the loop waits on _wait; the test drives it via the mocked sleep
        section._update_content = Mock()
        section._stop_event.set()

        section._start_monitoring()

        section._update_content.assert_not_called()

    @patch('assistant.prompt.sections.SonosFavoritesSectionGenerator.time')
    @patch('assistant.prompt.sections.SonosFavoritesSectionGenerator.MusicManager')
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

        from assistant.prompt.sections.SonosFavoritesSectionGenerator import SonosFavoritesSectionGenerator
        section = SonosFavoritesSectionGenerator(None)
        section._wait = mock_time.sleep  # the loop waits on _wait; the test drives it via the mocked sleep
        section._update_content = Mock()

        section._start_monitoring()

        assert section._update_content.call_count == 1

    @patch('assistant.prompt.sections.SonosFavoritesSectionGenerator.time')
    @patch('assistant.prompt.sections.SonosFavoritesSectionGenerator.MusicManager')
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

        from assistant.prompt.sections.SonosFavoritesSectionGenerator import SonosFavoritesSectionGenerator
        section = SonosFavoritesSectionGenerator(None)
        section._wait = mock_time.sleep  # the loop waits on _wait; the test drives it via the mocked sleep
        section._update_content = Mock()

        section._start_monitoring()

        section._update_content.assert_called_once()

    @patch('assistant.prompt.sections.SonosFavoritesSectionGenerator.time')
    @patch('assistant.prompt.sections.SonosFavoritesSectionGenerator.MusicManager')
    def test_start_monitoring_uses_no_room_for_initial_check(self, mock_mm_cls, mock_time):
        mock_mm = Mock()
        mock_mm.get_favorites.return_value = ["Radio 1"]
        mock_mm.get_playlists.return_value = []
        mock_mm_cls.get_instance.return_value = mock_mm

        mock_time.time.return_value = 100.0

        def sleep_and_stop(duration):
            if duration == 30 * 60:
                section._stop_event.set()

        mock_time.sleep.side_effect = sleep_and_stop

        from assistant.prompt.sections.SonosFavoritesSectionGenerator import SonosFavoritesSectionGenerator
        section = SonosFavoritesSectionGenerator(None)
        section._wait = mock_time.sleep  # the loop waits on _wait; the test drives it via the mocked sleep
        section._update_content = Mock()

        section._start_monitoring()

        mock_mm.get_playlists.assert_called_with()
        mock_mm.get_favorites.assert_called_with()
