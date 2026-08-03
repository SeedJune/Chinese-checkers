"""How good a position is, for the search bots.

Chinese checkers is a pure race: nothing is ever captured, so there is no
material to count and no tactical horizon to worry about.  What decides the
game is simply how far every piece still has to travel.  That gives us a cheap
and almost-monotone evaluation -- the sum of cube distances from each piece to
the camp it has to fill -- which is the single reason alpha-beta is the right
search for this game rather than MCTS (see :mod:`.minimax`).

Distances are measured to the camp *tip* rather than to the nearest cell of the
camp, and that choice is load-bearing.  The tip is the deepest hole, so pulling
every piece towards it fills the camp from the back forwards -- exactly the
order ``home_lock`` demands, since a piece that parks in the shallow end of its
own target camp can never come out again to make room.

Everything here is written for a *player*, not a colour: in the three-colour
mode one person runs three seats towards three different camps, and the sum
over their seats is the number that matters.
"""

from __future__ import annotations

from ..core import board
from ..core.coords import Hex, distance
from ..core.rules import Move
from ..core.state import GameState

#: Weight of the plain sum of distances -- the term that decides ordinary play.
W_SUM = 1.0

#: Weight of the single worst-placed piece.  Winning needs *every* hole filled,
#: so one straggler left behind the pack costs more than the sum alone admits;
#: without this the bot happily races nine pieces home and abandons the tenth.
W_MAX = 0.6

#: Weight of a hole still open in one's own target camp.  This is zero for most
#: of the game (the camp starts out packed with the opponent's pieces) and
#: decisive at the end, because it is the literal content of
#: :meth:`~..core.state.GameState.seat_is_home`.  It is what keeps the bot
#: sensible when an opponent squats in its target camp: the distance sum can
#: then never be driven down to its minimum, but the holes still can.
W_HOLE = 2.0

#: Score of a won position.  Comfortably beyond any reachable cost difference
#: (thirty pieces sixteen units from home is 480 at the absolute worst).
WIN = 10_000.0


def target_tips(state: GameState) -> tuple[Hex, ...]:
    """Seat index -> the tip cell of the camp that seat has to fill.

    Hoisted out of the evaluation because it is fixed for a whole game and the
    search asks for it at every node.
    """
    return tuple(board.CAMP_TIPS[seat.targets[0]] for seat in state.seats)


def player_terms(
    state: GameState, player: int, tips: tuple[Hex, ...]
) -> tuple[float, bool]:
    """``(cost, finished)`` for one player -- lower cost is better.

    The two are computed together because they walk the same target camps and
    this runs at every leaf of the search; ``finished`` here is exactly
    :meth:`~..core.state.GameState.has_finished`, re-derived from the hole
    count rather than called separately.
    """
    total = 0
    worst = 0
    holes = 0
    finished = True
    for seat in state.seats_of(player):
        tip = tips[seat]
        for cell in state.pieces[seat]:
            d = distance(cell, tip)
            total += d
            if d > worst:
                worst = d
        for camp in state.seats[seat].targets:
            cells = board.CAMPS[camp]
            empty = sum(1 for cell in cells if cell not in state.occupancy)
            holes += empty
            # A camp with no hole left is only *this seat's* home if one of the
            # pieces filling it is actually ours -- the same second clause
            # ``seat_is_home`` needs, and the reason a full camp at game start
            # (packed with the opponent) does not count.
            if empty or not any(state.occupancy[cell] == seat for cell in cells):
                finished = False
    return W_SUM * total + W_MAX * worst + W_HOLE * holes, finished


def player_cost(
    state: GameState, player: int, tips: tuple[Hex, ...] | None = None
) -> float:
    """How much work ``player`` still has to do.  Zero-ish means home."""
    return player_terms(state, player, tips or target_tips(state))[0]


def evaluate(
    state: GameState, player: int, tips: tuple[Hex, ...] | None = None
) -> float:
    """Zero-sum score of the position from ``player``'s point of view.

    Two players only -- which is the whole scope of the search bot, and covers
    both the ordinary two-player game and the three-colour mode.
    """
    tips = tips or target_tips(state)
    other = 1 - player
    mine, i_am_home = player_terms(state, player, tips)
    theirs, they_are_home = player_terms(state, other, tips)
    if i_am_home:
        return WIN
    if they_are_home:
        return -WIN
    return theirs - mine


def move_progress(state: GameState, move: Move, tips: tuple[Hex, ...]) -> int:
    """How many units of distance ``move`` gains, for move ordering.

    Deliberately does not touch the board: ordering happens before any of the
    moves is applied, and this is called once per move per node, so it has to
    stay two subtractions.
    """
    tip = tips[state.occupancy[move.origin]]
    return distance(move.origin, tip) - distance(move.destination, tip)
