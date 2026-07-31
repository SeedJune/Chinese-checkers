"""User-facing layer: Tkinter rendering and (eventually) widgets/controllers.

Deliberately kept separate from :mod:`chinese_checkers.core` -- everything in
here only knows how to turn plain data (see :class:`~.render.Scene`) into
pixels, never how the rules work.
"""

from __future__ import annotations
