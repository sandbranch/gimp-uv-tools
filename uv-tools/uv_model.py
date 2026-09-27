# UV Tools for GIMP 3: the UV layout model in plain Python (no GIMP, no
# numpy), so that it can be tested by itself.
#
# - read a UV layout: SVG (Blender's File > Export UV Layout, the SVG
#   files of GIMP Link for Blender, most other SVGs of polygons, paths
#   and rectangles), Wavefront OBJ (texture coordinates of the faces;
#   Blender, Blockbench, Maya, Substance and most 3D programs write it)
#   and Blockbench projects (.bbmodel);
# - find the UV islands: faces joined across shared edges;
# - the islands' outer boundaries (and holes), without internal edges;
# - rasterise faces or islands at pixel centres;
# - write paths as SVG for GIMP's path import.
#
# Coordinates: a Layout holds faces in layout units, 0..1 across the
# texture, x to the right and y DOWN (image orientation), so that V is
# already flipped for SVG and Blockbench (which are y down) and flipped
# on reading for OBJ (V up).
#
# Copyright 2026 David
# SPDX-License-Identifier: GPL-3.0-or-later

import json
import math
import re
import xml.etree.ElementTree as ET
from array import array

VERSION = "0.1.0"
QUANT = 1 << 20          # vertices closer than 1/QUANT of the texture are one
EPS_PX = 1e-7            # pixel centres this close to an edge are inside


class LayoutError(Exception):
    pass


class Layout:
    """faces: list of polygons, each a list of (x, y) in 0..1, y down.
    islands: None, or a list of explicit islands, each a list of loops
    (from an SVG that names its islands, such as GIMP Link's
    islands-<tile>.svg): then these are the islands, not the faces.
    source: a short description of what was read."""

    def __init__(self, faces, islands=None, source="", aspect=None):
        self.faces = faces
        self.islands = islands
        self.source = source
        self.aspect = aspect      # width / height of the source, if it has one

    def flipped(self):
        """The same layout mirrored top to bottom (V the other way)."""
        f = [[(x, 1.0 - y) for (x, y) in p] for p in self.faces]
        isl = None
        if self.islands is not None:
            isl = [[[(x, 1.0 - y) for (x, y) in lp] for lp in loops] for loops in self.islands]
        return Layout(f, isl, self.source, self.aspect)


# ------------------------------------------------------------------ SVG

_NUM = re.compile(r"[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?")
_CMD = re.compile(r"[MmLlHhVvZzCcSsQqTtAa]|[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?")
_ISLAND_ID = re.compile(r"^(?:uv[-_ ]?)?island[-_ ]?(\d+)$", re.I)


def _local(tag):
    return tag.rsplit("}", 1)[-1] if isinstance(tag, str) else ""


def _nums(s):
    return [float(v) for v in _NUM.findall(s or "")]


def _length(s):
    """A length attribute as a number (units ignored), or None."""
    v = _nums(s)
    return v[0] if v else None


def _mul(a, b):
    """Affine matrices (a, b, c, d, e, f) as in SVG: a applied after b."""
    a0, a1, a2, a3, a4, a5 = a
    b0, b1, b2, b3, b4, b5 = b
    return (a0 * b0 + a2 * b1, a1 * b0 + a3 * b1,
            a0 * b2 + a2 * b3, a1 * b2 + a3 * b3,
            a0 * b4 + a2 * b5 + a4, a1 * b4 + a3 * b5 + a5)


IDENTITY = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)


def parse_transform(s):
    m = IDENTITY
    for name, args in re.findall(r"(\w+)\s*\(([^)]*)\)", s or ""):
        v = _nums(args)
        name = name.lower()
        if name == "matrix" and len(v) == 6:
            t = tuple(v)
        elif name == "translate" and v:
            t = (1, 0, 0, 1, v[0], v[1] if len(v) > 1 else 0.0)
        elif name == "scale" and v:
            t = (v[0], 0, 0, v[1] if len(v) > 1 else v[0], 0, 0)
        elif name == "rotate" and v:
            r = math.radians(v[0])
            c, s_ = math.cos(r), math.sin(r)
            t = (c, s_, -s_, c, 0, 0)
            if len(v) == 3:
                t = _mul(_mul((1, 0, 0, 1, v[1], v[2]), t), (1, 0, 0, 1, -v[1], -v[2]))
        elif name == "skewx" and v:
            t = (1, 0, math.tan(math.radians(v[0])), 1, 0, 0)
        elif name == "skewy" and v:
            t = (1, math.tan(math.radians(v[0])), 0, 1, 0, 0)
        else:
            continue
        m = _mul(m, t)
    return m


def _apply(m, p):
    return (m[0] * p[0] + m[2] * p[1] + m[4], m[1] * p[0] + m[3] * p[1] + m[5])


def _bezier(p0, pts, steps=8):
    """Points along a cubic (three control points) or quadratic (two)
    Bezier from p0, without p0."""
    out = []
    for i in range(1, steps + 1):
        t = i / steps
        u = 1 - t
        if len(pts) == 3:
            c1, c2, p3 = pts
            x = u ** 3 * p0[0] + 3 * u * u * t * c1[0] + 3 * u * t * t * c2[0] + t ** 3 * p3[0]
            y = u ** 3 * p0[1] + 3 * u * u * t * c1[1] + 3 * u * t * t * c2[1] + t ** 3 * p3[1]
        else:
            c1, p2 = pts
            x = u * u * p0[0] + 2 * u * t * c1[0] + t * t * p2[0]
            y = u * u * p0[1] + 2 * u * t * c1[1] + t * t * p2[1]
        out.append((x, y))
    return out


def parse_path_d(d):
    """Subpaths of an SVG path: list of (points, closed). Curves are
    flattened (UV edges are straight); arcs become a straight line to
    their end point."""
    toks = _CMD.findall(d or "")
    subs = []
    cur = None
    pos = (0.0, 0.0)
    start = pos
    cmd = None
    last_ctrl = None
    i = 0

    def num():
        nonlocal i
        v = float(toks[i])
        i += 1
        return v

    def is_num(k):
        return k < len(toks) and not toks[k].isalpha()

    while i < len(toks):
        if toks[i].isalpha():
            cmd = toks[i]
            i += 1
        elif cmd is None:
            break
        rel = cmd.islower()
        c = cmd.upper()
        base = pos if rel else (0.0, 0.0)
        if c == "Z":
            if cur is not None:
                subs.append((cur, True))
                cur = None
            pos = start
            last_ctrl = None
            continue
        if not is_num(i):
            if c != "Z":
                cmd = None
            continue
        if c == "M":
            if cur is not None:
                subs.append((cur, False))
            x, y = num(), num()
            pos = (base[0] + x, base[1] + y)
            start = pos
            cur = [pos]
            cmd = "l" if rel else "L"
            last_ctrl = None
            continue
        if cur is None:
            cur = [pos]
            start = pos
        if c == "L":
            x, y = num(), num()
            pos = (base[0] + x, base[1] + y)
            cur.append(pos)
            last_ctrl = None
        elif c == "H":
            x = num()
            pos = ((pos[0] + x) if rel else x, pos[1])
            cur.append(pos)
            last_ctrl = None
        elif c == "V":
            y = num()
            pos = (pos[0], (pos[1] + y) if rel else y)
            cur.append(pos)
            last_ctrl = None
        elif c == "C":
            v = [num() for _ in range(6)]
            pts = [(base[0] + v[0], base[1] + v[1]), (base[0] + v[2], base[1] + v[3]),
                   (base[0] + v[4], base[1] + v[5])]
            cur += _bezier(pos, pts)
            last_ctrl = pts[1]
            pos = pts[2]
        elif c == "S":
            v = [num() for _ in range(4)]
            c1 = (2 * pos[0] - last_ctrl[0], 2 * pos[1] - last_ctrl[1]) if last_ctrl else pos
            pts = [c1, (base[0] + v[0], base[1] + v[1]), (base[0] + v[2], base[1] + v[3])]
            cur += _bezier(pos, pts)
            last_ctrl = pts[1]
            pos = pts[2]
        elif c == "Q":
            v = [num() for _ in range(4)]
            pts = [(base[0] + v[0], base[1] + v[1]), (base[0] + v[2], base[1] + v[3])]
            cur += _bezier(pos, pts)
            last_ctrl = pts[0]
            pos = pts[1]
        elif c == "T":
            v = [num() for _ in range(2)]
            c1 = (2 * pos[0] - last_ctrl[0], 2 * pos[1] - last_ctrl[1]) if last_ctrl else pos
            pts = [c1, (base[0] + v[0], base[1] + v[1])]
            cur += _bezier(pos, pts)
            last_ctrl = c1
            pos = pts[1]
        elif c == "A":
            v = [num() for _ in range(7)]
            pos = (base[0] + v[5], base[1] + v[6])
            cur.append(pos)
            last_ctrl = None
        else:
            i += 1
    if cur is not None:
        subs.append((cur, False))
    return subs


def _closed(points, closed):
    """A face from a subpath: closed ones, and open ones that end where
    they start. None for anything else."""
    pts = list(points)
    if len(pts) > 1 and _key(pts[0]) == _key(pts[-1]):
        pts = pts[:-1]
        closed = True
    if not closed or len(pts) < 3:
        return None
    return pts


def parse_svg(text):
    """A Layout from an SVG. Each closed shape (polygon, rect, closed
    subpath of a path) is a face; a path whose id is island-N (GIMP Link
    for Blender) is island N with its subpaths as its outline and
    holes. The viewBox (else width and height) is the texture."""
    try:
        root = ET.fromstring(text)
    except ET.ParseError as e:
        raise LayoutError("not an SVG file: %s" % e)
    if _local(root.tag) != "svg":
        raise LayoutError("not an SVG file (the root element is <%s>)" % _local(root.tag))
    vb = _nums(root.get("viewBox"))
    if len(vb) == 4 and vb[2] > 0 and vb[3] > 0:
        x0, y0, w, h = vb
    else:
        w, h = _length(root.get("width")), _length(root.get("height"))
        x0 = y0 = 0.0
        if not w or not h:
            raise LayoutError("the SVG has neither a viewBox nor a width and height")
    norm = (1.0 / w, 0.0, 0.0, 1.0 / h, -x0 / w, -y0 / h)
    faces = []
    islands = {}
    seen_paths = set()

    def add(pts, m, isl):
        pts = [_apply(m, p) for p in pts]
        if isl is None:
            faces.append(pts)
        else:
            islands.setdefault(isl, []).append(pts)

    def walk(el, m):
        tag = _local(el.tag)
        if tag in ("defs", "desc", "title", "metadata", "clipPath", "mask", "pattern",
                   "symbol", "style", "text"):
            return
        m = _mul(m, parse_transform(el.get("transform")))
        isl = None
        ident = el.get("id") or ""
        mt = _ISLAND_ID.match(ident)
        if mt and tag == "path":
            isl = int(mt.group(1))
        if tag in ("polygon", "polyline"):
            v = _nums(el.get("points"))
            pts = list(zip(v[0::2], v[1::2]))
            pts = _closed(pts, tag == "polygon")
            if pts:
                add(pts, m, isl)
        elif tag == "rect":
            x, y = _length(el.get("x")) or 0.0, _length(el.get("y")) or 0.0
            rw, rh = _length(el.get("width")), _length(el.get("height"))
            if rw and rh and rw > 0 and rh > 0:
                add([(x, y), (x + rw, y), (x + rw, y + rh), (x, y + rh)], m, isl)
        elif tag == "path":
            subs = [p for p in (_closed(pts, closed) for pts, closed in parse_path_d(el.get("d")))
                    if p]
            # one path drawing many faces, repeated for styling (a light
            # halo under a dark line, as GIMP Link's uv SVG): read once
            sig = (el.get("d"), m)
            if len(subs) > 1 and isl is None and sig in seen_paths:
                subs = []
            seen_paths.add(sig)
            for pts in subs:
                add(pts, m, isl)
        for ch in el:
            walk(ch, m)

    for ch in root:
        walk(ch, norm)
    isl = [islands[k] for k in sorted(islands)] if islands else None
    if not faces and not isl:
        raise LayoutError("the SVG has no closed shapes (polygons, rectangles or closed paths)")
    return Layout(faces, isl, "SVG", w / h)


# ------------------------------------------------------------------ OBJ

def parse_obj(text, material="", tile=1001):
    """A Layout from the texture coordinates of an OBJ's faces. Only
    faces in the UDIM tile (by their UV centre; 1001 is 0..1) and, if
    material is given, those after "usemtl <material>". V points up in
    OBJ, so it is flipped here."""
    vts = []
    faces = []
    mat = ""
    tu, tv = (tile - 1001) % 10, (tile - 1001) // 10
    for line in text.splitlines():
        if line.startswith("vt"):
            p = line.split()
            if p[0] == "vt" and len(p) >= 3:
                vts.append((float(p[1]), float(p[2])))
        elif line.startswith("usemtl"):
            mat = line[6:].strip()
        elif line.startswith("f ") or line.startswith("f\t"):
            if material and mat != material:
                continue
            uv = []
            for ref in line.split()[1:]:
                parts = ref.split("/")
                if len(parts) < 2 or not parts[1]:
                    uv = None
                    break
                k = int(parts[1])
                uv.append(vts[k - 1] if k > 0 else vts[k])
            if not uv or len(uv) < 3:
                continue
            cu = sum(u for u, v in uv) / len(uv)
            cv = sum(v for u, v in uv) / len(uv)
            if int(math.floor(cu)) != tu or int(math.floor(cv)) != tv:
                continue
            faces.append([(u - tu, 1.0 - (v - tv)) for (u, v) in uv])
    if not faces:
        raise LayoutError("the OBJ has no faces with texture coordinates%s%s"
                          % (" in tile %d" % tile if tile != 1001 else "",
                             " with material %s" % material if material else ""))
    return Layout(faces, None, "OBJ")


# ------------------------------------------------------------ Blockbench

def parse_bbmodel(text, material=""):
    """A Layout from a Blockbench project: the UV rectangles of cube
    faces and the UV polygons of mesh faces, in the project's UV size
    (Project > UV size, or a texture's own UV size). material: a
    texture name or index to take only its faces; faces without a
    texture (disabled faces) are left out."""
    try:
        model = json.loads(text)
    except ValueError as e:
        raise LayoutError("not a Blockbench project: %s" % e)
    if not isinstance(model, dict) or "elements" not in model:
        raise LayoutError("not a Blockbench project (no elements)")
    res = model.get("resolution") or {}
    rw, rh = float(res.get("width") or 16), float(res.get("height") or 16)
    textures = model.get("textures") or []

    def tex_index(t):
        if isinstance(t, int):
            return t
        for i, tx in enumerate(textures):
            if isinstance(tx, dict) and t in (tx.get("uuid"), tx.get("name"), tx.get("id")):
                return i
        return None

    want = None
    if material != "":
        want = int(material) if str(material).isdigit() else tex_index(material)
        if want is None:
            names = [tx.get("name") for tx in textures if isinstance(tx, dict)]
            raise LayoutError("the project has no texture %r (it has %s)" % (material, names))

    def size_of(ti):
        if ti is not None and 0 <= ti < len(textures) and isinstance(textures[ti], dict):
            t = textures[ti]
            if t.get("uv_width") and t.get("uv_height"):
                return float(t["uv_width"]), float(t["uv_height"])
        return rw, rh

    faces = []
    for el in model["elements"]:
        if not isinstance(el, dict):
            continue
        kind = el.get("type", "cube")
        for fk, f in (el.get("faces") or {}).items():
            if not isinstance(f, dict) or f.get("texture", 0) is None:
                continue
            ti = tex_index(f.get("texture"))
            if want is not None and ti != want:
                continue
            sw, sh = size_of(ti)
            if kind == "mesh":
                uvs = f.get("uv") or {}
                pts = [uvs[v] for v in f.get("vertices") or [] if v in uvs]
                if len(pts) < 3:
                    continue
                faces.append([(u / sw, v / sh) for u, v in pts])
            else:
                uv = f.get("uv")
                if not uv or len(uv) != 4:
                    continue
                x1, x2 = sorted((uv[0], uv[2]))
                y1, y2 = sorted((uv[1], uv[3]))
                if x2 <= x1 or y2 <= y1:
                    continue
                faces.append([(x1 / sw, y1 / sh), (x2 / sw, y1 / sh), (x2 / sw, y2 / sh),
                              (x1 / sw, y2 / sh)])
    if not faces:
        raise LayoutError("the Blockbench project has no textured faces")
    return Layout(faces, None, "Blockbench", rw / rh)


def read_layout(path, material="", tile=1001):
    """A Layout from a file, by its content (SVG, OBJ or bbmodel)."""
    with open(path, "rb") as f:
        data = f.read()
    text = data.decode("utf-8", "replace")
    head = text.lstrip()[:512].lower()
    low = path.lower()
    if low.endswith(".bbmodel") or (head.startswith("{") and '"elements"' in text[:100000]):
        return parse_bbmodel(text, material)
    if low.endswith(".obj") or re.search(r"^vt\s", text, re.M) and not head.startswith("<"):
        return parse_obj(text, material, tile)
    if "<svg" in text[:4096].lower():
        return parse_svg(text)
    raise LayoutError("%s is not an SVG, OBJ or Blockbench file" % path)


# --------------------------------------------------------------- islands

def _key(p):
    return (round(p[0] * QUANT), round(p[1] * QUANT))


def _area2(pts):
    a = 0.0
    n = len(pts)
    for i in range(n):
        x0, y0 = pts[i]
        x1, y1 = pts[(i + 1) % n]
        a += x0 * y1 - x1 * y0
    return a


class _UF:
    def __init__(self, n):
        self.p = list(range(n))

    def find(self, i):
        p = self.p
        while p[i] != i:
            p[i] = p[p[i]]
            i = p[i]
        return i

    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.p[ra] = rb


class Island:
    """faces: indices into Islands.faces; loops: the boundary, closed
    loops of points (outer boundaries and holes, without internal
    edges); bbox: (x0, y0, x1, y1) in layout units."""

    def __init__(self, faces, loops):
        self.faces = faces
        self.loops = loops
        xs = [p[0] for lp in loops for p in lp]
        ys = [p[1] for lp in loops for p in lp]
        self.bbox = (min(xs), min(ys), max(xs), max(ys)) if xs else (0, 0, 0, 0)


class Islands:
    """The islands of a layout. faces: the cleaned faces (explicit
    islands: their loops); islands: list of Island; boundary_edges: how
    many face edges are on an island boundary (each seam counts once per
    side); edges: every distinct face edge, as ((x, y), (x, y))."""

    def __init__(self, faces, islands, boundary_edges, edges, explicit=False):
        self.faces = faces
        self.islands = islands
        self.boundary_edges = boundary_edges
        self.edges = edges
        self.explicit = explicit


def _clean(pts):
    """Drops repeated points (and a repeated first point at the end)."""
    out = []
    for p in pts:
        if not out or _key(out[-1]) != _key(p):
            out.append(p)
    while len(out) > 1 and _key(out[0]) == _key(out[-1]):
        out.pop()
    return out


def find_islands(layout, split_t=True):
    """Islands of a layout. Faces are joined across an edge when both
    run along it in opposite directions (the way neighbouring faces of
    one UV island do) and wind the same way round, so that two islands
    only touching at a corner, or a mirrored copy stacked on an island,
    stay separate. Where several faces share an edge (identical copies
    stacked on each other), each is paired with the partner nearest to
    it in the file, which keeps the copies apart when their faces come
    one copy after the other (as Blender writes them). With split_t, an
    edge that has no partner is split where other boundary vertices lie
    on it (T-junctions, as in Blockbench's box UV nets) and tried again."""
    if layout.islands is not None:
        return _explicit(layout)
    faces = []
    for f in layout.faces:
        f = _clean(f)
        if len(f) >= 3 and abs(_area2(f)) > 1e-18:
            faces.append(f)
    if not faces:
        raise LayoutError("the layout has no faces with an area")
    signs = [1 if _area2(f) > 0 else -1 for f in faces]
    uf = _UF(len(faces))
    # directed edges: (key a, key b, sign) -> list of (face, tag)
    directed = {}
    points = {}
    for i, f in enumerate(faces):
        n = len(f)
        for j in range(n):
            a, b = f[j], f[(j + 1) % n]
            ka, kb = _key(a), _key(b)
            points[ka] = a
            points[kb] = b
            directed.setdefault((ka, kb, signs[i]), []).append((i, None))
    boundary = _pair(directed, uf)
    nb = len(boundary)
    if split_t and boundary:
        boundary, nb = _split_t(boundary, signs, uf)
    # edges of the whole layout, each once
    seen = set()
    edges = []
    for (ka, kb, _s) in directed:
        u = (ka, kb) if ka <= kb else (kb, ka)
        if u not in seen:
            seen.add(u)
            edges.append((points[u[0]], points[u[1]]))
    # islands, ordered by their first face
    groups = {}
    for i in range(len(faces)):
        groups.setdefault(uf.find(i), []).append(i)
    order = sorted(groups.values(), key=lambda g: g[0])
    root_of = {}
    for n, g in enumerate(order):
        root_of[uf.find(g[0])] = n
    bsegs = [[] for _ in order]
    for (ka, kb, fi, tag) in boundary:
        bsegs[root_of[uf.find(fi)]].append((ka, kb))
    islands = []
    for n, g in enumerate(order):
        loops = [[points[k] for k in lp] for lp in chain(bsegs[n])]
        islands.append(Island(g, loops))
    return Islands(faces, islands, nb, edges)


def _pair(directed, uf):
    """Joins faces across edges run both ways (each face with the
    partner nearest in file order); returns the unpaired edges as
    (key a, key b, face, tag)."""
    boundary = []
    for (ka, kb, s), fs in directed.items():
        back = directed.get((kb, ka, s), ())
        if len(fs) > 1:
            fs = sorted(fs)
        if len(back) > 1:
            back = sorted(back)
        n = min(len(fs), len(back))
        for k in range(n):
            uf.union(fs[k][0], back[k][0])
        for item in fs[n:]:
            boundary.append((ka, kb) + tuple(item))
    return boundary


def _split_t(boundary, signs, uf):
    """Splits unpaired edges at boundary vertices lying on them, joins
    what then pairs up. Returns the unpaired pieces and how many of the
    original edges still have a piece without a partner."""
    verts = set()
    for (ka, kb, fi, tag) in boundary:
        verts.add(ka)
        verts.add(kb)
    cell = QUANT // 256
    grid = {}
    for k in verts:
        grid.setdefault((k[0] // cell, k[1] // cell), []).append(k)
    vlist = list(verts)
    sub = {}
    for n, (ka, kb, fi, tag) in enumerate(boundary):
        (ax, ay), (bx, by) = ka, kb
        dx, dy = bx - ax, by - ay
        ll = dx * dx + dy * dy
        gx0, gx1 = min(ax, bx) // cell, max(ax, bx) // cell
        gy0, gy1 = min(ay, by) // cell, max(ay, by) // cell
        if (gx1 - gx0 + 1) * (gy1 - gy0 + 1) > len(grid):
            cands = vlist
        else:
            cands = [k for gx in range(gx0, gx1 + 1) for gy in range(gy0, gy1 + 1)
                     for k in grid.get((gx, gy), ())]
        on = []
        for k in cands:
            if k == ka or k == kb:
                continue
            px, py = k[0] - ax, k[1] - ay
            t = (px * dx + py * dy) / ll
            if 0 < t < 1 and abs(px * dy - py * dx) <= 2 * math.sqrt(ll):
                on.append((t, k))
        on.sort()
        pts = [ka] + [k for t, k in on] + [kb]
        for a, b in zip(pts, pts[1:]):
            sub.setdefault((a, b, signs[fi]), []).append((fi, n))
    left = _pair(sub, uf)
    # an edge none of whose pieces found a partner stays whole
    pieces = {}
    for item in left:
        pieces.setdefault(item[3], []).append(item)
    total = {}
    for fs in sub.values():
        for (fi, n) in fs:
            total[n] = total.get(n, 0) + 1
    out = []
    for n, items in pieces.items():
        if len(items) == total[n]:
            ka, kb, fi, tag = boundary[n]
            out.append((ka, kb, fi, n))
        else:
            out.extend(items)
    return out, len(pieces)


def _explicit(layout):
    faces = []
    islands = []
    edges = []
    nb = 0
    for loops in layout.islands:
        loops = [_clean(lp) for lp in loops]
        loops = [lp for lp in loops if len(lp) >= 3]
        if not loops:
            continue
        idx = list(range(len(faces), len(faces) + len(loops)))
        faces.extend(loops)
        islands.append(Island(idx, loops))
        for lp in loops:
            nb += len(lp)
            edges.extend(zip(lp, lp[1:] + lp[:1]))
    if not islands:
        raise LayoutError("the islands of the SVG have no closed outlines")
    return Islands(faces, islands, nb, edges, explicit=True)


def chain(segments):
    """Joins directed edges (key a, key b) into closed loops of keys.
    The boundary of a union of faces has as many edges leaving each
    vertex as arriving, so every edge ends up in a loop."""
    out_of = {}
    for i, (a, b) in enumerate(segments):
        out_of.setdefault(a, []).append(i)
    used = [False] * len(segments)
    loops = []
    for i in range(len(segments)):
        if used[i]:
            continue
        used[i] = True
        a, b = segments[i]
        loop = [a]
        cur = b
        while cur != a:
            nxt = None
            for j in out_of.get(cur, ()):
                if not used[j]:
                    nxt = j
                    break
            if nxt is None:
                break
            used[nxt] = True
            loop.append(cur)
            cur = segments[nxt][1]
        if len(loop) >= 2:
            loops.append(loop)
    return loops


def chain_edges(edges):
    """Undirected edges into polylines (fewer, longer strokes)."""
    adj = {}
    for i, (a, b) in enumerate(edges):
        adj.setdefault(_key(a), []).append(i)
        adj.setdefault(_key(b), []).append(i)
    used = [False] * len(edges)
    lines = []
    for i in range(len(edges)):
        if used[i]:
            continue
        used[i] = True
        a, b = edges[i]
        line = [a, b]
        cur = b
        while True:
            nxt = None
            for j in adj[_key(cur)]:
                if not used[j]:
                    nxt = j
                    break
            if nxt is None:
                break
            used[nxt] = True
            p, q = edges[nxt]
            cur = q if _key(p) == _key(cur) else p
            line.append(cur)
        lines.append(line)
    return lines


# ---------------------------------------------------------- rasterising

def _spans(poly, w, h, sx, sy):
    """For a polygon in layout units: (row, x0, x1) spans of the pixels
    whose centres are inside it or on its edges (even-odd), x1 not
    included. sx, sy: pixels per layout unit."""
    pts = [(x * sx, y * sy) for (x, y) in poly]
    n = len(pts)
    ys = [p[1] for p in pts]
    r0 = max(0, int(math.floor(min(ys) - 0.5 - EPS_PX)) + 1)
    r1 = min(h - 1, int(math.floor(max(ys) - 0.5 + EPS_PX)))
    edges = []
    for i in range(n):
        (x0, y0), (x1, y1) = pts[i], pts[(i + 1) % n]
        edges.append((x0, y0, x1, y1))
    out = []
    for r in range(r0, r1 + 1):
        yc = r + 0.5
        xs = []
        extra = []
        for (x0, y0, x1, y1) in edges:
            if y0 == y1:
                if abs(yc - y0) <= EPS_PX:
                    extra.append((min(x0, x1), max(x0, x1)))
                continue
            lo, hi = (y0, y1) if y0 < y1 else (y1, y0)
            if lo <= yc < hi:
                xs.append(x0 + (yc - y0) * (x1 - x0) / (y1 - y0))
            elif abs(yc - hi) <= EPS_PX:
                # a centre on the edge's end: inside, but not a crossing
                xe = x1 if y1 == hi else x0
                extra.append((xe, xe))
        xs.sort()
        ivs = [(xs[k], xs[k + 1]) for k in range(0, len(xs) - 1, 2)] + extra
        for (a, b) in ivs:
            c0 = max(0, int(math.ceil(a - EPS_PX - 0.5)))
            c1 = min(w, int(math.floor(b + EPS_PX - 0.5)) + 1)
            if c1 > c0:
                out.append((r, c0, c1))
    return out


def _loop_spans(loops, w, h, sx, sy):
    """Spans of the pixels inside a set of loops by the nonzero winding
    rule (an island given only by its outlines: holes wind the other
    way; where an island folds over itself it stays inside)."""
    pts_loops = [[(x * sx, y * sy) for (x, y) in lp] for lp in loops]
    edges = []
    for pts in pts_loops:
        n = len(pts)
        for i in range(n):
            (x0, y0), (x1, y1) = pts[i], pts[(i + 1) % n]
            if y0 != y1:
                edges.append((x0, y0, x1, y1, 1 if y1 > y0 else -1))
    if not edges:
        return []
    ys = [p[1] for pts in pts_loops for p in pts]
    r0 = max(0, int(math.floor(min(ys) - 0.5)) + 1)
    r1 = min(h - 1, int(math.floor(max(ys) - 0.5)))
    out = []
    for r in range(r0, r1 + 1):
        yc = r + 0.5
        xs = sorted((x0 + (yc - y0) * (x1 - x0) / (y1 - y0), d) for (x0, y0, x1, y1, d) in edges
                    if min(y0, y1) <= yc < max(y0, y1))
        wind = 0
        start = None
        for x, d in xs:
            before = wind
            wind += d
            if before == 0 and wind != 0:
                start = x
            elif before != 0 and wind == 0:
                c0 = max(0, int(math.ceil(start - EPS_PX - 0.5)))
                c1 = min(w, int(math.floor(x + EPS_PX - 0.5)) + 1)
                if c1 > c0:
                    out.append((r, c0, c1))
    return out


def island_spans(isl, islands, w, h):
    """Spans of the pixels of one island."""
    if islands.explicit:
        return _loop_spans(isl.loops, w, h, w, h)
    out = []
    for fi in isl.faces:
        out.extend(_spans(islands.faces[fi], w, h, w, h))
    return out


def mask(islands, w, h, which=None, value=255):
    """8-bit mask (bytearray, rows from the top) of the islands (all,
    or the indices in which): value inside, 0 outside."""
    m = bytearray(w * h)
    full = bytes([value]) * w
    for n, isl in enumerate(islands.islands):
        if which is not None and n not in which:
            continue
        for (r, c0, c1) in island_spans(isl, islands, w, h):
            o = r * w
            m[o + c0:o + c1] = full[:c1 - c0]
    return m


def ids(islands, w, h):
    """Island number + 1 per pixel (array of unsigned 32-bit, rows from
    the top), 0 outside every island; where islands overlap, the later."""
    out = array("I", bytes(4 * w * h))
    for n, isl in enumerate(islands.islands):
        v = array("I", [n + 1]) * w
        for (r, c0, c1) in island_spans(isl, islands, w, h):
            o = r * w
            out[o + c0:o + c1] = v[:c1 - c0]
    return out


# ------------------------------------------------------------- SVG out

def _fmt(v):
    s = "%.4f" % v
    s = s.rstrip("0").rstrip(".")
    return s if s not in ("-0", "") else "0"


def path_d(loops, w, h, close=True):
    parts = []
    for lp in loops:
        if len(lp) < 2:
            continue
        parts.append("M" + " L".join("%s %s" % (_fmt(x * w), _fmt(y * h)) for (x, y) in lp)
                     + (" Z" if close else ""))
    return " ".join(parts)


def _xml_attr(s):
    return (s.replace("&", "&amp;").replace('"', "&quot;").replace("<", "&lt;")
            .replace(">", "&gt;"))


def paths_svg(paths, w, h):
    """An SVG of paths [(name, d)] sized w x h pixels, for GIMP's path
    import (each <path> becomes a path named after its id)."""
    body = "".join('<path id="%s" d="%s" fill="none" stroke="#000"/>\n' % (_xml_attr(n), d)
                   for n, d in paths if d)
    return ('<?xml version="1.0" encoding="UTF-8"?>\n'
            '<svg xmlns="http://www.w3.org/2000/svg" width="%d" height="%d" '
            'viewBox="0 0 %d %d">\n%s</svg>\n' % (w, h, w, h, body))


def point_in_island(isl, islands, x, y):
    """Is the point (layout units) in the island (in one of its faces,
    or inside its loops, nonzero rule, for explicit islands)?"""
    x0, y0, x1, y1 = isl.bbox
    if not (x0 <= x <= x1 and y0 <= y <= y1):
        return False
    if islands.explicit:
        return _winding(isl.loops, x, y) != 0
    return any(_winding([islands.faces[i]], x, y) != 0 for i in isl.faces)


def _winding(polys, x, y):
    wn = 0
    for pts in polys:
        n = len(pts)
        for i in range(n):
            (xa, ya), (xb, yb) = pts[i], pts[(i + 1) % n]
            if (ya > y) != (yb > y) and x < xa + (y - ya) * (xb - xa) / (yb - ya):
                wn += 1 if yb > ya else -1
    return wn
