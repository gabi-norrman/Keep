"""Finds the voices the house keeps hearing that match no enrolled speaker. Once a night it
embeds the week's `log/audio` clips with the SpeakerIdentifier's model, clusters them by voice,
and files every voice that recurs over enough days as a candidate in `voice_id/candidates/`
(`assistant.VoiceCandidates`). The identifier enrolls a candidate under its id, so the next time
the voice speaks the turn note says whose name is missing and the assistant asks; `name_voice`
then turns the candidate into an enrollment the identifier reloads by itself.

The numbers were measured on a week of a real household (32,519 clips, ~/Desktop/voice_clusters
on the maintainer's machine, 2026-10-13): clips under 2 s or quieter than RMS 0.003 carry no
voice; halves of one clip with cosine below 0 hold two voices or voice-then-noise; average-linkage
clustering cut at cosine distance 0.65 never put two enrolled people's samples in one cluster
while a cut of 0.75 merged two sisters; merging clusters whose centroids are closer than 0.68
(average linkage again — pairwise union chains everything into one bucket) reassembles a voice the
room or the mood split; a cluster whose centroid is within 0.45 of an enrolled speaker's samples
and 0.10 ahead of the runner-up is that speaker. The four household voices came out of every
single day's clips on their own; visitors showed up as one-day clusters, which the min_days rule
drops. The clustering itself (`cluster`) is pure numpy/scipy so the tests feed it synthetic
embeddings."""
from __future__ import annotations

import os
import random
import threading
import time
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Callable, Optional

import numpy as np

from assistant import VoiceCandidates
from common.ConfigManager import ConfigManager
from common.Paths import CACHE_DIR, LOG_DIR, VOICE_ID_DIR
from interfaces.voice.speaker_id import EnrollmentRefresh
from common.logging import error, info, trace

CONFIG_KEY = "voice_discovery"   # the one config section: `enabled` (default true); the rest is tuned here
HOUR = 5              # the hour of the nightly run
DAYS = 7              # how many days of recordings a run looks at
MIN_CLIPS = 100       # a voice is filed once it has this many clips...
MIN_DAYS = 3          # ...spread over at least this many days; a one-day voice is a visitor
AUDIO_DIR = LOG_DIR / "audio"
CACHE_FILE = CACHE_DIR / "voice_discovery" / "embeddings.npz"
CHECK_INTERVAL = 60

CUT = 0.65            # cosine distance the clustering is cut at
MERGE_SIM = 0.68      # clusters whose centroids are this similar are one voice
OUTLIER_SIM = 0.35    # a clip further than this from its cluster's centroid is left out
MIN_CLUSTER = 10
KNOWN_SIM = 0.45      # a bucket this close to an enrolled speaker (top-3 mean)...
KNOWN_MARGIN = 0.10   # ...and this far ahead of the runner-up is that speaker
SAME_VOICE_SIM = 0.60 # a bucket this close to an open candidate is that voice again
REFRESH_OFFER = 200   # the most central clips of a known bucket offered to the person's enrollment refresh
MAX_CHUNK = 6000      # clips clustered at once: linkage needs n²/2 float64 (6k -> 150 MB, 20k -> 3 GB measured)
SAMPLES_KEPT = 12
TRANSCRIPTS_SHOWN = 6
MIN_TRANSCRIPT_WORDS = 3

# (unit embedding or None, "ok" or why the clip is unusable, duration in seconds)
Embedder = Callable[[Path], tuple[Optional[np.ndarray], str, float]]


@dataclass
class Clip:
    name: str
    embedding: Optional[np.ndarray]
    reason: str
    duration: float
    mtime: float

    @property
    def day(self) -> date:
        return datetime.fromtimestamp(self.mtime).date()


def enabled() -> bool:
    """`voice_discovery.enabled` of config.yaml, on unless the key says otherwise."""
    section = ConfigManager.get_instance().get(CONFIG_KEY, {})
    return bool(section.get("enabled", True)) if isinstance(section, dict) else True


def cluster(X: np.ndarray, cut: float = CUT, merge: float = MERGE_SIM, outlier: float = OUTLIER_SIM,
            min_cluster: int = MIN_CLUSTER) -> list[np.ndarray]:
    """Group the rows of X (unit embeddings) by voice: the index arrays of the buckets, largest
    first, each holding only the clips within *outlier* of its centroid."""
    if len(X) < max(min_cluster, 2):
        return []
    from scipy.cluster.hierarchy import fcluster, linkage
    labels = fcluster(linkage(X, method="average", metric="cosine"), t=cut, criterion="distance")
    parts = []
    for label in set(labels):
        idx = np.where(labels == label)[0]
        c = centroid(X, idx)
        core = idx[X[idx] @ c >= outlier]
        if len(core) >= min_cluster:
            parts.append(core)
    if not parts:
        return []
    if len(parts) == 1:
        return parts
    C = np.stack([centroid(X, p) for p in parts])
    merged = fcluster(linkage(C, method="average", metric="cosine"), t=1 - merge, criterion="distance")
    buckets: dict[int, list[np.ndarray]] = {}
    for part, label in zip(parts, merged):
        buckets.setdefault(int(label), []).append(part)
    return sorted((np.concatenate(ps) for ps in buckets.values()), key=len, reverse=True)


def cluster_by_day(X: np.ndarray, days: list[date], durations: list[float] = None,
                   max_chunk: int = MAX_CHUNK) -> list[np.ndarray]:
    """`cluster` on each day's clips, then the days' buckets merged by their centroids (average
    linkage at MERGE_SIM): the memory of one day's linkage instead of the window's, and every
    household voice comes out of a single day on its own (measured). A day beyond *max_chunk*
    clips keeps its longest ones."""
    days = np.asarray(days)
    parts = []
    for day in sorted(set(days.tolist())):
        idx = np.where(days == day)[0]
        if len(idx) > max_chunk:
            longest = np.argsort([-(durations[i] if durations else 0.0) for i in idx])[:max_chunk]
            trace(f"Voice discovery: {day} has {len(idx)} usable clips, clustering the {max_chunk} longest")
            idx = idx[longest]
        parts += [idx[b] for b in cluster(X[idx])]
    if not parts:
        return []
    if len(parts) == 1:
        return parts
    from scipy.cluster.hierarchy import fcluster, linkage
    C = np.stack([centroid(X, p) for p in parts])
    merged = fcluster(linkage(C, method="average", metric="cosine"), t=1 - MERGE_SIM, criterion="distance")
    buckets: dict[int, list[np.ndarray]] = {}
    for part, label in zip(parts, merged):
        buckets.setdefault(int(label), []).append(part)
    return sorted((np.concatenate(ps) for ps in buckets.values()), key=len, reverse=True)


def centroid(X: np.ndarray, idx) -> np.ndarray:
    c = X[idx].mean(0)
    return c / (np.linalg.norm(c) or 1.0)


def match_enrolled(c: np.ndarray, enrolled: dict[str, np.ndarray]) -> tuple[str, float, float]:
    """The enrolled speaker a centroid is closest to: (name, top-3 mean similarity, margin over
    the runner-up). ('', 0, 0) with nobody enrolled."""
    scores = {name: float(np.sort(E @ c)[-3:].mean()) for name, E in enrolled.items() if len(E)}
    if not scores:
        return "", 0.0, 0.0
    ranked = sorted(scores.items(), key=lambda item: -item[1])
    best, sim = ranked[0]
    return best, sim, sim - (ranked[1][1] if len(ranked) > 1 else 0.0)


def describe_hours(hours: list[int]) -> str:
    if not hours:
        return ""
    lo, hi = int(np.percentile(hours, 25)), int(np.percentile(hours, 75))
    return f"mostly around {lo}h" if lo == hi else f"mostly {lo}–{hi}h"


class VoiceDiscovery:
    """The nightly run and its hour loop; see the module doc for what it does and why."""

    def __init__(self, embedder: Embedder = None, enrolled: Callable[[], dict[str, np.ndarray]] = None,
                 audio_dir: Path = None, cache_file: Path = None,
                 candidates_dir: Path = None, voice_id_dir: Path = None,
                 sample_embedder: Callable[[Path], Optional[np.ndarray]] = None, replaced_dir: Path = None,
                 enabled: bool = None, hour: int = HOUR, min_clips: int = MIN_CLIPS, min_days: int = MIN_DAYS,
                 samples_per_person: int = EnrollmentRefresh.SIZE):
        """The keyword arguments after *replaced_dir* let a test shrink the thresholds; the server
        runs on the module constants and the config's `enabled`."""
        self._embedder = embedder
        self._enrolled = enrolled
        self._sample_embedder = sample_embedder
        self.voice_id_dir = voice_id_dir or VOICE_ID_DIR
        self.replaced_dir = replaced_dir
        self.audio_dir = audio_dir or AUDIO_DIR
        self.cache_file = cache_file or CACHE_FILE
        self.candidates_dir = candidates_dir
        self._enabled = enabled
        self.hour, self.min_clips, self.min_days, self.samples_per_person = hour, min_clips, min_days, samples_per_person
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._last_run_date: Optional[date] = None

    # --- the hour loop -------------------------------------------------------------------

    def start(self) -> None:
        if not (self._enabled if self._enabled is not None else enabled()):
            info("Voice discovery is off (voice_discovery.enabled)")
            return
        now = datetime.now()
        if now.hour == self.hour:
            # A boot inside the run hour: the speaker identifier is still loading and a run now
            # would embed nothing, and a restart after tonight's run must not run it twice.
            self._last_run_date = now.date()
            info(f"Voice discovery: started inside the {self.hour:02d}:00 hour, tonight counts as done")
        self._running = True
        self._thread = threading.Thread(target=self._loop, name="VoiceDiscovery", daemon=True)
        self._thread.start()
        info(f"Voice discovery started: runs at {self.hour:02d}:00 over the last {DAYS} days")

    def stop(self) -> None:
        self._running = False

    def _loop(self) -> None:
        while self._running:
            try:
                now = datetime.now()
                if now.hour == self.hour and self._last_run_date != now.date():
                    self._last_run_date = now.date()
                    self.run(now)
            except Exception as e:
                error(f"Voice discovery error: {e}")
            for _ in range(CHECK_INTERVAL):
                if not self._running:
                    return
                time.sleep(1)

    # --- one run ------------------------------------------------------------------------

    def run(self, now: datetime = None) -> dict:
        """Embed the window's new clips, cluster and file. Returns the counts, for the log and
        the tests."""
        now = now or datetime.now()
        summary = {"clips": 0, "usable": 0, "buckets": 0, "known": 0, "filed": 0, "refreshed": 0,
                   "too_small": 0, "enrollment_refreshed": 0}
        clips = self._embed_window(now - timedelta(days=DAYS))
        usable = [c for c in clips if c.reason == "ok" and c.embedding is not None]
        summary["clips"], summary["usable"] = len(clips), len(usable)
        if len(usable) < self.min_clips:
            info(f"Voice discovery: {len(usable)} usable clips of {len(clips)} in the window, fewer than "
                 f"the {self.min_clips} a voice needs — nothing to do")
            return summary
        X = np.stack([c.embedding for c in usable])
        buckets = cluster_by_day(X, [c.day for c in usable], [c.duration for c in usable])
        summary["buckets"] = len(buckets)
        info(f"Voice discovery: {len(usable)} usable clips of {len(clips)} -> {len(buckets)} voice bucket(s)")
        enrolled = self._enrolled_embeddings()
        offers: dict[str, list[EnrollmentRefresh.Sample]] = {}
        open_candidates = VoiceCandidates.items(self.candidates_dir)
        for idx in buckets:
            members = [usable[i] for i in idx]
            days = sorted({c.day for c in members})
            if len(members) < self.min_clips or len(days) < self.min_days:
                summary["too_small"] += 1
                trace(f"Voice discovery: a bucket of {len(members)} clips over {len(days)} day(s) is below "
                      f"{self.min_clips} clips / {self.min_days} days — a visitor or noise, skipped")
                continue
            c = centroid(X, idx)
            who, sim, margin = match_enrolled(c, enrolled)
            if sim >= KNOWN_SIM and margin >= KNOWN_MARGIN:
                summary["known"] += 1
                info(f"Voice discovery: a bucket of {len(members)} clips over {len(days)} days sounds like "
                     f"{who!r} ({sim:.2f}, margin {margin:.2f}) — known")
                order = idx[np.argsort(-(X[idx] @ c))[:REFRESH_OFFER]]
                offers.setdefault(who, []).extend(
                    EnrollmentRefresh.Sample(self.audio_dir / usable[i].name, X[i], usable[i].mtime,
                                             EnrollmentRefresh.ORIGIN_DISCOVERY) for i in order)
                continue
            sims = X[idx] @ c
            order = idx[np.argsort(-sims)]
            facts = dict(clips=len(members), days=len(days), first_day=days[0].isoformat(), last_day=days[-1].isoformat(),
                         hours=describe_hours([datetime.fromtimestamp(c.mtime).hour for c in members]),
                         transcripts=self._transcripts([usable[i].name for i in order]))
            existing = next((cand for cand in open_candidates if _similar(c, cand.centroid)), None)
            if existing is not None:
                VoiceCandidates.update(existing, **facts)
                summary["refreshed"] += 1
                candidate = existing
            else:
                samples = [self.audio_dir / usable[i].name for i in order[:SAMPLES_KEPT]]
                candidate = VoiceCandidates.create(**facts, centroid=c.tolist(), samples=samples,
                                                   directory=self.candidates_dir, today=now.date())
                open_candidates.append(candidate)
                summary["filed"] += 1
        # One refresh per person a night, whatever the number of buckets that sounded like them:
        # the turnover cap is a night's, not a bucket's.
        for person, offered in offers.items():
            outcome = self._refresh_enrollment(person, offered, enrolled, now, self.samples_per_person)
            if outcome and (outcome["added"] or outcome["dropped"]):
                summary["enrollment_refreshed"] += 1
        info(f"Voice discovery done: {summary}")
        return summary

    def _refresh_enrollment(self, person: str, offered: list[EnrollmentRefresh.Sample],
                            enrolled: dict[str, np.ndarray], now: datetime, size: int) -> Optional[dict]:
        """Offer the night's clips that sounded like the person to their sample set (EnrollmentRefresh)."""
        folder = self.voice_id_dir / person
        if not folder.is_dir():
            trace(f"Voice discovery: no enrollment folder for {person!r}, nothing to refresh")
            return None
        embed = self._sample_embedder or self._default_sample_embedder()
        if embed is None:
            return None
        current = EnrollmentRefresh.current_samples(folder, embed)
        others = {name: E for name, E in enrolled.items() if name != person}
        return EnrollmentRefresh.refresh(person, folder, current, offered, others, size=size,
                                         now=now.timestamp(), replaced_dir=self.replaced_dir)

    @staticmethod
    def _transcripts(names_by_centrality: list[str]) -> list[str]:
        """What the voice says: the clearest clips' transcripts first, then a few at random, no
        fragments and no repeats."""
        seen, picked = set(), []
        central = names_by_centrality[:TRANSCRIPTS_SHOWN * 2]
        rest = names_by_centrality[len(central):]
        random.Random(len(names_by_centrality)).shuffle(rest)
        for name in central + rest:
            text = VoiceCandidates.transcript_of(name)
            if len(text.split()) < MIN_TRANSCRIPT_WORDS or text.lower() in seen:
                continue
            seen.add(text.lower())
            picked.append(text)
            if len(picked) == TRANSCRIPTS_SHOWN:
                break
        return picked

    # --- the embeddings and their cache --------------------------------------------------

    def _embed_window(self, since: datetime) -> list[Clip]:
        """Every clip of the window, embedded — from the cache when it was embedded before (the
        cache is rewritten holding the window only, so it never outgrows it)."""
        if not self.audio_dir.is_dir():
            info(f"Voice discovery: no recordings folder {self.audio_dir}")
            return []
        cutoff = since.timestamp()
        files = []
        for entry in os.scandir(self.audio_dir):
            if entry.name.endswith(".wav") and entry.is_file():
                mtime = entry.stat().st_mtime
                if mtime >= cutoff:
                    files.append((entry.name, mtime))
        cached = self._load_cache()
        # A name comes back once the size cap deleted its file: a changed mtime is a new clip.
        todo = [(name, mtime) for name, mtime in files if name not in cached or cached[name].mtime != mtime]
        if todo:
            embedder = self._embedder or self._default_embedder()
            if embedder is None:
                error("Voice discovery: the speaker identifier is not ready, no embeddings tonight")
                return []
            info(f"Voice discovery: embedding {len(todo)} new clips ({len(files) - len(todo)} cached)")
            started = time.time()
            for n, (name, mtime) in enumerate(todo, 1):
                try:
                    embedding, reason, duration = embedder(self.audio_dir / name)
                except Exception as e:
                    embedding, reason, duration = None, f"error:{type(e).__name__}", 0.0
                cached[name] = Clip(name, embedding, reason, duration, mtime)
                if n % 1000 == 0:
                    trace(f"Voice discovery: {n}/{len(todo)} embedded")
            trace(f"Voice discovery: embedded in {time.time() - started:.0f}s")
        clips = [cached[name] for name, _ in files]
        self._save_cache(clips)
        return clips

    def _load_cache(self) -> dict[str, Clip]:
        if not self.cache_file.exists():
            return {}
        try:
            z = np.load(self.cache_file, allow_pickle=False)
            return {str(n): Clip(str(n), e if r == "ok" else None, str(r), float(d), float(m))
                    for n, e, r, d, m in zip(z["names"], z["embs"], z["reasons"], z["durs"], z["mtimes"])}
        except (OSError, ValueError, KeyError) as e:
            error(f"Voice discovery: embedding cache unreadable, starting over: {e}")
            return {}

    def _save_cache(self, clips: list[Clip]) -> None:
        if not clips:
            return
        width = next((len(c.embedding) for c in clips if c.embedding is not None), 1)
        try:
            self.cache_file.parent.mkdir(parents=True, exist_ok=True)
            np.savez(self.cache_file, names=np.array([c.name for c in clips]),
                     embs=np.stack([c.embedding if c.embedding is not None else np.zeros(width, dtype=np.float32)
                                    for c in clips]).astype(np.float32),
                     reasons=np.array([c.reason for c in clips]), durs=np.array([c.duration for c in clips]),
                     mtimes=np.array([c.mtime for c in clips]))
        except OSError as e:
            error(f"Voice discovery: embedding cache not written: {e}")

    # --- the identifier's model and enrollment --------------------------------------------

    @staticmethod
    def _identifier():
        from interfaces.voice.speaker_id.SpeakerIdentifier import SpeakerIdentifier
        return SpeakerIdentifier.get_instance()

    def _default_embedder(self) -> Optional[Embedder]:
        identifier = self._identifier()
        return identifier.embed_clip if identifier is not None else None

    def _default_sample_embedder(self):
        identifier = self._identifier()
        return identifier.embed_sample if identifier is not None else None

    def _enrolled_embeddings(self) -> dict[str, np.ndarray]:
        if self._enrolled is not None:
            return self._enrolled()
        identifier = self._identifier()
        return identifier.enrolled_embeddings() if identifier is not None else {}


def _similar(c: np.ndarray, other) -> bool:
    if other is None or len(other) != len(c):
        return False
    return float(c @ np.asarray(other, dtype=float)) >= SAME_VOICE_SIM
