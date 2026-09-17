"""Local microphone outposts.

Server-internal outposts: one per connected microphone. They capture audio,
transcribe it with a shared Whisper (MLX) instance, and push the resulting text
to the server's /stream endpoint just like a remote Android/iOS outpost would.
The existing content-based voting in SpeechAnalyzer then treats them as peers of
the remote outposts.
"""

from .LocalOutpostManager import LocalOutpostManager
from .bootstrap import start_local_outposts

__all__ = ["LocalOutpostManager", "start_local_outposts"]
