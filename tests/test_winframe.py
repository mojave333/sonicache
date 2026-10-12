"""How a window without a frame is sized and taken out of maximized; the arithmetic only."""

import pytest

from sonicache import winframe

START = (100, 100, 900, 700)  # left, top, right, bottom: 800 x 600
MINIMUM = (720, 520)


class TestResized:
    @pytest.mark.parametrize("edge, delta, expected", [
        ("e", (50, 20), (100, 100, 950, 700)),
        ("s", (50, 20), (100, 100, 900, 720)),
        ("w", (-30, 20), (70, 100, 900, 700)),
        ("n", (20, -30), (100, 70, 900, 700)),
        ("se", (40, 60), (100, 100, 940, 760)),
        ("nw", (-10, -20), (90, 80, 900, 700)),
        ("ne", (10, -20), (100, 80, 910, 700)),
        ("sw", (-10, 20), (90, 100, 900, 720)),
    ])
    def test_the_dragged_side_follows_the_cursor_and_the_other_stays(self, edge, delta, expected):
        assert winframe.resized(START, delta, edge, MINIMUM) == expected

    def test_the_window_stops_at_its_minimum_width_and_the_far_side_does_not_move(self):
        left, top, right, bottom = winframe.resized(START, (500, 0), "w", MINIMUM)
        assert right == 900 and right - left == 720

    def test_the_window_stops_at_its_minimum_height_from_above_or_below(self):
        assert winframe.resized(START, (0, 900), "n", MINIMUM)[1:] == (180, 900, 700)
        assert winframe.resized(START, (0, -900), "s", MINIMUM)[1:] == (100, 900, 620)

    def test_a_corner_is_kept_to_the_minimum_on_both_axes(self):
        assert winframe.resized(START, (-900, -900), "se", MINIMUM) == (100, 100, 820, 620)

    def test_nothing_moves_when_the_cursor_has_not(self):
        assert winframe.resized(START, (0, 0), "se", MINIMUM) == START


class TestRestoredCorner:
    SCREEN = (0, 0, 1920, 1040)

    def test_a_window_taken_by_its_middle_stays_under_the_cursor_by_its_middle(self):
        left, top = winframe.restored_corner(self.SCREEN, (960, 10), (1000, 700))
        assert left == 460 and top == 0

    def test_taken_by_its_left_end_it_keeps_the_left_end(self):
        assert winframe.restored_corner(self.SCREEN, (0, 10), (1000, 700))[0] == 0

    def test_taken_by_its_right_end_it_keeps_the_right_end(self):
        left, _ = winframe.restored_corner(self.SCREEN, (1920, 10), (1000, 700))
        assert left == 920

    def test_it_is_held_a_little_below_its_upper_edge_however_low_the_press(self):
        assert winframe.restored_corner(self.SCREEN, (960, 400), (1000, 700))[1] == 400 - winframe.GRAB_HEIGHT

    def test_a_screen_that_does_not_begin_at_zero_is_allowed_for(self):
        screen = (-1920, 0, 0, 1080)  # a second monitor to the left
        assert winframe.restored_corner(screen, (-960, 10), (1000, 700))[0] == -1460


class TestCentred:
    WORK = (0, 0, 1920, 1020)

    def test_a_window_that_fits_is_set_in_the_middle(self):
        assert winframe.centred(self.WORK, (1450, 1000)) == (235, 10, 1685, 1010)

    def test_one_that_does_not_is_cut_to_the_area_and_fills_it(self):
        assert winframe.centred(self.WORK, (2400, 1400)) == self.WORK

    def test_an_area_that_does_not_begin_at_zero_is_allowed_for(self):
        assert winframe.centred((0, 40, 1920, 1080), (1000, 600)) == (460, 260, 1460, 860)


def test_only_known_edges_are_drawn():
    assert set(winframe.EDGES) == {"n", "s", "e", "w", "ne", "nw", "se", "sw"}
