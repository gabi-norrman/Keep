"""Runtime Scribe availability, shared between LocalOutpostManager (which learns at boot
whether ScribeWorker actually came up) and RequestProcessor's wake-word verdict gate.

Distinct from the static SCRIBE_ENABLED constant: Scribe can be enabled in constants yet
unavailable at runtime (missing API key, missing deps), in which case the manager falls
back to Whisper-only. The verdict gate must key off THIS flag — with Scribe enabled but
not running, no verdict can ever arrive, and gating on the constant alone would silently
add the full wait cap to every wake-word reply."""

_scribe_running = False


def set_scribe_running(running: bool):
    global _scribe_running
    _scribe_running = running


def scribe_is_running() -> bool:
    return _scribe_running
