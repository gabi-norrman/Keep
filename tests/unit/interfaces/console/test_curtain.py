"""The startup curtains: two black bars closing to the middle."""
from interfaces.console.framework import Curtain
from interfaces.console.framework.TUIScreen import TUIScreen

BLACK = TUIScreen.BACKGROUND_SEQ


def rows(data: bytes):
    return [part for part in data.decode().split(TUIScreen.CSI) if part and part[0].isdigit() and "H" in part]


def test_frames_last_the_curtain_seconds_and_end_all_black():
    frames = list(Curtain.frames(20, 3, fps=10))
    assert len(frames) == round(Curtain.CURTAIN_SECONDS * 10) == 5
    last = frames[-1].decode()
    assert last.count(" " * 20) == 3
    assert last.startswith(TUIScreen.BEGIN_SYNC) and last.endswith(TUIScreen.END_SYNC)


def test_a_frame_paints_each_row_black_on_both_sides_and_nothing_between():
    data = Curtain.frame(20, 2, 4).decode()
    assert f"{TUIScreen.CSI}1;1H{TUIScreen.RESET}{BLACK}    {TUIScreen.CSI}1;17H    " in data
    assert f"{TUIScreen.CSI}2;1H" in data
    assert data.count(BLACK) == 2, "one black run per row, the middle untouched"


def test_the_curtains_advance_every_frame_and_meet_in_the_middle():
    count = Curtain.frame_count(30)
    widths = [Curtain.covered(81, i / count) for i in range(1, count + 1)]
    assert widths == sorted(widths) and widths[0] >= 1
    assert widths[-1] * 2 >= 81, "the last frame closes the gap"
    assert Curtain.frame(81, 1, widths[-1]).decode().count(" " * 81) == 1


def test_nothing_is_drawn_before_the_first_column_is_covered():
    data = Curtain.frame(200, 5, 0).decode()
    assert "H" not in data.replace(TUIScreen.BEGIN_SYNC, "").replace(TUIScreen.END_SYNC, "")
