"""Tkinter drawing layer for the star board.

The renderer knows nothing about turns, legality or players -- it only knows
how to paint a :class:`Scene`, a plain data snapshot of "what's on the board
and what should be highlighted right now".  That decoupling is deliberate:
the same :class:`BoardRenderer` draws the live game, a paused review, and the
start menu's tiny seating previews, and it can be unit-tested by constructing
a ``Scene`` by hand -- no game engine required.

Geometry comes entirely from :mod:`chinese_checkers.core.coords` /
:mod:`chinese_checkers.core.board`.  Everything the renderer draws is scaled
from a single number, ``cell_size`` (the pixel distance between two adjacent
holes), so a resize is just "recompute ``cell_size`` and redraw" -- no cached
pixel positions to invalidate by hand beyond that.
"""

from __future__ import annotations

import tkinter as tk
from dataclasses import dataclass, field
from pathlib import Path
from typing import Sequence

from ..core.board import (
    BOARD_EXTENT,
    BOARD_SORTED,
    CAMPS,
    DEFAULT_COLORS,
    STAR_OUTLINE,
)
from ..core.coords import Hex, to_pixel
from .theme import DEFAULT, Theme, mix, resolved, shade

#: Set to False (by tests / the preview script) to force the pure-Tkinter
#: marble fallback even when Pillow is importable, so both code paths can be
#: screenshotted and compared.
HAS_PIL = True
try:
    from PIL import Image, ImageDraw, ImageTk
except ImportError:  # pragma: no cover - exercised on machines without Pillow
    HAS_PIL = False

def _w(px: float) -> int:
    """Clamp a computed line width to at least 1 device pixel."""
    return max(1, round(px))


@dataclass
class Scene:
    """Everything to draw, as plain data.

    Deliberately independent of any game-state class so the renderer can be
    unit-tested and reused for the start menu's mini previews -- it only ever
    reads this dataclass.
    """

    occupancy: dict[Hex, int] = field(default_factory=dict)      # cell -> seat index
    colors: tuple[str, ...] = DEFAULT_COLORS                     # seat index -> colour
    target_camp: int | None = None                # tint this camp (current player's goal)
    selectable: frozenset[Hex] = frozenset()       # soft glow: pieces that can move
    selected: Hex | None = None                    # pulsing ring
    step_targets: frozenset[Hex] = frozenset()     # small filled dots
    jump_targets: frozenset[Hex] = frozenset()     # hollow rings
    path: tuple[Hex, ...] = ()                     # origin, landing1, landing2, ... -> dashed polyline + badges
    hover: Hex | None = None
    last_move: tuple[Hex, Hex] | None = None       # faint start/end markers
    floating: tuple[int, float, float] | None = None   # (seat, canvas_x, canvas_y): mid-animation piece
    dim_seats: frozenset[int] = frozenset()        # finished players' pieces render desaturated
    active_seat: int | None = None                 # whose turn it is; tints every overlay
    can_confirm: bool = False                       # current endpoint accepts a confirming click


class BoardRenderer:
    """Paints a :class:`Scene` onto a ``tk.Canvas``."""

    def __init__(self, canvas: tk.Canvas, theme: Theme = DEFAULT) -> None:
        self.canvas = canvas
        try:
            self.theme = resolved(theme)
        except Exception:
            self.theme = theme
        self._use_pil = HAS_PIL
        self._cell_size: float = 24.0
        self._origin: tuple[float, float] = (0.0, 0.0)
        self._width = int(canvas["width"] or 1) or 1
        self._height = int(canvas["height"] or 1) or 1
        # Keeps every PhotoImage alive -- Tk shows a blank canvas the instant
        # the last Python reference to an image is dropped, garbage collector
        # or not.
        self._marble_cache: dict[tuple[str, int], "ImageTk.PhotoImage"] = {}
        self._backdrop: tk.PhotoImage | None = None
        self._backdrop_loaded = False

    # -- layout --------------------------------------------------------

    def layout(self, width: int, height: int) -> None:
        """Recompute ``cell_size``/origin to fit :data:`BOARD_EXTENT` into
        ``width`` x ``height`` with a margin.  Call on every ``<Configure>``.
        """
        self._width, self._height = max(1, width), max(1, height)
        min_x, min_y, max_x, max_y = BOARD_EXTENT
        # The silhouette's points stick out twice as far as ``star_push`` (see
        # _pushed_outline), so the padding has to clear that or the top and
        # bottom tips get sliced off by the canvas edge.
        margin = max(self.theme.board_margin_units, 2.0 * self.theme.star_push + 0.45)
        units_w = (max_x - min_x) + 2 * margin
        units_h = (max_y - min_y) + 2 * margin
        cell_size = max(4.0, min(self._width / units_w, self._height / units_h))
        self._cell_size = cell_size
        board_px_w = (max_x - min_x) * cell_size
        board_px_h = (max_y - min_y) * cell_size
        origin_x = (self._width - board_px_w) / 2.0 - min_x * cell_size
        origin_y = (self._height - board_px_h) / 2.0 - min_y * cell_size
        self._origin = (origin_x, origin_y)

    @property
    def cell_size(self) -> float:
        return self._cell_size

    def center_of(self, cell: Hex) -> tuple[float, float]:
        fx, fy = to_pixel(cell)
        ox, oy = self._origin
        return (ox + fx * self._cell_size, oy + fy * self._cell_size)

    def cell_at(self, x: float, y: float) -> Hex | None:
        """Nearest cell to canvas point ``(x, y)``, or ``None`` if too far
        (more than ~half a hole spacing) from every hole.  A linear scan of
        121 cells is trivially fast.
        """
        threshold = 0.5 * self._cell_size
        best: Hex | None = None
        best_d2 = threshold * threshold
        for cell in BOARD_SORTED:
            cx, cy = self.center_of(cell)
            d2 = (cx - x) ** 2 + (cy - y) ** 2
            if d2 <= best_d2:
                best_d2 = d2
                best = cell
        return best

    # -- silhouette geometry --------------------------------------------

    def _pushed_outline(self, push_factor: float) -> list[tuple[float, float]]:
        """``STAR_OUTLINE`` grown outwards by a *constant edge clearance*.

        Pushing each vertex radially from the board centre would be wrong: at
        the star's 60-degree points a radial push of ``d`` only buys
        ``d * sin(30) = d/2`` of clearance from the edges, which shaves the tips
        into needles and lets the outermost marbles spill over the rim.  The
        standard miter offset -- shift each vertex along its angle bisector by
        ``d / sin(theta/2)`` -- keeps every edge exactly ``d`` away from the
        original outline, so tips move out twice as far as flat sections and
        the notches follow suit.
        """
        pts = [self.center_of(cell) for cell in STAR_OUTLINE]
        n = len(pts)
        push = push_factor * self._cell_size

        # Shoelace sign tells us the winding, which fixes which side of an edge
        # counts as "outward".  Clockwise on screen (y down) gives a positive
        # area, and then the outward normal of edge (ex, ey) is (ey, -ex).
        area = sum(
            pts[i][0] * pts[(i + 1) % n][1] - pts[(i + 1) % n][0] * pts[i][1]
            for i in range(n)
        )
        sign = 1.0 if area > 0 else -1.0

        def unit(a: tuple[float, float], b: tuple[float, float]) -> tuple[float, float]:
            dx, dy = b[0] - a[0], b[1] - a[1]
            length = (dx * dx + dy * dy) ** 0.5 or 1.0
            return (dx / length, dy / length)

        out: list[tuple[float, float]] = []
        for i in range(n):
            prev_p, v, next_p = pts[i - 1], pts[i], pts[(i + 1) % n]
            e1 = unit(prev_p, v)
            e2 = unit(v, next_p)
            n1 = (sign * e1[1], -sign * e1[0])
            n2 = (sign * e2[1], -sign * e2[0])
            mx, my = n1[0] + n2[0], n1[1] + n2[1]
            denom = 1.0 + (n1[0] * n2[0] + n1[1] * n2[1])
            if abs(denom) < 1e-6:  # 180-degree turn-back; fall back to one normal
                mx, my, denom = n1[0], n1[1], 1.0
            out.append((v[0] + push * mx / denom, v[1] + push * my / denom))
        return out

    def _camp_triangle(
        self, camp: int, push_factor: float | None = None
    ) -> tuple[tuple[float, float], ...]:
        """Silhouette triangle for one camp: (notch, tip, notch).

        ``STAR_OUTLINE`` alternates tip/notch starting at camp 0's tip, so
        camp ``i``'s tip sits at index ``2*i`` and its two flanking notches
        at ``2*i - 1`` and ``2*i + 1`` (mod 12).
        """
        if push_factor is None:
            push_factor = self.theme.star_push
        outline = self._pushed_outline(push_factor)
        tip = outline[(2 * camp) % 12]
        left = outline[(2 * camp - 1) % 12]
        right = outline[(2 * camp + 1) % 12]
        return (left, tip, right)

    # -- full board draw --------------------------------------------------

    def draw(self, scene: Scene) -> None:
        canvas = self.canvas
        theme = self.theme
        canvas.delete("all")
        canvas.configure(background=theme.app_bg)
        self._draw_backdrop()

        self._draw_silhouette()
        self._draw_target_tint(scene)
        self._draw_holes()

        for cell, seat in scene.occupancy.items():
            self._draw_marble(cell, seat, scene, dim=seat in scene.dim_seats)

        self._draw_last_move(scene)
        self._draw_selectable_glow(scene)
        self._draw_hover(scene)
        self._draw_step_targets(scene)
        self._draw_jump_targets(scene)
        self._draw_path(scene)
        self._draw_selected(scene)

        if scene.floating is not None:
            seat, x, y = scene.floating
            self._draw_marble_at(x, y, seat, scene, dim=seat in scene.dim_seats)

    def _draw_backdrop(self) -> None:
        """Centre-crop the generated xuan-paper painting behind the board.

        Loading is lazy: start-menu mini renderers share this class but never
        draw a full scene, so they should not each retain a multi-megabyte PNG.
        """
        if not self._backdrop_loaded:
            self._backdrop_loaded = True
            if isinstance(self.canvas, tk.Canvas):
                asset = (
                    Path(__file__).resolve().parents[2]
                    / "assets"
                    / "ui"
                    / "guochao-game-v2.png"
                )
                try:
                    source = tk.PhotoImage(file=str(asset))
                    zoom = max(1, round(self.theme.display_scale))
                    self._backdrop = source.zoom(zoom) if zoom > 1 else source
                except tk.TclError:
                    self._backdrop = None
        if self._backdrop is not None:
            self.canvas.create_image(
                self._width / 2,
                self._height / 2,
                image=self._backdrop,
                anchor=tk.CENTER,
            )

    # -- silhouette / holes -------------------------------------------------

    def _draw_silhouette(self) -> None:
        theme = self.theme
        canvas = self.canvas
        cs = self._cell_size

        shadow_pts = self._pushed_outline(theme.star_push)
        # Three quiet offset silhouettes approximate a soft ink shadow without
        # alpha support and avoid the cut-out sticker look of one hard shadow.
        for factor, color in (
            (0.25, mix(theme.app_bg, theme.board_shadow, 0.18)),
            (0.65, mix(theme.app_bg, theme.board_shadow, 0.30)),
            (1.00, theme.board_shadow),
        ):
            offset = theme.shadow_offset * cs * factor
            shadow_flat: list[float] = []
            for x, y in shadow_pts:
                shadow_flat += [x + offset, y + offset]
            canvas.create_polygon(*shadow_flat, fill=color, outline="", smooth=False)

        outline_pts = self._pushed_outline(theme.star_push)
        flat = [c for pt in outline_pts for c in pt]
        canvas.create_polygon(
            *flat,
            fill=theme.board_fill,
            outline=theme.board_edge,
            width=theme.px(theme.board_border_px),
            joinstyle=tk.ROUND,
        )

        # Thin celadon and antique-gold layers: more like a carved scholar's
        # board than a glossy game-app polygon.
        cx = sum(p[0] for p in outline_pts) / len(outline_pts)
        cy = sum(p[1] for p in outline_pts) / len(outline_pts)
        bevel_flat: list[float] = []
        for x, y in outline_pts:
            bevel_flat += [cx + (x - cx) * 0.972, cy + (y - cy) * 0.972]
        canvas.create_polygon(
            *bevel_flat,
            fill=mix(theme.board_fill, "#FFFFFF", 0.34),
            outline=theme.board_edge,
            width=max(1, theme.px(theme.board_border_px) - 1),
        )
        core_flat: list[float] = []
        for x, y in outline_pts:
            core_flat += [cx + (x - cx) * 0.935, cy + (y - cy) * 0.935]
        canvas.create_polygon(
            *core_flat,
            fill=theme.board_fill,
            outline=mix(theme.board_fill, theme.ink, 0.18),
            width=theme.px(1),
        )

    def _draw_holes(self) -> None:
        theme = self.theme
        canvas = self.canvas
        r = theme.hole_radius * self._cell_size
        rim_w = _w(theme.hole_rim_width * self._cell_size)
        for cell in BOARD_SORTED:
            x, y = self.center_of(cell)
            # Broad umbra first, then the narrower socket: this small two-step
            # depth cue keeps the empty cells quiet but convincingly recessed.
            outer = r * 1.10
            canvas.create_oval(
                x - outer, y - outer, x + outer, y + outer,
                fill=theme.hole_rim_dark, outline="",
            )
            canvas.create_oval(
                x - r, y - r, x + r, y + r, fill=theme.hole_fill, outline=""
            )
            # Shadow arc, upper-left -- reads as the far recessed wall.
            canvas.create_arc(
                x - r, y - r, x + r, y + r,
                start=105, extent=150,
                style=tk.ARC, outline=theme.hole_rim_dark, width=rim_w,
            )
            # Highlight arc, lower-right -- catches the (implied) light.
            canvas.create_arc(
                x - r, y - r, x + r, y + r,
                start=285, extent=135,
                style=tk.ARC, outline=theme.hole_rim_light, width=rim_w,
            )

    def _draw_target_tint(self, scene: Scene) -> None:
        if scene.target_camp is None:
            return
        theme = self.theme
        canvas = self.canvas
        seat_color = self._active_seat_color(scene)
        tint = mix(theme.board_fill, seat_color, theme.target_camp_tint_strength)
        triangle = self._camp_triangle(scene.target_camp, self.theme.star_push * 0.58)
        flat = [coord for point in triangle for coord in point]
        canvas.create_polygon(
            *flat,
            fill=tint,
            outline=mix(theme.board_fill, seat_color, 0.38),
            width=max(1, round(self._cell_size * 0.035)),
        )

    # -- marbles -----------------------------------------------------------

    def _marble_color(self, scene: Scene, seat: int, dim: bool) -> str:
        color = scene.colors[seat] if seat < len(scene.colors) else "#888888"
        if dim:
            # Enough desaturation to read as "out of play", not so much that a
            # dimmed seat starts looking like a different player's colour.
            color = mix(color, "#9A9A9A", 0.25)
        return color

    def _draw_marble(self, cell: Hex, seat: int, scene: Scene, dim: bool) -> None:
        x, y = self.center_of(cell)
        self._draw_marble_at(x, y, seat, scene, dim)

    def _draw_marble_at(
        self, x: float, y: float, seat: int, scene: Scene, dim: bool
    ) -> None:
        color = self._marble_color(scene, seat, dim)
        radius = self.theme.piece_radius * self._cell_size
        if self._use_pil:
            self._blit_pil_marble(x, y, color, radius)
        else:
            self._draw_tk_marble(x, y, color, radius)

    def _draw_tk_marble(self, x: float, y: float, color: str, r: float) -> None:
        """Mineral-pigment stone using layered, offset colour stops.

        Tk has no gradients, but a dozen softly shifting ellipses give the
        same light direction as the optional high-resolution Pillow path.  It
        looks deliberate at large sizes and stays clean at tiny board sizes.
        """
        canvas = self.canvas
        outline_w = _w(self.theme.marble_outline_width * self._cell_size)
        # A broad, warm contact shadow seats the piece in its carved socket.
        canvas.create_oval(
            x - r * 0.84, y - r * 0.70, x + r * 0.98, y + r * 1.12,
            fill=mix(self.theme.board_fill, self.theme.ink, 0.42), outline="",
        )
        canvas.create_oval(x - r, y - r, x + r, y + r, fill=shade(color, 0.66), outline="")
        stops = 9
        for i in range(1, stops + 1):
            t = i / stops
            rr = r * (1.0 - 0.061 * i)
            ox = -r * 0.13 * t
            oy = -r * 0.16 * t
            lit = mix(shade(color, 0.82), mix(color, "#FFF8E9", 0.28), t * 0.78)
            canvas.create_oval(x - rr + ox, y - rr + oy, x + rr + ox, y + rr + oy, fill=lit, outline="")
        # A narrow glazed highlight keeps the stone lively without turning it
        # into a candy-like glass marble.
        canvas.create_oval(
            x - r * 0.50, y - r * 0.54, x - r * 0.20, y - r * 0.25,
            fill=mix(color, "#FFF9EA", 0.78), outline="",
        )
        canvas.create_arc(
            x - r * 0.79, y - r * 0.79, x + r * 0.79, y + r * 0.79,
            start=285, extent=92, style=tk.ARC,
            outline=mix(color, "#E7EEE4", 0.58), width=max(1, outline_w),
        )
        canvas.create_oval(
            x - r, y - r, x + r, y + r,
            outline=mix(shade(color, 0.48), theme.board_edge, 0.22), width=outline_w
        )

    def _blit_pil_marble(self, x: float, y: float, color: str, radius: float) -> None:
        key = (color, int(round(radius)))
        photo = self._marble_cache.get(key)
        if photo is None:
            photo = self._make_pil_marble(color, radius)
            self._marble_cache[key] = photo
        self.canvas.create_image(x, y, image=photo)

    @staticmethod
    def _hex_to_rgba(color: str, alpha: int = 255) -> tuple[int, int, int, int]:
        color = color.lstrip("#")
        return (int(color[0:2], 16), int(color[2:4], 16), int(color[4:6], 16), alpha)

    def _make_pil_marble(self, color: str, radius: float) -> "ImageTk.PhotoImage":
        """Pre-render one glazed mineral stone at 4x and downscale.

        Cached by ``(color, int(radius))`` on the renderer so repeated pieces
        of the same colour/size are drawn once, not once per frame.
        """
        scale = 4
        d = max(2, int(round(radius * 2)))
        size = d * scale
        img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        cx = cy = size / 2.0
        r = size / 2.0 - scale

        draw.ellipse(
            [cx - r, cy - r, cx + r, cy + r],
            fill=self._hex_to_rgba(shade(color, 0.66)),
        )
        for i in range(1, 19):
            t = i / 18
            rr = r * (1.0 - 0.0305 * i)
            ox = -r * 0.13 * t
            oy = -r * 0.16 * t
            lit = mix(shade(color, 0.82), mix(color, "#FFF8E9", 0.28), t * 0.78)
            draw.ellipse(
                [cx - rr + ox, cy - rr + oy, cx + rr + ox, cy + rr + oy],
                fill=self._hex_to_rgba(lit),
            )
        draw.ellipse(
            [cx - r * 0.50, cy - r * 0.54, cx - r * 0.20, cy - r * 0.25],
            fill=self._hex_to_rgba(mix(color, "#FFF9EA", 0.78)),
        )
        draw.arc(
            [cx - r * 0.79, cy - r * 0.79, cx + r * 0.79, cy + r * 0.79],
            start=285, end=377, fill=self._hex_to_rgba(mix(color, "#E7EEE4", 0.58)),
            width=max(1, scale),
        )
        draw.ellipse(
            [cx - r, cy - r, cx + r, cy + r],
            outline=self._hex_to_rgba(mix(shade(color, 0.48), self.theme.board_edge, 0.22)),
            width=max(1, scale),
        )
        img = img.resize((d, d), Image.LANCZOS)
        photo = ImageTk.PhotoImage(img)
        # Keep the source bitmap reachable: the offscreen QA canvas in
        # scripts/canvas_png.py has to paste real pixels, and PhotoImage
        # offers no way back to them.
        photo._source_image = img  # type: ignore[attr-defined]
        return photo

    # -- overlays ------------------------------------------------------

    def _active_seat_color(self, scene: Scene) -> str:
        """The colour every overlay is tinted with.

        Overlays borrow the current player's colour rather than using fixed
        hues, so a highlight is never mistaken for another player's marble and
        the board reads as "this is *your* turn" at a glance.
        """
        theme = self.theme
        if scene.active_seat is not None and scene.active_seat < len(scene.colors):
            return scene.colors[scene.active_seat]
        if scene.selected is not None and scene.selected in scene.occupancy:
            seat = scene.occupancy[scene.selected]
            return scene.colors[seat] if seat < len(scene.colors) else theme.selectable_glow
        if scene.selectable:
            cell = next(iter(scene.selectable))
            seat = scene.occupancy.get(cell)
            if seat is not None and seat < len(scene.colors):
                return scene.colors[seat]
        if scene.path and scene.path[0] in scene.occupancy:
            seat = scene.occupancy[scene.path[0]]
            return scene.colors[seat] if seat < len(scene.colors) else theme.selectable_glow
        return theme.selectable_glow

    def _draw_last_move(self, scene: Scene) -> None:
        if scene.last_move is None:
            return
        theme = self.theme
        r = theme.last_move_radius * self._cell_size
        w = _w(theme.last_move_width * self._cell_size)
        for cell in scene.last_move:
            x, y = self.center_of(cell)
            self.canvas.create_oval(
                x - r, y - r, x + r, y + r,
                outline=theme.last_move_marker, width=w
            )

    def _draw_selectable_glow(self, scene: Scene) -> None:
        if not scene.selectable:
            return
        theme = self.theme
        r = theme.selectable_glow_radius * self._cell_size
        w = _w(theme.selectable_glow_width * self._cell_size)
        # Sits just outside the marble and stays close to the board tone: this
        # marks a dozen pieces at once, so anything louder turns the board into
        # noise and drowns out the one ring that matters (the selection).
        glow = theme.selectable_glow
        for cell in scene.selectable:
            x, y = self.center_of(cell)
            self.canvas.create_oval(
                x - r, y - r, x + r, y + r, outline=glow, width=w
            )

    def _draw_hover(self, scene: Scene) -> None:
        if scene.hover is None:
            return
        theme = self.theme
        r = theme.hover_ring_radius * self._cell_size
        w = _w(theme.hover_ring_width * self._cell_size)
        x, y = self.center_of(scene.hover)
        self.canvas.create_oval(
            x - r, y - r, x + r, y + r, outline=theme.hover_ring, width=w
        )

    def _draw_step_targets(self, scene: Scene) -> None:
        if not scene.step_targets:
            return
        theme = self.theme
        r = theme.step_target_radius * self._cell_size
        color = theme.step_target
        for cell in scene.step_targets:
            x, y = self.center_of(cell)
            self.canvas.create_oval(
                x - r, y - r, x + r, y + r,
                fill=color, outline=theme.card_bg, width=_w(0.045 * self._cell_size),
            )

    def _draw_jump_targets(self, scene: Scene) -> None:
        if not scene.jump_targets:
            return
        theme = self.theme
        r = theme.jump_target_radius * self._cell_size
        w = _w(theme.jump_target_width * self._cell_size)
        color = theme.jump_target_ring
        for cell in scene.jump_targets:
            x, y = self.center_of(cell)
            self.canvas.create_oval(
                x - r, y - r, x + r, y + r, outline=color, width=w
            )

    def _draw_path(self, scene: Scene) -> None:
        if len(scene.path) < 2:
            return
        theme = self.theme
        canvas = self.canvas
        line_color = theme.path_line
        badge_color = theme.path_badge_fill
        pts = [self.center_of(c) for c in scene.path]
        flat = [c for pt in pts for c in pt]
        w = _w(theme.path_line_width * self._cell_size)
        canvas.create_line(
            *flat,
            fill=line_color,
            width=w,
            dash=(max(2, int(self._cell_size * 0.12)), max(2, int(self._cell_size * 0.09))),
            capstyle=tk.ROUND,
            joinstyle=tk.ROUND,
            smooth=False,
        )
        r = theme.path_badge_radius * self._cell_size
        for i, cell in enumerate(scene.path[1:], start=1):
            x, y = self.center_of(cell)
            canvas.create_oval(
                x - r, y - r, x + r, y + r,
                fill=badge_color, outline=theme.card_bg, width=_w(w * 0.6),
            )
            canvas.create_text(
                # Plain numerals inside the circle we already drew -- the
                # precomposed circled digits are missing from plenty of
                # faces and degrade to tofu boxes when they are.
                x, y, text=str(i), fill=theme.path_badge_text,
                font=theme.small_font,
            )
        # The last stop is also the primary mouse confirmation affordance.  A
        # quiet double halo communicates "click here again" without putting
        # a permanent extra button in the player's path.
        if scene.can_confirm:
            x, y = self.center_of(scene.path[-1])
            commit_r = r * 1.58
            canvas.create_oval(
                x - commit_r, y - commit_r, x + commit_r, y + commit_r,
                outline=theme.commit_ring, width=_w(w * 0.75),
            )

    def _draw_selected(self, scene: Scene) -> None:
        if scene.selected is None:
            return
        theme = self.theme
        r = theme.selected_ring_radius * self._cell_size
        w = _w(theme.selected_ring_width * self._cell_size)
        x, y = self.center_of(scene.selected)
        # Two rings, light over dark: the selection must stay unmistakable on
        # top of any of the six marble colours, and a single hue never can.
        self.canvas.create_oval(
            x - r, y - r, x + r, y + r,
            outline=theme.card_bg, width=_w(w * 1.9)
        )
        self.canvas.create_oval(
            x - r, y - r, x + r, y + r, outline=theme.selected_ring, width=w
        )

    # -- start-menu mini preview -----------------------------------------

    def draw_mini(
        self,
        occupied_camps: Sequence[int],
        colors: tuple[str, ...] = DEFAULT_COLORS,
        groups: Sequence[Sequence[int]] | None = None,
    ) -> None:
        """Tiny preview for the start menu: star silhouette with the given
        camps filled in their colours, no holes, no overlays.

        ``groups`` names which camps belong to the same player.  It only
        matters when a player owns several corners: six filled triangles look
        the same whether they are six players or two players with three
        colours each, so the preview draws the seam between the blocks.
        """
        canvas = self.canvas
        width = canvas.winfo_width()
        height = canvas.winfo_height()
        if width <= 1 or height <= 1:
            width = int(float(canvas["width"] or 60))
            height = int(float(canvas["height"] or 60))
        self.layout(width, height)

        theme = self.theme
        canvas.delete("all")
        canvas.configure(background=theme.card_bg)

        mini_push = theme.star_push * 0.9
        outline_pts = self._pushed_outline(mini_push)
        flat = [c for pt in outline_pts for c in pt]
        canvas.create_polygon(
            *flat, fill=mix(theme.board_fill, theme.card_bg, 0.18), outline=theme.board_edge,
            width=max(1, theme.px(theme.board_border_px) - 1), joinstyle=tk.ROUND,
        )
        for camp in occupied_camps:
            left, tip, right = self._camp_triangle(camp, mini_push)
            color = colors[camp] if camp < len(colors) else theme.text_muted
            canvas.create_polygon(
                left[0], left[1], tip[0], tip[1], right[0], right[1],
                fill=color, outline=shade(color, 0.7),
            )
        if groups is not None and any(len(g) > 1 for g in groups):
            self._draw_mini_seam(outline_pts, groups)

    def _draw_mini_seam(
        self, outline: list[tuple[float, float]], groups: Sequence[Sequence[int]]
    ) -> None:
        """Dashed line across the star where one player's camps meet another's.

        The seam is derived from the grouping instead of being drawn at a fixed
        angle, so it follows the engine's seating rather than restating it: a
        seam sits on the notch between camp ``c`` and camp ``c + 1`` whenever
        those two camps have different owners, and ``STAR_OUTLINE`` puts that
        notch at index ``2 * c + 1``.
        """
        owner = {camp: i for i, camps in enumerate(groups) for camp in camps}
        seams = [
            outline[(2 * camp + 1) % 12]
            for camp in sorted(owner)
            if owner.get((camp + 1) % 6, owner[camp]) != owner[camp]
        ]
        if len(seams) != 2:  # more than two blocks -- no single line can split them
            return
        (x0, y0), (x1, y1) = seams
        dash = max(2, int(self._cell_size * 0.5))
        self.canvas.create_line(
            x0, y0, x1, y1,
            fill=self.theme.board_edge,
            width=_w(0.16 * self._cell_size),
            dash=(dash, dash),
            capstyle=tk.ROUND,
        )
