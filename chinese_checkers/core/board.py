"""Static geometry of the 121-hole six-pointed star board.

Everything here is derived once at import time and is immutable; the game state
never mutates board geometry, it only tracks which player occupies which cell.

The board is the union of two overlapping large triangles::

    {max(x, y, z) <= 4}  union  {min(x, y, z) >= -4}

which yields the central hexagon of 61 cells plus six 10-cell corner triangles,
121 in total.  Each corner camp is exactly the set of cells that violate one of
the six bounds, which makes ``opposite`` a trivial ``(camp + 3) % 6``.
"""

from __future__ import annotations

from .coords import Hex, to_pixel

#: Half-width of the central hexagon; the camps occupy coordinates beyond it.
RADIUS = 4

#: Pieces per camp.
PIECES_PER_CAMP = 10


def _build_board() -> frozenset[Hex]:
    cells = []
    limit = 2 * RADIUS
    for x in range(-limit, limit + 1):
        for y in range(-limit, limit + 1):
            z = -x - y
            if abs(z) > limit:
                continue
            if max(x, y, z) <= RADIUS or min(x, y, z) >= -RADIUS:
                cells.append((x, y, z))
    return frozenset(cells)


#: All 121 playable holes.
BOARD: frozenset[Hex] = _build_board()

#: Deterministic top-to-bottom, left-to-right ordering (handy for rendering and
#: for stable serialisation).
BOARD_SORTED: tuple[Hex, ...] = tuple(sorted(BOARD, key=lambda c: (c[2], c[0])))

# Each camp is "the cells that push one coordinate past the central hexagon".
# Index order is clockwise on screen starting from the top point.
_CAMP_TESTS = (
    lambda c: c[2] <= -RADIUS - 1,  # 0  top
    lambda c: c[0] >= RADIUS + 1,   # 1  upper right
    lambda c: c[1] <= -RADIUS - 1,  # 2  lower right
    lambda c: c[2] >= RADIUS + 1,   # 3  bottom
    lambda c: c[0] <= -RADIUS - 1,  # 4  lower left
    lambda c: c[1] >= RADIUS + 1,   # 5  upper left
)

#: The six corner triangles, 10 cells each.
CAMPS: tuple[frozenset[Hex], ...] = tuple(
    frozenset(c for c in BOARD if test(c)) for test in _CAMP_TESTS
)

#: Reverse lookup: cell -> camp index, or ``None`` for the central hexagon.
CAMP_OF: dict[Hex, int | None] = {c: None for c in BOARD}
for _i, _camp in enumerate(CAMPS):
    for _cell in _camp:
        CAMP_OF[_cell] = _i

#: The single tip cell of each camp -- the far corner a player starts from.
CAMP_TIPS: tuple[Hex, ...] = tuple(
    max(camp, key=lambda c, i=i: abs(to_pixel(c)[0]) + abs(to_pixel(c)[1]))
    for i, camp in enumerate(CAMPS)
)


def opposite(camp: int) -> int:
    """The camp a player starting at ``camp`` must fill to win."""
    return (camp + 3) % 6


def camp_of(cell: Hex) -> int | None:
    return CAMP_OF.get(cell)


#: Which camps are occupied for each supported player count.  Seats are listed
#: in clockwise order so that turn order rotates around the board.
SEATING: dict[int, tuple[int, ...]] = {
    2: (0, 3),              # opposite points
    3: (0, 2, 4),           # alternating -- every camp faces an empty one
    4: (0, 1, 3, 4),        # two opposing pairs
    5: (0, 1, 2, 3, 4),     # asymmetric: camp 2 races into an unopposed camp 5
    6: (0, 1, 2, 3, 4, 5),
}

#: Player counts for which the seating is perfectly symmetric.
BALANCED_COUNTS = frozenset({2, 3, 4, 6})

#: README "method 2": two players, three colours each.  Each player takes three
#: *consecutive* corners, which is what makes the pairing work -- every one of a
#: player's colours then faces one of the opponent's (0<->3, 5<->2, 4<->1),
#: exactly the arrangement drawn in ``assets/2.jpg``.
TRIPLE_SEATING: tuple[tuple[int, ...], ...] = ((4, 5, 0), (1, 2, 3))


def player_camps(player_count: int, colors_each: int = 1) -> tuple[tuple[int, ...], ...]:
    """The camps each player controls, one tuple per player.

    ``colors_each=1`` is the ordinary game (2-6 players, one corner each);
    ``colors_each=3`` is method 2 and is only defined for two players.
    """
    if colors_each == 1:
        if player_count not in SEATING:
            raise ValueError(f"不支持的玩家人数：{player_count}。")
        return tuple((camp,) for camp in SEATING[player_count])
    if colors_each == 3:
        if player_count != 2:
            raise ValueError("三色玩法只支持 2 名玩家。")
        return TRIPLE_SEATING
    raise ValueError(f"不支持的每人颜色数：{colors_each}。")

#: Default colour per camp.  The palette is deliberately jewel-toned rather
#: than fully saturated: it remains distinct on the midnight lacquer board
#: while letting the highlights make every marble feel like real glass.
DEFAULT_COLORS: tuple[str, ...] = (
    "#B94E62",  # 0 garnet   top
    "#5675B9",  # 1 sapphire upper right
    "#46977E",  # 2 jade     lower right
    "#8064A5",  # 3 amethyst bottom
    "#C7834B",  # 4 amber    lower left
    "#BFA65B",  # 5 citrine  upper left
)

#: Human-readable colour names, same order as :data:`DEFAULT_COLORS`.
COLOR_NAMES: tuple[str, ...] = ("红", "蓝", "绿", "紫", "橙", "黄")

#: The twelve cells on the star's outline, in order around the perimeter:
#: camp tip, inner notch, camp tip, ...  Renderers walk this to draw the board
#: silhouette (expanded outwards by a margin).
STAR_OUTLINE: tuple[Hex, ...] = (
    (RADIUS, RADIUS, -2 * RADIUS),      # tip of camp 0
    (RADIUS, 0, -RADIUS),               # notch
    (2 * RADIUS, -RADIUS, -RADIUS),     # tip of camp 1
    (RADIUS, -RADIUS, 0),               # notch
    (RADIUS, -2 * RADIUS, RADIUS),      # tip of camp 2
    (0, -RADIUS, RADIUS),               # notch
    (-RADIUS, -RADIUS, 2 * RADIUS),     # tip of camp 3
    (-RADIUS, 0, RADIUS),               # notch
    (-2 * RADIUS, RADIUS, RADIUS),      # tip of camp 4
    (-RADIUS, RADIUS, 0),               # notch
    (-RADIUS, 2 * RADIUS, -RADIUS),     # tip of camp 5
    (0, RADIUS, -RADIUS),               # notch
)


def _extent() -> tuple[float, float, float, float]:
    xs, ys = zip(*(to_pixel(c) for c in BOARD))
    return (min(xs), min(ys), max(xs), max(ys))


#: ``(min_x, min_y, max_x, max_y)`` of :func:`~.coords.to_pixel` over the whole
#: board, in hole-spacing units.  Renderers use it to fit the board to a canvas.
BOARD_EXTENT: tuple[float, float, float, float] = _extent()
