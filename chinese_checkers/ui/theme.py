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
    "Noto Sans SC",
    "Source Han Sans SC",
    "Noto Sans CJK SC",
    "PingFang SC",
    "Microsoft YaHei UI",
    "微软雅黑",
    "Heiti SC",
    "STHeiti",
    "Microsoft YaHei",
    "WenQuanYi Micro Hei",
    "SimHei",
    "黑体",
    "Arial Unicode MS",
    "Helvetica",
    "TkDefaultFont",
)

# Small Chinese copy needs aggressive screen hinting.  YaHei UI is tuned for
# Windows' ClearType rasteriser, so prefer it for captions even when Noto Sans
# SC remains the more characterful body face.
_CAPTION_FONT_CANDIDATES: tuple[str, ...] = (
    "Microsoft YaHei UI",
    "微软雅黑",
    "Microsoft YaHei",
    "Noto Sans SC",
    "Source Han Sans SC",
    "Noto Sans CJK SC",
    *_FONT_CANDIDATES,
)

_DISPLAY_FONT_CANDIDATES: tuple[str, ...] = (
    "华文行楷",
    "STXingkai",
    "Xingkai SC",
    "Kaiti SC",
    "STKaiti",
    "华文楷体",
    "楷体",
    "KaiTi",
    "Noto Serif SC Medium",
    "Noto Serif SC",
    "TkDefaultFont",
)

_HEADING_FONT_CANDIDATES: tuple[str, ...] = (
    "Noto Serif SC SemiBold",
    "Noto Serif SC Medium",
    "Source Han Serif SC",
    "Noto Serif CJK SC",
    "Songti SC",
    "STSong",
    "华文中宋",
    "SimSun",
    "宋体",
    *_FONT_CANDIDATES,
)

_NUMERIC_FONT_CANDIDATES: tuple[str, ...] = (
    "Palatino Linotype",
    "Palatino",
    "Georgia",
    "Noto Serif SC Medium",
    *_HEADING_FONT_CANDIDATES,
)


def pick_font_family(candidates: Sequence[str] = _FONT_CANDIDATES) -> str:
    """Return the first family in ``candidates`` that Tk actually has installed.

    Requires a Tk root to already exist (``tkinter.font.families()`` needs
    one), so this cannot run at import time -- call it once a canvas/root is
    live, e.g. from :func:`resolved`.
    """
    try:
        available = {name.casefold(): name for name in tkfont.families()}
    except Exception:
        return candidates[-1]
    for name in candidates:
        match = available.get(name.casefold())
        if match is not None:
            return match
    return candidates[-1]


# --------------------------------------------------------------------------
# Theme
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Theme:
    """Every colour, size ratio and font the renderer consults.  No logic."""

    # -- chrome ------------------------------------------------------------
    # Xuan paper and mineral pigments.  ``app_bg`` is also the graceful
    # fallback when the generated watercolour assets are unavailable.
    app_bg: str = "#F1EBDD"
    panel_bg: str = "#F7F1E5"
    card_bg: str = "#FBF7EE"
    paper_deep: str = "#E8DDC8"
    ink: str = "#273B35"
    cinnabar: str = "#B34A38"
    antique_gold: str = "#B58A43"
    celadon: str = "#B7CBB7"

    # -- board -------------------------------------------------------------
    board_fill: str = "#DCE1CF"    # pale celadon stone
    board_edge: str = "#8B6A38"    # antique-gold rim
    board_shadow: str = "#B8AE9B"  # soft ink shadow on paper
    board_border_px: int = 2

    # Sockets stay close to the board tone -- a high-contrast dark circle reads
    # as a brown marble rather than as an empty hole.
    hole_fill: str = "#B8BDAE"          # warm celadon recess
    hole_rim_light: str = "#EFF0E4"     # paper-light inner edge
    hole_rim_dark: str = "#7F897D"      # soft inner shadow

    # -- overlays ------------------------------------------------------------
    hover_ring: str = "#6E7F73"
    selectable_glow: str = "#C9A052"
    selected_ring: str = "#B34A38"
    step_target: str = "#5D9271"
    jump_target_ring: str = "#3F7782"
    path_line: str = "#B58A43"
    path_badge_fill: str = "#B34A38"
    path_badge_text: str = "#FFFFFF"
    commit_ring: str = "#8E3028"
    target_camp_tint_strength: float = 0.18
    last_move_marker: str = "#8B887C"

    # -- text ----------------------------------------------------------------
    text_primary: str = "#273B35"
    text_muted: str = "#776F61"
    text_danger: str = "#A43F35"
    text_success: str = "#47775D"

    # -- seats -----------------------------------------------------------
    seat_colors: tuple[str, ...] = DEFAULT_COLORS

    # -- size ratios, all fractions of cell_size unless noted "px" ---------
    hole_radius: float = 0.30
    hole_rim_width: float = 0.05
    piece_radius: float = 0.40
    marble_outline_width: float = 0.045

    board_margin_units: float = 1.65
    star_push: float = 0.82
    shadow_offset: float = 0.16

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
    font_family: str = "Noto Sans SC"
    font_candidates: tuple[str, ...] = _FONT_CANDIDATES
    title_font_family: str = "华文行楷"
    title_font_candidates: tuple[str, ...] = _DISPLAY_FONT_CANDIDATES
    heading_font_family: str = "Noto Serif SC SemiBold"
    heading_font_candidates: tuple[str, ...] = _HEADING_FONT_CANDIDATES
    numeric_font_family: str = "Palatino Linotype"
    numeric_font_candidates: tuple[str, ...] = _NUMERIC_FONT_CANDIDATES
    caption_font_family: str = "Microsoft YaHei UI"
    caption_font_candidates: tuple[str, ...] = _CAPTION_FONT_CANDIDATES
    display_scale: float = 1.0
    title_font_size: int = 25
    heading_font_size: int = 15
    ui_font_size: int = 13
    small_font_size: int = 12

    def px(self, value: float) -> int:
        """Convert a logical UI pixel to a crisp device-pixel measurement."""
        return max(1, round(value * self.display_scale))

    @property
    def title_font(self) -> tuple[str, int, str]:
        return (self.title_font_family, self.title_font_size, "normal")

    @property
    def heading_font(self) -> tuple[str, int, str]:
        return (self.heading_font_family, self.heading_font_size, "bold")

    @property
    def button_font(self) -> tuple[str, int]:
        return (self.heading_font_family, self.ui_font_size)

    @property
    def ui_font(self) -> tuple[str, int]:
        return (self.font_family, self.ui_font_size)

    @property
    def small_font(self) -> tuple[str, int]:
        return (self.caption_font_family, self.small_font_size)


def resolved(theme: Theme, display_scale: float | None = None) -> Theme:
    """Return a copy of ``theme`` with ``font_family`` swapped for an installed one.

    Call this once a Tk root exists (e.g. from ``BoardRenderer.__init__``);
    ``DEFAULT`` itself is built at import time, before any Tk root, so it
    keeps a plain guess until resolved.
    """
    return replace(
        theme,
        font_family=pick_font_family(theme.font_candidates),
        title_font_family=pick_font_family(theme.title_font_candidates),
        heading_font_family=pick_font_family(theme.heading_font_candidates),
        numeric_font_family=pick_font_family(theme.numeric_font_candidates),
        caption_font_family=pick_font_family(theme.caption_font_candidates),
        display_scale=theme.display_scale if display_scale is None else display_scale,
    )


DEFAULT = Theme()
