# Runs inside gimp-console (tests/run.sh): the plug-in's procedures
# through the PDB, as a script would call them, on the layouts Blender
# made (UVT_CASES) and on made-up images. Prints UVT PASS or UVT FAIL per
# check, UVT TIME lines, and "UVT GIMP failures: N" at the end.
#
# Copyright 2026 David
# SPDX-License-Identifier: GPL-3.0-or-later

import json
import os
import random
import struct
import sys
import time
import traceback

import gi
gi.require_version("Gimp", "3.0")
gi.require_version("Gegl", "0.4")
from gi.repository import Gimp, Gegl, Gio, GObject  # noqa: E402

SRC = os.environ["UVT_SRC"]
OUT = os.environ["UVT_OUT"]
CASES = os.environ.get("UVT_CASES", "")
ONLY = os.environ.get("UVT_ONLY", "")
sys.path.insert(0, os.path.join(SRC, "uv-tools"))
sys.path.insert(0, os.path.join(SRC, "tests"))
import uv_model as um  # noqa: E402
import uv_gimp as ug  # noqa: E402
import uv_dilate_ref as ref  # noqa: E402
import fixtures  # noqa: E402
import pngio  # noqa: E402

Gegl.init(None)
pdb = Gimp.get_pdb()
fails = [0]
os.makedirs(OUT, exist_ok=True)


def check(name, ok, detail=""):
    print("UVT %s gimp %s%s" % ("PASS" if ok else "FAIL", name,
                                "" if ok or detail == "" else ": %s" % (detail,)), flush=True)
    if not ok:
        fails[0] += 1


def run(proc_name, image=None, drawables=None, **args):
    """Runs a procedure non-interactively; returns the result values."""
    name = proc_name
    proc = pdb.lookup_procedure(name)
    cfg = proc.create_config()
    cfg.set_property("run-mode", Gimp.RunMode.NONINTERACTIVE)
    if image is not None:
        cfg.set_property("image", image)
        cfg.set_core_object_array("drawables", drawables or [])
    for k, v in args.items():
        cfg.set_property(k.replace("_", "-"), v)
    res = proc.run(cfg)
    status = res.index(0)
    if status != Gimp.PDBStatusType.SUCCESS:
        raise RuntimeError("%s: %s %s" % (name, status.value_nick,
                                          res.index(1) if res.length() > 1 else ""))
    return [res.index(i) for i in range(1, res.length())]


def new_image(w, h, precision=Gimp.Precision.U8_NON_LINEAR, typ=Gimp.ImageType.RGBA_IMAGE,
              base=Gimp.ImageBaseType.RGB):
    im = Gimp.Image.new_with_precision(w, h, base, precision)
    lay = Gimp.Layer.new(im, "paint", w, h, typ, 100.0, Gimp.LayerMode.NORMAL)
    im.insert_layer(lay, None, 0)
    return im, lay


def read(drawable, fmt, rect=None):
    x, y, w, h = rect or (0, 0, drawable.get_width(), drawable.get_height())
    return bytes(drawable.get_buffer().get(Gegl.Rectangle.new(x, y, w, h), 1.0, fmt,
                                           Gegl.AbyssPolicy.NONE))


def write(drawable, fmt, data):
    w, h = drawable.get_width(), drawable.get_height()
    buf = drawable.get_buffer()
    buf.set(Gegl.Rectangle.new(0, 0, w, h), fmt, data)
    buf.flush()
    drawable.update(0, 0, w, h)


def gfile(path):
    return Gio.File.new_for_path(path)


def section(name):
    return not ONLY or name in ONLY.split(",")


def safe(fn):
    try:
        fn()
    except Exception as e:
        traceback.print_exc()
        check("%s ran" % fn.__name__, False, "%s: %s" % (type(e).__name__, e))


def segs_of_path(path, q=1000):
    out = []
    for lp in ug.path_loops(path):
        for a, b in zip(lp, lp[1:] + lp[:1]):
            ka = (round(a[0] * q), round(a[1] * q))
            kb = (round(b[0] * q), round(b[1] * q))
            out.append((min(ka, kb), max(ka, kb)))
    return sorted(out)


def segs_of_loops(loops, w, h, q=1000):
    out = []
    for lp in loops:
        for a, b in zip(lp, lp[1:] + lp[:1]):
            ka = (round(a[0] * w * q), round(a[1] * h * q))
            kb = (round(b[0] * w * q), round(b[1] * h * q))
            out.append((min(ka, kb), max(ka, kb)))
    return sorted(out)


# ------------------------------------------------------------ procedures
def t_procedures():
    names = {"plug-in-uv-tools-import": "<Image>/Image/UV Tools",
             "plug-in-uv-tools-select-islands": "<Image>/Select",
             "plug-in-uv-tools-seam-bleed": "<Image>/Image/UV Tools",
             "plug-in-uv-tools-fill-outside": "<Image>/Image/UV Tools",
             "plug-in-uv-tools-pack-channels": "<Image>/Image/UV Tools",
             "plug-in-uv-tools-unpack-channels": "<Image>/Image/UV Tools"}
    for n, menu in names.items():
        p = pdb.lookup_procedure(n)
        check("procedure %s in %s" % (n, menu), p is not None and menu in p.get_menu_paths(),
              p and p.get_menu_paths())


# ------------------------------------------------------------ import
def t_import():
    if not CASES:
        check("Blender's layouts", False, "UVT_CASES not set")
        return
    for case in sorted(os.listdir(CASES)):
        folder = os.path.join(CASES, case)
        e = json.load(open(os.path.join(folder, "expected.json")))
        w, h = e["width"], e["height"]
        im, lay = new_image(w, h)
        t0 = time.time()
        islands, faces, outline, channel = run("plug-in-uv-tools-import", im, [lay],
                                               file=gfile(os.path.join(folder, "layout.svg")))
        dt = time.time() - t0
        if case in ("big", "huge"):
            print("UVT TIME import %s (%d faces, %d islands) into %dx%d: %.2f s"
                  % (case, faces, islands, w, h, dt), flush=True)
        check("import %s: %d islands, %d faces" % (case, e["islands"], e["faces"]),
              (islands, faces) == (e["islands"], e["faces"]), (islands, faces))
        gbl = um.find_islands(um.parse_svg(open(os.path.join(folder, "gbl-islands.svg")).read()))
        want = segs_of_loops([lp for i in gbl.islands for lp in i.loops], w, h)
        got = segs_of_path(outline)
        check("import %s: the outline path has exactly the mesh's %d boundary edges, no "
              "internal edges" % (case, e["boundary_edges"]), got == want,
              "%d vs %d" % (len(got), len(want)))
        check("import %s: the outline path is shown, named UV Islands" % case,
              outline.get_visible() and outline.get_name() == "UV Islands")
        paths = ug.island_paths(im)
        check("import %s: one path per island, UV Island 1..%d" % (case, e["islands"]),
              sorted(paths) == list(range(1, e["islands"] + 1))
              and paths[1].get_name() == "UV Island 1")
        mine = um.mask(um.find_islands(um.read_layout(os.path.join(folder, "layout.svg"))), w, h)
        ch = bytes(channel.get_buffer().get(Gegl.Rectangle.new(0, 0, w, h), 1.0, "Y u8",
                                            Gegl.AbyssPolicy.NONE))
        check("import %s: the UV Islands channel is the islands' pixels" % case, ch == bytes(mine))
        mp = os.path.join(folder, "mask.png")
        if os.path.exists(mp):
            refm = pngio.read_grey8(mp)[2]
            diff = sum(1 for a, b in zip(ch, refm) if (a > 127) != (b > 127))
            # (unit.py checks that each differing pixel's centre is within
            # the SVG's rounding of an edge)
            check("import %s: channel vs GIMP Link's island mask: %d of %d pixels differ"
                  % (case, diff, w * h), diff <= max(0, w * h // 50000))
        im.delete()


def t_import_more():
    folder = os.path.join(CASES, "monkey")
    im, lay = new_image(1024, 1024)
    r1 = run("plug-in-uv-tools-import", im, [lay], file=gfile(os.path.join(folder, "layout.svg")),
             island_ids_layer=True, outline_layer=True, all_edges=True)
    ids = [l for l in im.get_layers() if ug.marked(l, ug.P_IDS) is not None]
    guide = [l for l in im.get_layers() if ug.marked(l, ug.P_GUIDE) is not None]
    edges = [p for p in im.get_paths() if ug.marked(p, ug.P_EDGES) is not None]
    check("import with the IDs layer, outline layer and all edges", len(ids) == 1
          and len(guide) == 1 and len(edges) == 1 and not ids[0].get_visible())
    isl = um.find_islands(um.read_layout(os.path.join(folder, "layout.svg")))
    got = read(ids[0], "R'G'B'A u8")
    idm = um.ids(isl, 1024, 1024)
    colors = {}
    ok = True
    for i in range(0, 1024 * 1024, 7):
        n = idm[i]
        px = got[4 * i:4 * i + 4]
        if n == 0:
            ok = ok and px[3] == 0
        else:
            ok = ok and px[3] == 255 and px[:3] == ug.id_color(n)
            colors.setdefault(px[:3], set()).add(n)
    check("IDs layer: every island in its own colour", ok and all(len(v) == 1
                                                                 for v in colors.values()))
    g = read(guide[0], "R'G'B'A u8")
    check("outline layer has the outline drawn", sum(1 for i in range(3, len(g), 4) if g[i]) > 1000)
    nall = sum(1 for p in im.get_paths())
    run("plug-in-uv-tools-import", im, [lay], file=gfile(os.path.join(folder, "layout.svg")))
    nch = sum(1 for c in im.get_channels() if ug.marked(c, ug.P_CHANNEL) is not None)
    check("importing again replaces the earlier import", nch == 1
          and len(ug.island_paths(im)) == len(isl.islands)
          and not any(ug.marked(l, ug.P_IDS) for l in im.get_layers()), (nall, nch))
    # OBJ: V is up in OBJ; the same channel as from the SVG (up to rounding)
    run("plug-in-uv-tools-import", im, [lay], file=gfile(os.path.join(folder, "layout.obj")))
    a = bytes(ug.islands_channel(im).get_buffer().get(Gegl.Rectangle.new(0, 0, 1024, 1024), 1.0,
                                                      "Y u8", Gegl.AbyssPolicy.NONE))
    d = sum(1 for x, y in zip(a, um.mask(isl, 1024, 1024)) if x != y)
    check("OBJ import = SVG import (%d pixels differ by rounding)" % d, d < 30)
    # flip V
    run("plug-in-uv-tools-import", im, [lay], file=gfile(os.path.join(folder, "layout.svg")),
        flip_v=True)
    a = bytes(ug.islands_channel(im).get_buffer().get(Gegl.Rectangle.new(0, 0, 1024, 1024), 1.0,
                                                      "Y u8", Gegl.AbyssPolicy.NONE))
    fm = um.mask(um.find_islands(um.read_layout(os.path.join(folder, "layout.svg")).flipped()),
                 1024, 1024)
    check("flip V", a == bytes(fm))
    im.delete()
    # Blockbench
    text, exp = fixtures.bbmodel()
    p = os.path.join(OUT, "test.bbmodel")
    open(p, "w").write(text)
    im, lay = new_image(64, 64)
    n, faces, outline, channel = run("plug-in-uv-tools-import", im, [lay], file=gfile(p))
    loops = ug.path_loops(outline)
    check("Blockbench import: %d islands, box UV nets without inner edges" % exp["islands"],
          n == exp["islands"] and len(loops) == exp["islands"], (n, len(loops)))
    im.delete()
    # an unusable file
    bad = os.path.join(OUT, "bad.svg")
    open(bad, "w").write("<svg xmlns='http://www.w3.org/2000/svg'/>")
    im, lay = new_image(8, 8)
    try:
        run("plug-in-uv-tools-import", im, [lay], file=gfile(bad))
        check("a bad file is refused", False)
    except RuntimeError as e:
        check("a bad file is refused (%s)" % str(e)[:80], "execution-error" in str(e))
    im.delete()


# ------------------------------------------------------------ select
def sel_bytes(im):
    return ug.selection_mask(im)


def t_select():
    folder = os.path.join(CASES, "monkey")
    w = h = 1024
    im, lay = new_image(w, h)
    run("plug-in-uv-tools-import", im, [lay], file=gfile(os.path.join(folder, "layout.svg")))
    # the islands straight from the file, not from the plug-in's paths
    isl = um.find_islands(um.read_layout(os.path.join(folder, "layout.svg")))

    def m(which):
        return um.mask(isl, w, h, which=set(which))

    def close(sel, want, name):
        area = sum(1 for v in want if v)
        d = sum(1 for a, b in zip(sel, want) if (a > 127) != (b > 127))
        # the paths hold 4 decimals: only pixel centres that close to an
        # outline may differ
        check("%s (%d pixels, %d differ)" % (name, area, d), area > 0 and d <= max(2, area // 2000))
    run("plug-in-uv-tools-select-islands", im, [lay], mode="number", island=5)
    close(sel_bytes(im), m([4]), "select island number 5")
    run("plug-in-uv-tools-select-islands", im, [lay], mode="number", island=9, operation="add")
    close(sel_bytes(im), m([4, 8]), "add island 9")
    run("plug-in-uv-tools-select-islands", im, [lay], mode="number", island=5,
        operation="subtract")
    close(sel_bytes(im), m([8]), "subtract island 5")
    run("plug-in-uv-tools-select-islands", im, [lay], mode="number", island=9,
        operation="intersect")
    close(sel_bytes(im), m([8]), "intersect with island 9")
    # a pixel deep inside the biggest island (no other island near it)
    ids = um.ids(isl, w, h)
    big = max(range(len(isl.islands)), key=lambda k: sum(1 for v in ids if v == k + 1))
    px = next(i for i in range(w + 1, w * h - w - 1)
              if all(ids[i + d] == big + 1 for d in (-w - 1, -w, -w + 1, -1, 0, 1, w - 1, w, w + 1)))
    x, y = px % w, px // w
    im.select_rectangle(Gimp.ChannelOps.REPLACE, x, y, 1, 1)
    n = run("plug-in-uv-tools-select-islands", im, [lay], mode="touching")[0]
    check("select the island a 1 px selection touches: one island", n == 1, n)
    close(sel_bytes(im), m([big]), "... and it is the island under it")
    im.select_rectangle(Gimp.ChannelOps.REPLACE, x - 1, y - 1, 3, 3)
    run("plug-in-uv-tools-select-islands", im, [lay], mode="centre")
    close(sel_bytes(im), m([big]), "select the island at the selection's centre")
    # a rectangle over two islands
    b0, b1 = isl.islands[0].bbox, isl.islands[1].bbox
    im.select_rectangle(Gimp.ChannelOps.REPLACE, 0, 0, w, h)
    n = run("plug-in-uv-tools-select-islands", im, [lay], mode="touching")[0]
    check("a selection over everything touches all %d islands" % len(isl.islands),
          n == len(isl.islands), n)
    run("plug-in-uv-tools-select-islands", im, [lay], mode="all")
    close(sel_bytes(im), m(range(len(isl.islands))), "select all islands")
    Gimp.Selection.none(im)
    try:
        run("plug-in-uv-tools-select-islands", im, [lay], mode="touching")
        check("touching without a selection is refused", False)
    except RuntimeError:
        check("touching without a selection is refused", True)
    im.delete()


# ------------------------------------------------------------ seam bleed
PRECISIONS = [
    ("u8 perceptual", Gimp.Precision.U8_NON_LINEAR, "R'G'B'A u8", 4),
    ("u8 linear", Gimp.Precision.U8_LINEAR, "RGBA u8", 4),
    ("u16 perceptual", Gimp.Precision.U16_NON_LINEAR, "R'G'B'A u16", 8),
    ("u16 linear", Gimp.Precision.U16_LINEAR, "RGBA u16", 8),
    ("half linear", Gimp.Precision.HALF_LINEAR, "RGBA half", 8),
    ("float linear", Gimp.Precision.FLOAT_LINEAR, "RGBA float", 16),
    ("float perceptual", Gimp.Precision.FLOAT_NON_LINEAR, "R'G'B'A float", 16),
    ("u32 linear", Gimp.Precision.U32_LINEAR, "RGBA u32", 16),
]


def random_pixels(rng, fmt, bpp, n):
    if "float" in fmt:
        return b"".join(struct.pack("<4f", *[rng.uniform(-0.5, 2.0) for _ in range(3)],
                                    rng.choice([0.0, 0.3, 1.0])) for _ in range(n))
    if "half" in fmt:
        return b"".join(struct.pack("<4e", *[rng.uniform(0, 1.5) for _ in range(3)],
                                    rng.choice([0.0, 0.5, 1.0])) for _ in range(n))
    return bytes(rng.randrange(256) for _ in range(bpp * n))


def windowed_reference(mask, w, h, n):
    """reference offsets, fast enough for a few hundred pixels across:
    a window search with the same order as uv_dilate_ref.offsets."""
    cand = sorted(((dx * dx + dy * dy, dx, dy * dy, dy), dx, dy)
                  for dx in range(-n, n + 1) for dy in range(-n, n + 1) if dx * dx + dy * dy <= n * n)
    out = []
    for y in range(h):
        for x in range(w):
            if mask[y * w + x]:
                out.append((0, 0))
                continue
            o = (0, 0)
            for (_k, dx, dy) in cand:
                xx, yy = x + dx, y + dy
                if 0 <= xx < w and 0 <= yy < h and mask[yy * w + xx]:
                    o = (dx, dy)
                    break
            out.append(o)
    return out


def bleed_case(label, w, h, n, prec, fmt, bpp, rng, density=0.03, source="channel",
               offsets=None, blobs=False, grey=False):
    typ = Gimp.ImageType.GRAYA_IMAGE if grey else Gimp.ImageType.RGBA_IMAGE
    im, lay = new_image(w, h, prec, typ,
                        Gimp.ImageBaseType.GRAY if grey else Gimp.ImageBaseType.RGB)
    lw, lh, ox, oy = w, h, 0, 0
    if offsets:
        ox, oy, lw, lh = offsets
        im.remove_layer(lay)
        lay = Gimp.Layer.new(im, "paint", lw, lh, typ, 100.0, Gimp.LayerMode.NORMAL)
        im.insert_layer(lay, None, 0)
        lay.set_offsets(ox, oy)
    mask = [False] * (w * h)
    if blobs:
        for _ in range(12):
            cx, cy, r = rng.randrange(w), rng.randrange(h), rng.randint(3, 25)
            for y in range(max(0, cy - r), min(h, cy + r)):
                for x in range(max(0, cx - r), min(w, cx + r)):
                    if (x - cx) ** 2 + (y - cy) ** 2 < r * r:
                        mask[y * w + x] = True
    else:
        mask = [rng.random() < density for _ in range(w * h)]
    mbytes = bytes(255 if v else 0 for v in mask)
    ch = ug.new_channel(im, "islands", mbytes)
    pixfmt = fmt if not grey else fmt.replace("R'G'B'A", "Y'A").replace("RGBA", "YA")
    pbpp = bpp if not grey else bpp // 2
    before = random_pixels(rng, fmt, bpp, lw * lh)
    if grey:
        before = bytes(b for i in range(lw * lh) for b in before[i * bpp:i * bpp + pbpp])
    write(lay, pixfmt, before)
    before = read(lay, pixfmt)
    args = dict(radius=n, islands_from=source)
    if source == "channel":
        args["islands_channel"] = ch
    elif source == "selection":
        im.select_item(Gimp.ChannelOps.REPLACE, ch)
    elif source == "alpha":
        # the islands are the opaque pixels of the layer
        amask = []
        comp = pbpp // (2 if grey else 4)
        for i in range(lw * lh):
            a = before[(i + 1) * pbpp - comp:(i + 1) * pbpp]
            if "float" in fmt:
                v = struct.unpack("<f", a)[0]
            elif "half" in fmt:
                v = struct.unpack("<e", a)[0]
            else:
                v = int.from_bytes(a, "little") / float((1 << (8 * comp)) - 1)
            amask.append(v)
        rgba_mask = [False] * (w * h)
        for i, v in enumerate(amask):
            rgba_mask[(i // lw + oy) * w + i % lw + ox] = round(v * 255) > 127
        mask = rgba_mask
    t0 = time.time()
    tattoo, name = lay.get_tattoo(), lay.get_name()
    lay2 = run("plug-in-uv-tools-seam-bleed", im, [lay], **args)[0]
    dt = time.time() - t0
    if lay2 != lay:
        check("seam bleed %s: the result layer takes the old one's place, name and tattoo"
              % label, im.get_layer_by_tattoo(tattoo) == lay2 and lay2.get_name() == name
              and len(im.get_layers()) == 1 and lay2.get_offsets()[1:] == (ox, oy))
    lay = lay2
    after = read(lay, pixfmt)
    # the reference, in the layer's coordinates
    lmask = [mask[(y + oy) * w + x + ox] for y in range(lh) for x in range(lw)]
    if lw * lh <= 1500:
        offs = ref.offsets(lmask, lw, lh, n)
        near = ref.nearest_sets(lmask, lw, lh, n)
    else:
        offs = windowed_reference(lmask, lw, lh, n)
        near = None
    bad = []

    def same(a, b):
        if "u32" not in fmt:
            return a == b
        # 32-bit integers go through float32 (24-bit mantissa)
        va, vb = struct.unpack("<%dI" % (len(a) // 4), a), struct.unpack("<%dI" % (len(b) // 4), b)
        return all(abs(x - y) <= max(1, y) * 2 ** -23 for x, y in zip(va, vb))
    for i in range(lw * lh):
        dx, dy = offs[i]
        j = i + dy * lw + dx
        got = after[i * pbpp:(i + 1) * pbpp]
        if not same(got, before[j * pbpp:(j + 1) * pbpp]):
            code = {1: "B", 2: "H", 4: "f" if ("float" in fmt) else "I"}[pbpp // (2 if grey else 4)]
            if "half" in fmt:
                code = "e"
            bad.append((i % lw, i // lw, (dx, dy),
                        struct.unpack("<" + code * (2 if grey else 4), got),
                        struct.unpack("<" + code * (2 if grey else 4), before[j * pbpp:(j + 1) * pbpp])))
        elif near is not None and near[i] is not None:
            if not any(same(got, before[(yy * lw + xx) * pbpp:(yy * lw + xx + 1) * pbpp])
                       for (xx, yy) in near[i]):
                bad.append((i % lw, i // lw, "not a nearest pixel's value"))
    n_fill = sum(1 for i in range(lw * lh) if offs[i] != (0, 0))
    check("seam bleed %s: %dx%d, %d px, islands from %s: %d pixels bled, island pixels and "
          "pixels farther than %d unchanged, each bled pixel %s the value of its "
          "nearest island pixel" % (label, lw, lh, n, source, n_fill, n,
                                    "(to float precision)" if "u32" in fmt else "exactly"),
          not bad, bad[:5])
    im.delete()
    return dt


def t_bleed():
    rng = random.Random(7)
    for label, prec, fmt, bpp in PRECISIONS:
        for n, w, h, dens in [(1, 17, 13, 0.08), (3, 30, 22, 0.02), (6, 33, 29, 0.01)]:
            bleed_case(label, w, h, n, prec, fmt, bpp, rng, dens)
    bleed_case("u16 perceptual", 31, 23, 4, *PRECISIONS[2][1:], rng=rng, source="selection")
    bleed_case("u8 perceptual", 31, 23, 3, *PRECISIONS[0][1:], rng=rng, source="alpha",
               density=0.0)
    bleed_case("u8 perceptual, a layer smaller than the image and moved", 40, 30, 3,
               *PRECISIONS[0][1:], rng=rng, offsets=(5, 7, 26, 19))
    bleed_case("grey u16 perceptual", 31, 23, 4, *PRECISIONS[2][1:], rng=rng, grey=True)
    bleed_case("grey u8 perceptual", 31, 23, 4, *PRECISIONS[0][1:], rng=rng, grey=True)
    bleed_case("grey float linear", 31, 23, 4, *PRECISIONS[5][1:], rng=rng, grey=True)
    bleed_case("grey float perceptual", 31, 23, 4, *PRECISIONS[6][1:], rng=rng, grey=True)
    # many tiles: work split into rectangles
    bleed_case("u16 linear, 300x270 in tiles, blobs", 300, 270, 9, *PRECISIONS[3][1:], rng=rng,
               blobs=True)


def t_bleed_more():
    rng = random.Random(5)
    w, h, n = 40, 30, 4
    # a channel (data in a channel): merged the usual way, exact
    for label, prec, fmt, bpp in [PRECISIONS[0], PRECISIONS[3], PRECISIONS[5]]:
        im, lay = new_image(w, h, prec)
        mask = [rng.random() < 0.03 for _ in range(w * h)]
        isl = ug.new_channel(im, "islands", bytes(255 if v else 0 for v in mask))
        data = ug.new_channel(im, "data", bytes(rng.randrange(256) for _ in range(w * h)))
        cf = fmt.replace("R'G'B'A", "Y").replace("RGBA", "Y")
        cb = bpp // 4
        before = read(data, cf)
        tattoo = data.get_tattoo()
        d2 = run("plug-in-uv-tools-seam-bleed", im, [data], radius=n, islands_channel=isl)[0]
        after = read(d2, cf)
        offs = windowed_reference(mask, w, h, n)
        ok = d2.get_name() == "data" and d2.get_tattoo() == tattoo and all(after[i * cb:(i + 1) * cb] == before[(i + dy * w + dx) * cb:
                                                                    (i + dy * w + dx + 1) * cb]
                                for i, (dx, dy) in enumerate(offs))
        check("seam bleed of a channel (%s): exact; the result takes its place, name and tattoo"
              % label, ok)
        im.delete()
    # a selection limits where pixels change
    im, lay = new_image(w, h, Gimp.Precision.U16_NON_LINEAR)
    mask = [rng.random() < 0.03 for _ in range(w * h)]
    isl = ug.new_channel(im, "islands", bytes(255 if v else 0 for v in mask))
    write(lay, "R'G'B'A u16", random_pixels(rng, "R'G'B'A u16", 8, w * h))
    before = read(lay, "R'G'B'A u16")
    im.select_rectangle(Gimp.ChannelOps.REPLACE, 10, 5, 20, 15)
    lay = run("plug-in-uv-tools-seam-bleed", im, [lay], radius=n, islands_channel=isl)[0]
    after = read(lay, "R'G'B'A u16")
    offs = windowed_reference(mask, w, h, n)
    ok = True
    for i, (dx, dy) in enumerate(offs):
        x, y = i % w, i // w
        inside = 10 <= x < 30 and 5 <= y < 20
        j = i + dy * w + dx if inside else i
        ok = ok and after[8 * i:8 * i + 8] == before[8 * j:8 * j + 8]
    check("seam bleed only inside the selection, which stays", ok and
          Gimp.Selection.bounds(im)[2:] == (10, 5, 30, 20))
    im.delete()
    # things that are refused
    im, lay = new_image(w, h)
    grp = Gimp.GroupLayer.new(im, "group")
    im.insert_layer(grp, None, 0)
    try:
        run("plug-in-uv-tools-seam-bleed", im, [lay], radius=n)
        check("seam bleed without an islands channel is refused", False)
    except RuntimeError as e:
        check("seam bleed without an islands channel is refused", "Import UV Layout" in str(e))
    ug.new_channel(im, "x", bytes(w * h))
    try:
        run("plug-in-uv-tools-seam-bleed", im, [grp], radius=n, islands_from="alpha")
        check("seam bleed on a layer group is refused", False)
    except RuntimeError as e:
        check("seam bleed on a layer group is refused", "group" in str(e))
    im.delete()


def t_bleed_speed():
    folder = os.path.join(CASES, "huge")
    for prec, label in [(Gimp.Precision.U16_NON_LINEAR, "16-bit"),
                        (Gimp.Precision.U8_NON_LINEAR, "8-bit"),
                        (Gimp.Precision.FLOAT_LINEAR, "float")]:
        im, lay = new_image(4096, 4096, prec)
        lay.fill(Gimp.FillType.WHITE)
        run("plug-in-uv-tools-import", im, [lay], file=gfile(os.path.join(folder, "layout.svg")),
            island_paths=False)
        for n in ((4, 16) if label == "16-bit" else (16,)):
            t0 = time.time()
            lay, px = run("plug-in-uv-tools-seam-bleed", im, [lay], radius=n)
            print("UVT TIME seam bleed 4096x4096 %s, huge layout (1579 islands), %d px: %.2f s "
                  "(%.0f%% of the image worked on)" % (label, n, time.time() - t0,
                                                     100.0 * px / (4096 * 4096)), flush=True)
        im.delete()
    # the monkey at 4K: fewer, bigger islands
    im, lay = new_image(4096, 4096, Gimp.Precision.U16_NON_LINEAR)
    run("plug-in-uv-tools-import", im, [lay],
        file=gfile(os.path.join(CASES, "monkey", "layout.svg")), island_paths=False)
    t0 = time.time()
    lay, px = run("plug-in-uv-tools-seam-bleed", im, [lay], radius=16)
    print("UVT TIME seam bleed 4096x4096 16-bit, monkey layout (139 islands), 16 px: %.2f s "
          "(%.0f%% of the image worked on)" % (time.time() - t0, 100.0 * px / (4096 * 4096)),
          flush=True)
    im.delete()


# ------------------------------------------------------------ fill outside
def t_fill():
    rng = random.Random(3)
    w, h = 24, 16
    mask = [rng.random() < 0.4 for _ in range(w * h)]
    for label, prec, fmt, bpp in [PRECISIONS[0], PRECISIONS[5]]:
        im, lay = new_image(w, h, prec)
        ch = ug.new_channel(im, "islands", bytes(255 if v else 0 for v in mask))
        before = random_pixels(rng, fmt, bpp, w * h)
        write(lay, fmt, before)
        before = read(lay, fmt)
        run("plug-in-uv-tools-fill-outside", im, [lay], islands_channel=ch, fill="transparent")
        after = read(lay, fmt)
        cs = bpp // 4
        ok = all((after[i * bpp:(i + 1) * bpp] == before[i * bpp:(i + 1) * bpp]) if mask[i]
                 else after[i * bpp + 3 * cs:(i + 1) * bpp] == b"\0" * cs for i in range(w * h))
        check("fill outside (%s): transparent outside, islands unchanged" % label, ok)
        run("plug-in-uv-tools-fill-outside", im, [lay], islands_channel=ch, fill="color",
            color=Gegl.Color.new("#ff0000"))
        after = read(lay, "R'G'B'A u8")
        before8 = read(lay, "R'G'B'A u8")
        ok = all(after[4 * i:4 * i + 4] == b"\xff\0\0\xff" for i in range(w * h) if not mask[i])
        check("fill outside (%s): colour outside" % label, ok)
        im.delete()
    # a data map in a linear image: #8080ff stored as 128/255, 128/255, 1
    im, lay = new_image(w, h, Gimp.Precision.FLOAT_LINEAR)
    ch = ug.new_channel(im, "islands", bytes(255 if v else 0 for v in mask))
    run("plug-in-uv-tools-fill-outside", im, [lay], islands_channel=ch, fill="color",
        color=Gegl.Color.new("#8080ff"), as_numbers=True)
    after = read(lay, "RGBA float")
    i = mask.index(False)
    v = struct.unpack("<4f", after[16 * i:16 * i + 16])
    check("fill outside as stored numbers: #8080ff is (0.502, 0.502, 1, 1) in a linear image",
          all(abs(a - b) < 1e-6 for a, b in zip(v, (128 / 255, 128 / 255, 1.0, 1.0))), v)
    run("plug-in-uv-tools-fill-outside", im, [lay], islands_channel=ch, fill="color",
        color=Gegl.Color.new("#8080ff"), as_numbers=False)
    v = struct.unpack("<4f", read(lay, "RGBA float")[16 * i:16 * i + 16])
    check("fill outside as a colour: #8080ff is converted (0.216, 0.216, 1) in a linear image",
          abs(v[0] - 0.2158605) < 1e-4, v)
    im.delete()


# ------------------------------------------------------------ pack
PACK_PRECISIONS = PRECISIONS + [("double linear", Gimp.Precision.DOUBLE_LINEAR, "RGBA double", 32),
                                ("u16 perceptual (other)", Gimp.Precision.U16_PERCEPTUAL,
                                 "R~G~B~A u16", 8)]


def t_pack():
    rng = random.Random(11)
    w, h = 21, 11
    for label, prec, fmt, bpp in PACK_PRECISIONS:
        im, base = new_image(w, h, prec)
        cs = bpp // 4
        srcs = []
        for name in ("occlusion", "roughness", "metallic", "opacity"):
            lay = Gimp.Layer.new(im, name, w, h, Gimp.ImageType.RGBA_IMAGE, 100.0,
                                 Gimp.LayerMode.NORMAL)
            im.insert_layer(lay, None, 0)
            grey = random_pixels(rng, fmt, bpp, w * h)
            # R = G = B, alpha 1
            px = bytearray()
            one = read(base, fmt, (0, 0, 1, 1))
            for i in range(w * h):
                v = grey[i * bpp:i * bpp + cs]
                px += v * 3 + one[3 * cs:]
            write(lay, fmt, bytes(px))
            srcs.append(lay)
        stored = [read(s, fmt) for s in srcs]
        packed = run("plug-in-uv-tools-pack-channels", im, [], red=srcs[0], green=srcs[1],
                     blue=srcs[2], alpha=srcs[3], name="ORM")[0]
        pk = read(packed, fmt)
        ok = all(pk[c * cs + j::bpp] == stored[c][j::bpp] for c in range(4) for j in range(cs))
        check("pack %s: R, G, B, A are the sources' stored numbers exactly" % label, ok)
        back = run("plug-in-uv-tools-unpack-channels", im, [packed], with_alpha=True)[0]
        outs = [l for l in im.get_layers() if l.get_name().startswith("ORM ")]
        names = sorted(l.get_name() for l in outs)
        ok = back == 4 and len(outs) == 4
        for lay in outs:
            c = ["red", "green", "blue", "alpha"].index(lay.get_name().split()[-1])
            u = read(lay, fmt)
            for dst in range(3):
                for j in range(cs):
                    ok = ok and u[dst * cs + j::bpp] == stored[c][j::bpp]
        check("unpack %s: grey layers equal to the packed channels exactly" % label, ok, names)
        rgb = run("plug-in-uv-tools-pack-channels", im, [], red=srcs[0], green=srcs[1],
                  blue=srcs[2])[0]
        check("pack %s without alpha: an RGB layer" % label, not rgb.has_alpha())
        im.delete()


for sec, fn in [("procedures", t_procedures), ("import", t_import), ("import", t_import_more),
                ("select", t_select), ("bleed", t_bleed), ("bleed", t_bleed_more), ("fill", t_fill), ("pack", t_pack),
                ("speed", t_bleed_speed)]:
    if section(sec):
        safe(fn)
print("UVT GIMP failures: %d" % fails[0], flush=True)
