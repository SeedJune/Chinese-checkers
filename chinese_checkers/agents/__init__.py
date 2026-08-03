from __future__ import annotations

from ..core.game import Game
from .base import Agent, RandomAgent
from .minimax import MinimaxAgent, SearchStats, snapshot

#: ``(key, label)`` for the difficulty levels the menu offers, easiest first.
#: The labels are shown to the player verbatim.
LEVELS: tuple[tuple[str, str], ...] = (
    ("easy", "简单"),
    ("normal", "普通"),
    ("hard", "困难"),
)

LEVEL_NAMES: dict[str, str] = dict(LEVELS)


def make_agent(level: str, seed: int | None = None) -> Agent:
    """Build the bot for a difficulty level.

    The three levels are one search with three sets of knobs rather than three
    algorithms: ``easy`` looks one move ahead and picks loosely among the
    decent replies, ``normal`` searches a fixed three plies (~40 ms, so it
    answers instantly), and ``hard`` deepens iteratively for a second, which
    measures out at depth 5-6 in the midgame.
    """
    if level == "easy":
        return MinimaxAgent(max_depth=1, slack=2.0, seed=seed)
    if level == "normal":
        return MinimaxAgent(max_depth=3, seed=seed)
    if level == "hard":
        return MinimaxAgent(max_depth=12, time_budget=1.0, beam=20, seed=seed)
    raise ValueError(f"未知的电脑难度：{level!r}。")


def detached(game: Game) -> Game:
    """A copy of ``game`` that an agent may be handed on another thread.

    The position is copied, the immutable seat/rule metadata is shared, and the
    move history is dropped -- an agent only ever looks forwards.  Callers get
    the guarantee that whatever the agent does to it cannot be seen by the game
    the player is looking at.
    """
    return Game(snapshot(game.state))


__all__ = [
    "Agent",
    "RandomAgent",
    "MinimaxAgent",
    "SearchStats",
    "LEVELS",
    "LEVEL_NAMES",
    "detached",
    "make_agent",
    "snapshot",
]
