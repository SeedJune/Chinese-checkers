"""Move legality: the single source of truth for what a piece may do.

The UI and any future bot both call into :func:`legal_moves`/:func:`reachable`
instead of re-deriving adjacency and jump geometry themselves, so a rule change
here (e.g. loosening ``home_lock``) automatically propagates everywhere.

A move is either one single step to an adjacent empty cell, or a chain of one
or more jumps -- never a mix.  A jump hops over exactly one occupied neighbour
in a straight line and lands on the empty cell immediately beyond it; within a
chain the piece's own origin counts as empty (it is "in the air" for the
whole turn) and no cell may be revisited, which also rules out null moves.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass

from . import board
from .coords import DIRECTIONS, Hex, add, neighbors, scale


@dataclass(frozen=True)
class RuleSet:
    """Tunable rules for a game.  Only ``home_lock`` has real teeth today."""

    jump_rule: str = "adjacent"  # only variant implemented so far
    home_lock: bool = True  # a piece inside its target camp may never leave it
    no_stop_in_foreign_camp: bool = False  # forbid ending a move in someone else's camp
    win_condition: str = "fill_available"
    stall_limit: int | None = None  # reserved for a future draw rule


class IllegalMove(Exception):
    """Raised by :func:`validate` with a Chinese explanation of the failure."""


@dataclass(frozen=True)
class Move:
    origin: Hex
    path: tuple[Hex, ...]  # successive landing cells; path[-1] is the destination
    kind: str  # "step" | "jump"

    @property
    def destination(self) -> Hex:
        return self.path[-1]


def _seat_camps(state, seat: int) -> tuple[int, ...]:
    return state.seats[seat].camps


def _seat_targets(state, seat: int) -> tuple[int, ...]:
    return state.seats[seat].targets


def _home_lock_ok(state, seat: int, origin: Hex, path: tuple[Hex, ...]) -> bool:
    """If ``origin`` sits in one of the seat's target camps, the whole path must
    stay inside that same camp."""
    if not state.rules.home_lock:
        return True
    origin_camp = board.camp_of(origin)
    if origin_camp is None or origin_camp not in _seat_targets(state, seat):
        return True
    return all(board.camp_of(cell) == origin_camp for cell in path)


def _no_stop_ok(state, seat: int, destination: Hex) -> bool:
    """Reject landing (as a final stop) in a camp belonging to another *player*.

    Scoped to the player rather than the colour on purpose: in the three-colour
    mode your other two colours' camps are still yours, so parking a red piece
    in your own blue corner is your business.  Passing through is always fine;
    this only gates the cell a move actually ends on.
    """
    if not state.rules.no_stop_in_foreign_camp:
        return True
    camp = board.camp_of(destination)
    if camp is None:
        return True
    allowed: set[int] = set()
    for owned in state.seats_of(state.owner_of(seat)):
        allowed |= set(_seat_camps(state, owned)) | set(_seat_targets(state, owned))
    return camp in allowed


def can_stop_at(state, seat: int, cell: Hex) -> bool:
    """Whether a move by ``seat`` is allowed to *end* on ``cell``.

    Public because the two rules differ in scope: a jump chain may pass through
    a camp it is forbidden to stop in, so a landing cell being offered as a hop
    does not imply the move can be confirmed there.  The UI needs to ask this
    separately or it would light up a confirm button that validation rejects.
    """
    return _no_stop_ok(state, seat, cell)


def step_targets(state, origin: Hex) -> list[Hex]:
    """The (filtered) adjacent empty cells ``origin`` may step to."""
    seat = state.occupancy.get(origin)
    if seat is None:
        return []
    targets = []
    for cell in neighbors(origin):
        if cell not in board.BOARD or cell in state.occupancy:
            continue
        if not _home_lock_ok(state, seat, origin, (cell,)):
            continue
        if not _no_stop_ok(state, seat, cell):
            continue
        targets.append(cell)
    return targets


def jump_hops(state, origin: Hex, chain: tuple[Hex, ...] = ()) -> list[Hex]:
    """Landing cells reachable by one more jump from the end of ``chain``.

    ``origin`` is treated as vacant for the whole chain (the piece is "in the
    air"), and no cell in ``(origin,) + chain`` may be revisited.  Only
    ``home_lock`` is checked here -- ``no_stop_in_foreign_camp`` only
    constrains the cell a chain actually *stops* on, which callers decide.
    """
    seat = state.occupancy.get(origin)
    if seat is None:
        return []
    current = chain[-1] if chain else origin
    visited = (origin,) + chain
    hops = []
    for d in DIRECTIONS:
        over = add(current, d)
        landing = add(current, scale(d, 2))
        if landing not in board.BOARD:
            continue
        if over == origin:
            continue  # nothing to jump over -- the origin is empty mid-chain
        if over not in state.occupancy:
            continue  # no piece to hop over in this direction
        landing_empty = landing not in state.occupancy or landing == origin
        if not landing_empty or landing in visited:
            continue
        if not _home_lock_ok(state, seat, origin, (landing,)):
            continue
        hops.append(landing)
    return hops


def reachable(state, origin: Hex) -> dict[Hex, Move]:
    """Every legal destination from ``origin``, mapped to a shortest-path Move.

    Steps come first (they are trivially shortest), then jump chains are
    explored breadth-first so the first path found to any landing cell has
    the fewest hops.
    """
    seat = state.occupancy.get(origin)
    if seat is None:
        return {}

    result: dict[Hex, Move] = {}
    for cell in step_targets(state, origin):
        result[cell] = Move(origin=origin, path=(cell,), kind="step")

    seen: set[tuple[Hex, frozenset[Hex]]] = set()
    queue: deque[tuple[Hex, ...]] = deque([()])
    while queue:
        chain = queue.popleft()
        current = chain[-1] if chain else origin
        key = (current, frozenset((origin,) + chain))
        if key in seen:
            continue
        seen.add(key)
        for landing in jump_hops(state, origin, chain):
            new_chain = chain + (landing,)
            if landing not in result and _no_stop_ok(state, seat, landing):
                result[landing] = Move(origin=origin, path=new_chain, kind="jump")
            queue.append(new_chain)
    return result


def legal_moves(state, seat: int) -> list[Move]:
    """Every legal move available to one colour, across all of its pieces."""
    moves: list[Move] = []
    for origin in state.pieces.get(seat, ()):
        moves.extend(reachable(state, origin).values())
    return moves


def player_moves(state, player: int) -> list[Move]:
    """Every legal move available to a *player* this turn.

    Distinct from :func:`legal_moves` because a three-colour player picks one
    piece from any of their colours -- this is what a bot should enumerate.
    """
    moves: list[Move] = []
    for seat in state.seats_of(player):
        moves.extend(legal_moves(state, seat))
    return moves


def validate(state, move: Move) -> None:
    """Re-derive ``move`` from scratch; raise :class:`IllegalMove` (Chinese
    message, shown verbatim by the UI) if anything about it is wrong."""
    if not move.path:
        raise IllegalMove("移动路径不能为空。")

    origin = move.origin
    if origin not in state.occupancy:
        raise IllegalMove("起点没有棋子。")

    seat = state.occupancy[origin]
    if state.owner_of(seat) != state.current:
        mover_name = state.current_player.name
        raise IllegalMove(f"现在是{mover_name}的回合，不能移动别人的棋子。")

    for cell in move.path:
        if cell not in board.BOARD:
            raise IllegalMove("目标位置不在棋盘范围内。")
    if origin in move.path or len(set(move.path)) != len(move.path):
        raise IllegalMove("移动路径不能重复经过同一个位置。")

    if move.kind == "step":
        if len(move.path) != 1:
            raise IllegalMove("单步移动和跳跃不能混合,单步只能移动一格。")
        if move.path[0] not in step_targets(state, origin):
            raise IllegalMove("这一步不合法(目标被占据、超出规则允许范围,或违反了老家锁定规则)。")
    elif move.kind == "jump":
        chain: tuple[Hex, ...] = ()
        for landing in move.path:
            if landing not in jump_hops(state, origin, chain):
                raise IllegalMove(
                    "跳跃路径不合法(中间没有可跳过的棋子、落点被占用,或违反了老家锁定规则)。"
                )
            chain = chain + (landing,)
        if not _no_stop_ok(state, seat, move.destination):
            raise IllegalMove("不能停在其他玩家的营地中,只能经过。")
    else:
        raise IllegalMove(f"未知的移动类型:{move.kind!r}。")
