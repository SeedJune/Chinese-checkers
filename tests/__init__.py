"""Test package for the Chinese Checkers engine.

Holds the handful of helpers shared by more than one test module.  The rules
tests deliberately build tiny hand-made positions instead of playing games, so
almost every test needs a way to stamp an arbitrary occupancy onto a
:class:`GameState` that still carries realistic seat metadata.
"""

from __future__ import annotations

from chinese_checkers.core.coords import Hex
from chinese_checkers.core.game import Game
from chinese_checkers.core.rules import RuleSet
from chinese_checkers.core.state import GameState, SeatInfo

#: The six standard seats (seat ``i`` starts in camp ``i`` and targets ``i+3``).
#: Reused everywhere so hand-made positions have the same camps/targets as a
#: real six-player game.
SEATS: tuple[SeatInfo, ...] = Game.new(6).state.seats


def make_state(
    occupancy: dict[Hex, int],
    *,
    current: int = 0,
    rules: RuleSet | None = None,
    seats: tuple[SeatInfo, ...] = SEATS,
) -> GameState:
    """A ``GameState`` with exactly ``occupancy`` on the board.

    ``pieces`` is derived from ``occupancy`` so the two stay consistent; seats
    with no listed cell simply own nothing.
    """
    state = GameState(rules=rules if rules is not None else RuleSet(), seats=seats)
    state.occupancy = dict(occupancy)
    state.pieces = {seat.index: set() for seat in seats}
    for cell, seat_index in occupancy.items():
        state.pieces[seat_index].add(cell)
    state.current = current
    return state


def make_game(occupancy: dict[Hex, int], **kwargs) -> Game:
    """``make_state`` wrapped in a fresh :class:`Game` (builder in IDLE)."""
    return Game(make_state(occupancy, **kwargs))
