"""The orange mark every console view puts on a masked vault value (`•••• 1234`,
`a•••@example.com`), shared by the Memory tab and the Home chat."""
from common.vault.Redactor import Redactor
from interfaces.console.Theme import COLOR_SECRET
from interfaces.console.framework.TUIScreen import TUIScreen
from interfaces.console.framework.TextWidth import display_width


def mark_masks(screen: TUIScreen, redactor: Redactor, x: int, y: int, width: int):
    """Every mask on the drawn row in bold COLOR_SECRET. Matching the rendered row rather
    than the source text means a mask keeps its mark when markdown markers around it were
    stripped; a mask that word-wrap split over two rows loses its colour, nothing more."""
    text = screen.row_text(x, y, width)
    for start, end in redactor.masked_spans(text):
        col = display_width(text[:start])
        screen.recolor_row(x + col, y, display_width(text[start:end]), COLOR_SECRET, bold=True)
