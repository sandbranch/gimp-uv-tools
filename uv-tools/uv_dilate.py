# UV Tools for GIMP 3: seam bleed (dilation) as GEGL graphs.
#
# Every pixel outside the islands and within N pixels (Euclidean
# distance of pixel centres) of an island pixel gets the exact stored
# value of its nearest island pixel; island pixels and pixels farther
# than N keep theirs. A plug-in cannot count on numpy in GIMP's Python, so
# the work is done by GEGL operations (threaded C code) and Python only
# builds the graphs. The method is explained in docs/dilation.md.
#
# Copyright 2026 David
# SPDX-License-Identifier: GPL-3.0-or-later

import struct

import gi
gi.require_version("Gegl", "0.4")
from gi.repository import Gegl  # noqa: E402

# Keys are integers held exactly in float32, all below 2**23.
M = 256            # one step of squared distance; below it, an index 0..2N
MAX_RADIUS = 100   # (2 N^2 + 2) * M stays below 2**23
MAGIC = 12582912.0  # 1.5 * 2**23: x + MAGIC - MAGIC rounds x to an integer
FMT = "RGB float"  # three candidates at a time, one per channel
ROWS = 256         # rows per piece when Python fills a buffer


def check_radius(n):
    if not 1 <= int(n) <= MAX_RADIUS:
        raise ValueError("the radius must be 1 to %d pixels" % MAX_RADIUS)


def key(d, n):
    """Key of a candidate at offset d on one axis: d^2, then d."""
    return d * d * M + d + n


class Graph:
    """A GEGL graph with helpers for the operations used here."""

    def __init__(self):
        self.g = Gegl.Node()

    def node(self, op, **props):
        nd = self.g.create_child(op)
        for k, v in props.items():
            nd.set_property(k.replace("_", "-"), v)
        return nd

    def op(self, op, inp, aux=None, **props):
        nd = self.node(op, **props)
        inp.connect_to("output", nd, "input")
        if aux is not None:
            aux.connect_to("output", nd, "aux")
        return nd

    def source(self, buf):
        return self.node("gegl:buffer-source", buffer=buf)

    def color(self, r, g, b, a=1.0):
        c = Gegl.Color.new("black")
        c.set_rgba(float(r), float(g), float(b), float(a))
        return self.node("gegl:color", value=c)

    def add_rgb(self, inp, r, g, b):
        """Adds a constant per channel (alpha stays)."""
        return self.op("gegl:add", inp, self.color(r, g, b))

    def mul_rgb(self, inp, r, g, b):
        """Multiplies by a constant per channel (alpha stays)."""
        return self.op("gegl:multiply", inp, self.color(r, g, b))

    def matrix(self, inp, rows):
        """feColorMatrix, unclamped in GEGL: four rows of five numbers
        (R, G, B, A, 1). Its output is premultiplied: keep alpha 1."""
        vals = " ".join(repr(float(v)) for row in rows for v in row)
        return self.op("gegl:svg-matrix", inp, values=vals)

    def translate(self, inp, x, y):
        if x == 0 and y == 0:
            return inp
        return self.op("gegl:translate", inp, x=float(x), y=float(y),
                       sampler=Gegl.SamplerType.NEAREST)

    def minimum(self, a, b):
        """Per channel min of integer floats: a - max(a - b, 0)."""
        d = self.op("gegl:subtract", a, b)
        c = self.op("gegl:rgb-clip", d, clip_low=True, low_limit=0.0, clip_high=False)
        return self.op("gegl:subtract", a, c)

    def floor_div(self, inp):
        """From a key in R, G and B: R = floor(key / M), G = B = key
        (keys hold an index below M under the multiple of M). Only R
        is rounded: G must keep the key."""
        s = self.mul_rgb(inp, 1.0 / M, 1, 1)
        s = self.add_rgb(s, -(M - 1) / (2.0 * M), 0, 0)
        s = self.add_rgb(s, MAGIC, 0, 0)
        return self.add_rgb(s, -MAGIC, 0, 0)


def fmt(name):
    """A Babl format, as GEGL's format properties take it."""
    return Gegl.format(name)


def blit(node, buf, rect):
    node.blit_buffer(buf, rect, 0, Gegl.AbyssPolicy.NONE)


def rect(x, y, w, h):
    return Gegl.Rectangle.new(x, y, w, h)


def filled(x, y, w, h, value, fmt_name=FMT):
    """A buffer of the given extent with every channel set to value,
    exactly (written as bytes, not through a colour)."""
    ch = 3 if fmt_name == FMT else 1
    buf = Gegl.Buffer.new(fmt_name, x, y, w, h)
    row = struct.pack("<f", value) * (ch * w)
    for y0 in range(y, y + h, ROWS):
        k = min(ROWS, y + h - y0)
        buf.set(rect(x, y0, w, k), fmt_name, row * k)
    return buf


def fetch(gr, inp, off, n):
    """inp's pixel at (x + off.R, y + off.G), exactly: map-relative with
    the nearest sampler samples at x + 0.5 + dx, y + 0.5 + dy. It reads
    its aux raw as two floats per pixel, so the offsets go into a YA
    float buffer, Y = dx (with R = G = B = dx) and alpha = dy + K with
    K = n + 1, above 0 everywhere (babl changes the colour of pixels
    whose alpha is 0 or less); inp is moved down by K to make up for
    it. svg-matrix output is premultiplied, so it cannot set alpha;
    gegl:opacity multiplies premultiplied RGBA by its aux, which leaves
    R = G = B = dx and makes alpha dy + K."""
    k = n + 1
    xs = gr.matrix(off, [[1, 0, 0, 0, 0], [1, 0, 0, 0, 0], [1, 0, 0, 0, 0], [0, 0, 0, 0, 1]])
    ys = gr.matrix(off, [[0, 1, 0, 0, k], [0, 1, 0, 0, k], [0, 1, 0, 0, k], [0, 0, 0, 0, 1]])
    q = gr.op("gegl:opacity", xs, ys, value=1.0)
    aux = gr.op("gegl:convert-format", q, format=fmt("YA float"))
    return gr.op("gegl:map-relative", gr.translate(inp, 0, k), aux, scaling=1.0,
                 sampler_type=Gegl.SamplerType.NEAREST)


def packed(src_buf, axis, roi):
    """From a padded buffer K (keys in R, G and B), a buffer with R = K(p),
    G = K(p + 1), B = K(p + 2) along the axis (0: x, 1: y), so that the
    three channels of one translated copy are three neighbouring
    candidates. roi: where it is needed."""
    gr = Graph()
    s = gr.source(src_buf)
    parts = []
    for c in range(3):
        t = gr.translate(s, -c if axis == 0 else 0, -c if axis == 1 else 0)
        # K is in all three channels: keep channel c
        parts.append(gr.mul_rgb(t, *[1 if i == c else 0 for i in range(3)]))
    p = gr.op("gegl:add", gr.op("gegl:add", parts[0], parts[1]), parts[2])
    p = gr.op("gegl:convert-format", p, format=fmt(FMT))
    out = Gegl.Buffer.new(FMT, roi.x, roi.y, roi.width, roi.height)
    blit(p, out, roi)
    return out


def min_pass(src_buf, n, axis, roi):
    """For each pixel of roi, the minimum over the offsets d in -n..n
    along the axis of K(p + d) + key(d), where src_buf holds K (in R, G, B)
    and covers roi grown by n + 2 on that axis. Returns an RGB float
    buffer (roi) with the minimum in R, G and B."""
    grow = n + 2
    need = rect(roi.x - (grow if axis == 0 else 0), roi.y - (grow if axis == 1 else 0),
                roi.width + (2 * grow if axis == 0 else 0),
                roi.height + (2 * grow if axis == 1 else 0))
    pk = packed(src_buf, axis, need)
    big = float(2 * (n * n + 1) * M)
    gr = Graph()
    s = gr.source(pk)
    run = None
    for base in range(-n, n + 1, 3):
        t = gr.translate(s, -base if axis == 0 else 0, -base if axis == 1 else 0)
        ks = [key(base + c, n) if base + c <= n else big for c in range(3)]
        cand = gr.add_rgb(t, *ks)
        run = cand if run is None else gr.minimum(run, cand)
    # the minimum of the three channels, into all three
    # (min(R, G), min(G, B), min(B, R)), then that with its channels
    # turned: min(R, G, B) in all three
    turn = [[0, 1, 0, 0, 0], [0, 0, 1, 0, 0], [1, 0, 0, 0, 0], [0, 0, 0, 0, 1]]
    m = gr.minimum(run, gr.matrix(run, turn))
    m = gr.minimum(m, gr.matrix(m, turn))
    m = gr.op("gegl:convert-format", m, format=fmt(FMT))
    out = Gegl.Buffer.new(FMT, roi.x, roi.y, roi.width, roi.height)
    blit(m, out, roi)
    return out


def island_keys(island_node, n, w, h, roi):
    """Padded buffer: 0 on island pixels, BIG elsewhere and around the
    image, over roi grown by n + 4. island_node: a node with the island
    mask in R (0..1; above 0.5 is island)."""
    pad = n + 4
    big = float((n * n + 1) * M)
    area = rect(roi.x - pad, roi.y - pad, roi.width + 2 * pad, roi.height + 2 * pad)
    buf = filled(area.x, area.y, area.width, area.height, big)
    gr = Graph()
    # the mask is in all three channels (or copied there from Y):
    # m = 1 above 0.5, else 0; k = big * (1 - m)
    m = gr.op("gegl:add", island_node, value=-0.5)
    m = gr.op("gegl:multiply", m, value=16777216.0)
    m = gr.op("gegl:rgb-clip", m, clip_low=True, low_limit=0.0, clip_high=True, high_limit=1.0)
    k = gr.op("gegl:add", gr.op("gegl:multiply", m, value=-big), value=big)
    k = gr.op("gegl:convert-format", k, format=fmt(FMT))
    blit(k, buf, clip(area, w, h))
    return buf


def clip(r, w, h):
    x0, y0 = max(0, r.x), max(0, r.y)
    x1, y1 = min(w, r.x + r.width), min(h, r.y + r.height)
    return rect(x0, y0, max(0, x1 - x0), max(0, y1 - y0))


def offsets(island_node, n, w, h, roi=None):
    """An RGB float buffer with (dx, dy) in (R, G) for each pixel of
    roi: where it takes its value from; (0, 0) keeps its own."""
    check_radius(n)
    n = int(n)
    if roi is None:
        roi = rect(0, 0, w, h)
    ik = island_keys(island_node, n, w, h, roi)
    # pass 1, down the columns: the nearest island pixel of the column,
    # for roi grown by n + 2 across (what pass 2 reads)
    r1 = rect(roi.x - n - 2, roi.y, roi.width + 2 * n + 4, roi.height)
    w1 = min_pass(ik, n, 1, clip(r1, w, h))
    # split into s * M in all channels (pass 2's input, padded with BIG)
    # and dy + n (in G)
    big = float((n * n + 1) * M)
    qk = filled(r1.x, r1.y, r1.width, r1.height, big)
    q = filled(r1.x, r1.y, r1.width, r1.height, big)
    gr = Graph()
    s = gr.floor_div(gr.source(w1))
    # from R = s, G = key
    sm = gr.matrix(s, [[M, 0, 0, 0, 0], [M, 0, 0, 0, 0], [M, 0, 0, 0, 0], [0, 0, 0, 0, 1]])
    blit(gr.op("gegl:convert-format", sm, format=fmt(FMT)), qk, clip(r1, w, h))
    dyn = gr.matrix(s, [[0, 0, 0, 0, 0], [-M, 1, 0, 0, 0], [0, 0, 0, 0, 0], [0, 0, 0, 0, 1]])
    blit(gr.op("gegl:convert-format", dyn, format=fmt(FMT)), q, clip(r1, w, h))
    # pass 2, along the rows
    w2 = min_pass(qk, n, 0, roi)
    gr = Graph()
    s = gr.floor_div(gr.source(w2))
    # R = s, G = key: dx = key - s * M - n; found = clip(n^2 + 1 - s)
    d = gr.matrix(s, [[-M, 1, 0, 0, -n], [0, 0, 0, 0, 0], [0, 0, 0, 0, 0], [0, 0, 0, 0, 1]])
    found = gr.matrix(s, [[-1, 0, 0, 0, n * n + 1], [-1, 0, 0, 0, n * n + 1],
                          [-1, 0, 0, 0, n * n + 1], [0, 0, 0, 0, 1]])
    found = gr.op("gegl:rgb-clip", found, clip_low=True, low_limit=0.0, clip_high=True,
                  high_limit=1.0)
    # dy + n of the chosen column, in G of q at (x + dx, y)
    dyc = fetch(gr, gr.source(q), d, n)
    both = gr.op("gegl:add", d, gr.matrix(dyc, [[0, 0, 0, 0, 0], [0, 1, 0, 0, -n],
                                                [0, 0, 0, 0, 0], [0, 0, 0, 0, 1]]))
    o = gr.op("gegl:multiply", both, found)
    o = gr.op("gegl:convert-format", o, format=fmt(FMT))
    out = Gegl.Buffer.new(FMT, roi.x, roi.y, roi.width, roi.height)
    blit(o, out, roi)
    return out


def fetch_exact(gr, numbers, off, n):
    """The numbers (straight RGBA float, any alpha) at the offsets,
    exactly. Sampling and moving work on premultiplied pixels, which
    would lose the colour under alpha 0 and round it under other
    alphas (svg-matrix reads premultiplied too); so the colour is
    fetched as RGB float (opaque: converting straight RGBA to RGB drops
    alpha and nothing else) and alpha by itself as an opaque grey, and
    the two are put together unpremultiplied."""
    rgb = fetch(gr, gr.op("gegl:convert-format", numbers, format=fmt("RGB float")), off, n)
    # alpha is not premultiplied, so the matrix moves it exactly
    a = gr.matrix(numbers, [[0, 0, 0, 1, 0], [0, 0, 0, 1, 0], [0, 0, 0, 1, 0], [0, 0, 0, 0, 1]])
    a = fetch(gr, a, off, n)
    # (0, 0, 0, a), made straight before the add, so that the add
    # (which keeps its input's alpha) writes straight colour under any
    # alpha
    a = gr.matrix(a, [[0, 0, 0, 0, 0], [0, 0, 0, 0, 0], [0, 0, 0, 0, 0], [1, 0, 0, 0, 0]])
    a = gr.op("gegl:convert-format", a, format=fmt("RGBA float"))
    return gr.op("gegl:add", a, gr.op("gegl:convert-format", rgb, format=fmt("RGBA float")))


def dilate(numbers_node, island_node, n, w, h, out_buf, cast_to=None, rois=None, allowed=None):
    """Writes the dilated numbers into out_buf for each rectangle of
    rois (default: the whole image). numbers_node gives the stored
    numbers as straight RGBA float; cast_to: the format name those
    numbers are cast back to before writing (see uv_gimp). moved: a
    "Y u8" buffer that gets 255 where a pixel changes. allowed: a "Y u8"
    buffer; only pixels above 127 there may change (a selection)."""
    for roi in rois or [rect(0, 0, w, h)]:
        off = offsets(island_node, n, w, h, roi)
        gr = Graph()
        o = gr.source(off)
        if allowed is not None:
            a = gr.op("gegl:add", gr.source(allowed), value=-0.5)
            a = gr.op("gegl:multiply", a, value=16777216.0)
            a = gr.op("gegl:rgb-clip", a, clip_low=True, low_limit=0.0, clip_high=True,
                      high_limit=1.0)
            o = gr.op("gegl:multiply", o, a)
        res = fetch_exact(gr, numbers_node, o, n)
        if cast_to:
            res = gr.op("gegl:cast-format", res, input_format=fmt("RGBA float"),
                        output_format=fmt(cast_to))
        blit(res, out_buf, roi)
