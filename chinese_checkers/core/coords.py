"""Cube coordinates for the hexagonal lattice the star board lives on.

A cell is a plain ``tuple[int, int, int]`` obeying ``x + y + z == 0``.  Tuples are
used instead of a dataclass because they are hashable, picklable and roughly
twice as fast to compare -- the move generator touches millions of them when a
search bot is running.
"""

from __future__ import annotations

import math

Hex = tuple[int, int, int]

#: The six lattice directions, in clockwise screen order starting from "east".
DIRECTIONS: tuple[Hex, ...] = (
    (1, -1, 0),   # east
    (1, 0, -1),   # north-east
    (0, 1, -1),   # north-west
    (-1, 1, 0),   # west
    (-1, 0, 1),   # south-west
    (0, -1, 1),   # south-east
)

_SQRT3_OVER_2 = math.sqrt(3.0) / 2.0


def add(a: Hex, b: Hex) -> Hex:
    return (a[0] + b[0], a[1] + b[1], a[2] + b[2])


def sub(a: Hex, b: Hex) -> Hex:
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def scale(a: Hex, k: int) -> Hex:
    return (a[0] * k, a[1] * k, a[2] * k)


def neighbors(cell: Hex) -> tuple[Hex, ...]:
    """The six cells adjacent to ``cell`` (no board-membership filtering)."""
    return tuple(add(cell, d) for d in DIRECTIONS)


def distance(a: Hex, b: Hex) -> int:
    d = sub(a, b)
    return (abs(d[0]) + abs(d[1]) + abs(d[2])) // 2


def to_pixel(cell: Hex) -> tuple[float, float]:
    """Map a cell to normalised layout coordinates.

    One unit equals the spacing between two adjacent holes.  ``y`` grows
    downwards to match screen conventions, so the ``z <= -5`` camp renders at
    the top of the star.  Renderers scale and translate the result to fit their
    canvas; see :data:`chinese_checkers.core.board.BOARD_EXTENT`.
    """
    x, _y, z = cell
    return (x + z / 2.0, z * _SQRT3_OVER_2)
