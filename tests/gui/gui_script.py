# Runs inside a real GIMP (Broadway display, tests/gui/gui_test.py
# starts it): does what the test asks, one command file at a time.
# The test writes UVT_GUI_OUT/cmd-N.json ({"do": ...}); this answers in
# res-N.json. Running a procedure INTERACTIVE shows its dialog; the
# answer comes when the dialog is closed. "quit" ends GIMP.
#
# Copyright 2026 David
# SPDX-License-Identifier: GPL-3.0-or-later
import hashlib
import json
import os
import sys
import time
import traceback

import gi
gi.require_version("Gimp", "3.0")
gi.require_version("Gegl", "0.4")
from gi.repository import Gimp, Gegl, Gio  # noqa: E402

out = os.environ["UVT_GUI_OUT"]
src = os.environ["UVT_SRC"]
cases = os.environ["UVT_CASES"]
sys.path.insert(0, os.path.join(src, "uv-tools"))
import uv_gimp as ug  # noqa: E402

Gegl.init(None)
pdb = Gimp.get_pdb()
S = {"image": None}


def answer(n, data):
    p = os.path.join(out, "res-%d.json" % n)
    with open(p + ".tmp", "w") as f:
        json.dump(data, f)
    os.rename(p + ".tmp", p)


def state():
    im = S["image"]
    layers = im.get_layers()
    digest = hashlib.sha1()
    for lay in layers:
        w, h = lay.get_width(), lay.get_height()
        digest.update(bytes(lay.get_buffer().get(Gegl.Rectangle.new(0, 0, w, h), 1.0,
                                                 "R'G'B'A u16", Gegl.AbyssPolicy.NONE)))
    ok, non_empty, x1, y1, x2, y2 = Gimp.Selection.bounds(im)
    return {"layers": [l.get_name() for l in layers], "tattoos": [l.get_tattoo() for l in layers],
            "paths": len(im.get_paths()), "island_paths": len(ug.island_paths(im)),
            "channels": [c.get_name() for c in im.get_channels()],
            "pixels": digest.hexdigest(), "selection": [non_empty, x1, y1, x2, y2]}


def run(name, mode, **args):
    im = S["image"]
    proc = pdb.lookup_procedure(name)
    cfg = proc.create_config()
    cfg.set_property("run-mode", mode)
    cfg.set_property("image", im)
    cfg.set_core_object_array("drawables", im.get_selected_layers())
    for k, v in args.items():
        cfg.set_property(k.replace("_", "-"), v)
    res = proc.run(cfg)
    return res.index(0).value_nick


def do(cmd):
    what = cmd["do"]
    if what == "open":
        im = Gimp.Image.new_with_precision(1024, 1024, Gimp.ImageBaseType.RGB,
                                           Gimp.Precision.U16_NON_LINEAR)
        lay = Gimp.Layer.new(im, "Texture", 1024, 1024, Gimp.ImageType.RGBA_IMAGE, 100.0,
                             Gimp.LayerMode.NORMAL)
        im.insert_layer(lay, None, 0)
        Gimp.context_set_gradient_fg_bg_rgb()
        Gimp.context_set_foreground(Gegl.Color.new("#3060a0"))
        Gimp.context_set_background(Gegl.Color.new("#e0c090"))
        lay.edit_gradient_fill(Gimp.GradientType.LINEAR, 0, False, 1, 0, True, 0, 0, 1024, 1024)
        im.clean_all()
        S["image"] = im
        Gimp.Display.new(im)
        Gimp.displays_flush()
        return state()
    if what == "import":
        f = Gio.File.new_for_path(os.path.join(cases, cmd["case"], "layout.svg"))
        st = run("plug-in-uv-tools-import", Gimp.RunMode.INTERACTIVE if cmd.get("dialog")
                 else Gimp.RunMode.NONINTERACTIVE, file=f)
        Gimp.displays_flush()
        return dict(state(), status=st)
    if what == "select-layer":
        im = S["image"]
        im.set_selected_layers([l for l in im.get_layers() if l.get_name() == cmd["name"]])
        return state()
    if what == "grey-layers":
        im = S["image"]
        for i, name in enumerate(("Metallic", "Roughness", "Occlusion")):
            lay = Gimp.Layer.new(im, name, 1024, 1024, Gimp.ImageType.RGBA_IMAGE, 100.0,
                                 Gimp.LayerMode.NORMAL)
            im.insert_layer(lay, None, 0)
            lay.fill(Gimp.FillType.WHITE if i == 1 else Gimp.FillType.TRANSPARENT)
        im.set_selected_layers([l for l in im.get_layers()
                                if l.get_name() in ("Occlusion", "Roughness", "Metallic")])
        return state()
    if what == "rect-in-island":
        # a small rectangle deep inside the biggest island
        import uv_model as um
        isl = um.find_islands(um.read_layout(os.path.join(cases, cmd["case"], "layout.svg")))
        ids = um.ids(isl, 1024, 1024)
        k = max(range(len(isl.islands)), key=lambda k: ids.count(k + 1))
        w = 1024
        px = next(i for i in range(4 * w, w * w - 4 * w)
                  if all(ids[i + dy * w + dx] == k + 1 for dx in range(-3, 4) for dy in range(-3, 4)))
        S["image"].select_rectangle(Gimp.ChannelOps.REPLACE, px % w - 2, px // w - 2, 5, 5)
        Gimp.displays_flush()
        return state()
    if what == "rect":
        S["image"].select_rectangle(Gimp.ChannelOps.REPLACE, *cmd["rect"])
        Gimp.displays_flush()
        return state()
    if what == "run":
        st = run(cmd["proc"], Gimp.RunMode.INTERACTIVE)
        Gimp.displays_flush()
        return dict(state(), status=st)
    if what == "state":
        return state()
    raise ValueError("unknown command %r" % what)


n = 0
start = time.time()
answer(0, {"ready": True})
while True:
    n += 1
    p = os.path.join(out, "cmd-%d.json" % n)
    while not os.path.exists(p):
        time.sleep(0.2)
        if time.time() - start > 1500:
            break
    if not os.path.exists(p):
        break
    cmd = json.load(open(p))
    if cmd["do"] == "quit":
        answer(n, {"quit": True})
        break
    try:
        answer(n, {"ok": True, "state": do(cmd)})
    except Exception as e:
        traceback.print_exc()
        answer(n, {"ok": False, "error": "%s: %s" % (type(e).__name__, e)})
proc = pdb.lookup_procedure("gimp-quit")
cfg = proc.create_config()
cfg.set_property("force", True)
proc.run(cfg)
