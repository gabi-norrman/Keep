from unittest.mock import Mock, patch, MagicMock

import pytest

from common.AnthropicCostMonitor import AnthropicCostMonitor


def _make_monitor():
    tab = Mock()
    tab.cost_error = ""
    tab.daily_costs = {}
    tab.cost_models = []
    tab.total_cost_30d = 0.0
    tab.total_cost_7d = 0.0
    tab.total_cost_1d = 0.0
    tab.cost_dates = []
    tab.cost_date_strs = []
    tab.cost_last_updated = 0.0
    return AnthropicCostMonitor(tab, fetch_interval=0), tab


def _bucket(date_str, results):
    """Build one API response bucket. amounts are in cents."""
    return {
        "starting_at": f"{date_str}T00:00:00Z",
        "results": [
            {"model": model, "amount": str(cents)}
            for model, cents in results
        ],
    }


@pytest.mark.unit
class TestAnthropicCostMonitorAggregation:

    @patch('common.AnthropicCostMonitor.config')
    @patch('common.AnthropicCostMonitor.requests.get')
    def test_aggregates_daily_costs_cents_to_dollars(self, mock_get, mock_config):
        mock_config.get.return_value = "test-key"
        mock_get.return_value = MagicMock(
            status_code=200,
            json=lambda: {
                "data": [
                    _bucket("2026-04-20", [("claude-opus-4-6", "5000")]),
                    _bucket("2026-04-19", [("claude-opus-4-6", "3000")]),
                ],
                "has_more": False,
            }
        )

        monitor, tab = _make_monitor()
        monitor._fetch_cost_data()

        assert tab.daily_costs["2026-04-20"]["claude-opus-4-6"] == 50.0
        assert tab.daily_costs["2026-04-19"]["claude-opus-4-6"] == 30.0
        assert tab.cost_error == ""

    @patch('common.AnthropicCostMonitor.config')
    @patch('common.AnthropicCostMonitor.requests.get')
    def test_filters_models_below_min_cost(self, mock_get, mock_config):
        mock_config.get.return_value = "test-key"
        mock_get.return_value = MagicMock(
            status_code=200,
            json=lambda: {
                "data": [
                    _bucket("2026-04-20", [
                        ("claude-opus-4-6", "5000"),   # $50 total -> kept
                        ("cheap-model", "50"),          # $0.50 total -> filtered
                    ]),
                ],
                "has_more": False,
            }
        )

        monitor, tab = _make_monitor()
        monitor._fetch_cost_data()

        assert "claude-opus-4-6" in tab.cost_models
        assert "cheap-model" not in tab.cost_models
        assert "cheap-model" not in tab.daily_costs.get("2026-04-20", {})

    @patch('common.AnthropicCostMonitor.config')
    @patch('common.AnthropicCostMonitor.requests.get')
    def test_aggregates_same_model_across_buckets(self, mock_get, mock_config):
        mock_config.get.return_value = "test-key"
        mock_get.return_value = MagicMock(
            status_code=200,
            json=lambda: {
                "data": [
                    _bucket("2026-04-20", [("claude-opus-4-6", "2000")]),
                    _bucket("2026-04-20", [("claude-opus-4-6", "3000")]),
                ],
                "has_more": False,
            }
        )

        monitor, tab = _make_monitor()
        monitor._fetch_cost_data()

        assert tab.daily_costs["2026-04-20"]["claude-opus-4-6"] == 50.0

    @patch('common.AnthropicCostMonitor.config')
    @patch('common.AnthropicCostMonitor.requests.get')
    def test_handles_pagination(self, mock_get, mock_config):
        mock_config.get.return_value = "test-key"
        mock_get.side_effect = [
            MagicMock(
                status_code=200,
                json=lambda: {
                    "data": [_bucket("2026-04-20", [("claude-opus-4-6", "5000")])],
                    "has_more": True,
                    "next_page": "page2",
                }
            ),
            MagicMock(
                status_code=200,
                json=lambda: {
                    "data": [_bucket("2026-04-19", [("claude-opus-4-6", "3000")])],
                    "has_more": False,
                }
            ),
        ]

        monitor, tab = _make_monitor()
        monitor._fetch_cost_data()

        assert "2026-04-20" in tab.daily_costs
        assert "2026-04-19" in tab.daily_costs
        assert mock_get.call_count == 2

    @patch('common.AnthropicCostMonitor.config')
    @patch('common.AnthropicCostMonitor.requests.get')
    def test_30_day_total_excludes_older_data(self, mock_get, mock_config):
        mock_config.get.return_value = "test-key"
        # Create a bucket 45 days ago — outside the 30-day window
        from datetime import datetime, timedelta, timezone
        now = datetime.now(timezone.utc)
        old_date = (now - timedelta(days=45)).strftime("%Y-%m-%d")
        recent_date = now.strftime("%Y-%m-%d")

        mock_get.return_value = MagicMock(
            status_code=200,
            json=lambda: {
                "data": [
                    _bucket(old_date, [("claude-opus-4-6", "10000")]),
                    _bucket(recent_date, [("claude-opus-4-6", "5000")]),
                ],
                "has_more": False,
            }
        )

        monitor, tab = _make_monitor()
        monitor._fetch_cost_data()

        # 30-day total should only include recent_date's $50
        assert tab.total_cost_30d == 50.0

    @patch('common.AnthropicCostMonitor.config')
    @patch('common.AnthropicCostMonitor.requests.get')
    def test_7d_and_1d_totals(self, mock_get, mock_config):
        mock_config.get.return_value = "test-key"
        from datetime import datetime, timedelta, timezone
        now = datetime.now(timezone.utc)
        today = now.strftime("%Y-%m-%d")
        three_days_ago = (now - timedelta(days=3)).strftime("%Y-%m-%d")
        ten_days_ago = (now - timedelta(days=10)).strftime("%Y-%m-%d")

        mock_get.return_value = MagicMock(
            status_code=200,
            json=lambda: {
                "data": [
                    _bucket(today, [("claude-opus-4-6", "2000")]),          # $20 - in 1d, 7d, 30d
                    _bucket(three_days_ago, [("claude-opus-4-6", "3000")]), # $30 - in 7d, 30d
                    _bucket(ten_days_ago, [("claude-opus-4-6", "5000")]),   # $50 - in 30d only
                ],
                "has_more": False,
            }
        )

        monitor, tab = _make_monitor()
        monitor._fetch_cost_data()

        assert tab.total_cost_1d == 20.0
        assert tab.total_cost_7d == 50.0
        assert tab.total_cost_30d == 100.0

    @patch('common.AnthropicCostMonitor.config')
    def test_missing_api_key_sets_error(self, mock_config):
        mock_config.get.return_value = ""

        monitor, tab = _make_monitor()
        monitor._fetch_cost_data()

        assert "api_keys.anthropic_admin" in tab.cost_error

    @patch('common.AnthropicCostMonitor.config')
    @patch('common.AnthropicCostMonitor.requests.get')
    def test_auth_failure_sets_error(self, mock_get, mock_config):
        mock_config.get.return_value = "bad-key"
        mock_get.return_value = MagicMock(status_code=401)

        monitor, tab = _make_monitor()
        monitor._fetch_cost_data()

        assert "Invalid admin API key" in tab.cost_error

    @patch('common.AnthropicCostMonitor.config')
    @patch('common.AnthropicCostMonitor.requests.get')
    def test_empty_response_no_crash(self, mock_get, mock_config):
        mock_config.get.return_value = "test-key"
        mock_get.return_value = MagicMock(
            status_code=200,
            json=lambda: {"data": [], "has_more": False}
        )

        monitor, tab = _make_monitor()
        monitor._fetch_cost_data()

        assert tab.cost_models == []
        assert tab.total_cost_30d == 0.0
        assert tab.total_cost_7d == 0.0
        assert tab.total_cost_1d == 0.0
        assert tab.cost_error == ""

    @patch('common.AnthropicCostMonitor.config')
    @patch('common.AnthropicCostMonitor.requests.get')
    def test_cost_dates_span_max_days(self, mock_get, mock_config):
        mock_config.get.return_value = "test-key"
        mock_get.return_value = MagicMock(
            status_code=200,
            json=lambda: {"data": [
                _bucket("2026-04-20", [("claude-opus-4-6", "200")])
            ], "has_more": False}
        )

        monitor, tab = _make_monitor()
        monitor._fetch_cost_data()

        assert len(tab.cost_dates) == AnthropicCostMonitor.MAX_DAYS
        assert len(tab.cost_date_strs) == AnthropicCostMonitor.MAX_DAYS
