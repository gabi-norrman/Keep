import os
import re
import time
from enum import Enum
from typing import Iterator, List, Tuple

from .ScrollableTabView import (ScrollableTabView, COLOR_HINT, COLOR_SCROLLBAR_THUMB, COLOR_SCROLLBAR_TRACK,
                                scrollbar_thumb)
from .TabView import SUB_CONTENT_TOP
from interfaces.console import TUIState
from interfaces.console.framework.TUIScreen import TUIScreen
from interfaces.console.framework.TextWidth import char_width, display_width, normalize
from common.logging import info

MAX_LINES = 10000

LOG_PATH_NORMAL = "log/floxy.out"
LOG_PATH_VERBOSE = "log/verbose-floxy.out"



class FilterMode(Enum):
    VERBOSE = "verbose"
    NORMAL = "normal"
    INFO = "info"


# Cycle order for the 'f' key, from most to least verbose
FILTER_MODE_CYCLE = [FilterMode.VERBOSE, FilterMode.NORMAL, FilterMode.INFO]


class LogTab(ScrollableTabView):
    """Log viewer tab that tails the log file with filtering, search and masking."""

    ANSI_RE = re.compile(r'\x1b\[[0-9;]*m')

    def __init__(self):
        super().__init__()
        self.log_lines: List[str] = []
        self.last_read_time = 0
        self.last_file_size = 0
        self.last_file_mtime = 0
        self._pending_new_lines: List[str] = []
        self.read_interval = 0.5
        self.filter_mode = FilterMode.NORMAL
        self.search_mode = False
        self.search_text = ""
        self.search_matches: List[int] = []
        self.search_current_idx = -1
        self.pre_search_scroll_offset = 0
        self.mask_input_mode = False
        self.mask_active = False
        self.mask_text = ""
        self.pre_mask_scroll_offset = 0
        self._plain_lines_cache: List[str] = []
        self._plain_lines_valid = False
        self._load_settings()

    @property
    def captures_text(self) -> bool:
        return self.search_mode or self.mask_input_mode

    def draw(self, screen: TUIScreen, frame: int):
        """Draw the log viewer"""
        current_time = time.time()

        if not self.search_mode and not self.mask_input_mode and current_time - self.last_read_time > self.read_interval:
            self._read_log_lines()
            self.last_read_time = current_time

        start_y = SUB_CONTENT_TOP
        display_height = screen.height - start_y - 1
        self.display_height = display_height

        base_lines = self._compute_masked_lines()
        mask_on = (self.mask_active or self.mask_input_mode) and bool(self.mask_text)

        if self.line_wrap:
            wrap_width = screen.width - 2
            display_lines = []
            for raw_line in base_lines:
                line = raw_line.rstrip('\n\r')
                line = self._format_line_for_width(line, screen.width)
                display_lines.extend(self._wrap_ansi_aware(line, wrap_width))
            total_lines = len(display_lines)
        else:
            display_lines = None
            total_lines = len(base_lines)

        self.total_lines = total_lines

        # When scrolled up, keep the viewport anchored to its content as new lines
        # stream in at the bottom: bump scroll_offset by however many display lines
        # were just appended so start_line stays constant (offset==0 keeps following).
        if self._pending_new_lines and self.scroll_offset > 0:
            if mask_on:
                query = self.mask_text.lower()
                new_visible = [l for l in self._pending_new_lines
                               if query in self.ANSI_RE.sub('', l).lower()]
            else:
                new_visible = self._pending_new_lines
            if self.line_wrap:
                wrap_width = screen.width - 2
                appended = sum(
                    len(self._wrap_ansi_aware(
                        self._format_line_for_width(l.rstrip('\n\r'), screen.width),
                        wrap_width))
                    for l in new_visible
                )
            else:
                appended = len(new_visible)
            if appended:
                max_scroll = max(0, total_lines - display_height)
                self.scroll_offset = min(self.scroll_offset + appended, max_scroll)
        self._pending_new_lines = []

        # Get visible window of log lines
        start_line = max(0, total_lines - display_height - self.scroll_offset)
        end_line = start_line + display_height

        if self.line_wrap:
            visible_lines = display_lines[start_line:end_line]
        else:
            visible_lines = base_lines[start_line:end_line]

        highlight_lines = set()
        current_highlight_line = -1
        if self.search_mode and self.search_text and not self.line_wrap:
            for match_idx in self.search_matches:
                if start_line <= match_idx < end_line:
                    highlight_lines.add(match_idx)
            if self.search_current_idx >= 0:
                current_highlight_line = self.search_matches[self.search_current_idx]

        # Draw log lines (use control sequences to clear since write_raw_ansi_at bypasses buffer)
        y = start_y
        for i, line in enumerate(visible_lines):
            if y > start_y + display_height - 1:
                break
            screen.control_sequences.append(screen.erase_line(y))
            line = line.rstrip('\n\r')
            if not self.line_wrap:
                line = self._format_line_for_width(line, screen.width)
                line = self._truncate_ansi_aware(line, screen.width - 2)

            raw_idx = start_line + i
            if not self.line_wrap and raw_idx in highlight_lines:
                is_current = (raw_idx == current_highlight_line)
                line = self._highlight_match(line, self.search_text, is_current)
            elif mask_on:
                line = self._highlight_match(line, self.mask_text, False)

            screen.write_raw_ansi_at(1, y, line)
            y += 1

        # Clear any remaining lines
        for clear_y in range(y, start_y + display_height):
            screen.control_sequences.append(screen.erase_line(clear_y))

        if total_lines > display_height:
            self.draw_scroll_indicator(screen)
            self._draw_scrollbar_raw(screen, screen.width - 1, start_y, total_lines, display_height)
        self._draw_filter_mode(screen)

    def _draw_scrollbar_raw(self, screen: TUIScreen, x: int, start_y: int,
                            total_lines: int, display_height: int):
        """Draw scrollbar via control sequences so it doesn't conflict with raw ANSI log lines."""
        if total_lines <= display_height or display_height < 2:
            return
        thumb_top, thumb_size = scrollbar_thumb(total_lines, display_height, self.scroll_offset)
        for i in range(display_height):
            y = start_y + i
            if thumb_top <= i < thumb_top + thumb_size:
                color = COLOR_SCROLLBAR_THUMB
                char = '┃'
            else:
                color = COLOR_SCROLLBAR_TRACK
                char = '│'
            screen.control_sequences.append(
                f"{screen.CSI}{y};{x + 1}H{color}{char}\x1b[0m"
            )

    def handle_input(self, key: str) -> bool:
        """Handle scrolling, filter mode, and search input, persisting search/mask state on change."""
        before = (self.search_text, self.mask_text, self.mask_active)
        result = self._handle_input(key)
        if (self.search_text, self.mask_text, self.mask_active) != before:
            self._save_settings()
        return result

    def _handle_input(self, key: str) -> bool:
        if key == '\x06':
            self.search_mode = True
            self.search_matches = []
            self.search_current_idx = -1
            self.pre_search_scroll_offset = self.scroll_offset
            self.mask_input_mode = False
            self.mask_active = False
            self.mask_text = ""
            if self.search_text:
                self._perform_search()
            return True

        if self.search_mode:
            return self._handle_search_input(key)

        if self.mask_input_mode:
            return self._handle_mask_input(key)

        if key == 'm':
            self.mask_input_mode = True
            if not self.mask_active:
                self.mask_text = ""
                self.pre_mask_scroll_offset = self.scroll_offset
            self.mask_active = False
            self.search_mode = False
            self.scroll_offset = 0
            return True

        if key == 'escape' and self.mask_active:
            self.mask_active = False
            self.mask_text = ""
            self.scroll_offset = 0
            return True

        if self.handle_scroll_input(key, self.scroll_total(len(self.log_lines))):
            return True
        if key == 'f':
            idx = FILTER_MODE_CYCLE.index(self.filter_mode)
            self.filter_mode = FILTER_MODE_CYCLE[(idx + 1) % len(FILTER_MODE_CYCLE)]
            self.scroll_offset = 0
            self.log_lines = []
            self.last_file_size = 0
            self.last_file_mtime = 0
            return True
        return False

    def _handle_search_input(self, key: str) -> bool:
        if key == 'escape':
            self.search_mode = False
            self.scroll_offset = self.pre_search_scroll_offset
            return True
        if key == '\r' or key == '\n' or key == 'enter':
            self.search_mode = False
            return True
        if key == '\x7f' or key == '\x08' or key == 'backspace':
            if self.search_text:
                self.search_text = self.search_text[:-1]
                self._perform_search()
            return True
        if key == 'up':
            if self.search_matches and self.search_current_idx > 0:
                self.search_current_idx -= 1
                self._scroll_to_match(self.search_matches[self.search_current_idx])
            return True
        if key == 'down':
            if self.search_matches and self.search_current_idx < len(self.search_matches) - 1:
                self.search_current_idx += 1
                self._scroll_to_match(self.search_matches[self.search_current_idx])
            return True
        if key in ('pageup', 'pagedown'):
            return self.handle_scroll_input(key, self.scroll_total(len(self.log_lines)))
        if len(key) == 1 and key.isprintable():
            self.search_text += key
            self._perform_search()
            return True
        return True

    def _handle_mask_input(self, key: str) -> bool:
        if key == 'escape':
            self.mask_input_mode = False
            self.mask_active = False
            self.mask_text = ""
            self.scroll_offset = self.pre_mask_scroll_offset
            return True
        if key == '\r' or key == '\n' or key == 'enter':
            self.mask_input_mode = False
            self.mask_active = bool(self.mask_text)
            self.scroll_offset = 0
            return True
        if key == '\x7f' or key == '\x08' or key == 'backspace':
            if self.mask_text:
                self.mask_text = self.mask_text[:-1]
                self.scroll_offset = 0
            return True
        if key in ('pageup', 'pagedown'):
            return self.handle_scroll_input(key, self.scroll_total(len(self.log_lines)))
        if len(key) == 1 and key.isprintable():
            self.mask_text += key
            self.scroll_offset = 0
            return True
        return True

    def _compute_masked_lines(self) -> List[str]:
        """Return only the log lines containing the mask query, or all lines when mask is off."""
        if not ((self.mask_active or self.mask_input_mode) and self.mask_text):
            return self.log_lines
        self._ensure_plain_cache()
        query = self.mask_text.lower()
        return [
            line for line, plain in zip(self.log_lines, self._plain_lines_cache)
            if query in plain
        ]

    def _ensure_plain_cache(self):
        if not self._plain_lines_valid:
            self._plain_lines_cache = [
                self.ANSI_RE.sub('', line).lower() for line in self.log_lines
            ]
            self._plain_lines_valid = True

    def _perform_search(self):
        self.search_matches = []
        self.search_current_idx = -1
        if not self.search_text:
            return
        self._ensure_plain_cache()
        query = self.search_text.lower()
        for i, plain in enumerate(self._plain_lines_cache):
            if query in plain:
                self.search_matches.append(i)
        if self.search_matches:
            self.search_current_idx = len(self.search_matches) - 1
            self._scroll_to_match(self.search_matches[self.search_current_idx])

    def _scroll_to_match(self, line_idx: int):
        total = len(self.log_lines)
        display_height = getattr(self, 'display_height', 20)
        center_offset = total - line_idx - display_height // 2
        max_scroll = max(0, total - display_height)
        self.scroll_offset = max(0, min(center_offset, max_scroll))

    def _load_settings(self):
        """Restore persisted search and mask state from the previous session."""
        data = TUIState.load("log")
        self.search_text = data.get("search_text", "")
        self.mask_text = data.get("mask_text", "")
        self.mask_active = bool(data.get("mask_active", False)) and bool(self.mask_text)
        info(f"LogTab settings restored: search={self.search_text!r} mask={self.mask_text!r} mask_active={self.mask_active}")

    def _save_settings(self):
        """Persist search and mask state so it survives server restarts."""
        TUIState.save("log", search_text=self.search_text, mask_text=self.mask_text, mask_active=self.mask_active)

    def _apply_level_filter(self, lines: List[str]) -> List[str]:
        """In INFO mode, drop TRACE/FINE lines (level char is the first visible character)."""
        if self.filter_mode != FilterMode.INFO:
            return lines
        return [
            line for line in lines
            if not self.ANSI_RE.sub('', line).startswith(('T', 'F'))
        ]

    def _read_log_lines(self):
        """Read log file incrementally - only new content since last read"""
        log_path = LOG_PATH_VERBOSE if self.filter_mode == FilterMode.VERBOSE else LOG_PATH_NORMAL
        self._pending_new_lines = []
        try:
            if not os.path.exists(log_path):
                self.log_lines = [f"Log file not found: {log_path}"]
                return

            stat = os.stat(log_path)
            current_size = stat.st_size
            current_mtime = stat.st_mtime

            # File unchanged - skip read
            if current_size == self.last_file_size and current_mtime == self.last_file_mtime:
                return

            # File truncated or rotated - full re-read
            if current_size < self.last_file_size:
                self.last_file_size = 0
                self.log_lines = []

            with open(log_path, 'r', encoding='utf-8', errors='replace') as f:
                if self.last_file_size > 0:
                    # Seek to last position and read only new content
                    f.seek(self.last_file_size)
                    new_lines = self._apply_level_filter(f.readlines())
                    if new_lines:
                        self.log_lines.extend(new_lines)
                        self._pending_new_lines = new_lines
                else:
                    # Initial read or after truncation - only read the tail
                    tail_offset = max(0, current_size - 1_500_000)
                    if tail_offset > 0:
                        f.seek(tail_offset)
                        f.readline()  # discard partial line
                    self.log_lines = self._apply_level_filter(f.readlines())

            # Trim to MAX_LINES to prevent unbounded memory growth
            if len(self.log_lines) > MAX_LINES:
                self.log_lines = self.log_lines[-MAX_LINES:]

            self._plain_lines_valid = False
            self.last_file_size = current_size
            self.last_file_mtime = current_mtime

        except Exception as e:
            self.log_lines = [f"Error reading log: {str(e)}"]

    def _draw_filter_mode(self, screen: TUIScreen):
        """Draw current filter mode indicator or search bar"""
        screen.reset_color()
        screen.set_color(COLOR_HINT)

        if self.search_mode:
            match_info = ""
            if self.search_text and self.search_matches:
                match_info = f"  {self.search_current_idx + 1} of {len(self.search_matches)}"
            elif self.search_text:
                match_info = "  no matches"
            bar = f" /{self.search_text}\u2588{match_info}  Esc cancel  Enter confirm  \u2191\u2193 prev/next "
            screen.write_at(1, screen.height - 1, bar)
        elif self.mask_input_mode:
            count = len(self._compute_masked_lines()) if self.mask_text else 0
            match_info = f"  {count} matches" if self.mask_text else ""
            bar = f" &{self.mask_text}\u2588{match_info}  Esc cancel  Enter apply "
            screen.write_at(1, screen.height - 1, bar)
        elif self.mask_active and self.mask_text:
            count = len(self._compute_masked_lines())
            bar = f" mask:\"{self.mask_text}\" ({count})  Esc clear  m edit "
            screen.write_at(1, screen.height - 1, bar)
        else:
            hints = f" f {self.filter_mode.name}  {self.wrap_hint}  m mask  \u2191\u2193 scroll  PgUp/PgDn page  Ctrl+F search "
            screen.write_at(1, screen.height - 1, hints)

    @staticmethod
    def _ansi_tokens(line: str) -> Iterator[Tuple[str, bool]]:
        """The line as (text, is_code) tokens: each `ESC[…m` colour code whole, every other
        character on its own."""
        i = 0
        n = len(line)
        while i < n:
            if line[i] == '\x1b' and i + 1 < n and line[i + 1] == '[':
                j = i + 2
                while j < n and line[j] != 'm':
                    j += 1
                yield line[i:j + 1], True
                i = j + 1
            else:
                yield line[i], False
                i += 1

    def _highlight_match(self, line: str, query: str, is_current: bool) -> str:
        """Highlight a query within a line. Current match uses inverse video, others use yellow."""
        query = query.lower()
        if not query:
            return line
        plain = self.ANSI_RE.sub('', line)
        match_pos = plain.lower().find(query)
        if match_pos < 0:
            return line

        # Find the raw offsets of the match's first and last visible characters
        vis = 0
        pos = 0
        insert_start = insert_end = None
        for token, is_code in self._ansi_tokens(line):
            if not is_code:
                if vis == match_pos:
                    insert_start = pos
                vis += 1
                if insert_start is not None and vis == match_pos + len(query):
                    insert_end = pos + 1
                    break
            pos += len(token)
        if insert_start is None or insert_end is None:
            return line

        hl_start = '\x1b[0m\x1b[7m' if is_current else '\x1b[0m\x1b[33;1m\x1b[48;2;60;50;0m'
        hl_end = '\x1b[0m'
        return line[:insert_start] + hl_start + line[insert_start:insert_end] + hl_end + line[insert_end:]

    def _format_line_for_width(self, line: str, width: int) -> str:
        """Remove columns based on terminal width.

        Format: level│timestamp│thread│message
        """
        if width >= 170:
            return line

        parts = line.split('│')
        if len(parts) < 4:
            return line

        # parts[0]=level, parts[1]=timestamp, parts[2]=thread, parts[3+]=message
        if width < 120:
            # Keep only level and message
            return parts[0] + '│' + '│'.join(parts[3:])
        else:
            # Keep level, timestamp, message (remove thread)
            return parts[0] + '│' + parts[1] + '│' + '│'.join(parts[3:])

    def _wrap_ansi_aware(self, line: str, width: int) -> list:
        """Wrap ANSI-colored line at word boundaries, carrying color across lines."""
        # word_wrap normalizes what it measures; the tokens it is mapped back onto must match
        # character for character, so drop the same sequences here (colour codes hold none).
        line = normalize(line)
        visible = self.ANSI_RE.sub('', line)
        if display_width(visible) <= width:
            return [line]

        wrapped_visible = self.word_wrap(visible, width)
        if len(wrapped_visible) <= 1:
            return [line]

        # Split the original ANSI line to the wrapped visible lengths, re-opening the colour
        # codes still active at each break on the next line
        tokens = list(self._ansi_tokens(line))
        result = []
        active_codes: List[str] = []
        ti = 0

        def note(code: str):
            if code == '\x1b[0m' or code == '\x1b[m':
                active_codes.clear()
            else:
                active_codes.append(code)

        for seg_idx, seg in enumerate(wrapped_visible):
            segment = ''.join(active_codes)
            vis_count = 0
            while ti < len(tokens) and vis_count < len(seg):
                token, is_code = tokens[ti]
                ti += 1
                segment += token
                if is_code:
                    note(token)
                else:
                    vis_count += 1
            result.append(segment)

            # Skip the break-point space between segments (word_wrap consumed it)
            if seg_idx < len(wrapped_visible) - 1:
                while ti < len(tokens):
                    token, is_code = tokens[ti]
                    if is_code:
                        note(token)
                        ti += 1
                    elif token == ' ':
                        ti += 1
                        break
                    else:
                        break

        return result

    def _truncate_ansi_aware(self, line: str, max_width: int) -> str:
        """Truncate line to max_width visible cells, preserving ANSI codes."""
        result = []
        visible_len = 0
        for token, is_code in self._ansi_tokens(line):
            if visible_len >= max_width:
                break
            if not is_code:
                cw = char_width(token)
                if visible_len + cw > max_width:
                    break
                visible_len += cw
            result.append(token)
        return ''.join(result)

