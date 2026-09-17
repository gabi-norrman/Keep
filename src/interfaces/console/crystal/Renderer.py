import math
from itertools import product
from typing import List, NamedTuple, Sequence, Tuple

import numpy as np

from interfaces.console.crystal.Framebuffer import Framebuffer, RGB, focal_length

Vec = Tuple[float, float, float]
Face = Tuple[int, ...]

# Right-handed view space: x right, y up, the camera at the origin looking down -z.
AMBIENT = 0.04                 # what a face no light reaches still shows of its color
AO_RADIUS = 0.25               # how far a neighbouring piece's shadow reaches across a face
AO_STRENGTH = 0.75             # how dark a face gets right against a neighbouring piece


class Light(NamedTuple):
    """A point light in view space; `color` is its strength per channel, 1 = full white."""
    position: Vec
    color: Tuple[float, float, float] = (1.0, 1.0, 1.0)


# The rig: a warm key far up to the right and a little behind the camera, and a cool, weak
# fill from the lower right so the faces the key misses still show their shape.
LIGHTS = (Light((20.0, 20.0, 4.5), (1.0, 0.97, 0.9)),
          Light((4.0, -1.5, 3.0), (0.229, 0.250, 0.291)))

# What a mirror sees, by how far up the reflected ray points (-1 = straight down, 1 = up):
# a flat grey sky over a faintly bluer ground of the same brightness, with a fairly sharp
# horizon between them. Muted so the mirrored faces stay coloured rather than blowing out.
SKY_STOPS = ((-1.0, (0.30, 0.30, 0.36)), (-0.08, (0.30, 0.30, 0.345)),
             (0.08, (0.30, 0.30, 0.315)), (1.0, (0.30, 0.30, 0.30)))


class Material(NamedTuple):
    color: RGB                 # what a face lit head-on shows; the lighting only darkens it
    gloss: float = 0.0         # strength of the specular highlight, 0 = matte
    shininess: float = 1.0     # how tight the highlight is; higher is smaller and sharper
    reflectivity: float = 0.0  # how much of the face is the sky/ground mirrored in it, 1 = chrome


WHITE = Material((255, 255, 255))


class Mesh:
    """Vertices and faces: convex planar polygons as index tuples, wound counter-clockwise
    seen from outside so the cross product of two edges points out of the solid."""

    def __init__(self, vertices: Sequence[Vec], faces: Sequence[Face], material: Material = WHITE):
        self.vertices = list(vertices)
        self.faces = list(faces)
        self.material = material
        self.lo = tuple(min(v[k] for v in self.vertices) for k in range(3))
        self.hi = tuple(max(v[k] for v in self.vertices) for k in range(3))


Box = Tuple[Vec, Vec]
Edge = Tuple[int, int, int]    # the axis an edge runs along, and its side (0 low, 1 high) on each other axis
EPS = 1e-6                     # how far off an edge the probes for its neighbourhood go


def box(lo: Vec, hi: Vec, material: Material = WHITE, chamfer: float = 0.0) -> Mesh:
    """An axis-aligned box from its minimum corner to its maximum corner; `chamfer` as in `solid`."""
    return solid([(lo, hi)], material, chamfer)[0]


def solid(boxes: Sequence[Box], material: Material = WHITE, chamfer: float = 0.0) -> List[Mesh]:
    """A solid built from touching axis-aligned boxes, one mesh per box. A `chamfer` > 0
    bevels the edges: a face shrinks by that much along each bevelled edge, a 45° quad
    bridges the edge and a triangle caps every corner where three bevels meet, so the edges
    catch the light as lines of their own instead of vanishing where two flat faces meet.
    Only the convex edges of the boxes' union are bevelled: a face that runs on across the
    join of two boxes stays flat and a concave corner stays sharp, so the boxes read as one
    piece. The chamfer is capped at half the thinnest side of any box."""
    c = min([chamfer] + [(h - l) / 2 for lo, hi in boxes for l, h in zip(lo, hi)])
    if c <= 0:
        return [_plain_box(lo, hi, material) for lo, hi in boxes]
    planes = [sorted({bound[k] for lo, hi in boxes for bound in (lo, hi)}) for k in range(3)]
    convex = _convexity(boxes)
    meshes = []
    for lo, hi in boxes:
        vertices: List[Vec] = []
        faces: List[Face] = []
        for part in _split((lo, hi), planes, convex):
            part_vertices, part_faces = _chamfered_box(part, c, convex)
            faces += [tuple(i + len(vertices) for i in face) for face in part_faces]
            vertices += part_vertices
        meshes.append(Mesh(vertices, faces, material))
    return meshes


def _plain_box(lo: Vec, hi: Vec, material: Material) -> Mesh:
    (x0, y0, z0), (x1, y1, z1) = lo, hi
    return Mesh(
        vertices=[(x0, y0, z0), (x1, y0, z0), (x1, y1, z0), (x0, y1, z0),
                  (x0, y0, z1), (x1, y0, z1), (x1, y1, z1), (x0, y1, z1)],
        faces=[(4, 5, 6, 7),   # front  z = z1
               (1, 0, 3, 2),   # back   z = z0
               (5, 1, 2, 6),   # right  x = x1
               (0, 4, 7, 3),   # left   x = x0
               (3, 7, 6, 2),   # top    y = y1
               (0, 1, 5, 4)],  # bottom y = y0
        material=material,
    )


def _others(axis: int) -> Tuple[int, int]:
    return tuple(k for k in range(3) if k != axis)


def _convexity(boxes: Sequence[Box]):
    """`convex(point, axis)`: whether the edge running along `axis` through `point` is a convex
    edge of the boxes' union there, i.e. exactly one of the four quadrants around it is solid."""
    def inside(p: Sequence[float]) -> bool:
        return any(all(l <= x <= h for x, l, h in zip(p, lo, hi)) for lo, hi in boxes)

    def convex(point: Sequence[float], axis: int) -> bool:
        i, j = _others(axis)
        solid_quadrants = 0
        for di, dj in product((-EPS, EPS), repeat=2):
            probe = list(point)
            probe[i] += di
            probe[j] += dj
            solid_quadrants += inside(probe)
        return solid_quadrants == 1
    return convex


def _edge_point(box: Box, axis: int, si: int, sj: int, along: float) -> List[float]:
    """The point at coordinate `along` on the edge of `box` running along `axis` at side `si`
    of the lower other axis and `sj` of the higher."""
    i, j = _others(axis)
    point = [0.0, 0.0, 0.0]
    point[axis], point[i], point[j] = along, box[si][i], box[sj][j]
    return point


def _split(box: Box, planes: Sequence[Sequence[float]], convex) -> List[Box]:
    """`box` cut along the other boxes' bounding planes wherever one of its edges is convex
    along only part of its length, so every edge of every part is bevelled whole or not at all."""
    lo, hi = box
    for axis in range(3):
        for plane in planes[axis]:
            if not lo[axis] < plane < hi[axis]:
                continue
            for si, sj in product((0, 1), repeat=2):
                if convex(_edge_point(box, axis, si, sj, plane - EPS), axis) != \
                        convex(_edge_point(box, axis, si, sj, plane + EPS), axis):
                    cut_hi = tuple(plane if k == axis else hi[k] for k in range(3))
                    cut_lo = tuple(plane if k == axis else lo[k] for k in range(3))
                    return _split((lo, cut_hi), planes, convex) + _split((cut_lo, hi), planes, convex)
    return [box]


def _chamfered_box(box: Box, c: float, convex) -> Tuple[List[Vec], List[Face]]:
    """Three vertices per corner, one on each of its faces, each pulled `c` in from the corner
    along the face for every bevelled edge the face has there. A face keeps a corner none of
    whose edges is bevelled, and where the only bevelled edge is the one running into the
    corner the face is cut round that bevel's end, so a face joined to another box never
    pokes out of the bevels that run through the join. The faces are then wound outward by
    `_outward`, which spares listing the windings by hand."""
    lo, hi = box
    corners = list(product((0, 1), repeat=3))      # per axis: at the low or the high side

    def edge(axis: int, signs: Tuple[int, ...]) -> Edge:
        """The edge running along `axis` through corner `signs`."""
        return (axis,) + tuple(s for k, s in enumerate(signs) if k != axis)

    def at(signs: Tuple[int, ...], axis: int) -> int:
        """Index of the vertex at corner `signs` on the face perpendicular to `axis`."""
        return corners.index(signs) * 3 + axis

    def corner(axis: int, side: int, i: int, si: int, j: int, sj: int) -> Tuple[int, ...]:
        signs = [0, 0, 0]
        signs[axis], signs[i], signs[j] = side, si, sj
        return tuple(signs)

    middle = [(l + h) / 2 for l, h in zip(lo, hi)]
    bevelled = {(axis, si, sj) for axis in range(3) for si, sj in product((0, 1), repeat=2)
                if convex(_edge_point(box, axis, si, sj, middle[axis]), axis)}

    vertices: List[Vec] = []
    for signs in corners:
        for axis in range(3):
            v = [(l, h)[s] for l, h, s in zip(lo, hi, signs)]
            for k in _others(axis):                  # the face meets the one across `k` along the third axis
                if edge(3 - axis - k, signs) in bevelled:
                    v[k] += -c if signs[k] else c
            vertices.append(tuple(v))

    faces: List[Face] = []
    for axis in range(3):
        i, j = _others(axis)
        for side in (0, 1):                                          # the shrunken face
            face: List[int] = []
            for (si, sj), arrival in zip(((0, 0), (1, 0), (1, 1), (0, 1)), (j, i, j, i)):
                signs = corner(axis, side, i, si, j, sj)
                if edge(axis, signs) in bevelled and not any(edge(k, signs) in bevelled for k in (i, j)):
                    # Only the edge running into this corner is bevelled, so its bevel ends on
                    # this face and cuts the corner off it: the face goes round the bevel's end.
                    departure = i if arrival == j else j
                    face += [at(signs, departure), at(signs, arrival)]
                else:
                    face.append(at(signs, axis))
            faces.append(tuple(face))
        for si, sj in product((0, 1), repeat=2):                     # the bevel along `axis`
            if (axis, si, sj) in bevelled:
                a, b = corner(axis, 0, i, si, j, sj), corner(axis, 1, i, si, j, sj)
                faces.append((at(a, i), at(a, j), at(b, j), at(b, i)))
    faces += [(at(signs, 0), at(signs, 1), at(signs, 2)) for signs in corners   # the corner caps
              if all(edge(axis, signs) in bevelled for axis in range(3))]
    faces = [tuple(i for n, i in enumerate(face) if vertices[i] != vertices[face[n - 1]]) for face in faces]
    return vertices, [_outward(vertices, face, tuple(middle)) for face in faces
                      if len(face) >= 3 and any(normal(*(vertices[i] for i in face[:3])))]


def _outward(vertices: Sequence[Vec], face: Face, middle: Vec) -> Face:
    """`face` wound so its normal points away from `middle`, the inside of a convex solid."""
    a, b, c = (vertices[i] for i in face[:3])
    centroid = tuple(sum(vertices[i][k] for i in face) / len(face) - middle[k] for k in range(3))
    return face if dot(normal(a, b, c), centroid) > 0 else face[::-1]


CUBE = box((-1, -1, -1), (1, 1, 1))


def rotate(v: Vec, ax: float, ay: float, az: float) -> Vec:
    """Rotate about x, then y, then z."""
    x, y, z = v
    c, s = math.cos(ax), math.sin(ax)
    y, z = y * c - z * s, y * s + z * c
    c, s = math.cos(ay), math.sin(ay)
    x, z = x * c + z * s, -x * s + z * c
    c, s = math.cos(az), math.sin(az)
    x, y = x * c - y * s, x * s + y * c
    return x, y, z


def normal(a: Vec, b: Vec, c: Vec) -> Vec:
    ux, uy, uz = b[0] - a[0], b[1] - a[1], b[2] - a[2]
    vx, vy, vz = c[0] - a[0], c[1] - a[1], c[2] - a[2]
    return uy * vz - uz * vy, uz * vx - ux * vz, ux * vy - uy * vx


def dot(a: Vec, b: Vec) -> float:
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def normalized(v: Vec) -> Vec:
    length = math.sqrt(dot(v, v)) or 1.0
    return v[0] / length, v[1] / length, v[2] / length


def _unit(v: np.ndarray) -> np.ndarray:
    return v / np.maximum(np.linalg.norm(v, axis=1, keepdims=True), 1e-9)


SkyStops = Sequence[Tuple[float, Tuple[float, float, float]]]


def environment(up: np.ndarray, stops: SkyStops = SKY_STOPS) -> np.ndarray:
    """The sky/ground color (n × 3) seen along rays whose y components are `up`."""
    knots = [stop for stop, _ in stops]
    return np.stack([np.interp(up, knots, [color[c] for _, color in stops]) for c in range(3)],
                    axis=1).astype(np.float32)


class Renderer:
    """Back-face culling, a depth buffer and per-pixel Blinn-Phong shading under the light rig:
    a diffuse gradient across every face, a specular highlight per light and, for reflective
    materials, the sky and ground mirrored in the face, all darkened where another mesh
    stands close by. The rig, ambient level, occlusion reach and sky are plain attributes,
    defaulting to the module constants, so they can be tuned while running."""

    def __init__(self, meshes: Sequence[Mesh] = (CUBE,), distance: float = 5.0,
                 lights: Sequence[Light] = LIGHTS, ambient: float = AMBIENT,
                 ao_radius: float = AO_RADIUS, ao_strength: float = AO_STRENGTH, sky: SkyStops = SKY_STOPS):
        self.meshes = list(meshes)
        self.distance = distance
        self.lights = lights
        self.ambient = ambient
        self.ao_radius = ao_radius
        self.ao_strength = ao_strength
        self.sky = sky

    @property
    def lights(self) -> List[Tuple[np.ndarray, np.ndarray]]:
        """The rig as (position, color) float arrays, ready for the shader."""
        return self._lights

    @lights.setter
    def lights(self, lights: Sequence[Light]):
        self._lights = [(np.asarray(light.position, np.float32), np.asarray(light.color, np.float32))
                        for light in lights]

    @staticmethod
    def project(v: Vec, width: int, height: int, zoom: float = 1.0) -> Tuple[float, float]:
        """`v` on a `width` × `height` surface with the principal point in the middle; a
        framebuffer's own `project` honours where its principal point actually is."""
        focal = focal_length(width, height) * zoom
        depth = -v[2]
        return width / 2 + focal * v[0] / depth, height / 2 - focal * v[1] / depth

    def transform(self, mesh: Mesh, ax: float, ay: float, az: float) -> List[Vec]:
        return [(x, y, z - self.distance) for x, y, z in (rotate(v, ax, ay, az) for v in mesh.vertices)]

    def camera(self, ax: float, ay: float, az: float) -> Vec:
        """The camera's position in mesh coordinates: the rotation undone on the view-space
        offset (0, 0, distance), i.e. the third row of the rotation matrix scaled by it."""
        return tuple(self.distance * rotate(axis, ax, ay, az)[2]
                     for axis in ((1, 0, 0), (0, 1, 0), (0, 0, 1)))

    @staticmethod
    def axes(ax: float, ay: float, az: float) -> np.ndarray:
        """The mesh axes in view space, one per row; its transpose undoes the rotation."""
        return np.array([rotate(axis, ax, ay, az) for axis in ((1, 0, 0), (0, 1, 0), (0, 0, 1))], np.float32)

    def occlusion(self, mesh: Mesh, positions: np.ndarray, n: Vec, axes: np.ndarray) -> np.ndarray:
        """How much of the light reaches each view-space point on a face of `mesh` with normal
        `n`: 1 in the open, down to 1 - ao_strength against another mesh. Every other mesh's
        bounding box that rises above the face's plane casts a shadow that fades with the
        distance to the box, so coplanar neighbours cast none and only the gaps and the
        concave corners darken — a cheap stand-in for ambient occlusion."""
        local = (positions + np.array([0, 0, self.distance], np.float32)) @ axes.T
        normal = axes @ np.asarray(n, np.float32)
        plane = float(local[0] @ normal)
        shadow = np.zeros(len(local), np.float32)
        for other in self.meshes:
            if other is mesh:
                continue
            rise = sum(max(lo * c, hi * c) for lo, hi, c in zip(other.lo, other.hi, normal)) - plane
            if rise <= 1e-6:
                continue
            outside = np.maximum(np.maximum(np.subtract(other.lo, local), local - other.hi), 0)
            distance = np.linalg.norm(outside, axis=1)
            shadow += min(rise / self.ao_radius, 1.0) * np.exp(-distance / self.ao_radius)
        return 1 - self.ao_strength * np.minimum(shadow, 1)

    def shade(self, material: Material, positions: np.ndarray, n: Vec) -> np.ndarray:
        """Blinn-Phong at view-space `positions` (n × 3) on a face with unit normal `n`:
        ambient plus each light's diffuse term, the mirrored environment mixed in by the
        material's reflectivity, and on top each light's highlight where the half vector
        between light and eye lines up with the normal."""
        n = np.asarray(n, np.float32)
        base = np.asarray(material.color, np.float32) / 255
        to_eye = _unit(-positions)
        rgb = np.full_like(positions, self.ambient) * base
        highlight = np.zeros_like(positions)
        for position, color in self.lights:
            to_light = _unit(position - positions)
            rgb += base * color * np.clip(to_light @ n, 0, 1)[:, None]
            if material.gloss:
                half = _unit(to_light + to_eye)
                highlight += color * (np.clip(half @ n, 0, 1) ** material.shininess * material.gloss)[:, None]
        if material.reflectivity:
            mirrored = 2 * (to_eye @ n)[:, None] * n - to_eye
            rgb = (rgb * (1 - material.reflectivity)
                   + environment(mirrored[:, 1], self.sky) * base * material.reflectivity)
        return rgb + highlight

    def render(self, fb: Framebuffer, ax: float, ay: float, az: float) -> int:
        """Clear, then rotate, cull, rasterize and shade every face; returns the number of
        faces drawn (front-facing and not entirely hidden)."""
        fb.clear()
        drawn = 0
        axes = self.axes(ax, ay, az)
        for mesh in self.meshes:
            view = self.transform(mesh, ax, ay, az)
            screen = [fb.project(v) for v in view]
            for face in mesh.faces:
                n = normal(view[face[0]], view[face[1]], view[face[2]])
                if dot(n, view[face[0]]) >= 0:   # faces away from the camera at the origin
                    continue
                n = normalized(n)
                fragment = fb.rasterize([screen[i] for i in face], n, view[face[0]])
                if fragment is None:
                    continue
                rgb = self.shade(mesh.material, fragment.positions, n)
                fb.paint(fragment, rgb * self.occlusion(mesh, fragment.positions, n, axes)[:, None])
                drawn += 1
        return drawn
