"""Startup helper for server-internal mic outposts.

Kept out of the server's startup block so the boot logic lives next to the
package it starts. See LocalOutpostManager for the actual outpost machinery.
"""

import threading
import traceback

from common.logging import info, error, trace
from common.ConfigManager import config
from interfaces.voice.microphones.OutpostServer import outpost_server
from interfaces.voice.microphones.local_outposts.LocalOutpostManager import LocalOutpostManager
from interfaces.voice.microphones.local_outposts.constants import DEFAULT_MODEL_REPO
from interfaces.voice.tts.VoiceSynthesizer import VoiceSynthesizer


def start_local_outposts(port, running_as_testsuite=False):
    """Spin up server-internal mic outposts if enabled. Boots on a background thread
    because warming up the Whisper model can take a few seconds — we don't want to
    delay the web server coming up. Returns the manager (for clean shutdown) or None."""
    if running_as_testsuite:
        return None
    # On by default: a server that enables voice usually wants its own mics; a phone-only
    # server sets local_outposts.enabled: false. No mic or no MLX just logs an error below.
    if not config.get('local_outposts.enabled', True):
        info("Local mic outposts disabled (local_outposts.enabled is false).")
        return None

    manager = LocalOutpostManager(
        port,
        model_repo=config.get('local_outposts.model_repo', DEFAULT_MODEL_REPO),
        languages=VoiceSynthesizer.configured_languages(),
        exclude_mics=config.get('local_outposts.exclude_mics', []),
        auto_recovery=config.get('local_outposts.auto_recovery', True),
        usb_power_cycle=config.get('local_outposts.usb_power_cycle', True),
    )
    # Register before booting so commands arriving mid-startup are handled (no-op
    # until the manager is active).
    outpost_server.add_command_listener(manager.on_command)

    def _boot():
        try:
            manager.start()
        except Exception as e:
            error(f"Failed to start local outposts: {e}")
            trace(traceback.format_exc())

    threading.Thread(target=_boot, name="LocalOutpostsBoot", daemon=True).start()
    return manager
