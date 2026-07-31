"""GameState: setup, the win condition, turn rotation and serialisation."""

from __future__ import annotations

import json
import unittest

from chinese_checkers.core import board
from chinese_checkers.core.game import Game
from chinese_checkers.core.rules import RuleSet
from chinese_checkers.core.state import GameState, MoveRecord, SeatInfo

from . import SEATS, make_state


def _seat_camp_layout(occupied: dict[int, int]) -> dict:
    """occupancy dict placing seat ``v``'s pieces on all of camp ``k``."""
    return {cell: seat for camp, seat in occupied.items() for cell in board.CAMPS[camp]}


class SetupTests(unittest.TestCase):
    def setUp(self):
        self.state = Game.new(6).state

    def test_every_seat_starts_with_ten_pieces_in_its_own_camp(self):
        for seat in self.state.seats:
            with self.subTest(seat=seat.index):
                cells = self.state.pieces[seat.index]
                self.assertEqual(len(cells), board.PIECES_PER_CAMP)
                self.assertEqual(cells, set(board.CAMPS[seat.camps[0]]))

    def test_occupancy_and_pieces_agree(self):
        self.assertEqual(len(self.state.occupancy), 6 * board.PIECES_PER_CAMP)
        for seat, cells in self.state.pieces.items():
            for cell in cells:
                self.assertEqual(self.state.occupancy[cell], seat)

    def test_each_seat_targets_the_opposite_camp(self):
        for seat in self.state.seats:
            self.assertEqual(seat.targets, (board.opposite(seat.camps[0]),))

    def test_nobody_has_finished_at_the_start(self):
        """Subtle: every target camp is *full* at the start -- of enemy pieces.

        ``has_finished`` must also require at least one of the seat's own
        pieces to be in there, otherwise everybody would win on move zero.
        """
        for seat in self.state.seats:
            with self.subTest(seat=seat.index):
                target = seat.targets[0]
                self.assertTrue(all(c in self.state.occupancy for c in board.CAMPS[target]))
                self.assertFalse(self.state.has_finished(seat.index))
        self.assertEqual(self.state.rankings, [])
        self.assertFalse(self.state.is_over)

    def test_three_player_setup_leaves_the_target_camps_empty(self):
        state = Game.new(3).state
        for seat in state.seats:
            self.assertEqual(
                [c for c in board.CAMPS[seat.targets[0]] if c in state.occupancy], []
            )
            self.assertFalse(state.has_finished(seat.index))


class PiecesHomeTests(unittest.TestCase):
    def test_zero_at_the_start(self):
        state = Game.new(6).state
        for seat in state.seats:
            self.assertEqual(state.pieces_home(seat.index), 0)

    def test_counts_only_pieces_inside_the_target_camp(self):
        camp3 = sorted(board.CAMPS[3])
        occupancy = {cell: 0 for cell in camp3[:4]}
        occupancy[(0, 0, 0)] = 0  # centre, not home
        occupancy[(3, 3, -6)] = 0  # seat 0's *start* camp, not home either
        state = make_state(occupancy)
        self.assertEqual(state.pieces_home(0), 4)
        self.assertEqual(len(state.pieces[0]), 6)

    def test_other_seats_pieces_do_not_count(self):
        state = make_state({cell: 3 for cell in board.CAMPS[3]})
        self.assertEqual(state.pieces_home(0), 0)


class TurnRotationTests(unittest.TestCase):
    def setUp(self):
        self.state = Game.new(6).state

    def test_advance_turn_moves_clockwise(self):
        for expected in (1, 2, 3, 4, 5, 0):
            self.state.advance_turn()
            self.assertEqual(self.state.current, expected)

    def test_advance_turn_skips_seats_in_rankings(self):
        self.state.rankings = [1, 2]
        self.state.current = 0
        self.state.advance_turn()
        self.assertEqual(self.state.current, 3)

    def test_advance_turn_wraps_past_finished_seats(self):
        self.state.rankings = [0, 1]
        self.state.current = 5
        self.state.advance_turn()
        self.assertEqual(self.state.current, 2)

    def test_advance_turn_is_a_no_op_once_the_game_is_over(self):
        self.state.rankings = [0, 1, 2, 3, 4]
        self.state.current = 5
        self.state.advance_turn()
        self.assertEqual(self.state.current, 5)


class WinConditionTests(unittest.TestCase):
    """Seat 0 must fill camp 3 ("fill every hole that is available")."""

    CAMP3 = sorted(board.CAMPS[3])
    BLOCKED = CAMP3[0]

    def _position(self, *, blocker: int | None, spare: tuple[int, int, int]) -> GameState:
        occupancy = {cell: 0 for cell in self.CAMP3[1:]}  # nine own pieces
        if blocker is not None:
            occupancy[self.BLOCKED] = blocker
        occupancy[spare] = 0
        return make_state(occupancy)

    def test_win_with_an_opponent_blocking_the_last_hole(self):
        state = self._position(blocker=3, spare=(0, 0, 0))
        self.assertEqual(state.pieces_home(0), 9)
        self.assertEqual(len(state.pieces[0]), 10)
        self.assertTrue(state.has_finished(0))

    def test_an_empty_hole_in_the_target_camp_is_not_a_win(self):
        state = self._position(blocker=None, spare=(0, 0, 0))
        self.assertEqual(state.pieces_home(0), 9)
        self.assertFalse(state.has_finished(0))

    def test_a_full_target_camp_of_enemy_pieces_is_not_a_win(self):
        occupancy = {cell: 3 for cell in self.CAMP3}
        occupancy[(0, 0, 0)] = 0
        state = make_state(occupancy)
        self.assertFalse(state.has_finished(0))

    def test_all_ten_pieces_home_is_a_win(self):
        state = make_state({cell: 0 for cell in self.CAMP3})
        self.assertEqual(state.pieces_home(0), 10)
        self.assertTrue(state.has_finished(0))

    def test_unknown_win_condition_is_rejected(self):
        state = make_state({(0, 0, 0): 0}, rules=RuleSet(win_condition="race"))
        with self.assertRaises(NotImplementedError):
            state.has_finished(0)


class GameOverTests(unittest.TestCase):
    def setUp(self):
        self.state = Game.new(6).state

    def test_is_over_only_when_one_seat_is_left(self):
        for count in range(5):
            self.state.rankings = list(range(count))
            self.assertFalse(self.state.is_over, count)
        self.state.rankings = [0, 1, 2, 3, 4]
        self.assertTrue(self.state.is_over)

    def test_final_rankings_appends_the_last_seat(self):
        self.state.rankings = [4, 2, 0, 5, 1]
        final = self.state.final_rankings
        self.assertEqual(final, [4, 2, 0, 5, 1, 3])
        self.assertEqual(sorted(final), list(range(6)))
        self.assertEqual(len(set(final)), 6)

    def test_final_rankings_is_empty_while_the_game_runs(self):
        self.state.rankings = [4]
        self.assertEqual(self.state.final_rankings, [4])

    def test_two_player_game_is_over_after_one_finisher(self):
        state = Game.new(2).state
        state.rankings = [0]
        self.assertTrue(state.is_over)
        self.assertEqual(state.final_rankings, [0, 1])


class SerialisationTests(unittest.TestCase):
    def _played_game(self) -> Game:
        from chinese_checkers.agents.base import RandomAgent

        game = Game.new(4, rules=RuleSet(home_lock=True, no_stop_in_foreign_camp=True))
        agent = RandomAgent(seed=11)
        for _ in range(20):
            move = agent.select_move(game)
            game.select(move.origin)
            for cell in move.path:
                game.extend(cell)
            game.confirm()
        return game

    def test_round_trip_preserves_the_position(self):
        original = self._played_game().state
        clone = GameState.from_dict(original.to_dict())

        self.assertEqual(clone.occupancy, original.occupancy)
        self.assertEqual(clone.pieces, original.pieces)
        self.assertEqual(clone.current, original.current)
        self.assertEqual(clone.rankings, original.rankings)
        self.assertEqual([r.move for r in clone.history], [r.move for r in original.history])
        self.assertEqual(len(clone.history), 20)

    def test_round_trip_preserves_rules_and_seats(self):
        original = self._played_game().state
        clone = GameState.from_dict(original.to_dict())
        self.assertEqual(clone.rules, original.rules)
        self.assertEqual(clone.seats, original.seats)
        for record, other in zip(clone.history, original.history):
            self.assertEqual(record, other)

    def test_snapshot_is_json_serialisable(self):
        state = self._played_game().state
        reloaded = GameState.from_dict(json.loads(json.dumps(state.to_dict())))
        self.assertEqual(reloaded.occupancy, state.occupancy)
        self.assertEqual(reloaded.pieces, state.pieces)

    def test_cells_survive_as_tuples(self):
        state = self._played_game().state
        clone = GameState.from_dict(json.loads(json.dumps(state.to_dict())))
        for cell in clone.occupancy:
            self.assertIsInstance(cell, tuple)
            self.assertIn(cell, board.BOARD)
        for record in clone.history:
            self.assertIsInstance(record.move.origin, tuple)
            self.assertIsInstance(record.move.path, tuple)


class ApplyUndoTests(unittest.TestCase):
    def test_apply_records_history_and_advances_the_turn(self):
        from chinese_checkers.core.rules import Move

        state = make_state({(0, 0, 0): 0}, seats=SEATS)
        move = Move(origin=(0, 0, 0), path=((1, -1, 0),), kind="step")
        finished = state.apply(move)

        self.assertIsNone(finished)
        self.assertEqual(state.occupancy, {(1, -1, 0): 0})
        self.assertEqual(state.pieces[0], {(1, -1, 0)})
        self.assertEqual(state.current, 1)
        self.assertEqual(state.history, [MoveRecord(seat=0, move=move, finished=False)])

    def test_undo_on_an_empty_history_raises(self):
        state = make_state({(0, 0, 0): 0})
        with self.assertRaises(RuntimeError):
            state.undo()

    def test_seat_info_is_immutable(self):
        seat = SeatInfo(index=0, name="x", color="#000000", camps=(0,), targets=(3,))
        with self.assertRaises(Exception):
            seat.index = 1  # type: ignore[misc]


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
