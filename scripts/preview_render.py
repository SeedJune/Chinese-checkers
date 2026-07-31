"""Manual/visual check for ``chinese_checkers.ui.render``.

Not a unit test -- there is no assertion here that "glossy" looks glossy.
This just builds one plausible 6-player mid-game :class:`Scene` by hand and
opens it in a real Tk window so a human (or a screenshot) can judge whether
the renderer actually looks good.

Usage::

    python scripts/preview_render.py                       # interactive window
    python scripts/preview_render.py --shot out.png         # save PNG and exit
    python scripts/preview_render.py --no-pil --shot out.png  # force the
        pure-Tkinter marble fallback, to check it still looks acceptable
        without Pillow installed.
"""

from __future__ import annotations

import argparse
import sys
import tkinter as tk
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from _shot import shoot_and_quit  # noqa: E402

from chinese_checkers.core.board import (  # noqa: E402
    BOARD,
    CAMPS,
    DEFAULT_COLORS,
    SEATING,
    camp_of,
    opposite,
)
from chinese_checkers.core.coords import distance, to_pixel  # noqa: E402


def build_scene():
    from chinese_checkers.ui.render import Scene

    seats = SEATING[6]  # (0, 1, 2, 3, 4, 5) -- every camp occupied
    occupancy: dict = {}

    # Central hexagon cells (not part of any camp), ordered so that cells
    # near a given camp's mouth come first for that camp -- gives each seat
    # "advanced" pieces that look like they marched out toward the middle
    # instead of a random scatter.
    central = [c for c in BOARD if camp_of(c) is None]

    def camp_centroid(camp: int) -> tuple[float, float]:
        xs, ys = zip(*(to_pixel(c) for c in CAMPS[camp]))
        return (sum(xs) / len(xs), sum(ys) / len(ys))

    used: set = set()
    advanced_by_seat: dict[int, list] = {}
    for seat in seats:
        home_x, home_y = camp_centroid(seat)
        ranked = sorted(
            (c for c in central if c not in used),
            key=lambda c: (to_pixel(c)[0] - home_x) ** 2 + (to_pixel(c)[1] - home_y) ** 2,
        )
        take = ranked[:4]
        for c in take:
            used.add(c)
        advanced_by_seat[seat] = take

    for seat in seats:
        home_cells = sorted(CAMPS[seat], key=lambda c: -distance(c, (0, 0, 0)))
        # Leave the 6 cells closest to the camp tip occupied; the 4 nearest
        # the board's mouth "left" to make room for the advanced pieces.
        stay_home = home_cells[:6]
        for c in stay_home:
            occupancy[c] = seat
        for c in advanced_by_seat[seat]:
            occupancy[c] = seat

    # Seat 5 (yellow) is nearly done: fill its target camp (opposite = camp 2)
    # almost completely and mark it dimmed, like a finished player.
    finished_seat = 5
    target = opposite(finished_seat)
    finished_cells = sorted(CAMPS[target], key=lambda c: distance(c, (0, 0, 0)))
    for c in finished_cells[:9]:
        occupancy[c] = finished_seat
    occupancy[list(CAMPS[finished_seat])[0]] = finished_seat  # one straggler left at home

    # Current player: seat 0 (red). Pick one of its advanced pieces as the
    # selected piece, its other pieces as "selectable", and fabricate a
    # plausible step/jump/path around it.
    red_cells = [c for c, s in occupancy.items() if s == 0]
    selected = advanced_by_seat[0][0]
    selectable = frozenset(c for c in red_cells if c != selected)

    def neighbors_of(cell):
        from chinese_checkers.core.coords import DIRECTIONS, add

        return [add(cell, d) for d in DIRECTIONS]

    empty = [c for c in BOARD if c not in occupancy]
    step_targets = frozenset(list(c for c in neighbors_of(selected) if c in empty)[:2])

    # A jump target: two steps away in some direction, over an occupied cell.
    jump_candidates = []
    from chinese_checkers.core.coords import DIRECTIONS, add, scale

    for d in DIRECTIONS:
        mid = add(selected, d)
        far = add(selected, scale(d, 2))
        if mid in occupancy and far in empty:
            jump_candidates.append(far)
    jump_targets = frozenset(jump_candidates[:2])

    # A 3-hop path: origin + two landings (a walk then a jump, say).
    path = [selected]
    if step_targets:
        path.append(next(iter(step_targets)))
    if jump_targets:
        path.append(next(iter(jump_targets)))
    if len(path) < 3 and jump_candidates:
        path.append(jump_candidates[0])
    path = tuple(path) if len(path) >= 2 else ()

    hover = next((c for c in empty if c not in step_targets and c not in jump_targets), None)

    last_move = None
    blue_cells = [c for c, s in occupancy.items() if s == 1]
    if blue_cells:
        last_move = (advanced_by_seat[1][-1] if advanced_by_seat[1] else blue_cells[0], blue_cells[0])

    return Scene(
        occupancy=occupancy,
        colors=DEFAULT_COLORS,
        target_camp=opposite(0),
        selectable=selectable,
        selected=selected,
        step_targets=step_targets,
        jump_targets=jump_targets,
        path=path,
        hover=hover,
        last_move=last_move,
        dim_seats=frozenset({finished_seat}),
        active_seat=0,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--shot", type=str, default=None, help="screenshot a real Tk window here and exit")
    parser.add_argument(
        "--png",
        type=str,
        default=None,
        help="render offscreen straight to this PNG (no window; the reliable path)",
    )
    parser.add_argument("--no-pil", action="store_true", help="force the pure-Tkinter marble fallback")
    parser.add_argument("--width", type=int, default=900)
    parser.add_argument("--height", type=int, default=820)
    args = parser.parse_args()

    import chinese_checkers.ui.render as render_mod

    if args.no_pil:
        render_mod.HAS_PIL = False

    if args.png:
        # ImageTk.PhotoImage still needs a Tk interpreter to exist, but the
        # window never has to be mapped -- which is the whole point.
        from canvas_png import PngCanvas

        root = tk.Tk()
        root.withdraw()
        canvas = PngCanvas(args.width, args.height, scale=2)
        renderer = render_mod.BoardRenderer(canvas)
        renderer.layout(args.width * canvas.scale, args.height * canvas.scale)
        renderer.draw(build_scene())
        canvas.save(args.png)
        root.destroy()
        return

    root = tk.Tk()
    root.title("中国跳棋 -- 渲染预览" + ("（无 Pillow）" if args.no_pil else ""))
    canvas = tk.Canvas(root, width=args.width, height=args.height, highlightthickness=0)
    canvas.pack(fill=tk.BOTH, expand=True)

    renderer = render_mod.BoardRenderer(canvas)
    renderer.layout(args.width, args.height)
    scene = build_scene()

    def redraw(event=None) -> None:
        renderer.layout(canvas.winfo_width(), canvas.winfo_height())
        renderer.draw(scene)

    canvas.bind("<Configure>", redraw)
    root.update_idletasks()
    redraw()

    if args.shot:
        shoot_and_quit(root, args.shot)

    root.mainloop()


if __name__ == "__main__":
    main()
