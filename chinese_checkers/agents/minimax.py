"""Negamax with alpha-beta pruning -- the bot that actually plays.

Why alpha-beta and not MCTS, which is the other obvious candidate: this game
hands us a cheap and nearly monotone evaluation for free (see
:mod:`.evaluate`), and alpha-beta is exactly the search that cashes a good
evaluation in.  MCTS would have to fight the opposite problem -- with no
captures and no terminal pressure a random playout essentially never ends, the
marbles just wander around the middle of the star -- so it would need
heuristic, truncated playouts scored by *the same* distance function, which is
strictly more machinery for less tactical precision.  UCT's known edge in this
game is in three-or-more-player positions, where alpha-beta does not apply at
all; this bot deliberately only claims the two-player case.

The measured shape of the game (opening branching factor 14, midgame 51 on
average, ~0.1 ms to generate every legal move of a turn) means a one-second
budget reaches depth 4 full-width and depth 5-6 with a beam, which for a race
is plenty.

Search runs on a :func:`snapshot` of the state rather than the live one, which
buys two things at once: the UI can keep reading the real position while the
bot thinks, and the whole search can therefore be run on a worker thread.
"""

from __future__ import annotations

import random
import time
from dataclasses import dataclass

from ..core import board
from ..core.game import Game
from ..core.rules import Move, player_moves
from ..core.state import GameState
from .evaluate import WIN, evaluate, move_progress, target_tips

#: Transposition table entry kinds: the stored value is the true score, or only
#: a bound on it because the node was cut off.
EXACT, LOWER, UPPER = 0, 1, 2

#: Score deducted at the root from a move that returns to a position this agent
#: has already created.  Small on purpose -- just enough to break the shuffling
#: that a distance evaluation otherwise invites when two moves tie.
REPEAT_PENALTY = 0.5

#: Nodes between clock checks.  At ~0.02 ms a node this keeps the time budget
#: accurate to about 20 ms without calling ``monotonic`` a million times.
_CLOCK_INTERVAL = 1024

_INF = float("inf")


class _Timeout(Exception):
    """Unwinds the search when the time budget runs out mid-iteration."""


def snapshot(state: GameState) -> GameState:
    """A copy of ``state`` that is safe to search on from another thread.

    Only the four mutable pieces are copied; ``rules``, ``seats`` and
    ``players`` are frozen dataclasses and tuples, so sharing them is safe and
    saves a deep copy.  ``history`` starts empty because the search only ever
    undoes moves it applied itself -- ``rankings`` is what actually carries the
    "somebody already finished" part of the position.
    """
    return GameState(
        rules=state.rules,
        seats=state.seats,
        occupancy=dict(state.occupancy),
        pieces={seat: set(cells) for seat, cells in state.pieces.items()},
        current=state.current,
        rankings=list(state.rankings),
        history=[],
        players=state.players,
    )


def _zobrist_tables() -> tuple[dict, tuple[int, ...]]:
    """Random keys for hashing a position, drawn from a fixed seed.

    Fixed so that hashes are reproducible across runs and across processes,
    which is what makes a search reproducible in tests.
    """
    rng = random.Random(0x9E3779B97F4A7C15)
    cells = {
        cell: tuple(rng.getrandbits(64) for _ in range(len(board.CAMPS)))
        for cell in board.BOARD_SORTED
    }
    turn = tuple(rng.getrandbits(64) for _ in range(len(board.CAMPS)))
    return cells, turn


_Z_CELL, _Z_TURN = _zobrist_tables()


def zobrist(state: GameState) -> int:
    """Hash of the position *and* whose turn it is.

    Recomputed from scratch at every node instead of being updated
    incrementally: twenty pieces is twenty xors (~5 us, about 5% of a node),
    and an incremental hash that has to stay correct across ``undo`` is the
    kind of code that fails silently.
    """
    h = _Z_TURN[state.current]
    for cell, seat in state.occupancy.items():
        h ^= _Z_CELL[cell][seat]
    return h


@dataclass
class SearchStats:
    """What the last :meth:`MinimaxAgent.select_move` actually did."""

    nodes: int = 0
    depth: int = 0
    value: float = 0.0
    elapsed: float = 0.0


class MinimaxAgent:
    """Alpha-beta bot.  Implements the :class:`~.base.Agent` protocol.

    ``max_depth`` alone gives a fixed-depth search; adding ``time_budget``
    turns it into iterative deepening that stops when the clock runs out and
    plays the best move of the last iteration that completed.

    ``beam`` caps how many moves are examined at each node below the root,
    after ordering.  The root is deliberately never beamed, so the bot always
    weighs every move it is actually allowed to make.

    ``slack`` makes the bot pick at random among root moves within that many
    points of the best, which is what the easy level is made of; it also
    switches the root to a full window so those scores are exact rather than
    the upper bounds alpha-beta would otherwise leave behind.
    """

    def __init__(
        self,
        max_depth: int = 3,
        time_budget: float | None = None,
        beam: int | None = None,
        slack: float = 0.0,
        seed: int | None = None,
        use_tt: bool = True,
    ) -> None:
        self.max_depth = max_depth
        self.time_budget = time_budget
        self.beam = beam
        self.slack = slack
        self.use_tt = use_tt
        self.stats = SearchStats()
        self._rng = random.Random(seed)
        # Positions this agent has already moved into, so it can be nudged out
        # of shuffling back and forth between two equal-scoring positions.
        self._seen: set[int] = set()
        self._tips: tuple = ()
        self._tt: dict[int, tuple[int, float, int, Move]] = {}
        self._killers: dict[int, list[Move]] = {}

    # ------------------------------------------------------------- public ----

    def select_move(self, game: Game) -> Move:
        started = time.monotonic()
        state = snapshot(game.state)
        moves = player_moves(state, state.current)
        if not moves:
            raise ValueError("当前玩家没有合法的走法。")

        self._tips = target_tips(state)
        self._tt = {}
        self._killers = {}
        self.stats = SearchStats()

        if len(state.players) != 2:
            # Alpha-beta is only sound for two sides.  Rather than refuse to
            # move, fall back to the depth-1 greedy pick, which is well defined
            # for any number of players -- a real three-player bot wants max^n
            # and is a separate piece of work.
            best = self._pick([(move_progress(state, m, self._tips), m) for m in moves])
        else:
            best = self._deepen(state, moves, started)

        self.stats.elapsed = time.monotonic() - started
        state.apply(best)
        self._seen.add(zobrist(state))
        state.undo()
        return best

    # ------------------------------------------------------------- search ----

    def _deepen(self, state: GameState, moves: list[Move], started: float) -> Move:
        """Iterative deepening.  Returns the best move of the last full pass."""
        deadline = started + self.time_budget if self.time_budget else None
        ordered = self._order(state, moves, None, 0)
        best = ordered[0]
        for depth in range(1, self.max_depth + 1):
            try:
                move, value = self._root(state, depth, deadline)
            except _Timeout:
                # The exception unwound past every ``state.undo()`` on the way
                # out, so the board is stranded somewhere deep in the tree.
                # ``snapshot`` handed us an empty history, so whatever is left
                # in it is exactly the moves the search still owes an undo for.
                while state.history:
                    state.undo()
                break
            best = move
            self.stats.depth = depth
            self.stats.value = value
            if abs(value) >= WIN:
                break  # a forced result will not change with more depth
            if deadline is not None and time.monotonic() >= deadline:
                break
        return best

    def _root(
        self, state: GameState, depth: int, deadline: float | None
    ) -> tuple[Move, float]:
        wide = self.slack > 0.0
        moves = self._order(state, player_moves(state, state.current), self._tt_move(state), 0)
        alpha = -_INF
        scored: list[tuple[float, Move]] = []
        best_move, best_value = moves[0], -_INF
        for move in moves:
            state.apply(move)
            if state.is_over:
                value = WIN - 1  # the side that just moved is home
            else:
                lo, hi = (-_INF, _INF) if wide else (-_INF, -alpha)
                value = -self._search(state, depth - 1, lo, hi, deadline, 1)
            if zobrist(state) in self._seen:
                value -= REPEAT_PENALTY
            state.undo()
            scored.append((value, move))
            if value > best_value:
                best_value, best_move = value, move
            if value > alpha:
                alpha = value
        if wide:
            return self._pick(scored), best_value
        return best_move, best_value

    def _search(
        self,
        state: GameState,
        depth: int,
        alpha: float,
        beta: float,
        deadline: float | None,
        ply: int,
    ) -> float:
        """Score of ``state`` from the point of view of the side to move.

        Only ever entered on a live position -- whether a move ended the game
        is decided by the caller, right after it applied the move, because
        :meth:`~..core.state.GameState.advance_turn` leaves the winner on turn
        and the usual negamax sign flip would then read a win as a loss.
        """
        self.stats.nodes += 1
        if (
            deadline is not None
            and self.stats.nodes % _CLOCK_INTERVAL == 0
            and time.monotonic() >= deadline
        ):
            raise _Timeout
        if depth <= 0:
            return evaluate(state, state.current, self._tips)

        key = zobrist(state) if self.use_tt else None
        tt_move: Move | None = None
        if key is not None:
            entry = self._tt.get(key)
            if entry is not None:
                stored_depth, value, flag, tt_move = entry
                if stored_depth >= depth:
                    if flag == EXACT:
                        return value
                    if flag == LOWER:
                        alpha = max(alpha, value)
                    else:
                        beta = min(beta, value)
                    if alpha >= beta:
                        return value

        moves = player_moves(state, state.current)
        if not moves:
            # No legal move without having finished: unreachable in a real game
            # (ten marbles always have somewhere to go), but the engine has no
            # pass, so score it rather than crash.
            return evaluate(state, state.current, self._tips)
        moves = self._order(state, moves, tt_move, ply)
        if self.beam is not None:
            moves = moves[: self.beam]

        original_alpha = alpha
        best_value = -_INF
        best_move = moves[0]
        for move in moves:
            state.apply(move)
            if state.is_over:
                # Prefer winning sooner: a mate in one beats the same mate in
                # three, which also stops the bot dawdling on the last hole.
                value = WIN - ply
            else:
                value = -self._search(state, depth - 1, -beta, -alpha, deadline, ply + 1)
            state.undo()
            if value > best_value:
                best_value, best_move = value, move
            if best_value > alpha:
                alpha = best_value
            if alpha >= beta:
                self._remember_killer(ply, move)
                break

        if key is not None:
            if best_value <= original_alpha:
                flag = UPPER
            elif best_value >= beta:
                flag = LOWER
            else:
                flag = EXACT
            self._tt[key] = (depth, best_value, flag, best_move)
        return best_value

    # ----------------------------------------------------------- ordering ----

    def _order(
        self, state: GameState, moves: list[Move], tt_move: Move | None, ply: int
    ) -> list[Move]:
        """Best-first, because nearly all of alpha-beta's value is here.

        Three tiers: whatever a previous (shallower or transposed) search
        already liked, then the moves that caused a cutoff elsewhere at this
        ply, then raw progress towards home.
        """
        killers = self._killers.get(ply, ())
        tips = self._tips
        ranked = []
        for move in moves:
            if move == tt_move:
                rank = 2
            elif move in killers:
                rank = 1
            else:
                rank = 0
            ranked.append((rank, move_progress(state, move, tips), move))
        ranked.sort(key=lambda item: (item[0], item[1]), reverse=True)
        return [move for _rank, _progress, move in ranked]

    def _remember_killer(self, ply: int, move: Move) -> None:
        killers = self._killers.setdefault(ply, [])
        if move in killers:
            return
        killers.insert(0, move)
        del killers[2:]

    def _tt_move(self, state: GameState) -> Move | None:
        if not self.use_tt:
            return None
        entry = self._tt.get(zobrist(state))
        return entry[3] if entry is not None else None

    def _pick(self, scored: list[tuple[float, Move]]) -> Move:
        """Best move, or a random one from within ``slack`` of the best."""
        best = max(value for value, _move in scored)
        pool = [move for value, move in scored if value >= best - self.slack]
        return pool[0] if len(pool) == 1 else self._rng.choice(pool)
