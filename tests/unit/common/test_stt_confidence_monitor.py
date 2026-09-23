import pytest
from common.STTConfidenceMonitor import STTConfidenceMonitor


@pytest.fixture(autouse=True)
def reset_singleton():
    STTConfidenceMonitor.reset_instance()
    yield
    STTConfidenceMonitor.reset_instance()


class TestRecordPerMic:
    def test_record_stores_per_mic(self):
        mon = STTConfidenceMonitor()
        mon.record("mic-a", 0.8, 1)
        mon.record("mic-b", 0.6, 1)
        assert list(mon.history("mic-a")) == [0.8]
        assert list(mon.history("mic-b")) == [0.6]

    def test_empty_mic_is_ignored(self):
        mon = STTConfidenceMonitor()
        mon.record("", 0.9, 1)
        assert mon.mics == []

    def test_history_capped(self):
        mon = STTConfidenceMonitor()
        for i in range(STTConfidenceMonitor.HISTORY_SIZE + 5):
            mon.record("mic", 0.5, i)
        assert len(mon.history("mic")) == STTConfidenceMonitor.HISTORY_SIZE


class TestDedupPerUtterance:
    def test_same_mic_same_generation_keeps_highest(self):
        mon = STTConfidenceMonitor()
        mon.record("mic", 0.4, 7)
        mon.record("mic", 0.9, 7)  # later partial of same utterance — raises the bar
        assert list(mon.history("mic")) == [0.9]

    def test_same_mic_same_generation_ignores_lower_partial(self):
        mon = STTConfidenceMonitor()
        mon.record("mic", 0.9, 7)
        mon.record("mic", 0.4, 7)  # lower later partial — kept at the max
        assert list(mon.history("mic")) == [0.9]

    def test_raised_partial_updates_rolling_average(self):
        mon = STTConfidenceMonitor()
        mon.record("mic", 0.4, 7, now=1000.0)
        mon.record("mic", 0.9, 7, now=1000.0)  # same utterance, higher confidence
        assert mon.rolling_avg_12h("mic", now=1000.0) == pytest.approx(0.9)
        assert mon.rolling_avg_12h(now=1000.0) == pytest.approx(0.9)

    def test_new_generation_records_again(self):
        mon = STTConfidenceMonitor()
        mon.record("mic", 0.4, 7)
        mon.record("mic", 0.9, 8)
        assert list(mon.history("mic")) == [0.4, 0.9]

    def test_dedup_is_per_mic(self):
        mon = STTConfidenceMonitor()
        mon.record("mic-a", 0.4, 7)
        mon.record("mic-b", 0.6, 7)  # same generation, different mic — kept
        assert list(mon.history("mic-a")) == [0.4]
        assert list(mon.history("mic-b")) == [0.6]


class TestMicOrderingAndCap:
    def test_mics_in_first_seen_order(self):
        mon = STTConfidenceMonitor()
        mon.record("z", 0.5, 1)
        mon.record("a", 0.5, 1)
        mon.record("m", 0.5, 1)
        assert mon.mics == ["z", "a", "m"]

    def test_mics_capped_for_display(self):
        mon = STTConfidenceMonitor()
        for i, name in enumerate(["a", "b", "c", "d"]):
            mon.record(name, 0.5, i)
        assert mon.mics == ["a", "b", "c"]
        assert len(mon.history("d")) == 1  # still tracked, just not displayed


class TestRolling12hAverage:
    def test_average_across_all_mics(self):
        mon = STTConfidenceMonitor()
        mon.record("a", 0.4, 1, now=1000.0)
        mon.record("b", 0.8, 1, now=1000.0)
        assert mon.rolling_avg_12h(now=1000.0) == pytest.approx(0.6)

    def test_old_samples_pruned(self):
        mon = STTConfidenceMonitor()
        mon.record("a", 0.2, 1, now=0.0)              # 12h+ before the query
        mon.record("a", 0.8, 2, now=100_000.0)
        window = STTConfidenceMonitor.ROLLING_WINDOW_SECONDS
        assert mon.rolling_avg_12h(now=100_000.0) == pytest.approx(0.8)
        # sanity: the old sample really is outside the window
        assert 100_000.0 - 0.0 > window

    def test_empty_is_zero(self):
        assert STTConfidenceMonitor().rolling_avg_12h(now=1000.0) == 0.0

    def test_per_mic_average_is_isolated(self):
        mon = STTConfidenceMonitor()
        mon.record("a", 0.4, 1, now=1000.0)
        mon.record("a", 0.6, 2, now=1000.0)
        mon.record("b", 0.9, 1, now=1000.0)
        assert mon.rolling_avg_12h("a", now=1000.0) == pytest.approx(0.5)
        assert mon.rolling_avg_12h("b", now=1000.0) == pytest.approx(0.9)

    def test_per_mic_old_samples_pruned(self):
        mon = STTConfidenceMonitor()
        mon.record("a", 0.2, 1, now=0.0)              # 12h+ before the query
        mon.record("a", 0.8, 2, now=100_000.0)
        assert mon.rolling_avg_12h("a", now=100_000.0) == pytest.approx(0.8)

    def test_per_mic_unknown_is_zero(self):
        assert STTConfidenceMonitor().rolling_avg_12h("nope", now=1000.0) == 0.0
