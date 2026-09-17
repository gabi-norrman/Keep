import time
from ..PromptSectionGenerator import PromptSectionGenerator
from common.logging import trace, info, error
from ...integration.integrations.MusicIntegration import MusicManager


class SonosFavoritesSectionGenerator(PromptSectionGenerator):
    """Refreshes the Sonos favorites and playlists section every 30 minutes once Sonos has loaded."""

    def __init__(self, prompt_manager):
        super().__init__(prompt_manager, "SonosFavorites")
        self.playback_manager = MusicManager.get_instance()

    def _start_monitoring(self):
        """Update when Sonos data is available."""
        # Poll until playlists/favorites are loaded (with timeout)
        max_wait = 60  # seconds
        start_time = time.time()

        while not self._stop_event.is_set():
            playlists = self.playback_manager.get_playlists()
            favorites = self.playback_manager.get_favorites()

            if playlists or favorites:
                # Data is available, update content
                info("Sonos data loaded, updating content")
                self._update_content()
                break

            if time.time() - start_time > max_wait:
                info(f"No Sonos favorites or playlists after {max_wait}s (no speakers found, or Spotify not "
                     f"configured) — section stays empty, checked again every 30 minutes")
                break

            self._wait(0.1)

        # Continue with normal 30-minute refresh cycle
        while not self._stop_event.is_set():
            self._wait(30 * 60)  # 30 minutes
            if self._stop_event.is_set():
                break
            if self.playback_manager.get_playlists() or self.playback_manager.get_favorites():
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
            playlists = self.playback_manager.get_playlists()
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
            favorites = self.playback_manager.get_favorites()
            index = 0
            for favorite_name in favorites:
                res += favorite_name + ' (ID: F-' + str(index) + ')\n'
                index += 1
            trace(f"Found {len(favorites)} Sonos favorites")
        except Exception as e:
            error(f"Error fetching Sonos favorites: {e}")
        return res
