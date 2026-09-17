from __future__ import annotations

import logging
import threading
import time
from collections import Counter
from pathlib import Path
from typing import Dict, List, Tuple, Optional, TYPE_CHECKING, ClassVar

if TYPE_CHECKING:
    import numpy as np
    import torch

from common.logging import info, trace, error, fine
from common.Paths import VOICE_ID_DIR as _VOICE_ID_DIR

# These get loaded in SpeakerIdentifier.__init__ (which runs in background thread)
np = None
torch = None
F = None
torchaudio = None


class _FloxyLoggingHandler(logging.Handler):
    """Routes Python logging to Floxy's logging system."""

    def emit(self, record):
        if record.levelno < logging.INFO:
            return  # Skip DEBUG and below
        msg = self.format(record)
        if record.levelno >= logging.ERROR:
            error(f"[speechbrain] {msg}")
        elif record.levelno >= logging.WARNING:
            trace(f"[speechbrain] {msg}")
        else:
            fine(f"[speechbrain] {msg}")


def _setup_speechbrain_logging():
    """Redirect SpeechBrain and related library logs to Floxy logging."""
    handler = _FloxyLoggingHandler()
    handler.setFormatter(logging.Formatter('%(message)s'))

    for logger_name in ['speechbrain', 'hyperpyyaml', 'torch', 'torchaudio']:
        logger = logging.getLogger(logger_name)
        logger.handlers.clear()
        logger.addHandler(handler)
        logger.propagate = False


_setup_speechbrain_logging()


class SpeakerIdentifier:
    """Identifies who is speaking from an audio sample using SpeechBrain's ECAPA-TDNN model.

    Enrolls users from the WAV samples in voice_id/<username>/ directories.
    """

    _instance: ClassVar[Optional['SpeakerIdentifier']] = None

    VOICE_ID_DIR = _VOICE_ID_DIR
    MODEL_DIR = Path(__file__).parent / "pretrained_models"
    UNKNOWN_THRESHOLD = 0.35  # lower than around 31% gives false identifications
    K_NEAREST = 3
    IDENTIFICATION_VOTE_WINDOW = 30.0  # seconds to consider for voting

    @classmethod
    def get_instance(cls) -> Optional['SpeakerIdentifier']:
        return cls._instance

    @classmethod
    def reset_instance(cls):
        cls._instance = None

    @classmethod
    def create_instance(cls) -> 'SpeakerIdentifier':
        cls._instance = cls()
        return cls._instance

    def __init__(self):
        # Import heavy ML libraries here (runs in background thread)
        global np, torch, F, torchaudio
        import numpy as np_
        import torch as torch_
        import torch.nn.functional as F_
        import torchaudio as torchaudio_
        np = np_
        torch = torch_
        F = F_
        torchaudio = torchaudio_

        self._classifier = None
        self._enrolled_users: Dict[str, List[torch.Tensor]] = {}
        self._identification_history: List[Tuple[str, float, float]] = []  # (username, confidence, timestamp)
        # Guards _identification_history: multiple local mic outposts can call identify()
        # concurrently (one send thread per mic), interleaving append + prune.
        self._history_lock = threading.Lock()
        self._enroll_users()

    def _ensure_model_loaded(self):
        if self._classifier is not None:
            return

        from speechbrain.inference.speaker import EncoderClassifier

        info("Loading SpeechBrain speaker recognition model...")
        self._classifier = EncoderClassifier.from_hparams(
            source="speechbrain/spkrec-ecapa-voxceleb",
            savedir=str(self.MODEL_DIR)
        )
        trace("SpeechBrain model loaded.")

    def _enroll_users(self):
        """Scan voice_id/ directory and enroll all users."""
        if not self.VOICE_ID_DIR.exists():
            info(f"Voice ID directory not found: {self.VOICE_ID_DIR}")
            info("Create voice_id/<username>/ directories with WAV samples to enroll users.")
            return

        self._ensure_model_loaded()

        for user_dir in self.VOICE_ID_DIR.iterdir():
            if not user_dir.is_dir():
                continue

            username = user_dir.name
            wav_files = list(user_dir.glob("*.wav"))

            if not wav_files:
                fine(f"No WAV files found for user: {username}")
                continue

            embeddings = []
            for wav_file in wav_files:
                try:
                    embedding = self._extract_embedding_from_file(wav_file)
                    if embedding is not None:
                        embeddings.append(embedding)
                except Exception as e:
                    fine(f"Failed to process {wav_file.name}: {e}")

            if not embeddings:
                error(f"No valid embeddings for user: {username}")
                continue

            # Normalize and store all embeddings for k-NN matching
            normalized = [F.normalize(emb, p=2, dim=-1) for emb in embeddings]
            self._enrolled_users[username] = normalized
            trace(f"Enrolled user '{username}' with {len(embeddings)} samples")

        info(f"Speaker identification ready: {len(self._enrolled_users)} users enrolled")

    def _extract_embedding_from_file(self, wav_path: Path) -> torch.Tensor | None:
        """Extract speaker embedding from a WAV file."""
        signal, sample_rate = torchaudio.load(str(wav_path))

        # Convert to mono if stereo
        if signal.shape[0] > 1:
            signal = torch.mean(signal, dim=0, keepdim=True)

        # Resample to 16kHz if needed
        if sample_rate != 16000:
            resampler = torchaudio.transforms.Resample(sample_rate, 16000)
            signal = resampler(signal)

        # Extract embedding
        embedding = self._classifier.encode_batch(signal)
        return embedding.squeeze()

    def _extract_embedding_from_pcm(self, audio_data: bytes, sample_rate: int = 16000,
                                    bit_depth: int = 16) -> torch.Tensor | None:
        """Extract speaker embedding from raw PCM audio data."""
        # Convert PCM bytes to numpy array normalized to [-1, 1]
        if bit_depth == 16:
            audio_np = np.frombuffer(audio_data, dtype=np.int16).astype(np.float32) / 32768.0
        elif bit_depth == 8:
            # 8-bit PCM is unsigned (0-255) with silence at 128, so center around 0
            audio_np = (np.frombuffer(audio_data, dtype=np.uint8).astype(np.float32) - 128.0) / 128.0
        else:
            error(f"Unsupported bit depth: {bit_depth}")
            return None
        signal = torch.from_numpy(audio_np).unsqueeze(0)  # Add channel dim

        # Resample if needed
        if sample_rate != 16000:
            resampler = torchaudio.transforms.Resample(sample_rate, 16000)
            signal = resampler(signal)

        # Extract embedding
        embedding = self._classifier.encode_batch(signal)
        return embedding.squeeze()

    def identify(self, audio_data: bytes, sample_rate: int = 16000,
                 bit_depth: int = 16, record: bool = True) -> Tuple[str, float]:
        """
        Identify the speaker from raw PCM audio data.

        Args:
            audio_data: Raw PCM audio bytes
            sample_rate: Sample rate in Hz (default 16000)
            bit_depth: Bits per sample (default 16)
            record: When True (default), a confident match is added to the voting
                history that get_last_identified_user() reads. Set False to compute and
                log the speaker WITHOUT updating the identified user — used by the local
                mic outposts, which identify speculatively on all background speech and
                must not attribute the identity unless a wake word was actually detected
                (recorded later via record_identification()).

        Returns:
            Tuple of (username, confidence). Returns ("unknown", 0.0) if no match.
        """
        if not self._enrolled_users:
            return ("unknown", 0.0)

        self._ensure_model_loaded()

        embedding = self._extract_embedding_from_pcm(audio_data, sample_rate, bit_depth)
        if embedding is None:
            return ("unknown", 0.0)

        # Normalize
        embedding = F.normalize(embedding.unsqueeze(0), p=2, dim=-1).squeeze()

        # Compare to all enrolled users using k-NN (top-k average similarity)
        best_match = "unknown"
        best_score = -1.0

        for username, enrolled_embs in self._enrolled_users.items():
            # Compute similarity to all embeddings for this user
            similarities = [
                F.cosine_similarity(embedding.unsqueeze(0), emb.unsqueeze(0)).item()
                for emb in enrolled_embs
            ]
            # Average top-k similarities
            top_k = sorted(similarities, reverse=True)[:self.K_NEAREST]
            score = sum(top_k) / len(top_k)

            if score > best_score:
                best_score = score
                best_match = username

        if best_score < self.UNKNOWN_THRESHOLD:
            # Runs speculatively on every utterance (incl. background speech with no wake
            # word), so a sub-threshold score is the common no-op outcome — fine().
            fine(f"Could not identify speaker reliably ({best_match} {best_score:.2f}).")
            return "unknown", best_score

        if record:
            self.record_identification(best_match, best_score)
        return best_match, best_score

    def record_identification(self, username: str, confidence: float):
        """Add a confident identification to the voting history that
        get_last_identified_user() reads. Lets a caller defer recording until it knows
        the identity should count (e.g. a local outpost confirming a wake word) after a
        prior identify(record=False) computed the speaker. A no-op for 'unknown'."""
        if not username or username == "unknown":
            return
        with self._history_lock:
            self._identification_history.append((username, confidence, time.time()))
            self._prune_old_identifications()

    def _prune_old_identifications(self):
        """Remove identifications older than the vote window. Caller must hold
        _history_lock."""
        cutoff = time.time() - self.IDENTIFICATION_VOTE_WINDOW
        self._identification_history = [
            (user, conf, ts) for user, conf, ts in self._identification_history
            if ts >= cutoff
        ]

    def get_last_identified_user(self, max_age_seconds: float = 5.0) -> Optional[str]:
        """
        Return the identified user based on voting over recent identifications.

        Uses all identifications within the vote window (30 seconds) to determine
        the most likely user. Returns None if there's a tie or no recent identifications.

        Args:
            max_age_seconds: Only consider identifications within this time window
        """
        with self._history_lock:
            self._prune_old_identifications()
            cutoff = time.time() - max_age_seconds
            recent = [user for user, conf, ts in self._identification_history if ts >= cutoff]

        if not recent:
            return None

        vote_counts = Counter(recent)
        most_common = vote_counts.most_common(2)

        if len(most_common) == 1:
            return most_common[0][0]

        # Check for tie
        if most_common[0][1] == most_common[1][1]:
            fine(f"Speaker identification tie: {most_common[0][0]} vs {most_common[1][0]} ({most_common[0][1]} votes each)")
            return None

        return most_common[0][0]

    def clear_last_identified_user(self):
        trace("Clearing last identified user")
        with self._history_lock:
            self._identification_history.clear()

