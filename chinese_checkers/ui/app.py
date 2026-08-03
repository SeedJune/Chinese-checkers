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

The app is also where a bot takes its turn, for the same reason it owns the
screen swap: nothing below it should have to know that a player might not be a
person.  The search runs on a worker thread over its own copy of the position
and posts the move back through a queue that the main thread polls, because Tk
is not safe to touch from anywhere but the thread that owns the loop.  The move
itself is then played through exactly the same builder calls a click would
make, so the animation, the banner and the panel need no bot-specific path.
"""

from __future__ import annotations

import queue
import threading
import time
import tkinter as tk
from typing import Callable

from ..agents import Agent, LEVEL_NAMES, detached, make_agent
from ..core.game import Game, MoveResult
from ..core.rules import IllegalMove, Move
from .board_view import BoardView
from .menu import GameConfig, MenuScreen
from .panel import SidePanel, rank_badge
from .theme import DEFAULT, mix, resolved

#: Banner timing: how long it sits still, then how long it takes to fade.
BANNER_HOLD_MS = 1800
BANNER_FADE_STEPS = 14
BANNER_FADE_MS = 50

#: How often the main thread checks whether the bot has finished thinking.
BOT_POLL_MS = 40

#: How long the chosen route is shown before the marble actually sets off, so
#: the move can be read rather than merely noticed after the fact.
BOT_REVEAL_MS = 220

#: Floor on a bot's visible thinking time.  The easy level answers in under a
#: millisecond, and a piece that teleports the instant you release the mouse
#: reads as a glitch rather than as a reply.
BOT_MIN_THINK_MS = 320

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

        #: Player index -> the bot playing that seat.  Empty for a hot-seat game.
        self.agents: dict[int, Agent] = {}
        self._bot_queue: queue.Queue = queue.Queue()
        # Bumped whenever the position a search was started from stops being
        # the live one.  A worker thread cannot be stopped, so it is disowned:
        # its result arrives, fails the generation check, and is dropped.
        self._bot_generation = 0
        self._bot_thinking = False
        self._bot_job: str | None = None
        self._bot_started = 0.0
        self._undoing = False

        self._bind_keys()
        self.show_menu()

    # ---------------------------------------------------------- screens ----

    def _clear_screen(self) -> None:
        self._cancel_bot()
        self._cancel_banner()
        self._close_overlay()
        if self._screen is not None:
            self._screen.destroy()
        self._screen = None
        self.board_view = None
        self.panel = None

    def show_menu(self) -> None:
        self._clear_screen()
        self.agents = {}
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
        # ``bots`` is empty on a config built before the menu grew the choice,
        # which is exactly the "everybody is human" case.
        self.agents = {
            index: make_agent(level)
            for index, level in enumerate(config.bots or ())
            if level is not None and index < config.player_count
        }

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
            bots={
                index: LEVEL_NAMES.get(level, level)
                for index, level in enumerate(config.bots or ())
                if level is not None and index < config.player_count
            },
        )
        self.panel.pack(side=tk.RIGHT, fill=tk.Y)

        self.panel.refresh(self.game)
        if self.game.state.current not in self.agents:
            self._on_status(
                f"轮到{self.game.current_player.name}行棋，点击一颗高亮的棋子。", "info"
            )
        self._maybe_start_bot_turn()

    def restart(self) -> None:
        if self._config is not None:
            self.start_game(self._config)

    # -------------------------------------------------------- callbacks ----

    def _on_state_changed(self, result: MoveResult | None = None) -> None:
        if self.game is None:
            return
        if self.panel is not None:
            self.panel.refresh(self.game, locked=self._locked)
        if result is None:
            # Every redraw lands here, which is precisely why the bot is
            # started from here too: no caller has to remember to do it.
            self._maybe_start_bot_turn()
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
            return
        if self.game.state.current not in self.agents:
            self._on_status(
                f"轮到{self.game.current_player.name}行棋，点击一颗高亮的棋子。", "info"
            )
        self._maybe_start_bot_turn()

    def _on_status(self, message: str, kind: str = "info") -> None:
        if self.panel is not None:
            self.panel.set_status(message, kind)

    # -------------------------------------------------------- bot turns ----

    @property
    def _locked(self) -> bool:
        return self.board_view is not None and self.board_view.locked

    def _cancel_bot(self) -> None:
        """Disown any search in flight and drop whatever it eventually posts.

        A running thread cannot be stopped, but it does not need to be: it
        works on its own copy of the position and can only ever hand the result
        back through the queue, where the generation check throws it away.
        """
        self._bot_generation += 1
        self._bot_thinking = False
        if self._bot_job is not None:
            try:
                self.after_cancel(self._bot_job)
            except tk.TclError:  # already fired
                pass
            self._bot_job = None
        if self.board_view is not None:
            self.board_view.locked = False

    def _maybe_start_bot_turn(self) -> None:
        """Set the bot thinking if the turn is its own and nothing is in the way.

        Idempotent and cheap by design, so every place the game might have
        changed hands can just call it without knowing whether a bot is even
        playing.
        """
        game = self.game
        view = self.board_view
        if game is None or view is None or game.is_over:
            return
        if self._bot_thinking or self._undoing or view._animating:
            return
        agent = self.agents.get(game.state.current)
        if agent is None:
            return

        game.cancel()  # a half-built move from before an undo must not linger
        self._bot_thinking = True
        self._bot_started = time.monotonic()
        view.locked = True
        self._on_status(f"{game.current_player.name}思考中…", "info")
        view.refresh()

        generation = self._bot_generation
        # Copied here, on the main thread, so the worker never reads a position
        # the UI could still be mutating.
        threading.Thread(
            target=self._think,
            args=(generation, agent, detached(game)),
            daemon=True,
        ).start()
        self._bot_job = self.after(BOT_POLL_MS, self._poll_bot)

    def _think(self, generation: int, agent: Agent, game: Game) -> None:
        """Worker thread.  Must not touch Tk or the live game from in here."""
        try:
            move = agent.select_move(game)
        except Exception as exc:  # a bot bug must not take the window with it
            self._bot_queue.put((generation, None, exc))
        else:
            self._bot_queue.put((generation, move, None))

    def _poll_bot(self) -> None:
        self._bot_job = None
        try:
            generation, move, error = self._bot_queue.get_nowait()
        except queue.Empty:
            self._bot_job = self.after(BOT_POLL_MS, self._poll_bot)
            return
        if generation != self._bot_generation or self.board_view is None:
            return  # belongs to a game that no longer exists
        if error is not None or move is None:
            self._bot_thinking = False
            self.board_view.locked = False
            self._on_status(f"电脑走子失败：{error}", "error")
            self.board_view.refresh()
            return
        waited = time.monotonic() - self._bot_started
        delay = max(1, int((BOT_MIN_THINK_MS / 1000.0 - waited) * 1000))
        self._bot_job = self.after(delay, lambda: self._play_bot_move(generation, move))

    def _play_bot_move(self, generation: int, move: Move) -> None:
        """Build the bot's move with the very calls a click would make."""
        self._bot_job = None
        game = self.game
        if generation != self._bot_generation or game is None or self.board_view is None:
            return
        try:
            game.cancel()
            game.select(move.origin)
            for cell in move.path:
                game.extend(cell)
        except IllegalMove as exc:
            game.cancel()
            self._bot_thinking = False
            self.board_view.locked = False
            self._on_status(f"电脑给出了不合法的走法：{exc}", "error")
            self.board_view.refresh()
            return
        self.board_view.refresh()  # the route is drawn even while locked
        self._bot_job = self.after(
            BOT_REVEAL_MS, lambda: self._commit_bot_move(generation)
        )

    def _commit_bot_move(self, generation: int) -> None:
        self._bot_job = None
        if generation != self._bot_generation or self.board_view is None:
            return
        # Released before the flight: ``_animating`` holds the board for the
        # animation, and whoever moves next decides whether it locks again.
        self.board_view.locked = False
        # ``_bot_thinking`` stays set across the confirm and is only dropped
        # afterwards.  Confirming repaints, and every repaint runs back through
        # ``_maybe_start_bot_turn``; clearing the flag first would let that
        # re-entrant call start a second search on a turn already being played.
        self.board_view.confirm_move()
        self._bot_thinking = False

    # --------------------------------------------------------- commands ----

    def _confirm(self) -> None:
        if self.board_view is not None and not self._locked:
            self.board_view.confirm_move()

    def _rollback(self) -> None:
        if self.board_view is not None:
            self.board_view.rollback()

    def _cancel(self) -> None:
        if self.board_view is not None:
            self.board_view.cancel()

    def _undo(self) -> None:
        """Take back a move -- and, against a bot, its reply as well.

        Undoing a single ply would just hand the bot the same turn back, so it
        would instantly play again and nothing would appear to happen.  Walking
        back to the nearest human turn is what "take that back" actually means
        here.  It doubles as the way to interrupt a bot you no longer want to
        wait for, which is why the undo button stays enabled while it thinks.
        """
        view = self.board_view
        if view is None or self.game is None:
            return
        self._cancel_bot()
        self._undoing = True
        try:
            view.undo()
            if any(p.index not in self.agents for p in self.game.state.players):
                while self.game.state.current in self.agents and self.game.can_undo:
                    view.undo()
        finally:
            self._undoing = False
        view.refresh()

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
