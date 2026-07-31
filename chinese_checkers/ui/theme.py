"""Visual constants for the board renderer, gathered in one immutable record.

Keeping every colour, size ratio and font in a single frozen dataclass means
the renderer never hardcodes a magic number: it always asks ``self.theme``.
That makes it trivial to ship a dark theme or a colour-blind-friendly theme
later without touching a single line of drawing code -- swap the ``Theme``
instance passed to :class:`~.render.BoardRenderer`.

Sizes are expressed as *ratios of the hole spacing* (``cell_size``, the pixel
distance between two adjacent holes -- see ``core.coords.to_pixel``), not raw
pixels, so the whole board reflows cleanly when the window is resized.
"""

from __future__ import annotations

import tkinter.font as tkfont
from dataclasses import dataclass, field, replace
from typing import Sequence

from ..core.board import DEFAULT_COLORS

# --------------------------------------------------------------------------
# Colour arithmetic
# --------------------------------------------------------------------------


def _hex_to_rgb(color: str) -> tuple[int, int, int]:
    color = color.lstrip("#")
    if len(color) == 3:
        color = "".join(ch * 2 for ch in color)
    return (int(color[0:2], 16), int(color[2:4], 16), int(color[4:6], 16))


def _rgb_to_hex(r: float, g: float, b: float) -> str:
    def clamp(v: float) -> int:
        return max(0, min(255, int(round(v))))

    return f"#{clamp(r):02X}{clamp(g):02X}{clamp(b):02X}"


def shade(hex_color: str, factor: float) -> str:
    """Scale a colour's RGB channels by ``factor`` (darken if < 1, lighten if > 1).

    Cheap stand-in for a proper HSL lightness adjustment -- good enough for the
    small bevels and outlines the renderer needs, and it never leaves the
    plain-hex-string world Tkinter wants.
    """
    r, g, b = _hex_to_rgb(hex_color)
    return _rgb_to_hex(r * factor, g * factor, b * factor)


def mix(a: str, b: str, t: float) -> str:
    """Linear interpolation between two colours; ``t=0`` -> ``a``, ``t=1`` -> ``b``.

    Tkinter's canvas has no real alpha channel, so every "translucent glow" or
    "soft tint" in the renderer is faked by mixing the overlay colour towards
    whatever sits underneath it instead.
    """
    t = max(0.0, min(1.0, t))
    ar, ag, ab = _hex_to_rgb(a)
    br, bg, bb = _hex_to_rgb(b)
    return _rgb_to_hex(ar + (br - ar) * t, ag + (bg - ag) * t, ab + (bb - ab) * t)


# --------------------------------------------------------------------------
# Fonts
# --------------------------------------------------------------------------

#: Fallback chain for a CJK-capable UI face, best macOS choice first.  The
#: whole interface is in Chinese, so a family without Han glyphs would render
#: as tofu boxes.
_FONT_CANDIDATES: tuple[str, ...] = (
    "PingFang SC",
    "Heiti SC",
    "STHeiti",
    "Microsoft YaHei",
    "SimHei",
    "Arial Unicode MS",
    "Helvetica",
    "TkDefaultFont",
)


def pick_font_family(candidates: Sequence[str] = _FONT_CANDIDATES) -> str:
    """Return the first family in ``candidates`` that Tk actually has installed.

    Requires a Tk root to already exist (``tkinter.font.families()`` needs
    one), so this cannot run at import time -- call it once a canvas/root is
    live, e.g. from :func:`resolved`.
    """
    try:
        available = set(tkfont.families())
    except Exception:
        return candidates[-1]
    for name in candidates:
        if name in available:
            return name
    return candidates[-1]


# --------------------------------------------------------------------------
# Theme
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Theme:
    """Every colour, size ratio and font the renderer consults.  No logic."""

    # -- chrome ------------------------------------------------------------
    app_bg: str = "#F4EFE6"        # warm off-white paper
    panel_bg: str = "#E8E0CF"      # side panel, a touch darker than app_bg

    # -- board -------------------------------------------------------------
    board_fill: str = "#C08A4E"    # wood tone
    board_edge: str = "#5E3A1E"    # dark border around the star silhouette
    board_shadow: str = "#B9AD91"  # drop shadow (solid tone mixed with app_bg)
    board_border_px: int = 2

    # Sockets stay close to the board tone -- a high-contrast dark circle reads
    # as a brown marble rather than as an empty hole.
    hole_fill: str = "#A97742"          # recessed socket
    hole_rim_light: str = "#D9A867"     # thin highlight arc, bottom-right
    hole_rim_dark: str = "#7A5227"      # thin shadow arc, top-left

    # -- overlays ------------------------------------------------------------
    hover_ring: str = "#3A2E1F"   # neutral ink; a hue here would clash with a seat colour
    selectable_glow: str = "#F4D35E"
    selected_ring: str = "#FF5A36"
    step_target: str = "#38A169"
    jump_target_ring: str = "#2B6CB0"
    path_line: str = "#D97706"
    path_badge_fill: str = "#D97706"
    path_badge_text: str = "#FFFFFF"
    target_camp_tint_strength: float = 0.38   # mixed into board/hole colour
    last_move_marker: str = "#9AA3AF"

    # -- text ----------------------------------------------------------------
    text_primary: str = "#2B2117"
    text_muted: str = "#7A6F5D"
    text_danger: str = "#C0392B"
    text_success: str = "#2E7D32"

    # -- seats -----------------------------------------------------------
    seat_colors: tuple[str, ...] = DEFAULT_COLORS

    # -- size ratios, all fractions of cell_size unless noted "px" ---------
    hole_radius: float = 0.30
    hole_rim_width: float = 0.05
    piece_radius: float = 0.40
    marble_outline_width: float = 0.045

    board_margin_units: float = 1.55   # extra hole-spacing padding around BOARD_EXTENT
    star_push: float = 0.85            # outward push of silhouette vertices
    shadow_offset: float = 0.14

    selectable_glow_radius: float = 0.47
    selectable_glow_width: float = 0.06
    hover_ring_radius: float = 0.48
    hover_ring_width: float = 0.07
    selected_ring_radius: float = 0.50
    selected_ring_width: float = 0.09
    step_target_radius: float = 0.20
    jump_target_radius: float = 0.30
    jump_target_width: float = 0.08
    path_line_width: float = 0.09
    path_badge_radius: float = 0.24
    last_move_radius: float = 0.30
    last_move_width: float = 0.05
    target_tint_radius: float = 0.60

    # -- fonts ---------------------------------------------------------------
    font_family: str = "PingFang SC"          # resolved to an installed face at runtime
    font_candidates: tuple[str, ...] = _FONT_CANDIDATES
    title_font_size: int = 22
    ui_font_size: int = 13
    small_font_size: int = 11

    @property
    def title_font(self) -> tuple[str, int, str]:
        return (self.font_family, self.title_font_size, "bold")

    @property
    def ui_font(self) -> tuple[str, int]:
        return (self.font_family, self.ui_font_size)

    @property
    def small_font(self) -> tuple[str, int]:
        return (self.font_family, self.small_font_size)


def resolved(theme: Theme) -> Theme:
    """Return a copy of ``theme`` with ``font_family`` swapped for an installed one.

    Call this once a Tk root exists (e.g. from ``BoardRenderer.__init__``);
    ``DEFAULT`` itself is built at import time, before any Tk root, so it
    keeps a plain guess until resolved.
    """
    return replace(theme, font_family=pick_font_family(theme.font_candidates))


DEFAULT = Theme()
