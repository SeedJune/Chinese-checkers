"""Move generation and validation.

Every position here is built by hand -- a handful of pieces dropped onto an
otherwise empty board -- so each test isolates exactly one rule.

Coordinates used repeatedly:
  ORIGIN  (0, 0, 0)      centre of the board
  camp 0  z <= -5        top      (seat 0 starts here, targets camp 3)
  camp 3  z >=  5        bottom   (seat 0's target camp)
  camp 1  x >=  5        upper right (foreign to seat 0)
"""

from __future__ import annotations

import unittest

from chinese_checkers.core import board
from chinese_checkers.core.coords import DIRECTIONS, add, distance, scale
from chinese_checkers.core.game import Game
from chinese_checkers.core.rules import (
    IllegalMove,
    Move,
    RuleSet,
    jump_hops,
    legal_moves,
    reachable,
    step_targets,
    validate,
)

from . import make_state

ORIGIN = (0, 0, 0)
OFF_BOARD = (9, -9, 0)


class StepTests(unittest.TestCase):
    def test_step_reaches_all_six_empty_neighbours(self):
        state = make_state({ORIGIN: 0})
        self.assertEqual(set(step_targets(state, ORIGIN)), set(add(ORIGIN, d) for d in DIRECTIONS))

    def test_occupied_neighbours_are_excluded(self):
        blocked = (1, -1, 0)
        state = make_state({ORIGIN: 0, blocked: 1})
        targets = set(step_targets(state, ORIGIN))
        self.assertNotIn(blocked, targets)
        self.assertEqual(len(targets), 5)

    def test_off_board_neighbours_are_excluded(self):
        """The tip of a camp only has two on-board neighbours."""
        tip = board.CAMP_TIPS[0]
        state = make_state({tip: 0})
        targets = step_targets(state, tip)
        self.assertEqual(set(targets), {(3, 4, -7), (4, 3, -7)})
        for cell in targets:
            self.assertIn(cell, board.BOARD)

    def test_empty_origin_has_no_moves(self):
        state = make_state({ORIGIN: 0})
        self.assertEqual(step_targets(state, (1, -1, 0)), [])
        self.assertEqual(jump_hops(state, (1, -1, 0)), [])
        self.assertEqual(reachable(state, (1, -1, 0)), {})


class JumpGeometryTests(unittest.TestCase):
    def test_jump_lands_two_cells_away_in_a_straight_line(self):
        over = (1, -1, 0)
        state = make_state({ORIGIN: 0, over: 1})
        hops = jump_hops(state, ORIGIN)
        self.assertEqual(hops, [(2, -2, 0)])
        landing = hops[0]
        self.assertEqual(distance(ORIGIN, landing), 2)
        # collinear: landing - origin is exactly twice the direction hopped over.
        self.assertEqual(landing, add(ORIGIN, scale((1, -1, 0), 2)))

    def test_no_jump_across_a_gap(self):
        """Classic rules: the piece hopped over must be *adjacent*.

        An empty cell between the jumper and the piece must not enable a jump
        (that would be the "super jump" variant, which this engine does not
        implement).
        """
        state = make_state({ORIGIN: 0, (2, -2, 0): 1})
        self.assertEqual(jump_hops(state, ORIGIN), [])
        self.assertNotIn((4, -4, 0), reachable(state, ORIGIN))
        # ...but the single step towards the gap is still available.
        self.assertIn((1, -1, 0), step_targets(state, ORIGIN))

    def test_jump_needs_an_empty_landing_cell(self):
        state = make_state({ORIGIN: 0, (1, -1, 0): 1, (2, -2, 0): 1})
        self.assertEqual(jump_hops(state, ORIGIN), [])

    def test_jump_may_not_land_off_the_board(self):
        origin = (3, 2, -5)  # inside camp 0, near the tip
        state = make_state({origin: 0, (4, 2, -6): 1})
        for landing in jump_hops(state, origin):
            self.assertIn(landing, board.BOARD)
        self.assertNotIn((5, 2, -7), jump_hops(state, origin))


class ChainTests(unittest.TestCase):
    """Chains: (0,0,0) -> (2,-2,0) -> (4,-2,-2), turning left mid-flight."""

    def _bent_chain_state(self):
        return make_state({ORIGIN: 0, (1, -1, 0): 1, (3, -2, -1): 1})

    def test_chain_may_change_direction(self):
        state = self._bent_chain_state()
        self.assertEqual(jump_hops(state, ORIGIN), [(2, -2, 0)])
        self.assertEqual(jump_hops(state, ORIGIN, ((2, -2, 0),)), [(4, -2, -2)])

        move = reachable(state, ORIGIN)[(4, -2, -2)]
        self.assertEqual(move.kind, "jump")
        self.assertEqual(move.path, ((2, -2, 0), (4, -2, -2)))
        validate(state, move)  # must not raise

        first = (2, -2, 0)
        self.assertNotEqual(
            tuple(a - b for a, b in zip(first, ORIGIN)),
            tuple(a - b for a, b in zip((4, -2, -2), first)),
        )

    def test_chain_may_not_revisit_a_landing_cell(self):
        # Two pieces in a row: O -> A -> B, and B could hop straight back to A.
        state = make_state({ORIGIN: 0, (1, -1, 0): 1, (3, -3, 0): 1})
        chain = ((2, -2, 0), (4, -4, 0))
        self.assertNotIn((2, -2, 0), jump_hops(state, ORIGIN, chain))

        bad = Move(origin=ORIGIN, path=chain + ((2, -2, 0),), kind="jump")
        with self.assertRaises(IllegalMove):
            validate(state, bad)

    def test_chain_may_not_land_back_on_the_origin(self):
        state = make_state({ORIGIN: 0, (1, -1, 0): 1})
        self.assertEqual(jump_hops(state, ORIGIN, ((2, -2, 0),)), [])

        bad = Move(origin=ORIGIN, path=((2, -2, 0), ORIGIN), kind="jump")
        with self.assertRaises(IllegalMove):
            validate(state, bad)

    def test_landing_on_the_origin_is_refused_only_by_the_no_revisit_rule(self):
        """The origin counts as *empty* while the piece is in the air.

        A/B check on identical geometry: hopping over (1,-1,0) from (2,-2,0)
        into (0,0,0) is a perfectly good jump -- it is rejected in the first
        state purely because (0,0,0) is the mover's own origin and no cell may
        be revisited, not because something is standing there.
        """
        over = (1, -1, 0)
        mid = (2, -2, 0)

        in_flight = make_state({ORIGIN: 0, over: 1})
        self.assertIn(ORIGIN, in_flight.occupancy)  # the mover is still recorded there
        self.assertNotIn(ORIGIN, jump_hops(in_flight, ORIGIN, (mid,)))

        other_piece = make_state({mid: 0, over: 1})
        self.assertIn(ORIGIN, jump_hops(other_piece, mid))

        # The hopped-over cell sits directly beside the vacated origin, which
        # is allowed; on this lattice such a hop always lands on the origin
        # itself, so the revisit rule is the only thing standing in the way.
        self.assertEqual(distance(ORIGIN, over), 1)
        self.assertIn(over, [add(ORIGIN, d) for d in DIRECTIONS])

    def test_chain_is_explored_breadth_first(self):
        state = make_state({ORIGIN: 0, (1, -1, 0): 1, (3, -3, 0): 1})
        found = reachable(state, ORIGIN)
        self.assertEqual(len(found[(2, -2, 0)].path), 1)
        self.assertEqual(len(found[(4, -4, 0)].path), 2)


class MixingStepAndJumpTests(unittest.TestCase):
    def test_step_move_must_have_exactly_one_cell(self):
        state = make_state({ORIGIN: 0, (1, -1, 0): 1, (3, -2, -1): 1})
        bad = Move(origin=ORIGIN, path=((2, -2, 0), (4, -2, -2)), kind="step")
        with self.assertRaises(IllegalMove):
            validate(state, bad)

    def test_jump_move_may_not_start_with_a_single_step(self):
        state = make_state({ORIGIN: 0, (1, -1, 0): 1, (3, -2, -1): 1})
        bad = Move(origin=ORIGIN, path=((0, -1, 1),), kind="jump")
        self.assertEqual(distance(ORIGIN, (0, -1, 1)), 1)
        with self.assertRaises(IllegalMove):
            validate(state, bad)

    def test_reachable_never_mixes_kinds(self):
        state = make_state({ORIGIN: 0, (1, -1, 0): 1, (3, -2, -1): 1})
        for cell, move in reachable(state, ORIGIN).items():
            if move.kind == "step":
                self.assertEqual(len(move.path), 1)
                self.assertEqual(distance(ORIGIN, cell), 1)
            else:
                for previous, landing in zip((ORIGIN,) + move.path, move.path):
                    self.assertEqual(distance(previous, landing), 2)


class HomeLockTests(unittest.TestCase):
    """Seat 0 starts in camp 0 and must fill camp 3."""

    INSIDE = (-2, -3, 5)  # a camp 3 cell with neighbours both in and out of camp 3
    STAYS = (-3, -2, 5)  # neighbour inside camp 3
    LEAVES = (-2, -2, 4)  # neighbour in the central hexagon

    def test_target_camp_cell_may_move_within_the_camp(self):
        state = make_state({self.INSIDE: 0})
        self.assertEqual(board.camp_of(self.INSIDE), 0 + 3)
        self.assertIn(self.STAYS, step_targets(state, self.INSIDE))
        validate(state, Move(origin=self.INSIDE, path=(self.STAYS,), kind="step"))

    def test_target_camp_cell_may_not_leave(self):
        state = make_state({self.INSIDE: 0})
        self.assertNotIn(self.LEAVES, step_targets(state, self.INSIDE))
        for cell in reachable(state, self.INSIDE):
            self.assertEqual(board.camp_of(cell), 3, cell)
        with self.assertRaises(IllegalMove):
            validate(state, Move(origin=self.INSIDE, path=(self.LEAVES,), kind="step"))

    def test_home_lock_off_allows_the_same_move(self):
        state = make_state({self.INSIDE: 0}, rules=RuleSet(home_lock=False))
        self.assertIn(self.LEAVES, step_targets(state, self.INSIDE))
        validate(state, Move(origin=self.INSIDE, path=(self.LEAVES,), kind="step"))

    def test_home_lock_blocks_jumps_out_of_the_target_camp(self):
        origin = (-3, -3, 6)
        over = (-2, -3, 5)
        landing = (-1, -3, 4)  # central hexagon, two cells out
        self.assertEqual(board.camp_of(origin), 3)
        self.assertIsNone(board.camp_of(landing))

        locked = make_state({origin: 0, over: 1})
        self.assertNotIn(landing, jump_hops(locked, origin))

        unlocked = make_state({origin: 0, over: 1}, rules=RuleSet(home_lock=False))
        self.assertIn(landing, jump_hops(unlocked, origin))

    def test_home_lock_does_not_hold_pieces_in_their_start_camp(self):
        origin = (2, 3, -5)  # camp 0 = seat 0's *start* camp, not its target
        state = make_state({origin: 0})
        self.assertEqual(board.camp_of(origin), 0)
        self.assertIn((2, 2, -4), step_targets(state, origin))

    def test_home_lock_only_applies_to_the_owner(self):
        """Camp 3 is seat 0's target but seat 1's is camp 4 -- seat 1 roams free."""
        state = make_state({self.INSIDE: 1}, current=1)
        self.assertIn(self.LEAVES, step_targets(state, self.INSIDE))


class ForeignCampTests(unittest.TestCase):
    """Chain (4,-4,0) -> (6,-4,-2) -> (4,-2,-2): in and back out of camp 1."""

    ORIGIN = (4, -4, 0)
    THROUGH = (6, -4, -2)  # camp 1, foreign to seat 0
    END = (4, -2, -2)  # central hexagon
    PIECES = {(4, -4, 0): 0, (5, -4, -1): 1, (5, -3, -2): 1}

    def test_ending_in_a_foreign_camp_is_rejected(self):
        state = make_state(self.PIECES, rules=RuleSet(no_stop_in_foreign_camp=True))
        self.assertEqual(board.camp_of(self.THROUGH), 1)
        self.assertNotIn(self.THROUGH, reachable(state, self.ORIGIN))
        with self.assertRaises(IllegalMove):
            validate(state, Move(origin=self.ORIGIN, path=(self.THROUGH,), kind="jump"))

    def test_passing_through_a_foreign_camp_is_fine(self):
        state = make_state(self.PIECES, rules=RuleSet(no_stop_in_foreign_camp=True))
        move = reachable(state, self.ORIGIN)[self.END]
        self.assertEqual(move.path, (self.THROUGH, self.END))
        validate(state, move)  # must not raise

    def test_foreign_camp_steps_are_rejected_too(self):
        state = make_state(self.PIECES, rules=RuleSet(no_stop_in_foreign_camp=True))
        self.assertEqual(board.camp_of((4, -5, 1)), 2)
        self.assertNotIn((4, -5, 1), step_targets(state, self.ORIGIN))

    def test_default_rules_allow_stopping_anywhere(self):
        state = make_state(self.PIECES)
        self.assertFalse(state.rules.no_stop_in_foreign_camp)
        found = reachable(state, self.ORIGIN)
        self.assertIn(self.THROUGH, found)
        self.assertIn(self.END, found)
        self.assertIn((4, -5, 1), step_targets(state, self.ORIGIN))

    def test_own_and_target_camps_are_not_foreign(self):
        origin = (3, 3, -6)  # camp 0, seat 0's own camp
        state = make_state({origin: 0}, rules=RuleSet(no_stop_in_foreign_camp=True))
        self.assertTrue(any(board.camp_of(c) == 0 for c in step_targets(state, origin)))


class ReachableAndLegalMovesTests(unittest.TestCase):
    def test_reachable_returns_shortest_paths(self):
        # A bent ladder giving a 1-, 2- and 3-hop destination.
        state = make_state({ORIGIN: 0, (1, -1, 0): 1, (3, -2, -1): 1, (4, -1, -3): 1})
        found = reachable(state, ORIGIN)
        self.assertEqual(len(found[(2, -2, 0)].path), 1)
        self.assertEqual(len(found[(4, -2, -2)].path), 2)
        self.assertEqual(len(found[(4, 0, -4)].path), 3)
        for cell, move in found.items():
            if move.kind == "jump":
                self.assertEqual(move.destination, cell)

    def test_reachable_prefers_a_short_chain_over_a_long_one(self):
        """(4,-2,-2) is 2 hops away; a 3-hop detour must not win."""
        state = make_state(
            {
                ORIGIN: 0,
                (1, -1, 0): 1,
                (3, -2, -1): 1,  # short route: O -> (2,-2,0) -> (4,-2,-2)
                (1, 0, -1): 1,  # detour: O -> (2,0,-2) -> ...
                (3, -1, -2): 1,
            }
        )
        move = reachable(state, ORIGIN)[(4, -2, -2)]
        self.assertEqual(len(move.path), 2)

    def test_every_move_reported_by_reachable_validates(self):
        state = make_state({ORIGIN: 0, (1, -1, 0): 1, (3, -2, -1): 1})
        found = reachable(state, ORIGIN)
        self.assertTrue(found)
        for move in found.values():
            validate(state, move)

    def test_legal_moves_covers_every_movable_piece(self):
        state = Game.new(6).state
        moves = legal_moves(state, 0)
        movable = {cell for cell in state.pieces[0] if reachable(state, cell)}
        self.assertTrue(movable)
        self.assertEqual({m.origin for m in moves}, movable)
        self.assertLessEqual(movable, state.pieces[0])
        self.assertEqual(len(moves), sum(len(reachable(state, c)) for c in state.pieces[0]))

    def test_legal_moves_is_empty_for_a_seat_with_no_pieces(self):
        state = make_state({ORIGIN: 0})
        self.assertEqual(legal_moves(state, 4), [])


class ValidateRejectionTests(unittest.TestCase):
    def setUp(self):
        self.state = make_state({ORIGIN: 0, (1, -1, 0): 1, (-1, 1, 0): 1}, current=0)

    def test_moving_on_the_wrong_turn(self):
        self.state.current = 1
        move = Move(origin=ORIGIN, path=((0, -1, 1),), kind="step")
        with self.assertRaises(IllegalMove):
            validate(self.state, move)

    def test_moving_from_an_empty_origin(self):
        move = Move(origin=(0, 2, -2), path=((0, 1, -1),), kind="step")
        with self.assertRaises(IllegalMove):
            validate(self.state, move)

    def test_destination_off_the_board(self):
        self.assertNotIn(OFF_BOARD, board.BOARD)
        move = Move(origin=ORIGIN, path=(OFF_BOARD,), kind="step")
        with self.assertRaises(IllegalMove):
            validate(self.state, move)

    def test_repeated_cell_in_the_path(self):
        move = Move(origin=ORIGIN, path=((2, -2, 0), (2, -2, 0)), kind="jump")
        with self.assertRaises(IllegalMove):
            validate(self.state, move)

    def test_path_returning_to_the_origin(self):
        move = Move(origin=ORIGIN, path=((2, -2, 0), ORIGIN), kind="jump")
        with self.assertRaises(IllegalMove):
            validate(self.state, move)

    def test_empty_path(self):
        move = Move(origin=ORIGIN, path=(), kind="step")
        with self.assertRaises(IllegalMove):
            validate(self.state, move)

    def test_unknown_move_kind(self):
        move = Move(origin=ORIGIN, path=((0, -1, 1),), kind="teleport")
        with self.assertRaises(IllegalMove):
            validate(self.state, move)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
