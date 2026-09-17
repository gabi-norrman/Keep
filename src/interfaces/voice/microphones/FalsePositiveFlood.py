import asyncio

from interfaces.voice.microphones.OutpostServer import outpost_server
from common.logging import note, error
from .speech_analysis.SpeechAnalyzer import SpeechAnalyzer


def report_false_positive_flood():
    """The assistant saw several false-positive wake-ups in a row: we are listening to
    gibberish. Send every outpost back to wake-word mode and have the SpeechAnalyzer
    ignore incoming requests for a few seconds. Installed on Commands.false_positive_reporter
    by the launcher when the voice interface is running."""
    note("False positive flood! We're listening to gibberish - broadcast wakeword to all Outposts and ignore incoming requests.")
    loop = outpost_server.loop
    if loop and loop.is_running() and not loop.is_closed():
        try:
            current_loop = asyncio.get_running_loop()
        except RuntimeError:
            current_loop = None

        try:
            if current_loop is loop:
                # If we're already on the target loop thread, schedule without blocking.
                future = loop.create_task(outpost_server.broadcast_command("wakeword"))
            else:
                future = asyncio.run_coroutine_threadsafe(outpost_server.broadcast_command("wakeword"), loop)
            future.add_done_callback(_log_wakeword_broadcast_failure)
        except Exception as e:
            error(f"Failed to schedule wakeword broadcast: {e}")
    SpeechAnalyzer.notify_false_positive_flood()


def _log_wakeword_broadcast_failure(future):
    try:
        exc = future.exception()
    except Exception as e:
        error(f"Failed to broadcast wakeword: {e}")
        return
    if exc:
        error(f"Failed to broadcast wakeword: {exc}")
