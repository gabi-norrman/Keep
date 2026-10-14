"""The nightly refresh of a person's voice samples on synthetic embeddings: the scoring and
selection, the turnover cap, the never-shrink rule, the margin veto and the files it writes."""
import json
import math
from pathlib import Path

import numpy as np
import pytest

from interfaces.voice.speaker_id import EnrollmentRefresh as er
from interfaces.voice.speaker_id.EnrollmentRefresh import Sample, refresh, select, score, min_margin

DIM = 32
NOW = 1_800_000_000.0
DAY = 86400.0


def _unit(v):
    return v / np.linalg.norm(v)


def _around(rng, centre, n, spread=0.3):
    X = centre + spread / np.sqrt(DIM) * rng.standard_normal((n, DIM))
    return X / np.linalg.norm(X, axis=1, keepdims=True)


def _samples(tmp_path, X, prefix, when=NOW - DAY, origin=er.ORIGIN_ENROLLED):
    folder = tmp_path / prefix
    folder.mkdir(exist_ok=True)
    out = []
    for i, e in enumerate(X):
        p = folder / f"{prefix}-{i:02d}.wav"
        p.write_bytes(b"RIFF")
        out.append(Sample(p, e, when, origin))
    return out


@pytest.fixture
def voices():
    rng = np.random.default_rng(5)
    C = rng.standard_normal((3, DIM))
    a, b, c = (C / np.linalg.norm(C, axis=1, keepdims=True))
    return rng, a, b, c


def test_score_rewards_identity_and_margin_and_penalises_age(tmp_path, voices):
    rng, a, b, _ = voices
    samples = _samples(tmp_path, _around(rng, a, 5), "p")
    samples[0].when = NOW - 3 * 365 * DAY
    stray = Sample(tmp_path / "stray.wav", _unit(a + b), NOW - DAY)
    score(samples + [stray], {"other": _around(rng, b, 5)}, NOW)
    assert all(s.identity > 0.9 and s.margin > 0.5 for s in samples)
    assert stray.identity < 0.8 and stray.margin < samples[1].margin
    assert samples[0].score < samples[1].score - 0.1


def test_select_keeps_one_of_a_near_duplicate_pair_and_fills_up_to_size(tmp_path, voices):
    rng, a, _, _ = voices
    distinct = _samples(tmp_path, _around(rng, a, 6, spread=1.2), "p")
    near = distinct[0].embedding + 0.25 * _unit(rng.standard_normal(DIM))   # cosine ~0.97: the same condition, not the same file
    twin = Sample(tmp_path / "twin.wav", near / np.linalg.norm(near), NOW)
    pool = distinct + [twin]
    score(pool, {}, NOW)
    twin.score = distinct[0].score + 1   # the twin outranks its original
    kept = select(pool, size=6, floor=0.0, diversity=0.95)
    names = {s.name for s in kept}
    assert "twin.wav" in names and distinct[0].name not in names and len(kept) == 6
    assert len(select(pool, size=7, floor=0.0, diversity=0.95)) == 7   # room left: the near-duplicate fills it


def test_select_drops_samples_below_the_cleanliness_floor(tmp_path, voices):
    rng, a, b, _ = voices
    pool = _samples(tmp_path, _around(rng, a, 4), "p") + [Sample(tmp_path / "b.wav", b, NOW)]
    score(pool, {}, NOW)
    assert {s.name for s in select(pool)} == {s.name for s in pool[:4]}


def test_min_margin_is_the_worst_sample_s_lead_over_the_others(tmp_path, voices):
    rng, a, b, _ = voices
    own = _samples(tmp_path, _around(rng, a, 6), "p")
    others = {"other": _around(rng, b, 6)}
    assert min_margin(own, others) > 0.5
    own.append(Sample(tmp_path / "x.wav", _unit(a + 2 * b), NOW))
    assert min_margin(own, others) < 0
    assert min_margin(own[:1], others) == -1.0


class TestRefresh:
    def test_adds_the_offered_clips_up_to_the_turnover_cap_and_never_shrinks(self, tmp_path, voices):
        rng, a, b, _ = voices
        folder = tmp_path / "charlotte"
        current = _samples(tmp_path, _around(rng, a, 10, spread=0.6), "charlotte", when=NOW - 400 * DAY)
        offered = _samples(tmp_path, _around(rng, a, 30), "offer", when=NOW - DAY, origin=er.ORIGIN_DISCOVERY)
        outcome = refresh("charlotte", folder, current, offered, {"frances": _around(rng, b, 5)}, size=10, now=NOW,
                          replaced_dir=tmp_path / "replaced")
        assert len(outcome["added"]) == 2 and len(outcome["dropped"]) == 2 and outcome["reason"] == ""
        files = sorted(p.name for p in folder.glob("*.wav"))
        assert len(files) == 10 and sum(f.startswith("charlotte-") and "-01.wav" in f or "-02.wav" in f for f in files) >= 2
        assert sorted(p.name for p in (tmp_path / "replaced" / "charlotte").glob("*.wav")) == sorted(outcome["dropped"])
        ledger = json.loads((folder / er.LEDGER).read_text())
        assert len(ledger) == 10 and {e["origin"] for e in ledger} == {er.ORIGIN_ENROLLED, er.ORIGIN_DISCOVERY}
        assert all(len(e["embedding"]) == DIM for e in ledger)

    def test_grows_a_small_set_towards_size_without_dropping(self, tmp_path, voices):
        rng, a, _, _ = voices
        folder = tmp_path / "charlotte"
        current = _samples(tmp_path, _around(rng, a, 3), "charlotte")
        offered = _samples(tmp_path, _around(rng, a, 30), "offer", origin=er.ORIGIN_DISCOVERY)
        outcome = refresh("charlotte", folder, current, offered, {}, size=40, now=NOW, replaced_dir=tmp_path / "r")
        assert len(outcome["added"]) == 8 and outcome["dropped"] == []
        assert len(list(folder.glob("*.wav"))) == 11

    def test_a_proposal_that_lowers_the_margin_to_another_person_is_vetoed(self, tmp_path, voices):
        rng, a, b, _ = voices
        folder = tmp_path / "charlotte"
        current = _samples(tmp_path, _around(rng, a, 6), "charlotte", when=NOW - 400 * DAY)
        towards_b = _around(rng, _unit(a + 0.8 * b), 10)   # closer to Frances than any current sample
        offered = _samples(tmp_path, towards_b, "offer", origin=er.ORIGIN_DISCOVERY)
        outcome = refresh("charlotte", folder, current, offered, {"frances": _around(rng, b, 6)}, size=6, now=NOW,
                          replaced_dir=tmp_path / "r")
        assert outcome["added"] == [] and outcome["dropped"] == [] and "margin" in outcome["reason"]
        assert len(list(folder.glob("*.wav"))) == 6 and (folder / er.LEDGER).exists()

    def test_nothing_better_offered_leaves_the_set_and_writes_the_ledger(self, tmp_path, voices):
        rng, a, _, _ = voices
        folder = tmp_path / "charlotte"
        current = _samples(tmp_path, _around(rng, a, 5), "charlotte")
        outcome = refresh("charlotte", folder, current, [], {}, size=5, now=NOW, replaced_dir=tmp_path / "r")
        assert outcome["reason"] == "nothing better offered" and (folder / er.LEDGER).exists()


def test_current_samples_come_from_the_ledger_when_it_knows_the_file(tmp_path, voices):
    rng, a, _, _ = voices
    folder = tmp_path / "charlotte"
    samples = _samples(tmp_path, _around(rng, a, 3), "charlotte")
    score(samples, {}, NOW)
    er.save_ledger(folder, samples)
    (folder / "charlotte-new.wav").write_bytes(b"RIFF")
    embedded = []

    def embed(path):
        embedded.append(path.name)
        return a

    loaded = er.current_samples(folder, embed)
    assert embedded == ["charlotte-new.wav"] and len(loaded) == 4
    assert all(math.isclose(float(np.linalg.norm(s.embedding)), 1.0, abs_tol=1e-4) for s in loaded)


def test_an_unembeddable_file_is_left_out(tmp_path):
    folder = tmp_path / "charlotte"
    folder.mkdir()
    (folder / "bad.wav").write_bytes(b"")
    assert er.current_samples(folder, lambda path: None) == []


def test_a_set_above_the_margin_floor_may_lose_some_margin_for_fresher_samples(tmp_path, voices):
    rng, a, b, _ = voices
    folder = tmp_path / "charlotte"
    current = _samples(tmp_path, np.repeat(a[None], 4, axis=0), "charlotte", when=NOW - 400 * DAY)
    offered = _samples(tmp_path, _around(rng, a, 20), "offer", origin=er.ORIGIN_DISCOVERY)
    outcome = refresh("charlotte", folder, current, offered, {"frances": _around(rng, b, 6)}, size=10, now=NOW,
                      replaced_dir=tmp_path / "r")
    assert outcome["added"] and outcome["reason"] == ""


def test_two_refreshes_on_one_day_do_not_overwrite_each_other_s_files(tmp_path, voices):
    rng, a, _, _ = voices
    folder = tmp_path / "charlotte"
    current = _samples(tmp_path, _around(rng, a, 2), "charlotte")
    first = _samples(tmp_path, _around(rng, a, 5), "first", origin=er.ORIGIN_DISCOVERY)
    refresh("charlotte", folder, current, first, {}, size=20, now=NOW, replaced_dir=tmp_path / "r")
    current = er.current_samples(folder, lambda path: a)
    second = _samples(tmp_path, _around(rng, a, 5), "second", origin=er.ORIGIN_DISCOVERY)
    refresh("charlotte", folder, current, second, {}, size=20, now=NOW, replaced_dir=tmp_path / "r")
    assert len(list(folder.glob("*.wav"))) == 10


def test_the_same_clips_offered_night_after_night_are_not_added_twice(tmp_path, voices):
    """The window re-offers a clip for a week; renamed into the set it must still count as taken."""
    rng, a, _, _ = voices
    folder = tmp_path / "charlotte"
    current = _samples(tmp_path, _around(rng, a, 2), "charlotte")
    offered = _samples(tmp_path, _around(rng, a, 6), "offer", origin=er.ORIGIN_DISCOVERY)
    for night in range(5):
        current = er.current_samples(folder, lambda path: a) if night else current
        again = [Sample(o.path, o.embedding, o.when, o.origin) for o in offered]
        er.refresh("charlotte", folder, current, again, {}, size=20, now=NOW + night * DAY, replaced_dir=tmp_path / "r")
    ledger = json.loads((folder / er.LEDGER).read_text())
    assert len(ledger) == 8 and len({e["source"] for e in ledger}) == 8


def test_select_never_fills_the_set_with_a_twin(tmp_path, voices):
    rng, a, _, _ = voices
    pool = _samples(tmp_path, _around(rng, a, 3), "p")
    twin = Sample(tmp_path / "twin.wav", pool[0].embedding.copy(), NOW)
    score(pool + [twin], {}, NOW)
    assert len(select(pool + [twin], size=10, floor=0.0)) == 3


def test_same_day_numbering_continues_past_a_dropped_file(tmp_path, voices):
    rng, a, _, _ = voices
    folder = tmp_path / "charlotte"
    current = _samples(tmp_path, _around(rng, a, 2), "charlotte")
    first = _samples(tmp_path, _around(rng, a, 3), "first", origin=er.ORIGIN_DISCOVERY)
    er.refresh("charlotte", folder, current, first, {}, size=20, now=NOW, replaced_dir=tmp_path / "r")
    (next(folder.glob("charlotte-*-01.wav"))).unlink()
    current = er.current_samples(folder, lambda path: a)
    second = _samples(tmp_path, _around(rng, a, 2), "second", origin=er.ORIGIN_DISCOVERY)
    er.refresh("charlotte", folder, current, second, {}, size=20, now=NOW, replaced_dir=tmp_path / "r")
    numbers = sorted(int(f.name[-6:-4]) for f in folder.glob("charlotte-*-*.wav"))
    assert numbers == [2, 3, 4, 5]
