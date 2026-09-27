# Test layouts made in Python: a Blockbench project and SVG edge cases.
#
# The Blockbench project is written the way Blockbench saves one
# (js/formats/bbmodel.js, Cube and Mesh getSaveCopy): cubes with box UV
# get the face rectangles Blockbench computes from uv_offset and the
# cube's size (Cube.preview_controller.updateUV in js/outliner/types/
# cube.js), with the mirror swap; a cube with per-face UV; a mesh with
# per-vertex UVs; a disabled face (texture null).
#
# Copyright 2026 David
# SPDX-License-Identifier: GPL-3.0-or-later

import json


def box_uv_faces(size, offset, mirror=False):
    """Blockbench's box UV face rectangles [x1, y1, x2, y2] of a cube of
    size (x, y, z) at uv_offset."""
    x, y, z = size
    faces = [
        ("east", [0, z], [z, y]),
        ("west", [z + x, z], [z, y]),
        ("up", [z + x, z], [-x, -z]),
        ("down", [z + x * 2, 0], [-x, z]),
        ("south", [z * 2 + x, z], [x, y]),
        ("north", [z, z], [x, y]),
    ]
    faces = [[n, list(f), list(s)] for n, f, s in faces]
    if mirror:
        for f in faces:
            f[1][0] += f[2][0]
            f[2][0] *= -1
        faces[0][1:], faces[1][1:] = faces[1][1:], faces[0][1:]
    out = {}
    for n, f, s in faces:
        out[n] = [f[0] + offset[0], f[1] + offset[1], f[0] + s[0] + offset[0],
                  f[1] + s[1] + offset[1]]
    return out


def net_cells(size, offset, mirror=False):
    """The unit cells of the texture a box UV net covers."""
    cells = set()
    for r in box_uv_faces(size, offset, mirror).values():
        x1, x2 = sorted((r[0], r[2]))
        y1, y2 = sorted((r[1], r[3]))
        for cx in range(x1, x2):
            for cy in range(y1, y2):
                cells.add((cx, cy))
    return cells


def perimeter(cells):
    """Length of the outline of a set of unit cells (outer and holes)."""
    n = 0
    for (x, y) in cells:
        for d in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            if (x + d[0], y + d[1]) not in cells:
                n += 1
    return n


CUBES = [  # size, uv_offset, mirror
    ((8, 12, 4), (0, 0), False),
    ((4, 12, 4), (32, 0), True),
    ((6, 6, 6), (0, 32), False),
]


def bbmodel():
    """(json text, expected): a project with a UV size of 64 x 64."""
    elements = []
    for i, (size, off, mirror) in enumerate(CUBES):
        faces = {n: {"uv": r, "texture": 0}
                 for n, r in box_uv_faces(size, off, mirror).items()}
        elements.append({"name": "cube%d" % i, "box_uv": True, "from": [0, 0, 0],
                         "to": list(size), "uv_offset": list(off), "mirror_uv": mirror,
                         "faces": faces, "type": "cube", "uuid": "c%d" % i})
    # a cube with per-face UV: two faces, one disabled
    elements.append({"name": "plate", "box_uv": False, "from": [0, 0, 0], "to": [4, 4, 1],
                     "faces": {"north": {"uv": [40, 40, 48, 48], "texture": 0},
                               "south": {"uv": [50, 40, 58, 48], "texture": None},
                               "up": {"uv": [40, 50, 44, 54], "texture": 1}},
                     "type": "cube", "uuid": "p"})
    # a mesh: two triangles and a quad forming one island
    elements.append({"name": "mesh", "type": "mesh", "uuid": "m",
                     "vertices": {"a": [0, 0, 0], "b": [1, 0, 0], "c": [1, 1, 0], "d": [0, 1, 0],
                                  "e": [2, 0, 0]},
                     "faces": {"f1": {"uv": {"a": [20, 40], "b": [30, 40], "c": [30, 50]},
                                      "vertices": ["a", "b", "c"], "texture": 0},
                               "f2": {"uv": {"a": [20, 40], "c": [30, 50], "d": [20, 50]},
                                      "vertices": ["a", "c", "d"], "texture": 0},
                               "f3": {"uv": {"b": [30, 40], "e": [36, 40], "c": [30, 50]},
                                      "vertices": ["b", "e", "c"], "texture": 0}}})
    model = {"meta": {"format_version": "5.0", "model_format": "free", "box_uv": True},
             "name": "test", "resolution": {"width": 64, "height": 64},
             "elements": elements, "outliner": [e["uuid"] for e in elements],
             "textures": [{"name": "skin", "uuid": "t0"}, {"name": "other", "uuid": "t1"}]}
    exp = {"islands": len(CUBES) + 2 + 1,
           "perimeters": [perimeter(net_cells(s, o, m)) for s, o, m in CUBES],
           "cells": [len(net_cells(s, o, m)) for s, o, m in CUBES]}
    return json.dumps(model, indent=1), exp


# SVG edge cases, each (name, svg text, islands, boundary edges)
SVGS = [
    ("transforms and relative paths",
     '<svg xmlns="http://www.w3.org/2000/svg" width="100" height="100">'
     '<g transform="translate(10 10) scale(2)"><path d="m0 0 h10 v10 h-10 z"/>'
     '<path d="M10 0 l10 0 0 10 -10 0 Z"/></g>'
     '<rect x="60" y="60" width="20" height="10" transform="rotate(0)"/></svg>', 2, 10),
    ("viewBox not at 0",
     '<svg xmlns="http://www.w3.org/2000/svg" viewBox="-50 -50 100 100" width="7cm">'
     '<polygon points="-50,-50 0,-50 0,0 -50,0"/></svg>', 1, 4),
    ("touching at a corner only",
     '<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10">'
     '<polygon points="0,0 5,0 5,5 0,5"/><polygon points="5,5 10,5 10,10 5,10"/></svg>', 2, 8),
    ("hole made of faces",
     '<svg xmlns="http://www.w3.org/2000/svg" width="3" height="3">' +
     "".join('<polygon points="%d,%d %d,%d %d,%d %d,%d"/>' % (x, y, x + 1, y, x + 1, y + 1, x, y + 1)
             for y in range(3) for x in range(3) if (x, y) != (1, 1)) + '</svg>', 1, 16),
    ("a mirrored copy stacked on an island",
     '<svg xmlns="http://www.w3.org/2000/svg" width="4" height="4">'
     '<polygon points="0,0 2,0 2,2 0,2"/><polygon points="2,0 4,0 4,2 2,2"/>'
     '<polygon points="4,0 2,0 2,2 4,2"/><polygon points="2,0 0,0 0,2 2,2"/></svg>', 2, 12),
    ("T-junction",
     '<svg xmlns="http://www.w3.org/2000/svg" width="4" height="4">'
     '<polygon points="0,0 2,0 2,2 0,2"/><polygon points="2,0 3,0 3,1 2,1"/>'
     '<polygon points="2,1 3,1 3,2 2,2"/></svg>', 1, 7),
    ("curves and an open line (ignored)",
     '<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10">'
     '<path d="M0 0 C 3 0 5 0 8 0 L8 8 Q 4 8 0 8 Z"/><line x1="0" y1="0" x2="5" y2="5"/>'
     '<polyline points="1,1 2,2 3,1"/></svg>', 1, 18),
]
