"""Mutable game state: who sits where, who owns which cell, whose turn it is.

Kept deliberately dumb -- this module has no notion of legality (that lives in
:mod:`.rules`).  ``apply``/``undo`` trust the caller (:class:`~.game.Game`) to
have validated the move already, so the same state machine can replay a
recorded game or drive an RL environment without re-checking rules on load.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from . import board
from .coords import Hex
from .rules import Move, RuleSet


@dataclass(frozen=True)
class SeatInfo:
    """One *colour* -- ten pieces, one starting camp, one target camp.

    A seat is the unit of ownership on the board: ``occupancy`` maps a cell to a
    seat index, and home-lock applies per seat, because a blue piece must finish
    in the blue target and nowhere else.  Whose *turn* it is, by contrast, is a
    property of a :class:`PlayerInfo`, which may own several seats.

    ``camps``/``targets`` stay tuples even though a seat always has exactly one
    of each; they predate the split and several callers already destructure
    them.
    """

    index: int
    name: str
    color: str
    camps: tuple[int, ...]
    targets: tuple[int, ...]


@dataclass(frozen=True)
class PlayerInfo:
    """One person at the table, owning one or more colours.

    The ordinary game gives every player a single seat, so player index and
    seat index coincide and the distinction is invisible.  Method 2 -- two
    players with three colours each -- is the reason the two are separate:
    turn order, finishing and rankings are all per *player*, while movement
    and home-lock stay per *colour*.
    """

    index: int
    name: str
    seats: tuple[int, ...]


@dataclass(frozen=True)
class MoveRecord:
    """Enough to replay or exactly undo one applied move."""

    seat: int    # the colour that moved
    move: Move
    finished: bool  # True if this move is what appended `player` to rankings
    player: int = 0  # the person whose turn it was


@dataclass
class GameState:
    rules: RuleSet
    seats: tuple[SeatInfo, ...]
    occupancy: dict[Hex, int] = field(default_factory=dict)   # cell -> seat
    pieces: dict[int, set[Hex]] = field(default_factory=dict)  # seat -> cells
    current: int = 0                                   # player index
    rankings: list[int] = field(default_factory=list)  # player indices
    history: list[MoveRecord] = field(default_factory=list)
    players: tuple[PlayerInfo, ...] = ()

    def __post_init__(self) -> None:
        if not self.players:
            # One player per colour: the ordinary game, and what every caller
            # that predates method 2 assumes.
            self.players = tuple(
                PlayerInfo(index=s.index, name=s.name, seats=(s.index,)) for s in self.seats
            )
        self._owner = {
            seat: player.index for player in self.players for seat in player.seats
        }

    @classmethod
    def new(
        cls,
        seats: tuple[SeatInfo, ...],
        rules: RuleSet | None = None,
        players: tuple[PlayerInfo, ...] = (),
    ) -> GameState:
        """Build the starting position: every seat's pieces fill its camps."""
        rules = rules if rules is not None else RuleSet()
        occupancy: dict[Hex, int] = {}
        pieces: dict[int, set[Hex]] = {}
        for seat in seats:
            cells: set[Hex] = set()
            for camp in seat.camps:
                cells |= board.CAMPS[camp]
            pieces[seat.index] = cells
            for cell in cells:
                occupancy[cell] = seat.index
        return cls(
            rules=rules,
            seats=tuple(seats),
            occupancy=occupancy,
            pieces=pieces,
            players=players,
        )

    # -- players vs seats --------------------------------------------------

    def owner_of(self, seat: int) -> int:
        """Which player controls a colour."""
        return self._owner[seat]

    def seats_of(self, player: int) -> tuple[int, ...]:
        return self.players[player].seats

    @property
    def current_player(self) -> PlayerInfo:
        return self.players[self.current]

    def player_cells(self, player: int) -> set[Hex]:
        """Every piece the player may move this turn, across all their colours."""
        cells: set[Hex] = set()
        for seat in self.players[player].seats:
            cells |= self.pieces.get(seat, set())
        return cells

    def apply(self, move: Move) -> int | None:
        """Move the piece along ``move.path`` (only origin/destination change),
        record it, and advance the turn.  Does NOT validate -- ``Game`` does
        that first.  Returns the seat that just finished, if any."""
        player = self.current
        origin = move.origin
        dest = move.destination
        seat = self.occupancy[origin]

        del self.occupancy[origin]
        self.occupancy[dest] = seat
        self.pieces[seat].discard(origin)
        self.pieces[seat].add(dest)

        just_finished = player not in self.rankings and self.has_finished(player)
        if just_finished:
            self.rankings.append(player)
        self.history.append(
            MoveRecord(seat=seat, move=move, finished=just_finished, player=player)
        )

        self.advance_turn()
        return player if just_finished else None

    def undo(self) -> MoveRecord:
        """Exactly invert the last applied move."""
        if not self.history:
            raise RuntimeError("没有可撤销的历史记录。")
        record = self.history.pop()
        seat = record.seat
        origin, dest = record.move.origin, record.move.destination

        del self.occupancy[dest]
        self.occupancy[origin] = seat
        self.pieces[seat].discard(dest)
        self.pieces[seat].add(origin)

        if record.finished:
            assert self.rankings and self.rankings[-1] == record.player
            self.rankings.pop()

        self.current = record.player
        return record

    def advance_turn(self) -> None:
        """Move ``current`` to the next player clockwise that hasn't finished."""
        if self.is_over:
            return
        n = len(self.players)
        nxt = self.current
        for _ in range(n):
            nxt = (nxt + 1) % n
            if nxt not in self.rankings:
                self.current = nxt
                return

    def seat_is_home(self, seat: int) -> bool:
        """``fill_available`` for one colour: its target camp has no empty hole
        and at least one of the pieces in it belongs to this colour.

        The second clause is what keeps this False at game start: a seat's
        target camp starts out completely full of its *opponent's* pieces,
        which alone would satisfy "no empty cell".
        """
        if self.rules.win_condition != "fill_available":
            raise NotImplementedError(self.rules.win_condition)
        for camp in self.seats[seat].targets:
            cells = board.CAMPS[camp]
            if any(cell not in self.occupancy for cell in cells):
                return False
            if not any(self.occupancy[cell] == seat for cell in cells):
                return False
        return True

    def has_finished(self, player: int) -> bool:
        """A player is done when *every* colour they own has reached its own
        target camp -- a three-colour player must bring all three home."""
        return all(self.seat_is_home(seat) for seat in self.players[player].seats)

    @property
    def is_over(self) -> bool:
        """The last remaining player is implicitly last place."""
        return len(self.rankings) >= len(self.players) - 1

    @property
    def final_rankings(self) -> list[int]:
        """``rankings`` plus the trailing unfinished player once the game is over."""
        result = list(self.rankings)
        if self.is_over:
            result.extend(p.index for p in self.players if p.index not in result)
        return result

    def pieces_home_of_seat(self, seat: int) -> int:
        """How many of one colour's pieces sit in that colour's target camp."""
        targets = self.seats[seat].targets
        return sum(
            1 for cell in self.pieces.get(seat, ()) if board.camp_of(cell) in targets
        )

    def pieces_home(self, player: int) -> int:
        """How many of a player's pieces sit in the target camp of their own
        colour (a red piece parked in the blue target does not count)."""
        return sum(
            self.pieces_home_of_seat(seat) for seat in self.players[player].seats
        )

    def pieces_total(self, player: int) -> int:
        """Denominator for the progress display: 10 per colour owned."""
        return sum(len(self.pieces.get(seat, ())) for seat in self.players[player].seats)

    def to_dict(self) -> dict:
        """JSON-safe round-trippable snapshot (cells become ``[x, y, z]``)."""
        return {
            "rules": {
                "jump_rule": self.rules.jump_rule,
                "home_lock": self.rules.home_lock,
                "no_stop_in_foreign_camp": self.rules.no_stop_in_foreign_camp,
                "win_condition": self.rules.win_condition,
                "stall_limit": self.rules.stall_limit,
            },
            "seats": [
                {
                    "index": s.index,
                    "name": s.name,
                    "color": s.color,
                    "camps": list(s.camps),
                    "targets": list(s.targets),
                }
                for s in self.seats
            ],
            "occupancy": [[list(cell), seat] for cell, seat in self.occupancy.items()],
            "pieces": {
                str(seat): [list(cell) for cell in cells] for seat, cells in self.pieces.items()
            },
            "players": [
                {"index": p.index, "name": p.name, "seats": list(p.seats)}
                for p in self.players
            ],
            "current": self.current,
            "rankings": list(self.rankings),
            "history": [
                {
                    "seat": r.seat,
                    "move": {
                        "origin": list(r.move.origin),
                        "path": [list(c) for c in r.move.path],
                        "kind": r.move.kind,
                    },
                    "finished": r.finished,
                    "player": r.player,
                }
                for r in self.history
            ],
        }

    @classmethod
    def from_dict(cls, data: dict) -> GameState:
        rules = RuleSet(
            jump_rule=data["rules"]["jump_rule"],
            home_lock=data["rules"]["home_lock"],
            no_stop_in_foreign_camp=data["rules"]["no_stop_in_foreign_camp"],
            win_condition=data["rules"]["win_condition"],
            stall_limit=data["rules"]["stall_limit"],
        )
        seats = tuple(
            SeatInfo(
                index=s["index"],
                name=s["name"],
                color=s["color"],
                camps=tuple(s["camps"]),
                targets=tuple(s["targets"]),
            )
            for s in data["seats"]
        )
        occupancy: dict[Hex, int] = {
            tuple(cell): seat for cell, seat in data["occupancy"]  # type: ignore[misc]
        }
        pieces: dict[int, set[Hex]] = {
            int(seat): {tuple(cell) for cell in cells}  # type: ignore[misc]
            for seat, cells in data["pieces"].items()
        }
        history = [
            MoveRecord(
                seat=r["seat"],
                move=Move(
                    origin=tuple(r["move"]["origin"]),
                    path=tuple(tuple(c) for c in r["move"]["path"]),
                    kind=r["move"]["kind"],
                ),
                finished=r["finished"],
                player=r.get("player", r["seat"]),
            )
            for r in data["history"]
        ]
        players = tuple(
            PlayerInfo(index=p["index"], name=p["name"], seats=tuple(p["seats"]))
            for p in data.get("players", ())
        )
        return cls(
            rules=rules,
            seats=seats,
            occupancy=occupancy,
            pieces=pieces,
            current=data["current"],
            rankings=list(data["rankings"]),
            history=history,
            players=players,
        )
