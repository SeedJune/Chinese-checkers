"""The side panel: everything about the game that isn't the board itself.

The panel is a pure read-out.  It never calls a command on the game -- the
buttons hand their clicks straight back to the app, which forwards them to the
:class:`~.board_view.BoardView` so that keyboard and mouse take exactly the same
path.  Enablement is likewise derived from the game's own predicates
(``can_confirm``, ``can_undo``, ``phase``) rather than from a mirrored copy of
the interaction state, so a button can never offer something the engine would
refuse.

Widgets are built once and merely restyled in :meth:`SidePanel.refresh`;
rebuilding a dozen labels on every click makes Tk flicker on macOS.
"""

from __future__ import annotations

import tkinter as tk
from dataclasses import dataclass
from typing import Callable

from ..core.board import PIECES_PER_CAMP
from ..core.game import Game, Phase
from ..core.state import GameState
from .theme import DEFAULT, Theme, mix

#: Panel width in pixels.  Fixed, so the board gets every other pixel.
PANEL_WIDTH = 270

_STATUS_COLORS = {"info": "text_primary", "error": "text_danger", "success": "text_success"}

#: 🥇/🥈/🥉 for the podium, plain text below it.
_MEDALS = ("🥇", "🥈", "🥉")

_CIRCLED = "①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮⑯⑰⑱⑲⑳"


def rank_badge(rank: int) -> str:
    """``1`` -> ``"🥇第1名"``, ``4`` -> ``"第4名"``."""
    medal = _MEDALS[rank - 1] if rank <= len(_MEDALS) else ""
    return f"{medal}第{rank}名"


def _circled(n: int) -> str:
    return _CIRCLED[n - 1] if 1 <= n <= len(_CIRCLED) else f"({n})"


def route_text(game: Game) -> str:
    """The 路线 read-out for the move currently being built."""
    path = game.path
    if not path:
        return "未选择棋子"
    landings = path[1:]
    if not landings:
        return "路线：起点（请选择落点）"
    marks = " → ".join(_circled(i) for i in range(1, len(landings) + 1))
    suffix = "单步" if game.phase is Phase.STEPPED else f"{len(landings)} 跳"
    return f"路线：起点 → {marks}（{suffix}）"


def _dot(parent: tk.Misc, size: int, background: str) -> tk.Canvas:
    """A colour chip; recoloured later via :func:`_paint_dot`."""
    canvas = tk.Canvas(
        parent,
        width=size,
        height=size,
        highlightthickness=0,
        bd=0,
        background=background,
    )
    return canvas


def _paint_dot(canvas: tk.Canvas, color: str, background: str, dim: bool = False) -> None:
    size = int(canvas["width"])
    canvas.delete("all")
    canvas.configure(background=background)
    fill = mix(color, background, 0.55) if dim else color
    pad = 1
    canvas.create_oval(
        pad, pad, size - pad, size - pad, fill=fill, outline=mix(fill, "#000000", 0.35)
    )


@dataclass(frozen=True)
class _ColorRow:
    """The per-colour line under a multi-colour player's row."""

    seat: int
    dot: tk.Canvas
    label: tk.Label


@dataclass(frozen=True)
class _PlayerRow:
    """Widgets of one player's row; ``dots`` holds one chip per colour owned."""

    frame: tk.Frame
    dots: list[tk.Canvas]
    name: tk.Label
    info: tk.Label
    colors: list[_ColorRow]


class SidePanel(tk.Frame):
    """Turn indicator, player table, route read-out, controls and status line."""

    def __init__(
        self,
        master: tk.Misc,
        game: Game,
        on_confirm: Callable[[], None],
        on_rollback: Callable[[], None],
        on_cancel: Callable[[], None],
        on_undo: Callable[[], None],
        theme: Theme = DEFAULT,
    ) -> None:
        super().__init__(master, width=PANEL_WIDTH, background=theme.panel_bg)
        self.theme = theme
        # Without this the frame shrinks to fit its children and the fixed
        # width is silently ignored.
        self.pack_propagate(False)

        pad = 16
        self._build_header(game, pad)
        self._build_player_rows(game, pad)
        self._build_route(pad)
        self._build_buttons(pad, on_confirm, on_rollback, on_cancel, on_undo)
        self._build_status(pad)

        self.refresh(game)

    # ----------------------------------------------------------- layout ----

    def _build_header(self, game: Game, pad: int) -> None:
        theme = self.theme
        head = tk.Frame(self, background=theme.panel_bg)
        head.pack(fill=tk.X, padx=pad, pady=(pad, 4))
        tk.Label(
            head,
            text="当前回合",
            font=theme.small_font,
            fg=theme.text_muted,
            background=theme.panel_bg,
            anchor="w",
        ).pack(fill=tk.X)

        row = tk.Frame(head, background=theme.panel_bg)
        row.pack(fill=tk.X, pady=(4, 0))
        # One dot per colour the side to move runs.  Every player owns the same
        # number of colours, so the widgets can be built once and only repainted.
        self._turn_dots = [
            _dot(row, 16, theme.panel_bg) for _ in game.state.seats_of(0)
        ]
        for i, dot in enumerate(self._turn_dots):
            dot.pack(side=tk.LEFT, padx=(0, 8 if i == len(self._turn_dots) - 1 else 3))
        self._turn_name = tk.Label(
            row,
            text="",
            font=(theme.font_family, theme.title_font_size - 4, "bold"),
            fg=theme.text_primary,
            background=theme.panel_bg,
            anchor="w",
        )
        self._turn_name.pack(side=tk.LEFT)

    def _separator(self, pad: int) -> None:
        theme = self.theme
        line = tk.Frame(self, height=1, background=mix(theme.panel_bg, theme.text_muted, 0.35))
        line.pack(fill=tk.X, padx=pad, pady=8)

    def _build_player_rows(self, game: Game, pad: int) -> None:
        """One row per *person*, plus a per-colour breakdown when they run
        several colours -- a three-colour player has to bring all three home,
        so "which colour is behind" is the number that decides what to do next.
        """
        theme = self.theme
        state = game.state
        self._separator(pad)
        tk.Label(
            self,
            text="玩家",
            font=theme.small_font,
            fg=theme.text_muted,
            background=theme.panel_bg,
            anchor="w",
        ).pack(fill=tk.X, padx=pad)

        self._player_rows: list[_PlayerRow] = []
        for player in state.players:
            row = tk.Frame(self, background=theme.panel_bg)
            row.pack(fill=tk.X, padx=pad - 6, pady=(2, 0))
            dots: list[tk.Canvas] = []
            for i, _seat in enumerate(player.seats):
                dot = _dot(row, 12, theme.panel_bg)
                dot.pack(side=tk.LEFT, padx=(6 if i == 0 else 2, 0))
                dots.append(dot)
            name = tk.Label(
                row,
                text=player.name,
                font=theme.ui_font,
                fg=theme.text_primary,
                background=theme.panel_bg,
                anchor="w",
            )
            name.pack(side=tk.LEFT, padx=(8, 0))
            info = tk.Label(
                row,
                text="",
                font=theme.small_font,
                fg=theme.text_muted,
                background=theme.panel_bg,
                anchor="e",
            )
            info.pack(side=tk.RIGHT, padx=(0, 6))

            colors: list[_ColorRow] = []
            if len(player.seats) > 1:
                for seat_index in player.seats:
                    line = tk.Frame(self, background=theme.panel_bg)
                    line.pack(fill=tk.X, padx=pad + 8)
                    seat_dot = _dot(line, 9, theme.panel_bg)
                    seat_dot.pack(side=tk.LEFT, padx=(0, 6))
                    label = tk.Label(
                        line,
                        text="",
                        font=theme.small_font,
                        fg=theme.text_muted,
                        background=theme.panel_bg,
                        anchor="w",
                    )
                    label.pack(side=tk.LEFT)
                    colors.append(_ColorRow(seat=seat_index, dot=seat_dot, label=label))
            self._player_rows.append(
                _PlayerRow(dots=dots, frame=row, name=name, info=info, colors=colors)
            )

    def _build_route(self, pad: int) -> None:
        theme = self.theme
        self._separator(pad)
        self._route = tk.Label(
            self,
            text="",
            font=theme.ui_font,
            fg=theme.text_primary,
            background=theme.panel_bg,
            anchor="w",
            justify=tk.LEFT,
            wraplength=PANEL_WIDTH - 2 * pad,
        )
        self._route.pack(fill=tk.X, padx=pad)

    def _build_buttons(
        self,
        pad: int,
        on_confirm: Callable[[], None],
        on_rollback: Callable[[], None],
        on_cancel: Callable[[], None],
        on_undo: Callable[[], None],
    ) -> None:
        theme = self.theme
        self._separator(pad)
        box = tk.Frame(self, background=theme.panel_bg)
        box.pack(fill=tk.X, padx=pad)

        specs = (
            ("confirm", "确认走子 (Enter)", on_confirm),
            ("rollback", "回退一跳 (⌫)", on_rollback),
            ("cancel", "取消选择 (Esc)", on_cancel),
            ("undo", "悔棋 (⌘Z)", on_undo),
        )
        self.buttons: dict[str, tk.Button] = {}
        for key, label, command in specs:
            button = tk.Button(
                box,
                text=label,
                font=theme.ui_font,
                command=command,
                # macOS draws the button itself; ``highlightbackground`` is the
                # only knob that keeps the surrounding pixels off-white.
                highlightbackground=theme.panel_bg,
                default=tk.ACTIVE if key == "confirm" else tk.NORMAL,
            )
            button.pack(fill=tk.X, pady=3)
            self.buttons[key] = button

    def _build_status(self, pad: int) -> None:
        theme = self.theme
        self._status = tk.Label(
            self,
            text="",
            font=theme.small_font,
            fg=theme.text_primary,
            background=theme.panel_bg,
            anchor="w",
            justify=tk.LEFT,
            wraplength=PANEL_WIDTH - 2 * pad,
        )
        self._status.pack(side=tk.BOTTOM, fill=tk.X, padx=pad, pady=pad)

    # ------------------------------------------------------------ update ----

    def set_status(self, message: str, kind: str = "info") -> None:
        color = getattr(self.theme, _STATUS_COLORS.get(kind, "text_primary"))
        self._status.configure(text=message, fg=color)

    def refresh(self, game: Game) -> None:
        theme = self.theme
        state = game.state
        current = state.current
        # ``final_rankings`` folds in the trailing player once the game is over,
        # so last place gets a badge too instead of a stuck progress count.
        ranks = state.final_rankings if state.is_over else state.rankings

        # The header follows the marble in hand, like the board overlays do, so
        # the panel and the board never disagree about which colour is moving.
        for dot, seat in zip(self._turn_dots, game.current_seats):
            _paint_dot(dot, seat.color, theme.panel_bg)
        self._turn_name.configure(
            text="本局结束" if state.is_over else game.current_player.name,
            fg=theme.text_muted if state.is_over else theme.text_primary,
        )

        for player, widgets in zip(state.players, self._player_rows):
            finished = player.index in ranks
            active = player.index == current and not state.is_over
            # Tint with the colour actually in play, not just the first one.
            tint = game.active_seat.color if active else state.seats[player.seats[0]].color
            bg = mix(theme.panel_bg, tint, 0.22) if active else theme.panel_bg
            widgets.frame.configure(background=bg)
            for dot, seat_index in zip(widgets.dots, player.seats):
                _paint_dot(dot, state.seats[seat_index].color, bg, dim=finished)
            widgets.name.configure(
                text=player.name,
                background=bg,
                fg=theme.text_muted if finished else theme.text_primary,
                font=(theme.font_family, theme.ui_font_size, "bold")
                if active
                else theme.ui_font,
            )
            total = state.pieces_total(player.index)
            if finished:
                text = rank_badge(ranks.index(player.index) + 1)
                fg = theme.text_success
            else:
                text = f"已到家 {state.pieces_home(player.index)}/{total}"
                fg = theme.text_primary if active else theme.text_muted
            widgets.info.configure(text=text, background=bg, fg=fg)

            for line in widgets.colors:
                seat = state.seats[line.seat]
                home = state.seat_is_home(line.seat)
                _paint_dot(line.dot, seat.color, theme.panel_bg, dim=finished)
                line.label.configure(
                    text=f"{seat.name}　✓ 已到家"
                    if home
                    else f"{seat.name}　到家 {state.pieces_home_of_seat(line.seat)}/{PIECES_PER_CAMP}",
                    fg=theme.text_success if home else theme.text_muted,
                )

        self._route.configure(text=route_text(game))

        phase = game.phase
        self._enable("confirm", game.can_confirm)
        self._enable("rollback", phase in (Phase.CHAINING, Phase.STEPPED))
        self._enable("cancel", phase is not Phase.IDLE)
        self._enable("undo", game.can_undo)

    def _enable(self, key: str, enabled: bool) -> None:
        self.buttons[key].configure(state=tk.NORMAL if enabled else tk.DISABLED)
