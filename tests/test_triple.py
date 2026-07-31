"""Method 2: two players, three colours each.

The engine distinguishes a *seat* (one colour, ten pieces, one target camp)
from a *player* (the person whose turn it is, owning one or more seats).  In
the ordinary game the two coincide, so these tests exist to pin down the cases
where they must not: turn order, ownership, home-lock and winning.
"""

from __future__ import annotations

import random
import unittest

from chinese_checkers.core import board
from chinese_checkers.core.coords import distance
from chinese_checkers.core.game import Game
from chinese_checkers.core.rules import IllegalMove, player_moves


class SetupTests(unittest.TestCase):
    def setUp(self) -> None:
        self.game = Game.new(2, colors_each=3)
        self.state = self.game.state

    def test_two_players_own_three_colours_each(self):
        self.assertEqual(len(self.state.players), 2)
        self.assertEqual(len(self.state.seats), 6)
        for player in self.state.players:
            self.assertEqual(len(player.seats), 3)

    def test_thirty_pieces_each(self):
        for player in self.state.players:
            self.assertEqual(self.state.pieces_total(player.index), 30)
            self.assertEqual(self.state.pieces_home(player.index), 0)

    def test_every_colour_faces_an_opponent_colour(self):
        """The point of the arrangement: each of my colours races into a camp
        the opponent starts in, so the two sides run straight at each other."""
        camps = [
            {self.state.seats[s].camps[0] for s in player.seats}
            for player in self.state.players
        ]
        self.assertEqual(camps[0] | camps[1], set(range(6)))
        for camp in camps[0]:
            self.assertIn(board.opposite(camp), camps[1])

    def test_each_colour_targets_its_own_opposite_camp(self):
        for seat in self.state.seats:
            self.assertEqual(seat.targets, (board.opposite(seat.camps[0]),))

    def test_nobody_has_finished_at_the_start(self):
        for player in self.state.players:
            self.assertFalse(self.state.has_finished(player.index))

    def test_three_colours_are_rejected_for_other_player_counts(self):
        for count in (3, 4, 5, 6):
            with self.assertRaises(ValueError):
                Game.new(count, colors_each=3)


class OwnershipTests(unittest.TestCase):
    def setUp(self) -> None:
        self.game = Game.new(2, colors_each=3)
        self.state = self.game.state

    def test_selectable_spans_all_three_of_my_colours(self):
        seats = {self.state.occupancy[cell] for cell in self.game.selectable()}
        self.assertEqual(seats, set(self.state.players[0].seats))

    def test_cannot_select_an_opponent_piece(self):
        cell = next(
            c
            for c, seat in self.state.occupancy.items()
            if self.state.owner_of(seat) != self.state.current
        )
        with self.assertRaises(IllegalMove):
            self.game.select(cell)

    def test_owner_lookup_is_consistent_with_the_seat_lists(self):
        for player in self.state.players:
            for seat in player.seats:
                self.assertEqual(self.state.owner_of(seat), player.index)


class TurnOrderTests(unittest.TestCase):
    def test_turns_alternate_between_the_two_players(self):
        game = Game.new(2, colors_each=3)
        seen = []
        for _ in range(6):
            seen.append(game.state.current)
            move = sorted(
                player_moves(game.state, game.state.current), key=lambda m: (m.origin, m.path)
            )[0]
            game.select(move.origin)
            for cell in move.path:
                game.extend(cell)
            game.confirm()
        self.assertEqual(seen, [0, 1, 0, 1, 0, 1])

    def test_moving_two_colours_in_a_row_still_costs_two_turns(self):
        """Owning three colours does not buy extra moves -- one piece a turn."""
        game = Game.new(2, colors_each=3)
        first = sorted(player_moves(game.state, 0), key=lambda m: (m.origin, m.path))[0]
        game.select(first.origin)
        for cell in first.path:
            game.extend(cell)
        game.confirm()
        self.assertEqual(game.state.current, 1)


class HomeLockTests(unittest.TestCase):
    """Home-lock is per colour: red locks into the red target, not into any
    camp its owner happens to control."""

    def test_a_piece_locks_only_into_its_own_target(self):
        game = Game.new(2, colors_each=3)
        state = game.state
        seat = state.players[0].seats[0]
        own_target = state.seats[seat].targets[0]
        sibling_target = state.seats[state.players[0].seats[1]].targets[0]
        self.assertNotEqual(own_target, sibling_target)

        # Park one piece of `seat` inside a sibling colour's target camp; it is
        # not home there, so it must still be free to leave.
        cell = min(board.CAMPS[sibling_target])
        state.occupancy = {c: s for c, s in state.occupancy.items() if c != cell}
        for cells in state.pieces.values():
            cells.discard(cell)
        state.occupancy[cell] = seat
        state.pieces[seat].add(cell)
        from chinese_checkers.core.rules import step_targets

        free = [t for t in step_targets(state, cell) if board.camp_of(t) != sibling_target]
        self.assertTrue(free, "a piece in a sibling colour's camp must not be locked in")


class WinningTests(unittest.TestCase):
    def test_a_player_needs_all_three_colours_home(self):
        game = Game.new(2, colors_each=3)
        state = game.state
        player = state.players[0]

        # Teleport two of the three colours home; the player is not done yet.
        for seat in player.seats[:2]:
            self._send_home(state, seat)
        self.assertFalse(state.has_finished(player.index))
        self.assertEqual(
            [state.seat_is_home(s) for s in player.seats], [True, True, False]
        )

        self._send_home(state, player.seats[2])
        self.assertTrue(state.has_finished(player.index))

    @staticmethod
    def _send_home(state, seat: int) -> None:
        target = board.CAMPS[state.seats[seat].targets[0]]
        for cell in list(state.pieces[seat]):
            del state.occupancy[cell]
        state.pieces[seat] = set()
        for cell in target:
            other = state.occupancy.pop(cell, None)
            if other is not None:
                state.pieces[other].discard(cell)
            state.occupancy[cell] = seat
            state.pieces[seat].add(cell)


class FullGameTests(unittest.TestCase):
    def test_a_greedy_game_terminates_with_every_colour_home(self):
        """End to end: the mode has to actually be winnable, not just set up."""
        game = Game.new(2, colors_each=3)
        rng = random.Random(11)
        tips = {s.index: board.CAMP_TIPS[s.targets[0]] for s in game.state.seats}

        moves = 0
        while not game.is_over and moves < 2000:
            candidates = player_moves(game.state, game.state.current)
            self.assertTrue(candidates, "a player should never be stuck")

            def progress(move):
                tip = tips[game.state.occupancy[move.origin]]
                return distance(move.destination, tip) - distance(move.origin, tip)

            best = min(candidates, key=lambda m: (progress(m), rng.random()))
            game.select(best.origin)
            for cell in best.path:
                game.extend(cell)
            game.confirm()
            moves += 1

        self.assertTrue(game.is_over, f"game did not finish in {moves} moves")
        winner = game.state.rankings[0]
        self.assertEqual(game.state.pieces_home(winner), 30)
        self.assertTrue(all(game.state.seat_is_home(s) for s in game.state.players[winner].seats))
        self.assertEqual(sorted(game.state.final_rankings), [0, 1])


class UndoTests(unittest.TestCase):
    def test_undo_restores_the_player_to_move_not_the_colour(self):
        game = Game.new(2, colors_each=3)
        before = dict(game.state.occupancy)
        move = sorted(player_moves(game.state, 0), key=lambda m: (m.origin, m.path))[0]
        game.select(move.origin)
        for cell in move.path:
            game.extend(cell)
        game.confirm()
        self.assertEqual(game.state.current, 1)

        game.undo_last_move()
        self.assertEqual(game.state.current, 0)
        self.assertEqual(game.state.occupancy, before)

    def test_full_undo_returns_to_the_opening_position(self):
        game = Game.new(2, colors_each=3)
        fresh = Game.new(2, colors_each=3)
        rng = random.Random(3)
        for _ in range(40):
            moves = player_moves(game.state, game.state.current)
            move = rng.choice(sorted(moves, key=lambda m: (m.origin, m.path)))
            game.select(move.origin)
            for cell in move.path:
                game.extend(cell)
            game.confirm()
        while game.can_undo:
            game.undo_last_move()
        self.assertEqual(game.state.occupancy, fresh.state.occupancy)
        self.assertEqual(game.state.pieces, fresh.state.pieces)
        self.assertEqual(game.state.current, fresh.state.current)
        self.assertEqual(game.state.rankings, fresh.state.rankings)


if __name__ == "__main__":
    unittest.main()
