import re
from typing import List

from .ScrollableTabView import ScrollableTabView
from interfaces.console import Theme
from interfaces.console.framework.TUIScreen import TUIScreen
from interfaces.console.framework import TextWidth
from interfaces.console.framework.TextWidth import char_width, display_width, normalize

MARKDOWN_COLORS = {
    'h1': Theme.COLOR_CYAN,
    'h2': Theme.COLOR_GREEN,
    'h3': Theme.mix(Theme.COLOR_PANEL_BG, Theme.COLOR_GREEN, 0.62),   # h2's green, dimmed
    'h4': Theme.COLOR_PURPLE,
    'code': Theme.COLOR_TEAL,          # teal, so backticked names never read as a vaulted value
    'code_block': Theme.mix(Theme.COLOR_LABEL, Theme.COLOR_YELLOW, 0.45),
    'bullet': Theme.COLOR_BLUE,
    'bold': Theme.COLOR_BRIGHT,
    'normal': Theme.COLOR_TEXT,
}

# An added line: every element pulled most of the way to the green.
ADDED_TINT_COLORS = {element: Theme.mix(color, Theme.COLOR_GREEN, 0.65) for element, color in MARKDOWN_COLORS.items()}


fit = TextWidth.fit


class MarkdownTabView(ScrollableTabView):
    """Base class for scrollable tabs that render markdown-formatted lines."""

    def __init__(self):
        super().__init__()
        self.in_code_block = False

    def seek_code_block(self, lines: List[str], start_line: int):
        """Set the code-block state to what it is after the first `start_line` lines, so a
        scrolled window starts with the right fence parity."""
        self.in_code_block = sum(1 for line in lines[:start_line] if line.strip().startswith('```')) % 2 == 1

    def draw_formatted_line(self, screen: TUIScreen, x: int, y: int, line: str,
                            max_width: int = 0, colors: dict = None):
        """Draw a line with markdown formatting"""
        if max_width == 0:
            max_width = screen.width - 2
        if colors is None:
            colors = MARKDOWN_COLORS
        screen.move_cursor(x, y)

        # A fence toggles the code block and is drawn in its colour, like the lines inside
        fence = line.strip().startswith('```')
        if fence:
            self.in_code_block = not self.in_code_block
        if fence or self.in_code_block:
            screen.reset_color()
            screen.set_color(colors['code_block'])
            screen.write(fit(line, max_width))
            return

        # Headers
        header_match = re.match(r'^(#{1,4})\s+(.*)$', line)
        if header_match:
            level = len(header_match.group(1))
            text = header_match.group(2)
            header_colors = {1: colors['h1'], 2: colors['h2'], 3: colors['h3'], 4: colors['h4']}
            screen.reset_color()
            screen.bold()
            screen.set_color(header_colors.get(level, colors['h1']))
            screen.write(fit('#' * level + ' ' + text, max_width))
            return

        # Bullet points
        bullet_match = re.match(r'^(\s*)([-*])\s+(.*)$', line)
        if bullet_match:
            indent = bullet_match.group(1)
            text = bullet_match.group(3)
            screen.reset_color()
            screen.write(indent)
            screen.set_color(colors['bullet'])
            screen.bold()
            screen.write('\u2022 ')
            screen.reset_color()
            self.draw_inline_formatted(screen, text, max_width - len(indent) - 2, colors)
            return

        # Numbered lists
        num_match = re.match(r'^(\s*)(\d+\.)\s+(.*)$', line)
        if num_match:
            indent = num_match.group(1)
            num = num_match.group(2)
            text = num_match.group(3)
            screen.reset_color()
            screen.write(indent)
            screen.set_color(colors['bullet'])
            screen.bold()
            screen.write(num + ' ')
            screen.reset_color()
            self.draw_inline_formatted(screen, text, max_width - len(indent) - len(num) - 1, colors)
            return

        # Regular line with inline formatting
        screen.reset_color()
        self.draw_inline_formatted(screen, line, max_width, colors)

    def draw_inline_formatted(self, screen: TUIScreen, text: str, max_width: int,
                              colors: dict = None):
        """Draw text with inline markdown formatting (bold, italic, code)"""
        if colors is None:
            colors = MARKDOWN_COLORS
        screen.set_color(colors['normal'])
        pos = 0
        written = 0

        text = normalize(text)
        # Reserve space for "..." when text will likely overflow
        limit = max(0, max_width - 3) if display_width(text) > max_width else max_width

        while pos < len(text) and written < limit:
            remaining = limit - written

            # Inline code `code`
            if text[pos] == '`':
                end = text.find('`', pos + 1)
                if end != -1:
                    code_text = text[pos + 1:end]
                    if display_width(code_text) + 2 <= remaining:
                        screen.reset_color()
                        screen.set_color(colors['code'])
                        screen.write(code_text)
                        written += display_width(code_text)
                        pos = end + 1
                        screen.reset_color()
                        screen.set_color(colors['normal'])
                        continue

            # Bold **text**
            if text[pos:pos + 2] == '**':
                end = text.find('**', pos + 2)
                if end != -1:
                    bold_text = text[pos + 2:end]
                    if display_width(bold_text) <= remaining:
                        screen.reset_color()
                        screen.bold()
                        screen.set_color(colors['bold'])
                        screen.write(bold_text)
                        written += display_width(bold_text)
                        pos = end + 2
                        screen.reset_color()
                        screen.set_color(colors['normal'])
                        continue

            # Italic *text* (single asterisk, not followed by another)
            if text[pos] == '*' and (pos + 1 >= len(text) or text[pos + 1] != '*'):
                end = pos + 1
                while end < len(text):
                    if text[end] == '*' and (end + 1 >= len(text) or text[end + 1] != '*'):
                        break
                    end += 1
                if end < len(text):
                    italic_text = text[pos + 1:end]
                    if display_width(italic_text) <= remaining:
                        screen.reset_color()
                        screen.italic()
                        screen.set_color(colors['normal'])
                        screen.write(italic_text)
                        written += display_width(italic_text)
                        pos = end + 1
                        screen.reset_color()
                        screen.set_color(colors['normal'])
                        continue

            # Normal character
            cw = char_width(text[pos])
            if written + cw > limit:
                break
            screen.write(text[pos])
            written += cw
            pos += 1

        # Truncation indicator
        if pos < len(text):
            screen.write("...")
