"""ChartImage: the Dashboard's plots as pixels, and ChartPicture, one chart's picture kept across frames."""
import numpy as np
import pytest

from interfaces.console import Theme
from interfaces.console.framework.Pictures import CELL_PX
from interfaces.console.framework.TUIScreen import Color
from interfaces.console.widgets import ChartImage
from interfaces.console.widgets.ChartImage import Bar, ChartPicture, Segment

A = Color(200, 100, 50)
B = Color(40, 120, 230)
RULE = Color(90, 200, 90)
GROUND = (Theme.COLOR_PANEL_BG.r, Theme.COLOR_PANEL_BG.g, Theme.COLOR_PANEL_BG.b)

ROWS = 4
CELL_W, CELL_H = CELL_PX
HEIGHT = ROWS * CELL_H
ZERO_Y = HEIGHT - CELL_H / 2            # full scale runs from the bottom row's centre to the top row's
PLOT_H = HEIGHT - CELL_H


def y_at(fraction: float) -> int:
    """The pixel row a value at `fraction` of full scale lands on."""
    return int(ZERO_Y - fraction * PLOT_H)


def centre_x(cell: int) -> int:
    return cell * CELL_W + CELL_W // 2


def px(image, x, y):
    return tuple(int(c) for c in np.asarray(image)[y, x])


def distance(rgb, color: Color) -> float:
    return float(np.linalg.norm(np.subtract(rgb, (color.r, color.g, color.b))))


@pytest.mark.unit
class TestColumns:
    def test_a_column_rises_to_its_value_and_no_further(self):
        image = ChartImage.columns([50.0], [A], 100.0, 2, ROWS, glow=False)
        assert px(image, centre_x(1), y_at(0.25)) != GROUND
        assert px(image, centre_x(1), y_at(0.75)) == GROUND

    def test_the_newest_value_is_the_rightmost_column_and_the_left_stays_empty(self):
        image = ChartImage.columns([100.0], [A], 100.0, 3, ROWS, glow=False)
        assert px(image, centre_x(2), y_at(0.5)) != GROUND
        assert all(px(image, centre_x(cell), y_at(0.5)) == GROUND for cell in (0, 1))

    def test_more_values_than_cells_keep_the_newest(self):
        image = ChartImage.columns([100.0, 0.0, 0.0], [A] * 3, 100.0, 2, ROWS, glow=False)
        assert {px(image, x, y_at(0.5)) for x in range(2 * CELL_W)} == {GROUND}

    def test_columns_have_air_between_them(self):
        image = ChartImage.columns([100.0, 100.0], [A, A], 100.0, 2, ROWS, glow=False)
        assert px(image, CELL_W, y_at(0.5)) == GROUND

    def test_a_column_shades_up_to_its_own_colour_at_the_top(self):
        image = ChartImage.columns([100.0], [A], 100.0, 1, ROWS, glow=False)
        top, bottom = px(image, centre_x(0), y_at(0.97)), px(image, centre_x(0), y_at(0.03))
        assert distance(top, A) < 8
        assert distance(bottom, A) > distance(top, A) + 40

    def test_each_value_takes_its_own_colour(self):
        image = ChartImage.columns([100.0, 100.0], [A, B], 100.0, 2, ROWS, glow=False)
        left, right = px(image, centre_x(0), y_at(0.95)), px(image, centre_x(1), y_at(0.95))
        assert distance(left, A) < distance(left, B)
        assert distance(right, B) < distance(right, A)

    def test_a_rule_is_dashed_across_the_empty_plot(self):
        image = ChartImage.columns([], [], 100.0, 2, ROWS, rules=[(1.0, RULE)], glow=False)
        row = [px(image, x, y_at(1.0)) for x in range(2 * CELL_W)]
        assert (RULE.r, RULE.g, RULE.b) in row and GROUND in row

    def test_a_column_covers_the_rule_behind_it(self):
        image = ChartImage.columns([100.0], [A], 100.0, 1, ROWS, rules=[(0.5, RULE)], glow=False)
        assert px(image, centre_x(0), y_at(0.5)) != (RULE.r, RULE.g, RULE.b)

    def test_the_newest_column_glows_on_the_ground_beside_it(self):
        beside = (CELL_W + ChartImage.BAR_PAD_PX - 2, y_at(0.5))
        lit = ChartImage.columns([100.0], [A], 100.0, 2, ROWS, glow=True)
        dark = ChartImage.columns([100.0], [A], 100.0, 2, ROWS, glow=False)
        assert px(dark, *beside) == GROUND
        assert distance(px(lit, *beside), A) < distance(GROUND, A)

    def test_the_rounded_top_leaves_its_corners_to_the_ground(self):
        image = ChartImage.columns([100.0], [A], 100.0, 1, ROWS, glow=False)
        top = y_at(1.0)
        assert px(image, ChartImage.BAR_PAD_PX, top) == GROUND
        assert px(image, centre_x(0), top + 1) != GROUND


@pytest.mark.unit
class TestStackedBars:
    def test_segments_stack_bottom_to_top_with_the_ground_between_them(self):
        bar = Bar(0, 1, [Segment(0.0, 0.5, A), Segment(0.5, 1.0, B)])
        image = ChartImage.render([bar], 1, ROWS)
        low, high = px(image, centre_x(0), y_at(0.25)), px(image, centre_x(0), y_at(0.75))
        assert distance(low, A) < distance(low, B)
        assert distance(high, B) < distance(high, A)
        assert px(image, centre_x(0), y_at(0.5)) == GROUND

    def test_a_bar_spans_the_cells_it_is_given(self):
        image = ChartImage.render([Bar(1, 3, [Segment(0.0, 1.0, A)])], 4, ROWS)
        y = y_at(0.5)
        assert px(image, centre_x(0), y) == GROUND and px(image, centre_x(3), y) == GROUND
        assert px(image, centre_x(1), y) != GROUND and px(image, 2 * CELL_W, y) != GROUND


@pytest.mark.unit
class TestChartPicture:
    @pytest.fixture
    def clock(self, monkeypatch):
        now = [1000.0]
        monkeypatch.setattr(ChartImage.time, "monotonic", lambda: now[0])
        return now

    def test_the_same_signature_never_draws_again(self, clock):
        chart, draws = ChartPicture("test"), []
        draw = lambda: draws.append(1) or ChartImage.render([], 2, 2)
        first = chart.update(2, 2, ("a",), draw)
        clock[0] += 10
        assert chart.update(2, 2, ("a",), draw) is first
        assert len(draws) == 1 and first.revision == 0

    def test_a_change_draws_again_once_the_refresh_is_due_under_the_same_id(self, clock):
        chart, draws = ChartPicture("test"), []
        draw = lambda: draws.append(1) or ChartImage.render([], 2, 2)
        picture = chart.update(2, 2, ("a",), draw)
        chart.update(2, 2, ("b",), draw)
        assert len(draws) == 1
        clock[0] += ChartPicture.REFRESH_SECONDS
        again = chart.update(2, 2, ("b",), draw)
        assert len(draws) == 2 and again.id == picture.id and again.revision == 1

    def test_a_new_box_draws_at_once(self, clock):
        chart, draws = ChartPicture("test"), []
        draw = lambda: draws.append(1) or ChartImage.render([], 2, 2)
        chart.update(2, 2, ("a",), draw)
        chart.update(3, 2, ("a",), draw)
        assert len(draws) == 2
