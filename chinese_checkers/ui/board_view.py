"""The interactive board widget: where mouse input becomes game commands.

This is the only place in the UI that turns a click into a rules decision, and
even here it never *makes* one -- it hands the cell to :class:`~..core.game.Game`
and lets the engine accept it or raise.  Which command to try is chosen purely
from ``game.phase``, so the widget stays correct when the rules change: a cell
that used to be a legal landing simply stops appearing in ``game.options()``.

The one piece of state the widget owns beyond the hover cell is the confirm
animation.  While a marble is flying the board is input-locked (``_animating``),
because the engine has already applied the move and any click resolved against
the new position would contradict what the player can still see.
"""

from __future__ import annotations

import time
import tkinter as tk
from typing import Callable

from ..core.coords import Hex
from ..core.game import Game, MoveResult, Phase
from ..core.rules import IllegalMove, Move
from .render import BoardRenderer, Scene
from .theme import DEFAULT, Theme

#: ``on_status(message, kind)`` where kind is "info" | "error" | "success".
StatusCallback = Callable[[str, str], None]

#: ``on_state_changed(result)`` -- ``None`` for an ordinary redraw-worthy
#: change, a :class:`MoveResult` once a confirmed move has finished animating.
StateCallback = Callable[["MoveResult | None"], None]

#: Milliseconds per hop of the confirm animation, and the frame interval.
#: Timing is read from the clock rather than counted in frames so a slow redraw
#: shortens the animation instead of stretching it.
HOP_MS = 130
FRAME_MS = 16


def _ease(t: float) -> float:
    """Smoothstep: zero velocity at both ends of every hop."""
    return t * t * (3.0 - 2.0 * t)


class BoardView(tk.Canvas):
    """Canvas that draws the live game and routes clicks into ``Game``."""

    def __init__(
        self,
        master: tk.Misc,
        game: Game,
        on_state_changed: StateCallback,
        on_status: StatusCallback,
        theme: Theme = DEFAULT,
    ) -> None:
        super().__init__(master, highlightthickness=0, bd=0, background=theme.app_bg)
        self.game = game
        self.theme = theme
        self.on_state_changed = on_state_changed
        self.on_status = on_status
        self.renderer = BoardRenderer(self, theme)

        self._hover: Hex | None = None
        self._cursor: str = ""
        self._animating = False
        self._layout_size: tuple[int, int] = (0, 0)
        # (seat, cell hidden from occupancy, canvas_x, canvas_y)
        self._float: tuple[int, Hex, float, float] | None = None
        self._anim_job: str | None = None

        self.bind("<Configure>", self._on_configure)
        self.bind("<Motion>", self._on_motion)
        self.bind("<Leave>", self._on_leave)
        self.bind("<Button-1>", self._on_click)

    # ------------------------------------------------------------ scene ----

    def build_scene(self) -> Scene:
        """Snapshot the game as a :class:`Scene`.

        Every overlay that invites a click is suppressed mid-animation: the
        board is locked then, and highlighting the *next* player's pieces while
        the previous player's marble is still in the air reads as an invitation
        the widget would refuse.
        """
        game = self.game
        state = game.state
        occupancy = dict(state.occupancy)
        floating: tuple[int, float, float] | None = None
        if self._float is not None:
            seat, hidden, x, y = self._float
            occupancy.pop(hidden, None)
            floating = (seat, x, y)

        quiet = self._animating or game.is_over
        opts = (
            {"steps": frozenset(), "jumps": frozenset()} if quiet else game.options()
        )
        last = state.history[-1] if state.history else None
        return Scene(
            occupancy=occupancy,
            colors=tuple(seat.color for seat in state.seats),
            # Both of these are *colour* indices, never player indices: the
            # scene is drawn per marble, and a three-colour player's overlays
            # have to follow the marble in hand rather than the person.
            target_camp=game.active_seat.targets[0],
            selectable=frozenset() if quiet else game.selectable(),
            selected=game.path[0] if game.path else None,
            step_targets=opts["steps"],
            jump_targets=opts["jumps"],
            path=game.path,
            hover=None if quiet else self._hover,
            last_move=(last.move.origin, last.move.destination) if last else None,
            floating=floating,
            # ``rankings`` holds players, so a finished three-colour player has
            # to grey out all three of their colours.
            dim_seats=frozenset(s for p in state.rankings for s in state.seats_of(p)),
            active_seat=game.active_seat.index,
        )

    def ensure_layout(self) -> None:
        """Fit the renderer to the widget's current size if it hasn't been.

        ``<Configure>`` is the normal trigger, but it never fires for a canvas
        inside a window that was never mapped -- which is exactly how the
        headless QA harness drives this widget.  So every entry point that
        needs pixel geometry asks for a layout rather than assuming one
        happened; it is a no-op once the size is known and unchanged.
        """
        width, height = self.winfo_width(), self.winfo_height()
        if width > 1 and height > 1 and (width, height) != self._layout_size:
            self._layout_size = (width, height)
            self.renderer.layout(width, height)

    def _redraw(self) -> None:
        self.ensure_layout()
        self.renderer.draw(self.build_scene())

    def refresh(self) -> None:
        """Repaint and tell the app to restyle everything around the board."""
        self._redraw()
        self.on_state_changed(None)

    # ------------------------------------------------------------ events ----

    def _on_configure(self, event: tk.Event) -> None:
        self._layout_size = (event.width, event.height)
        self.renderer.layout(event.width, event.height)
        self._redraw()

    def _actionable(self) -> frozenset[Hex]:
        """Cells a click would currently do something with.

        Kept in sync with :meth:`_on_click` by construction -- it is the same
        set of questions asked of the game, just without mutating anything.
        """
        game = self.game
        if self._animating or game.is_over:
            return frozenset()
        if game.phase is Phase.IDLE:
            return game.selectable()
        opts = game.options()
        return opts["steps"] | opts["jumps"] | game.selectable() | {game.path[0]}

    def _set_cursor(self, name: str) -> None:
        if name != self._cursor:
            self._cursor = name
            self.configure(cursor=name)

    def _on_motion(self, event: tk.Event) -> None:
        self.ensure_layout()
        cell = self.renderer.cell_at(event.x, event.y)
        if cell is not None and cell not in self._actionable():
            cell = None
        self._set_cursor("hand2" if cell is not None else "")
        if cell != self._hover:
            self._hover = cell
            self._redraw()

    def _on_leave(self, _event: tk.Event) -> None:
        self._set_cursor("")
        if self._hover is not None:
            self._hover = None
            self._redraw()

    def _on_click(self, event: tk.Event) -> None:
        game = self.game
        if self._animating or game.is_over:
            return
        self.ensure_layout()
        cell = self.renderer.cell_at(event.x, event.y)
        if cell is None:
            return
        try:
            if game.phase is Phase.IDLE:
                game.select(cell)
                self.on_status("已选中棋子，点击高亮位置落子。", "info")
            else:
                self._click_while_building(cell)
        except IllegalMove as exc:
            # The engine's message is already written for a player to read.
            self.on_status(str(exc), "error")
        self._hover = None
        self.refresh()

    def _click_while_building(self, cell: Hex) -> None:
        """Second and later clicks of a move, in SELECTED/CHAINING/STEPPED."""
        game = self.game
        opts = game.options()
        if cell in opts["steps"] or cell in opts["jumps"]:
            game.extend(cell)
            if game.phase is Phase.CHAINING:
                self.on_status("可以继续跳跃，或按 Enter 确认。", "info")
            else:
                self.on_status("按 Enter 确认走子。", "info")
            return
        if cell == game.path[0]:
            game.cancel()
            self.on_status("已取消选择。", "info")
            return
        if cell in game.selectable():
            # Switching pieces mid-build is a normal thing to want; do it in
            # one click instead of demanding an explicit cancel first.
            game.cancel()
            game.select(cell)
            self.on_status("已改选棋子，点击高亮位置落子。", "info")
            return
        game.extend(cell)  # guaranteed to raise, with the engine's wording

    # --------------------------------------------------------- commands ----

    def confirm_move(self) -> None:
        game = self.game
        if self._animating:
            return
        try:
            result = game.confirm()
        except IllegalMove as exc:
            self.on_status(str(exc), "error")
            self._redraw()
            return
        self._hover = None
        # Lock before the first repaint so the panel disables its buttons in
        # the same frame the marble starts moving.
        self._animating = True
        self.on_state_changed(None)

        def done() -> None:
            self._redraw()
            self.on_state_changed(result)

        self.animate_move(result.move, done)

    def rollback(self) -> None:
        if self._animating:
            return
        try:
            self.game.rollback()
        except IllegalMove as exc:
            self.on_status(str(exc), "error")
            return
        self.on_status("已回退一跳。", "info")
        self._hover = None
        self.refresh()

    def cancel(self) -> None:
        if self._animating or self.game.phase is Phase.IDLE:
            return
        self.game.cancel()
        self.on_status("已取消选择。", "info")
        self._hover = None
        self.refresh()

    def undo(self) -> None:
        if self._animating:
            return
        if not self.game.can_undo:
            self.on_status("没有可以悔棋的走法。", "info")
            return
        self.game.undo_last_move()
        self.on_status(f"已悔棋，退回{self.game.current_player.name}的回合。", "info")
        self._hover = None
        self.refresh()

    def focus_next_piece(self) -> None:
        """Tab: walk the movable pieces in a stable top-to-bottom order."""
        game = self.game
        if self._animating or game.is_over:
            return
        if game.phase in (Phase.CHAINING, Phase.STEPPED):
            return  # a half-built move is in progress; don't discard it silently
        cells = sorted(game.selectable(), key=lambda c: (c[2], c[0]))
        if not cells:
            return
        if game.phase is Phase.IDLE:
            target = cells[0]
        else:
            current = game.path[0]
            index = cells.index(current) if current in cells else -1
            target = cells[(index + 1) % len(cells)]
            game.cancel()
        try:
            game.select(target)
        except IllegalMove as exc:
            self.on_status(str(exc), "error")
        else:
            self.on_status("已选中棋子，点击高亮位置落子。", "info")
        self._hover = None
        self.refresh()

    # -------------------------------------------------------- animation ----

    def animate_move(self, move: Move, done_callback: Callable[[], None]) -> None:
        """Walk the marble along ``move`` and unlock input in ``done_callback``.

        The move is already applied when this runs, so the piece is hidden at
        its *destination* and redrawn as ``Scene.floating`` instead.  Cell
        centres are recomputed every frame, which keeps the animation correct
        if the window is resized mid-flight.
        """
        cells = (move.origin, *move.path)
        seat = self.game.state.occupancy.get(move.destination)
        if seat is None or len(cells) < 2:
            done_callback()
            return

        self._animating = True
        self._set_cursor("")
        self.ensure_layout()
        hop = HOP_MS / 1000.0
        total = hop * (len(cells) - 1)
        started = time.monotonic()

        def tick() -> None:
            elapsed = time.monotonic() - started
            if elapsed >= total:
                self._anim_job = None
                self._float = None
                self._animating = False
                done_callback()
                return
            progress = elapsed / hop
            i = min(int(progress), len(cells) - 2)
            t = _ease(progress - i)
            x0, y0 = self.renderer.center_of(cells[i])
            x1, y1 = self.renderer.center_of(cells[i + 1])
            self._float = (
                seat,
                move.destination,
                x0 + (x1 - x0) * t,
                y0 + (y1 - y0) * t,
            )
            self._redraw()
            self._anim_job = self.after(FRAME_MS, tick)

        tick()
