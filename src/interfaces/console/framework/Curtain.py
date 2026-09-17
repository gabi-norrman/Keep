"""The curtains that open the startup intro: two black bars closing from the left and right
edges of the terminal to the middle over `CURTAIN_SECONDS`. They are raw bytes for the
client's terminal, sent before the console's own setup clears the screen, so whatever the
terminal shows — the previous process's last frame under tui.py's reconnect box, or the
shell — is wiped in the open rather than blinked away. Painting black needs nothing read
back from the terminal. `frames()` is pure: the bytes of every frame for a screen size, for
the server to pace and send."""
from typing import Iterator

from interfaces.console.framework.TUIScreen import TUIScreen

CURTAIN_SECONDS = 0.5
_BLACK = f"{TUIScreen.RESET}{TUIScreen.BACKGROUND_SEQ}"


def frame_count(fps: int) -> int:
    return max(1, round(CURTAIN_SECONDS * fps))


def covered(cols: int, u: float) -> int:
    """How many columns each curtain covers `u` (0..1) of the way through."""
    return int(cols / 2 * min(1.0, max(0.0, u)) + 0.5)


def frame(cols: int, rows: int, covered_cols: int) -> bytes:
    """One frame: each curtain `covered_cols` wide; once the two meet or overlap every row
    is black edge to edge."""
    closed = covered_cols * 2 >= cols
    parts = [TUIScreen.BEGIN_SYNC]
    for y in range(1, rows + 1):
        if closed:
            parts.append(f"{TUIScreen.CSI}{y};1H{_BLACK}{' ' * cols}")
            continue
        if covered_cols < 1:
            continue
        bar = ' ' * covered_cols
        parts.append(f"{TUIScreen.CSI}{y};1H{_BLACK}{bar}{TUIScreen.CSI}{y};{cols - covered_cols + 1}H{bar}")
    parts.append(f"{TUIScreen.RESET}{TUIScreen.END_SYNC}")
    return "".join(parts).encode("utf-8")


def frames(cols: int, rows: int, fps: int = 30) -> Iterator[bytes]:
    """Every frame of the curtains in order, the last one all black."""
    count = frame_count(fps)
    for i in range(1, count + 1):
        yield frame(cols, rows, covered(cols, i / count))
