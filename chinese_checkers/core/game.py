"""The ``Game`` facade: seat setup plus the step-by-step move-building UX.

Building a move is a little state machine (select a piece, extend it one hop
at a time, optionally roll back, then confirm) that both the desktop UI and a
future web front-end need identically, so it lives here instead of being
duplicated per-renderer.  Every command either mutates the builder or raises
:class:`~.rules.IllegalMove` with a Chinese message meant to be shown as-is.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass

from . import board
from .coords import Hex
from .rules import (
    IllegalMove,
    Move,
    RuleSet,
    can_stop_at,
    jump_hops,
    reachable,
    step_targets,
    validate,
)
from .state import GameState, PlayerInfo, SeatInfo


class Phase(enum.Enum):
    IDLE = "idle"  # nothing selected
    SELECTED = "selected"  # a piece is picked, no landing chosen yet
    CHAINING = "chaining"  # one or more jumps taken, may continue or confirm
    STEPPED = "stepped"  # a single step taken, may only confirm or roll back


@dataclass
class MoveResult:
    move: Move
    finished: int | None
    game_over: bool


class Game:
    """Owns a :class:`GameState` plus the in-progress move build."""

    def __init__(self, state: GameState) -> None:
        self.state = state
        self._phase = Phase.IDLE
        self._origin: Hex | None = None
        self._landings: list[Hex] = []
        self._kind: str | None = None

    @classmethod
    def new(
        cls,
        player_count: int,
        names: list[str] | None = None,
        colors: list[str] | None = None,
        rules: RuleSet | None = None,
        colors_each: int = 1,
    ) -> Game:
        """Set up a game.

        ``colors_each=1`` is the ordinary game; ``colors_each=3`` with two
        players is the README's method 2, where each player runs three colours
        into three different corners and the two take turns.
        """
        camps_per_player = board.player_camps(player_count, colors_each)
        names = names if names is not None else [f"玩家{i + 1}" for i in range(player_count)]
        if len(names) != player_count:
            raise ValueError("names 的数量必须与玩家人数一致。")

        # Seats are numbered in camp order so a colour's index is stable no
        # matter how the camps are dealt out to players.
        flat_camps = [camp for camps in camps_per_player for camp in camps]
        if colors is not None and len(colors) != len(flat_camps):
            raise ValueError("colors 的数量必须与颜色总数一致。")

        seats: list[SeatInfo] = []
        players: list[PlayerInfo] = []
        for player_index, camps in enumerate(camps_per_player):
            owned: list[int] = []
            for camp in camps:
                index = len(seats)
                color = colors[index] if colors is not None else board.DEFAULT_COLORS[camp]
                seats.append(
                    SeatInfo(
                        index=index,
                        name=board.COLOR_NAMES[camp],
                        color=color,
                        camps=(camp,),
                        targets=(board.opposite(camp),),
                    )
                )
                owned.append(index)
            players.append(
                PlayerInfo(index=player_index, name=names[player_index], seats=tuple(owned))
            )

        state = GameState.new(
            tuple(seats), rules if rules is not None else RuleSet(), tuple(players)
        )
        return cls(state)

    # ---------------------------------------------------------- queries ----

    @property
    def current_player(self) -> PlayerInfo:
        return self.state.current_player

    @property
    def current_seats(self) -> tuple[SeatInfo, ...]:
        """Every colour the player to move controls, in camp order."""
        return tuple(self.state.seats[i] for i in self.state.seats_of(self.state.current))

    @property
    def current_seat(self) -> SeatInfo:
        """The single colour of the player to move.

        Only meaningful in the ordinary one-colour game; kept because the board
        overlays want "the" colour to tint with.  In the three-colour mode the
        widget should follow the selected piece instead -- see
        :attr:`active_seat`.
        """
        return self.current_seats[0]

    @property
    def active_seat(self) -> SeatInfo:
        """The colour the overlays should be drawn in.

        Follows the piece being moved once one is selected, so a three-colour
        player's highlights match the marble they actually picked up.
        """
        if self._origin is not None:
            return self.state.seats[self.state.occupancy[self._origin]]
        return self.current_seats[0]

    @property
    def is_over(self) -> bool:
        return self.state.is_over

    @property
    def phase(self) -> Phase:
        return self._phase

    @property
    def path(self) -> tuple[Hex, ...]:
        if self._phase is Phase.IDLE:
            return ()
        assert self._origin is not None
        return (self._origin, *self._landings)

    def selectable(self) -> frozenset[Hex]:
        """Pieces the player to move could pick up -- across all their colours."""
        return frozenset(
            cell
            for cell in self.state.player_cells(self.state.current)
            if reachable(self.state, cell)
        )

    def options(self) -> dict[str, frozenset[Hex]]:
        """Cells :meth:`extend` would currently accept, split by move kind.

        Step and jump are never offered together: a jump chain in progress
        only offers further jumps, and a completed step offers nothing (it
        can only be confirmed, rolled back, or cancelled).
        """
        if self._phase is Phase.SELECTED:
            assert self._origin is not None
            return {
                "steps": frozenset(step_targets(self.state, self._origin)),
                "jumps": frozenset(jump_hops(self.state, self._origin, ())),
            }
        if self._phase is Phase.CHAINING:
            assert self._origin is not None
            return {
                "steps": frozenset(),
                "jumps": frozenset(jump_hops(self.state, self._origin, tuple(self._landings))),
            }
        return {"steps": frozenset(), "jumps": frozenset()}

    @property
    def can_confirm(self) -> bool:
        """True only if the move as built would actually pass validation.

        With ``no_stop_in_foreign_camp`` on, a chain may legally hop *through*
        another player's camp but not stop there, so having a path is not
        enough -- checking here is what keeps the confirm button from offering
        a move that :func:`validate` would refuse.
        """
        if self._phase not in (Phase.CHAINING, Phase.STEPPED):
            return False
        return can_stop_at(self.state, self.state.occupancy[self._origin], self._landings[-1])

    @property
    def can_undo(self) -> bool:
        return bool(self.state.history)

    # --------------------------------------------------------- commands ----

    def select(self, cell: Hex) -> None:
        if self._phase is not Phase.IDLE:
            raise IllegalMove("请先确认或取消当前的走子操作。")
        seat = self.state.occupancy.get(cell)
        if seat is None or self.state.owner_of(seat) != self.state.current:
            raise IllegalMove("只能选择自己的棋子。")
        if not reachable(self.state, cell):
            raise IllegalMove("这颗棋子当前没有可走的位置。")
        self._origin = cell
        self._landings = []
        self._kind = None
        self._phase = Phase.SELECTED

    def extend(self, cell: Hex) -> None:
        opts = self.options()
        if cell in opts["steps"]:
            self._landings = [cell]
            self._kind = "step"
            self._phase = Phase.STEPPED
            return
        if cell in opts["jumps"]:
            self._landings.append(cell)
            self._kind = "jump"
            self._phase = Phase.CHAINING
            return
        raise IllegalMove("该位置不是合法的落点。")

    def rollback(self) -> None:
        if self._phase not in (Phase.CHAINING, Phase.STEPPED):
            raise IllegalMove("没有可以回退的落点。")
        self._landings.pop()
        if self._landings:
            self._phase = Phase.CHAINING
        else:
            self._phase = Phase.SELECTED
            self._kind = None

    def cancel(self) -> None:
        self._reset_build()

    def confirm(self) -> MoveResult:
        if self._phase not in (Phase.CHAINING, Phase.STEPPED):
            raise IllegalMove("当前没有可以确认的走法。")
        assert self._origin is not None and self._kind is not None
        move = Move(origin=self._origin, path=tuple(self._landings), kind=self._kind)
        validate(self.state, move)
        finished = self.state.apply(move)
        result = MoveResult(move=move, finished=finished, game_over=self.state.is_over)
        self._reset_build()
        return result

    def undo_last_move(self) -> Move:
        self._reset_build()
        record = self.state.undo()
        return record.move

    def _reset_build(self) -> None:
        self._phase = Phase.IDLE
        self._origin = None
        self._landings = []
        self._kind = None
