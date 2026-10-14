import threading
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Optional

import requests

from common.logging import fine, trace
from .ConfigManager import config


class AnthropicCostMonitor:
    """Periodically fetches Anthropic organization spend per workspace into the DashboardTab.

    Requires the `api_keys.anthropic_admin` key; runs on a daemon thread and reports
    fetch problems through the tab's cost_error rather than raising. Every API key belongs
    to one workspace, so a workspace per machine splits the bill per machine; the cost
    report reports the default workspace without an id, shown as "Default".
    """

    API_URL = "https://api.anthropic.com/v1/organizations/cost_report"
    WORKSPACES_URL = "https://api.anthropic.com/v1/organizations/workspaces"
    DEFAULT_WORKSPACE = "Default"
    MIN_WORKSPACE_COST = 1.0  # Only show workspaces with > $1 total spend
    MAX_PAGES = 10
    MAX_DAYS = 60
    API_PAGE_LIMIT = 31

    def __init__(self, dashboard_tab, fetch_interval: float = 300.0):
        self._dashboard_tab = dashboard_tab
        self._fetch_interval = fetch_interval
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self):
        if self._thread and self._thread.is_alive() and not self._stop_event.is_set():
            return
        # A stopped loop still mid-sample keeps the event it was started with and ends on it;
        # this start gets a fresh one. With one event shared, coming back to the tab before
        # the old loop had ended started nothing and left the monitor dead while the tab showed.
        self._stop_event = threading.Event()
        self._thread = threading.Thread(target=self._monitor_loop, args=(self._stop_event,), daemon=True,
                                        name="AnthropicCostMonitor")
        self._thread.start()
        fine("AnthropicCostMonitor started")

    def stop(self):
        """Returns at once — a tab switch calls it on the TUI's input thread, and a join here
        held every keypress back while the loop was mid-sample. The loop ends by itself at
        its next look at its event."""
        self._stop_event.set()

    def _monitor_loop(self, stop_event: threading.Event):
        while not stop_event.is_set():
            try:
                self._fetch_cost_data()
            except Exception as e:
                trace(f"AnthropicCostMonitor error: {e}")
                self._dashboard_tab.cost_error = str(e)
            stop_event.wait(self._fetch_interval)

    def _fetch_cost_data(self):
        api_key = config.get('api_keys.anthropic_admin', '')
        if not api_key:
            self._dashboard_tab.cost_error = "Configure 'api_keys.anthropic_admin' in config.yaml"
            trace("AnthropicCostMonitor: no admin API key configured")
            return

        now = datetime.now(timezone.utc)
        starting_at = (now - timedelta(days=self.MAX_DAYS)).strftime("%Y-%m-%dT00:00:00Z")
        ending_at = (now + timedelta(days=1)).strftime("%Y-%m-%dT00:00:00Z")

        headers = {
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
        }
        params = {
            "starting_at": starting_at,
            "ending_at": ending_at,
            "bucket_width": "1d",
            "limit": self.API_PAGE_LIMIT,
            "group_by[]": "workspace_id",
        }

        # Collect all day buckets, handling pagination
        all_buckets = []
        page = None
        for _ in range(self.MAX_PAGES):
            if page:
                params["page"] = page

            resp = requests.get(self.API_URL, headers=headers, params=params, timeout=30)

            if resp.status_code in (401, 403):
                self._dashboard_tab.cost_error = "Invalid admin API key"
                trace(f"AnthropicCostMonitor: auth failed ({resp.status_code})")
                return

            if resp.status_code >= 400:
                trace(f"AnthropicCostMonitor: API error {resp.status_code}: {resp.text[:200]}")
            resp.raise_for_status()
            body = resp.json()
            all_buckets.extend(body.get("data", []))

            if body.get("has_more"):
                page = body.get("next_page")
            else:
                break

        workspace_ids = {result.get("workspace_id") for bucket in all_buckets for result in bucket.get("results", [])}
        names = self._workspace_names(headers) if workspace_ids - {None} else {}

        # Aggregate: {date_str: {workspace: total_dollars}}
        # Response structure: each bucket has starting_at + results[]
        # Each result has workspace_id (None for the default one), amount (cents as decimal string)
        daily_costs = defaultdict(lambda: defaultdict(float))
        workspace_totals = defaultdict(float)

        for bucket in all_buckets:
            date_str = bucket.get("starting_at", "")[:10]  # "2026-04-15"
            for result in bucket.get("results", []):
                workspace_id = result.get("workspace_id")
                workspace = names.get(workspace_id, workspace_id) if workspace_id else self.DEFAULT_WORKSPACE
                amount_dollars = float(result.get("amount", "0")) / 100.0
                daily_costs[date_str][workspace] += amount_dollars
                workspace_totals[workspace] += amount_dollars

        significant = sorted(w for w, total in workspace_totals.items() if total > self.MIN_WORKSPACE_COST)
        if len(significant) < len(workspace_totals):
            trace(f"AnthropicCostMonitor: left out workspaces at <= ${self.MIN_WORKSPACE_COST:.0f}: "
                  f"{sorted(set(workspace_totals) - set(significant))}")

        filtered_daily = {}
        for date_str, workspaces in daily_costs.items():
            filtered = {w: cost for w, cost in workspaces.items() if w in significant}
            if filtered:
                filtered_daily[date_str] = filtered

        today = now.date()
        cost_dates = [(today - timedelta(days=self.MAX_DAYS - 1 - i)) for i in range(self.MAX_DAYS)]
        cost_date_strs = [d.strftime("%Y-%m-%d") for d in cost_dates]

        thirty_day_strs = set(cost_date_strs[-30:])
        seven_day_strs = set(cost_date_strs[-7:])
        today_str = {cost_date_strs[-1]}
        yesterday_str = {cost_date_strs[-2]} if len(cost_date_strs) >= 2 else set()

        def _sum_costs(date_set):
            return sum(sum(models.values()) for ds, models in filtered_daily.items() if ds in date_set)

        self._dashboard_tab.daily_costs = filtered_daily
        self._dashboard_tab.cost_workspaces = significant
        self._dashboard_tab.total_cost_30d = _sum_costs(thirty_day_strs)
        self._dashboard_tab.total_cost_7d = _sum_costs(seven_day_strs)
        self._dashboard_tab.total_cost_1d = _sum_costs(today_str)
        self._dashboard_tab.total_cost_yesterday = _sum_costs(yesterday_str)
        self._dashboard_tab.cost_dates = cost_dates
        self._dashboard_tab.cost_date_strs = cost_date_strs
        self._dashboard_tab.cost_error = ""
        self._dashboard_tab.cost_last_updated = time.time()

        fine(f"AnthropicCostMonitor: fetched {len(all_buckets)} day buckets, "
             f"{len(significant)} workspaces > $1 {significant}, "
             f"30d ${self._dashboard_tab.total_cost_30d:.0f} / 7d ${self._dashboard_tab.total_cost_7d:.0f} / "
             f"yesterday ${self._dashboard_tab.total_cost_yesterday:.0f} / 1d ${self._dashboard_tab.total_cost_1d:.0f}")

    def _workspace_names(self, headers: dict) -> dict:
        """Workspace id -> name, archived ones included so their past spend keeps its name;
        {} when the list cannot be read, and the chart shows the ids instead."""
        try:
            resp = requests.get(self.WORKSPACES_URL, headers=headers, timeout=30,
                                params={"limit": 1000, "include_archived": "true"})
            resp.raise_for_status()
            names = {w["id"]: w["name"] for w in resp.json().get("data", [])}
            trace(f"AnthropicCostMonitor: {len(names)} workspaces named")
            return names
        except Exception as e:
            trace(f"AnthropicCostMonitor: workspace names unavailable, showing ids: {e}")
            return {}
