"""Extension point for bots: anything that can pick a move for the player to move.

Kept in its own package (never imported by ``core/``) so future search-based
or RL-trained agents can depend on the engine without the engine depending on
them.
"""

from __future__ import annotations

import random
from typing import Protocol

from ..core.game import Game
from ..core.rules import Move, player_moves


class Agent(Protocol):
    def select_move(self, game: Game) -> Move: ...


class RandomAgent:
    """Uniform random legal move.

    Exists to prove the :class:`Agent` interface end-to-end and to drive
    tests (a seeded instance makes games deterministic and replayable).
    """

    def __init__(self, seed: int | None = None) -> None:
        self._rng = random.Random(seed)

    def select_move(self, game: Game) -> Move:
        # Per *player*, not per colour: a three-colour player chooses one
        # piece from any of their three sets.
        moves = player_moves(game.state, game.state.current)
        if not moves:
            raise ValueError("当前玩家没有合法的走法。")
        return self._rng.choice(moves)
