# Headless Blender: makes the test layouts. For each case a mesh with
# known UVs, and from it
#   layout.svg        Blender's UV > Export UV Layout (SVG)
#   layout.obj        the mesh as OBJ (texture coordinates)
#   gbl-uv.svg        GIMP Link for Blender's UV layout SVG
#   gbl-islands.svg   GIMP Link for Blender's islands SVG (island-N)
#   mask.png          GIMP Link's island mask (8-bit, pixel centres)
#   expected.json     islands and boundary edges, counted on the mesh
# The islands, boundary edges and mask come from GIMP Link for Blender's
# own code (blender/gimp_link/islands.py, next to this repository or in
# GBL_REPO): UV islands from the mesh (faces joined across edges whose
# two sides have the same UVs), boundary edges counted per face side.
#
#   blender -b --factory-startup --python tests/blender_make.py -- OUT
#
# Copyright 2026 David
# SPDX-License-Identifier: GPL-3.0-or-later

import json
import math
import os
import sys
import time

import bmesh
import bpy

here = os.path.dirname(os.path.abspath(__file__))
repo = os.path.dirname(here)
gbl = os.environ.get("GBL_REPO") or os.path.join(os.path.dirname(repo), "gimp-blender-link")
sys.path.insert(0, os.path.join(gbl, "blender"))
from gimp_link import islands as gisl, gbl_png  # noqa: E402

out = sys.argv[sys.argv.index("--") + 1]
os.makedirs(out, exist_ok=True)


def clear():
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete()
    for m in list(bpy.data.meshes):
        bpy.data.meshes.remove(m)


def mesh_from(name, quads):
    """A mesh of separate faces: quads is a list of faces, each a list
    of ((x, y, z), (u, v)); vertices with the same position are merged,
    so faces whose UVs match along an edge form one island."""
    me = bpy.data.meshes.new(name)
    bm = bmesh.new()
    verts = {}
    uvl = bm.loops.layers.uv.new("UVMap")
    for f in quads:
        vs = []
        for (p, uv) in f:
            k = tuple(round(c, 6) for c in p)
            if k not in verts:
                verts[k] = bm.verts.new(p)
            vs.append(verts[k])
        face = bm.faces.new(vs)
        for loop, (p, uv) in zip(face.loops, f):
            loop[uvl].uv = uv
    bm.to_mesh(me)
    bm.free()
    ob = bpy.data.objects.new(name, me)
    bpy.context.scene.collection.objects.link(ob)
    return ob


def grid_faces(nx, ny, u0, v0, du, dv, skip=(), z=0.0, x0=0.0, flip=False):
    """Faces of an nx x ny grid with UVs in one island; skip: (i, j)
    cells left out (holes)."""
    faces = []
    for j in range(ny):
        for i in range(nx):
            if (i, j) in skip:
                continue
            corners = [(i, j), (i + 1, j), (i + 1, j + 1), (i, j + 1)]
            f = []
            for (a, b) in corners:
                u = u0 + a * du
                v = v0 + b * dv
                if flip:
                    u = u0 + (nx - a) * du
                f.append(((x0 + a, b, z), (u, v)))
            if flip:
                f.reverse()
            faces.append(f)
    return faces


def export(ob, name, size, masks=True):
    folder = os.path.join(out, name)
    os.makedirs(folder, exist_ok=True)
    w, h = size
    for o in bpy.context.scene.objects:
        o.select_set(o == ob)
    bpy.context.view_layer.objects.active = ob
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="SELECT")
    t0 = time.time()
    r = bpy.ops.uv.export_layout(filepath=os.path.join(folder, "layout.svg"), mode="SVG",
                                 size=(w, h), export_all=True, opacity=0.25)
    t_svg = time.time() - t0
    bpy.ops.object.mode_set(mode="OBJECT")
    bpy.ops.wm.obj_export(filepath=os.path.join(folder, "layout.obj"), export_selected_objects=True,
                          export_uv=True, export_normals=False, export_materials=False,
                          apply_modifiers=False, forward_axis="Y", up_axis="Z")
    data = gisl.UVData()
    data.add_mesh(ob.data)
    isl = data.islands.get(1001, [])
    boundary = sum(len(lp) for i in isl for lp in i["loops"])
    with open(os.path.join(folder, "gbl-uv.svg"), "w") as f:
        f.write(gisl.layout_svg(data.faces.get(1001, []), w, h))
    with open(os.path.join(folder, "gbl-islands.svg"), "w") as f:
        f.write(gisl.islands_svg(isl, w, h))
    t_mask = None
    if masks:
        t0 = time.time()
        ids = gisl.rasterize(isl, w, h)
        t_mask = time.time() - t0
        m = (ids > 0).astype("uint8") * 255
        gbl_png.write(os.path.join(folder, "mask.png"), w, h, 1, 8, m.tobytes())
    exp = {"name": name, "width": w, "height": h, "faces": len(ob.data.polygons),
           "islands": len(isl), "boundary_edges": boundary, "export": list(r),
           "seconds_svg": t_svg, "seconds_mask": t_mask,
           "blender": bpy.app.version_string}
    with open(os.path.join(folder, "expected.json"), "w") as f:
        json.dump(exp, f, indent=1)
    print("MADE", name, exp["faces"], "faces", exp["islands"], "islands",
          boundary, "boundary edges", flush=True)


clear()
# a cube unwrapped by Blender (Smart UV Project): six islands
bpy.ops.mesh.primitive_cube_add()
cube = bpy.context.object
bpy.ops.object.mode_set(mode="EDIT")
bpy.ops.mesh.select_all(action="SELECT")
bpy.ops.uv.smart_project(island_margin=0.05)
bpy.ops.object.mode_set(mode="OBJECT")
export(cube, "cube", (256, 256))

clear()
# an island with holes (a grid with cells left out), and a second one
faces = grid_faces(8, 8, 0.05, 0.05, 0.05, 0.05, skip={(2, 2), (2, 3), (3, 2), (5, 5)})
faces += grid_faces(3, 5, 0.6, 0.1, 0.1, 0.15, x0=20)
export(mesh_from("holes", faces), "holes", (256, 256))

clear()
# overlapping islands: two islands crossing each other, and a mirrored
# copy stacked exactly on a third one (mirrored UVs, as for symmetry)
faces = grid_faces(4, 4, 0.1, 0.1, 0.1, 0.1)
faces += grid_faces(4, 2, 0.25, 0.3, 0.1, 0.1, x0=10)
faces += grid_faces(3, 3, 0.55, 0.55, 0.1, 0.1, x0=20)
faces += grid_faces(3, 3, 0.55, 0.55, 0.1, 0.1, x0=30, flip=True)
export(mesh_from("overlap", faces), "overlap", (256, 256))

clear()
# islands touching the border of the texture (0 and 1) and a corner
faces = grid_faces(2, 10, 0.0, 0.0, 0.1, 0.1)
faces += grid_faces(3, 3, 0.7, 0.7, 0.1, 0.1, x0=10)
faces += grid_faces(4, 2, 0.4, 0.0, 0.05, 0.05, x0=20)
export(mesh_from("border", faces), "border", (200, 100))

clear()
# a model unwrapped by Blender: Suzanne, subdivided, Smart UV Project
bpy.ops.mesh.primitive_monkey_add()
monkey = bpy.context.object
mod = monkey.modifiers.new("s", "SUBSURF")
mod.levels = 1
bpy.ops.object.modifier_apply(modifier="s")
bpy.ops.object.mode_set(mode="EDIT")
bpy.ops.mesh.select_all(action="SELECT")
bpy.ops.uv.smart_project(angle_limit=math.radians(50), island_margin=0.01)
bpy.ops.object.mode_set(mode="OBJECT")
export(monkey, "monkey", (1024, 1024))

clear()
# a big mesh with many islands, for speed
bpy.ops.mesh.primitive_monkey_add()
big = bpy.context.object
mod = big.modifiers.new("s", "SUBSURF")
mod.levels = 3
bpy.ops.object.modifier_apply(modifier="s")
bpy.ops.object.mode_set(mode="EDIT")
bpy.ops.mesh.select_all(action="SELECT")
bpy.ops.uv.smart_project(angle_limit=math.radians(20), island_margin=0.002)
bpy.ops.object.mode_set(mode="OBJECT")
export(big, "big", (2048, 2048))

clear()
# a huge mesh with many islands (no mask: GIMP Link's numpy rasteriser
# takes long on it), for the speed of reading and finding islands
bpy.ops.mesh.primitive_monkey_add()
huge = bpy.context.object
mod = huge.modifiers.new("s", "SUBSURF")
mod.levels = 4
bpy.ops.object.modifier_apply(modifier="s")
bpy.ops.object.mode_set(mode="EDIT")
bpy.ops.mesh.select_all(action="SELECT")
bpy.ops.uv.smart_project(angle_limit=math.radians(15), island_margin=0.001)
bpy.ops.object.mode_set(mode="OBJECT")
export(huge, "huge", (4096, 4096), masks=False)
print("BLENDER MAKE done", flush=True)
