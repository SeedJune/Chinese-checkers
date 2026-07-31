"""The Game facade: the move-builder state machine, undo and turn rotation."""

from __future__ import annotations

import unittest

from chinese_checkers.agents.base import RandomAgent
from chinese_checkers.core import board
from chinese_checkers.core.coords import neighbors
from chinese_checkers.core.game import Game, Phase
from chinese_checkers.core.rules import IllegalMove, RuleSet

from . import make_game

ORIGIN = (0, 0, 0)

# A two-hop chain that bends: (0,0,0) -> (2,-2,0) -> (4,-2,-2).
CHAIN_PIECES = {ORIGIN: 0, (1, -1, 0): 1, (3, -2, -1): 1}
FIRST_LANDING = (2, -2, 0)
SECOND_LANDING = (4, -2, -2)


def play(game: Game, move) -> None:
    """Drive the builder through a whole ``Move`` and confirm it."""
    game.select(move.origin)
    for cell in move.path:
        game.extend(cell)
    game.confirm()


#: The one hole of camp 3 left open in :func:`almost_finished_position`, and
#: the seat-0 piece standing right next to it.
LAST_HOLE = (-4, -1, 5)  # camp 3's entrance hole
DOORSTEP = (-4, 0, 4)  # central hexagon, adjacent to LAST_HOLE


def almost_finished_position() -> Game:
    """Six seats; seat 0 is one single step away from filling camp 3.

    Seat 0 holds nine of camp 3's ten holes plus one piece on ``DOORSTEP``,
    which is adjacent to ``LAST_HOLE``.  Seat 3's own camp is occupied, so its
    pieces sit in camp 0 (minus one hole, so seat 3 has *not* finished).  Seats
    1, 2, 4 and 5 stay in their own camps.
    """
    occupancy = {cell: 0 for cell in board.CAMPS[3] if cell != LAST_HOLE}
    occupancy[DOORSTEP] = 0
    for cell in sorted(board.CAMPS[0])[:-1]:
        occupancy[cell] = 3
    occupancy[ORIGIN] = 3
    for camp in (1, 2, 4, 5):
        for cell in board.CAMPS[camp]:
            occupancy[cell] = camp
    return make_game(occupancy, current=0)


class PhaseTransitionTests(unittest.TestCase):
    def setUp(self):
        self.game = make_game(CHAIN_PIECES)

    def test_idle_select_chain_chain_confirm(self):
        self.assertIs(self.game.phase, Phase.IDLE)
        self.assertEqual(self.game.path, ())
        self.assertFalse(self.game.can_confirm)

        self.game.select(ORIGIN)
        self.assertIs(self.game.phase, Phase.SELECTED)
        self.assertEqual(self.game.path, (ORIGIN,))
        self.assertFalse(self.game.can_confirm)
        self.assertEqual(self.game.options()["jumps"], frozenset({FIRST_LANDING}))

        self.game.extend(FIRST_LANDING)
        self.assertIs(self.game.phase, Phase.CHAINING)
        self.assertEqual(self.game.path, (ORIGIN, FIRST_LANDING))
        self.assertTrue(self.game.can_confirm)
        self.assertEqual(self.game.options()["steps"], frozenset())
        self.assertEqual(self.game.options()["jumps"], frozenset({SECOND_LANDING}))

        self.game.extend(SECOND_LANDING)
        self.assertIs(self.game.phase, Phase.CHAINING)
        self.assertEqual(self.game.path, (ORIGIN, FIRST_LANDING, SECOND_LANDING))

        result = self.game.confirm()
        self.assertIs(self.game.phase, Phase.IDLE)
        self.assertEqual(self.game.path, ())
        self.assertEqual(result.move.kind, "jump")
        self.assertEqual(result.move.path, (FIRST_LANDING, SECOND_LANDING))

    def test_selected_offers_steps_and_jumps_separately(self):
        self.game.select(ORIGIN)
        options = self.game.options()
        self.assertEqual(options["jumps"], frozenset({FIRST_LANDING}))
        self.assertIn((0, -1, 1), options["steps"])
        self.assertEqual(options["steps"] & options["jumps"], frozenset())

    def test_extend_onto_a_step_target_ends_the_build(self):
        self.game.select(ORIGIN)
        self.game.extend((0, -1, 1))
        self.assertIs(self.game.phase, Phase.STEPPED)
        self.assertEqual(self.game.path, (ORIGIN, (0, -1, 1)))
        self.assertTrue(self.game.can_confirm)
        self.assertEqual(self.game.options(), {"steps": frozenset(), "jumps": frozenset()})

    def test_options_are_empty_while_idle(self):
        self.assertEqual(self.game.options(), {"steps": frozenset(), "jumps": frozenset()})

    def test_rollback_from_one_landing_returns_to_selected(self):
        self.game.select(ORIGIN)
        self.game.extend(FIRST_LANDING)
        self.game.rollback()
        self.assertIs(self.game.phase, Phase.SELECTED)
        self.assertEqual(self.game.path, (ORIGIN,))
        self.assertEqual(self.game.options()["jumps"], frozenset({FIRST_LANDING}))

    def test_rollback_from_two_landings_stays_chaining(self):
        self.game.select(ORIGIN)
        self.game.extend(FIRST_LANDING)
        self.game.extend(SECOND_LANDING)
        self.game.rollback()
        self.assertIs(self.game.phase, Phase.CHAINING)
        self.assertEqual(self.game.path, (ORIGIN, FIRST_LANDING))

    def test_rollback_from_a_step_returns_to_selected(self):
        self.game.select(ORIGIN)
        self.game.extend((0, -1, 1))
        self.game.rollback()
        self.assertIs(self.game.phase, Phase.SELECTED)

    def test_rollback_while_selected_raises(self):
        self.game.select(ORIGIN)
        with self.assertRaises(IllegalMove):
            self.game.rollback()
        self.assertIs(self.game.phase, Phase.SELECTED)

    def test_cancel_returns_to_idle_and_clears_the_path(self):
        self.game.select(ORIGIN)
        self.game.extend(FIRST_LANDING)
        self.game.cancel()
        self.assertIs(self.game.phase, Phase.IDLE)
        self.assertEqual(self.game.path, ())
        self.assertIn(ORIGIN, self.game.state.occupancy)  # nothing was applied

    def test_cancel_while_idle_is_harmless(self):
        self.game.cancel()
        self.assertIs(self.game.phase, Phase.IDLE)


class SelectRejectionTests(unittest.TestCase):
    def setUp(self):
        self.game = make_game(CHAIN_PIECES)

    def test_selecting_an_opponent_piece_raises(self):
        with self.assertRaises(IllegalMove):
            self.game.select((1, -1, 0))
        self.assertIs(self.game.phase, Phase.IDLE)
        self.assertEqual(self.game.path, ())

    def test_selecting_an_empty_cell_raises(self):
        with self.assertRaises(IllegalMove):
            self.game.select((0, 2, -2))
        self.assertIs(self.game.phase, Phase.IDLE)

    def test_selecting_a_piece_with_no_legal_moves_raises(self):
        # Box the piece in: every neighbour and every landing beyond is taken.
        occupancy = {ORIGIN: 0}
        for neighbour in neighbors(ORIGIN):
            occupancy[neighbour] = 1
            occupancy[tuple(2 * c for c in neighbour)] = 1
        game = make_game(occupancy)
        self.assertEqual(game.selectable(), frozenset())
        with self.assertRaises(IllegalMove):
            game.select(ORIGIN)
        self.assertIs(game.phase, Phase.IDLE)

    def test_selecting_twice_raises(self):
        self.game.select(ORIGIN)
        with self.assertRaises(IllegalMove):
            self.game.select(ORIGIN)
        self.assertIs(self.game.phase, Phase.SELECTED)

    def test_selectable_lists_only_movable_pieces_of_the_current_seat(self):
        game = Game.new(6)
        self.assertTrue(game.selectable() <= game.state.pieces[0])
        self.assertTrue(game.selectable())


class ExtendAndConfirmTests(unittest.TestCase):
    def setUp(self):
        self.game = make_game(CHAIN_PIECES)

    def test_extend_to_an_illegal_cell_leaves_the_path_unchanged(self):
        self.game.select(ORIGIN)
        before = self.game.path
        with self.assertRaises(IllegalMove):
            self.game.extend((4, -4, 0))
        self.assertEqual(self.game.path, before)
        self.assertIs(self.game.phase, Phase.SELECTED)

    def test_extend_cannot_continue_past_a_step(self):
        self.game.select(ORIGIN)
        self.game.extend((0, -1, 1))
        with self.assertRaises(IllegalMove):
            self.game.extend((0, -2, 2))
        self.assertEqual(self.game.path, (ORIGIN, (0, -1, 1)))
        self.assertIs(self.game.phase, Phase.STEPPED)

    def test_extend_while_idle_raises(self):
        with self.assertRaises(IllegalMove):
            self.game.extend(FIRST_LANDING)
        self.assertIs(self.game.phase, Phase.IDLE)

    def test_confirm_while_idle_raises(self):
        with self.assertRaises(IllegalMove):
            self.game.confirm()
        self.assertIs(self.game.phase, Phase.IDLE)

    def test_confirm_while_only_selected_raises(self):
        self.game.select(ORIGIN)
        with self.assertRaises(IllegalMove):
            self.game.confirm()
        self.assertIs(self.game.phase, Phase.SELECTED)

    def test_confirm_moves_the_piece_and_advances_the_turn(self):
        self.game.select(ORIGIN)
        self.game.extend(FIRST_LANDING)
        self.game.extend(SECOND_LANDING)
        result = self.game.confirm()

        state = self.game.state
        self.assertNotIn(ORIGIN, state.occupancy)
        self.assertEqual(state.occupancy[SECOND_LANDING], 0)
        self.assertEqual(state.pieces[0], {SECOND_LANDING})
        self.assertEqual(state.current, 1)
        self.assertIsNone(result.finished)
        self.assertFalse(result.game_over)
        self.assertTrue(self.game.can_undo)

    def test_confirm_leaves_intermediate_cells_untouched(self):
        self.game.select(ORIGIN)
        self.game.extend(FIRST_LANDING)
        self.game.extend(SECOND_LANDING)
        self.game.confirm()
        self.assertNotIn(FIRST_LANDING, self.game.state.occupancy)
        self.assertEqual(self.game.state.occupancy[(1, -1, 0)], 1)


class UndoTests(unittest.TestCase):
    def test_undo_restores_the_opening_position_after_a_random_game(self):
        """The invariant the whole search/RL stack will lean on."""
        game = Game.new(6)
        agent = RandomAgent(seed=20240731)
        for _ in range(120):
            play(game, agent.select_move(game))
        self.assertEqual(len(game.state.history), 120)

        fresh = Game.new(6)
        self.assertNotEqual(game.state.occupancy, fresh.state.occupancy)

        while game.can_undo:
            game.undo_last_move()

        self.assertEqual(game.state.occupancy, fresh.state.occupancy)
        self.assertEqual(game.state.pieces, fresh.state.pieces)
        self.assertEqual(game.state.current, fresh.state.current)
        self.assertEqual(game.state.rankings, fresh.state.rankings)
        self.assertEqual(game.state.history, fresh.state.history)
        self.assertIs(game.phase, Phase.IDLE)
        self.assertFalse(game.can_undo)

    def test_undo_step_by_step_matches_replay(self):
        game = Game.new(4)
        agent = RandomAgent(seed=7)
        snapshots = [dict(game.state.occupancy)]
        for _ in range(25):
            play(game, agent.select_move(game))
            snapshots.append(dict(game.state.occupancy))
        for expected in reversed(snapshots[:-1]):
            game.undo_last_move()
            self.assertEqual(game.state.occupancy, expected)

    def test_undo_cancels_an_in_progress_build(self):
        game = Game.new(6)
        agent = RandomAgent(seed=3)
        play(game, agent.select_move(game))

        # Seat 1 starts building a move, then someone hits "undo".
        pending = agent.select_move(game)
        game.select(pending.origin)
        game.extend(pending.path[0])

        game.undo_last_move()
        self.assertIs(game.phase, Phase.IDLE)
        self.assertEqual(game.path, ())
        self.assertEqual(game.state.occupancy, Game.new(6).state.occupancy)
        self.assertEqual(game.state.current, 0)

    def test_undo_with_no_history_raises(self):
        game = Game.new(2)
        with self.assertRaises(RuntimeError):
            game.undo_last_move()


class FinishAndRotationTests(unittest.TestCase):
    def test_the_finishing_move_is_reported_and_ranked(self):
        game = almost_finished_position()

        self.assertFalse(game.state.has_finished(0))
        game.select(DOORSTEP)
        game.extend(LAST_HOLE)
        result = game.confirm()

        self.assertEqual(result.finished, 0)
        self.assertFalse(result.game_over)
        self.assertEqual(game.state.rankings, [0])
        self.assertEqual(game.state.pieces_home(0), 10)
        self.assertEqual(game.state.current, 1)

    def test_undo_takes_back_a_finish(self):
        game = almost_finished_position()
        game.select(DOORSTEP)
        game.extend(LAST_HOLE)
        game.confirm()

        game.undo_last_move()

        self.assertEqual(game.state.rankings, [])
        self.assertNotIn(0, game.state.rankings)
        self.assertEqual(game.state.current, 0)
        self.assertFalse(game.state.has_finished(0))
        self.assertEqual(game.state.occupancy[DOORSTEP], 0)
        self.assertNotIn(LAST_HOLE, game.state.occupancy)
        self.assertEqual(game.state.history, [])

    def test_turn_rotation_skips_a_finished_seat(self):
        game = almost_finished_position()
        game.select(DOORSTEP)
        game.extend(LAST_HOLE)
        game.confirm()

        agent = RandomAgent(seed=99)
        movers = []
        for _ in range(15):
            movers.append(game.state.current)
            play(game, agent.select_move(game))

        self.assertEqual(movers, [1, 2, 3, 4, 5] * 3)
        self.assertNotIn(0, movers)
        self.assertEqual(game.state.rankings, [0])


class SetupTests(unittest.TestCase):
    def test_supported_player_counts(self):
        for count in sorted(board.SEATING):
            with self.subTest(players=count):
                game = Game.new(count)
                self.assertEqual(len(game.state.seats), count)
                self.assertEqual(len(game.state.occupancy), count * board.PIECES_PER_CAMP)

    def test_unsupported_player_count_raises(self):
        with self.assertRaises(ValueError):
            Game.new(7)

    def test_mismatched_names_raise(self):
        with self.assertRaises(ValueError):
            Game.new(3, names=["a", "b"])

    def test_current_seat_follows_the_state(self):
        game = Game.new(6)
        self.assertIs(game.current_seat, game.state.seats[0])
        self.assertFalse(game.is_over)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()


class ConfirmGatingTests(unittest.TestCase):
    """`can_confirm` must agree with `validate`, not merely with "a path exists".

    With ``no_stop_in_foreign_camp`` on, a chain may hop *through* another
    player's camp but not stop in it, so the confirm button has to stay
    disabled on a landing cell that validation would reject.
    """

    ORIGIN = (3, 0, -3)
    OVER = (4, -1, -3)
    LANDING = (5, -2, -3)  # inside camp 1, which is foreign to seat 0

    def _game(self, *, no_stop: bool):
        return make_game(
            {self.ORIGIN: 0, self.OVER: 1},
            rules=RuleSet(no_stop_in_foreign_camp=no_stop),
        )

    def test_landing_in_a_foreign_camp_cannot_be_confirmed(self):
        game = self._game(no_stop=True)
        game.select(self.ORIGIN)
        game.extend(self.LANDING)  # offered as a hop: passing through is legal
        self.assertEqual(game.phase, Phase.CHAINING)
        self.assertFalse(game.can_confirm)
        with self.assertRaises(IllegalMove):
            game.confirm()

    def test_the_same_landing_is_confirmable_under_default_rules(self):
        game = self._game(no_stop=False)
        game.select(self.ORIGIN)
        game.extend(self.LANDING)
        self.assertTrue(game.can_confirm)
        game.confirm()
        self.assertEqual(game.state.occupancy[self.LANDING], 0)
