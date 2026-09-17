"""The Vault tab: the secrets in vault.bin — listed masked, with add, edit and delete flows
(VAULT_PLAN §3.8) and, under the list, a plain-words explanation of how the vault works. It
talks to the `Vault` singleton directly, so an edit is live for the next request. An id is the
slug of its description, so an edit that changes the description offers to rename the id to
the new slug (y/n) before the value step; the LLM addresses secrets by id, so it is never
renamed silently. Every text field is a `LineEditor(persist_history=False)`: no key of a
secret ever reaches cache/tui_state.json, and no value is ever logged.
"""
import re
import textwrap
from typing import Callable, Optional

from common.logging import info, trace
from common.vault.SecretKinds import KINDS
from common.vault.Vault import Secret, Vault, VaultError
from interfaces.console import Theme
from interfaces.console.framework.TUIScreen import TUIScreen, Color
from interfaces.console.framework.TextWidth import fit, ljust
from interfaces.console.tabs.AuthTab import (COLOR_GRAY, COLOR_GREEN, COLOR_HELP, COLOR_RED, COLOR_WHITE,
                                             COLOR_YELLOW, ENTER_KEYS)
from interfaces.console.tabs.MarkdownTabView import MARKDOWN_COLORS
from interfaces.console.tabs.TabView import CONTENT_TOP, TabView
from interfaces.console.widgets.LineEditor import LineEditor

VIEW = "view"
ADD_KIND = "add kind"
ADD_DESCRIPTION = "add description"
ADD_VALUE = "add value"
EDIT_DESCRIPTION = "edit description"
CONFIRM_RENAME = "confirm rename"
EDIT_VALUE = "edit value"
CONFIRM_DELETE = "confirm delete"
CONFIRM_MODES = (CONFIRM_RENAME, CONFIRM_DELETE)
TEXT_MODES = (ADD_DESCRIPTION, ADD_VALUE, EDIT_DESCRIPTION, EDIT_VALUE)
VALUE_MODES = (ADD_VALUE, EDIT_VALUE)

# Reveal while typing a value: 'v' may well be part of the value, Tab cannot be
REVEAL_FIELD_KEY = 'tab'
COMMAND_KEYS = ('a', 'e', 'd', 'v')

LEFT = 2
MARKER_WIDTH = 2                  # "› " in front of the id
ID_WIDTH, KIND_WIDTH, DESCRIPTION_WIDTH = 28, 13, 28
MASK = "•"
CURSOR = "▏"
EMPTY_HINT = "(no secrets yet — press a to add one)"

# The explanation under the list, for someone who runs the console but does not read the code:
# paragraphs with **bold** emphasis, wrapped to the screen but never wider than HELP_MAX_WIDTH
# and centred. Shown only while the list keeps MIN_LIST_ROWS rows and the text stays readable.
HELP_TITLE = "How the vault works"
HELP = (
    "Secrets are stored encrypted in **vault.bin**, unlocked by the passphrase **vault.key in config.yaml**. "
    "Without it nothing opens the file: **lose the key and the secrets are gone**, change it and the vault locks.",

    "Before a request reaches the AI, every secret in it is swapped for a stand-in such as **{{secret:work-visa}}**, "
    "whether it appears in your words, the conversation, the memory or a command result. **The AI sees only the "
    "stand-in and your description**, and the real value is put back into its answer on the way out: it can enter "
    "your card number on a website, or put your account number in an e-mail, without ever seeing either.",

    "A secret is recognised **however it is written**: a phone as +46 70-123 45 67, 070-1234567 or 46701234567, "
    "cards and accounts with or without spaces or dashes, plates and e-mails in upper or lower case, a personal "
    "identity number with 10 or 12 digits. Passwords, API keys and text must appear **exactly as typed**; anything "
    "shorter than six characters is refused as too short to hide safely.",

    "**Add** passwords, API keys, card numbers, bank accounts, personal identity numbers, phone numbers, e-mail "
    "addresses and registration plates. **Don't add** names, addresses, birthdays, places and other everyday facts "
    "— the AI needs them to be useful. The vault keeps specific secrets out of the AI; "
    "**it is not an anonymisation tool**.",

    "        **The secret values stay on your local machine.** The AI in the cloud never sees them.",
)
BOLD_RE = re.compile(r"\*\*(.+?)\*\*")
HELP_MAX_WIDTH = 100
MIN_HELP_TEXT_WIDTH = 30
MIN_LIST_ROWS = 3

# The table: a slate header plate, every other row faintly tinted, the selection a navy bar
COLOR_HEADER_BG = Color(30, 34, 46)
COLOR_HEADER_FG = Color(190, 195, 210)
COLOR_STRIPE_BG = Color(16, 18, 23)
COLOR_SELECTED_BG = Theme.COLOR_BLUE_BG_BRIGHTER
COLOR_KIND = Theme.COLOR_BLUE_FG_BRIGHTER
COLOR_MASK = Color(110, 110, 110)
COLOR_HELP_TITLE = MARKDOWN_COLORS['h1']   # the Memory tab's top-level headers

HINTS = {
    VIEW: "↑↓ select  a add  e edit  d delete  v reveal",
    ADD_KIND: "←→ kind  Enter next  Esc cancel",
    ADD_DESCRIPTION: "Enter next  Esc cancel",
    ADD_VALUE: "Enter save  Tab reveal  Esc cancel",
    EDIT_DESCRIPTION: "Enter next  Esc cancel",
    CONFIRM_RENAME: "y adjust the id  n keep it  Esc cancel",
    EDIT_VALUE: "Enter save (empty keeps the old value)  Tab reveal  Esc cancel",
    CONFIRM_DELETE: "y delete  n/Esc cancel",
}


class VaultTab(TabView):
    def __init__(self, vault: Optional[Vault] = None):
        self._vault = vault                       # the singleton, resolved on first use
        self._mode = VIEW
        self._selected = 0
        self._revealed: set[str] = set()
        self._editor: Optional[LineEditor] = None
        self._kind_index = 0
        self._target: Optional[Secret] = None     # the secret being edited or deleted
        self._description: Optional[str] = None   # the description accepted earlier in the flow
        self._new_id: Optional[str] = None        # the id the edit renames to, once confirmed
        self._reveal_field = False
        self._status = ""
        self._status_color = COLOR_GREEN

    @property
    def vault(self) -> Vault:
        if self._vault is None:
            self._vault = Vault.get_instance()
        return self._vault

    @property
    def captures_text(self) -> bool:
        return self._mode in TEXT_MODES

    @property
    def claims_tab(self) -> bool:
        return self._mode in VALUE_MODES

    # --- the list ----------------------------------------------------------------------------

    def _rows(self) -> list[Secret]:
        return sorted(self.vault.secrets(), key=lambda s: s.id)

    def _selected_secret(self, rows: list[Secret]) -> Optional[Secret]:
        if not rows:
            self._selected = 0
            return None
        self._selected = max(0, min(self._selected, len(rows) - 1))
        return rows[self._selected]

    def _select(self, secret_id: str):
        ids = [s.id for s in self._rows()]
        if secret_id in ids:
            self._selected = ids.index(secret_id)

    # --- drawing -----------------------------------------------------------------------------

    def draw(self, screen: TUIScreen, frame: int):
        vault = self.vault
        rows = self._rows()
        selected = self._selected_secret(rows)
        width = screen.width - LEFT
        y = CONTENT_TOP

        self._draw_title(screen, y, width, vault, len(rows))
        y += 2
        self._draw_header(screen, y, width)
        y += 1

        # Under the list: a blank row, the prompt and the status; the hint row sits at the bottom
        # with the help block (a blank row, its title, a blank row, its lines, a blank row) above
        # it when the list still keeps MIN_LIST_ROWS
        available = screen.height - 4 - y
        help_lines = self._help_lines(width)
        if available - len(help_lines) - 4 < MIN_LIST_ROWS:
            help_lines = []
        list_rows = max(1, available - (len(help_lines) + 4 if help_lines else 0))
        if not rows:
            screen.reset_color()
            screen.set_color(COLOR_GRAY)
            screen.write_at(LEFT, y, self._fit(EMPTY_HINT if vault.enabled else "", width))
            y += 1
        else:
            start = max(0, self._selected - list_rows + 1)
            for index, secret in enumerate(rows[start:start + list_rows], start):
                self._draw_secret(screen, y, width, secret, secret is selected, striped=index % 2 == 1)
                y += 1

        y += 1
        self._draw_prompt(screen, y, width)
        if self._status:
            screen.reset_color()
            screen.set_color(self._status_color)
            screen.write_at(LEFT, y + 1, self._fit(self._status, width))
        if help_lines:
            self._draw_help(screen, screen.height - 4 - len(help_lines), width, help_lines)

        screen.reset_color()
        screen.set_color(COLOR_HELP)
        screen.write_at(LEFT, screen.height - 1, self._fit(HINTS[self._mode], width))
        screen.reset_color()

    def _draw_title(self, screen: TUIScreen, y: int, width: int, vault: Vault, count: int):
        screen.reset_color()
        screen.bold()
        if vault.enabled:
            screen.set_color(COLOR_WHITE)
            plural = "" if count == 1 else "s"
            title = f"Vault — {count} secret{plural} in {vault.path.name}"
        else:
            screen.set_color(COLOR_RED)
            title = f"Vault — LOCKED: {vault.error}"
        screen.write_at(LEFT, y, self._fit(title, width))

    def _draw_header(self, screen: TUIScreen, y: int, width: int):
        labels = (("", MARKER_WIDTH), ("ID", ID_WIDTH), ("KIND", KIND_WIDTH),
                  ("DESCRIPTION", DESCRIPTION_WIDTH), ("VALUE", None))
        self._draw_row(screen, y, width, COLOR_HEADER_BG,
                       [(text, cell_width, COLOR_HEADER_FG, True) for text, cell_width in labels])

    def _draw_secret(self, screen: TUIScreen, y: int, width: int, secret: Secret,
                     is_selected: bool, striped: bool):
        """A secret's row: the id white (bold on the selected row), the kind a periwinkle tag,
        a masked value dim and a revealed one in the console's secret orange."""
        revealed = secret.id in self._revealed
        value = secret.value if revealed else secret.masked
        bg = COLOR_SELECTED_BG if is_selected else COLOR_STRIPE_BG if striped else None
        text_fg = COLOR_WHITE if is_selected else COLOR_GRAY
        mask_fg = COLOR_GRAY if is_selected else COLOR_MASK
        self._draw_row(screen, y, width, bg, [
            ("›" if is_selected else "", MARKER_WIDTH, COLOR_WHITE, is_selected),
            (secret.id, ID_WIDTH, COLOR_WHITE, is_selected),
            (secret.kind, KIND_WIDTH, COLOR_KIND, False),
            (secret.description, DESCRIPTION_WIDTH, text_fg, False),
            (value, None, Theme.COLOR_SECRET if revealed else mask_fg, revealed),
        ])

    def _draw_row(self, screen: TUIScreen, y: int, width: int, bg: Optional[Color],
                  cells: list[tuple[str, Optional[int], Color, bool]]):
        """One table row over `bg` across the whole `width`: each (text, width, fg, bold) cell
        in turn, the last one (width None) taking what is left. Cells past the right edge of a
        narrow client shrink to nothing rather than wrap."""
        screen.move_cursor(LEFT, y)
        remaining = width
        for text, cell_width, fg, bold in cells:
            cell_width = remaining if cell_width is None else min(cell_width, remaining)
            screen.reset_color()
            if bg is not None:
                screen.set_bg_color(bg)
            screen.set_color(fg)
            if bold:
                screen.bold()
            screen.write(self._fit(text, cell_width))
            remaining -= cell_width

    def _draw_prompt(self, screen: TUIScreen, y: int, width: int):
        """The current step's yellow label with its value in white; nothing in view mode."""
        if self._mode == VIEW:
            return
        if self._mode == ADD_KIND:
            label, value = "Kind: ", f"‹ {KINDS[self._kind_index]} ›"
        elif self._mode == CONFIRM_DELETE:
            label, value = f"Delete {self._target.id}? ", "y/n"
        elif self._mode == CONFIRM_RENAME:
            label, value = f"Adjust id to {self._new_id}? ", "y/n"
        else:
            label, value = f"{self._field_label()}: ", self._field_text()
        screen.reset_color()
        screen.set_color(COLOR_YELLOW)
        screen.write_at(LEFT, y, self._fit(label, width))
        screen.set_color(COLOR_WHITE)
        screen.write_at(LEFT + len(label), y, self._fit(value, width - len(label)))

    @classmethod
    def _help_lines(cls, width: int) -> list[list[tuple[str, bool]]]:
        """HELP wrapped to `width` (at most HELP_MAX_WIDTH): one list of (text, bold) pieces per
        row, an empty list for the blank row between paragraphs; nothing when the screen is too
        narrow to read."""
        if width < MIN_HELP_TEXT_WIDTH:
            return []
        width = min(width, HELP_MAX_WIDTH)
        lines = []
        for paragraph in HELP:
            # Odd parts of the split are the bold ones; spans are kept in plain-text offsets
            plain, spans = "", []
            for i, part in enumerate(BOLD_RE.split(paragraph)):
                if i % 2:
                    spans.append((len(plain), len(plain) + len(part)))
                plain += part
            if lines:
                lines.append([])
            pos = 0
            for line in textwrap.wrap(plain, width, break_on_hyphens=False):
                start = plain.index(line, pos)
                pos = start + len(line)
                lines.append(cls._styled(line, start, spans))
        return lines

    @staticmethod
    def _styled(line: str, start: int, spans: list[tuple[int, int]]) -> list[tuple[str, bool]]:
        """`line` (a slice of its paragraph from `start`) cut into (text, bold) pieces."""
        pieces, cursor = [], 0
        for span_start, span_end in spans:
            lo, hi = max(span_start - start, cursor), min(span_end - start, len(line))
            if lo >= hi:
                continue
            if lo > cursor:
                pieces.append((line[cursor:lo], False))
            pieces.append((line[lo:hi], True))
            cursor = hi
        if cursor < len(line):
            pieces.append((line[cursor:], False))
        return pieces

    @staticmethod
    def help_left(screen_width: int) -> int:
        """Column of the help block's left edge: the block, as wide as the text wraps to, sits
        centred on the screen — at LEFT when the screen is no wider than that."""
        return (screen_width - min(screen_width - LEFT, HELP_MAX_WIDTH)) // 2 + 1

    def _draw_help(self, screen: TUIScreen, y: int, width: int, lines: list[list[tuple[str, bool]]]):
        """The title in the Memory tab's header blue, a blank row, then the text gray with the
        bold pieces bright white."""
        x, block_width = self.help_left(screen.width), min(width, HELP_MAX_WIDTH)
        screen.reset_color()
        screen.set_color(COLOR_HELP_TITLE)
        screen.bold()
        screen.write_at(x, y, self._fit(HELP_TITLE, block_width))
        y += 1
        for pieces in lines:
            y += 1
            screen.move_cursor(x, y)
            remaining = block_width
            for text, bold in pieces:
                screen.reset_color()
                screen.set_color(COLOR_WHITE if bold else COLOR_GRAY)
                if bold:
                    screen.bold()
                screen.write(text[:remaining])
                remaining -= len(text)

    def _field_label(self) -> str:
        if self._mode == ADD_VALUE:
            return f"Value ({KINDS[self._kind_index]})"
        if self._mode == EDIT_VALUE:
            return f"New value ({self._target.kind})"
        return "Description"

    def _field_text(self) -> str:
        """The field as typed so far with the cursor in it; a value is drawn as one MASK per
        character until revealed."""
        text, cursor = self._editor.text, self._editor.cursor
        if self._mode in VALUE_MODES and not self._reveal_field:
            text = MASK * len(text)
        return text[:cursor] + CURSOR + text[cursor:]

    @staticmethod
    def _fit(s: str, width: int) -> str:
        """`s` truncated with an ellipsis or padded to exactly `width` columns; "" when there
        is no room at all (a client narrower than the column geometry must not raise)."""
        if width <= 0:
            return ""
        return ljust(fit(s, width), width)

    # --- keys --------------------------------------------------------------------------------

    def handle_input(self, key: str) -> bool:
        if self._mode == VIEW:
            return self._handle_view_key(key)
        if key == 'escape':
            self._cancel()
        elif self._mode == ADD_KIND:
            self._handle_kind_key(key)
        elif self._mode in CONFIRM_MODES:
            self._handle_confirm_key(key)
        elif key in ENTER_KEYS:
            self._accept_field()
        elif key == REVEAL_FIELD_KEY and self._mode in VALUE_MODES:
            self._reveal_field = not self._reveal_field
        else:
            self._editor.handle_key(key)
        return True

    def _handle_view_key(self, key: str) -> bool:
        rows = self._rows()
        if key == 'up':
            self._selected = max(0, self._selected - 1)
        elif key == 'down':
            self._selected = min(max(0, len(rows) - 1), self._selected + 1)
        elif key in COMMAND_KEYS:
            if not self.vault.enabled:
                info(f"Vault tab: '{key}' ignored — the vault is locked ({self.vault.error})")
                return True
            self._command(key, self._selected_secret(rows))
        else:
            return False
        return True

    def _command(self, key: str, selected: Optional[Secret]):
        if key == 'a':
            self._kind_index = 0
            self._enter(ADD_KIND)
        elif selected is None:
            trace(f"Vault tab: '{key}' with no secret selected — nothing to do")
        elif key == 'v':
            self._revealed ^= {selected.id}
        elif key == 'e':
            self._target = selected
            self._enter(EDIT_DESCRIPTION, prefill=selected.description)
        elif key == 'd':
            self._target = selected
            self._enter(CONFIRM_DELETE)

    def _handle_kind_key(self, key: str):
        if key == 'left':
            self._kind_index = (self._kind_index - 1) % len(KINDS)
        elif key == 'right':
            self._kind_index = (self._kind_index + 1) % len(KINDS)
        elif key in ENTER_KEYS:
            self._enter(ADD_DESCRIPTION)

    def _handle_confirm_key(self, key: str):
        if key not in ('y', 'n'):
            return
        if self._mode == CONFIRM_RENAME:
            if key == 'n':
                trace(f"Vault tab: keeping id '{self._target.id}' (declined '{self._new_id}')")
                self._new_id = None
            self._enter(EDIT_VALUE)
        elif key == 'y':
            target = self._target
            self._commit("delete", "Deleted", lambda: self.vault.delete(target.id))
        else:
            self._cancel()

    def _accept_field(self):
        """Enter in a text field: on to the next step, or the write on the last one."""
        text = self._editor.text.strip()
        if self._mode == ADD_DESCRIPTION:
            if not text:
                self._set_status("A description is required", COLOR_RED)
                return
            self._description = text
            self._enter(ADD_VALUE)
        elif self._mode == ADD_VALUE:
            kind, description = KINDS[self._kind_index], self._description
            self._commit("add", "Added", lambda: self.vault.add(kind, description, text))
        elif self._mode == EDIT_DESCRIPTION:
            self._description = text or None
            self._offer_rename()
        elif self._mode == EDIT_VALUE:
            target, description, value, new_id = self._target, self._description, text or None, self._new_id
            if description is None and value is None and new_id is None:
                self._cancel()
                self._set_status("Nothing changed", COLOR_YELLOW)
                return
            done = f"Renamed '{target.id}' →" if new_id else "Updated"
            self._commit("update", done, lambda: self.vault.update(target.id, description, value, id=new_id))

    def _offer_rename(self):
        """After a changed description: ask about the id the new wording derives to, unless
        it is the current one; an unchanged description goes straight to the value step."""
        target, description = self._target, self._description
        if description is None or description == target.description:
            self._enter(EDIT_VALUE)
            return
        self._new_id = self.vault.suggest_id(description, target.kind, exclude=target.id)
        if self._new_id == target.id:
            trace(f"Vault tab: new description keeps id '{target.id}' — no rename to offer")
            self._new_id = None
            self._enter(EDIT_VALUE)
            return
        self._enter(CONFIRM_RENAME)

    def _commit(self, verb: str, done: str, write: Callable[[], Optional[Secret]]):
        """Run a vault write. Success: back to the list, the row selected and a green status;
        a VaultError: red status and the current step stays open so the input can be fixed."""
        try:
            secret = write() or self._target
        except VaultError as e:
            self._set_status(f"Cannot {verb}: {e}", COLOR_RED)
            info(f"Vault tab: {verb} failed: {e}")
            return
        self._cancel()
        self._revealed.discard(secret.id)
        self._select(secret.id)
        self._set_status(f"{done} '{secret.id}' ({secret.kind})", COLOR_GREEN)
        info(f"Vault tab: {verb} '{secret.id}' ({secret.kind})")

    def _enter(self, mode: str, prefill: str = ""):
        trace(f"Vault tab: {self._mode} → {mode}")
        self._mode = mode
        self._status = ""
        self._reveal_field = False
        self._editor = None
        if mode in TEXT_MODES:
            self._editor = LineEditor(persist_history=False)
            self._editor.insert(prefill)

    def _cancel(self):
        self._target = None
        self._description = None
        self._new_id = None
        self._enter(VIEW)

    def _set_status(self, text: str, color: Color):
        self._status, self._status_color = text, color

    # --- tab lifecycle -----------------------------------------------------------------------

    def on_deactivate(self):
        """Leaving the tab drops a half-typed flow and hides every revealed value."""
        if self._mode != VIEW:
            self._cancel()
        self._revealed.clear()
