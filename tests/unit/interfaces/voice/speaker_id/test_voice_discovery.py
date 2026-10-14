"""The nightly voice discovery on synthetic voices: the clustering, the household rule, the known
and dismissed voices, the asking, and the embedding cache."""
import os
import time
from datetime import datetime, timedelta

import numpy as np
import pytest

from assistant import VoiceCandidates
from interfaces.voice.speaker_id import VoiceDiscovery as vd
from interfaces.voice.speaker_id.VoiceDiscovery import VoiceDiscovery, cluster, cluster_by_day, match_enrolled, describe_hours

DIM = 32
NOW = datetime(2026, 10, 14, 5, 0)


def _voice(rng, n: int, centre: np.ndarray, spread: float = 0.3) -> np.ndarray:
    """n unit embeddings around a centre, the noise *spread* long: within a voice cosine ~0.95,
    between voices ~0 (random directions in DIM dimensions are nearly orthogonal)."""
    X = centre + spread / np.sqrt(DIM) * rng.standard_normal((n, DIM))
    return X / np.linalg.norm(X, axis=1, keepdims=True)


def _centres(rng, k: int) -> list[np.ndarray]:
    C = rng.standard_normal((k, DIM))
    return list(C / np.linalg.norm(C, axis=1, keepdims=True))


def test_cluster_recovers_the_voices_and_leaves_the_noise_out():
    rng = np.random.default_rng(1)
    a, b, c = _centres(rng, 3)
    X = np.vstack([_voice(rng, 60, a), _voice(rng, 40, b), _voice(rng, 15, c), _voice(rng, 5, _centres(rng, 1)[0], 8.0)])
    buckets = cluster(X)
    assert [len(x) for x in buckets] == [60, 40, 15]
    assert set(buckets[0]) == set(range(60)) and set(buckets[1]) == set(range(60, 100))


def test_cluster_merges_a_voice_split_by_condition_but_not_two_voices():
    rng = np.random.default_rng(2)
    a, b = _centres(rng, 2)
    shifted = a + 0.9 * _centres(rng, 1)[0]   # cosine ~0.74 to a: one voice in another room
    shifted /= np.linalg.norm(shifted)
    X = np.vstack([_voice(rng, 40, a), _voice(rng, 40, shifted), _voice(rng, 40, b)])
    buckets = cluster(X)
    assert len(buckets) == 2 and len(buckets[0]) == 80 and set(buckets[1]) == set(range(80, 120))


def test_cluster_with_too_few_clips_is_empty():
    assert cluster(np.zeros((3, DIM))) == []


def test_match_enrolled_reports_the_margin_over_the_runner_up():
    rng = np.random.default_rng(3)
    a, b = _centres(rng, 2)
    enrolled = {"charlotte": _voice(rng, 5, a), "frances": _voice(rng, 5, b)}
    who, sim, margin = match_enrolled(a, enrolled)
    assert who == "charlotte" and sim > 0.8 and margin > 0.5
    between = (a + b) / np.linalg.norm(a + b)
    _, _, margin = match_enrolled(between, enrolled)
    assert margin < 0.2
    assert match_enrolled(a, {}) == ("", 0.0, 0.0)


def test_describe_hours_spans_the_middle_half():
    assert describe_hours([17] * 10) == "mostly around 17h"
    assert describe_hours([8, 17, 17, 18, 18, 19, 20, 20]) == "mostly 17–19h"
    assert describe_hours([]) == ""


class Fake:
    """A recordings folder of named clips with chosen days, an embedder that answers from a table."""

    def __init__(self, tmp_path):
        self.audio = tmp_path / "audio"
        self.audio.mkdir()
        self.table = {}
        self.calls = 0
        self.asked = []
        self.rng = np.random.default_rng(7)
        self.centres = _centres(self.rng, 4)

    def voice(self, index: int, n: int, days: list[int], prefix="clip", reason="ok"):
        X = _voice(self.rng, n, self.centres[index])
        for i in range(n):
            name = f"{prefix}_{index}_{i}_what_a_lovely_day_{i}.wav"
            path = self.audio / name
            path.write_bytes(b"RIFF")
            when = (NOW - timedelta(days=days[i % len(days)], hours=12)).timestamp()
            os.utime(path, (when, when))
            self.table[name] = (X[i] if reason == "ok" else None, reason, 3.0)
        return self.centres[index]

    def embed(self, path):
        self.calls += 1
        return self.table[path.name]

    def ask(self, text):
        self.asked.append(text)
        return ""

    def discovery(self, tmp_path, enrolled=None, **config):
        cfg = {"enabled": True, "hour": 5, "days": 7, "min_clips": 30, "min_days": 3, "refresh_enrollment": True,
               "samples_per_person": 10} | config
        return VoiceDiscovery(embedder=self.embed, enrolled=lambda: enrolled or {}, ask=self.ask,
                              audio_dir=self.audio, cache_file=tmp_path / "cache" / "emb.npz",
                              candidates_dir=tmp_path / "candidates", config=cfg, voice_id_dir=tmp_path / "voice_id",
                              sample_embedder=lambda path: self.centres[0], replaced_dir=tmp_path / "replaced")


def test_a_recurring_unknown_voice_is_filed_and_the_owner_is_asked(tmp_path):
    fake = Fake(tmp_path)
    fake.voice(0, 40, days=[1, 2, 3, 4])
    summary = fake.discovery(tmp_path).run(NOW)
    assert summary["filed"] == 1 and summary["asked"] == 1 and summary["known"] == 0
    (candidate,) = VoiceCandidates.items(tmp_path / "candidates")
    assert candidate.clips == 40 and candidate.days == 4 and candidate.asked == "2026-10-14"
    assert len(candidate.samples) == vd.SAMPLES_KEPT and candidate.transcripts
    assert candidate.id in fake.asked[0] and "40 times" in fake.asked[0]


def test_a_one_day_voice_is_a_visitor_and_is_not_asked_about(tmp_path):
    fake = Fake(tmp_path)
    fake.voice(0, 40, days=[2])
    fake.voice(1, 40, days=[1, 2, 3, 4])
    summary = fake.discovery(tmp_path).run(NOW)
    assert summary["too_small"] == 1 and summary["filed"] == 1 and len(fake.asked) == 1


def test_an_enrolled_voice_is_known_and_skipped(tmp_path):
    fake = Fake(tmp_path)
    centre = fake.voice(0, 40, days=[1, 2, 3, 4])
    enrolled = {"charlotte": _voice(fake.rng, 6, centre)}
    summary = fake.discovery(tmp_path, enrolled=enrolled).run(NOW)
    assert summary["known"] == 1 and summary["filed"] == 0 and fake.asked == []


def test_a_voice_the_owner_dismissed_is_not_proposed_again(tmp_path):
    fake = Fake(tmp_path)
    fake.voice(0, 40, days=[1, 2, 3, 4])
    discovery = fake.discovery(tmp_path)
    discovery.run(NOW)
    (candidate,) = VoiceCandidates.items(tmp_path / "candidates")
    assert VoiceCandidates.dismiss(candidate.id, "the radio", directory=tmp_path / "candidates", log_dir=tmp_path / "log") == ""
    summary = discovery.run(NOW)
    assert summary["dismissed"] == 1 and summary["filed"] == 0 and len(fake.asked) == 1


def test_a_second_run_refreshes_the_open_candidate_instead_of_filing_it_again(tmp_path):
    fake = Fake(tmp_path)
    fake.voice(0, 40, days=[1, 2, 3, 4])
    discovery = fake.discovery(tmp_path)
    discovery.run(NOW)
    fake.voice(0, 20, days=[5, 6], prefix="later")
    summary = discovery.run(NOW)
    assert summary["refreshed"] == 1 and summary["filed"] == 0 and len(fake.asked) == 1
    (candidate,) = VoiceCandidates.items(tmp_path / "candidates")
    assert candidate.clips == 60 and candidate.days == 6


def test_the_cache_spares_the_clips_embedded_before(tmp_path):
    fake = Fake(tmp_path)
    fake.voice(0, 40, days=[1, 2, 3, 4])
    discovery = fake.discovery(tmp_path)
    discovery.run(NOW)
    assert fake.calls == 40
    fake.voice(1, 5, days=[1], prefix="new")
    discovery.run(NOW)
    assert fake.calls == 45


def test_clips_outside_the_window_and_unusable_ones_are_not_clustered(tmp_path):
    fake = Fake(tmp_path)
    fake.voice(0, 40, days=[10, 11, 12, 13])
    fake.voice(1, 40, days=[1, 2, 3, 4], reason="short")
    summary = fake.discovery(tmp_path).run(NOW)
    assert summary["clips"] == 40 and summary["usable"] == 0 and summary["buckets"] == 0


def test_an_unanswered_ask_is_retried_next_run(tmp_path):
    fake = Fake(tmp_path)
    fake.voice(0, 40, days=[1, 2, 3, 4])
    discovery = fake.discovery(tmp_path)
    discovery._ask = lambda text: "post_message failed: unknown destination"
    assert discovery.run(NOW)["asked"] == 0
    (candidate,) = VoiceCandidates.items(tmp_path / "candidates")
    assert candidate.asked is None
    discovery._ask = fake.ask
    assert discovery.run(NOW)["asked"] == 1


def test_transcripts_skip_fragments_and_repeats():
    names = ["Huh_.wav", "Thank_you_.wav", "what_s_for_dinner_1.wav", "what_s_for_dinner_2.wav", "turn_off_the_lights_please.wav"]
    assert VoiceDiscovery._transcripts(names) == ["what s for dinner", "turn off the lights please"]


def test_settings_fall_back_to_the_defaults(monkeypatch):
    from common.ConfigManager import ConfigManager
    monkeypatch.setattr(ConfigManager.get_instance(), "get", lambda key, default=None: {"hour": 3})
    assert vd.settings() == vd.DEFAULTS | {"hour": 3}
    monkeypatch.setattr(ConfigManager.get_instance(), "get", lambda key, default=None: "not a section")
    assert vd.settings() == vd.DEFAULTS


def test_settings_survive_a_config_without_the_section(monkeypatch):
    from common.ConfigManager import ConfigManager
    manager = ConfigManager.get_instance()
    monkeypatch.setattr(manager, "_config", {"owner_name": "Marcus"})
    assert vd.settings() == vd.DEFAULTS


def test_a_disabled_discovery_does_not_start(tmp_path):
    discovery = Fake(tmp_path).discovery(tmp_path, enabled=False)
    discovery.start()
    assert discovery._thread is None


def test_a_known_voice_with_a_clear_margin_refreshes_the_persons_samples(tmp_path):
    fake = Fake(tmp_path)
    centre = fake.voice(0, 40, days=[1, 2, 3, 4])
    folder = tmp_path / "voice_id" / "charlotte"
    folder.mkdir(parents=True)
    for i in range(3):
        (folder / f"old-{i}.wav").write_bytes(b"RIFF")
    enrolled = {"charlotte": _voice(fake.rng, 6, centre), "frances": _voice(fake.rng, 6, fake.centres[1])}
    summary = fake.discovery(tmp_path, enrolled=enrolled).run(NOW)
    assert summary["known"] == 1 and summary["enrollment_refreshed"] == 1
    assert len(list(folder.glob("*.wav"))) == 5 and (folder / "samples.json").exists()


def test_the_refresh_can_be_switched_off(tmp_path):
    fake = Fake(tmp_path)
    centre = fake.voice(0, 40, days=[1, 2, 3, 4])
    folder = tmp_path / "voice_id" / "charlotte"
    folder.mkdir(parents=True)
    summary = fake.discovery(tmp_path, enrolled={"charlotte": _voice(fake.rng, 6, centre)}, refresh_enrollment=False).run(NOW)
    assert summary["known"] == 1 and summary["enrollment_refreshed"] == 0 and list(folder.iterdir()) == []


def test_two_buckets_of_one_person_make_one_refresh_within_the_nightly_cap(tmp_path):
    fake = Fake(tmp_path)
    centre = fake.voice(0, 40, days=[1, 2, 3, 4])
    shifted = centre + 3.2 * fake.centres[2]
    fake.centres[3] = shifted / np.linalg.norm(shifted)   # the same person far from the mic: its own bucket
    fake.voice(3, 40, days=[1, 2, 3, 4], prefix="room")
    folder = tmp_path / "voice_id" / "charlotte"
    folder.mkdir(parents=True)
    for i in range(3):
        (folder / f"old-{i}.wav").write_bytes(b"RIFF")
    enrolled = {"charlotte": np.vstack([_voice(fake.rng, 3, centre), _voice(fake.rng, 3, fake.centres[3])]),
                "frances": _voice(fake.rng, 6, fake.centres[1])}
    summary = fake.discovery(tmp_path, enrolled=enrolled).run(NOW)
    assert summary["known"] == 2 and summary["enrollment_refreshed"] == 1
    assert len(list(folder.glob("*.wav"))) == 5


def test_cluster_by_day_reassembles_a_voice_across_days_and_caps_a_day_by_length():
    from datetime import date
    rng = np.random.default_rng(4)
    a, b = _centres(rng, 2)
    X = np.vstack([_voice(rng, 30, a), _voice(rng, 20, b), _voice(rng, 20, a), _voice(rng, 10, b)])
    days = [date(2026, 10, 6)] * 50 + [date(2026, 10, 7)] * 30
    buckets = cluster_by_day(X, days)
    assert [len(x) for x in buckets] == [50, 30]
    assert set(buckets[0]) == set(range(30)) | set(range(50, 70))
    durations = [1.0] * 80
    durations[:30] = [9.0] * 30   # day one over the cap keeps its 35 longest: all of a, too little of b
    capped = cluster_by_day(X, days, durations, max_chunk=35)
    assert [len(x) for x in capped] == [50, 10]


def test_a_rewritten_clip_with_a_new_mtime_is_embedded_again(tmp_path):
    fake = Fake(tmp_path)
    fake.voice(0, 40, days=[1, 2, 3, 4])
    discovery = fake.discovery(tmp_path)
    discovery.run(NOW)
    name = next(iter(fake.table))
    path = fake.audio / name
    later = (NOW - timedelta(hours=6)).timestamp()
    os.utime(path, (later, later))
    calls = fake.calls
    discovery.run(NOW)
    assert fake.calls == calls + 1


def test_a_boot_inside_the_run_hour_counts_tonight_as_done(tmp_path, monkeypatch):
    discovery = Fake(tmp_path).discovery(tmp_path, hour=datetime.now().hour)
    monkeypatch.setattr(discovery, "_loop", lambda: None)
    discovery.start()
    assert discovery._last_run_date == datetime.now().date()
