# Makes the pictures of the README (docs/images), inside gimp-console
# with UV Tools installed (tools/doc-images.sh runs it): Blender's UV
# layout with every edge next to UV Tools' island outline, and a texture
# before and after Seam Bleed. Uses the test layouts that tests/run.sh
# made with Blender.
#
# Copyright 2026 David
# SPDX-License-Identifier: GPL-3.0-or-later
import os
import sys

import gi
gi.require_version("Gimp", "3.0")
gi.require_version("Gegl", "0.4")
from gi.repository import Gimp, Gegl, Gio  # noqa: E402

SRC = os.environ["UVT_SRC"]
CASES = os.environ["UVT_CASES"]
OUT = os.path.join(SRC, "docs", "images")
sys.path.insert(0, os.path.join(SRC, "uv-tools"))
import uv_gimp as ug  # noqa: E402
import uv_model as um  # noqa: E402

Gegl.init(None)
pdb = Gimp.get_pdb()
os.makedirs(OUT, exist_ok=True)


def run(name, image, drawables, **args):
    proc = pdb.lookup_procedure(name)
    cfg = proc.create_config()
    cfg.set_property("run-mode", Gimp.RunMode.NONINTERACTIVE)
    cfg.set_property("image", image)
    cfg.set_core_object_array("drawables", drawables)
    for k, v in args.items():
        cfg.set_property(k.replace("_", "-"), v)
    res = proc.run(cfg)
    if res.index(0) != Gimp.PDBStatusType.SUCCESS:
        raise RuntimeError("%s failed: %s" % (name, res.index(1)))
    return [res.index(i) for i in range(1, res.length())]


def stroke(layer, path, color, width):
    Gimp.context_push()
    Gimp.context_set_foreground(Gegl.Color.new(color))
    Gimp.context_set_stroke_method(Gimp.StrokeMethod.LINE)
    Gimp.context_set_line_width(width)
    Gimp.context_set_antialias(True)
    layer.edit_stroke_item(path)
    Gimp.context_pop()


def save(image, name, crop=None):
    dup = image.duplicate()
    dup.flatten()
    if crop:
        dup.crop(*crop)
    Gimp.file_save(Gimp.RunMode.NONINTERACTIVE, dup, Gio.File.new_for_path(os.path.join(OUT, name)),
                   None)
    dup.delete()


svg = Gio.File.new_for_path(os.path.join(CASES, "monkey", "layout.svg"))
S = 768
# 1. every edge (what Blender's export shows) and the islands' outline
for name, what in [("edges.png", "all-edges"), ("outline.png", "outline")]:
    im = Gimp.Image.new(S, S, Gimp.ImageBaseType.RGB)
    bg = Gimp.Layer.new(im, "bg", S, S, Gimp.ImageType.RGB_IMAGE, 100, Gimp.LayerMode.NORMAL)
    im.insert_layer(bg, None, 0)
    Gimp.context_set_background(Gegl.Color.new("#d8d4cc"))
    bg.fill(Gimp.FillType.BACKGROUND)
    run("plug-in-uv-tools-import", im, [bg], file=svg, all_edges=(what == "all-edges"),
        island_paths=False, islands_channel=False)
    paths = {("edges" if ug.marked(p, ug.P_EDGES) else "outline"): p for p in im.get_paths()}
    stroke(bg, paths["edges" if what == "all-edges" else "outline"], "#202020", 1.2)
    save(im, name, (384, 384, 0, 0))
    im.delete()

# 2. seam bleed: islands in their own colours, black outside, then 8 px
im = Gimp.Image.new_with_precision(S, S, Gimp.ImageBaseType.RGB, Gimp.Precision.U8_NON_LINEAR)
lay = Gimp.Layer.new(im, "Texture", S, S, Gimp.ImageType.RGBA_IMAGE, 100, Gimp.LayerMode.NORMAL)
im.insert_layer(lay, None, 0)
r = run("plug-in-uv-tools-import", im, [lay], file=svg, island_ids_layer=True)
ids = [l for l in im.get_layers() if ug.marked(l, ug.P_IDS) is not None][0]
buf = ids.get_buffer()
raw = bytearray(buf.get(Gegl.Rectangle.new(0, 0, S, S), 1.0, "R'G'B'A u8", Gegl.AbyssPolicy.NONE))
# soften the island colours and make everything outside black
for i in range(0, len(raw), 4):
    if raw[i + 3]:
        for c in range(3):
            raw[i + c] = 90 + raw[i + c] * 150 // 255
    else:
        raw[i:i + 4] = b"\0\0\0\xff"
lb = lay.get_buffer()
lb.set(Gegl.Rectangle.new(0, 0, S, S), "R'G'B'A u8", bytes(raw))
lb.flush()
im.remove_layer(ids)
for p in list(im.get_paths()):
    im.remove_path(p)
save(im, "bleed-before.png", (256, 256, 128, 384))
lay = run("plug-in-uv-tools-seam-bleed", im, [lay], radius=8)[0]
save(im, "bleed-after.png", (256, 256, 128, 384))
im.delete()
print("DOC IMAGES done", flush=True)
