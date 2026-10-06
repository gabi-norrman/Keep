from assistant.commands.CommandSet import CommandSet
from common.logging import trace
from integrations.music.MusicIntegration import MusicManager


class MusicCommands(CommandSet):
    """The music commands: playback and volume in a Sonos zone, Spotify search, playlists and favorites."""
    MULTI_PARAM_COMMANDS = frozenset({"play_music", "sonos_enqueue_and_play"})

    music_manager = None

    @classmethod
    def get_music_manager(cls) -> 'MusicManager':
        if cls.music_manager is None:
            cls.music_manager = MusicManager.get_instance()
        return cls.music_manager

    def cmd_search_music(self, parameter_string: str):
        self.background_command(self.music_manager.search_spotify, parameter_string)

    def cmd_play_music(self, *args: str):
        if len(args) < 2:
            return "play_music failed: expected 'zone, id' parameters"
        room = args[0].strip()
        id = args[1].strip()
        self.music_manager.play_in_zone(id, room)

    def cmd_play(self, parameter_string: str):
        self.music_manager.play(parameter_string)

    def cmd_pause(self, parameter_string: str):
        self.music_manager.pause(parameter_string)

    def cmd_next(self, parameter_string: str):
        self.music_manager.next_track(parameter_string)

    def cmd_volume_up(self, parameter_string: str):
        self.music_manager.adjust_volume(parameter_string, 4)

    def cmd_volume_down(self, parameter_string: str):
        self.music_manager.adjust_volume(parameter_string, -4)

    #  todo: this is not in the prompt, do we still want it?
    def cmd_sonos_enqueue_and_play(self, *args: str):
        if len(args) < 2:
            return "sonos_enqueue_and_play failed: expected 'zone, playlist' parameters"
        room = args[0].strip()
        playlist = args[1].strip()

        favorites = MusicCommands.music_manager.get_favorites(room)
        if playlist in favorites:
            trace("Sonos favorite, playing.")
            MusicCommands.music_manager.play_favorite(room, playlist)
        else:
            trace("Sonos playlist, playing.")
            MusicCommands.music_manager.play_playlist(room, playlist, shuffle=True)

    def describe_search_music(self, p):           return f"Searching music for {p.strip()}"
    def describe_play(self, p):                   return f"Playing in {p.strip()}" if p.strip() else "Playing"
    def describe_pause(self, p):                  return f"Pausing in {p.strip()}" if p.strip() else "Pausing"
    def describe_next(self, p):                   return f"Skipping to next track in {p.strip()}" if p.strip() else "Skipping to next track"
    def describe_volume_up(self, p):              return f"Turning up the volume in {p.strip()}" if p.strip() else "Turning up the volume"
    def describe_volume_down(self, p):            return f"Turning down the volume in {p.strip()}" if p.strip() else "Turning down the volume"

    def describe_play_music(self, p):
        room = p.split(",", 1)[0].strip()
        return f"Playing music in {room}" if room else "Playing music"

    def describe_sonos_enqueue_and_play(self, p):
        parts = [x.strip() for x in p.split(",", 1)]
        if len(parts) == 2:
            return f"Playing {parts[1]} in {parts[0]}"
        return "Playing music"
