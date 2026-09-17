import os
import sqlite3
import subprocess
import threading
import time
from datetime import datetime
from typing import Optional

from .TabView import TabView, SUB_CONTENT_TOP
from interfaces.console.framework.TUIScreen import TUIScreen, Color
from interfaces.console.framework.TextWidth import clip, display_width
from common.FloxyDatabase import FloxyDatabase
from assistant.integration.integrations.photos.PhotoProcessor import PhotoProcessor, PHOTOS_SQLITE_PATH

SCORE_BAR_WIDTH = 20

SORT_MODES = [
    ("creation_date", False, "Date (oldest)"),
    ("creation_date", True, "Date (newest)"),
    ("indexed_at", True, "Recently indexed"),
    ("pretty_score", True, "Pretty score"),
    ("family_importance_score", True, "Family score"),
]


class PhotosTab(TabView):
    """Tab for browsing the indexed photo library and triggering photo processing."""

    COLOR_WHITE = Color(220, 220, 220)
    COLOR_GRAY = Color(150, 150, 150)
    COLOR_DIM = Color(100, 100, 100)
    COLOR_ACCENT = Color(110, 160, 220)
    COLOR_BAR_FILLED = Color(90, 160, 230)
    COLOR_BAR_EMPTY = Color(50, 55, 65)
    COLOR_LABEL = Color(140, 145, 155)
    COLOR_GREEN = Color(80, 200, 120)
    COLOR_YELLOW = Color(220, 200, 80)
    COLOR_RED = Color(220, 80, 80)

    def __init__(self):
        self._index: int = 0
        self._count: int = 0
        self._total_apple: int = 0
        self._photo: Optional[dict] = None
        self._sort_mode_idx: int = 1
        self._last_load_time: float = 0
        self._reload_interval: float = 30.0
        self._loading: bool = False
        self._processing: bool = False
        self._process_status: str = ""
        self._process_status_type: str = ""

    def _current_sort(self):
        return SORT_MODES[self._sort_mode_idx]

    def _load_data(self):
        if self._loading:
            return
        self._loading = True

        def load():
            try:
                db = FloxyDatabase.get_instance()
                self._count = db.get_photo_count()
                self._total_apple = self._get_apple_photo_count()
                if self._count == 0:
                    self._photo = None
                    self._index = 0
                    return
                self._index = max(0, min(self._index, self._count - 1))
                sort_col, sort_desc, _ = self._current_sort()
                results = db.get_photos_paginated(
                    offset=self._index, limit=1,
                    sort_by=sort_col, sort_desc=sort_desc)
                self._photo = results[0] if results else None
            finally:
                self._last_load_time = time.time()
                self._loading = False

        threading.Thread(target=load, daemon=True).start()

    def _navigate(self, delta: int):
        if self._count == 0:
            return
        new_index = max(0, min(self._index + delta, self._count - 1))
        if new_index != self._index:
            self._index = new_index
            self._load_data()

    def _go_to(self, index: int):
        if self._count == 0:
            return
        clamped = max(0, min(index, self._count - 1))
        if clamped != self._index:
            self._index = clamped
            self._load_data()

    def draw(self, screen: TUIScreen, frame: int):
        now = time.time()
        if now - self._last_load_time > self._reload_interval:
            self._load_data()

        screen.reset_color()
        y = SUB_CONTENT_TOP
        _, _, sort_label = self._current_sort()

        # Header line
        if self._count == 0:
            left = "No photos indexed"
        else:
            left = f"Photo {self._index + 1:,} of {self._count:,}"
        if self._total_apple > 0:
            left += f"  ({self._total_apple:,} in iCloud library)"
        right = f"Sort: {sort_label}"
        screen.set_color(self.COLOR_WHITE)
        screen.bold()
        screen.write_at(2, y, left)
        screen.reset_color()
        screen.set_color(self.COLOR_DIM)
        screen.write_at(max(len(left) + 4, screen.width - len(right) - 1), y, right)
        screen.reset_color()

        # Separator
        y += 1
        screen.set_color(self.COLOR_DIM)
        screen.write_at(2, y, "\u2500" * (screen.width - 3))
        screen.reset_color()

        if self._photo is None:
            y += 2
            screen.set_color(self.COLOR_GRAY)
            if self._loading:
                screen.write_at(2, y, "Loading...")
            else:
                screen.write_at(2, y, "No photos to display.")
            self._draw_footer(screen)
            return

        p = self._photo
        max_w = screen.width - 4

        # Short description (title)
        y += 1
        screen.set_color(self.COLOR_ACCENT)
        screen.bold()
        screen.write_at(2, y, clip(p.get("short_description") or "", max_w))
        screen.reset_color()

        # Detailed description (word-wrapped)
        y += 2
        detail = p.get("detailed_description") or ""
        for line in self._wrap(detail, max_w):
            screen.set_color(self.COLOR_GRAY)
            screen.write_at(2, y, line)
            y += 1

        label_x = 2
        value_x = 23

        # Date
        y += 1
        self._draw_field(screen, y, label_x, value_x, "Date:", self._format_date(p.get("creation_date")))

        # Location
        y += 1
        loc_parts = []
        if p.get("location_text"):
            loc_parts.append(p["location_text"].replace("/", ", "))
        if p.get("latitude") is not None and p.get("longitude") is not None:
            loc_parts.append(f"({p['latitude']:.4f}, {p['longitude']:.4f})")
        self._draw_field(screen, y, label_x, value_x, "Location:", "  ".join(loc_parts) if loc_parts else "\u2014")

        # Albums
        y += 1
        self._draw_field(screen, y, label_x, value_x, "Albums:", p.get("albums") or "\u2014")

        # Persons
        y += 1
        self._draw_field(screen, y, label_x, value_x, "Persons:", p.get("persons") or "\u2014")

        # Type
        y += 1
        self._draw_field(screen, y, label_x, value_x, "Type:", self._type_label(p))

        # Score bars
        y += 2
        self._draw_score_bar(screen, y, label_x, value_x, "Pretty score:", p.get("pretty_score", 0))
        y += 1
        self._draw_score_bar(screen, y, label_x, value_x, "Family score:", p.get("family_importance_score", 0))

        # Indexed at
        y += 2
        self._draw_field(screen, y, label_x, value_x, "Indexed at:", self._format_date(p.get("indexed_at")))

        # ID
        y += 1
        screen.set_color(self.COLOR_DIM)
        screen.write_at(label_x, y, clip(f"ID: {p.get('asset_guid', '')}", max_w))

        # Processing status
        if self._processing or self._process_status:
            y += 2
            max_w = screen.width - 4
            if self._processing:
                screen.set_color(self.COLOR_YELLOW)
                screen.write_at(2, y, "Processing...")
            elif self._process_status_type == "ok":
                screen.set_color(self.COLOR_GREEN)
                screen.write_at(2, y, clip(self._process_status, max_w))
            elif self._process_status_type == "none":
                screen.set_color(self.COLOR_GRAY)
                screen.write_at(2, y, clip(self._process_status, max_w))
            elif self._process_status_type == "error":
                screen.set_color(self.COLOR_RED)
                screen.write_at(2, y, clip(self._process_status, max_w))

        self._draw_footer(screen)

    def _draw_footer(self, screen: TUIScreen):
        screen.reset_color()
        screen.set_color(self.COLOR_DIM)
        screen.write_at(2, screen.height - 1,
                        " \u2191\u2193 prev/next  PgUp/PgDn \u00b110  Home/End  Ctrl-S sort  Ctrl-O open  Ctrl-P process  Ctrl-B bulk ")

    def _draw_field(self, screen: TUIScreen, y: int, label_x: int, value_x: int, label: str, value: str):
        screen.set_color(self.COLOR_LABEL)
        screen.write_at(label_x, y, label)
        screen.set_color(self.COLOR_WHITE)
        screen.write_at(value_x, y, value[:screen.width - value_x - 1])

    def _draw_score_bar(self, screen: TUIScreen, y: int, label_x: int, value_x: int, label: str, score: int):
        screen.set_color(self.COLOR_LABEL)
        screen.write_at(label_x, y, label)
        filled = round(score / 100 * SCORE_BAR_WIDTH)
        screen.set_color(self.COLOR_BAR_FILLED)
        screen.write_at(value_x, y, "\u2588" * filled)
        screen.set_color(self.COLOR_BAR_EMPTY)
        screen.write_at(value_x + filled, y, "\u2591" * (SCORE_BAR_WIDTH - filled))
        screen.set_color(self.COLOR_WHITE)
        screen.write_at(value_x + SCORE_BAR_WIDTH + 1, y, str(score))

    @staticmethod
    def _wrap(text: str, width: int) -> list:
        if not text:
            return []
        words = text.split()
        lines = []
        current = ""
        for word in words:
            if current and display_width(current) + 1 + display_width(word) > width:
                lines.append(current)
                current = word
            else:
                current = f"{current} {word}" if current else word
        if current:
            lines.append(current)
        return lines

    @staticmethod
    def _type_label(p: dict) -> str:
        label = p.get("photo_type") or "\u2014"
        duration = p.get("duration_seconds")
        return f"{label} \u00b7 {duration:.0f}s" if duration else label

    @staticmethod
    def _format_date(ts) -> str:
        if ts is None:
            return "\u2014"
        try:
            dt = datetime.fromtimestamp(float(ts))
            return dt.strftime("%B %d, %Y at %I:%M %p").replace(" 0", " ")
        except (ValueError, OSError):
            return "\u2014"

    @staticmethod
    def _get_apple_photo_count() -> int:
        try:
            if not os.path.exists(PHOTOS_SQLITE_PATH):
                return 0
            conn = sqlite3.connect(f"file:{PHOTOS_SQLITE_PATH}?mode=ro", uri=True)
            try:
                cursor = conn.cursor()
                cursor.execute("SELECT COUNT(*) FROM ZASSET WHERE ZKIND = 0 AND ZTRASHEDSTATE = 0 AND ZHIDDEN = 0")
                return cursor.fetchone()[0]
            finally:
                conn.close()
        except Exception:
            return 0

    def _trigger_process(self, count: int = 1):
        if self._processing:
            return
        self._processing = True
        self._process_status = ""
        self._process_status_type = ""

        def on_progress(ok, fail, total):
            self._process_status = f"Processing... {ok + fail}/{total} done"

        def run():
            try:
                processor = PhotoProcessor()
                if count == 1:
                    status, message = processor.process_next_unprocessed(album="Favorites")
                else:
                    status, message = processor.process_bulk_unprocessed(
                        count=count, album="Favorites", on_progress=on_progress)
                self._process_status_type = status
                self._process_status = message
            except Exception as e:
                self._process_status_type = "error"
                self._process_status = f"Error: {e}"
            finally:
                self._processing = False
                self._load_data()

        threading.Thread(target=run, daemon=True).start()

    def _open_in_photos(self):
        if self._photo is None:
            return
        asset_guid = self._photo.get("asset_guid", "")
        if not asset_guid:
            return
        # AppleScript only understands the machine-local UUID — resolve it from the GUID.
        try:
            conn = sqlite3.connect(f"file:{PHOTOS_SQLITE_PATH}?mode=ro", uri=True)
            try:
                row = conn.execute(
                    "SELECT ZUUID FROM ZASSET WHERE ZCLOUDASSETGUID = ?", (asset_guid,)).fetchone()
            finally:
                conn.close()
        except sqlite3.Error:
            return
        if not row or not row[0]:
            return
        subprocess.Popen(
            ["osascript", "-e", f'tell application "Photos" to spotlight media item id "{row[0]}"'],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def handle_input(self, key: str) -> bool:
        if key == '\x0f':
            self._open_in_photos()
            return True
        if key == '\x10':
            self._trigger_process(count=1)
            return True
        if key == '\x02':  # Ctrl+B - bulk process
            self._trigger_process(count=200)
            return True
        if key == 'up':
            self._navigate(-1)
            return True
        if key == 'down':
            self._navigate(1)
            return True
        if key == 'pageup':
            self._navigate(-10)
            return True
        if key == 'pagedown':
            self._navigate(10)
            return True
        if key == 'home':
            self._go_to(0)
            return True
        if key == 'end':
            self._go_to(self._count - 1)
            return True
        if key == '\x13':
            self._sort_mode_idx = (self._sort_mode_idx + 1) % len(SORT_MODES)
            self._index = 0
            self._load_data()
            return True
        return False

    def on_activate(self):
        self._load_data()
