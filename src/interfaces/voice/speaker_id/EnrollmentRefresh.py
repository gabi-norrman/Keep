"""Keeps each enrolled person's voice samples the best few dozen over time. Every night the
discovery hands it the clips of a bucket that clearly matched a person; the refresh pools them
with the person's current samples, scores each on identity (closeness to the person's centre),
margin (lead over the closest other person — what separates two sisters) and age (voices drift,
children's fastest), and keeps SIZE of them: in score order, skipping one within DIVERSITY_SIM
of a kept one, so one condition — the dinner table, another room — cannot crowd out the others.

It is cautious: at most TURNOVER of the set changes per night, the set never shrinks, and the
new set's smallest leave-one-out margin over the other people must stay above MARGIN_FLOOR, or
at least not fall below what the current set has, or the night's proposal is dropped. Replaced files go to `log/voice_id/replaced/<person>/`; `samples.json` in
the person's folder is the ledger — each file's scores, origin and embedding — that explains
why a sample is there, and spares re-embedding the set every night."""
from __future__ import annotations

import json
import math
import shutil
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

import numpy as np

from common.Paths import LOG_DIR
from common.logging import error, info, trace

SIZE = 40
TURNOVER = 0.2          # the share of the set a night may replace
CLEAN_SIM = 0.50        # a sample further than this from the person's centre is not kept
DIVERSITY_SIM = 0.90    # two samples this alike are one condition; the better scored stays
AGE_PENALTY = 0.05      # per year, off the score
MARGIN_FLOOR = 0.25     # a set whose leave-one-out margin stays above this may change freely; below it, never for the worse
LEDGER = "samples.json"
REPLACED_DIR = LOG_DIR / "voice_id" / "replaced"
ORIGIN_ENROLLED = "enrolled"
ORIGIN_DISCOVERY = "discovery"


@dataclass
class Sample:
    path: Path
    embedding: np.ndarray
    when: float                      # epoch of the recording
    origin: str = ORIGIN_ENROLLED
    identity: float = 0.0
    margin: float = 0.0
    score: float = 0.0
    keep: bool = field(default=False, compare=False)

    @property
    def name(self) -> str:
        return self.path.name


def _unit(v: np.ndarray) -> np.ndarray:
    return v / (np.linalg.norm(v) or 1.0)


def _top3(E: np.ndarray, e: np.ndarray) -> float:
    return float(np.sort(E @ e)[-3:].mean()) if len(E) else -1.0


def score(samples: list[Sample], others: dict[str, np.ndarray], now: float) -> None:
    """Fill identity, margin and score of every sample in place."""
    if not samples:
        return
    centre = _unit(np.stack([s.embedding for s in samples]).mean(0))
    for s in samples:
        s.identity = float(s.embedding @ centre)
        closest_other = max((_top3(E, s.embedding) for E in others.values()), default=-1.0)
        s.margin = s.identity - closest_other
        age_years = max(0.0, now - s.when) / (365 * 86400)
        s.score = 0.5 * s.identity + 0.5 * s.margin - AGE_PENALTY * age_years


def select(samples: list[Sample], size: int = SIZE, floor: float = CLEAN_SIM,
           diversity: float = DIVERSITY_SIM) -> list[Sample]:
    """The best *size* samples: in score order, skipping one within *diversity* of a kept one,
    then — if that leaves room — the best of the skipped ones. Below *floor* nothing is kept."""
    ranked = sorted((s for s in samples if s.identity >= floor), key=lambda s: -s.score)
    kept, skipped = [], []
    for s in ranked:
        if len(kept) == size:
            break
        if any(float(s.embedding @ k.embedding) >= diversity for k in kept):
            skipped.append(s)
        else:
            kept.append(s)
    kept += skipped[:size - len(kept)]
    return kept


def min_margin(samples: list[Sample], others: dict[str, np.ndarray]) -> float:
    """Leave-one-out: the smallest lead any sample has, identified by the rest of the set,
    over the closest other person. The number a refresh must not lower."""
    if len(samples) < 2:
        return -1.0
    E = np.stack([s.embedding for s in samples])
    worst = math.inf
    for i, s in enumerate(samples):
        rest = np.delete(E, i, axis=0)
        own = _top3(rest, s.embedding)
        closest_other = max((_top3(O, s.embedding) for O in others.values()), default=-1.0)
        worst = min(worst, own - closest_other)
    return worst


def load_ledger(folder: Path) -> dict[str, dict]:
    path = folder / LEDGER
    if not path.exists():
        return {}
    try:
        return {entry["file"]: entry for entry in json.loads(path.read_text(encoding="utf-8"))}
    except (OSError, ValueError, KeyError, TypeError) as e:
        error(f"Enrollment refresh: {path} unreadable, re-embedding the folder: {e}")
        return {}


def save_ledger(folder: Path, samples: list[Sample]) -> None:
    entries = [{"file": s.name, "when": s.when, "origin": s.origin, "identity": round(s.identity, 4),
                "margin": round(s.margin, 4), "score": round(s.score, 4),
                "embedding": [round(float(v), 5) for v in s.embedding]} for s in sorted(samples, key=lambda s: -s.score)]
    (folder / LEDGER).write_text(json.dumps(entries, indent=0), encoding="utf-8")


def current_samples(folder: Path, embed: Callable[[Path], Optional[np.ndarray]]) -> list[Sample]:
    """The person's samples on disk, embedded from the ledger when it knows the file."""
    ledger = load_ledger(folder)
    samples = []
    for path in sorted(folder.glob("*.wav")):
        entry = ledger.get(path.name)
        if entry is not None and entry.get("embedding"):
            samples.append(Sample(path, _unit(np.asarray(entry["embedding"], dtype=np.float32)),
                                  float(entry.get("when", path.stat().st_mtime)), entry.get("origin", ORIGIN_ENROLLED)))
            continue
        try:
            embedding = embed(path)
        except Exception as e:
            embedding = None
            trace(f"Enrollment refresh: {path.name} could not be embedded: {e}")
        if embedding is not None:
            samples.append(Sample(path, _unit(np.asarray(embedding, dtype=np.float32)), path.stat().st_mtime))
    return samples


def refresh(person: str, folder: Path, current: list[Sample], offered: list[Sample],
            others: dict[str, np.ndarray], size: int = SIZE, now: float = None,
            replaced_dir: Path = None) -> dict:
    """One night's refresh of *person*'s set from the *offered* clips. Returns what happened:
    added, dropped and the reason when nothing changed."""
    now = now or datetime.now().timestamp()
    replaced_dir = (replaced_dir or REPLACED_DIR) / person
    pool = current + [o for o in offered if o.name not in {c.name for c in current}]
    score(pool, others, now)
    proposal = select(pool, size)
    current_names = {c.name for c in current}
    allowed = max(1, math.ceil(TURNOVER * size))
    added = sorted((s for s in proposal if s.name not in current_names), key=lambda s: -s.score)[:allowed]
    kept_current = [s for s in proposal if s.name in current_names]
    # Never shrink: the best current samples not in the proposal fill the set back up.
    room = max(len(current), min(size, len(pool))) - len(kept_current) - len(added)
    if room > 0:
        proposal_names = {s.name for s in proposal}
        kept_current += sorted((s for s in current if s.name not in proposal_names), key=lambda s: -s.score)[:room]
    new_set = kept_current + added
    dropped = [s for s in current if s.name not in {n.name for n in new_set}]
    outcome = {"person": person, "added": [s.name for s in added], "dropped": [s.name for s in dropped], "reason": ""}
    if not added and not dropped:
        outcome["reason"] = "nothing better offered"
        trace(f"Enrollment refresh: {person} — {outcome['reason']}")
        save_ledger(folder, current)
        return outcome
    before, after = min_margin(current, others), min_margin(new_set, others)
    if others and after < MARGIN_FLOOR and after < before - 1e-6:
        outcome.update(added=[], dropped=[], reason=f"would lower the margin to the other people from {before:.2f} to {after:.2f}")
        info(f"Enrollment refresh: {person} — {outcome['reason']}, kept as is")
        save_ledger(folder, current)
        return outcome
    try:
        stamp = datetime.fromtimestamp(now).strftime("%Y%m%d")
        taken = len(list(folder.glob(f"{person}-{stamp}-*.wav")))
        for n, s in enumerate(added, taken + 1):
            target = folder / f"{person}-{stamp}-{n:02d}.wav"
            shutil.copy(s.path, target)
            s.path, s.origin = target, ORIGIN_DISCOVERY
        replaced_dir.mkdir(parents=True, exist_ok=True)
        for s in dropped:
            shutil.move(str(s.path), replaced_dir / s.name)
        save_ledger(folder, new_set)
    except OSError as e:
        error(f"Enrollment refresh: {person} — writing the set failed: {e}")
        outcome["reason"] = f"failed: {e}"
        return outcome
    info(f"Enrollment refresh: {person} — {len(added)} sample(s) added, {len(dropped)} replaced; "
         f"{len(new_set)} samples, margin to the others {before:.2f} -> {after:.2f}")
    return outcome
