import logging
from datetime import datetime, timedelta, date
from typing import List

import caldav
import pytz

from common.ConfigManager import config
from ..Integration import Integration
from common.logging import info, error, trace


class CalDAVIntegration(Integration):
    """Reads and creates calendar events on the iCloud CalDAV server."""

    @property
    def name(self) -> str:
        return "CalDAV"

    @property
    def config_keys(self) -> List[str]:
        return ["calendar"]

    def check_health(self) -> bool:
        username = config.get('calendar.username')
        password = config.get('calendar.password')
        if not username or not password:
            return False
        client = caldav.DAVClient('https://caldav.icloud.com/', username=username, password=password)
        principal = client.principal()
        calendars = principal.calendars()
        if calendars:
            today = date.today()
            calendars[0].date_search(
                start=datetime.combine(today, datetime.min.time()),
                end=datetime.combine(today + timedelta(days=1), datetime.min.time())
            )
        return True

    @staticmethod
    def fetch_calendar_events(start_date: date, end_date: date) -> str:
        try:
            logging.getLogger("root").setLevel(logging.CRITICAL)

            def extract_time(ev, key) -> datetime:
                time_value = ev.vobject_instance.vevent.contents[key][0].value
                if isinstance(time_value, str):
                    dt = datetime.strptime(time_value, "%Y-%m-%dT%H:%M:%SZ")
                    return dt.replace(tzinfo=pytz.UTC)
                return time_value

            def make_sure_is_datetime(dt) -> datetime:
                if isinstance(dt, date) and not isinstance(dt, datetime):
                    return datetime.combine(dt, datetime.min.time()).replace(tzinfo=pytz.UTC)
                if isinstance(dt, datetime) and dt.tzinfo is None:
                    return dt.replace(tzinfo=pytz.UTC)
                return dt

            username = config.get('calendar.username', "")
            password = config.get('calendar.password', "")
            if not username or not password:
                return "(Calendar not configured)"

            url = 'https://caldav.icloud.com/'
            client = caldav.DAVClient(url, username=username, password=password)
            principal = client.principal()
            calendars = principal.calendars()

            parsed_events = []
            for calendar in calendars:
                events = calendar.date_search(
                    start=datetime.combine(start_date, datetime.min.time()),
                    end=datetime.combine(end_date, datetime.min.time())
                )
                for event in events:
                    time_start = extract_time(event, 'dtstart')
                    time_end = extract_time(event, 'dtend')
                    title = event.vobject_instance.vevent.summary.value
                    parsed_events.append((make_sure_is_datetime(time_start), make_sure_is_datetime(time_end), title))

            parsed_events.sort(key=lambda x: x[0])

            local_tz = pytz.timezone(config.get_home_timezone())
            start_dt = datetime.combine(start_date, datetime.min.time()).replace(tzinfo=pytz.UTC)
            end_dt = datetime.combine(end_date, datetime.min.time()).replace(tzinfo=pytz.UTC)

            lines = []
            for time_start, time_end, title in parsed_events:
                if start_dt <= time_start <= end_dt or start_dt <= time_end <= end_dt:
                    local_start = time_start.astimezone(local_tz)
                    local_end = time_end.astimezone(local_tz)
                    title = title.replace("\n", " ")
                    if local_start.date() == local_end.date():
                        lines.append(f"{local_start.strftime('%Y-%m-%d')} {local_start.strftime('%H:%M')} - {local_end.strftime('%H:%M')}: {title}")
                    else:
                        lines.append(f"{local_start.strftime('%Y-%m-%d %H:%M')} - {local_end.strftime('%Y-%m-%d %H:%M')}: {title}")

            return "\n".join(lines) if lines else "(No upcoming events)"

        except Exception as e:
            error(f"Error fetching calendar events: {e}")
            return "(No upcoming events)"

    @staticmethod
    def create_calendar_event(title: str, date_str: str, start_time: str, end_time: str) -> None:
        logging.getLogger("root").setLevel(logging.CRITICAL)

        username = config.get('calendar.username')
        password = config.get('calendar.password')
        if not username or not password:
            raise ValueError("Calendar username or password not found in config")

        local_tz = pytz.timezone(config.get_home_timezone())
        dt_start = local_tz.localize(datetime.strptime(f"{date_str} {start_time}", "%Y-%m-%d %H:%M"))
        dt_end = local_tz.localize(datetime.strptime(f"{date_str} {end_time}", "%Y-%m-%d %H:%M"))
        url = 'https://caldav.icloud.com/'
        client = caldav.DAVClient(url, username=username, password=password)
        principal = client.principal()

        target_name = config.get('calendar.default_calendar', 'Home')
        calendar = None
        for cal in principal.calendars():
            if cal.name == target_name:
                calendar = cal
                break

        if calendar is None:
            available = [cal.name for cal in principal.calendars()]
            raise ValueError(f"Calendar '{target_name}' not found. Available: {available}")

        calendar.save_event(dtstart=dt_start, dtend=dt_end, summary=title)
        info(f"Created calendar event: {title} on {date_str} {start_time}-{end_time}")


fetch_calendar_events = CalDAVIntegration.fetch_calendar_events
create_calendar_event = CalDAVIntegration.create_calendar_event
