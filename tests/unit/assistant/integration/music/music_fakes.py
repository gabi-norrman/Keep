"""A speaker system for tests: an Integration implementing MusicSystem over in-memory zones, recording what it was asked."""
from assistant.integration.Integration import Integration
from assistant.integration.music.MusicSystem import MusicSystem


class FakeSpeakers(Integration, MusicSystem):
    ID_PREFIXES = ("F-",)

    def __init__(self, zones=(), status=None, library="", missing=(), failure=""):
        super().__init__()
        self._zones = list(zones)
        self._status = list(status) if status is not None else [f"{z} (Paused)" for z in self._zones]
        self._library = library
        self._missing = list(missing)
        self.failure = failure   # what every action answers; "" = done
        self.calls = []

    @property
    def name(self): return "Fake Speakers"
    @property
    def config_keys(self): return ["fake.speakers"]
    def check_health(self): return True
    def missing_config_keys(self): return self._missing

    def zones(self): return self._zones
    def zone_status(self): return self._status
    def library_text(self): return self._library

    def _act(self, *call):
        self.calls.append(call)
        return self.failure

    def play(self, zone): return self._act("play", zone)
    def pause(self, zone): return self._act("pause", zone)
    def next_track(self, zone): return self._act("next_track", zone)
    def adjust_volume(self, zone, step): return self._act("adjust_volume", zone, step)
    def play_id(self, zone, music_id): return self._act("play_id", zone, music_id)


class OtherSpeakers(FakeSpeakers):
    ID_PREFIXES = ("H-",)

    @property
    def name(self): return "Other Speakers"
