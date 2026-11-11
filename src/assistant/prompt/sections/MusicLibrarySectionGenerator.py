import threading
import time

from assistant.integration.music import MusicSystem as music
from assistant.integration.music.SpotifyAccount import SpotifyAccount
from common.logging import trace, fine, info, error

from ..PromptSectionGenerator import PromptSectionGenerator


class MusicLibrarySectionGenerator(PromptSectionGenerator):
    """Keeps the music library the music toolkit carries fresh: every speaker system's own lists, then the user's Spotify playlists.

    It reads the lists the systems and the shared Spotify account cache (reloaded every 30
    minutes, and the Spotify library again right after a sign-in) and never fetches itself:
    checking them once a minute costs nothing, and the base loop skips the update when nothing
    changed. A reload of the Spotify library wakes the check at once (the account's library
    listener), so a fresh sign-in's playlists reach the LLM with the next request rather than a
    minute later. It runs only while a MusicSystem integration is on, so a house without speakers
    never looks for one."""
    key = "music"
    needs = music.MusicSystem
    CHECK_INTERVAL = 60
    FIRST_DATA_WAIT = 60

    def __init__(self, prompt_manager):
        super().__init__(prompt_manager, "MusicLibrary")
        self._wake = threading.Event()   # set by a library reload or stop(): ends the current wait early
        self._on_library_reload = self._wake.set

    def stop(self):
        self._stop_event.set()   # before the wake, so the wait it ends reports the stop
        self._wake.set()
        super().stop()

    def _wait(self, seconds: float) -> bool:
        """The base wait, cut short by a Spotify library reload as well as by stop()."""
        self._wake.wait(seconds)
        self._wake.clear()
        return self._stop_event.is_set()

    @staticmethod
    def _spotify() -> SpotifyAccount:
        return SpotifyAccount.get_instance()

    def _has_data(self) -> bool:
        if self._spotify().playlists():
            return True
        return any(system.has_library() for system in music.active())

    def _start_monitoring(self):
        """Load once the data is in, then follow the cached lists."""
        self._wake.clear()
        SpotifyAccount.add_library_listener(self._on_library_reload)
        try:
            self._await_first_data()
            self._refresh()
        finally:
            SpotifyAccount.remove_library_listener(self._on_library_reload)

    def _await_first_data(self):
        """Poll until a list is loaded (with timeout)."""
        start_time = time.time()
        while not self._stop_event.is_set():
            if self._has_data():
                info("Music library loaded, updating content")
                self._update_content()
                break
            if time.time() - start_time > self.FIRST_DATA_WAIT:
                info(f"No music library after {self.FIRST_DATA_WAIT}s (no speakers found, or Spotify not "
                     f"configured) — section stays empty, checked again every {self.CHECK_INTERVAL}s")
                break
            self._wait(0.1)

    def _refresh(self):
        """Follows the caches: a changed list reaches the prompt within a minute, a reloaded
        Spotify library (a sign-in, the 30-minute reload) at once."""
        while not self._stop_event.is_set():
            self._wait(self.CHECK_INTERVAL)
            if self._stop_event.is_set():
                break
            if self._has_data():
                fine("Music: checking the cached lists for the prompt")
                self._update_content()
            else:
                trace("Music: still no favorites or playlists, section left empty")

    def _fetch_content(self) -> str:
        parts = []
        for system in music.active():
            try:
                text = system.library_text()
            except Exception as e:
                error(f"Music: {system.name} could not list its library: {e}")
                continue
            if text:
                parts.append(text)
        parts.append(self._spotify_playlists())
        # A blank line between the parts: each system's lists, then Spotify's.
        res = "\n\n".join(part.strip() for part in parts if part.strip())
        fine(f"Music library: {len(res.splitlines())} lines")
        return res.strip()

    def _spotify_playlists(self) -> str:
        """The listed part of the signed-in user's Spotify library with S- ids, and how much of
        the library it is."""
        try:
            account = self._spotify()
            playlists = account.playlists()
            res = "The user's own Spotify playlists (own and followed, newest first):\n" if playlists else ""
            for index, playlist in enumerate(playlists):
                by = f" by {playlist.owner}" if playlist.owner else ""
                res += f"{playlist.name}{by} (ID: S-{index})\n"
            total = account.playlists_total()
            if total > len(playlists):
                res += (f"(the {total - len(playlists)} older playlists of the library are not listed; "
                        "search_music finds a public one by name)\n")
            fine(f"Listed {len(playlists)} of the signed-in user's {total} Spotify playlists")
            return res
        except Exception as e:
            error(f"Error listing the user's Spotify playlists: {e}")
            return ""
