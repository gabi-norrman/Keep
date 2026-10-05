"""PhotosTab's board: the summary tiles, the photo's description and its details, the scores
drawn as meters. The library is never read: the tab's state is set by hand and the reload
pinned off."""
import io

import pytest
from PIL import Image

from interfaces.console import Theme
from interfaces.console.framework.Pictures import Picture
from interfaces.console.framework.TUIScreen import TUIScreen
from interfaces.console.tabs.PhotosTab import PhotosTab
from interfaces.console.widgets.Board import SIDE_BY_SIDE_MIN

PHOTO = {
    "short_description": "Charlotte and Frances on the jetty",
    "detailed_description": "Two people sit on a wooden jetty with their feet over the water. " * 3,
    "creation_date": 1700000000.0, "location_text": "Sweden/Dalarna/Leksand", "latitude": 60.7301,
    "longitude": 14.9999, "albums": "Favorites", "persons": "Charlotte, Frances", "photo_type": "photo",
    "duration_seconds": None, "pretty_score": 87, "family_importance_score": 0, "indexed_at": 1727000000.0,
    "asset_guid": "0A1B2C3D-FAKE-GUID-0001",
}


def make_tab(photo=PHOTO, count=1234, total=20000, index=11):
    tab = PhotosTab()
    tab._last_load_time = float("inf")    # no background load during the test
    tab._photo, tab._count, tab._total_apple, tab._index = photo, count, total, index
    return tab


def draw(tab, width=110, height=40, graphics=False):
    screen = TUIScreen(width, height)
    screen.graphics = graphics
    tab.draw(screen, 0)
    return screen


def with_picture(tab, width=300, height=200):
    image = Image.new("RGB", (width, height), (200, 40, 40))
    tab._picture, tab._picture_guid = Picture(image), tab._photo["asset_guid"]
    return tab


def rows(screen):
    return ["".join(c.char for c in line).rstrip() for line in screen.next_frame]


def row_with(lines, needle):
    matches = [l for l in lines if needle in l]
    assert len(matches) == 1, (needle, lines)
    return matches[0]


@pytest.mark.unit
class TestSummary:
    def test_the_tiles_say_how_much_is_indexed_and_where_the_browsing_stands(self):
        lines = rows(draw(make_tab(), width=170))
        top = row_with(lines, "Browsing")
        assert "1,234" in top.split("Browsing")[0]               # the Indexed tile's value
        assert "12 of 1,234" in top
        assert any("6%" in l and "20,000" in l for l in lines)

    def test_an_indexing_outcome_is_shown_in_its_colour(self):
        tab = make_tab()
        tab._process_status_type, tab._process_status = "error", "Error: disk full"
        screen = draw(tab, width=170)
        lines = rows(screen)
        line = row_with(lines, "disk full")
        assert screen.next_frame[lines.index(line)][line.index("disk full")].style.fg == Theme.COLOR_RED

    def test_a_running_batch_shows_its_progress(self):
        tab = make_tab()
        tab._processing, tab._process_status = True, "Processing... 3/200 done"
        assert any("3/200" in l for l in rows(draw(tab, width=170)))


@pytest.mark.unit
class TestPhoto:
    def test_the_description_and_the_details_are_drawn(self):
        lines = rows(draw(make_tab()))
        assert any("Charlotte and Frances on the jetty" in l for l in lines)
        assert sum("wooden jetty" in l for l in lines) >= 2      # the long text wraps
        assert "Sweden, Dalarna, Leksand" in row_with(lines, "Location")
        assert "Charlotte, Frances" in row_with(lines, "Persons")
        assert "0A1B2C3D-FAKE-GUID-0001" in row_with(lines, "Id ")

    def test_a_score_is_a_meter_as_long_as_the_score_with_its_number(self):
        lines = rows(draw(make_tab()))
        pretty, family = row_with(lines, "Pretty score"), row_with(lines, "Family score")
        assert pretty.rstrip().endswith("87") and family.rstrip().endswith("0")
        assert pretty.count("█") > 10 and family.count("█") == 0
        # both numbers sit in the same column, after meters of equal length
        assert len(pretty) - 2 == len(family) - 1

    def test_the_text_stands_beside_the_picture_on_a_wide_screen_and_under_it_on_a_narrow_one(self):
        wide = rows(draw(make_tab(), width=SIDE_BY_SIDE_MIN))
        line = row_with(wide, "Charlotte and Frances on")
        assert line.index("Photo") < line.index("Charlotte and Frances")          # the picture card at the left
        assert wide.index(line) < wide.index(row_with(wide, "Details"))             # the details under the description
        narrow = rows(draw(make_tab(), width=SIDE_BY_SIDE_MIN - 1))
        assert narrow.index(row_with(narrow, "Photo")) < narrow.index(row_with(narrow, "Charlotte and Frances on")) \
            < narrow.index(row_with(narrow, "Details"))

    def test_nothing_runs_past_the_right_edge(self):
        for width in (80, 110, 170):
            assert all(len(l) <= width for l in rows(draw(make_tab(), width=width)))

    def test_a_short_screen_cuts_the_details_instead_of_failing(self):
        lines = rows(draw(make_tab(), height=18))
        assert any("Browsing" in l for l in lines)

    def test_an_empty_library_says_so_and_draws_no_details(self):
        lines = rows(draw(make_tab(photo=None, count=0, total=0, index=0)))
        assert not any("Details" in l or "Pretty score" in l for l in lines)
        assert any("No photos" in l for l in lines)
        assert any("Ctrl-P" in l for l in lines[:-2])            # how to index the first one


from interfaces.console.tabs.PhotosTab import DESCRIPTION_MIN_ROWS


@pytest.mark.unit
class TestNarrowLayout:
    def test_a_long_description_gives_way_so_the_details_stay_whole(self):
        photo = dict(PHOTO, detailed_description="A long afternoon on the jetty with the whole family. " * 40)
        description_rows = lambda height: sum("jetty" in l for l in rows(draw(make_tab(photo=photo), width=SIDE_BY_SIDE_MIN - 1, height=height)))
        whole = description_rows(200)
        lines = rows(draw(make_tab(photo=photo), width=SIDE_BY_SIDE_MIN - 1, height=36))
        assert "0A1B2C3D-FAKE-GUID-0001" in row_with(lines, "Id ")
        assert DESCRIPTION_MIN_ROWS <= description_rows(36) < whole


@pytest.mark.unit
class TestPicture:
    def test_the_picture_stands_left_of_the_text_on_a_wide_screen(self):
        tab = with_picture(make_tab())
        screen = draw(tab, width=170, graphics=True)
        lines = rows(screen)
        x, y, cols, rows_, _, _ = screen._placements[tab._picture.id][1]
        photo_title = row_with(lines, "300 × 200")
        assert photo_title.index("Photo") < x < photo_title.index("Charlotte and Frances")
        assert cols + 4 <= 165 * 50 // 100          # the card keeps within half the width
        assert any("wooden jetty" in l for l in lines) and "Charlotte, Frances" in row_with(lines, "Persons")

    def test_the_picture_sits_over_the_text_on_a_narrow_screen(self):
        tab = with_picture(make_tab())
        screen = draw(tab, width=110, graphics=True)
        lines = rows(screen)
        _, y, _, rows_, _, _ = screen._placements[tab._picture.id][1]
        assert y + rows_ <= lines.index(row_with(lines, "Charlotte and Frances on the jetty"))

    def test_a_tall_picture_is_fitted_to_the_card_rather_than_stretched(self):
        tab = with_picture(make_tab(), width=200, height=300)
        screen = draw(tab, width=170, graphics=True)
        _, _, cols, rows_, _, _ = screen._placements[tab._picture.id][1]
        assert cols < rows_ * 2                      # taller than wide in cells too

    def test_without_graphics_the_picture_is_drawn_into_the_cells(self):
        tab = with_picture(make_tab())
        screen = draw(tab, width=170)
        assert any(c.style.bg is not None and c.style.bg.r > 150 for line in screen.next_frame for c in line)

    def test_the_card_says_why_there_is_no_picture(self):
        tab = make_tab()
        tab._picture_guid, tab._picture_state = tab._photo["asset_guid"], "loading"
        assert any("Loading" in l for l in rows(draw(tab, width=170)))
        tab._picture_state = "video"
        assert any("no preview" in l for l in rows(draw(tab, width=170)))

    def test_a_video_asks_for_no_picture(self):
        tab = make_tab(photo=dict(PHOTO, photo_type="video", asset_guid="0A1B2C3D-FAKE-GUID-0002"))
        tab._request_picture(tab._photo)
        assert tab._picture is None and tab._picture_state == "video" and not tab._picture_pending

    def test_a_picture_arriving_after_the_browsing_moved_on_is_dropped(self, monkeypatch):
        import threading
        from interfaces.console.tabs import PhotosTab as module
        started = []
        monkeypatch.setattr(threading, "Thread", lambda target, daemon: started.append(target) or type("T", (), {"start": lambda self: None})())
        monkeypatch.setattr(module.PhotoProcessor, "image_bytes_for_asset", staticmethod(lambda guid: jpeg_bytes()))
        tab = make_tab()
        tab._request_picture(tab._photo)
        assert tab._picture_state == "loading" and tab._picture_pending == {PHOTO["asset_guid"]}
        tab._request_picture(dict(PHOTO, asset_guid="0A1B2C3D-FAKE-GUID-0002"))
        started[0]()                                 # the first fetch finishes now
        assert tab._picture is None and tab._picture_pending == {"0A1B2C3D-FAKE-GUID-0002"}
        started[1]()
        assert tab._picture is not None and tab._picture_guid == "0A1B2C3D-FAKE-GUID-0002"


def jpeg_bytes() -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (30, 20), (200, 40, 40)).save(out, format="JPEG")
    return out.getvalue()
