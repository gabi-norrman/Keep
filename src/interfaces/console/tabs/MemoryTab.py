import re
import os
import difflib
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Tuple
from .MarkdownTabView import MarkdownTabView, MARKDOWN_COLORS, ADDED_TINT_COLORS, fit
from .ScrollableTabView import COLOR_HINT
from .TabView import CONTENT_TOP
from assistant import FirstRun
from common.logging import trace
from common.vault.Redactor import Redactor
from common.vault.Vault import Vault
from interfaces.console.widgets.DiffText import (COLOR_ADDED_HIGHLIGHT_BG, COLOR_ADDED_LINE_BG, COLOR_GUTTER_ADD,
                                                 COLOR_GUTTER_DEL, COLOR_REMOVED, COLOR_REMOVED_HIGHLIGHT_BG,
                                                 COLOR_REMOVED_LINE_BG, inline_diff)
from interfaces.console.framework.TUIScreen import TUIScreen, Color
from interfaces.console.Theme import COLOR_SECRET, COLOR_YELLOW_BG, COLOR_YELLOW_FG
from interfaces.console.widgets.SecretMarks import mark_masks

COLOR_DATE_LABEL = Color(180, 140, 255)

FIRST_CURATION_LABEL = "Heads up"
FIRST_CURATION_NOTICE = ("Keep is building its memory for the first time. What you read here is a "
                         "work in progress and may look wrong until that finishes.")


class MemoryTab(MarkdownTabView):
    """Memory viewer tab showing memory.md with diff highlighting and snapshot history browsing.
    Every vaulted value is masked the way the Vault tab lists it (`•••• 1234`, `a•••@example.com`)
    as the text is loaded — before it is wrapped or diffed — so the tab never shows one in the
    clear; the Vault tab reveals on request. Outside a diff the masks are marked in bold orange.
    While the first-run pipeline curates the first bank (`FirstRun.curation_progress`) a two-row
    orange heads-up band sits above the text: what it shows is a work in progress, not a verdict."""

    def __init__(self, redactor: Optional[Redactor] = None):
        super().__init__()
        self._redactor = redactor  # over the Vault singleton, resolved on first draw
        self.memory_lines: List[str] = []
        self._line_types: List[str] = []  # 'normal', 'added', 'removed' - parallel to memory_lines
        self._inline_highlights: List[Optional[List[Tuple[int, int]]]] = []  # char spans to highlight
        self.last_memory_hash = 0
        self.memory_file_path = "memory.md"
        self.log_dir = Path("log/memory")

        # History browsing state
        self._snapshot_dates: List[str] = []  # sorted dates YYYYMMDD (newest first)
        self._snapshot_files: dict = {}  # date -> list of file paths
        self._history_index: Optional[int] = None  # None = live, 0 = newest snapshot, etc.
        self._last_scan_hash = 0
        self._viewing_date_label: Optional[str] = None

    @property
    def _has_diff(self) -> bool:
        return len(self._line_types) > 0 and any(t != 'normal' for t in self._line_types)

    @property
    def redactor(self) -> Redactor:
        if self._redactor is None:
            self._redactor = Redactor(Vault.get_instance())
            trace(f"Memory tab: secret marking resolved, {len(self._redactor.vault)} vault secret(s)")
        return self._redactor

    def draw(self, screen: TUIScreen, frame: int):
        """Draw the memory viewer"""
        self._read_memory_lines()

        start_y = CONTENT_TOP
        curating = FirstRun.curation_progress() is not None
        if curating:
            self._draw_first_curation_notice(screen, start_y)
            start_y += self.NOTICE_ROWS
        date_bar_height = 1 if self._viewing_date_label else 0
        content_start_y = start_y + date_bar_height
        display_height = screen.height - start_y - 1 - date_bar_height
        self.display_height = display_height

        # Draw date navigation bar
        if self._viewing_date_label:
            self._draw_date_bar(screen, start_y)

        # Pre-wrap lines if line_wrap is on
        if self.line_wrap:
            wrap_width = screen.width - 5  # gutter + scrollbar margin
            display_lines = []
            display_types = []
            display_highlights = []
            for i, line in enumerate(self.memory_lines):
                line = line.rstrip('\n\r')
                wrapped = self.word_wrap(line, wrap_width)
                line_type = self._line_types[i] if i < len(self._line_types) else 'normal'
                highlights = self._inline_highlights[i] if i < len(self._inline_highlights) else None
                # Distribute highlight spans across wrapped lines
                char_offset = 0
                for wl in wrapped:
                    display_lines.append(wl)
                    display_types.append(line_type)
                    if highlights:
                        shifted = []
                        wl_end = char_offset + len(wl)
                        for s, e in highlights:
                            hs = max(s, char_offset) - char_offset
                            he = min(e, wl_end) - char_offset
                            if hs < he:
                                shifted.append((hs, he))
                        display_highlights.append(shifted if shifted else None)
                    else:
                        display_highlights.append(None)
                    char_offset += len(wl)
                    if char_offset < len(line) and line[char_offset] == ' ':
                        char_offset += 1
        else:
            display_lines = [l.rstrip('\n\r') for l in self.memory_lines]
            display_types = self._line_types
            display_highlights = list(self._inline_highlights) if self._inline_highlights else []

        # Get the visible window of memory lines
        total_lines = len(display_lines)
        self.total_lines = total_lines
        start_line = max(0, total_lines - display_height - self.scroll_offset)
        end_line = start_line + display_height
        visible_lines = display_lines[start_line:end_line]
        visible_types = display_types[start_line:end_line] if display_types else []
        visible_highlights = display_highlights[start_line:end_line] if display_highlights else []
        self.seek_code_block(display_lines, start_line)

        has_diff = self._has_diff
        gutter_width = 2 if has_diff else 0
        content_x = 1 + gutter_width
        # The diff colours already say what changed; the orange on the masks waits for the live view
        mark_secrets = not has_diff and self.redactor.active

        # Reserve rightmost column for scrollbar when content overflows
        has_scrollbar = total_lines > display_height
        right_margin = 2 if has_scrollbar else 1

        # Draw memory lines with formatting
        y = content_start_y
        for idx, line in enumerate(visible_lines):
            if y > content_start_y + display_height - 1:
                break
            line = line.rstrip('\n\r')
            line_type = visible_types[idx] if idx < len(visible_types) else 'normal'

            # Draw gutter
            if has_diff:
                self._draw_gutter(screen, 1, y, line_type)

            # Draw content
            max_width = screen.width - content_x - right_margin
            highlights = visible_highlights[idx] if idx < len(visible_highlights) else None
            if line_type == 'removed':
                self._draw_removed_line(screen, content_x, y, line, max_width, highlights)
            else:
                colors = ADDED_TINT_COLORS if line_type == 'added' else None
                self._draw_content_line(screen, content_x, y, line, max_width,
                                        line_type, colors, highlights)
            if mark_secrets:
                self._mark_secrets(screen, content_x, y, max_width)
            if line_type == 'added':
                screen.tint_row(1, y, screen.width - right_margin, COLOR_ADDED_LINE_BG)
            elif line_type == 'removed':
                screen.tint_row(1, y, screen.width - right_margin, COLOR_REMOVED_LINE_BG)
            y += 1

        # Clear any remaining lines
        screen.reset_color()
        clear_width = screen.width - 2 - (1 if has_scrollbar else 0)
        for clear_y in range(y, content_start_y + display_height):
            screen.write_at(1, clear_y, " " * clear_width)

        if has_scrollbar:
            self.draw_scrollbar(screen, screen.width - 1, content_start_y,
                                total_lines, display_height)
        self.draw_bottom_bar(screen, f" {self.wrap_hint}  ←→ history  ↑↓ scroll  PgUp/PgDn page ",
                             total_lines, display_height)

    def handle_input(self, key: str) -> bool:
        """Handle scrolling and history navigation input"""
        if self.handle_scroll_input(key, self.scroll_total(len(self.memory_lines))):
            return True
        if key == 'left':
            self._navigate_history(-1)
            return True
        elif key == 'right':
            self._navigate_history(1)
            return True
        return False

    def _navigate_history(self, direction: int):
        """Navigate through memory history. direction: -1 = older, +1 = newer"""
        self._scan_snapshots()
        if not self._snapshot_dates:
            return

        if direction == -1:  # Go older
            if self._history_index is None:
                self._history_index = 0
            elif self._history_index < len(self._snapshot_dates) - 1:
                self._history_index += 1
        elif direction == 1:  # Go newer
            if self._history_index is not None:
                if self._history_index <= 0:
                    self._history_index = None
                else:
                    self._history_index -= 1

        self.last_memory_hash = 0  # Force reload

    def _scan_snapshots(self):
        """Scan log/memory/ for snapshot files, grouped by date"""
        if not self.log_dir.exists():
            self._snapshot_dates = []
            self._snapshot_files = {}
            return

        try:
            files = sorted(self.log_dir.glob("*_memory.md"), reverse=True)
        except OSError:
            return

        scan_hash = hash(tuple(str(f) for f in files))
        if scan_hash == self._last_scan_hash:
            return
        self._last_scan_hash = scan_hash

        dates_dict: dict = {}
        for f in files:
            match = re.match(r'^(\d{8})_', f.name)
            if match:
                date_str = match.group(1)
                dates_dict.setdefault(date_str, []).append(f)

        self._snapshot_dates = sorted(dates_dict.keys(), reverse=True)
        self._snapshot_files = dates_dict

    def _read_memory_lines(self):
        """Read memory lines from the appropriate source"""
        if self._history_index is None:
            self._show_live()
        else:
            self._read_snapshot_memory()

    def _show_live(self):
        """Back to the live memory.md, with no diff annotations."""
        self._history_index = None
        self._viewing_date_label = None
        self._line_types = []
        self._inline_highlights = []
        self._read_live_memory()

    def _read_live_memory(self):
        """Read memory lines from memory.md file"""
        try:
            if os.path.exists(self.memory_file_path):
                with open(self.memory_file_path, 'r', encoding='utf-8') as f:
                    memory_text = f.read()
                memory_hash = self._content_hash(memory_text)
                if memory_hash != self.last_memory_hash:
                    self.memory_lines = self.redactor.mask(memory_text).splitlines(keepends=True)
                    self.last_memory_hash = memory_hash
            else:
                self.memory_lines = ["No memory file found at memory.md"]
        except Exception as e:
            self.memory_lines = [f"Error reading memory: {str(e)}"]

    def _read_snapshot_memory(self):
        """Read memory from a historical snapshot with diff annotations"""
        self._scan_snapshots()
        if self._history_index is None or self._history_index >= len(self._snapshot_dates):
            self._show_live()
            return

        date_str = self._snapshot_dates[self._history_index]
        files = self._snapshot_files.get(date_str, [])
        if not files:
            self._viewing_date_label = date_str
            self.memory_lines = ["No snapshot data found"]
            self._line_types = ['normal']
            return

        # Use the latest snapshot for this date
        snapshot_path = sorted(files, reverse=True)[0]

        try:
            dt = datetime.strptime(date_str, "%Y%m%d")
            self._viewing_date_label = dt.strftime("%A, %B %-d, %Y")
        except ValueError:
            self._viewing_date_label = date_str

        try:
            with open(snapshot_path, 'r', encoding='utf-8') as f:
                content = f.read()

            memory_hash = self._content_hash(content)
            if memory_hash != self.last_memory_hash:
                new_memory = self.redactor.mask(self._extract_updated_memory(content))

                # Load previous snapshot for diff
                old_memory = self._load_previous_memory()

                if old_memory:
                    lines, types, highlights = self._build_annotated_view(old_memory, new_memory)
                    self.memory_lines = [l + '\n' for l in lines]
                    self._line_types = types
                    self._inline_highlights = highlights
                else:
                    self.memory_lines = new_memory.splitlines(keepends=True)
                    self._line_types = ['normal'] * len(self.memory_lines)
                    self._inline_highlights = [None] * len(self.memory_lines)

                self.last_memory_hash = memory_hash
        except Exception as e:
            self.memory_lines = [f"Error reading snapshot: {str(e)}"]
            self._line_types = ['normal']

    def _content_hash(self, text: str) -> int:
        """Changes with the text or the vault: a secret added while the tab is open is masked
        on the next frame, not on the next edit of the file."""
        return hash((text, self.redactor.vault.version))

    def _load_previous_memory(self) -> Optional[str]:
        """Load the memory from the previous (older) snapshot for diff comparison"""
        if self._history_index is None:
            return None

        prev_index = self._history_index + 1
        if prev_index >= len(self._snapshot_dates):
            return None

        prev_date = self._snapshot_dates[prev_index]
        prev_files = self._snapshot_files.get(prev_date, [])
        if not prev_files:
            return None

        prev_path = sorted(prev_files, reverse=True)[0]
        try:
            with open(prev_path, 'r', encoding='utf-8') as f:
                return self.redactor.mask(self._extract_updated_memory(f.read()))
        except Exception:
            return None

    @staticmethod
    def _build_annotated_view(old_memory: str, new_memory: str) -> Tuple[List[str], List[str], List[Optional[List[Tuple[int, int]]]]]:
        """Build a merged diff view with line type annotations and inline highlights.
        Returns (lines, types, highlights) where types are 'normal', 'added', 'removed'
        and highlights are optional lists of (start, end) char spans for within-line changes."""
        old_lines = old_memory.splitlines()
        new_lines = new_memory.splitlines()

        result_lines: List[str] = []
        result_types: List[str] = []
        result_highlights: List[Optional[List[Tuple[int, int]]]] = []

        sm = difflib.SequenceMatcher(None, old_lines, new_lines)
        for op, i1, i2, j1, j2 in sm.get_opcodes():
            if op == 'equal':
                for line in new_lines[j1:j2]:
                    result_lines.append(line)
                    result_types.append('normal')
                    result_highlights.append(None)
            elif op == 'replace':
                old_block = old_lines[i1:i2]
                new_block = new_lines[j1:j2]
                # Pair up lines for inline diff when block sizes match
                paired = min(len(old_block), len(new_block))
                for k in range(paired):
                    old_spans, new_spans = inline_diff(old_block[k], new_block[k])
                    result_lines.append(old_block[k])
                    result_types.append('removed')
                    result_highlights.append(old_spans)
                    result_lines.append(new_block[k])
                    result_types.append('added')
                    result_highlights.append(new_spans)
                # Remaining unpaired lines
                for line in old_block[paired:]:
                    result_lines.append(line)
                    result_types.append('removed')
                    result_highlights.append(None)
                for line in new_block[paired:]:
                    result_lines.append(line)
                    result_types.append('added')
                    result_highlights.append(None)
            elif op == 'insert':
                for line in new_lines[j1:j2]:
                    result_lines.append(line)
                    result_types.append('added')
                    result_highlights.append(None)
            elif op == 'delete':
                for line in old_lines[i1:i2]:
                    result_lines.append(line)
                    result_types.append('removed')
                    result_highlights.append(None)

        return result_lines, result_types, result_highlights

    @staticmethod
    def _extract_updated_memory(debug_log_content: str) -> str:
        """Extract the '## Updated Memory' section from a debug log file"""
        marker = "## Updated Memory"
        idx = debug_log_content.find(marker)
        if idx != -1:
            after_marker = debug_log_content[idx + len(marker):]
            return after_marker.lstrip('\n')
        return debug_log_content

    def _draw_gutter(self, screen: TUIScreen, x: int, y: int, line_type: str):
        """Draw a 2-char gutter marker for diff lines"""
        screen.move_cursor(x, y)
        if line_type == 'added':
            screen.reset_color()
            screen.set_color(COLOR_GUTTER_ADD)
            screen.write('+ ')
        elif line_type == 'removed':
            screen.reset_color()
            screen.set_color(COLOR_GUTTER_DEL)
            screen.write('- ')
        else:
            screen.reset_color()
            screen.write('  ')

    def _draw_content_line(self, screen: TUIScreen, x: int, y: int, line: str,
                           max_width: int, line_type: str, colors: dict = None,
                           highlights: Optional[List[Tuple[int, int]]] = None):
        """Draw a content line with optional inline diff highlighting"""
        if not highlights:
            self.draw_formatted_line(screen, x, y, line, max_width, colors)
            return
        # A line with inline highlights is plain text with highlight backgrounds
        fg = ADDED_TINT_COLORS['normal'] if colors else MARKDOWN_COLORS['normal']
        self._draw_highlighted(screen, x, y, fit(line, max_width), fg, COLOR_ADDED_HIGHLIGHT_BG, highlights)

    def _draw_removed_line(self, screen: TUIScreen, x: int, y: int, line: str,
                           max_width: int, highlights: Optional[List[Tuple[int, int]]] = None):
        """Draw a removed line in dim red, with optional inline diff highlighting"""
        self._draw_highlighted(screen, x, y, fit(line, max_width), COLOR_REMOVED, COLOR_REMOVED_HIGHLIGHT_BG,
                               highlights)

    @staticmethod
    def _draw_highlighted(screen: TUIScreen, x: int, y: int, text: str, fg: Color, highlight_bg: Color,
                          highlights: Optional[List[Tuple[int, int]]]):
        """`text` in `fg`, the characters inside the `highlights` ranges on `highlight_bg`."""
        screen.move_cursor(x, y)
        screen.reset_color()
        screen.set_color(fg)
        if not highlights:
            screen.write(text)
            return
        for i, ch in enumerate(text):
            screen.reset_color()
            screen.set_color(fg)
            if any(s <= i < e for s, e in highlights):
                screen.set_bg_color(highlight_bg)
            screen.write(ch)

    def _mark_secrets(self, screen: TUIScreen, x: int, y: int, width: int):
        mark_masks(screen, self.redactor, x, y, width)

    NOTICE_ROWS = 2

    @staticmethod
    def _draw_first_curation_notice(screen: TUIScreen, y: int):
        """Two rows in the Home tab's progress-bar colours: the bold label centred on the
        first, the sentence centred on the second, each clipped to the width."""
        width = screen.width - 2
        for row_y, text, bold in ((y, FIRST_CURATION_LABEL, True), (y + 1, FIRST_CURATION_NOTICE, False)):
            screen.reset_color()
            screen.write_at(1, row_y, " " * width)
            text = fit(text, width)
            screen.set_color(COLOR_YELLOW_FG)
            if bold:
                screen.bold()
            screen.write_at(1 + (width - len(text)) // 2, row_y, text)
            screen.reset_color()
            screen.tint_row(1, row_y, width, COLOR_YELLOW_BG)

    def _draw_date_bar(self, screen: TUIScreen, y: int):
        """Draw the date navigation bar, centered"""
        screen.reset_color()
        screen.write_at(1, y, " " * (screen.width - 2))

        label = self._viewing_date_label or ""
        if self._history_index is not None and self._history_index > 0:
            right_hint = " \u25B6"
        else:
            right_hint = " \u25B6 (live)"
        if self._history_index is None:
            return
        pos_text = f" {self._history_index + 1}/{len(self._snapshot_dates)} "

        # Center: "◀  <label>  ▶ (live)"
        center_text = f"\u25C0  {label} {right_hint}"
        center_x = max(1, (screen.width - len(center_text)) // 2)

        screen.set_color(COLOR_HINT)
        screen.write_at(center_x, y, "\u25C0  ")
        screen.bold()
        screen.set_color(COLOR_DATE_LABEL)
        screen.write(label)
        screen.reset_color()
        screen.set_color(COLOR_HINT)
        screen.write(right_hint)

        # Position indicator on the right
        screen.write_at(screen.width - len(pos_text) - 1, y, pos_text)
