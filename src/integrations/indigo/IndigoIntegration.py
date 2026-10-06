"""Indigo, the home-automation server, as the HomeAutomation backend, an Integration and a
Feed (plans/HOME_AUTOMATION_PLAN.md, plans/INDIGO_FEED_PLAN.md). The REST API (port 8176,
digest auth) runs the light and action commands and answers check_health. The state comes
from `prompt.txt`, a file a script on the Indigo host keeps current with one `## SECTION`
block per kind of thing (`"name": value` lines; the two `## LATEST AUTOMATION ... LOGS`
blocks are the error log), read over SSH. `fetch` runs the LogRefiner on the same host and
returns the day's `message:` events: no cursor and no backfill, every poll returns the whole
day and the history's dedup index on (timestamp, role, content) drops what is stored."""
import re
import subprocess
import time
import urllib.parse
from datetime import datetime, timedelta
from typing import List, Optional

import requests
from requests.auth import HTTPDigestAuth

from assistant.integration.HomeAutomation import Entity, HomeAutomation, HomeSnapshot
from assistant.integration.Feed import Feed, FeedBatch, FeedEntry
from assistant.integration.Integration import Integration
from common.ConfigManager import config
from common.logging import info, trace

POLL_INTERVAL = 5
REST_PORT = 8176
SSH_USER = "media"
PROMPT_FILE = "Documents/Floxy/prompt.txt"
LOG_REFINER = "/usr/local/bin/python3 Documents/Floxy/AI/Tools/LogRefiner/log_refiner.py"
# "HH:MM:SS (x mins ago) type: device" — what the refiner prints per event
LOG_LINE = re.compile(r'(\d{2}:\d{2}:\d{2})\s+\(.*?\)\s+(.+?):\s+(.+)')
# The refiner's clock times lag the moment the event was logged; the nudge keeps an event
# ordered after the request that caused it when both land in the same second.
EVENT_TIME_NUDGE = 2
# prompt.txt's `## SECTION` headings → the snapshot list they fill; the rest is the error log.
SNAPSHOT_SECTIONS = {"LIGHTS": "lights", "SENSORS": "sensors", "VARIABLES": "variables", "ACTIONS": "actions"}
ERROR_LOG_SECTION = "LATEST AUTOMATION ERROR LOGS"
STATE_LINE = re.compile(r'^"(.+?)": ?(.*)$')


class IndigoIntegration(Integration, Feed, HomeAutomation):
    """Indigo's REST API for the light and action commands, prompt.txt for the state, and the
    event log as a feed."""

    poll_interval = POLL_INTERVAL
    item_noun = "events"

    @property
    def section_filters(self) -> dict[str, tuple[str, ...]]:
        """`indigo.section_filters`: per section (lights / sensors / variables / actions), the
        substrings of the lines the prompt never needs."""
        configured = config.get('indigo.section_filters', {}) or {}
        return {section: tuple(drop or ()) for section, drop in configured.items()}

    def __init__(self):
        super().__init__()
        self._base_url: Optional[str] = None
        self._auth: Optional[HTTPDigestAuth] = None

    def _on_reset(self):
        from assistant.integration.FeedRunner import Feeds
        Feeds.get_instance().stop(self.feed_name)

    @property
    def name(self) -> str:
        return "Indigo"

    @property
    def config_keys(self) -> List[str]:
        return ["indigo.host", "indigo.login", "indigo.password"]

    @property
    def config_section(self) -> str:
        return "indigo"

    @property
    def base_url(self) -> str:
        if self._base_url is None:
            self._base_url = f"http://{config.get('indigo.host')}:{REST_PORT}"
        return self._base_url

    @property
    def auth(self) -> HTTPDigestAuth:
        if self._auth is None:
            self._auth = HTTPDigestAuth(config.get("indigo.login"), config.get("indigo.password"))
        return self._auth

    def check_health(self) -> bool:
        response = requests.get(f"{self.base_url}/variables.json", auth=self.auth, timeout=10)
        response.raise_for_status()
        return True

    # --- HomeAutomation: commands -----------------------------------------------------------

    def turn_on(self, name: str) -> str:
        return self._set_device(name, {'isOn': '1'})

    def turn_off(self, name: str) -> str:
        return self._set_device(name, {'isOn': '0'})

    def dim(self, name: str, level: int) -> str:
        return self._set_device(name, {'brightness': str(level)})

    def execute(self, name: str) -> str:
        url = f"{self.base_url}/actions/{self._encode_name(name)}?_method=execute"
        return self._call(lambda: requests.get(url, auth=self.auth, timeout=10))

    def _set_device(self, name: str, data: dict) -> str:
        url = f"{self.base_url}/devices/{self._encode_name(name)}"
        return self._call(lambda: requests.put(url, data=data, auth=self.auth, timeout=10))

    @staticmethod
    def _encode_name(name: str) -> str:
        return urllib.parse.quote_plus(name).replace("+", "%20")

    def _call(self, request) -> str:
        try:
            return self._parse_response(request())
        except requests.Timeout:
            return "Request timed out"
        except requests.ConnectionError:
            return "Could not connect to home automation server"
        except Exception as e:
            return str(e)

    @staticmethod
    def _parse_response(response: requests.Response) -> str:
        """"" when Indigo accepted the request, else the error it reports: the body of a
        failed response, or the <center>...</center> message its HTML pages carry."""
        if not response.ok:
            return response.text.strip() or f"HTTP {response.status_code}"
        text = response.text
        start = text.find("<center>")
        if start != -1:
            end = text.find("</center>", start)
            if end != -1:
                error_text = text[start + len("<center>"):end].strip()
                if error_text:
                    return error_text
        return ""

    # --- HomeAutomation: state --------------------------------------------------------------

    def snapshot(self) -> HomeSnapshot:
        sections = self.parse_sections(self._read_prompt_file())
        snapshot = HomeSnapshot(taken_at=time.time())
        for heading, lines in sections.items():
            attr = SNAPSHOT_SECTIONS.get(heading)
            if attr is None:
                if "LOGS" not in heading:
                    trace(f"Indigo: prompt.txt section '{heading}' is not part of the snapshot — dropped")
                continue
            getattr(snapshot, attr).extend(self.parse_entities(lines))
        return snapshot

    def error_logs(self) -> str:
        sections = self.parse_sections(self._read_prompt_file())
        log = sections.get(ERROR_LOG_SECTION)
        if log is None:
            info("Indigo: prompt.txt has no error log section")
            return "Indigo reported no error log."
        text = HomeAutomation.to_display("\n".join(log)).strip()
        info(f"Indigo error log: {len(text)} chars")
        return f"## {ERROR_LOG_SECTION}\n{text}"

    @staticmethod
    def parse_sections(text: str) -> dict[str, list[str]]:
        """prompt.txt's `## HEADING` blocks: heading → its non-blank lines, stripped."""
        sections: dict[str, list[str]] = {}
        current = None
        for line in text.split('\n'):
            line = line.strip()
            if line.startswith("##"):
                current = line.strip('#').strip()
                sections[current] = []
            elif current and line:
                sections[current].append(line)
        return sections

    @staticmethod
    def parse_entities(lines: list[str]) -> list[Entity]:
        """`"name": value` lines as entities; anything else in a state section is traced and skipped."""
        entities = []
        for line in lines:
            match = STATE_LINE.match(line)
            if match:
                entities.append(Entity(match.group(1), match.group(2).strip()))
            else:
                trace(f"Indigo: not a state line, skipped: {line[:80]}")
        return entities

    def _read_prompt_file(self) -> str:
        result = subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10",
                                 f"{SSH_USER}@{config.get('indigo.host')}", f"cat {PROMPT_FILE}"],
                                capture_output=True, text=True, timeout=15)
        if result.returncode != 0 or not result.stdout:
            raise RuntimeError(f"could not read {PROMPT_FILE} on the Indigo host: "
                               f"exit {result.returncode} {result.stderr.strip()}")
        return result.stdout

    # --- Feed ---------------------------------------------------------------------------------

    @property
    def feed_name(self) -> str:
        return "Indigo"

    @property
    def config_slug(self) -> str:
        return "indigo"

    def initial_cursor(self, horizon_ts: float) -> Optional[str]:
        return None

    def fetch(self, cursor: Optional[str], backfill: bool) -> FeedBatch:
        """The day's events. A failed SSH call is an empty batch, not a feed failure: the
        Indigo Mac reboots now and then, and the next poll simply tries again."""
        try:
            output = self._run_log_refiner()
        except Exception as e:
            trace(f"Indigo: could not fetch the event log: {type(e).__name__}: {e}")
            return FeedBatch()
        return FeedBatch(self.parse_events(output, datetime.now()))

    @staticmethod
    def parse_events(output: str, now: datetime) -> list[FeedEntry]:
        """The `message:` lines as entries. The refiner prints clock times only, so the date
        is today's — or yesterday's when the clock time is still ahead of `now`."""
        entries = []
        for line in output.strip().split('\n'):
            if "message:" not in line:
                continue
            match = LOG_LINE.match(line)
            if not match:
                continue
            hour, minute, second = map(int, match.group(1).split(':'))
            event_time = now.replace(hour=hour, minute=minute, second=second, microsecond=0)
            if event_time > now:
                event_time -= timedelta(days=1)
            entries.append(FeedEntry(event_time.timestamp() + EVENT_TIME_NUDGE, match.group(3)))
        return entries

    def _run_log_refiner(self) -> str:
        result = subprocess.run(
            ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10",
             f"{SSH_USER}@{config.get('indigo.host')}", LOG_REFINER],
            capture_output=True, text=True, timeout=15)
        if result.returncode != 0:
            raise RuntimeError(f"log_refiner exited {result.returncode}: {result.stderr.strip()}")
        return result.stdout
