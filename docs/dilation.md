# Seam bleed: how it works

Seam bleed (edge padding, dilation) spreads each UV island's pixels
outward into the empty space around it, so that texture filtering and
mipmaps near a seam sample the island's own colours instead of whatever
lies outside.

## What it does, exactly

For a radius N and a set of island pixels:

- every pixel outside the islands whose centre is at most N pixels
  (Euclidean distance between pixel centres) from an island pixel's
  centre gets the **stored numbers** of its nearest island pixel: all
  four channels, bit for bit;
- island pixels keep their numbers;
- pixels farther than N keep theirs.

When several island pixels are equally near, the one taken is the one
with the smallest dx (to the left first), then the smallest |dy|, then
the one above. The tests compare every pixel against a brute-force
search in plain Python (`tests/uv_dilate_ref.py`) that uses the same
order, and also check that the value is that of one of the nearest
island pixels.

Which pixels are islands: the "UV Islands" channel from Import UV
Layout (or any channel), the selection, or the layer's alpha; a pixel is
an island pixel when that mask is above one half.

## Why not numpy, and why GEGL

A plug-in cannot count on numpy in GIMP's Python: a native GIMP runs
plug-ins with the system's Python, which has numpy only if it was
installed, and GIMP 3.2.6's Flatpak had Python 3.13 without it (checked
in 2026-09: `import numpy` failed in it). A pure Python loop over the
16.7 million pixels of a 4K texture takes far too long. GEGL, which GIMP
uses for all its pixel work, is there in every GIMP 3 and runs in
threaded C. So the plug-in builds GEGL graphs from Python and lets GEGL
do the work. None of GEGL's operations computes a nearest-pixel
transform (`gegl:distance-transform` gives distances only), so the
algorithm is put together from simple ones.

## The algorithm

It is the classic separable exact nearest-point transform (as in
Felzenszwalb and Huttenlocher's distance transform), written as
minimums over shifted copies:

1. **Keys.** K = 0 on island pixels, BIG elsewhere, in a buffer padded
   by N + 4 pixels of BIG, so that shifted copies never read GEGL's
   empty abyss.
2. **Pass 1, down the columns.** For each pixel, the nearest island
   pixel in its own column within N: the minimum over dy in -N..N of
   K(x, y + dy) + dy²·M + (dy + N). The low part (below M = 256) says
   which dy won.
3. **Pass 2, along the rows.** From pass 1, g² = the squared vertical
   distance in each column. For each pixel the minimum over dx in -N..N
   of g²(x + dx, y)·M + dx²·M + (dx + N): the smallest squared
   distance dx² + dy², and which dx gave it. This is exact: the nearest
   island pixel lies in some column x + dx, and in that column it is the
   vertically nearest one.
4. **Decode.** s = floor(key / M) (float32 rounding, see below),
   dx = key - s·M - N, found = (s <= N²). dy is read from pass 1 at
   (x + dx, y). Pixels not found get the offset (0, 0).
5. **Fetch.** `gegl:map-relative` with the nearest sampler copies each
   pixel from (x + dx, y + dy). Offsets of (0, 0) copy the pixel itself.

Integer arithmetic in float32: all keys are integers below 2^23 (N at
most 100), so sums, differences and products by powers of two are exact.
min(a, b) is `a - max(a - b, 0)` (`gegl:subtract`, `gegl:rgb-clip`,
`gegl:subtract`); floor(x / M) is x / M - (M - 1) / 2M, then + 1.5·2^23
and - 1.5·2^23 (float32 rounds to an integer there). Three candidates go
through each operation at once, one per channel of an RGB float buffer
(R = K(p), G = K(p + 1), B = K(p + 2)), which cuts the work to a third.

## Numbers, not colours

The pixels are read in the format that matches how the layer stores
them ("R'G'B'A u16" for 16-bit perceptual, "RGBA float" for float linear,
and so on), as floats of that same encoding, and relabelled
(`gegl:cast-format`) as linear floats so that no step on the way
converts them. 8 and 16 bit and half go through float32 exactly; float
is copied as is. 32-bit integer and double images are exact to float32
precision only (about 7 digits), since GEGL's samplers work in float32.

## GEGL and GIMP facts this rests on (all found by testing)

- The `svg:` blend operations (`svg:darken` and the others) clamp to
  0..1, so they cannot take the minimum of keys; `gegl:add`,
  `gegl:subtract` and `gegl:multiply` do not clamp and work on the
  source's own format (one channel for Y float, three for RGB float).
- `gegl:map-relative` and `gegl:map-absolute` read their aux as two raw
  floats per pixel (`babl_format_n(float, 2)` in GEGL's source): an RGBA
  aux is read wrongly. A "YA float" buffer has the same layout, but
  babl changes Y where alpha is 0 or below. So the offsets go in as
  Y = dx, alpha = dy + K with K = N + 1 (always at least 1), and the
  input is moved down by K to make up for it.
- `gegl:svg-matrix` works on premultiplied pixels (it cannot set alpha
  to a value without dividing the colour by it); `gegl:opacity`, which
  multiplies premultiplied RGBA by its aux, sets alpha instead.
- Moving and sampling work on premultiplied pixels too, which loses the
  colour under alpha 0 and rounds it under other alphas. So the colour
  is fetched as RGB float (converting straight RGBA to RGB drops alpha
  and nothing else) and alpha by itself, and the two are put together
  unpremultiplied.
- babl keeps a little of the colour of a pixel whose alpha is 0 when it
  premultiplies (so that the colour survives), which leaked into the
  "over" of transparent candidates: candidates are therefore made 0
  wherever they are transparent.
- GIMP merges a plug-in's shadow buffer by compositing it (replace mode:
  colour times alpha over alpha, old + (new - old)·mask, and through
  linear light for perceptual float), which rounds floats and some
  16-bit values under low alpha. So Seam Bleed writes into a copy of the
  layer (or channel) and puts the copy in its place, with the old one's
  name, tattoo, mask, mode, opacity and parasites: still one undo step,
  and exact. Only a layer mask, which cannot be replaced, is merged the
  usual way (exact at 8 and 16 bit).
- Chunked processing (`GeglProcessor`) was not faster than
  `gegl_node_blit_buffer`, and the work does not get faster with more
  than about 8 threads: it is bound by memory bandwidth.

## Speed

On an AMD Ryzen 9 5900X (12 cores, 24 threads), GIMP 3.2.6 (measured in
2026-09 with its Flatpak), a
4096 x 4096 layer (times from `tests/run.sh`, including reading and
writing the layer):

| Layout | Precision | N | Seconds |
|---|---|---|---|
| Suzanne, 125952 faces, 1579 islands | 16-bit | 4 | 3.0 |
| same | 16-bit | 16 | 4.2 |
| same | 8-bit | 16 | 4.2 |
| same | float | 16 | 4.3 |
| Suzanne, 1968 faces, 139 islands | 16-bit | 16 | 4.3 |
| Suzanne, 31488 faces, 833 islands, 2048 x 2048 | 16-bit | 8 | 1.3 |

Each step of pass 1 and 2 (three candidates) costs roughly 0.05 s at 4K,
so going from N = 4 to N = 16 adds about a second; the rest is fixed
(padding, packing, decoding, the fetches, reading and writing the
layer). Only tiles that hold pixels outside the islands with an
island within N are worked on; with most of a texture near an island,
as in these layouts, that is the whole image.

Considered and dropped: an iterative 1-pixel dilation and
`gegl:value-propagate` (they give a neighbour's colour, not the nearest
island pixel's), jump flooding (known not to be exact in every case),
and an "over" chain of every offset in the disc, nearest first (exact,
but about 3N² operations instead of about 4N/3). A first version with
one candidate per RGBA operation took 7.8 s for N = 16 at 4K.
