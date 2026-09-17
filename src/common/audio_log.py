"""Shared helpers for persisting raw audio samples to the logs folder.

Both the Android upload endpoint and the local mic outposts drop debuggable
WAV files into `log/audio/`, named after the spoken query. This module owns the
WAV-writing and folder-size-cap logic so the two paths stay in sync.
"""

import json
import os
import re
import struct
import threading
import wave

from common.logging import error, fine, trace

CLEANUP_EVERY_N_SAVES = 100

# RIFF chunk id for the optional JSON metadata appended after the PCM data (e.g. how much
# of the utterance Whisper actually decoded). Travelling inside the WAV means it survives
# the clip being copied into a test-set folder, which a sidecar file would not. Readers
# that don't know it (soundfile/libsndfile, Python's wave) skip it.
METADATA_CHUNK_ID = b"flxy"

_counter_lock = threading.Lock()
_saves_by_folder = {}
_cleanup_lock = threading.Lock()
# Serializes the pick-a-free-filename + write pair: each save runs on its own
# thread now, so two mics saving the same phrase concurrently would otherwise
# both pick name.wav and one would overwrite the other's debug WAV.
_save_lock = threading.Lock()


def _cleanup_due(folder_path: str) -> bool:
    """Scanning the folder (100k+ stat calls at the size cap) on every save is too
    expensive, so run it on the first save per folder after startup, then every Nth."""
    with _counter_lock:
        count = _saves_by_folder.get(folder_path, 0) + 1
        _saves_by_folder[folder_path] = count
    return count == 1 or count % CLEANUP_EVERY_N_SAVES == 0


def cleanup_audio_folder(folder_path: str, max_size_gb: float = 10):
    """Remove oldest files if folder size exceeds max_size_gb. Passes are serialized:
    concurrent saves on the per-mic WavSave threads used to race here and
    double-delete the same oldest files. A pass that queues behind another one
    re-scans and usually finds the folder already under cap, which is cheap."""
    with _cleanup_lock:
        _cleanup_audio_folder_locked(folder_path, max_size_gb)


def _cleanup_audio_folder_locked(folder_path: str, max_size_gb: float):
    max_size_bytes = max_size_gb * 1024 * 1024 * 1024

    files = []
    total_size = 0
    for f in os.listdir(folder_path):
        filepath = os.path.join(folder_path, f)
        try:
            stat = os.stat(filepath)
        except OSError:
            continue
        if os.path.isfile(filepath):
            files.append((filepath, stat.st_mtime, stat.st_size))
            total_size += stat.st_size

    if total_size <= max_size_bytes:
        trace(f"Audio folder {folder_path} within {max_size_gb}GB cap, nothing to clean.")
        return

    files.sort(key=lambda x: x[1])

    deleted = 0
    while total_size > max_size_bytes and files:
        filepath, _, size = files.pop(0)
        try:
            os.remove(filepath)
            total_size -= size
            deleted += 1
        except FileNotFoundError:
            total_size -= size
        except OSError as e:
            error(f"Failed to delete {filepath}: {e}")
    fine(f"Audio folder {folder_path} over {max_size_gb}GB cap, deleted {deleted} oldest files.")


def save_pcm_wav(audio_dir: str, name: str, pcm_bytes: bytes,
                 sample_rate: int = 16000, channels: int = 1, bit_depth: int = 16,
                 max_size_gb: float = 10, metadata: dict | None = None) -> str:
    """Write 16-bit PCM bytes as a WAV named after `name` (sanitized).

    Dedupes a name collision with a numeric suffix (`name_1.wav`, …), and
    enforces the folder size cap every CLEANUP_EVERY_N_SAVES saves. `metadata`,
    when given, is stored as JSON inside the file (see METADATA_CHUNK_ID /
    read_wav_metadata). Returns the path written.
    """
    os.makedirs(audio_dir, exist_ok=True)

    base_name = re.sub(r'[^a-zA-Z0-9]', '_', name)[:100]
    with _save_lock:
        filepath = os.path.join(audio_dir, f"{base_name}.wav")
        counter = 1
        while os.path.exists(filepath):
            filepath = os.path.join(audio_dir, f"{base_name}_{counter}.wav")
            counter += 1

        with wave.open(filepath, 'wb') as wf:
            wf.setnchannels(channels)
            wf.setsampwidth(bit_depth // 8)
            wf.setframerate(sample_rate)
            wf.writeframes(pcm_bytes)
        if metadata:
            _append_metadata_chunk(filepath, metadata)

    if _cleanup_due(audio_dir):
        cleanup_audio_folder(audio_dir, max_size_gb)
    return filepath


def _append_metadata_chunk(filepath: str, metadata: dict):
    """Append a `flxy` RIFF chunk holding `metadata` as JSON, and grow the RIFF size
    field to cover it. Appended after `data`, so the PCM offsets are untouched."""
    payload = json.dumps(metadata, separators=(",", ":")).encode("utf-8")
    chunk = METADATA_CHUNK_ID + struct.pack("<I", len(payload)) + payload
    if len(payload) % 2:
        chunk += b"\0"   # RIFF chunks are word-aligned
    with open(filepath, "r+b") as f:
        f.seek(0, os.SEEK_END)
        f.write(chunk)
        riff_size = f.tell() - 8
        f.seek(4)
        f.write(struct.pack("<I", riff_size))


def read_wav_metadata(filepath: str) -> dict:
    """The JSON stored by save_pcm_wav(metadata=…), or {} when the WAV carries none
    (clips recorded before the chunk existed, or made by other tools)."""
    with open(filepath, "rb") as f:
        header = f.read(12)
        if len(header) < 12 or header[:4] != b"RIFF" or header[8:] != b"WAVE":
            return {}
        while True:
            head = f.read(8)
            if len(head) < 8:
                return {}
            chunk_id, size = head[:4], struct.unpack("<I", head[4:])[0]
            if chunk_id == METADATA_CHUNK_ID:
                try:
                    return json.loads(f.read(size).decode("utf-8"))
                except (ValueError, UnicodeDecodeError):
                    return {}
            f.seek(size + (size % 2), os.SEEK_CUR)
