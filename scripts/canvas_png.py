"""An offscreen stand-in for ``tk.Canvas`` that draws into a Pillow image.

Screenshotting a real Tk window is unreliable here: a window opened by a
background process lands on the desktop Space while the editor sits in its own
fullscreen Space, and ``screencapture`` only ever photographs the Space that is
currently showing.  No amount of raising or ``-topmost`` fixes that.

So instead of photographing the renderer's output, we re-target it.  This shim
implements the handful of ``tk.Canvas`` calls
:class:`~chinese_checkers.ui.render.BoardRenderer` actually makes and paints
them with ``PIL.ImageDraw``, which makes visual checks headless, deterministic
and independent of the window manager.  It is a development tool only -- the
game itself never imports it, and the package keeps its zero-dependency core.

Usage note: lay the renderer out at the canvas's *pixel* size
(``width * scale``), not its logical size.  The shim draws 1:1 in pixels and
only downsamples when saving, so the marble bitmaps come out crisp instead of
being generated small and blown up.  ``scale`` therefore buys both
anti-aliasing and a faithful preview of a Retina display.
"""

from __future__ import annotations

import math
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

_FONT_PATHS = (
    "/System/Library/Fonts/PingFang.ttc",
    "/System/Library/Fonts/Helvetica.ttc",
    "/Library/Fonts/Arial Unicode.ttf",
)


def _load_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for path in _FONT_PATHS:
        if Path(path).exists():
            try:
                return ImageFont.truetype(path, size)
            except OSError:
                continue
    return ImageFont.load_default()


def _color(value: str | None) -> str | None:
    """Tk spells "no fill"/"no outline" as the empty string; Pillow wants None."""
    return value if value else None


class PngCanvas:
    """Duck-typed ``tk.Canvas`` that accumulates into a Pillow image."""

    def __init__(self, width: int, height: int, scale: int = 2) -> None:
        self.scale = scale
        self._w, self._h = width * scale, height * scale   # pixel size, drawn 1:1
        self._out = (width, height)                        # size saved to disk
        self._img = Image.new("RGBA", (self._w, self._h), (255, 255, 255, 255))
        self._draw = ImageDraw.Draw(self._img)

    # -- the tk.Canvas surface BoardRenderer uses -------------------------

    def __getitem__(self, key: str) -> int:
        return {"width": self._w, "height": self._h}[key]

    def winfo_width(self) -> int:
        return self._w

    def winfo_height(self) -> int:
        return self._h

    def delete(self, _tag: str) -> None:
        self._draw.rectangle([0, 0, self._img.width, self._img.height], fill=(255, 255, 255, 255))

    def configure(self, **kwargs) -> None:
        bg = kwargs.get("background") or kwargs.get("bg")
        if bg:
            self._draw.rectangle([0, 0, self._img.width, self._img.height], fill=bg)

    def create_polygon(self, *coords, **kw):
        pts = self._points(coords)
        fill = _color(kw.get("fill"))
        outline = _color(kw.get("outline"))
        if fill:
            self._draw.polygon(pts, fill=fill)
        if outline:
            width = max(1, int(round(kw.get("width", 1))))
            self._draw.line(pts + [pts[0]], fill=outline, width=width, joint="curve")

    def create_oval(self, x0, y0, x1, y1, **kw):
        box = self._box(x0, y0, x1, y1)
        fill = _color(kw.get("fill"))
        outline = _color(kw.get("outline"))
        width = max(1, int(round(kw.get("width", 1))))
        self._draw.ellipse(box, fill=fill, outline=outline, width=width)

    def create_arc(self, x0, y0, x1, y1, **kw):
        box = self._box(x0, y0, x1, y1)
        start, extent = kw.get("start", 0), kw.get("extent", 90)
        # Tk measures degrees counter-clockwise from 3 o'clock, Pillow measures
        # them clockwise, so the sweep has to be mirrored.
        self._draw.arc(
            box,
            start=-(start + extent),
            end=-start,
            fill=_color(kw.get("outline")),
            width=max(1, int(round(kw.get("width", 1)))),
        )

    def create_line(self, *coords, **kw):
        pts = self._points(coords)
        fill = _color(kw.get("fill"))
        width = max(1, int(round(kw.get("width", 1))))
        dash = kw.get("dash")
        if dash:
            for a, b in zip(pts, pts[1:]):
                self._dashed(a, b, fill, width, dash)
        else:
            self._draw.line(pts, fill=fill, width=width, joint="curve")

    def create_text(self, x, y, **kw):
        text = str(kw.get("text", ""))
        font_spec = kw.get("font") or ("", 11)
        size = int(font_spec[1]) if len(font_spec) > 1 else 11
        font = _load_font(max(6, int(size * self.scale)))
        self._draw.text(
            (x, y),
            text,
            fill=_color(kw.get("fill")) or "#000000",
            font=font,
            anchor="mm",
        )

    def create_image(self, x, y, **kw):
        photo = kw.get("image")
        src = getattr(photo, "_source_image", None)
        if src is None:
            return
        # BoardRenderer sizes its marbles for the canvas it was handed, which
        # is already the supersampled one, so paste at 1:1 and only recentre.
        px = int(round(x - src.width / 2))
        py = int(round(y - src.height / 2))
        self._img.alpha_composite(src, (px, py))

    # -- helpers ------------------------------------------------------------

    def _points(self, coords) -> list[tuple[float, float]]:
        flat = list(coords)
        if len(flat) == 1 and isinstance(flat[0], (list, tuple)):
            flat = list(flat[0])
        return [(flat[i], flat[i + 1]) for i in range(0, len(flat) - 1, 2)]

    def _box(self, x0, y0, x1, y1) -> list[float]:
        return [x0, y0, x1, y1]

    def _dashed(self, a, b, fill, width, dash) -> None:
        on = max(1.0, float(dash[0]))
        off = max(1.0, float(dash[1] if len(dash) > 1 else dash[0]))
        dx, dy = b[0] - a[0], b[1] - a[1]
        length = math.hypot(dx, dy)
        if length == 0:
            return
        ux, uy = dx / length, dy / length
        pos = 0.0
        while pos < length:
            end = min(pos + on, length)
            self._draw.line(
                [(a[0] + ux * pos, a[1] + uy * pos), (a[0] + ux * end, a[1] + uy * end)],
                fill=fill,
                width=width,
            )
            pos = end + off

    def save(self, path: str | Path) -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._img.convert("RGB").resize(self._out, Image.LANCZOS).save(path)
        print(f"已保存离屏渲染: {path}")
