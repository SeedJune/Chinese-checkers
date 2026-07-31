"""Geometry invariants of the 121-hole star board.

These are pure statements about the immutable data built at import time in
``chinese_checkers.core.board``; nothing here touches game state.
"""

from __future__ import annotations

import unittest

from chinese_checkers.core.board import (
    BALANCED_COUNTS,
    BOARD,
    BOARD_SORTED,
    CAMP_TIPS,
    CAMPS,
    COLOR_NAMES,
    DEFAULT_COLORS,
    PIECES_PER_CAMP,
    SEATING,
    STAR_OUTLINE,
    camp_of,
    opposite,
)
from chinese_checkers.core.coords import neighbors, to_pixel

EPS = 1e-9


def _centroid(cells) -> tuple[float, float]:
    points = [to_pixel(c) for c in cells]
    return (
        sum(p[0] for p in points) / len(points),
        sum(p[1] for p in points) / len(points),
    )


class BoardShapeTests(unittest.TestCase):
    def test_board_has_121_holes(self):
        self.assertEqual(len(BOARD), 121)
        self.assertEqual(len(BOARD_SORTED), 121)
        self.assertEqual(set(BOARD_SORTED), set(BOARD))

    def test_central_hexagon_has_61_holes(self):
        central = [c for c in BOARD if camp_of(c) is None]
        self.assertEqual(len(central), 61)
        # 61 central + 6 camps of 10 = 121, i.e. the camps tile the rest.
        self.assertEqual(len(central) + 6 * PIECES_PER_CAMP, len(BOARD))

    def test_cube_coordinates_sum_to_zero(self):
        for cell in BOARD:
            self.assertEqual(sum(cell), 0, cell)

    def test_each_camp_has_ten_cells_inside_the_board(self):
        for index, camp in enumerate(CAMPS):
            with self.subTest(camp=index):
                self.assertEqual(len(camp), PIECES_PER_CAMP)
                self.assertTrue(camp <= BOARD)

    def test_camps_are_pairwise_disjoint(self):
        for i in range(len(CAMPS)):
            for j in range(i + 1, len(CAMPS)):
                with self.subTest(pair=(i, j)):
                    self.assertEqual(CAMPS[i] & CAMPS[j], frozenset())

    def test_camp_of_agrees_with_camps(self):
        for index, camp in enumerate(CAMPS):
            for cell in camp:
                self.assertEqual(camp_of(cell), index, cell)

    def test_camp_of_is_none_off_the_board(self):
        self.assertIsNone(camp_of((99, -99, 0)))


class OppositeTests(unittest.TestCase):
    def test_opposite_is_the_camp_three_steps_round(self):
        for i in range(6):
            self.assertEqual(opposite(i), (i + 3) % 6)

    def test_opposite_is_an_involution(self):
        for i in range(6):
            self.assertEqual(opposite(opposite(i)), i)

    def test_a_camp_is_never_its_own_opposite(self):
        for i in range(6):
            self.assertNotEqual(opposite(i), i)


class AdjacencyTests(unittest.TestCase):
    def test_adjacency_is_symmetric(self):
        for cell in BOARD:
            for neighbour in neighbors(cell):
                if neighbour not in BOARD:
                    continue
                self.assertIn(cell, neighbors(neighbour), (cell, neighbour))

    def test_every_cell_has_between_two_and_six_neighbours(self):
        for cell in BOARD:
            degree = sum(1 for n in neighbors(cell) if n in BOARD)
            self.assertGreaterEqual(degree, 2, cell)
            self.assertLessEqual(degree, 6, cell)

    def test_camp_tips_are_the_two_neighbour_corners(self):
        for index, tip in enumerate(CAMP_TIPS):
            degree = sum(1 for n in neighbors(tip) if n in BOARD)
            self.assertEqual(degree, 2, (index, tip))


class SeatingTests(unittest.TestCase):
    def test_every_seating_uses_distinct_camps_in_range(self):
        for count, camps in SEATING.items():
            with self.subTest(players=count):
                self.assertEqual(len(camps), count)
                self.assertEqual(len(set(camps)), count)
                for camp in camps:
                    self.assertIn(camp, range(6))

    def test_balanced_seatings_are_rotationally_symmetric(self):
        """A balanced seating maps onto itself under some rotation of the star.

        NOTE: the stricter "every seated camp's opposite is also seated" holds
        for 2, 4 and 6 players but deliberately NOT for 3 -- ``SEATING[3]`` is
        ``(0, 2, 4)``, the classic alternating ring where each player races
        into an *empty* camp (see the module docstring in ``board.py``).
        Rotational symmetry is the invariant that actually characterises
        ``BALANCED_COUNTS``; ``test_opposite_closure_of_seatings`` below pins
        down the three-player exception explicitly.
        """
        for count in sorted(BALANCED_COUNTS):
            with self.subTest(players=count):
                seated = set(SEATING[count])
                rotations = [r for r in range(1, 6) if {(c + r) % 6 for c in seated} == seated]
                self.assertTrue(rotations, f"{count}-player seating is not symmetric")

    def test_five_player_seating_is_not_balanced(self):
        self.assertNotIn(5, BALANCED_COUNTS)
        seated = set(SEATING[5])
        rotations = [r for r in range(1, 6) if {(c + r) % 6 for c in seated} == seated]
        self.assertEqual(rotations, [])

    def test_opposite_closure_of_seatings(self):
        """Which seatings pair every player with the camp they race into."""
        closed = {
            count
            for count, camps in SEATING.items()
            if all(opposite(c) in camps for c in camps)
        }
        self.assertEqual(closed, {2, 4, 6})
        # Three players sit at alternating corners, so nobody's target camp is
        # occupied at the start -- this is by design, not a bug.
        self.assertTrue(all(opposite(c) not in SEATING[3] for c in SEATING[3]))
        self.assertFalse(all(opposite(c) in SEATING[5] for c in SEATING[5]))

    def test_colour_tables_cover_every_camp(self):
        self.assertEqual(len(DEFAULT_COLORS), 6)
        self.assertEqual(len(COLOR_NAMES), 6)
        self.assertEqual(len(set(DEFAULT_COLORS)), 6)


class PixelLayoutTests(unittest.TestCase):
    def test_camp_zero_sits_above_camp_three(self):
        # y grows downwards, so "above" means a smaller y.
        self.assertLess(_centroid(CAMPS[0])[1], _centroid(CAMPS[3])[1])

    def test_camp_one_sits_right_of_camp_four(self):
        self.assertGreater(_centroid(CAMPS[1])[0], _centroid(CAMPS[4])[0])

    def test_adjacent_cells_are_all_one_unit_apart(self):
        for cell in BOARD:
            px, py = to_pixel(cell)
            for neighbour in neighbors(cell):
                if neighbour not in BOARD:
                    continue
                qx, qy = to_pixel(neighbour)
                dist = ((px - qx) ** 2 + (py - qy) ** 2) ** 0.5
                self.assertAlmostEqual(dist, 1.0, delta=1e-9, msg=(cell, neighbour))

    def test_to_pixel_is_injective_on_the_board(self):
        self.assertEqual(len({to_pixel(c) for c in BOARD}), len(BOARD))


class OutlineTests(unittest.TestCase):
    def test_star_outline_cells_are_on_the_board(self):
        for cell in STAR_OUTLINE:
            self.assertIn(cell, BOARD, cell)

    def test_star_outline_alternates_tips_and_notches(self):
        self.assertEqual(len(STAR_OUTLINE), 12)
        for i in range(0, 12, 2):
            self.assertIn(STAR_OUTLINE[i], CAMP_TIPS)
        for i in range(1, 12, 2):
            self.assertIsNone(camp_of(STAR_OUTLINE[i]))

    def test_each_camp_tip_belongs_to_its_camp(self):
        for index, tip in enumerate(CAMP_TIPS):
            self.assertIn(tip, CAMPS[index], (index, tip))
        self.assertEqual(len(set(CAMP_TIPS)), 6)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
