import time
from assistant.prompt.PromptSectionGenerator import PromptSectionGenerator
from common.logging import trace, info, error
from integrations.music.MusicIntegration import MusicManager


class SonosFavoritesSectionGenerator(PromptSectionGenerator):
    """Refreshes the Sonos favorites and playlists section every 30 minutes once Sonos has loaded.

    It reads the integration the IntegrationMonitor built and never builds one; it runs only
    while Sonos & Spotify is on, so a house without it never looks for a speaker."""
    key = "sonos"
    heading = "# SONOS PLAYLISTS AND ZONES"
    intro = "Playlists and favorites the `music` toolkit can start (ids `P-0`, `F-0`, …), curated by the user:"

    def __init__(self, prompt_manager):
        super().__init__(prompt_manager, "SonosFavorites")

    @property
    def playback_manager(self):
        """The running integration, or None while it is being rebuilt (restart_integration)."""
        return MusicManager.peek_instance()

    def _playlists(self) -> list:
        manager = self.playback_manager
        return manager.get_playlists() if manager is not None else []

    def _favorites(self) -> list:
        manager = self.playback_manager
        return manager.get_favorites() if manager is not None else []

    def _start_monitoring(self):
        """Load once the integration's data is in, then refresh every 30 minutes."""
        self._await_first_data()
        self._refresh()

    def _await_first_data(self):
        """Poll until playlists/favorites are loaded (with timeout)."""
        max_wait = 60  # seconds
        start_time = time.time()

        while not self._stop_event.is_set():
            if self._playlists() or self._favorites():
                info("Sonos data loaded, updating content")
                self._update_content()
                break

            if time.time() - start_time > max_wait:
                info(f"No Sonos favorites or playlists after {max_wait}s (no speakers found, or Spotify not "
                     f"configured) — section stays empty, checked again every 30 minutes")
                break

            self._wait(0.1)

    def _refresh(self):
        """The 30-minute refresh cycle."""
        while not self._stop_event.is_set():
            self._wait(30 * 60)  # 30 minutes
            if self._stop_event.is_set():
                break
            if self._playlists() or self._favorites():
                self._update_content()
            else:
                trace("Sonos: still no favorites or playlists, section left empty")

    def _fetch_content(self) -> str:
        """Fetch Sonos favorites and playlists."""
        info("Fetching Sonos data...")
        res = self._fetch_sonos_favorites()
        res = self._fetch_sonos_playlists(res)
        trace(f"Successfully fetched {len(res.splitlines())} lines of Sonos favorites and playlists.")
        return res.strip()

    def _fetch_sonos_playlists(self, res) -> str:
        """Fetch Sonos playlists and append to result."""
        trace("Fetching Sonos playlists...")
        try:
            playlists = self._playlists()
            index = 0
            for playlist_name in playlists:
                res += playlist_name + ' (ID: P-' + str(index) + ')\n'
                index += 1
            trace(f"Found {len(playlists)} Sonos playlists")
        except Exception as e:
            error(f"Error fetching Sonos playlists: {e}")
        return res

    def _fetch_sonos_favorites(self) -> str:
        """Fetch Sonos favorites."""
        trace("Fetching Sonos favorites...")
        res = ""
        try:
            favorites = self._favorites()
            index = 0
            for favorite_name in favorites:
                res += favorite_name + ' (ID: F-' + str(index) + ')\n'
                index += 1
            trace(f"Found {len(favorites)} Sonos favorites")
        except Exception as e:
            error(f"Error fetching Sonos favorites: {e}")
        return res
