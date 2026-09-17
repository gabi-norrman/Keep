"""The objects the Crystal tab can show, assembled from axis-aligned boxes."""
from typing import List

from interfaces.console.crystal.Renderer import Material, Mesh, solid

GREEN = Material((110, 205, 88), gloss=1.5, shininess=40, reflectivity=0.4)
SILVER = Material((195, 195, 198), gloss=1.5, shininess=25, reflectivity=0.8)
CHAMFER = 0.025   # the bevel on every edge of every piece, as a fraction of the cube's width


def interlocking_cube(gap: float, green: Material = GREEN, silver: Material = SILVER,
                      chamfer: float = CHAMFER) -> List[Mesh]:
    """The console's logo: a cube cut into two pieces that slot into each other: a green L — a full-height column
    on the front-right corner with a foot running left along the bottom — and the rest of the
    cube in silver, the back half plus the block above the foot. `gap` is the clearance
    between the pieces as a fraction of the cube's width, the cube spanning -1..1; `green`
    and `silver` are the two pieces' materials and `chamfer` the bevel on every edge of
    each piece, in the same units — each piece is one `solid`, so the join of its two boxes
    stays flat. Returns the meshes in the order column, foot, back half, block."""
    g, c = gap, chamfer
    return solid([((g, -1, g), (1, 1, 1)),           # L: the column
                  ((-1, -1, g), (g, -g, 1))],        # L: the foot, touching the column
                 green, c) + \
        solid([((-1, -1, -1), (1, 1, -g)),           # rest: the back half
               ((-1, g, -g), (-g, 1, 1))],           # rest: the block above the foot, joined to the back half
              silver, c)
