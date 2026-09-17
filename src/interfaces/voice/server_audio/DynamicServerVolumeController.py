import subprocess
import threading
import time
from collections import deque
from typing import Optional

import numpy as np
import sounddevice as sd

from common.ConfigManager import config
from common.DevelopmentConfig import LOCAL_DEVELOPMENT_MODE
from common.logging import trace, info, error
from assistant.AssistantEvents import observers


class DynamicServerVolumeController:
    """Monitors ambient noise on the server mic and adjusts the system volume to match."""

    _instance: Optional['DynamicServerVolumeController'] = None

    @classmethod
    def get_instance(cls, **kwargs) -> 'DynamicServerVolumeController':
        if cls._instance is None:
            cls._instance = cls(**kwargs)
        return cls._instance

    @classmethod
    def reset_instance(cls):
        cls._instance = None

    def __init__(
            self,
            sample_rate: int = 16000,
            audio_block_duration: float = 0.1,  # in seconds
            history_seconds: float = 1.0,
            update_interval: float = 1.0,  # Update volume X seconds
            min_noise_db: float = 20.0,  # new mic, must make this dynamic!
            max_noise_db: float = 50.0,
            min_volume: int = 55,
            max_volume: int = 100,
            device_index: Optional[int] = None,
            prompt_manager=None
    ):
        self.sample_rate = sample_rate
        self.block_size = int(sample_rate * audio_block_duration)
        self.history_size = int(history_seconds / audio_block_duration)
        self.update_interval = update_interval

        # Noise and volume bounds
        self.min_noise_db = min_noise_db
        self.max_noise_db = max_noise_db
        self.min_volume = min_volume
        self.max_volume = max_volume

        # Device selection
        self.device_index = device_index

        self.prompt_manager = prompt_manager

        # State
        self.noise_history = deque(maxlen=self.history_size)
        self.current_noise_db = 40.0  # Start with reasonable default
        self.current_volume = 50
        self.smoothed_noise_db = 40.0

        # Control flags
        self.monitoring = False
        self.paused = False
        self._stop_event = threading.Event()
        self._monitor_thread = None
        self._volume_thread = None
        self._pause_timer = None

    @staticmethod
    def rms_to_db(rms: float) -> float:
        """Convert RMS value to decibels."""
        # Add small value to avoid log(0), use 1e-10 as reference
        return 20 * np.log10(max(rms, 1e-10) / 1e-4)  # Using 1e-4 as reference for better scaling

    def noise_to_volume(self, noise_db: float) -> int:
        """
        Convert noise level in dB to system volume percentage.
        Uses a sigmoid-like curve for smooth transitions.
        """
        # Normalize noise to 0-1 range
        normalized = (noise_db - self.min_noise_db) / (self.max_noise_db - self.min_noise_db)
        normalized = np.clip(normalized, 0, 1)

        # Map to volume range
        volume = self.min_volume + (self.max_volume - self.min_volume) * normalized

        return int(np.clip(volume, self.min_volume, self.max_volume))

    @staticmethod
    def calculate_rms(audio_chunk: np.ndarray) -> float:
        """Calculate RMS (Root Mean Square) of audio chunk."""
        return np.sqrt(np.mean(audio_chunk ** 2))

    @staticmethod
    def set_system_volume(volume: int) -> bool:
        """
        Set macOS system volume (0-100).
        Returns True if successful.
        """
        try:
            # Use osascript to set system volume on macOS
            subprocess.run(
                ["osascript", "-e", f"set volume output volume {volume}"],
                check=True,
                capture_output=True
            )
            return True
        except subprocess.CalledProcessError:
            return False

    def audio_callback(self, indata, frames, time_info, status):
        """Callback for processing audio input."""
        if status:
            trace(f"Audio callback status: {status}")

        if not self.paused and self.monitoring:
            # Calculate RMS and convert to dB
            rms = self.calculate_rms(indata[:, 0])  # Use first channel
            db = self.rms_to_db(rms)

            # Add to history
            self.noise_history.append(db)

    def _resolve_input_device(self) -> Optional[int]:
        """
        Return a usable input device index, or None if the configured/default
        device isn't available. sounddevice returns -1 for "no default device",
        which makes sd.InputStream raise PortAudioError, so we treat that as
        "no device yet".
        """
        if self.device_index is not None:
            return self.device_index
        try:
            default_input = sd.default.device[0]
        except Exception:
            return None
        if default_input is None or default_input < 0:
            return None
        return self._skip_excluded(default_input)

    @staticmethod
    def _skip_excluded(default_input: int) -> Optional[int]:
        """local_outposts.exclude_mics means "never listen on this mic" — for the noise monitor
        too, or a Bluetooth headset that macOS made the default input stays open (and stuck in
        its call profile) after the user excluded it. Falls back to the first allowed mic."""
        exclude = config.get('local_outposts.exclude_mics', [])
        if not exclude:
            return default_input
        from interfaces.voice.microphones.local_outposts.LocalOutpostManager import filter_input_devices
        allowed = filter_input_devices(sd.query_devices(), exclude)
        if any(idx == default_input for idx, _ in allowed):
            return default_input
        if not allowed:
            trace(f"Dynamic volume: default input #{default_input} is excluded and no other mic is allowed.")
            return None
        idx, name = allowed[0]
        trace(f"Dynamic volume: default input #{default_input} is excluded — using #{idx} '{name}' instead.")
        return idx

    def _monitor_noise(self):
        """Background thread for monitoring noise levels."""
        if LOCAL_DEVELOPMENT_MODE:
            # Testing mode with random noise
            import random
            while not self._stop_event.is_set():
                self.noise_history.append(random.uniform(20, 60))
                time.sleep(0.1)
            return

        # The audio input device may not be available at startup (e.g. the
        # server boots before the audio interface is ready). Keep retrying so
        # noise-based volume control recovers once a device appears, instead of
        # letting the thread die with an unhandled PortAudioError.
        backoff = 1.0
        warned = False
        while not self._stop_event.is_set():
            device = self._resolve_input_device()
            if device is None:
                if not warned:
                    error("Dynamic volume: no audio input device available, retrying in background...")
                    warned = True
                self._stop_event.wait(backoff)
                backoff = min(backoff * 2, 30.0)
                continue

            try:
                with sd.InputStream(
                        device=device,
                        channels=1,
                        samplerate=self.sample_rate,
                        blocksize=self.block_size,
                        callback=self.audio_callback
                ):
                    info(f"Dynamic volume: monitoring ambient noise on input device #{device}.")
                    warned = False
                    backoff = 1.0
                    while not self._stop_event.is_set():
                        self._stop_event.wait(0.1)
            except sd.PortAudioError as e:
                if not warned:
                    error(f"Dynamic volume: failed to open audio input ({e}), retrying in background...")
                    warned = True
                self._stop_event.wait(backoff)
                backoff = min(backoff * 2, 30.0)

    def _update_volume(self):
        """Background thread for updating system volume."""
        while not self._stop_event.is_set():
            if self.monitoring and not self.paused and len(self.noise_history) > 0:
                # Calculate average noise level
                self.current_noise_db = float(np.mean(self.noise_history))

                self.smoothed_noise_db = self.current_noise_db  # no need to smooth this, we'll make sure volume moves smoothly

                # Calculate target volume
                target_volume = self.noise_to_volume(self.smoothed_noise_db)

                diff = abs(self.current_volume - target_volume)
                if diff > 2:  # if diff is too large then just move towards the target
                    if target_volume > self.current_volume:
                        target_volume = self.current_volume + min(6, diff)  # move faster upward in volume
                    else:
                        target_volume = self.current_volume - min(2, diff)

                # Only update if change is significant
                if not LOCAL_DEVELOPMENT_MODE and abs(target_volume - self.current_volume) > 1:
                    if self.set_system_volume(target_volume):
                        self.current_volume = target_volume

                observers().on_server_volume(self.current_noise_db, target_volume)

            # Wait before next update
            time.sleep(self.update_interval)

    def start(self):
        """Start monitoring and adjusting volume."""
        if self.monitoring:
            return

        info(f"Dynamic volume controller starting...")

        self.monitoring = True
        self._stop_event.clear()

        # Start monitoring thread
        self._monitor_thread = threading.Thread(target=self._monitor_noise)
        self._monitor_thread.daemon = True
        self._monitor_thread.start()

        # Start volume update thread
        self._volume_thread = threading.Thread(target=self._update_volume)
        self._volume_thread.daemon = True
        self._volume_thread.start()

        trace(f"Noise range: {self.min_noise_db:.0f}dB - {self.max_noise_db:.0f}dB")
        trace(f"Volume range: {self.min_volume}% - {self.max_volume}%")

    def stop(self):
        """Stop monitoring and adjusting volume."""
        self.monitoring = False
        self._stop_event.set()

        if self._pause_timer:
            self._pause_timer.cancel()
            self._pause_timer = None

        if self._monitor_thread:
            self._monitor_thread.join(timeout=1)
        if self._volume_thread:
            self._volume_thread.join(timeout=1)

    def pause(self):
        """Pause monitoring for 5 seconds (e.g., while AI is speaking). Resets timer if called again."""
        if self._pause_timer:
            self._pause_timer.cancel()

        self.paused = True

        # Create new timer to resume after 5 seconds
        self._pause_timer = threading.Timer(5.0, self._auto_resume)
        self._pause_timer.daemon = True
        self._pause_timer.start()

    def _auto_resume(self):
        """Automatically resume monitoring after pause timeout."""
        self.paused = False
        self._pause_timer = None
