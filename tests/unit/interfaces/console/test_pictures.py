"""Pictures on the cell screen: fitting a picture to a cell box, the kitty graphics protocol
sequences, and the screen's image layer — sent once, placed when new or moved, taken down when
gone — against the half-block fallback drawn into the cells themselves."""
import io

import pytest
from PIL import Image

from interfaces.console.framework import Pictures
from interfaces.console.framework.Pictures import APC, CELL_ASPECT, ST, Picture
from interfaces.console.framework.Pixels import UPPER_HALF
from interfaces.console.framework.TUIScreen import TUIScreen


def jpeg(width: int, height: int, color=(200, 40, 40)) -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (width, height), color).save(out, format="JPEG")
    return out.getvalue()


def two_tone(width: int, height: int) -> Picture:
    """Red over blue: the top half of the picture one colour, the bottom half another."""
    image = Image.new("RGB", (width, height), (255, 0, 0))
    image.paste((0, 0, 255), (0, height // 2, width, height))
    return Picture(image)


def apcs(frame: bytes) -> list[str]:
    """The graphics commands in a frame, keys only."""
    text = frame.decode()
    out = []
    start = text.find(APC)
    while start >= 0:
        end = text.index(ST, start)
        out.append(text[start + len(APC):end].split(";")[0])
        start = text.find(APC, end)
    return out


@pytest.fixture
def screen():
    s = TUIScreen(80, 24)
    s.graphics = True
    s.clear_buffer()
    s.get_frame(False)
    return s


class TestPicture:
    def test_decodes_jpeg_and_knows_its_size(self):
        picture = Picture.decode(jpeg(300, 200))
        assert (picture.width, picture.height) == (300, 200)
        assert picture.size_bytes > 0

    def test_bytes_that_are_not_an_image_decode_to_none(self):
        assert Picture.decode(b"not an image") is None
        assert Picture.decode(b"") is None

    def test_exif_orientation_is_applied(self):
        image = Image.new("RGB", (300, 200))
        exif = image.getexif()
        exif[0x0112] = 6   # rotated 90° clockwise
        out = io.BytesIO()
        image.save(out, format="JPEG", exif=exif)
        picture = Picture.decode(out.getvalue())
        assert (picture.width, picture.height) == (200, 300)

    def test_fit_keeps_the_proportions_with_cells_half_as_wide_as_tall(self):
        picture = Picture.decode(jpeg(300, 200))
        cols, rows = picture.fit(100, 12)
        assert rows == 12
        assert cols == round(12 * 1.5 / CELL_ASPECT)

    def test_fit_is_bounded_by_the_columns_too(self):
        picture = Picture.decode(jpeg(300, 200))
        cols, rows = picture.fit(18, 12)
        assert cols == 18
        assert rows == round(18 * CELL_ASPECT / 1.5)

    def test_fit_never_returns_an_empty_box(self):
        assert Picture.decode(jpeg(3000, 10)).fit(10, 10) == (10, 1)
        assert Picture.decode(jpeg(10, 3000)).fit(10, 10) == (1, 10)

    def test_png_is_sized_for_the_box_and_never_larger_than_the_source(self):
        picture = Picture.decode(jpeg(300, 200))
        data, size = picture.png(10, 4)
        assert size == (10 * Pictures.CELL_PX[0], 4 * Pictures.CELL_PX[1])
        assert Image.open(io.BytesIO(data)).format == "PNG"
        _, full = picture.png(100, 100)
        assert full == (300, 200)

    def test_renderings_are_cached_per_box(self):
        picture = Picture.decode(jpeg(300, 200))
        assert picture.png(10, 4) is picture.png(10, 4)
        assert picture.pixels(10, 4) is picture.pixels(10, 4)

    def test_pixels_is_two_rows_of_pixels_per_cell_row(self):
        assert Picture.decode(jpeg(300, 200)).pixels(10, 4).shape == (8, 10, 3)

    def test_ids_are_unique(self):
        assert Picture.decode(jpeg(2, 2)).id != Picture.decode(jpeg(2, 2)).id


class TestProtocol:
    def test_transmit_chunks_the_png_and_marks_the_last_chunk(self):
        seq = Pictures.transmit(7, bytes(range(256)) * 20)   # 5120 bytes → 6828 base64 chars, two chunks
        commands = seq.split(ST)[:-1]
        assert len(commands) == 2
        assert commands[0].startswith(f"{APC}a=t,t=d,f=100,i=7,q=2,m=1;")
        assert commands[1].startswith(f"{APC}m=0;")
        assert len(commands[0].split(";", 1)[1]) == Pictures.CHUNK

    def test_place_covers_the_box_and_leaves_the_cursor(self):
        assert Pictures.place(7, 30, 10, (480, 320)) == f"{APC}a=p,i=7,c=30,r=10,C=1,q=2{ST}"

    def test_a_band_of_the_rows_is_a_crop_of_the_source(self):
        seq = Pictures.place(7, 30, 10, (480, 320), first_row=2, visible_rows=5)
        assert ",c=30,r=5," in seq
        assert seq.endswith(f",x=0,y=64,w=480,h=160{ST}")

    def test_every_command_asks_the_terminal_to_keep_quiet(self):
        for seq in (Pictures.transmit(1, b"x"), Pictures.place(1, 1, 1, (1, 1)), Pictures.delete_placements(1),
                    Pictures.free(1), Pictures.delete_all()):
            assert "q=2" in seq


class TestScreenImages:
    def test_a_new_picture_is_sent_once_and_placed_at_its_cell(self, screen):
        picture = Picture.decode(jpeg(300, 200))
        screen.clear_buffer()
        screen.image(3, 2, picture, 36, 12)
        frame = screen.get_frame(False)
        commands = apcs(frame)
        assert commands[0].startswith(f"a=t,t=d,f=100,i={picture.id}")
        assert commands[-1] == f"a=p,i={picture.id},c=36,r=12,C=1,q=2"
        assert b"\x1b[2;3H" + APC.encode() + b"a=p" in frame
        assert all(c.char == " " for c in screen.next_frame[1][2:38])   # blank cells under the picture

    def test_a_picture_that_stays_costs_nothing(self, screen):
        picture = Picture.decode(jpeg(300, 200))
        screen.clear_buffer()
        screen.image(3, 2, picture, 36, 12)
        screen.get_frame(False)
        screen.clear_buffer()
        screen.image(3, 2, picture, 36, 12)
        assert screen.get_frame(False) == b""

    def test_a_moved_picture_is_taken_down_and_placed_again_without_a_resend(self, screen):
        picture = Picture.decode(jpeg(300, 200))
        screen.clear_buffer()
        screen.image(3, 2, picture, 36, 12)
        screen.get_frame(False)
        screen.clear_buffer()
        screen.image(3, 1, picture, 36, 12, first_row=1, visible_rows=11)
        commands = apcs(screen.get_frame(False))
        assert commands == [f"a=d,d=i,i={picture.id},q=2",
                            f"a=p,i={picture.id},c=36,r=11,C=1,q=2,x=0,y=17,w=300,h=183"]

    def test_a_replaced_picture_is_taken_down_sent_again_under_its_id_and_placed(self, screen):
        picture = Picture.decode(jpeg(300, 200))
        screen.clear_buffer()
        screen.image(3, 2, picture, 36, 12)
        screen.get_frame(False)
        picture.replace(Image.new("RGB", (300, 200), (10, 200, 10)))
        screen.clear_buffer()
        screen.image(3, 2, picture, 36, 12)
        commands = apcs(screen.get_frame(False))
        assert commands[0] == f"a=d,d=i,i={picture.id},q=2"
        assert any(c.startswith(f"a=t,t=d,f=100,i={picture.id}") for c in commands)
        assert commands[-1] == f"a=p,i={picture.id},c=36,r=12,C=1,q=2"
        screen.clear_buffer()
        screen.image(3, 2, picture, 36, 12)
        assert screen.get_frame(False) == b""

    def test_replace_drops_the_renderings_of_the_old_image(self):
        picture = Picture.decode(jpeg(40, 20))
        before = picture.png(4, 2)[0]
        picture.replace(Image.new("RGB", (40, 20), (10, 200, 10)))
        assert picture.png(4, 2)[0] != before

    def test_a_picture_that_went_is_taken_down_but_kept_for_later(self, screen):
        picture = Picture.decode(jpeg(300, 200))
        screen.clear_buffer()
        screen.image(3, 2, picture, 36, 12)
        screen.get_frame(False)
        screen.clear_buffer()
        assert apcs(screen.get_frame(False)) == [f"a=d,d=i,i={picture.id},q=2"]
        screen.clear_buffer()
        screen.image(3, 2, picture, 36, 12)
        assert apcs(screen.get_frame(False)) == [f"a=p,i={picture.id},c=36,r=12,C=1,q=2"]

    def test_a_different_box_resends_the_picture(self, screen):
        picture = Picture.decode(jpeg(300, 200))
        screen.clear_buffer()
        screen.image(3, 2, picture, 36, 12)
        screen.get_frame(False)
        screen.clear_buffer()
        screen.image(3, 2, picture, 18, 6)
        commands = apcs(screen.get_frame(False))
        assert commands[0] == f"a=d,d=i,i={picture.id},q=2"
        assert commands[1].startswith("a=t,")
        assert commands[-1] == f"a=p,i={picture.id},c=18,r=6,C=1,q=2"

    def test_a_fresh_screen_takes_every_picture_down_and_sends_them_again(self, screen):
        picture = Picture.decode(jpeg(300, 200))
        screen.clear_buffer()
        screen.image(3, 2, picture, 36, 12)
        screen.get_frame(False)
        screen.invalidate_front_buffer()
        screen.clear_buffer()
        screen.image(3, 2, picture, 36, 12)
        commands = apcs(screen.get_frame(False))
        assert commands[0] == "a=d,d=A,q=2"
        assert any(c.startswith("a=t,") for c in commands)

    def test_the_oldest_picture_off_screen_is_freed_past_the_limit(self, screen, monkeypatch):
        monkeypatch.setattr(TUIScreen, "MAX_TRANSMITTED", 2)
        pictures = [Picture.decode(jpeg(4, 4)) for _ in range(3)]
        for picture in pictures:
            screen.clear_buffer()
            screen.image(1, 1, picture, 4, 2)
            commands = apcs(screen.get_frame(False))
        assert f"a=d,d=I,i={pictures[0].id},q=2" in commands
        assert pictures[0].id not in screen._transmitted and pictures[1].id in screen._transmitted

    def test_a_picture_hanging_off_the_screen_is_clipped(self, screen):
        picture = Picture.decode(jpeg(300, 200))
        screen.clear_buffer()
        screen.image(70, 20, picture, 36, 12)
        assert screen._placements[picture.id][1] == (70, 20, 11, 12, 0, 5)
        screen.clear_buffer()
        screen.image(90, 2, picture, 36, 12)
        assert screen._placements == {}


class TestHalfBlockFallback:
    def test_without_graphics_the_picture_is_half_block_cells_and_no_command(self):
        screen = TUIScreen(80, 24)
        screen.clear_buffer()
        screen.image(3, 2, two_tone(40, 40), 20, 1)   # one cell row: red pixels over blue ones
        frame = screen.get_frame(False)
        assert APC.encode() not in frame
        cell = screen.next_frame[1][2]
        assert cell.char == UPPER_HALF
        assert cell.style.fg.r > 200 > cell.style.fg.b      # the top pixel is the red half…
        assert cell.style.bg.b > 200 > cell.style.bg.r      # …the bottom one the blue (resampling blends the edge)

    def test_a_band_draws_only_those_rows(self):
        screen = TUIScreen(80, 24)
        screen.clear_buffer()
        screen.image(3, 2, two_tone(40, 40), 20, 4, first_row=2, visible_rows=2)   # the blue half
        assert screen.next_frame[1][2].style.bg.b == 255 and screen.next_frame[2][2].style.bg.b == 255
        assert screen.next_frame[3][2].style.bg is None

    def test_terminal_app_gets_one_averaged_cell_per_row(self):
        screen = TUIScreen(80, 24)
        screen.half_blocks = False
        screen.clear_buffer()
        screen.image(3, 2, two_tone(40, 40), 20, 1)
        cell = screen.next_frame[1][2]
        assert cell.char == " " and cell.style.fg is None
        assert cell.style.bg.r == cell.style.bg.b
