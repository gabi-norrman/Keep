"""The Vault tab: the secrets in vault.bin as a board in the Dashboard's tiles — summary tiles
(the vault's state, how many secrets and how they are matched), the secrets
in a table with their values masked, the selected one in a tile of its own that turns into the
form of the add, edit and delete flows (VAULT_PLAN §3.8), and a plain-words explanation of how
the vault works. From 150 columns the form and the explanation sit beside the table, under it
on a narrower screen. It talks to the `Vault` singleton directly, so an edit is live for the
next request. An id is the slug of its description, so an edit that changes the description
offers to rename the id to the new slug (y/n) before the value step; the LLM addresses secrets
by id, so it is never renamed silently. Every text field is a `LineEditor(persist_history=False)`:
no key of a secret ever reaches cache/tui_state.json, and no value is ever logged.
"""
from typing import Callable, Dict, List, Optional, Tuple

from common.logging import info, trace
from common.vault.SecretKinds import KINDS, VERBATIM_KINDS
from common.vault.Vault import Secret, Vault, VaultError
from interfaces.console import Theme
from interfaces.console.framework.TUIScreen import TUIScreen, Color
from interfaces.console.framework.TextWidth import fit
from interfaces.console.tabs.AuthTab import ENTER_KEYS
from interfaces.console.tabs.ScrollableTabView import COLOR_HINT, draw_scrollbar
from interfaces.console.tabs.TabView import CONTENT_TOP, TabView
from interfaces.console.widgets import Markdown, Tile
from interfaces.console.widgets.LineEditor import LineEditor

VIEW = "view"
ADD_KIND = "add kind"
ADD_DESCRIPTION = "add description"
ADD_VALUE = "add value"
EDIT_DESCRIPTION = "edit description"
CONFIRM_RENAME = "confirm rename"
EDIT_VALUE = "edit value"
CONFIRM_DELETE = "confirm delete"
ADD_MODES = (ADD_KIND, ADD_DESCRIPTION, ADD_VALUE)
EDIT_MODES = (EDIT_DESCRIPTION, CONFIRM_RENAME, EDIT_VALUE)
CONFIRM_MODES = (CONFIRM_RENAME, CONFIRM_DELETE)
TEXT_MODES = (ADD_DESCRIPTION, ADD_VALUE, EDIT_DESCRIPTION, EDIT_VALUE)
VALUE_MODES = (ADD_VALUE, EDIT_VALUE)

# Reveal while typing a value: 'v' may well be part of the value, Tab cannot be
REVEAL_FIELD_KEY = 'tab'
COMMAND_KEYS = ('a', 'e', 'd', 'v')

LEFT = 3
RIGHT_MARGIN = 2
GAP = 2
SUMMARY_H = 1 + Tile.CARD_CHROME
SUMMARY_TILE_MIN_W = 30           # a narrower screen shows the first tiles only
SIDE_BY_SIDE_MIN = 150            # screen columns from which the form and the help sit beside the table
SIDE_MIN_W = 50
MIN_LIST_ROWS = 3
# The table's columns after the state dot; the value takes what is left
ID_WIDTH, KIND_WIDTH, DESCRIPTION_WIDTH = 28, 12, 30
DESCRIPTION_MIN_WIDTH = 12
VALUE_MIN_WIDTH = 24              # the description gives way before the value is cut shorter than this
FORM_ROWS = 3                     # the fields of the selected secret, or of the flow that is open
FORM_LABEL_W = 13
MASK = "•"
CURSOR = "▏"
PENDING = "–"
EMPTY_HINT = "No secrets yet — press a to add one"

# The explanation, for someone who runs the console but does not read the code: markdown, laid
# out by `widgets/Markdown` to its tile but never wider than HELP_MAX_WIDTH. Shown only while
# the list keeps MIN_LIST_ROWS rows and the text stays readable; PgUp/PgDn scroll it.
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

    "> **The secret values stay on your local machine.** The AI in the cloud never sees them.",
)
HELP_MAX_WIDTH = 100
MIN_HELP_TEXT_WIDTH = 30
HELP_MIN_ROWS = 4

COLOR_FORMAT_KIND = Theme.COLOR_BLUE      # a kind matched however it is written
COLOR_VERBATIM_KIND = Theme.COLOR_PURPLE  # a kind matched exactly as typed
COLOR_PROMPT = Theme.COLOR_YELLOW         # the step a flow waits on

HINTS = {
    VIEW: "↑↓ select  a add  e edit  d delete  v reveal  PgUp/PgDn scroll the help",
    ADD_KIND: "←→ kind  Enter next  Esc cancel",
    ADD_DESCRIPTION: "Enter next  Esc cancel",
    ADD_VALUE: "Enter save  Tab reveal  Esc cancel",
    EDIT_DESCRIPTION: "Enter next  Esc cancel",
    CONFIRM_RENAME: "y adjust the id  n keep it  Esc cancel",
    EDIT_VALUE: "Enter save (empty keeps the old value)  Tab reveal  Esc cancel",
    CONFIRM_DELETE: "y delete  n/Esc cancel",
}

# A summary tile: its label, the value in its title strip, the value's colour, the row under it.
SummaryTile = Tuple[str, str, Color, List[Tuple[str, Color]]]
# A row of the form tile: its label, its value, the value's colour, whether the flow waits on it.
Field = Tuple[str, str, Color, bool]


def kind_color(kind: str) -> Color:
    return COLOR_VERBATIM_KIND if kind in VERBATIM_KINDS else COLOR_FORMAT_KIND


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
        self._status_color = Theme.COLOR_GREEN
        self._cards: List[Tile.Card] = []
        self._top = 0
        self._help_layouts: Dict[int, List[Markdown.Row]] = {}
        self._help_top = 0
        self._help_room = 1

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
        self._cards = []
        rows = self._rows()
        selected = self._selected_secret(rows)
        available = screen.width - LEFT - RIGHT_MARGIN
        bottom = screen.height - 1                # the last row is the key hints'
        y = CONTENT_TOP
        side = screen.width >= SIDE_BY_SIDE_MIN
        # The form's fields and its status row, a blank row between them where there is height for one
        form_h = Tile.CARD_CHROME + FORM_ROWS + (2 if side else 1)
        table_min_h = Tile.CARD_CHROME + 1 + MIN_LIST_ROWS

        if bottom - y - SUMMARY_H - 1 >= table_min_h + (0 if side else 1 + form_h):
            self._draw_summary(screen, y, available, self._summary_tiles(rows))
            y += SUMMARY_H + 1
        height = bottom - y

        if side:
            side_w = max(SIDE_MIN_W, available * 36 // 100)
            table_w = available - GAP - side_w
            side_x = LEFT + table_w + GAP
            self._draw_table(screen, LEFT, y, table_w, height, rows)
            self._draw_form(screen, side_x, y, side_w, min(form_h, height), selected)
            self._draw_help(screen, side_x, y + form_h + 1, side_w, height - form_h - 1)
        elif height - 1 - form_h < Tile.CARD_CHROME + 2:
            # No room for both: the list, or the form while a flow is open
            if self._mode == VIEW:
                self._draw_table(screen, LEFT, y, available, height, rows)
            else:
                self._draw_form(screen, LEFT, y, available, min(form_h, height), selected)
        else:
            table_h = min(max(1, len(rows)) + Tile.CARD_CHROME + 1, height - 1 - form_h)
            self._draw_table(screen, LEFT, y, available, table_h, rows)
            y += table_h + 1
            self._draw_form(screen, LEFT, y, available, form_h, selected)
            y += form_h + 1
            self._draw_help(screen, LEFT, y, available, bottom - y)
        Tile.tint_cards(screen, self._cards)

        screen.reset_color()
        screen.set_color(COLOR_HINT)
        screen.write_at(LEFT, screen.height - 1, fit(HINTS[self._mode], max(0, available)))
        screen.reset_color()

    # --- summary tiles -----------------------------------------------------------------------

    def _draw_summary(self, screen: TUIScreen, y: int, available: int, tiles: List[SummaryTile]):
        tiles = tiles[:max(1, available // SUMMARY_TILE_MIN_W)]
        count = len(tiles)
        width = (available - GAP * (count - 1)) // count
        for i, (label, value, color, segments) in enumerate(tiles):
            x = LEFT + i * (width + GAP)
            tile_w = width if i < count - 1 else available - (width + GAP) * (count - 1)
            Tile.draw_card(screen, self._cards, x, y, tile_w, SUMMARY_H, label, value, color)
            Tile.write_segments(screen, x + 2, y + Tile.CARD_TOP, tile_w - 4, segments)

    def _summary_tiles(self, rows: list[Secret]) -> List[SummaryTile]:
        return [self._vault_tile(), self._secrets_tile(rows)]

    def _vault_tile(self) -> SummaryTile:
        vault = self.vault
        if not vault.enabled:
            return "Vault", "locked", Theme.COLOR_RED, [(str(vault.error), Theme.COLOR_RED)]
        return ("Vault", "unlocked", Theme.COLOR_GREEN,
                [(vault.path.name, Theme.COLOR_LABEL), ("key in config.yaml", Theme.COLOR_DIM)])

    @staticmethod
    def _secrets_tile(rows: list[Secret]) -> SummaryTile:
        if not rows:
            return "Secrets", "0", Theme.COLOR_DIM, [("none yet", Theme.COLOR_DIM)]
        verbatim = sum(1 for secret in rows if secret.kind in VERBATIM_KINDS)
        groups = ((len(rows) - verbatim, "by format", COLOR_FORMAT_KIND),
                  (verbatim, "verbatim", COLOR_VERBATIM_KIND))
        return ("Secrets", str(len(rows)), Theme.COLOR_BRIGHT,
                [(f"{count} {label}", color) for count, label, color in groups if count])

    # --- the table ---------------------------------------------------------------------------

    def _draw_table(self, screen: TUIScreen, x: int, y: int, width: int, height: int, rows: list[Secret]):
        """The secrets, one per row: a dot in the colour of how its kind is matched, the id
        (bright and bold on the selected row), the kind, the description, and the value —
        masked and dim, or revealed in the console's secret orange. Columns past the right
        edge of a narrow client shrink to nothing rather than wrap."""
        if height <= Tile.CARD_CHROME:
            return
        vault = self.vault
        if vault.enabled:
            Tile.draw_card(screen, self._cards, x, y, width, height, "Secrets", f"{len(rows)} in {vault.path.name}")
        else:
            Tile.draw_card(screen, self._cards, x, y, width, height, "Secrets", "LOCKED", Theme.COLOR_RED)
        inner_w = width - 4
        header_y = y + Tile.CARD_TOP
        if not rows:
            notice, color = (EMPTY_HINT, Theme.COLOR_DIM) if vault.enabled else (f"Locked: {vault.error}", Theme.COLOR_RED)
            screen.set_color(color)
            screen.write_at(x + 2, header_y, fit(notice, max(0, inner_w)))
            screen.reset_color()
            return
        room = height - Tile.CARD_CHROME - 1    # a row goes to the column titles
        if room <= 0:
            return
        count = len(rows)
        self._top = max(0, min(max(min(self._top, self._selected), self._selected - room + 1), count - room))

        description_w = max(DESCRIPTION_MIN_WIDTH, min(DESCRIPTION_WIDTH,
                                                       inner_w - 2 - ID_WIDTH - KIND_WIDTH - 6 - VALUE_MIN_WIDTH))
        self._write_cells(screen, x + 4, header_y, inner_w - 2,
                          [(title, column_w, Theme.COLOR_DIM, False) for title, column_w in
                           (("Id", ID_WIDTH), ("Kind", KIND_WIDTH), ("Description", description_w), ("Value", None))])
        for i, secret in enumerate(rows[self._top:self._top + room]):
            row_y = header_y + 1 + i
            chosen = self._top + i == self._selected
            revealed = secret.id in self._revealed
            screen.reset_color()
            screen.set_color(kind_color(secret.kind))
            screen.write_at(x + 2, row_y, "●")
            self._write_cells(screen, x + 4, row_y, inner_w - 2, [
                (secret.id, ID_WIDTH, Theme.COLOR_BRIGHT if chosen else Theme.COLOR_TEXT, chosen),
                (secret.kind, KIND_WIDTH, kind_color(secret.kind), False),
                (secret.description, description_w, Theme.COLOR_TEXT if chosen else Theme.COLOR_LABEL, False),
                (secret.value if revealed else secret.masked, None,
                 Theme.COLOR_SECRET if revealed else Theme.COLOR_LABEL if chosen else Theme.COLOR_DIM, revealed),
            ])
            if chosen:
                screen.tint_row(x, row_y, width, Theme.COLOR_SELECTED_BG)
        draw_scrollbar(screen, x + width - 1, header_y + 1, count, room, count - room - self._top)
        screen.reset_color()

    @staticmethod
    def _write_cells(screen: TUIScreen, x: int, y: int, width: int, cells: list[tuple[str, Optional[int], Color, bool]]):
        """One table row: each (text, width, fg, bold) cell in turn, two columns apart, the
        last one (width None) taking what is left of `width`."""
        remaining = width
        for text, cell_w, fg, bold in cells:
            cell_w = remaining if cell_w is None else min(cell_w, remaining)
            if cell_w <= 0:
                break
            screen.reset_color()
            screen.set_color(fg)
            if bold:
                screen.bold()
            screen.write_at(x + width - remaining, y, fit(text, cell_w))
            remaining -= cell_w + 2
        screen.reset_color()

    # --- the selected secret and the flows' form -----------------------------------------------

    def _draw_form(self, screen: TUIScreen, x: int, y: int, width: int, height: int, selected: Optional[Secret]):
        """The selected secret's fields; while a flow is open, its steps — the one it waits on
        with a yellow label, the answered ones above it, the ones to come faint — and on the
        last row the outcome of the last write."""
        if height <= Tile.CARD_CHROME:
            return
        label, value, color, fields = self._form(selected)
        Tile.draw_card(screen, self._cards, x, y, width, height, label, value, color)
        value_x = x + 2 + FORM_LABEL_W
        value_w = max(0, width - 4 - FORM_LABEL_W)
        last = y + height - 2
        for i, (name, text, text_color, waiting) in enumerate(fields):
            row_y = y + Tile.CARD_TOP + i
            if row_y > last:
                break
            screen.reset_color()
            screen.set_color(COLOR_PROMPT if waiting else Theme.COLOR_DIM)
            screen.write_at(x + 2, row_y, name)
            screen.set_color(text_color)
            if waiting or text_color == Theme.COLOR_SECRET:
                screen.bold()
            screen.write_at(value_x, row_y, fit(text, value_w))
        screen.reset_color()
        if self._status and last >= y + Tile.CARD_TOP + len(fields):
            screen.set_color(self._status_color)
            screen.write_at(x + 2, last, fit(self._status, max(0, width - 4)))
            screen.reset_color()

    def _form(self, selected: Optional[Secret]) -> Tuple[str, str, Color, List[Field]]:
        """(the tile's label, the value in its title strip, that value's colour, its fields)."""
        mode = self._mode
        if mode in ADD_MODES:
            step = ADD_MODES.index(mode)
            kind = KINDS[self._kind_index]
            return "Add a secret", f"step {step + 1} of {len(ADD_MODES)}", COLOR_PROMPT, self._steps(step, [
                ("Kind", f"‹ {kind} ›" if step == 0 else kind),
                ("Description", self._field_text() if step == 1 else self._description or PENDING),
                ("Value", self._field_text() if step == 2 else PENDING)])
        if mode in EDIT_MODES:
            step, target = EDIT_MODES.index(mode), self._target
            new_id = f"{target.id} → {self._new_id}?  y/n" if step == 1 else self._new_id or target.id
            return f"Edit {target.id}", target.kind, kind_color(target.kind), self._steps(step, [
                ("Description", self._field_text() if step == 0 else self._description or target.description),
                ("Id", new_id),
                ("New value", self._field_text() if step == 2 else PENDING)])
        if mode == CONFIRM_DELETE:
            target = self._target
            return f"Delete {target.id}", "confirm", Theme.COLOR_RED, [
                ("Description", target.description, Theme.COLOR_TEXT, False),
                ("Value", target.masked, Theme.COLOR_LABEL, False),
                ("Delete it?", "y/n", Theme.COLOR_RED, True)]
        if selected is None:
            return "No secret selected", "", Theme.COLOR_DIM, []
        revealed = selected.id in self._revealed
        return selected.id, selected.kind, kind_color(selected.kind), [
            ("Description", selected.description, Theme.COLOR_TEXT, False),
            ("Value", selected.value if revealed else selected.masked,
             Theme.COLOR_SECRET if revealed else Theme.COLOR_LABEL, False),
            ("The AI sees", f"{{{{secret:{selected.id}}}}}", Theme.COLOR_TEAL, False)]

    @staticmethod
    def _steps(waiting: int, fields: list[tuple[str, str]]) -> List[Field]:
        colors = (Theme.COLOR_TEXT, Theme.COLOR_BRIGHT, Theme.COLOR_FAINT)   # answered, waited on, to come
        return [(label, text, colors[(i >= waiting) + (i > waiting)], i == waiting)
                for i, (label, text) in enumerate(fields)]

    def _field_text(self) -> str:
        """The field as typed so far with the cursor in it; a value is drawn as one MASK per
        character until revealed."""
        text, cursor = self._editor.text, self._editor.cursor
        if self._mode in VALUE_MODES and not self._reveal_field:
            text = MASK * len(text)
        return text[:cursor] + CURSOR + text[cursor:]

    # --- the explanation ---------------------------------------------------------------------

    def _help_rows(self, width: int) -> List[Markdown.Row]:
        if width not in self._help_layouts:
            lines = [line for paragraph in HELP for line in (paragraph, "")][:-1]
            self._help_layouts[width] = Markdown.layout(lines, width).rows
        return self._help_layouts[width]

    def _draw_help(self, screen: TUIScreen, x: int, y: int, width: int, height: int):
        """HELP in a tile as tall as the text, or as the room it is given; nothing when that
        is too little to read."""
        text_w = min(HELP_MAX_WIDTH, width - 4)
        if height < Tile.CARD_CHROME + HELP_MIN_ROWS or text_w < MIN_HELP_TEXT_WIDTH:
            return
        rows = self._help_rows(text_w)
        height = min(height, len(rows) + Tile.CARD_CHROME)
        room = self._help_room = height - Tile.CARD_CHROME
        self._help_top = max(0, min(self._help_top, len(rows) - room))
        Tile.draw_card(screen, self._cards, x, y, width, height, HELP_TITLE)
        top_y = y + Tile.CARD_TOP
        for i, row in enumerate(rows[self._help_top:self._help_top + room]):
            Markdown.draw_row(screen, x + 2, top_y + i, text_w, row)
        draw_scrollbar(screen, x + width - 1, top_y, len(rows), room, len(rows) - room - self._help_top)
        screen.reset_color()

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
        elif key in ('pageup', 'pagedown'):
            self._help_top = max(0, self._help_top + (self._help_room if key == 'pagedown' else -self._help_room))
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
                self._set_status("A description is required", Theme.COLOR_RED)
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
                self._set_status("Nothing changed", Theme.COLOR_YELLOW)
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
            self._set_status(f"Cannot {verb}: {e}", Theme.COLOR_RED)
            info(f"Vault tab: {verb} failed: {e}")
            return
        self._cancel()
        self._revealed.discard(secret.id)
        self._select(secret.id)
        self._set_status(f"{done} '{secret.id}' ({secret.kind})", Theme.COLOR_GREEN)
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
