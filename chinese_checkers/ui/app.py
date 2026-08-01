"""The application shell: screen swapping, keyboard, and the two announcements.

Everything here is chrome around widgets that already work on their own -- the
menu produces a config, the board owns interaction, the panel owns the
read-out.  The app's only real jobs are (a) deciding which screen is mounted,
(b) turning keystrokes into the same calls the panel's buttons make, so there is
exactly one code path per command, and (c) announcing the two events a player
must not miss: a *player* finishing (all of their colours home, which in the
three-colour mode is three at once), and the game ending.

Key handlers deliberately return ``None`` (rather than ``"break"``) whenever
they are not applicable, which leaves Tk's default behaviour -- notably Tab
traversal between the menu's name fields -- intact.
"""

from __future__ import annotations

import tkinter as tk
from typing import Callable

from ..core.game import Game, MoveResult
from .board_view import BoardView
from .menu import GameConfig, MenuScreen
from .panel import SidePanel, rank_badge
from .theme import DEFAULT, mix, resolved

#: Banner timing: how long it sits still, then how long it takes to fade.
BANNER_HOLD_MS = 1800
BANNER_FADE_STEPS = 14
BANNER_FADE_MS = 50

MIN_SIZE = (1040, 800)


class App(tk.Tk):
    """Root window.  Owns the current game and the mounted screen."""

    def __init__(self) -> None:
        super().__init__()
        self.title("中国跳棋")
        self.minsize(*MIN_SIZE)
        self.geometry("1180x860")
        # ``resolved`` needs a live Tk to ask which CJK faces are installed,
        # so the theme can only be finalised here, not at import time.
        self.theme = resolved(DEFAULT)
        self.configure(background=self.theme.app_bg)

        self.game: Game | None = None
        self.board_view: BoardView | None = None
        self.panel: SidePanel | None = None
        self._screen: tk.Frame | None = None
        self._config: GameConfig | None = None
        self._banner: tk.Label | None = None
        self._banner_bg: str = self.theme.panel_bg
        self._banner_job: str | None = None
        self._overlay: tk.Frame | None = None

        self._bind_keys()
        self.show_menu()

    # ---------------------------------------------------------- screens ----

    def _clear_screen(self) -> None:
        self._cancel_banner()
        self._close_overlay()
        if self._screen is not None:
            self._screen.destroy()
        self._screen = None
        self.board_view = None
        self.panel = None

    def show_menu(self) -> None:
        self._clear_screen()
        self.game = None
        menu = MenuScreen(self, on_start=self.start_game, theme=self.theme)
        menu.pack(fill=tk.BOTH, expand=True)
        self._screen = menu

    def start_game(self, config: GameConfig) -> None:
        self._clear_screen()
        self._config = config
        self.game = Game.new(
            config.player_count,
            names=list(config.names),
            colors=list(config.colors),
            rules=config.rules,
            colors_each=config.colors_each,
        )

        screen = tk.Frame(self, background=self.theme.app_bg)
        screen.pack(fill=tk.BOTH, expand=True)
        self._screen = screen

        self.board_view = BoardView(
            screen,
            self.game,
            on_state_changed=self._on_state_changed,
            on_status=self._on_status,
            theme=self.theme,
        )
        self.board_view.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        self.panel = SidePanel(
            screen,
            self.game,
            on_confirm=self._confirm,
            on_rollback=self._rollback,
            on_cancel=self._cancel,
            on_undo=self._undo,
            theme=self.theme,
        )
        self.panel.pack(side=tk.RIGHT, fill=tk.Y)

        self.panel.refresh(self.game)
        self._on_status(f"轮到{self.game.current_player.name}行棋，点击一颗高亮的棋子。", "info")

    def restart(self) -> None:
        if self._config is not None:
            self.start_game(self._config)

    # -------------------------------------------------------- callbacks ----

    def _on_state_changed(self, result: MoveResult | None = None) -> None:
        if self.game is None:
            return
        if self.panel is not None:
            self.panel.refresh(self.game)
        if result is None:
            return
        if result.finished is not None:
            # ``finished`` is a *player*: in the three-colour mode a person only
            # finishes once all three of their colours are home, so announcing a
            # colour here would claim a win that hasn't happened.
            state = self.game.state
            player = state.players[result.finished]
            rank = state.rankings.index(result.finished) + 1
            what = "三色全部到家" if state.pieces_total(player.index) > 10 else "全部到家"
            self._show_banner(f"{player.name} {what}！第 {rank} 名")
        if result.game_over:
            # Let the banner be readable before the overlay covers the board.
            self.after(BANNER_HOLD_MS, self._show_game_over)

    def _on_status(self, message: str, kind: str = "info") -> None:
        if self.panel is not None:
            self.panel.set_status(message, kind)

    # --------------------------------------------------------- commands ----

    def _confirm(self) -> None:
        if self.board_view is not None:
            self.board_view.confirm_move()

    def _rollback(self) -> None:
        if self.board_view is not None:
            self.board_view.rollback()

    def _cancel(self) -> None:
        if self.board_view is not None:
            self.board_view.cancel()

    def _undo(self) -> None:
        if self.board_view is not None:
            self.board_view.undo()

    # -------------------------------------------------------- keyboard ----

    def _bind_keys(self) -> None:
        self.bind("<Return>", self._key(self._confirm))
        self.bind("<KP_Enter>", self._key(self._confirm))
        self.bind("<BackSpace>", self._key(self._rollback))
        self.bind("<Escape>", self._key(self._cancel))
        self.bind("<Command-z>", self._key(self._undo))
        self.bind("<Control-z>", self._key(self._undo))
        self.bind("<Tab>", self._key(lambda: self.board_view.focus_next_piece()))

    def _key(self, action: Callable[[], None]) -> Callable[[tk.Event], str | None]:
        """Wrap a command so it is a no-op outside the game screen.

        Returning ``None`` there matters: the menu's entries still need Tab and
        BackSpace to behave normally.
        """

        def handler(_event: tk.Event) -> str | None:
            if self.board_view is None or self._overlay is not None:
                return None
            action()
            return "break"

        return handler

    # ---------------------------------------------------- announcements ----

    def _cancel_banner(self) -> None:
        if self._banner_job is not None:
            try:
                self.after_cancel(self._banner_job)
            except tk.TclError:  # already fired
                pass
            self._banner_job = None
        if self._banner is not None:
            self._banner.destroy()
            self._banner = None

    def _show_banner(self, text: str) -> None:
        if self.board_view is None:
            return
        self._cancel_banner()
        theme = self.theme
        self._banner_bg = mix(theme.app_bg, "#FFFFFF", 0.65)
        banner = tk.Label(
            self.board_view,
            text=text,
            font=theme.title_font,
            fg=theme.text_primary,
            background=self._banner_bg,
            padx=26,
            pady=14,
            bd=0,
            highlightthickness=1,
            highlightbackground=mix(theme.app_bg, theme.text_muted, 0.4),
        )
        banner.place(relx=0.5, rely=0.11, anchor="center")
        self._banner = banner
        self._banner_job = self.after(BANNER_HOLD_MS, lambda: self._fade_banner(0))

    def _fade_banner(self, step: int) -> None:
        """Fade by interpolating towards the board background.

        Tk has no widget alpha, so "fading" is a colour ramp: both the text and
        the plate walk to ``app_bg``, which is exactly what sits behind them.
        """
        if self._banner is None:
            return
        if step > BANNER_FADE_STEPS:
            self._cancel_banner()
            return
        t = step / BANNER_FADE_STEPS
        theme = self.theme
        self._banner.configure(
            background=mix(self._banner_bg, theme.app_bg, t),
            fg=mix(theme.text_primary, theme.app_bg, t),
            highlightbackground=mix(
                mix(theme.app_bg, theme.text_muted, 0.4), theme.app_bg, t
            ),
        )
        self._banner_job = self.after(BANNER_FADE_MS, lambda: self._fade_banner(step + 1))

    def _close_overlay(self) -> None:
        if self._overlay is not None:
            self._overlay.destroy()
            self._overlay = None

    def _show_game_over(self) -> None:
        if self.game is None or self.board_view is None or not self.game.is_over:
            return
        self._cancel_banner()
        self._close_overlay()
        theme = self.theme
        state = self.game.state

        overlay = tk.Frame(
            self.board_view,
            background=theme.panel_bg,
            highlightthickness=2,
            highlightbackground=theme.board_edge,
        )
        overlay.place(relx=0.5, rely=0.5, anchor="center")
        self._overlay = overlay

        tk.Label(
            overlay,
            text="本局结束",
            font=theme.title_font,
            fg=theme.text_primary,
            background=theme.panel_bg,
        ).pack(padx=48, pady=(26, 4))
        tk.Label(
            overlay,
            text="最终名次",
            font=theme.small_font,
            fg=theme.text_muted,
            background=theme.panel_bg,
        ).pack(pady=(0, 10))

        # ``final_rankings`` are players; each gets one dot per colour they ran,
        # which is also what tells the two three-colour players apart.
        for place, index in enumerate(state.final_rankings, start=1):
            player = state.players[index]
            row = tk.Frame(overlay, background=theme.panel_bg)
            row.pack(fill=tk.X, padx=40, pady=2)
            for seat_index in player.seats:
                color = state.seats[seat_index].color
                dot = tk.Canvas(
                    row, width=12, height=12, highlightthickness=0, bd=0,
                    background=theme.panel_bg,
                )
                dot.pack(side=tk.LEFT, padx=(0, 4))
                dot.create_oval(
                    1, 1, 11, 11, fill=color, outline=mix(color, "#000000", 0.35)
                )
            row.pack_slaves()[-1].pack_configure(padx=(0, 10))
            tk.Label(
                row,
                text=rank_badge(place),
                font=theme.ui_font,
                fg=theme.text_primary,
                background=theme.panel_bg,
                width=8,
                anchor="w",
            ).pack(side=tk.LEFT)
            tk.Label(
                row,
                text=player.name,
                font=theme.ui_font,
                fg=theme.text_primary,
                background=theme.panel_bg,
                anchor="w",
            ).pack(side=tk.LEFT)

        buttons = tk.Frame(overlay, background=theme.panel_bg)
        buttons.pack(pady=(20, 26))
        tk.Button(
            buttons,
            text="再来一局",
            font=theme.ui_font,
            command=self.restart,
            fg=theme.text_primary,
            bg=mix(theme.panel_bg, theme.board_fill, 0.52),
            activeforeground=theme.text_primary,
            activebackground=mix(theme.panel_bg, theme.board_edge, 0.20),
            relief=tk.FLAT,
            bd=0,
            padx=14,
            pady=7,
            highlightbackground=theme.panel_bg,
            default=tk.ACTIVE,
        ).pack(side=tk.LEFT, padx=6)
        tk.Button(
            buttons,
            text="返回菜单",
            font=theme.ui_font,
            command=self.show_menu,
            fg=theme.text_primary,
            bg=mix(theme.panel_bg, theme.board_fill, 0.52),
            activeforeground=theme.text_primary,
            activebackground=mix(theme.panel_bg, theme.board_edge, 0.20),
            relief=tk.FLAT,
            bd=0,
            padx=14,
            pady=7,
            highlightbackground=theme.panel_bg,
        ).pack(side=tk.LEFT, padx=6)

    # ------------------------------------------------------------- main ----

    def run(self) -> None:
        self.mainloop()


def run() -> None:
    """Entry point used by ``python3 -m chinese_checkers``."""
    App().run()


main = run
