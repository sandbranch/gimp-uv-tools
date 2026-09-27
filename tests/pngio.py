# Reads 8-bit grey PNGs (the island masks GIMP Link for Blender writes)
# in plain Python: (width, height, bytes of the rows).
#
# Copyright 2026 David
# SPDX-License-Identifier: GPL-3.0-or-later
import struct
import zlib


def read_grey8(path):
    with open(path, "rb") as f:
        d = f.read()
    if d[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("%s is not a PNG" % path)
    i, idat, w, h = 8, [], 0, 0
    while i < len(d):
        n, t = struct.unpack(">I4s", d[i:i + 8])
        c = d[i + 8:i + 8 + n]
        i += 12 + n
        if t == b"IHDR":
            w, h, depth, ctype = struct.unpack(">IIBB", c[:10])
            if depth != 8 or ctype != 0:
                raise ValueError("%s is not 8-bit grey" % path)
        elif t == b"IDAT":
            idat.append(c)
    raw = zlib.decompress(b"".join(idat))
    out = bytearray()
    prev = bytearray(w)
    for y in range(h):
        o = y * (w + 1)
        f, row = raw[o], bytearray(raw[o + 1:o + 1 + w])
        if f == 1:
            for x in range(1, w):
                row[x] = (row[x] + row[x - 1]) & 255
        elif f == 2:
            row = bytearray((a + b) & 255 for a, b in zip(row, prev))
        elif f == 3:
            for x in range(w):
                row[x] = (row[x] + ((row[x - 1] if x else 0) + prev[x]) // 2) & 255
        elif f == 4:
            for x in range(w):
                a, b, c = (row[x - 1] if x else 0), prev[x], (prev[x - 1] if x else 0)
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                row[x] = (row[x] + (a if pa <= pb and pa <= pc else b if pb <= pc else c)) & 255
        out += row
        prev = row
    return w, h, bytes(out)
