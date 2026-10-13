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
    # The enrollment folders as last read (name, mtime); None = not tracking the disk, which is
    # what a double built without __init__ gets.
    _enrollment_seen: tuple | None = None

    VOICE_ID_DIR = _VOICE_ID_DIR
    MODEL_DIR = Path(__file__).parent / "pretrained_models"
    UNKNOWN_THRESHOLD = 0.35  # lower than around 31% gives false identifications
    K_NEAREST = 3
    IDENTIFICATION_VOTE_WINDOW = 30.0  # seconds to consider for voting
    # The discovery's candidates live under voice_id/ too (VoiceCandidates); not an enrollment.
    CANDIDATES_FOLDER = "candidates"
    # What the discovery embeds: a clip shorter or quieter than this carries no voice, and halves
    # less alike than this hold two voices (see VoiceDiscovery for the measurements).
    CLIP_MIN_DURATION = 2.0
    CLIP_MIN_RMS = 0.003
    CLIP_MIXED_SIM = 0.0

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
        self._candidate_ids: set[str] = set()
        self._identification_history: List[Tuple[str, float, float]] = []  # (username, confidence, timestamp)
        # Guards _identification_history: multiple local mic outposts can call identify()
        # concurrently (one send thread per mic), interleaving append + prune.
        self._history_lock = threading.Lock()
        self._enrollment_seen = ()
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

    def _enrollment_version(self) -> tuple:
        """Changes when an enrollment folder is added, removed or written to — `name_voice`
        moving a candidate's clips into voice_id/<name>/."""
        try:
            folders = [d for d in self.VOICE_ID_DIR.iterdir() if d.is_dir()]
            folders += [d for d in folders if d.name == self.CANDIDATES_FOLDER for d in d.iterdir() if d.is_dir()]
            return tuple(sorted((d.name, d.stat().st_mtime_ns) for d in folders))
        except OSError:
            return ()

    def _reload_if_changed(self):
        """Re-enroll when voice_id/ changed on disk, so a voice the owner just named is
        recognised without a restart."""
        if self._enrollment_seen is None:
            return
        version = self._enrollment_version()
        if version != self._enrollment_seen:
            info("Voice enrollment changed on disk, re-enrolling")
            self._enrolled_users = {}
            self._enroll_users()

    def _enroll_users(self):
        """Scan voice_id/ directory and enroll all users."""
        if self._enrollment_seen is not None:
            self._enrollment_seen = self._enrollment_version()
        if not self.VOICE_ID_DIR.exists():
            info(f"Voice ID directory not found: {self.VOICE_ID_DIR}")
            info("Create voice_id/<username>/ directories with WAV samples to enroll users.")
            return

        self._ensure_model_loaded()

        candidates_dir = None
        for user_dir in self.VOICE_ID_DIR.iterdir():
            if not user_dir.is_dir():
                continue
            if user_dir.name == self.CANDIDATES_FOLDER:
                candidates_dir = user_dir
                continue
            self._enroll(user_dir.name, list(user_dir.glob("*.wav")))

        # The discovery's unnamed voices are enrolled under their id, so the one speaking is
        # recognised and the assistant can ask who they are (routes.py says so in the turn note).
        self._candidate_ids = set()
        if candidates_dir is not None:
            for folder in candidates_dir.iterdir():
                if folder.is_dir() and self._enroll(folder.name, list(folder.glob("*.wav"))):
                    self._candidate_ids.add(folder.name)

        info(f"Speaker identification ready: {len(self._enrolled_users) - len(self._candidate_ids)} users and "
             f"{len(self._candidate_ids)} unnamed voices enrolled")

    def _enroll(self, username: str, wav_files: list) -> bool:
        if not wav_files:
            fine(f"No WAV files found for user: {username}")
            return False

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
            return False

        # Normalize and store all embeddings for k-NN matching
        normalized = [F.normalize(emb, p=2, dim=-1) for emb in embeddings]
        self._enrolled_users[username] = normalized
        trace(f"Enrolled user '{username}' with {len(embeddings)} samples")
        return True

    def is_candidate(self, username: str) -> bool:
        """Whether an identified name is one of the discovery's unnamed voices."""
        return username in getattr(self, "_candidate_ids", ())

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

    def embed_clip(self, wav_path: Path) -> tuple['np.ndarray | None', str, float]:
        """A `log/audio` clip for the voice discovery: (unit embedding of the whole clip, "ok" or
        why the clip is unusable — short, quiet, mixed —, duration in seconds)."""
        signal, sample_rate = torchaudio.load(str(wav_path))
        if signal.shape[0] > 1:
            signal = torch.mean(signal, dim=0, keepdim=True)
        if sample_rate != 16000:
            signal = torchaudio.transforms.Resample(sample_rate, 16000)(signal)
        duration = signal.shape[1] / 16000
        if duration < self.CLIP_MIN_DURATION:
            return None, "short", duration
        if float(signal.pow(2).mean().sqrt()) < self.CLIP_MIN_RMS:
            return None, "quiet", duration
        self._ensure_model_loaded()
        unit = lambda part: F.normalize(self._classifier.encode_batch(part).squeeze(), p=2, dim=-1)
        half = signal.shape[1] // 2
        if float(unit(signal[:, :half]) @ unit(signal[:, half:])) < self.CLIP_MIXED_SIM:
            return None, "mixed", duration
        return unit(signal).numpy(), "ok", duration

    def embed_sample(self, wav_path: Path) -> 'np.ndarray | None':
        """An enrollment sample's unit embedding, the way `_enroll` sees it — no length or
        loudness filter, the hand-recorded samples are short and quiet."""
        self._ensure_model_loaded()
        embedding = self._extract_embedding_from_file(wav_path)
        return F.normalize(embedding, p=2, dim=-1).numpy() if embedding is not None else None

    def enrolled_embeddings(self) -> dict[str, 'np.ndarray']:
        """Every enrolled person's unit embeddings as one array each, for the discovery's
        matching — the unnamed voices left out, or a candidate would count as known."""
        return {name: torch.stack(embs).numpy() for name, embs in self._enrolled_users.items()
                if embs and not self.is_candidate(name)}

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
        self._reload_if_changed()
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

