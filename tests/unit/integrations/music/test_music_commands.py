from unittest.mock import Mock, patch

import pytest

from assistant.commands.Commands import Commands
from integrations.music.MusicCommands import MusicCommands


@pytest.fixture
def music_manager():
    manager = Mock()
    with patch.object(MusicCommands, '_get_music_manager', return_value=manager):
        yield manager


class TestCmdMusicPlayback:
    def test_volume_up(self, commands_instance, music_manager):
        commands_instance.run("volume_up", ["Living Room"])
        music_manager.adjust_volume.assert_called_once_with("Living Room", 4)

    def test_volume_down(self, commands_instance, music_manager):
        commands_instance.run("volume_down", ["Living Room"])
        music_manager.adjust_volume.assert_called_once_with("Living Room", -4)

    def test_play_music_splits_and_delegates(self, commands_instance, music_manager):
        commands_instance.run("play_music", ["Living Room", "spotify:track:123"])
        music_manager.play_in_zone.assert_called_once_with("spotify:track:123", "Living Room")

    def test_sonos_enqueue_favorite(self, commands_instance, music_manager):
        music_manager.get_favorites.return_value = ["My Fav"]
        commands_instance.run("sonos_enqueue_and_play", ["Kitchen", "My Fav"])
        music_manager.play_favorite.assert_called_once_with("Kitchen", "My Fav")

    def test_sonos_enqueue_playlist(self, commands_instance, music_manager):
        music_manager.get_favorites.return_value = ["Other Fav"]
        commands_instance.run("sonos_enqueue_and_play", ["Kitchen", "My Playlist"])
        music_manager.play_playlist.assert_called_once_with("Kitchen", "My Playlist", shuffle=True)

    def test_building_commands_does_not_build_the_music_integration(self):
        # A disabled Sonos must not be probed at startup: the integration is built by the
        # IntegrationMonitor when enabled, or by the first music command.
        with patch.object(MusicCommands, '_get_music_manager') as build:
            Commands(Mock())
        build.assert_not_called()
