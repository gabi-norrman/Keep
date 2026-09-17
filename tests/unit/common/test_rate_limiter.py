import pytest
from unittest.mock import patch
from common.RateLimiter import RateLimiter


@pytest.mark.unit
class TestRateLimiter:

    def test_allows_first_send(self):
        rl = RateLimiter(max_events=3, window_seconds=60)
        assert rl.check() is None

    def test_allows_up_to_max(self):
        rl = RateLimiter(max_events=3, window_seconds=60)
        for _ in range(3):
            assert rl.check() is None
            rl.record()

    def test_blocks_after_max(self):
        rl = RateLimiter(max_events=2, window_seconds=60)
        rl.record()
        rl.record()
        result = rl.check()
        assert result is not None
        assert "rate limit" in result
        assert "2 sends per 60s" in result

    def test_window_expiry_allows_again(self):
        rl = RateLimiter(max_events=1, window_seconds=10)
        with patch('common.RateLimiter.time') as mock_time:
            mock_time.time.return_value = 1000.0
            rl.record()
            assert rl.check() is not None

            mock_time.time.return_value = 1011.0
            assert rl.check() is None

    def test_sliding_window_evicts_old_timestamps(self):
        rl = RateLimiter(max_events=2, window_seconds=10)
        with patch('common.RateLimiter.time') as mock_time:
            mock_time.time.return_value = 100.0
            rl.record()
            mock_time.time.return_value = 105.0
            rl.record()

            mock_time.time.return_value = 111.0
            assert rl.check() is None
            rl.record()

            assert rl.check() is not None

    def test_record_without_check(self):
        rl = RateLimiter(max_events=1, window_seconds=60)
        rl.record()
        assert rl.check() is not None

    def test_zero_events_always_blocks(self):
        rl = RateLimiter(max_events=0, window_seconds=60)
        assert rl.check() is not None
