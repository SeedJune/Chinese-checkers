"""Headless end-to-end check of the interactive UI.

A real :class:`~chinese_checkers.ui.app.App` is booted *withdrawn* and driven
with synthesized events, so every assertion here exercises the same code path a
player would.  The window is never mapped because photographing it is
unreliable on macOS (see ``canvas_png``); instead each interesting step is
re-rendered offscreen through :class:`PngCanvas` at the live board's exact
geometry, which makes the visual check reproducible and reviewable as files.

Usage::

    python3 scripts/smoke_ui.py [--out DIR]
"""

from __future__ import annotations

import argparse
import sys
import time
import tkinter as tk
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from canvas_png import PngCanvas  # noqa: E402

from chinese_checkers.core.board import (  # noqa: E402
    BOARD,
    BOARD_SORTED,
    CAMPS,
    COLOR_NAMES,
    DEFAULT_COLORS,
    SEATING,
    TRIPLE_SEATING,
    camp_of,
)
from chinese_checkers.core.coords import distance, neighbors  # noqa: E402
from chinese_checkers.core.game import Phase  # noqa: E402
from chinese_checkers.core.rules import RuleSet  # noqa: E402
from chinese_checkers.ui.app import App  # noqa: E402
from chinese_checkers.ui.menu import COUNTS, TRIPLE_CAMPS, GameConfig  # noqa: E402
from chinese_checkers.ui.panel import PANEL_WIDTH  # noqa: E402
from chinese_checkers.ui.render import BoardRenderer  # noqa: E402

FAILURES: list[str] = []
SHOTS: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    print(f"  [{'ok  ' if condition else 'FAIL'}] {label}" + (f"  -- {detail}" if detail else ""))
    if not condition:
        FAILURES.append(label)


# -- driving the real widgets ------------------------------------------------


def click(app: App, cell) -> None:
    x, y = app.board_view.renderer.center_of(cell)
    app.board_view._on_click(SimpleNamespace(x=x, y=y))


def motion(app: App, cell) -> None:
    x, y = app.board_view.renderer.center_of(cell)
    app.board_view._on_motion(SimpleNamespace(x=x, y=y))


def snapshot(app: App, out: Path, name: str, scale: int = 2) -> None:
    """Re-render the board's current Scene into a PNG at the live geometry."""
    view = app.board_view
    width = max(200, view.winfo_width())
    height = max(200, view.winfo_height())
    canvas = PngCanvas(width, height, scale=scale)
    renderer = BoardRenderer(canvas, app.theme)
    renderer.layout(width * scale, height * scale)
    scene = view.build_scene()
    if scene.floating is not None:
        # ``layout`` is linear in canvas size, so live canvas coordinates map
        # onto the supersampled canvas by the same factor.
        seat, fx, fy = scene.floating
        scene.floating = (seat, fx * scale, fy * scale)
    renderer.draw(scene)
    canvas.save(out / name)
    SHOTS.append(str(out / name))


def snapshot_minis(app: App, out: Path, name: str = "08_menu_minis.png") -> None:
    """Render the menu's five seating previews side by side.

    The cards themselves are Tk widgets and cannot be photographed here, but
    their canvases are painted by ``draw_mini`` -- so re-running that call
    offscreen shows exactly what the cards show.
    """
    from PIL import Image

    size, scale, gap = 96, 3, 12
    strip = Image.new("RGB", (len(COUNTS) * size + (len(COUNTS) + 1) * gap, size + 2 * gap),
                      app.theme.app_bg)
    for i, count in enumerate(COUNTS):
        canvas = PngCanvas(size, size, scale=scale)
        BoardRenderer(canvas, app.theme).draw_mini(SEATING[count], DEFAULT_COLORS)
        tile = canvas._img.convert("RGB").resize((size, size), Image.LANCZOS)
        strip.paste(tile, (gap + i * (size + gap), gap))
    strip.save(out / name)
    print(f"已保存离屏渲染: {out / name}")
    SHOTS.append(str(out / name))


def snapshot_mode_minis(app: App, out: Path, name: str = "09_menu_modes.png") -> None:
    """Render the two 玩法 previews side by side.

    Same trick as :func:`snapshot_minis`: the cards are Tk widgets, but their
    canvases are painted by ``draw_mini``, so re-running that call offscreen
    shows exactly what a player sees.
    """
    from PIL import Image

    from chinese_checkers.ui.menu import MODES, _MODE_MINI_SIZE

    # Drawn at the card's real size and only magnified afterwards, so the strip
    # shows whether the seam still reads at the size a player actually sees.
    size, scale, gap, zoom = _MODE_MINI_SIZE, 3, 14, 2
    strip = Image.new(
        "RGB",
        (len(MODES) * size * zoom + (len(MODES) + 1) * gap, size * zoom + 2 * gap),
        app.theme.app_bg,
    )
    for i, (key, _title, _blurb) in enumerate(MODES):
        canvas = PngCanvas(size, size, scale=scale)
        renderer = BoardRenderer(canvas, app.theme)
        if key == "triple":
            renderer.draw_mini(TRIPLE_CAMPS, DEFAULT_COLORS, groups=TRIPLE_SEATING)
        else:
            renderer.draw_mini(SEATING[3], DEFAULT_COLORS)
        tile = canvas._img.convert("RGB").resize((size, size), Image.LANCZOS)
        strip.paste(tile.resize((size * zoom, size * zoom), Image.NEAREST),
                    (gap + i * (size * zoom + gap), gap))
    strip.save(out / name)
    print(f"已保存离屏渲染: {out / name}")
    SHOTS.append(str(out / name))


def pump(app: App, seconds: float = 0.0) -> None:
    """Run the Tk event loop for a while without mapping the window."""
    deadline = time.monotonic() + seconds
    while True:
        app.update()
        if time.monotonic() >= deadline:
            return
        time.sleep(0.005)


def wait_for_animation(app: App, out: Path | None = None, name: str = "") -> None:
    """Wait out the confirm animation, optionally grabbing one flying frame."""
    grabbed = out is None
    started = time.monotonic()
    while app.board_view._animating:
        app.update()
        if not grabbed and app.board_view._float is not None:
            snapshot(app, out, name)
            grabbed = True
        time.sleep(0.004)
        if time.monotonic() - started > 5.0:
            raise RuntimeError("动画没有结束")
    app.update()
    if not grabbed:
        print("  [warn] 没有抓到动画中间帧")


def realize(app: App) -> None:
    """Bring a withdrawn window as close to "mapped" as Tk allows.

    Tk hands a real size to the children of an unmapped toplevel exactly once
    (at the first ``update_idletasks``), so a screen mounted later reports 1x1
    forever and never gets a ``<Configure>``.  Requesting the size the packer
    would have given it makes ``winfo_width`` truthful again; in a live window
    ``expand=True`` overrides the request, so this only affects the harness.
    """
    app.update_idletasks()
    pump(app)
    view = app.board_view
    if view.winfo_width() <= 1 or view.winfo_height() <= 1:
        width = (app.winfo_width() or 1180) - PANEL_WIDTH
        height = app.winfo_height() or 860
        view.configure(width=max(400, width), height=max(400, height))
        app.update_idletasks()
    view.ensure_layout()


def walk(widget: tk.Misc):
    for child in widget.winfo_children():
        yield child
        yield from walk(child)


def button_states(app: App) -> dict[str, str]:
    return {k: str(b["state"]) for k, b in app.panel.buttons.items()}


def config_for(count: int, names: tuple[str, ...]) -> GameConfig:
    camps = SEATING[count]
    return GameConfig(count, names, tuple(DEFAULT_COLORS[c] for c in camps), RuleSet())


def find_jump_chain(game):
    """A piece with at least a two-hop jump chain, as (origin, hop1, hop2)."""
    for origin in sorted(game.selectable(), key=lambda c: (c[2], c[0])):
        game.select(origin)
        for first in sorted(game.options()["jumps"]):
            game.extend(first)
            seconds = sorted(game.options()["jumps"])
            if seconds:
                game.cancel()
                return (origin, first, seconds[0])
            game.rollback()
        game.cancel()
    return None


def play_until_chain(app: App, limit: int = 200):
    """Play random legal moves until the side to move has a two-hop chain.

    The opening position has single jumps but no chains, so the multi-hop UI
    can only be exercised from a slightly developed board.
    """
    from chinese_checkers.agents import RandomAgent

    agent = RandomAgent(seed=7)
    game = app.game
    for _ in range(limit):
        chain = find_jump_chain(game)
        if chain is not None:
            return chain
        move = agent.select_move(game)
        game.select(move.origin)
        for cell in move.path:
            game.extend(cell)
        game.confirm()
    return None


# -- endgame fixture ---------------------------------------------------------


def stage_endgame(app: App) -> None:
    """Hand-place a 2-player position where seat 0 is one step from home.

    Test-only surgery on the *data* in ``GameState`` (the engine's code is
    untouched): reaching a real endgame through legal play would take hundreds
    of moves and would not be reproducible.
    """
    state = app.game.state
    target = state.seats[0].targets[0]
    cells = sorted(CAMPS[target], key=lambda c: (c[2], c[0]))
    hole = cells[0]

    state.occupancy.clear()
    state.pieces[0].clear()
    state.pieces[1].clear()

    for cell in cells[1:]:  # nine of the ten target cells already filled
        state.occupancy[cell] = 0
        state.pieces[0].add(cell)
    entry = next(c for c in neighbors(hole) if c in BOARD and camp_of(c) != target)
    state.occupancy[entry] = 0
    state.pieces[0].add(entry)

    # Seat 1 is parked in the central hexagon, far from the hole -- putting it
    # in its own target camp would make it finished too and end the game early.
    central = sorted(
        (c for c in BOARD if camp_of(c) is None and c not in state.occupancy),
        key=lambda c: -distance(c, hole),
    )
    for cell in central[:10]:
        state.occupancy[cell] = 1
        state.pieces[1].add(cell)

    state.current = 0
    app._on_state_changed(None)


def play_one_move(app: App, seat: int | None = None):
    """Click out one whole move -- optionally with a piece of a given colour.

    Goes through the widget rather than the engine so the panel, the status
    line and the confirm animation are exercised exactly as they are by hand.
    """
    game, view = app.game, app.board_view
    for origin in sorted(game.selectable(), key=lambda c: (c[2], c[0])):
        if seat is not None and game.state.occupancy[origin] != seat:
            continue
        click(app, origin)
        options = game.options()
        landings = sorted(options["steps"]) or sorted(options["jumps"])
        if not landings:
            view.cancel()
            continue
        click(app, landings[0])
        if not game.can_confirm:
            view.cancel()
            continue
        view.confirm_move()
        wait_for_animation(app)
        return (origin, landings[0])
    return None


def develop(app: App, moves: int, seed: int = 11) -> None:
    """Play random legal moves straight on the engine to open the position up.

    Only used to get a board where camps have holes: the target tint is drawn
    under the marbles, so at the opening position it is invisible by design.
    """
    from chinese_checkers.agents import RandomAgent

    agent = RandomAgent(seed=seed)
    game = app.game
    for _ in range(moves):
        move = agent.select_move(game)
        game.select(move.origin)
        for cell in move.path:
            game.extend(cell)
        game.confirm()
    app.board_view.refresh()


def stage_triple_endgame(app: App):
    """Hand-place a three-colour position where player 0 is one step from done.

    Test-only surgery on the *data* in ``GameState`` (the engine's code is
    untouched), like :func:`stage_endgame`: two of the three colours are fully
    home and the third has one hole left, which is the only position where the
    "all three colours" finishing rule is worth looking at.  Returns the
    ``(origin, hole)`` step that completes it.
    """
    state = app.game.state
    state.occupancy.clear()
    for cells in state.pieces.values():
        cells.clear()

    mine = state.seats_of(0)
    hole = None
    for i, seat in enumerate(mine):
        target = state.seats[seat].targets[0]
        cells = sorted(CAMPS[target], key=lambda c: (c[2], c[0]))
        if i == len(mine) - 1:  # the last colour keeps one hole open
            hole = cells[0]
            cells = cells[1:]
        for cell in cells:
            state.occupancy[cell] = seat
            state.pieces[seat].add(cell)
    assert hole is not None
    entry = next(
        c
        for c in neighbors(hole)
        if c in BOARD and c not in state.occupancy and camp_of(c) != camp_of(hole)
    )
    state.occupancy[entry] = mine[-1]
    state.pieces[mine[-1]].add(entry)

    # The opponent parks in the central hexagon: their own target camps stay
    # empty, so nobody else finishes by accident.
    central = [c for c in BOARD_SORTED if camp_of(c) is None and c not in state.occupancy]
    for i, seat in enumerate(state.seats_of(1)):
        for cell in central[i * 10:(i + 1) * 10]:
            state.occupancy[cell] = seat
            state.pieces[seat].add(cell)

    state.current = 0
    app._on_state_changed(None)
    return (entry, hole)


def closing_move(app: App):
    """The (origin, destination) step that fills seat 0's last target hole.

    Only origins *outside* the target camp qualify: a piece already inside
    would just move the hole somewhere else.
    """
    game = app.game
    target = game.current_seat.targets[0]
    hole = next(c for c in CAMPS[target] if c not in game.state.occupancy)
    for origin in sorted(game.selectable()):
        if camp_of(origin) == target:
            continue
        game.select(origin)
        options = game.options()
        ok = hole in options["steps"] or hole in options["jumps"]
        game.cancel()
        if ok:
            return (origin, hole)
    return None


# ---------------------------------------------------------------------------


def check_menu(app: App, out: Path) -> None:
    print("\n== 菜单屏 ==")
    menu = app._screen
    app.update_idletasks()
    check("菜单已挂载", menu.__class__.__name__ == "MenuScreen")
    check(
        "菜单有非零尺寸",
        menu.winfo_reqwidth() > 0 and menu.winfo_reqheight() > 0,
        f"{menu.winfo_reqwidth()}x{menu.winfo_reqheight()}",
    )
    check("五张人数卡片", len(menu._cards) == 5, str(sorted(menu._cards)))
    minis = [p["mini"] for p in menu._cards.values()]
    check(
        "每张卡片都有迷你棋盘画布",
        all(m.winfo_reqwidth() > 10 and m.winfo_reqheight() > 10 for m in minis),
        f"{minis[0].winfo_reqwidth()}x{minis[0].winfo_reqheight()}",
    )
    menu._select_count(5)
    app.update_idletasks()
    check("5 人局给出不对称提示", "不对称" in menu._note.cget("text"), menu._note.cget("text"))
    menu._select_count(4)
    app.update_idletasks()
    check("4 人局没有提示", menu._note.cget("text") == "")
    check("玩家行数量随人数变化", len(menu._players.winfo_children()) == 4)
    entries = [w for w in walk(menu._players) if isinstance(w, tk.Entry)]
    check("每位玩家一个名字输入框", len(entries) == 4)
    cfg = menu.config_value()
    check("默认规则 home_lock 开启", cfg.rules.home_lock is True)
    check("默认规则 no_stop 关闭", cfg.rules.no_stop_in_foreign_camp is False)
    check("默认名字", cfg.names == ("玩家1", "玩家2", "玩家3", "玩家4"), str(cfg.names))
    check("颜色取自所选座位", cfg.colors == tuple(DEFAULT_COLORS[c] for c in SEATING[4]))
    checks = [w for w in walk(menu) if isinstance(w, tk.Checkbutton)]
    check("两个规则复选框", len(checks) == 2, str([c.cget("text") for c in checks]))
    labels = [w.cget("text") for w in walk(menu) if isinstance(w, tk.Label)]
    check("固定跳跃规则被展示", any("经典邻接跳" in t for t in labels))
    snapshot_minis(app, out)


def check_game(app: App, out: Path) -> None:
    print("\n== 开局 (3 人) ==")
    app.start_game(config_for(3, ("红方", "绿方", "橙方")))
    realize(app)
    game, view = app.game, app.board_view
    check(
        "棋盘挂载并有尺寸",
        view.winfo_width() > 400 and view.winfo_height() > 400,
        f"{view.winfo_width()}x{view.winfo_height()}",
    )
    check("渲染器已按窗口大小布局", view.renderer.cell_size > 20, f"cell_size={view.renderer.cell_size:.1f}")
    check(
        "像素与格子互相映射一致",
        all(
            view.renderer.cell_at(*view.renderer.center_of(c)) == c
            for c in list(game.state.occupancy)[:20]
        ),
    )
    check("侧栏宽度固定 270", app.panel.winfo_width() == 270, str(app.panel.winfo_width()))
    check("侧栏四个按钮齐全", sorted(app.panel.buttons) == ["cancel", "confirm", "rollback", "undo"])
    check(
        "按钮标签带快捷键",
        [app.panel.buttons[k].cget("text") for k in ("confirm", "rollback", "cancel", "undo")]
        == ["确认走子 (Enter)", "回退一跳 (⌫)", "取消选择 (Esc)", "悔棋 (⌘Z)"],
    )
    check("初始 30 颗棋子", len(game.state.occupancy) == 30)
    check("初始阶段 IDLE", game.phase is Phase.IDLE)
    check(
        "空闲时按钮全部禁用",
        button_states(app) == dict.fromkeys(button_states(app), "disabled"),
        str(button_states(app)),
    )
    check("路线读数为未选择", "未选择棋子" in app.panel._route.cget("text"))
    check("进度读数 0/10", "已到家 0/10" in app.panel._player_rows[0].info.cget("text"),
          app.panel._player_rows[0].info.cget("text"))
    snapshot(app, out, "01_start.png")

    # ------------------------------------------------------------ select --
    print("\n== 选子与高亮 ==")
    origin = sorted(game.selectable(), key=lambda c: (c[2], c[0]))[0]
    before = dict(game.state.occupancy)
    click(app, origin)
    check("phase 变为 SELECTED", game.phase is Phase.SELECTED, str(game.phase))
    check("path 为 (起点,)", game.path == (origin,), str(game.path))
    scene = view.build_scene()
    check("场景标记了选中子", scene.selected == origin)
    check(
        "场景给出了落点高亮",
        bool(scene.step_targets or scene.jump_targets),
        f"steps={len(scene.step_targets)} jumps={len(scene.jump_targets)}",
    )
    check("场景给出了可动棋子光晕", len(scene.selectable) > 0, str(len(scene.selectable)))
    check("目标三角被标注", scene.target_camp == game.current_seat.targets[0])
    check(
        "选中后可取消、不可确认",
        button_states(app)["cancel"] == "normal" and button_states(app)["confirm"] == "disabled",
    )
    snapshot(app, out, "02_selected.png")

    landing = sorted(scene.step_targets or scene.jump_targets)[0]
    motion(app, landing)
    check("悬停落点时有 hover 环", view.build_scene().hover == landing)
    check("悬停落点时手型光标", view._cursor == "hand2", view._cursor)
    foreign = next(c for c, s in game.state.occupancy.items() if s != game.state.current)
    motion(app, foreign)
    check("悬停在别人棋子上没有 hover 环", view.build_scene().hover is None)
    check("非可操作格恢复默认光标", view._cursor == "", repr(view._cursor))

    # ----------------------------------------------------------- illegal --
    print("\n== 非法点击 ==")
    app.panel.set_status("", "info")
    click(app, foreign)
    status = app.panel._status.cget("text")
    check("状态栏出现错误文案", status != "", status)
    check("错误文案用红色", app.panel._status.cget("fg") == app.theme.text_danger)
    check("非法点击没有改变棋盘", dict(game.state.occupancy) == before)
    view.cancel()
    check("取消后回到 IDLE", game.phase is Phase.IDLE and game.path == ())

    # -------------------------------------------------------- jump chain --
    print("\n== 连跳、回退、取消 ==")
    chain = play_until_chain(app)
    view.refresh()
    before = dict(game.state.occupancy)
    history_depth = len(game.state.history)
    check("找到了两跳链", chain is not None, str(chain))
    assert chain is not None
    o, h1, h2 = chain
    click(app, o)
    click(app, h1)
    check("一跳后 phase=CHAINING", game.phase is Phase.CHAINING, str(game.phase))
    click(app, h2)
    check("两跳后 path 长度为 3", len(game.path) == 3, str(game.path))
    check("两跳后可确认", game.can_confirm)
    check("路线读数写出 2 跳", "2 跳" in app.panel._route.cget("text"), app.panel._route.cget("text"))
    check(
        "连跳中按钮：确认/回退/取消都可用",
        [button_states(app)[k] for k in ("confirm", "rollback", "cancel")]
        == ["normal", "normal", "normal"],
        str(button_states(app)),
    )
    snapshot(app, out, "03_jump_chain.png")

    view.rollback()
    check("回退后 path 长度为 2", len(game.path) == 2, str(game.path))
    check("回退后仍是 CHAINING", game.phase is Phase.CHAINING)
    view.rollback()
    check("再回退后回到 SELECTED", game.phase is Phase.SELECTED and game.path == (o,))
    view.cancel()
    check("取消后 IDLE 且棋盘未变", game.phase is Phase.IDLE and dict(game.state.occupancy) == before)

    # 中途改选棋子
    other = next(c for c in sorted(game.selectable()) if c != o)
    click(app, o)
    click(app, other)
    check("点另一颗自己的棋子会直接改选", game.path == (other,), str(game.path))
    view.cancel()

    # ----------------------------------------------------------- confirm --
    print("\n== 确认走子（含动画） ==")
    click(app, o)
    click(app, h1)
    click(app, h2)
    mover = game.state.current
    view.confirm_move()
    check("动画期间输入被锁", view._animating is True)
    locked = view.build_scene()
    check(
        "动画期间场景不再邀请点击",
        locked.selectable == frozenset() and locked.step_targets == frozenset(),
    )
    check("动画期间棋子在飞", locked.floating is not None and h2 not in locked.occupancy)
    wait_for_animation(app, out, "04_animating.png")
    check("动画结束解锁", view._animating is False)
    check("棋子确实离开起点", o not in game.state.occupancy)
    check("棋子确实落在终点", game.state.occupancy.get(h2) == mover, str(game.state.occupancy.get(h2)))
    check("轮到下一个座位", game.state.current != mover, str(game.state.current))
    check("last_move 指向刚走的一步", view.build_scene().last_move == (o, h2))
    check("确认后可悔棋", button_states(app)["undo"] == "normal")
    snapshot(app, out, "05_after_move.png")

    # -------------------------------------------------------------- undo --
    print("\n== 悔棋 ==")
    view.undo()
    check("悔棋后棋盘完全复原", dict(game.state.occupancy) == before)
    check("悔棋后轮回原座位", game.state.current == mover)
    check("悔棋后历史回退一条", len(game.state.history) == history_depth, str(len(game.state.history)))

    # --------------------------------------------------------- Tab cycle --
    print("\n== Tab 循环选子 ==")
    order = sorted(game.selectable(), key=lambda c: (c[2], c[0]))
    view.focus_next_piece()
    check("Tab 选中第一颗", game.path == (order[0],), str(game.path))
    view.focus_next_piece()
    check("Tab 走到第二颗", game.path == (order[1],), str(game.path))
    for _ in range(len(order) - 1):
        view.focus_next_piece()
    check("Tab 绕回第一颗", game.path == (order[0],), str(game.path))
    view.cancel()

    # ------------------------------------------------------ key bindings --
    print("\n== 键盘绑定 ==")
    for name in ("<Return>", "<BackSpace>", "<Escape>", "<Command-z>", "<Control-z>", "<Tab>"):
        check(f"root 绑定了 {name}", bool(app.bind(name)))
    for name in ("<Return>", "<BackSpace>", "<Escape>", "<Command-z>"):
        app.event_generate(name, when="now")
    pump(app)
    check("空闲时按键是安全空操作", dict(game.state.occupancy) == before and game.phase is Phase.IDLE)


def check_endgame(app: App, out: Path) -> None:
    print("\n== 终局横幅与名次面板 ==")
    app.start_game(config_for(2, ("红方", "紫方")))
    realize(app)
    check(
        "第二局棋盘同样有尺寸",
        app.board_view.winfo_width() > 400 and app.board_view.renderer.cell_size > 20,
        f"{app.board_view.winfo_width()}x{app.board_view.winfo_height()}",
    )
    stage_endgame(app)
    move = closing_move(app)
    check("构造出了一步到家的局面", move is not None, str(move))
    assert move is not None
    src, dst = move
    click(app, src)
    click(app, dst)
    check("终局前一步已建好", app.game.can_confirm)
    snapshot(app, out, "06_endgame_ready.png")

    app.board_view.confirm_move()
    wait_for_animation(app)
    check("红方到家", app.game.state.rankings == [0], str(app.game.state.rankings))
    check("游戏结束", app.game.is_over)
    check("到家横幅出现", app._banner is not None)
    if app._banner is not None:
        check("横幅文案格式", "全部到家！第 1 名" in app._banner.cget("text"), app._banner.cget("text"))
    check(
        "侧栏给出名次徽章",
        "第1名" in app.panel._player_rows[0].info.cget("text"),
        app.panel._player_rows[0].info.cget("text"),
    )
    snapshot(app, out, "07_endgame_board.png")

    print("     等待横幅淡出与终局面板 ...")
    pump(app, 2.2)
    check("终局面板出现", app._overlay is not None)
    if app._overlay is not None:
        texts = [w.cget("text") for w in walk(app._overlay) if isinstance(w, tk.Label)]
        check(
            "面板列出全部名次",
            any("第1名" in t for t in texts) and any("第2名" in t for t in texts),
            str(texts),
        )
        buttons = [w.cget("text") for w in walk(app._overlay) if isinstance(w, tk.Button)]
        check("面板有再来一局/返回菜单", buttons == ["再来一局", "返回菜单"], str(buttons))
    pump(app, 1.6)
    check("横幅最终淡出销毁", app._banner is None)

    app.restart()
    app.update_idletasks()
    check(
        "再来一局回到初始局面",
        len(app.game.state.occupancy) == 20 and app.game.state.history == [],
    )
    check("再来一局清掉了终局面板", app._overlay is None)
    app.show_menu()
    app.update_idletasks()
    check("返回菜单后 board_view 为空", app.board_view is None)
    app.event_generate("<Return>", when="now")
    app.event_generate("<Tab>", when="now")
    app.update()
    check("菜单屏按键不抛异常", True)


def check_triple(app: App, out: Path) -> None:
    print("\n== 三色对战：菜单 ==")
    app.show_menu()
    menu = app._screen
    app.update_idletasks()
    check("两张玩法卡片", sorted(menu._mode_cards) == ["classic", "triple"], str(sorted(menu._mode_cards)))
    check("默认玩法是经典对战", menu._mode.get() == "classic")
    check("经典模式下人数行可见", menu._counts_box.winfo_manager() == "pack")

    menu._select_mode("triple")
    app.update_idletasks()
    check("三色模式隐藏人数行", menu._counts_box.winfo_manager() == "", repr(menu._counts_box.winfo_manager()))
    rows = menu._players.winfo_children()
    check("玩家行固定为两行", len(rows) == 2, str(len(rows)))
    swatches = [[w for w in walk(row) if isinstance(w, tk.Canvas)] for row in rows]
    check("每行三个颜色色块", [len(s) for s in swatches] == [3, 3], str([len(s) for s in swatches]))
    names = [w.cget("text") for row in rows for w in walk(row) if isinstance(w, tk.Label)]
    check(
        "色块旁写出颜色名",
        names == [COLOR_NAMES[camp] for camps in TRIPLE_SEATING for camp in camps],
        str(names),
    )
    check("每行一个名字输入框", len([w for w in walk(menu._players) if isinstance(w, tk.Entry)]) == 2)
    check("给出三色玩法说明", "每人执三色" in menu._note.cget("text"), menu._note.cget("text"))

    menu._names[0].set("甲方")
    menu._names[1].set("乙方")
    cfg = menu.config_value()
    check("配置带上 colors_each=3", cfg.colors_each == 3, str(cfg.colors_each))
    check("配置人数为 2", cfg.player_count == 2, str(cfg.player_count))
    check(
        "配置六种颜色且顺序同 TRIPLE_SEATING",
        cfg.colors == tuple(DEFAULT_COLORS[camp] for camp in TRIPLE_CAMPS),
        str(cfg.colors),
    )
    check("配置名字取自输入框", cfg.names == ("甲方", "乙方"), str(cfg.names))
    snapshot_mode_minis(app, out)

    menu._select_mode("classic")
    app.update_idletasks()
    check("切回经典模式恢复人数行", menu._counts_box.winfo_manager() == "pack")
    check("切回经典模式 colors_each=1", menu.config_value().colors_each == 1)

    # ------------------------------------------------------------ opening --
    print("\n== 三色对战：开局 ==")
    app.start_game(cfg)
    realize(app)
    game, view, state = app.game, app.board_view, app.game.state
    check("六十颗棋子在场", len(state.occupancy) == 60, str(len(state.occupancy)))
    check("两名玩家、六种颜色", (len(state.players), len(state.seats)) == (2, 6))
    check("每人三色", [len(p.seats) for p in state.players] == [3, 3])
    panel = app.panel
    check("侧栏两行玩家", len(panel._player_rows) == 2, str(len(panel._player_rows)))
    check("每行三个颜色点", [len(r.dots) for r in panel._player_rows] == [3, 3])
    check("每行三条分色明细", [len(r.colors) for r in panel._player_rows] == [3, 3])
    check(
        "进度按 30 计",
        "已到家 0/30" in panel._player_rows[0].info.cget("text"),
        panel._player_rows[0].info.cget("text"),
    )
    check(
        "分色明细写出 0/10",
        all("到家 0/10" in line.label.cget("text") for line in panel._player_rows[0].colors),
        str([line.label.cget("text") for line in panel._player_rows[0].colors]),
    )
    check("回合标题写玩家名", panel._turn_name.cget("text") == "甲方", panel._turn_name.cget("text"))
    check("回合标题带三个色点", len(panel._turn_dots) == 3, str(len(panel._turn_dots)))
    check("状态栏点名玩家", "甲方" in panel._status.cget("text"), panel._status.cget("text"))
    seen = {state.occupancy[c] for c in game.selectable()}
    check("可选棋子覆盖自己的三种颜色", seen == set(state.seats_of(0)), str(sorted(seen)))
    check(
        "对手的棋子一颗都不能选",
        not (seen & set(state.seats_of(1))),
        str(sorted(seen)),
    )
    snapshot(app, out, "10_triple_start.png")

    # ------------------------------------------------- overlays per colour --
    print("\n== 三色对战：高亮跟随所选颜色 ==")
    idle = view.build_scene()
    check("未选子时用玩家的第一种颜色", idle.active_seat == state.seats_of(0)[0], str(idle.active_seat))
    for seat in state.seats_of(0):
        origin = next(c for c in sorted(game.selectable()) if state.occupancy[c] == seat)
        click(app, origin)
        scene = view.build_scene()
        name = state.seats[seat].name
        check(
            f"选中{name}色时目标三角是它自己的对角",
            scene.target_camp == state.seats[seat].targets[0],
            f"{scene.target_camp} vs {state.seats[seat].targets[0]}",
        )
        check(f"选中{name}色时覆盖层用{name}色", scene.active_seat == seat, str(scene.active_seat))
        view.cancel()

    # ------------------------------------------------------ taking turns ----
    print("\n== 三色对战：三色各走一步、轮流走子 ==")
    for seat in state.seats_of(0):
        name = state.seats[seat].name
        played = play_one_move(app, seat)
        check(f"{name}色走了一步", played is not None, str(played))
        check("走完一步就轮到对手", state.current == 1, str(state.current))
        check("对手回合的可选棋子全是对手的",
              {state.occupancy[c] for c in game.selectable()} <= set(state.seats_of(1)))
        check("对手也走了一步", play_one_move(app) is not None)
        check("对手走完轮回自己", state.current == 0, str(state.current))
    check(
        "三种颜色都动过",
        {r.seat for r in state.history if r.player == 0} == set(state.seats_of(0)),
        str(sorted(r.seat for r in state.history if r.player == 0)),
    )
    check("一回合只动一颗子", len(state.history) == 6, str(len(state.history)))

    # --------------------------------------------------- mid-game visual ----
    print("\n== 三色对战：中局选子渲染 ==")
    develop(app, 40)
    check("随机对局后仍轮到甲方", state.current == 0, str(state.current))
    seat = state.seats_of(0)[2]
    origin = next(c for c in sorted(game.selectable()) if state.occupancy[c] == seat)
    click(app, origin)
    scene = view.build_scene()
    check("中局目标三角仍跟随所选颜色", scene.target_camp == state.seats[seat].targets[0])
    snapshot(app, out, "11_triple_selected.png")
    view.cancel()

    # -------------------------------------------------------- the finish ----
    print("\n== 三色对战：终局横幅与名次 ==")
    app.start_game(cfg)
    realize(app)
    state = app.game.state
    entry, hole = stage_triple_endgame(app)
    check("两色已到家", sum(state.seat_is_home(s) for s in state.seats_of(0)) == 2)
    check("尚未完成", not state.has_finished(0))
    check(
        "侧栏读出 29/30",
        "已到家 29/30" in app.panel._player_rows[0].info.cget("text"),
        app.panel._player_rows[0].info.cget("text"),
    )
    breakdown = [line.label.cget("text") for line in app.panel._player_rows[0].colors]
    check("分色明细给到家的颜色打勾", sum("✓" in t for t in breakdown) == 2, str(breakdown))
    check("分色明细标出落后的颜色", any("到家 9/10" in t for t in breakdown), str(breakdown))

    click(app, entry)
    click(app, hole)
    check("终局前一步已建好", app.game.can_confirm)
    app.board_view.confirm_move()
    wait_for_animation(app)
    check("甲方三色全部到家", app.game.state.rankings == [0], str(app.game.state.rankings))
    check("游戏结束", app.game.is_over)
    check("到家横幅出现", app._banner is not None)
    if app._banner is not None:
        text = app._banner.cget("text")
        check("横幅点名玩家而不是颜色", text.startswith("甲方"), text)
        check("横幅写明三色全部到家", "三色全部到家！第 1 名" in text, text)
    check(
        "分色明细三色全打勾",
        all("✓" in line.label.cget("text") for line in app.panel._player_rows[0].colors),
        str([line.label.cget("text") for line in app.panel._player_rows[0].colors]),
    )
    check(
        "完成方的三种颜色都变灰",
        app.board_view.build_scene().dim_seats == frozenset(state.seats_of(0)),
        str(sorted(app.board_view.build_scene().dim_seats)),
    )
    snapshot(app, out, "12_triple_endgame.png")

    print("     等待横幅淡出与终局面板 ...")
    pump(app, 2.2)
    check("终局面板出现", app._overlay is not None)
    if app._overlay is not None:
        texts = [w.cget("text") for w in walk(app._overlay) if isinstance(w, tk.Label)]
        check("面板列出两名玩家", "甲方" in texts and "乙方" in texts, str(texts))
        check("面板不再列颜色名", not (set(texts) & set(COLOR_NAMES)), str(texts))
        dots = [w for w in walk(app._overlay) if isinstance(w, tk.Canvas)]
        check("每名玩家三个色点", len(dots) == 6, str(len(dots)))
    app.show_menu()
    app.update_idletasks()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default="/tmp/cc_ui", help="directory for the PNGs")
    args = parser.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    app = App()
    app.withdraw()
    app.geometry("1180x860")
    app.update_idletasks()

    check_menu(app, out)
    check_game(app, out)
    check_endgame(app, out)
    check_triple(app, out)
    app.destroy()

    print("\n生成的 PNG:")
    for path in SHOTS:
        print("  " + path)
    if FAILURES:
        print(f"\n{len(FAILURES)} 项失败:")
        for failure in FAILURES:
            print("  - " + failure)
        return 1
    print("\n全部通过。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
