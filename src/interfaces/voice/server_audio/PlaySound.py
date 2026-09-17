import glob
import os
import random
import subprocess
import time

import sounddevice as sd
import soundfile as sf

from common.logging import trace


class PlaySound:
    """Plays the server's short UI sounds: the end-of-reply chime and per-language thinking fillers."""

    last_time_sound_played_on_server: float = time.time()

    _instance: 'PlaySound | None' = None

    @classmethod
    def get_instance(cls) -> 'PlaySound':
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def __init__(self):
        self.end_sample, self.fs2 = sf.read('res/end.wav')
        self.end_sample *= 0.2
        # Thinking fillers played when the LLM opens a <thinking> block, grouped by
        # language subtag from their folder name (res/thinking/<lang>/*.wav, e.g.
        # res/thinking/en, res/thinking/sv). Listed once here (the set is static for
        # the server's lifetime); afplay reads the chosen file at play time.
        self.thinking_paths_by_lang: dict[str, list[str]] = {}
        for path in sorted(glob.glob(os.path.join('res', 'thinking', '*', '*.wav'))):
            lang = os.path.basename(os.path.dirname(path)).lower()
            self.thinking_paths_by_lang.setdefault(lang, []).append(path)
        PlaySound._instance = self

    def playsound_end(self):
        sd.play(self.end_sample, self.fs2, latency='low')
        PlaySound.last_time_sound_played_on_server = time.time()

    def playsound_thinking(self, lang=None):
        """Play a random short "thinking" filler for `lang` via macOS `afplay`, in
        its own process — robust against audio-callback underruns while tokens
        stream (unlike sd.play, which glitches under that load). `lang` is a BCP-47
        tag or subtag (e.g. 'sv-SE', 'sv'); only the subtag is used. No-op when the
        language has no samples — we stay silent rather than play a wrong-language
        filler."""
        code = (lang or "").split("-")[0].lower()
        paths = self.thinking_paths_by_lang.get(code)
        if not paths:
            trace(f"PlaySound.thinking: no samples for lang={code!r}")
            return
        path = random.choice(paths)
        trace("PlaySound.thinking: " + path)
        subprocess.Popen(['afplay', path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        PlaySound.last_time_sound_played_on_server = time.time()
