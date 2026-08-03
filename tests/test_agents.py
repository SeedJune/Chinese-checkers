"""The search bot: what it scores, what it prunes, and that it plays legally.

The one test here worth more than all the others is
:meth:`AlphaBetaTests.test_pruning_returns_the_full_width_value` -- alpha-beta
is an *exact* optimisation, so any pruning bug shows up as a root score that
differs from a plain minimax over the same tree.  Everything else guards
properties that are cheap to state and expensive to notice by hand: that the
search leaves the caller's position untouched, that it honours a clock, and
that it actually beats weaker play from either side of the board.
"""

from __future__ import annotations

import time
import unittest

from chinese_checkers.agents import LEVELS, MinimaxAgent, RandomAgent, make_agent
from chinese_checkers.agents.evaluate import (
    WIN,
    evaluate,
    move_progress,
    player_cost,
    player_terms,
    target_tips,
)
from chinese_checkers.agents.minimax import snapshot, zobrist
from chinese_checkers.core.board import BOARD, CAMPS, camp_of
from chinese_checkers.core.coords import distance, neighbors
from chinese_checkers.core.game import Game
from chinese_checkers.core.rules import Move, player_moves

from . import make_game, make_state

#: The two seats of an ordinary two-player game: seat 0 runs camp 0 -> camp 3,
#: seat 1 the other way.  ``make_state`` derives one player per seat, so a state
#: built with these is a genuine two-player position.
TWO_SEATS = Game.new(2).state.seats


def developed(plies: int, seed: int = 5, colors_each: int = 1) -> Game:
    """A game opened up by random play -- reproducible, and past the point
    where every piece is still stacked in its own camp."""
    game = Game.new(2, colors_each=colors_each)
    agent = RandomAgent(seed=seed)
    for _ in range(plies):
        game.state.apply(agent.select_move(game))
    return game


def endgame_state():
    """Seat 0 one single step away from filling its target camp.

    Returns ``(state, entry, hole)``.  Reaching this through legal play would
    take hundreds of moves and would not be reproducible, so the occupancy is
    placed by hand -- the engine's own code is untouched.
    """
    target = TWO_SEATS[0].targets[0]
    cells = sorted(CAMPS[target], key=lambda c: (c[2], c[0]))
    hole = cells[0]
    occupancy = {cell: 0 for cell in cells[1:]}
    entry = next(c for c in neighbors(hole) if c in BOARD and camp_of(c) != target)
    occupancy[entry] = 0
    # The opponent sits in the central hexagon as far from the hole as
    # possible: parked in its own target camp it would be finished too, and
    # the game would already be over.
    central = sorted(
        (c for c in BOARD if camp_of(c) is None and c not in occupancy),
        key=lambda c: -distance(c, hole),
    )
    for cell in central[:10]:
        occupancy[cell] = 1
    return make_state(occupancy, seats=TWO_SEATS), entry, hole


def full_width(state, depth: int, tips, ply: int = 1) -> float:
    """Plain negamax with no pruning, no table and no beam.

    The reference alpha-beta has to agree with.  Terminality is decided by the
    caller right after applying a move, exactly as the real search does, because
    the engine leaves the winner on turn and the sign flip would otherwise read
    a win as a loss.
    """
    if depth <= 0:
        return evaluate(state, state.current, tips)
    best = float("-inf")
    for move in player_moves(state, state.current):
        state.apply(move)
        value = (WIN - ply) if state.is_over else -full_width(state, depth - 1, tips, ply + 1)
        state.undo()
        best = max(best, value)
    return best


class EvaluationTests(unittest.TestCase):
    def test_is_zero_sum(self) -> None:
        state = developed(30).state
        tips = target_tips(state)
        self.assertAlmostEqual(
            evaluate(state, 0, tips), -evaluate(state, 1, tips), places=9
        )

    def test_opening_position_is_dead_even(self) -> None:
        state = Game.new(2).state
        self.assertEqual(evaluate(state, 0), 0.0)

    def test_stepping_towards_home_lowers_the_cost(self) -> None:
        origin = (4, 4, -8)  # seat 0's own camp tip, the furthest cell from home
        forward = (3, 4, -7)
        far = make_state({origin: 0, (-4, -4, 8): 1}, seats=TWO_SEATS)
        near = make_state({forward: 0, (-4, -4, 8): 1}, seats=TWO_SEATS)
        self.assertLess(player_cost(near, 0), player_cost(far, 0))

    def test_finished_flag_agrees_with_the_engine(self) -> None:
        for state in (Game.new(2).state, developed(40).state, endgame_state()[0]):
            tips = target_tips(state)
            for player in (0, 1):
                self.assertEqual(
                    player_terms(state, player, tips)[1],
                    state.has_finished(player),
                    f"player {player}",
                )

    def test_a_won_position_scores_as_a_win(self) -> None:
        state, entry, hole = endgame_state()
        state.apply(Move(origin=entry, path=(hole,), kind="step"))
        self.assertTrue(state.has_finished(0))
        self.assertEqual(evaluate(state, 0), WIN)
        self.assertEqual(evaluate(state, 1), -WIN)

    def test_move_progress_counts_ground_gained(self) -> None:
        game = developed(20)
        tips = target_tips(game.state)
        for move in player_moves(game.state, game.state.current)[:20]:
            tip = tips[game.state.occupancy[move.origin]]
            self.assertEqual(
                move_progress(game.state, move, tips),
                distance(move.origin, tip) - distance(move.destination, tip),
            )


class AlphaBetaTests(unittest.TestCase):
    """Pruning is an exact optimisation, so the score must not move."""

    def test_pruning_returns_the_full_width_value(self) -> None:
        game = developed(24)
        tips = target_tips(game.state)
        reference = full_width(snapshot(game.state), 3, tips)

        agent = MinimaxAgent(max_depth=3, seed=0)
        agent.select_move(game)
        self.assertAlmostEqual(agent.stats.value, reference, places=9)

    def test_the_transposition_table_does_not_change_the_score(self) -> None:
        game = developed(24)
        with_tt = MinimaxAgent(max_depth=3, use_tt=True, seed=0)
        without = MinimaxAgent(max_depth=3, use_tt=False, seed=0)
        with_tt.select_move(game)
        without.select_move(game)
        self.assertAlmostEqual(with_tt.stats.value, without.stats.value, places=9)

    def test_deeper_search_visits_more_nodes(self) -> None:
        game = developed(24)
        counts = []
        for depth in (2, 3, 4):
            agent = MinimaxAgent(max_depth=depth, seed=0)
            agent.select_move(game)
            counts.append(agent.stats.nodes)
        self.assertEqual(counts, sorted(counts))

    def test_zobrist_separates_position_and_side_to_move(self) -> None:
        state = developed(24).state
        before = zobrist(state)
        state.current = 1 - state.current
        self.assertNotEqual(before, zobrist(state))
        state.current = 1 - state.current
        self.assertEqual(before, zobrist(state))


class SelectMoveTests(unittest.TestCase):
    def test_every_level_returns_a_legal_move(self) -> None:
        for key, _label in LEVELS:
            game = developed(18)
            legal = player_moves(game.state, game.state.current)
            move = make_agent(key, seed=1).select_move(game)
            self.assertIn(move, legal, key)

    def test_the_caller_position_is_left_untouched(self) -> None:
        game = developed(18)
        state = game.state
        before = (dict(state.occupancy), len(state.history), state.current)
        make_agent("hard", seed=1).select_move(game)
        self.assertEqual(
            (dict(state.occupancy), len(state.history), state.current), before
        )

    def test_it_plays_the_move_that_wins_now(self) -> None:
        state, entry, hole = endgame_state()
        game = make_game({}, seats=TWO_SEATS)
        game.state = state
        move = MinimaxAgent(max_depth=3, seed=0).select_move(game)
        self.assertEqual(move.destination, hole)
        self.assertEqual(move.origin, entry)
        state.apply(move)
        self.assertTrue(state.has_finished(0))
        self.assertTrue(state.is_over)

    def test_a_time_budget_is_honoured(self) -> None:
        game = developed(30)
        agent = MinimaxAgent(max_depth=12, time_budget=0.2, beam=20, seed=1)
        started = time.monotonic()
        agent.select_move(game)
        self.assertLess(time.monotonic() - started, 0.8)
        self.assertGreaterEqual(agent.stats.depth, 1)

    def test_running_out_of_time_leaves_the_search_copy_clean(self) -> None:
        """A timeout unwinds past every ``undo``; the search has to put the
        board back before it can look at the move it settled on."""
        game = developed(30)
        agent = MinimaxAgent(max_depth=12, time_budget=0.05, beam=20, seed=1)
        for _ in range(4):
            move = agent.select_move(game)
            self.assertIn(move, player_moves(game.state, game.state.current))
            game.state.apply(move)
            game.state.apply(RandomAgent(seed=2).select_move(game))

    def test_no_legal_move_is_reported_not_guessed(self) -> None:
        game = make_game({(4, 4, -8): 0}, seats=TWO_SEATS, current=1)
        with self.assertRaises(ValueError):
            MinimaxAgent(max_depth=2).select_move(game)

    def test_unknown_level_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            make_agent("impossible")


class StrengthTests(unittest.TestCase):
    """The bot has to actually win, from either side of the board."""

    @staticmethod
    def _play(agent_0, agent_1, limit: int = 400) -> Game:
        game = Game.new(2)
        agents = {0: agent_0, 1: agent_1}
        for _ in range(limit):
            if game.is_over:
                break
            move = agents[game.state.current].select_move(game)
            game.select(move.origin)
            for cell in move.path:
                game.extend(cell)
            game.confirm()
        return game

    def test_it_beats_random_play_moving_first(self) -> None:
        game = self._play(make_agent("normal", seed=1), RandomAgent(seed=2))
        self.assertTrue(game.is_over)
        self.assertEqual(game.state.rankings, [0])

    def test_it_beats_random_play_moving_second(self) -> None:
        game = self._play(RandomAgent(seed=2), make_agent("normal", seed=1))
        self.assertTrue(game.is_over)
        self.assertEqual(game.state.rankings, [1])

    def test_looking_further_ahead_makes_faster_progress(self) -> None:
        """Twelve plies each, from the same opening: depth 3 should have its
        pieces closer to home than depth 1 does."""
        costs = {}
        for depth in (1, 3):
            game = Game.new(2)
            agent = MinimaxAgent(max_depth=depth, seed=0)
            other = RandomAgent(seed=4)
            for ply in range(24):
                move = (agent if ply % 2 == 0 else other).select_move(game)
                game.state.apply(move)
            costs[depth] = player_cost(game.state, 0)
        self.assertLess(costs[3], costs[1])


class ThreeColourTests(unittest.TestCase):
    """Method 2 is still two *players*, so alpha-beta applies unchanged."""

    def test_it_plays_legally_with_three_colours_each(self) -> None:
        game = developed(30, colors_each=3)
        self.assertEqual(len(game.state.players), 2)
        self.assertEqual(len(game.state.seats), 6)
        move = make_agent("normal", seed=1).select_move(game)
        self.assertIn(move, player_moves(game.state, game.state.current))

    def test_cost_sums_over_all_three_colours(self) -> None:
        state = Game.new(2, colors_each=3).state
        tips = target_tips(state)
        seat_totals = sum(
            distance(cell, tips[seat])
            for seat in state.seats_of(0)
            for cell in state.pieces[seat]
        )
        # 30 pieces, all still at home: the sum term is the bulk of the cost.
        self.assertGreater(seat_totals, 0)
        self.assertGreaterEqual(player_cost(state, 0, tips), seat_totals)


if __name__ == "__main__":
    unittest.main()
