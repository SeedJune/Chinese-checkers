"""Start screen: pick the mode and the table, name the players, choose the rules.

The seating for a given player count is not obvious from a number -- "4 人"
could mean two opposing pairs or four adjacent camps -- so each choice is shown
as a card containing a real miniature of the board drawn by the same
:class:`~.render.BoardRenderer` the game uses.  That also means the previews can
never drift from the actual seating: both read ``board.SEATING`` /
``board.TRIPLE_SEATING``.

The two modes differ only in how many corners one person owns, so the screen
keeps a single notion of "the camps of each player" (:meth:`MenuScreen._camp_groups`)
and builds the player rows and the colour list from it -- one row per person in
either mode, with one swatch per colour they run.

The screen produces a :class:`GameConfig` and hands it to ``on_start``; it never
constructs a :class:`~..core.game.Game` itself, so the app stays in charge of
the screen swap.
"""

from __future__ import annotations

import tkinter as tk
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Sequence

from ..agents import LEVELS
from ..core.board import (
    BALANCED_COUNTS,
    COLOR_NAMES,
    DEFAULT_COLORS,
    SEATING,
    TRIPLE_SEATING,
)
from ..core.rules import RuleSet
from .render import BoardRenderer
from .theme import DEFAULT, Theme, mix

#: Player counts offered, in the order the cards appear.
COUNTS: tuple[int, ...] = (2, 3, 4, 5, 6)

#: ``(key, title, blurb)`` for the two modes, in card order.
MODES: tuple[tuple[str, str, str], ...] = (
    ("classic", "经典对战", "每人一色 · 2-6 人"),
    ("triple", "三色对战", "两人各执三色"),
)

#: The camps of the three-colour mode flattened into seat order, so the preview
#: and the colour list are read off the engine's seating instead of restating it.
TRIPLE_CAMPS: tuple[int, ...] = tuple(camp for camps in TRIPLE_SEATING for camp in camps)

_MINI_SIZE = 68

#: The mode cards sit next to their text instead of above it, so their preview
#: is smaller than the seating cards'.
_MODE_MINI_SIZE = 50

_UNBALANCED_NOTE = "5 人局中，某一方的目标三角无人占据，形势并不对称。"

_TRIPLE_NOTE = "每人执三色，各色分别驶向自己正对面的角；两人轮流走子，一回合只动一颗子。"

_CLASSIC_SUBTITLE = "把自己全部十颗棋子送进对面的三角，最先完成的人获胜。"
_TRIPLE_SUBTITLE = "每人三色、共三十颗棋子，三色全部到家才算完成。"

#: ``(key, label)`` for the seat kinds, in chip order.
SEAT_KINDS: tuple[tuple[str, str], ...] = (("human", "人类"), ("bot", "电脑"))

_BOT_NAME = "电脑"

_BOT_NOTE = "极小化极大搜索 · α-β 剪枝"


@dataclass(frozen=True)
class GameConfig:
    """Everything :meth:`Game.new` needs, as chosen on this screen."""

    player_count: int
    names: tuple[str, ...]
    colors: tuple[str, ...]
    rules: RuleSet
    #: Colours per person: 1 for the ordinary game, 3 for the README's method 2.
    #: ``colors`` therefore has ``player_count * colors_each`` entries.
    colors_each: int = 1
    #: One entry per player: ``None`` for a human, otherwise the difficulty key
    #: of the bot that plays that seat.  Empty means "everyone is human", which
    #: is what a config built without this field means.
    bots: tuple[str | None, ...] = ()


class MenuScreen(tk.Frame):
    """The pre-game setup screen."""

    def __init__(
        self,
        master: tk.Misc,
        on_start: Callable[[GameConfig], None],
        theme: Theme = DEFAULT,
    ) -> None:
        super().__init__(master, background=theme.app_bg)
        self.theme = theme
        self.on_start = on_start

        self._mode = tk.StringVar(value="classic")
        self._count = tk.IntVar(value=3)
        self._home_lock = tk.BooleanVar(value=True)
        self._no_stop = tk.BooleanVar(value=False)
        # Names survive a change of player count -- retyping four names because
        # you flipped from 4 to 5 and back would be maddening.
        self._names: dict[int, tk.StringVar] = {
            i: tk.StringVar(value=f"玩家{i + 1}") for i in range(max(COUNTS))
        }
        # Who plays each seat.  Kept for every possible player index (like the
        # names) so flipping between modes never forgets a choice.
        self._bot_kinds: dict[int, tk.StringVar] = {
            i: tk.StringVar(value="human") for i in range(max(COUNTS))
        }
        self._bot_level = tk.StringVar(value="normal")
        self._cards: dict[int, dict[str, tk.Widget]] = {}
        self._renderers: dict[int, BoardRenderer] = {}
        self._mode_cards: dict[str, dict[str, tk.Widget]] = {}
        self._mode_renderers: dict[str, BoardRenderer] = {}

        backdrop = Path(__file__).resolve().parents[2] / "assets" / "ui" / "guochao-menu-v2.png"
        self._backdrop_image: tk.PhotoImage | None = None
        if backdrop.exists():
            source = tk.PhotoImage(file=str(backdrop))
            zoom = max(1, round(theme.display_scale))
            self._backdrop_image = source.zoom(zoom) if zoom > 1 else source
            tk.Label(
                self,
                image=self._backdrop_image,
                background=theme.app_bg,
                borderwidth=0,
            ).place(relx=0.5, rely=0.5, anchor="center")

        sheet = tk.Frame(
            self,
            background=theme.panel_bg,
            highlightthickness=1,
            highlightbackground=mix(theme.paper_deep, theme.antique_gold, 0.24),
            bd=0,
        )
        sheet.place(relx=0.30, rely=0.5, anchor="center")
        self._sheet = sheet
        body = tk.Frame(sheet, background=theme.panel_bg)
        body.pack(padx=theme.px(24), pady=theme.px(5))
        self._body = body

        self._build_title(body)
        self._build_modes(body)
        self._build_counts(body)
        self._build_players(body)
        self._build_bot_level(body)
        self._build_rules(body)
        self._build_footer(body)

        self._select_mode("classic")

    # ----------------------------------------------------------- layout ----

    def _build_title(self, parent: tk.Misc) -> None:
        theme = self.theme
        title_row = tk.Frame(parent, background=theme.panel_bg)
        title_row.pack()
        tk.Label(
            title_row,
            text="中国跳棋",
            font=(theme.title_font_family, theme.title_font_size + 15),
            fg=theme.ink,
            background=theme.panel_bg,
        ).pack(side=tk.LEFT, pady=(0, 2))
        tk.Label(
            title_row,
            text="弈",
            font=(theme.heading_font_family, theme.small_font_size, "bold"),
            fg=theme.card_bg,
            background=theme.cinnabar,
            padx=theme.px(5),
            pady=theme.px(3),
        ).pack(side=tk.LEFT, padx=(theme.px(10), 0), pady=(theme.px(5), 0))
        self._subtitle = tk.Label(
            parent,
            text=_CLASSIC_SUBTITLE,
            font=theme.small_font,
            fg=theme.text_muted,
            background=theme.panel_bg,
        )
        self._subtitle.pack(pady=(0, 2))
        tk.Label(
            parent,
            text="—  跃星入局  —",
            font=(theme.heading_font_family, theme.small_font_size),
            fg=theme.antique_gold,
            background=theme.panel_bg,
        ).pack(pady=(0, 1))

    def _section(self, parent: tk.Misc, title: str) -> tk.Frame:
        """A titled row.  The title travels with the row inside one box, so a
        whole section can be hidden with a single ``pack_forget``."""
        theme = self.theme
        box = tk.Frame(parent, background=theme.panel_bg)
        box.pack(fill=tk.X)
        tk.Label(
            box,
            text=title,
            font=(theme.heading_font_family, theme.ui_font_size, "bold"),
            fg=theme.ink,
            background=theme.panel_bg,
            anchor="w",
        ).pack(fill=tk.X, pady=(theme.px(5), theme.px(2)))
        frame = tk.Frame(box, background=theme.panel_bg)
        frame.pack(fill=tk.X)
        return frame

    def _card(self, parent: tk.Misc, mini_size: int) -> dict[str, tk.Widget]:
        """A preview card: canvas on top, label under it, selectable border."""
        theme = self.theme
        card = tk.Frame(
            parent,
            background=theme.card_bg,
            highlightthickness=1,
            highlightbackground=theme.paper_deep,
            bd=0,
        )
        card.pack(side=tk.LEFT, padx=theme.px(5))
        mini = tk.Canvas(
            card,
            width=theme.px(mini_size),
            height=theme.px(mini_size),
            highlightthickness=0,
            bd=0,
            background=theme.card_bg,
        )
        mini.pack(
            padx=theme.px(7), pady=(theme.px(5), theme.px(1))
        )
        label = tk.Label(
            card,
            text="",
            font=theme.ui_font,
            fg=theme.text_primary,
            background=theme.card_bg,
        )
        label.pack(pady=(0, 3))
        return {"card": card, "mini": mini, "label": label}

    def _choice_chips(
        self,
        parent: tk.Misc,
        options: Sequence[tuple[str, str]],
        var: tk.StringVar,
        on_change: Callable[[str], None] | None = None,
        side: str = tk.LEFT,
    ) -> dict[str, tk.Button]:
        """A radio group drawn as flat chips.

        Hand-built rather than ``ttk.OptionMenu`` or ``Radiobutton`` because
        the rest of this screen is hand-drawn: a native widget here would be
        the one thing on the menu wearing the desktop's own chrome.
        """
        theme = self.theme
        buttons: dict[str, tk.Button] = {}

        def restyle() -> None:
            for key, button in buttons.items():
                chosen = var.get() == key
                button.configure(
                    background=mix(theme.card_bg, theme.cinnabar, 0.13)
                    if chosen
                    else theme.card_bg,
                    foreground=theme.cinnabar if chosen else theme.text_muted,
                    font=(theme.heading_font_family, theme.small_font_size, "bold")
                    if chosen
                    else theme.small_font,
                )

        def choose(key: str) -> None:
            var.set(key)
            restyle()
            if on_change is not None:
                on_change(key)

        for key, label in options:
            button = tk.Button(
                parent,
                text=label,
                command=lambda k=key: choose(k),
                relief=tk.FLAT,
                bd=0,
                padx=theme.px(9),
                pady=theme.px(3),
                activeforeground=theme.text_primary,
                activebackground=mix(theme.card_bg, theme.cinnabar, 0.20),
                highlightthickness=0,
            )
            button.pack(side=side, padx=theme.px(2))
            buttons[key] = button
        restyle()
        return buttons

    def _build_modes(self, parent: tk.Misc) -> None:
        """The two mode cards.

        Laid out sideways rather than as another column of stacked cards: the
        screen has to stay inside the window's minimum height, and a 6-player
        table already fills most of it.
        """
        theme = self.theme
        row = self._section(parent, "玩法")
        for key, title, blurb in MODES:
            card = tk.Frame(
                row,
                background=theme.card_bg,
                highlightthickness=1,
                highlightbackground=theme.paper_deep,
                bd=0,
            )
            card.pack(side=tk.LEFT, padx=theme.px(5))
            mini = tk.Canvas(
                card,
                width=theme.px(_MODE_MINI_SIZE),
                height=theme.px(_MODE_MINI_SIZE),
                highlightthickness=0,
                bd=0,
                background=theme.card_bg,
            )
            mini.pack(
                side=tk.LEFT,
                padx=(theme.px(7), theme.px(9)),
                pady=theme.px(5),
            )
            text = tk.Frame(card, background=theme.card_bg)
            text.pack(side=tk.LEFT, padx=(0, theme.px(14)))
            label = tk.Label(
                text,
                text=title,
                font=theme.heading_font,
                fg=theme.text_primary,
                background=theme.card_bg,
                anchor="w",
            )
            label.pack(fill=tk.X)
            hint = tk.Label(
                text,
                text=blurb,
                font=theme.small_font,
                fg=theme.text_muted,
                background=theme.card_bg,
                anchor="w",
            )
            hint.pack(fill=tk.X)

            parts = {"card": card, "mini": mini, "label": label, "hint": hint}
            self._mode_renderers[key] = BoardRenderer(mini, theme)
            self._mode_cards[key] = parts
            for widget in (card, mini, text, label, hint):
                widget.bind("<Button-1>", lambda _e, k=key: self._select_mode(k))
            mini.bind("<Configure>", lambda _e, k=key: self._draw_mode_mini(k))

    def _build_counts(self, parent: tk.Misc) -> None:
        row = self._section(parent, "人数与座位")
        # The section box, so the whole row can be hidden in three-colour mode,
        # where the player count is not the player's to choose.
        self._counts_box = row.master
        for count in COUNTS:
            parts = self._card(row, _MINI_SIZE)
            parts["label"].configure(text=f"{count} 人")
            self._renderers[count] = BoardRenderer(parts["mini"], self.theme)
            self._cards[count] = parts
            for widget in (parts["card"], parts["mini"], parts["label"]):
                widget.bind("<Button-1>", lambda _e, n=count: self._select_count(n))
            # The canvas has no real size until it is mapped; redraw then so the
            # preview is laid out against actual pixels rather than the request.
            parts["mini"].bind("<Configure>", lambda _e, n=count: self._draw_mini(n))
            self._draw_mini(count)

    def _draw_mini(self, count: int) -> None:
        self._renderers[count].draw_mini(SEATING[count], DEFAULT_COLORS)

    def _draw_mode_mini(self, key: str) -> None:
        """Each mode card previews the table it would actually deal.

        The classic card follows the chosen player count, so the two cards
        never show the same picture for different rules.
        """
        renderer = self._mode_renderers[key]
        if key == "triple":
            renderer.draw_mini(TRIPLE_CAMPS, DEFAULT_COLORS, groups=TRIPLE_SEATING)
        else:
            count = self._count.get()
            renderer.draw_mini(SEATING[count], DEFAULT_COLORS)

    def _build_players(self, parent: tk.Misc) -> None:
        self._players = self._section(parent, "玩家")
        self._players_box = self._players.master
        self._note = tk.Label(
            parent,
            text="",
            font=self.theme.small_font,
            fg=self.theme.text_muted,
            background=self.theme.panel_bg,
            anchor="w",
            justify=tk.LEFT,
        )
        self._note.pack(fill=tk.X, pady=(6, 0))

    def _build_bot_level(self, parent: tk.Misc) -> None:
        """The difficulty row.  Only shown once somebody is set to 电脑."""
        row = self._section(parent, "电脑难度")
        self._level_box = row.master
        self._choice_chips(row, LEVELS, self._bot_level)
        tk.Label(
            row,
            text=_BOT_NOTE,
            font=self.theme.small_font,
            fg=self.theme.text_muted,
            background=self.theme.panel_bg,
            anchor="w",
        ).pack(side=tk.LEFT, padx=(12, 0))

    def _build_rules(self, parent: tk.Misc) -> None:
        theme = self.theme
        box = self._section(parent, "规则")
        self._rules_box = box.master
        for text, var in (
            ("进家后不得再离开目标三角", self._home_lock),
            ("不得停留在他人的营地", self._no_stop),
        ):
            tk.Checkbutton(
                box,
                text=text,
                variable=var,
                font=theme.ui_font,
                fg=theme.text_primary,
                background=theme.panel_bg,
                activebackground=theme.panel_bg,
                activeforeground=theme.text_primary,
                selectcolor=theme.card_bg,
                highlightthickness=0,
                anchor="w",
            ).pack(fill=tk.X)
        tk.Label(
            box,
            text="跳跃规则：经典邻接跳（隔一子落到紧邻的空位）",
            font=theme.small_font,
            fg=theme.text_muted,
            background=theme.panel_bg,
            anchor="w",
        ).pack(fill=tk.X, pady=(4, 0))

    def _build_footer(self, parent: tk.Misc) -> None:
        theme = self.theme
        tk.Button(
            parent,
            text="开始游戏",
            font=(theme.heading_font_family, theme.ui_font_size + 2, "bold"),
            command=self.start,
            foreground=theme.card_bg,
            background=theme.cinnabar,
            activeforeground=theme.card_bg,
            activebackground=mix(theme.cinnabar, theme.ink, 0.18),
            relief=tk.FLAT,
            bd=0,
            padx=theme.px(14),
            pady=theme.px(5),
            highlightbackground=theme.panel_bg,
            default=tk.ACTIVE,
        ).pack(fill=tk.X, pady=(6, 0))

    # ------------------------------------------------------------ state ----

    def _style_card(self, parts: dict[str, tk.Widget], chosen: bool) -> None:
        theme = self.theme
        border = theme.cinnabar if chosen else theme.paper_deep
        parts["card"].configure(highlightbackground=border, highlightcolor=border)
        parts["label"].configure(
            background=mix(theme.card_bg, theme.cinnabar, 0.10)
            if chosen
            else theme.card_bg,
            foreground=theme.cinnabar if chosen else theme.text_primary,
            font=(theme.heading_font_family, theme.ui_font_size, "bold")
            if chosen
            else theme.ui_font,
        )

    def _select_mode(self, key: str) -> None:
        for other, parts in self._mode_cards.items():
            self._style_card(parts, other == key)
        self._mode.set(key)
        triple = key == "triple"
        self._subtitle.configure(text=_TRIPLE_SUBTITLE if triple else _CLASSIC_SUBTITLE)
        if triple:
            # Two players is not a choice here, it is the mode; showing a
            # count row that cannot be used would only invite clicks.
            self._counts_box.pack_forget()
            self._rebuild_players()
            self._note.configure(text=_TRIPLE_NOTE)
        else:
            self._counts_box.pack(fill=tk.X, before=self._players_box)
            self._select_count(self._count.get())
        for mode_key in self._mode_cards:
            self._draw_mode_mini(mode_key)

    def _select_count(self, count: int) -> None:
        self._count.set(count)
        for n, parts in self._cards.items():
            self._style_card(parts, n == count)
        self._rebuild_players()
        self._note.configure(text="" if count in BALANCED_COUNTS else _UNBALANCED_NOTE)
        self._draw_mode_mini("classic")

    def _camp_groups(self) -> tuple[tuple[int, ...], ...]:
        """The camps each player would own, one tuple per person.

        The single source of truth for the player rows, the colour list and the
        player count -- both modes are just different groupings of camps.
        """
        if self._mode.get() == "triple":
            return TRIPLE_SEATING
        return tuple((camp,) for camp in SEATING[self._count.get()])

    def _rebuild_players(self) -> None:
        theme = self.theme
        for child in self._players.winfo_children():
            child.destroy()
        groups = self._camp_groups()
        for i, camps in enumerate(groups):
            row = tk.Frame(self._players, background=theme.panel_bg)
            row.pack(fill=tk.X, pady=1)
            self._build_swatches(row, camps)
            # Packed before the entry: the entry expands into whatever cavity
            # is left, so anything packed after it would get no width at all.
            if self._bots_available():
                self._choice_chips(
                    row,
                    SEAT_KINDS,
                    self._bot_kinds[i],
                    on_change=lambda kind, index=i: self._on_seat_kind(index, kind),
                    side=tk.RIGHT,
                )
            tk.Entry(
                row,
                textvariable=self._names[i],
                font=theme.ui_font,
                highlightthickness=1,
                highlightbackground=theme.paper_deep,
                highlightcolor=theme.antique_gold,
                background=theme.card_bg,
                foreground=theme.ink,
                insertbackground=theme.cinnabar,
                relief=tk.FLAT,
                width=22,
            ).pack(side=tk.LEFT, fill=tk.X, expand=True)
        self._sync_bot_level()

    def _bots_available(self) -> bool:
        """Whether this table can be played against the bot.

        Exactly two players, which is what alpha-beta needs -- and which the
        three-colour mode also satisfies, since there each *person* owns three
        colours but there are still only two of them.
        """
        return len(self._camp_groups()) == 2

    def _on_seat_kind(self, index: int, kind: str) -> None:
        """Keep the default name honest when a seat changes hands."""
        name = self._names[index]
        if kind == "bot" and name.get().strip() in ("", f"玩家{index + 1}"):
            name.set(_BOT_NAME)
        elif kind == "human" and name.get().strip() in ("", _BOT_NAME):
            name.set(f"玩家{index + 1}")
        self._sync_bot_level()

    def _sync_bot_level(self) -> None:
        if any(level is not None for level in self._bot_levels()):
            self._level_box.pack(fill=tk.X, before=self._rules_box)
        else:
            self._level_box.pack_forget()

    def _bot_levels(self) -> tuple[str | None, ...]:
        """One entry per player: the difficulty they play at, or ``None``."""
        if not self._bots_available():
            return (None,) * len(self._camp_groups())
        level = self._bot_level.get()
        return tuple(
            level if self._bot_kinds[i].get() == "bot" else None
            for i in range(len(self._camp_groups()))
        )

    def _build_swatches(self, row: tk.Frame, camps: Sequence[int]) -> None:
        """One dot + colour name per camp the player runs."""
        theme = self.theme
        for camp in camps:
            swatch = tk.Canvas(
                row, width=18, height=18, highlightthickness=0, bd=0, background=theme.panel_bg
            )
            swatch.pack(side=tk.LEFT, padx=(0, 4))
            color = DEFAULT_COLORS[camp]
            swatch.create_oval(1, 1, 17, 17, fill=color, outline=mix(color, "#000000", 0.35))
            tk.Label(
                row,
                text=COLOR_NAMES[camp],
                font=theme.ui_font,
                fg=theme.text_primary,
                background=theme.panel_bg,
                width=2,
            ).pack(side=tk.LEFT, padx=(0, 6))
        row.pack_slaves()[-1].pack_configure(padx=(0, 10))

    def config_value(self) -> GameConfig:
        groups = self._camp_groups()
        names = tuple(
            (self._names[i].get().strip() or f"玩家{i + 1}") for i in range(len(groups))
        )
        colors = tuple(DEFAULT_COLORS[camp] for camps in groups for camp in camps)
        rules = RuleSet(
            home_lock=bool(self._home_lock.get()),
            no_stop_in_foreign_camp=bool(self._no_stop.get()),
        )
        return GameConfig(
            player_count=len(groups),
            names=names,
            colors=colors,
            rules=rules,
            colors_each=len(groups[0]),
            bots=self._bot_levels(),
        )

    def start(self) -> None:
        self.on_start(self.config_value())
