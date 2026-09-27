# Tests of the UV layout model in plain Python (no GIMP):
#
#   python3 tests/unit.py [CASES]
#
# SVG edge cases and a Blockbench project (tests/fixtures.py), OBJ and
# flipped V, and, with CASES (the folder tests/blender_make.py wrote),
# the layouts Blender made: islands and boundary edges as counted on the
# mesh, the outline's segments against GIMP Link for Blender's boundary
# loops, the islands mask against GIMP Link's mask, and the speed on
# big meshes. Prints UVT PASS or UVT FAIL per check.
#
# Copyright 2026 David
# SPDX-License-Identifier: GPL-3.0-or-later

import json
import math
import os
import sys
import time

here = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(here), "uv-tools"))
sys.path.insert(0, here)
import uv_model as um  # noqa: E402
import uv_dilate_ref as ref  # noqa: E402
import fixtures  # noqa: E402
import pngio  # noqa: E402

fails = 0


def check(name, ok, detail=""):
    global fails
    print("UVT %s unit %s%s" % ("PASS" if ok else "FAIL", name,
                                "" if ok or detail == "" else ": %s" % (detail,)), flush=True)
    if not ok:
        fails += 1


def seg_dist(px, py, a, b):
    (ax, ay), (bx, by) = a, b
    dx, dy = bx - ax, by - ay
    ll = dx * dx + dy * dy
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / ll)) if ll else 0.0
    return math.hypot(px - ax - t * dx, py - ay - t * dy)


def segments(loops, w, h, q=1000):
    """Undirected segments of loops, in pixels rounded to 1/q."""
    out = []
    for lp in loops:
        for a, b in zip(lp, lp[1:] + lp[:1]):
            ka = (round(a[0] * w * q), round(a[1] * h * q))
            kb = (round(b[0] * w * q), round(b[1] * h * q))
            out.append((min(ka, kb), max(ka, kb)))
    return sorted(out)


# ------------------------------------------------------------ SVG cases
for name, svg, n_isl, n_b in fixtures.SVGS:
    isl = um.find_islands(um.parse_svg(svg))
    check("svg %s: %d islands, %d boundary edges" % (name, n_isl, n_b),
          (len(isl.islands), isl.boundary_edges) == (n_isl, n_b),
          (len(isl.islands), isl.boundary_edges))

for bad, why in [("<svg xmlns='http://www.w3.org/2000/svg' width='4' height='4'/>", "no shapes"),
                 ("<html/>", "not svg"), ("<svg", "broken")]:
    try:
        um.parse_svg(bad)
        check("svg refused (%s)" % why, False)
    except um.LayoutError as e:
        check("svg refused (%s): %s" % (why, e), True)

# rasterising: a square on pixel edges covers exactly its pixels
sq = um.find_islands(um.Layout([[(0.25, 0.25), (0.75, 0.25), (0.75, 0.5), (0.25, 0.5)]]))
m = um.mask(sq, 8, 8)
want = bytearray(64)
for y in range(2, 4):
    for x in range(2, 6):
        want[y * 8 + x] = 255
check("mask of a pixel-aligned square", bytes(m) == bytes(want))
# a pixel centre on a face edge is inside (like GIMP Link's mask)
tri = um.find_islands(um.Layout([[(0.0, 0.0), (1.0, 0.0), (0.0, 1.0)]]))
m = um.mask(tri, 4, 4)
check("pixel centres on an edge are inside", all(m[y * 4 + x] == 255 for y in range(4)
                                                  for x in range(4) if x + y <= 3)
      and all(m[y * 4 + x] == 0 for y in range(4) for x in range(4) if x + y > 3))

# ------------------------------------------------------------ Blockbench
text, exp = fixtures.bbmodel()
lay = um.parse_bbmodel(text)
isl = um.find_islands(lay)
check("bbmodel: %d islands (box UV nets, per-face cube, mesh; disabled face left out)"
      % exp["islands"], len(isl.islands) == exp["islands"], len(isl.islands))
for k, (per, cells) in enumerate(zip(exp["perimeters"], exp["cells"])):
    loops = isl.islands[k].loops
    length = sum(math.hypot((b[0] - a[0]) * 64, (b[1] - a[1]) * 64)
                 for lp in loops for a, b in zip(lp, lp[1:] + lp[:1]))
    area = sum(abs(um._area2([(x * 64, y * 64) for x, y in lp])) / 2 for lp in loops)
    check("bbmodel box UV net %d: one outline of length %d around %d cells, no inner edges"
          % (k, per, cells), abs(length - per) < 1e-6 and abs(area - cells) < 1e-6
          and len(loops) == 1, (length, area, len(loops)))
check("bbmodel texture filter", len(um.find_islands(um.parse_bbmodel(text, "other")).islands) == 1)
check("bbmodel texture filter by index",
      len(um.find_islands(um.parse_bbmodel(text, "1")).islands) == 1)

# ------------------------------------------------------------ OBJ, flip
obj = ("vt 0 0\nvt 0.5 0\nvt 0.5 0.25\nvt 0 0.25\nvt 1.2 0.1\nvt 1.4 0.1\nvt 1.4 0.3\n"
       "v 0 0 0\nv 1 0 0\nv 1 1 0\nv 0 1 0\n"
       "usemtl a\nf 1/1 2/2 3/3 4/4\nusemtl b\nf 1/5 2/6 3/7\n")
lo = um.parse_obj(obj)
check("obj: faces of tile 1001 only", len(lo.faces) == 1)
check("obj: V points up (flipped on reading)", sorted(lo.faces[0])[0] == (0.0, 0.75),
      lo.faces[0])
check("obj: tile 1002", len(um.parse_obj(obj, tile=1002).faces) == 1)
check("obj: material", len(um.parse_obj(obj, material="a").faces) == 1)
fl = um.find_islands(lo.flipped())
check("flip V mirrors the mask", um.mask(fl, 8, 8) ==
      b"".join(bytes(um.mask(um.find_islands(lo), 8, 8)[(7 - y) * 8:(8 - y) * 8]) for y in range(8)))

# ------------------------------------------------------------ dilation reference
check("reference offsets: nearest island pixel",
      ref.offsets([False, True, False, False, False], 5, 1, 2) == [(1, 0), (0, 0), (-1, 0), (-2, 0),
                                                                    (0, 0)])

# ------------------------------------------------------------ Blender's layouts
cases = sys.argv[1] if len(sys.argv) > 1 else None
if cases and os.path.isdir(cases):
    for case in sorted(os.listdir(cases)):
        folder = os.path.join(cases, case)
        e = json.load(open(os.path.join(folder, "expected.json")))
        w, h = e["width"], e["height"]
        gbl = um.find_islands(um.parse_svg(open(os.path.join(folder, "gbl-islands.svg")).read()))
        want_segs = segments([lp for i in gbl.islands for lp in i.loops], w, h)
        refmask = None
        if os.path.exists(os.path.join(folder, "mask.png")):
            refmask = pngio.read_grey8(os.path.join(folder, "mask.png"))[2]
        for fn in ["layout.svg", "layout.obj", "gbl-uv.svg", "gbl-islands.svg"]:
            t0 = time.time()
            lay = um.read_layout(os.path.join(folder, fn))
            isl = um.find_islands(lay)
            dt = time.time() - t0
            tag = "%s %s (%d faces)" % (case, fn, e["faces"])
            check("%s: %d islands, %d boundary edges as on the mesh" % (tag, e["islands"],
                                                                       e["boundary_edges"]),
                  (len(isl.islands), isl.boundary_edges) == (e["islands"], e["boundary_edges"]),
                  (len(isl.islands), isl.boundary_edges))
            if fn == "layout.svg":
                got = segments([lp for i in isl.islands for lp in i.loops], w, h)
                check("%s: the outline is exactly the mesh's boundary edges (%d segments)"
                      % (tag, len(want_segs)), got == want_segs,
                      "%d vs %d segments" % (len(got), len(want_segs)))
            if case in ("big", "huge"):
                print("UVT TIME %s: read and islands %.2f s" % (tag, dt))
            if refmask is not None:
                t0 = time.time()
                m = um.mask(isl, w, h)
                dt = time.time() - t0
                bad = [i for i in range(w * h) if (m[i] > 0) != (refmask[i] > 0)]
                # the files hold the UVs rounded (SVG: 3 decimals of a
                # pixel, OBJ: 6 decimals of 0..1): only pixel centres that
                # close to an edge may come out the other way
                tol = 0.0006 if fn != "layout.obj" else 0.6e-6 * max(w, h) + 1e-4
                far = []
                for i in bad:
                    px, py = i % w + 0.5, i // w + 0.5
                    d = min(seg_dist(px, py, (a[0] * w, a[1] * h), (b[0] * w, b[1] * h))
                            for f in isl.faces for a, b in zip(f, f[1:] + f[:1])
                            if min(a[0], b[0]) * w - 1 <= px <= max(a[0], b[0]) * w + 1
                            and min(a[1], b[1]) * h - 1 <= py <= max(a[1], b[1]) * h + 1)
                    if d > tol:
                        far.append((i % w, i // w, d))
                check("%s: mask = GIMP Link's mask, %d of %d pixels differ, all within %g px "
                      "of an edge (the file's rounding)" % (tag, len(bad), w * h, tol),
                      not far, far[:5])
                if case in ("big", "huge"):
                    print("UVT TIME %s: mask %dx%d %.2f s" % (tag, w, h, dt))
            if case == "huge" and fn == "layout.svg":
                t0 = time.time()
                m = um.mask(isl, 4096, 4096)
                print("UVT TIME %s: mask 4096x4096 %.2f s" % (tag, time.time() - t0))
else:
    print("UVT SKIP unit: Blender's layouts (run tests/run.sh)")

print("UVT unit failures: %d" % fails)
sys.exit(1 if fails else 0)
