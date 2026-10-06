"""The Photos tab: the indexed photo library, one photo at a time, as a board in the Dashboard's
tiles — summary tiles (how much of the library is indexed, where the browsing stands, what the
indexing last did), the photo itself, its description and its details with the two scores as
meters. From 150 columns the picture stands at the left with the description over the details
beside it; a narrower screen stacks the three. The picture is fetched in the background as the
browsing moves (`PhotoProcessor.image_bytes_for_asset`, iCloud included) and drawn through
`TUIScreen.image`; a video has no preview."""
import os
import sqlite3
import subprocess
import threading
import time
from datetime import datetime
from typing import List, Optional

from interfaces.console.tabs.IntegrationTab import IntegrationTab
from interfaces.console.tabs.TabView import SUB_CONTENT_TOP
from common.logging import info, trace
from interfaces.console import Theme
from interfaces.console.framework.Pictures import CELL_ASPECT, Picture
from interfaces.console.framework.TUIScreen import TUIScreen
from interfaces.console.framework.TextWidth import wrap
from interfaces.console.widgets import Board, Tile
from interfaces.console.widgets.Board import Fact, SummaryTile
from common.FloxyDatabase import FloxyDatabase
from integrations.photos.PhotoProcessor import PhotoProcessor, PHOTOS_SQLITE_PATH
from integrations.photos.PhotosIntegration import PhotosIntegration

DETAILS_MIN_W = 60
FACT_LABEL_W = 14
DESCRIPTION_MIN_ROWS = 2
PICTURE_MIN_ROWS = 4         # fewer rows of picture than this is not worth a card
PICTURE_SHARE_H = 45         # percent of the board's height the picture takes when the three are stacked
PICTURE_SHARE_W = 50         # percent of the width the picture card may take beside the text
DEFAULT_ASPECT = 1.5         # a 3:2 photo: what the card is sized for while the picture is still loading
NO_PREVIEW = {"loading": "Loading…", "video": "Video — no preview", "none": "No preview"}
NONE = "\u2014"
HINTS = "\u2191\u2193 prev/next  PgUp/PgDn \u00b110  Home/End  Ctrl-S sort  Ctrl-O open  Ctrl-P process  Ctrl-B bulk"
# The colour of an indexing outcome by PhotoProcessor's status
PROCESS_COLORS = {"ok": Theme.COLOR_GREEN, "none": Theme.COLOR_LABEL, "error": Theme.COLOR_RED}

SORT_MODES = [
    ("creation_date", False, "Date (oldest)"),
    ("creation_date", True, "Date (newest)"),
    ("indexed_at", True, "Recently indexed"),
    ("pretty_score", True, "Pretty score"),
    ("family_importance_score", True, "Family score"),
]


class PhotosTab(IntegrationTab):
    """Tab for browsing the indexed photo library and triggering photo processing."""

    integration = PhotosIntegration

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
        self._cards: List[Tile.Card] = []
        self._picture: Optional[Picture] = None       # the current photo's picture, once fetched
        self._picture_guid: Optional[str] = None      # the asset the picture (or its absence) is for
        self._picture_state: str = "none"             # a NO_PREVIEW key while there is no picture
        self._picture_pending: set = set()            # asset guids a fetch is running for

    def _current_sort(self):
        return SORT_MODES[self._sort_mode_idx]

    # --- the picture -------------------------------------------------------------------------

    def _request_picture(self, photo: Optional[dict]):
        """Fetch the photo's picture in the background unless it is the one already shown or
        being fetched; a video gets no preview. The card shows "Loading…" meanwhile, and a
        fetch that finishes after the browsing has moved on is dropped."""
        guid = (photo or {}).get("asset_guid") or None
        if guid == self._picture_guid:
            return
        self._picture, self._picture_guid = None, guid
        if guid is None:
            self._picture_state = "none"
            return
        if (photo.get("photo_type") or "").startswith("video"):
            self._picture_state = "video"
            trace(f"Photos tab: {guid} is a video, no preview")
            return
        self._picture_state = "loading"
        if guid in self._picture_pending:
            trace(f"Photos tab: picture for {guid} is already being fetched")
            return
        self._picture_pending.add(guid)

        def fetch():
            try:
                picture = Picture.decode(PhotoProcessor.image_bytes_for_asset(guid))
            except Exception as e:
                info(f"Photos tab: fetching the picture for {guid} failed: {e}")
                picture = None
            finally:
                self._picture_pending.discard(guid)
            if self._picture_guid != guid:
                trace(f"Photos tab: picture for {guid} arrived after the browsing moved on, dropped")
                return
            self._picture = picture
            self._picture_state = "none"
            if picture is None:
                info(f"Photos tab: no picture for {guid}")

        threading.Thread(target=fetch, daemon=True).start()

    def _picture_card_width(self, available: int, height: int) -> int:
        """The columns the picture card takes beside the text: what the picture (or a 3:2
        frame while it loads) needs at the card's height, at most PICTURE_SHARE_W percent."""
        inner_rows = max(1, height - Tile.CARD_CHROME)
        max_cols = max(1, available * PICTURE_SHARE_W // 100 - 4)
        if self._picture is not None:
            cols, _ = self._picture.fit(max_cols, inner_rows)
        else:
            cols = min(max_cols, round(inner_rows * DEFAULT_ASPECT / CELL_ASPECT))
        return cols + 4

    def _draw_picture(self, screen: TUIScreen, x: int, y: int, width: int, height: int):
        """The picture card: the picture centred in the card's body, or why there is none."""
        if height <= Tile.CARD_CHROME:
            return
        picture = self._picture
        value = f"{picture.width} × {picture.height}" if picture is not None else ""
        Tile.draw_card(screen, self._cards, x, y, width, height, "Photo", value, Theme.COLOR_LABEL)
        inner_x, inner_y, inner_w, inner_h = x + 2, y + Tile.CARD_TOP, width - 4, height - Tile.CARD_CHROME
        if picture is None:
            screen.set_color(Theme.COLOR_DIM)
            screen.write_at(inner_x, inner_y, NO_PREVIEW[self._picture_state])
            screen.reset_color()
            return
        cols, rows = picture.fit(inner_w, inner_h)
        screen.image(inner_x + (inner_w - cols) // 2, inner_y + (inner_h - rows) // 2, picture, cols, rows)

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
                self._request_picture(self._photo)
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
        if time.time() - self._last_load_time > self._reload_interval:
            self._load_data()

        self._cards = []
        available = screen.width - Board.LEFT - Board.RIGHT_MARGIN
        bottom = screen.height - 1                # the last row is the key hints'
        y = SUB_CONTENT_TOP
        Board.draw_summary(screen, self._cards, y, available,
                           [self._indexed_tile(), self._browsing_tile(), self._indexing_tile()])
        y += Board.SUMMARY_H + 1
        height = bottom - y

        photo = self._photo
        if photo is None:
            Board.draw_notice(screen, self._cards, Board.LEFT, y, available, height, "Photo",
                              "Loading..." if self._loading else "No photos to display \u2014 Ctrl-P indexes the next one")
        elif screen.width >= Board.SIDE_BY_SIDE_MIN:
            picture_w = self._picture_card_width(available, height)
            self._draw_picture(screen, Board.LEFT, y, picture_w, height)
            text_x = Board.LEFT + picture_w + Board.GAP
            self._draw_text_column(screen, text_x, y, available - picture_w - Board.GAP, bottom, photo)
        else:
            # The picture takes its share of the height but yields to the text: the details
            # whole and the description at its shortest come first, and a board too short
            # for PICTURE_MIN_ROWS of picture above them shows no picture at all.
            facts = self._facts(photo)
            text_min = Tile.CARD_CHROME + DESCRIPTION_MIN_ROWS + 1 + len(facts) + Tile.CARD_CHROME
            picture_h = min(height * PICTURE_SHARE_H // 100, height - 1 - text_min)
            if picture_h >= PICTURE_MIN_ROWS + Tile.CARD_CHROME:
                self._draw_picture(screen, Board.LEFT, y, available, picture_h)
                y += picture_h + 1
            else:
                trace(f"Photos tab: no room for the picture over the text on a {height}-row board")
            self._draw_text_column(screen, Board.LEFT, y, available, bottom, photo, facts)
        Tile.tint_cards(screen, self._cards)
        Board.draw_hints(screen, HINTS)

    def _draw_text_column(self, screen: TUIScreen, x: int, y: int, width: int, bottom: int, photo: dict,
                          facts: Optional[List[Fact]] = None):
        """The description over the details in one column from row `y` down to `bottom`; the
        details keep their rows and the description gets the rest, at least DESCRIPTION_MIN_ROWS."""
        height = bottom - y
        facts = self._facts(photo) if facts is None else facts
        details_h = len(facts) + Tile.CARD_CHROME
        lines = wrap(photo.get("detailed_description") or "", width - 4)
        description_h = Tile.CARD_CHROME + max(DESCRIPTION_MIN_ROWS, min(len(lines), height - 1 - details_h - Tile.CARD_CHROME))
        self._draw_description(screen, x, y, width, description_h, photo)
        y += description_h + 1
        self._draw_details(screen, x, y, width, min(details_h, bottom - y), facts)

    # --- summary tiles -----------------------------------------------------------------------

    def _indexed_tile(self) -> SummaryTile:
        if not self._count:
            return "Indexed", "0", Theme.COLOR_DIM, [("no photos indexed", Theme.COLOR_DIM)]
        if self._total_apple <= 0:
            return "Indexed", f"{self._count:,}", Theme.COLOR_BRIGHT, [("photos described", Theme.COLOR_LABEL)]
        share = min(100, round(100 * self._count / self._total_apple))
        return ("Indexed", f"{self._count:,}", Theme.COLOR_BRIGHT,
                [(f"{share}% of the library", Theme.COLOR_LABEL), (f"{self._total_apple:,} in iCloud", Theme.COLOR_DIM)])

    def _browsing_tile(self) -> SummaryTile:
        sort = (f"sorted by {self._current_sort()[2].lower()}", Theme.COLOR_LABEL)
        if not self._count:
            return "Browsing", NONE, Theme.COLOR_DIM, [sort]
        return "Browsing", f"{self._index + 1:,} of {self._count:,}", Theme.COLOR_CYAN, [sort, ("Ctrl-S changes it", Theme.COLOR_DIM)]

    def _indexing_tile(self) -> SummaryTile:
        if self._processing:
            return ("Indexing", "running", Theme.COLOR_YELLOW,
                    [(self._process_status or "Processing...", Theme.COLOR_YELLOW)])
        if not self._process_status:
            return "Indexing", "idle", Theme.COLOR_DIM, [("Ctrl-P the next photo, Ctrl-B a batch", Theme.COLOR_DIM)]
        color = PROCESS_COLORS.get(self._process_status_type, Theme.COLOR_LABEL)
        value = {"ok": "done", "error": "failed"}.get(self._process_status_type, "idle")
        return "Indexing", value, color, [(self._process_status, color)]

    # --- the photo ---------------------------------------------------------------------------

    def _draw_description(self, screen: TUIScreen, x: int, y: int, width: int, height: int, photo: dict):
        """The photo's short description as the tile's title, the detailed one wrapped under it."""
        if height <= Tile.CARD_CHROME:
            return
        Tile.draw_card(screen, self._cards, x, y, width, height, photo.get("short_description") or "Photo",
                       self._type_label(photo), Theme.COLOR_BLUE)
        screen.set_color(Theme.COLOR_TEXT)
        for i, line in enumerate(wrap(photo.get("detailed_description") or "", width - 4, height - Tile.CARD_CHROME)):
            screen.write_at(x + 2, y + Tile.CARD_TOP + i, line)
        screen.reset_color()

    def _facts(self, photo: dict) -> List[Fact]:
        location = []
        if photo.get("location_text"):
            location.append(photo["location_text"].replace("/", ", "))
        if photo.get("latitude") is not None and photo.get("longitude") is not None:
            location.append(f"({photo['latitude']:.4f}, {photo['longitude']:.4f})")

        def text(label: str, value: Optional[str]) -> Fact:
            return Fact(label, value, Theme.COLOR_TEXT) if value and value != NONE else Fact(label, NONE, Theme.COLOR_FAINT)

        def score(label: str, value: Optional[int], color) -> Fact:
            return Fact(label, str(value or 0), color, meter=(value or 0) / 100)

        return [text("Date", self._format_date(photo.get("creation_date"))),
                text("Location", "  ".join(location)),
                text("Albums", photo.get("albums")),
                text("Persons", photo.get("persons")),
                Fact("", "", Theme.COLOR_FAINT),
                score("Pretty score", photo.get("pretty_score"), Theme.COLOR_PINK),
                score("Family score", photo.get("family_importance_score"), Theme.COLOR_CYAN),
                Fact("", "", Theme.COLOR_FAINT),
                Fact("Indexed", self._format_date(photo.get("indexed_at")), Theme.COLOR_LABEL),
                Fact("Id", str(photo.get("asset_guid") or NONE), Theme.COLOR_DIM)]

    def _draw_details(self, screen: TUIScreen, x: int, y: int, width: int, height: int, facts: List[Fact]):
        if height <= Tile.CARD_CHROME:
            return
        Tile.draw_card(screen, self._cards, x, y, width, height, "Details")
        Board.draw_facts(screen, x, y, width, height, facts, FACT_LABEL_W)

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
