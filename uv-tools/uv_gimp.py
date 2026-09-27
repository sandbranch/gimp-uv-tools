# UV Tools for GIMP 3: the work inside GIMP, without Gimp.main, so that
# the tests can run it in gimp-console. The procedures in uv-tools.py
# call these functions; so can other plug-ins (see docs/blender-link.md).
#
# Numbers, not colours: every pixel operation here moves the numbers a
# layer stores, as they are. GIMP stores an image in one precision (8,
# 16 or 32 bit integer, half or float) and one encoding (linear, or
# perceptual: sRGB-encoded). A buffer read in the format that matches
# the storage ("R'G'B'A u16" for 16-bit perceptual, "RGBA float" for
# float linear) gives the stored numbers without any conversion, and
# written back the same way it stores them unchanged. Where GEGL needs
# floats, the numbers are read as floats of the same encoding (exact for
# 8 and 16 bit) and relabelled (gegl:cast-format) as linear floats, so
# that no operation on the way converts them.
#
# Copyright 2026 David
# SPDX-License-Identifier: GPL-3.0-or-later

import json

import gi
gi.require_version("Gimp", "3.0")
gi.require_version("Gegl", "0.4")
from gi.repository import Gimp, Gegl  # noqa: E402

import uv_model as um  # noqa: E402
import uv_dilate as ud  # noqa: E402

# parasites marking what UV Tools made (renaming the items is fine)
P_OUTLINE = "uv-tools-outline"
P_ISLAND = "uv-tools-island"       # data: the island number (1..N)
P_EDGES = "uv-tools-edges"
P_CHANNEL = "uv-tools-islands"
P_IDS = "uv-tools-island-ids"
P_GUIDE = "uv-tools-guide"
P_LAYOUT = "uv-tools-layout"       # on the image: what was imported
MARKS = (P_OUTLINE, P_ISLAND, P_EDGES, P_CHANNEL, P_IDS, P_GUIDE)

NAMES = {
    "outline": "UV Islands",
    "island": "UV Island %d",
    "edges": "UV Edges",
    "channel": "UV Islands",
    "ids": "UV Island IDs",
    "guide": "UV Outline",
}
ROWS = 256
TILE = 128


class UVError(Exception):
    pass


def init_gegl():
    # a plug-in's GEGL is set up by libgimp when it touches a buffer;
    # nodes need the operations loaded, so make sure (harmless twice)
    Gegl.init(None)


def _parasite(name, data):
    """Saved in the XCF, and undoable: GIMP adds a "can't undo" step for
    a persistent parasite that is not undoable, and complains when that
    step is undone."""
    b = data if isinstance(data, bytes) else str(data).encode("utf-8")
    return Gimp.Parasite.new(name, Gimp.PARASITE_PERSISTENT | Gimp.PARASITE_UNDOABLE, list(b))


def mark(item, name, data=b"1"):
    item.attach_parasite(_parasite(name, data))


def marked(item, name):
    p = item.get_parasite(name)
    if p is None:
        return None
    return bytes(p.get_data()).decode("utf-8", "replace")


class undo_group:
    """One undo step for everything done inside."""

    def __init__(self, image):
        self.image = image

    def __enter__(self):
        self.image.undo_group_start()
        return self

    def __exit__(self, *exc):
        self.image.undo_group_end()
        Gimp.displays_flush()
        return False


# ---------------------------------------------------------------- formats

_TYPES = {"u8": "u8", "u16": "u16", "u32": "u32", "half": "half", "float": "float",
          "double": "double"}
_BYTES = {"u8": 1, "u16": 2, "u32": 4, "half": 2, "float": 4, "double": 8}


def storage(image):
    """(component type, encoding mark) of the image: e.g. ("u16", "'")
    for 16-bit perceptual (sRGB-encoded) and ("float", "") for float
    linear. The mark is "~" for GIMP's other perceptual encoding."""
    nick = image.get_precision().value_nick     # e.g. "u16-non-linear"
    t, _, enc = nick.partition("-")
    mark_ = {"linear": "", "non-linear": "'", "perceptual": "~"}[enc]
    return _TYPES[t], mark_


def numbers_format(image, drawable=None, alpha=True, grey=None):
    """The babl format of the stored numbers of the image's drawables."""
    t, m = storage(image)
    if grey is None:
        grey = drawable is not None and drawable.is_gray()
    model = ("Y%s" % m) if grey else ("R%sG%sB%s" % (m, m, m))
    return "%s%s %s" % (model, "A" if alpha else "", t)


def check_drawable(drawable):
    if drawable is None:
        raise UVError("no layer")
    if drawable.is_indexed():
        raise UVError("indexed images are not supported (Image > Mode > RGB)")


# ------------------------------------------------------------ the layout

def remove_marked(image):
    """Removes what an earlier import made."""
    for p in image.get_paths():
        if any(marked(p, n) is not None for n in MARKS):
            image.remove_path(p)
    for c in image.get_channels():
        if marked(c, P_CHANNEL) is not None:
            image.remove_channel(c)
    for lay in image.get_layers():
        if marked(lay, P_IDS) is not None or marked(lay, P_GUIDE) is not None:
            image.remove_layer(lay)


def import_layout(image, path, flip_v=False, material="", tile=1001, outline=True,
                  island_paths=True, edges=False, channel=True, ids_layer=False,
                  guide_layer=False, guide_color=None, replace=True):
    """Imports a UV layout file into the image, scaled to its size (the
    layout's 0..1 is the whole image). Returns a dict: islands, faces,
    boundary_edges, outline (Gimp.Path or None), channel, island_paths
    (list), edges, ids_layer, guide_layer."""
    layout = um.read_layout(path, material, tile)
    if flip_v:
        layout = layout.flipped()
    islands = um.find_islands(layout)
    w, h = image.get_width(), image.get_height()
    res = {"islands": len(islands.islands), "faces": len(islands.faces),
           "boundary_edges": islands.boundary_edges, "outline": None, "channel": None,
           "island_paths": [], "edges": None, "ids_layer": None, "guide_layer": None,
           "source": layout.source, "aspect": layout.aspect}
    with undo_group(image):
        if replace:
            remove_marked(image)
        want = []
        if outline or guide_layer:
            want.append(("outline", NAMES["outline"],
                         um.path_d([lp for isl in islands.islands for lp in isl.loops], w, h)))
        if island_paths:
            for n, isl in enumerate(islands.islands):
                want.append(("island", NAMES["island"] % (n + 1), um.path_d(isl.loops, w, h)))
        if edges:
            want.append(("edges", NAMES["edges"],
                         um.path_d(um.chain_edges(islands.edges), w, h, close=False)))
        want = [x for x in want if x[2]]
        if want:
            svg = um.paths_svg([(name, d) for (_k, name, d) in want], w, h)
            ok, paths = image.import_paths_from_string(svg, -1, False, False)
            if not ok or len(paths) != len(want):
                raise UVError("GIMP could not import the island paths")
            n_island = 0
            for (kind, name, d), p in zip(want, paths):
                if kind == "outline":
                    mark(p, P_OUTLINE)
                    p.set_visible(bool(outline))
                    res["outline"] = p
                elif kind == "island":
                    n_island += 1
                    mark(p, P_ISLAND, str(n_island))
                    p.set_visible(False)
                    res["island_paths"].append(p)
                else:
                    mark(p, P_EDGES)
                    p.set_visible(False)
                    res["edges"] = p
        if channel:
            res["channel"] = new_channel(image, NAMES["channel"], um.mask(islands, w, h))
            mark(res["channel"], P_CHANNEL)
        if ids_layer:
            res["ids_layer"] = _ids_layer(image, islands, w, h)
        if guide_layer:
            res["guide_layer"] = _guide_layer(image, res["outline"], guide_color)
            if not outline and res["outline"] is not None:
                image.remove_path(res["outline"])
                res["outline"] = None
        info = {"version": 1, "file": path, "flip_v": bool(flip_v), "material": material,
                "tile": tile, "islands": res["islands"], "width": w, "height": h}
        image.attach_parasite(_parasite(P_LAYOUT, json.dumps(info)))
    return res


def new_channel(image, name, mask_bytes, visible=False, position=0):
    """A channel from an 8-bit mask (bytes, rows from the top)."""
    w, h = image.get_width(), image.get_height()
    col = Gegl.Color.new("black")
    ch = Gimp.Channel.new(image, name, w, h, 50.0, col)
    image.insert_channel(ch, None, position)
    ch.set_visible(visible)
    buf = ch.get_buffer()
    for y0 in range(0, h, ROWS):
        n = min(ROWS, h - y0)
        buf.set(Gegl.Rectangle.new(0, y0, w, n), "Y u8", bytes(mask_bytes[y0 * w:(y0 + n) * w]))
    buf.flush()
    ch.update(0, 0, w, h)
    return ch


def id_color(n):
    """A colour (r, g, b) bytes for island number n (1..): different
    for every island (an odd multiplier is one to one below 2^24) and
    far apart for neighbouring numbers."""
    v = (n * 2654435761) & 0xFFFFFF
    return bytes(((v >> 16) & 255, (v >> 8) & 255, v & 255))


def _ids_layer(image, islands, w, h):
    lay = Gimp.Layer.new(image, NAMES["ids"], w, h, Gimp.ImageType.RGBA_IMAGE, 100.0,
                         Gimp.LayerMode.NORMAL)
    image.insert_layer(lay, None, 0)
    lay.set_visible(False)
    mark(lay, P_IDS)
    rows = [bytearray(4 * w) for _ in range(h)]
    for n, isl in enumerate(islands.islands):
        px = id_color(n + 1) + b"\xff"
        for (r, c0, c1) in um.island_spans(isl, islands, w, h):
            rows[r][4 * c0:4 * c1] = px * (c1 - c0)
    buf = lay.get_buffer()
    for y0 in range(0, h, ROWS):
        n = min(ROWS, h - y0)
        buf.set(Gegl.Rectangle.new(0, y0, w, n), "R'G'B'A u8", b"".join(rows[y0:y0 + n]))
    buf.flush()
    lay.update(0, 0, w, h)
    return lay


def _guide_layer(image, path, color):
    w, h = image.get_width(), image.get_height()
    lay = Gimp.Layer.new(image, NAMES["guide"], w, h, Gimp.ImageType.RGBA_IMAGE, 100.0,
                         Gimp.LayerMode.NORMAL)
    image.insert_layer(lay, None, 0)
    lay.fill(Gimp.FillType.TRANSPARENT)
    mark(lay, P_GUIDE)
    if path is not None:
        Gimp.context_push()
        try:
            Gimp.context_set_foreground(color or Gegl.Color.new("#ff00ff"))
            Gimp.context_set_stroke_method(Gimp.StrokeMethod.LINE)
            Gimp.context_set_line_width(1.0)
            Gimp.context_set_antialias(True)
            lay.edit_stroke_item(path)
        finally:
            Gimp.context_pop()
    return lay


# ---------------------------------------------------------- the islands

def island_paths(image):
    """{number: path} of the island paths UV Tools made."""
    out = {}
    for p in image.get_paths():
        v = marked(p, P_ISLAND)
        if v is not None and v.isdigit():
            out[int(v)] = p
    return out


def islands_channel(image):
    for c in image.get_channels():
        if marked(c, P_CHANNEL) is not None:
            return c
    return None


def path_loops(path):
    """The closed strokes of a path as loops of (x, y) (anchors)."""
    loops = []
    for s in path.get_strokes():
        _t, pts, closed = path.stroke_get_points(s)
        anchors = [(pts[i], pts[i + 1]) for i in range(2, len(pts) - 1, 6)]
        if len(anchors) >= 3:
            loops.append(anchors)
    return loops


def _loop_islands(image, numbers=None):
    """um.Islands made from the island paths (their pixel coordinates
    divided by the image size): (Islands, [numbers])."""
    paths = island_paths(image)
    if not paths:
        raise UVError("this image has no UV island paths: use Import UV Layout first")
    nums = sorted(paths) if numbers is None else [n for n in sorted(paths) if n in numbers]
    w, h = float(image.get_width()), float(image.get_height())
    loops = [[[(x / w, y / h) for (x, y) in lp] for lp in path_loops(paths[n])] for n in nums]
    lay = um.Layout([], loops, "paths")
    return um.find_islands(lay), nums


def selection_mask(image, rect=None):
    """The selection as bytes (Y u8) over rect (x, y, w, h), image
    coordinates."""
    x, y, w, h = rect or (0, 0, image.get_width(), image.get_height())
    sel = image.get_selection()
    buf = sel.get_buffer()
    return bytes(buf.get(Gegl.Rectangle.new(x, y, w, h), 1.0, "Y u8", Gegl.AbyssPolicy.NONE))


def select_islands(image, mode="touching", number=1, operation="replace"):
    """Selects UV islands. mode: "all"; "touching": the islands that
    have a pixel in the current selection; "centre": the island under
    the centre of the current selection; "number": island number
    (1..N, as in the path names). operation: replace, add, subtract,
    intersect. Returns the island numbers selected."""
    w, h = image.get_width(), image.get_height()
    ops = {"replace": Gimp.ChannelOps.REPLACE, "add": Gimp.ChannelOps.ADD,
           "subtract": Gimp.ChannelOps.SUBTRACT, "intersect": Gimp.ChannelOps.INTERSECT}
    if operation not in ops:
        raise UVError("unknown operation %r" % operation)
    isl, nums = _loop_islands(image)
    chosen = []
    if mode == "all":
        chosen = list(range(len(nums)))
    elif mode == "number":
        if number not in nums:
            raise UVError("there is no island %d (islands 1 to %d)" % (number, max(nums)))
        chosen = [nums.index(number)]
    elif mode in ("touching", "centre"):
        ok, non_empty, x1, y1, x2, y2 = Gimp.Selection.bounds(image)
        if not non_empty:
            raise UVError("select a part of the island(s) first, e.g. a small rectangle")
        if mode == "centre":
            cx, cy = (x1 + x2) / 2.0, (y1 + y2) / 2.0
            for k, i in enumerate(isl.islands):
                if um.point_in_island(i, isl, cx / w, cy / h):
                    chosen = [k]
            if not chosen:
                raise UVError("there is no island at the centre of the selection (%g, %g)"
                              % (cx, cy))
        else:
            sel = selection_mask(image, (x1, y1, x2 - x1, y2 - y1))
            sw = x2 - x1
            for k, i in enumerate(isl.islands):
                bx0, by0, bx1, by1 = i.bbox
                if bx1 * w < x1 or bx0 * w > x2 or by1 * h < y1 or by0 * h > y2:
                    continue
                for (r, c0, c1) in um.island_spans(i, isl, w, h):
                    if r < y1 or r >= y2:
                        continue
                    a, b = max(c0, x1), min(c1, x2)
                    if a < b:
                        row = sel[(r - y1) * sw + a - x1:(r - y1) * sw + b - x1]
                        if max(row) > 127:
                            chosen.append(k)
                            break
            if not chosen:
                raise UVError("the selection touches no island")
    else:
        raise UVError("unknown mode %r" % mode)
    m = um.mask(isl, w, h, which=set(chosen))
    with undo_group(image):
        tmp = new_channel(image, "UV Tools selection", m)
        image.select_item(ops[operation], tmp)
        image.remove_channel(tmp)
    return [nums[k] for k in chosen]


# -------------------------------------------------- islands as a mask

def mask_channel(image, drawable, islands_from="channel", channel=None):
    """A binary channel of the islands and whether it is a temporary
    one (to be removed): from the islands channel (or the given one),
    the selection, or the drawable's alpha (above half)."""
    if islands_from == "channel":
        ch = channel or islands_channel(image)
        if ch is None:
            raise UVError("no UV Islands channel: use Import UV Layout first, "
                          "or take the islands from the selection or the alpha")
        return ch, False
    if islands_from == "selection":
        if Gimp.Selection.is_empty(image):
            raise UVError("the selection is empty")
        return Gimp.Selection.save(image), True
    if islands_from == "alpha":
        if not drawable.has_alpha():
            raise UVError("the layer has no alpha channel")
        w, h = image.get_width(), image.get_height()
        # alpha above one half (128 of 255) is island
        half = bytes(255 if v > 127 else 0 for v in range(256))
        m = bytearray(w * h)
        for y0 in range(0, h, ROWS):
            n = min(ROWS, h - y0)
            ya = _read(drawable, "YA u8", 0, y0, w, n)
            m[y0 * w:(y0 + n) * w] = ya[1::2].translate(half)
        ch = new_channel(image, "UV Tools alpha", m)
        return ch, True
    raise UVError("unknown islands source %r" % islands_from)


def _mask_rows(ch, x, y, w, h):
    buf = ch.get_buffer()
    return bytes(buf.get(Gegl.Rectangle.new(x, y, w, h), 1.0, "Y u8", Gegl.AbyssPolicy.NONE))


def work_rects(mask, w, h, n, tile=TILE, strip=4):
    """The rectangles (x, y, w, h) that hold pixels outside the islands
    within n pixels of an island, from an 8-bit mask (bytes, above 127
    is island): tiles with a pixel outside the islands and an island
    pixel in the tile or its neighbours, merged into strips."""
    tw, th = (w + tile - 1) // tile, (h + tile - 1) // tile
    has_isl = [[False] * tw for _ in range(th)]
    has_out = [[False] * tw for _ in range(th)]
    hi = bytes(range(128, 256))
    lo = bytes(range(0, 128))
    for ty in range(th):
        for y in range(ty * tile, min(h, (ty + 1) * tile)):
            row = mask[y * w:(y + 1) * w]
            for tx in range(tw):
                seg = row[tx * tile:(tx + 1) * tile]
                if not has_isl[ty][tx] and seg.translate(None, lo):
                    has_isl[ty][tx] = True
                if not has_out[ty][tx] and seg.translate(None, hi):
                    has_out[ty][tx] = True
    reach = max(1, (n + tile - 1) // tile)
    need = [[False] * tw for _ in range(th)]
    count = 0
    for ty in range(th):
        for tx in range(tw):
            if has_out[ty][tx]:
                for yy in range(max(0, ty - reach), min(th, ty + reach + 1)):
                    if any(has_isl[yy][max(0, tx - reach):tx + reach + 1]):
                        need[ty][tx] = True
                        count += 1
                        break
    if count == 0:
        return []
    # each graph has a fixed cost (about 30 ms) and works on its
    # rectangle's margins again, so fewer, bigger rectangles are faster:
    # strips of `strip` tile rows, each from its first to its last tile
    # needing work; the whole image when most of it needs work
    if count > 0.6 * tw * th:
        return [(0, 0, w, h)]
    rects = []
    for ty0 in range(0, th, strip):
        cols = [tx for tx in range(tw) for ty in range(ty0, min(th, ty0 + strip))
                if need[ty][tx]]
        if not cols:
            continue
        x0, x1 = min(cols) * tile, min(w, (max(cols) + 1) * tile)
        y0, y1 = ty0 * tile, min(h, (ty0 + strip) * tile)
        rects.append((x0, y0, x1 - x0, y1 - y0))
    return rects


def _float_formats(image, drawable):
    """(read format, cast-as format): the drawable's numbers as floats
    of their own encoding, and the same bytes labelled linear."""
    t, m = storage(image)
    if drawable.is_channel() or drawable.is_layer_mask():
        # channels and masks hold linear numbers whatever the image's
        m = ""
    enc = "R%sG%sB%sA float" % (m, m, m)
    return enc, "RGBA float"


def _bytes_in_layer(full, iw, ih, ox, oy, w, h):
    """An image-sized 8-bit mask cut to a drawable's rectangle."""
    out = bytearray(w * h)
    a, b = max(0, ox), min(iw, ox + w)
    if a < b:
        for y in range(h):
            sy = y + oy
            if 0 <= sy < ih:
                out[y * w + a - ox:y * w + b - ox] = full[sy * iw + a:sy * iw + b]
    return out


def _y8(data, w, h):
    buf = Gegl.Buffer.new("Y u8", 0, 0, w, h)
    for y0 in range(0, h, ROWS):
        k = min(ROWS, h - y0)
        buf.set(Gegl.Rectangle.new(0, y0, w, k), "Y u8", bytes(data[y0 * w:(y0 + k) * w]))
    return buf


def _replace_item(image, old, new):
    """Puts new (a copy of old) in old's place, with its name and
    tattoo, and selected if old was."""
    parent = old.get_parent()
    pos = image.get_item_position(old)
    name, tattoo = old.get_name(), old.get_tattoo()
    if old.is_layer():
        selected = image.get_selected_layers()
        image.insert_layer(new, parent, pos)
        image.remove_layer(old)
    else:
        selected = image.get_selected_channels()
        image.insert_channel(new, parent, pos)
        image.remove_channel(old)
    new.set_name(name)
    new.set_tattoo(tattoo)
    sel = [new if s == old else s for s in selected]
    if sel != selected:
        if new.is_layer():
            image.set_selected_layers(sel)
        else:
            image.set_selected_channels(sel)
    return new


def _copy_rois(image, drawable, result, enc, out, rois):
    """Copies the result (stored numbers as RGBA floats in the format
    enc) into the buffer out of drawable, over rois. For a grey
    drawable (grey layer, channel, mask) R and A are taken as they are:
    converting R = G = B to grey would weigh and add them, which rounds
    floats."""
    if not (drawable.is_gray() or drawable.is_channel() or drawable.is_layer_mask()):
        gr = ud.Graph()
        src = gr.source(result)
        for r in rois:
            ud.blit(src, out, r)
        return
    t, m = storage(image)
    alpha = drawable.is_layer() and drawable.has_alpha()
    # (enc says which encoding the numbers are in)
    m = "'" if "'" in enc else "~" if "~" in enc else ""
    grey = "Y%s%s float" % (m, "A" if alpha else "")
    for r in rois:
        for y0 in range(r.y, r.y + r.height, ROWS):
            n = min(ROWS, r.y + r.height - y0)
            rect = Gegl.Rectangle.new(r.x, y0, r.width, n)
            data = bytes(result.get(rect, 1.0, enc, Gegl.AbyssPolicy.NONE))
            k = 8 if alpha else 4
            o = bytearray(r.width * n * k)
            for j in range(4):
                o[j::k] = data[j::16]
                if alpha:
                    o[4 + j::k] = data[12 + j::16]
            out.set(rect, grey, bytes(o))


def check_paintable(drawable):
    check_drawable(drawable)
    if drawable.is_group():
        raise UVError("a layer group has no pixels of its own: choose a layer")
    for kind in ("TextLayer", "LinkLayer", "VectorLayer"):
        cls = getattr(Gimp, kind, None)
        if cls is not None and isinstance(drawable, cls):
            raise UVError("this is a %s layer: Layer > Rasterize it first"
                          % kind[:-5].lower())
    if drawable.get_lock_content():
        raise UVError("the layer's pixels are locked")


def dilate(image, drawable, radius, islands_from="channel", channel=None):
    """Seam bleed: moves the stored numbers of the nearest island pixel
    into every pixel outside the islands within radius pixels (see
    uv_dilate); island pixels and pixels farther away stay exactly as
    they are. With islands from the channel or the alpha, an existing
    selection (more than half selected) limits where pixels change.

    One undo step. The result is exact: GIMP's merge of a plug-in's
    shadow buffer composites it (colour times alpha over alpha, old +
    (new - old) * mask, through linear light for perceptual float),
    which rounds floats and some 16-bit values under low alpha, so the
    result is written into a copy of the layer or channel (name, mask,
    mode, opacity, parasites and tattoo kept) that takes its place. A
    layer mask, which cannot be replaced, is merged as usual (exact at
    8 and 16 bit). Returns (drawable, pixels in the areas worked on,
    rects): use the returned drawable, it is a new object.""" 
    check_paintable(drawable)
    ud.check_radius(radius)
    init_gegl()
    w, h = drawable.get_width(), drawable.get_height()
    ok, ox, oy = drawable.get_offsets()
    iw, ih = image.get_width(), image.get_height()
    with undo_group(image):
        ch, temp = mask_channel(image, drawable, islands_from, channel)
        try:
            mrows = _bytes_in_layer(_mask_rows(ch, 0, 0, iw, ih), iw, ih, ox, oy, w, h)
        finally:
            if temp:
                image.remove_channel(ch)
        rects = work_rects(mrows, w, h, int(radius))
        if not rects:
            return drawable, 0, []
        allowed = None
        if islands_from != "selection" and not Gimp.Selection.is_empty(image):
            allowed = _y8(_bytes_in_layer(selection_mask(image), iw, ih, ox, oy, w, h), w, h)
        enc, cast = _float_formats(image, drawable)
        rois = [Gegl.Rectangle.new(*r) for r in rects]
        # the numbers, once, into a buffer of this process (reading the
        # layer's tiles from GIMP again for every graph is slow)
        local = Gegl.Buffer.new(cast, 0, 0, w, h)
        gr = ud.Graph()
        num = gr.op("gegl:convert-format", gr.source(drawable.get_buffer()), format=ud.fmt(enc))
        num = gr.op("gegl:cast-format", num, input_format=ud.fmt(enc), output_format=ud.fmt(cast))
        for r in rois:
            ud.blit(num, local, r)
        result = Gegl.Buffer.new(enc, 0, 0, w, h)
        ud.dilate(gr.source(local), gr.source(_y8(mrows, w, h)), int(radius), w, h, result,
                  cast_to=enc, rois=rois, allowed=allowed)
        if drawable.is_layer_mask():
            # a mask belongs to its layer: merged the usual way (exact at
            # 8 and 16 bit; GIMP's merge rounds floats)
            shadow = drawable.get_shadow_buffer()
            ud.blit(gr.source(drawable.get_buffer()), shadow, Gegl.Rectangle.new(0, 0, w, h))
            _copy_rois(image, drawable, result, enc, shadow, rois)
            shadow.flush()
            drawable.merge_shadow(True)
            drawable.update(0, 0, w, h)
        else:
            target = drawable.copy()
            out = target.get_buffer()
            _copy_rois(image, drawable, result, enc, out, rois)
            out.flush()
            drawable = _replace_item(image, drawable, target)
            drawable.update(0, 0, w, h)
    return drawable, sum(r[2] * r[3] for r in rects), rects


def srgb_encode(v):
    """Linear light to the sRGB-encoded number."""
    if v <= 0.0031308:
        return 12.92 * v
    return 1.055 * v ** (1 / 2.4) - 0.055


def numbers_color(image, color):
    """A GeglColor that GIMP stores in this image as the colour's own
    sRGB-encoded components (what a colour dialog or #rrggbb shows):
    for a linear image the components are given as linear values, so
    that nothing converts them."""
    t, m = storage(image)
    if m:
        return color
    r, g, b, a = color.get_rgba()
    c = Gegl.Color.new("black")
    c.set_rgba(srgb_encode(r), srgb_encode(g), srgb_encode(b), a)
    return c


def fill_outside(image, drawable, islands_from="channel", channel=None, fill="color",
                 color=None, as_numbers=False):
    """Fills everything outside the islands with a colour, or clears it
    (transparent). as_numbers: the colour's components are the numbers
    to store (for data maps: #8080ff stays 0.502, 0.502, 1 in a linear
    image), not a colour to convert into the image's encoding. With
    islands from the channel or the alpha, an existing selection limits
    the fill. One undo step."""
    check_drawable(drawable)
    with undo_group(image):
        ch, temp = mask_channel(image, drawable, islands_from, channel)
        saved = None if Gimp.Selection.is_empty(image) else Gimp.Selection.save(image)
        try:
            image.select_item(Gimp.ChannelOps.REPLACE, ch)
            Gimp.Selection.invert(image)
            if islands_from != "selection" and saved is not None:
                image.select_item(Gimp.ChannelOps.INTERSECT, saved)
            if fill == "transparent":
                if not drawable.has_alpha():
                    drawable.add_alpha()
                drawable.edit_clear()
            else:
                col = color or Gegl.Color.new("black")
                if as_numbers:
                    col = numbers_color(image, col)
                Gimp.context_push()
                try:
                    Gimp.context_set_foreground(col)
                    drawable.edit_fill(Gimp.FillType.FOREGROUND)
                finally:
                    Gimp.context_pop()
        finally:
            if saved is not None:
                image.select_item(Gimp.ChannelOps.REPLACE, saved)
                image.remove_channel(saved)
            else:
                Gimp.Selection.none(image)
            if temp:
                image.remove_channel(ch)


# -------------------------------------------------- channel packing

def _read(drawable, fmt, x, y, w, h):
    """Stored numbers of drawable over the image rectangle (outside the
    layer: zeros)."""
    ok, ox, oy = drawable.get_offsets()
    return bytes(drawable.get_buffer().get(Gegl.Rectangle.new(x - ox, y - oy, w, h), 1.0, fmt,
                                           Gegl.AbyssPolicy.NONE))


def pack(image, red, green, blue, alpha=None, name="Packed", channel="red"):
    """A new layer whose red, green and blue (and alpha) are the stored
    numbers of one channel (red, by default: grey layers have R = G = B)
    of each source layer, at the image's own precision: nothing is
    converted. Returns the new layer (above the topmost source)."""
    if not image.get_base_type() == Gimp.ImageBaseType.RGB:
        raise UVError("channel packing needs an RGB image (Image > Mode > RGB)")
    srcs = [red, green, blue] + ([alpha] if alpha is not None else [])
    if any(s is None for s in srcs[:3]):
        raise UVError("choose a layer for red, green and blue")
    t, m = storage(image)
    cs = _BYTES[t]
    fmt4 = numbers_format(image, alpha=True, grey=False)
    k = {"red": 0, "green": 1, "blue": 2, "alpha": 3}[channel]
    w, h = image.get_width(), image.get_height()
    nout = 4 if alpha is not None else 3
    fmt_out = numbers_format(image, alpha=alpha is not None, grey=False)
    with undo_group(image):
        typ = Gimp.ImageType.RGBA_IMAGE if alpha is not None else Gimp.ImageType.RGB_IMAGE
        lay = Gimp.Layer.new(image, name, w, h, typ, 100.0, Gimp.LayerMode.NORMAL)
        tops = [image.get_item_position(s) for s in srcs if s.get_parent() is None]
        image.insert_layer(lay, None, min(tops) if tops else 0)
        buf = lay.get_buffer()
        for y0 in range(0, h, ROWS):
            n = min(ROWS, h - y0)
            out = bytearray(w * n * nout * cs)
            for c, s in enumerate(srcs):
                data = _read(s, fmt4, 0, y0, w, n)
                for j in range(cs):
                    out[c * cs + j::nout * cs] = data[k * cs + j::4 * cs]
            buf.set(Gegl.Rectangle.new(0, y0, w, n), fmt_out, bytes(out))
        buf.flush()
        lay.update(0, 0, w, h)
    return lay


def unpack(image, drawable, with_alpha=True):
    """New grey layers (R = G = B, opaque) from the red, green, blue
    (and alpha) numbers of the drawable, at the image's precision.
    Returns them, red first; they go above the drawable."""
    check_drawable(drawable)
    if drawable.is_gray():
        raise UVError("the layer is grey already")
    t, m = storage(image)
    cs = _BYTES[t]
    fmt4 = numbers_format(image, alpha=True, grey=False)
    fmt3 = numbers_format(image, alpha=False, grey=False)
    w, h = image.get_width(), image.get_height()
    names = ["red", "green", "blue"] + (["alpha"] if with_alpha and drawable.has_alpha() else [])
    out = []
    with undo_group(image):
        parent = drawable.get_parent()
        pos = image.get_item_position(drawable)
        for c, nm in enumerate(names):
            lay = Gimp.Layer.new(image, "%s %s" % (drawable.get_name(), nm), w, h,
                                 Gimp.ImageType.RGB_IMAGE, 100.0, Gimp.LayerMode.NORMAL)
            image.insert_layer(lay, parent, pos + c)
            out.append(lay)
        bufs = [lay.get_buffer() for lay in out]
        for y0 in range(0, h, ROWS):
            n = min(ROWS, h - y0)
            data = _read(drawable, fmt4, 0, y0, w, n)
            for c, b in enumerate(bufs):
                o = bytearray(w * n * 3 * cs)
                for dst in range(3):
                    for j in range(cs):
                        o[dst * cs + j::3 * cs] = data[c * cs + j::4 * cs]
                b.set(Gegl.Rectangle.new(0, y0, w, n), fmt3, bytes(o))
        for lay, b in zip(out, bufs):
            b.flush()
            lay.update(0, 0, w, h)
    return out
