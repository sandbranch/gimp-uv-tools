# The definition of seam bleed in plain Python, as the tests' reference:
# brute force over every island pixel.
#
# Copyright 2026 David
# SPDX-License-Identifier: GPL-3.0-or-later


def offsets(mask, w, h, n):
    """For each pixel (row-major) the offset (dx, dy) to the island
    pixel whose value it takes, (0, 0) if it keeps its own. mask:
    row-major booleans. Nearest by squared distance (at most n^2); among
    equally near ones the smallest dx, then the smallest |dy|, then the
    negative dy: exactly what uv_dilate's graphs choose."""
    isl = [(x, y) for y in range(h) for x in range(w) if mask[y * w + x]]
    out = []
    for y in range(h):
        for x in range(w):
            if mask[y * w + x]:
                out.append((0, 0))
                continue
            best = None
            for (ix, iy) in isl:
                dx, dy = ix - x, iy - y
                if abs(dx) > n or abs(dy) > n:
                    continue
                k = (dx * dx + dy * dy, dx, dy * dy, dy)
                if best is None or k < best:
                    best = k
            if best is None or best[0] > n * n:
                out.append((0, 0))
            else:
                out.append((best[1], best[3]))
    return out


def nearest_sets(mask, w, h, n):
    """For each pixel the set of island pixels (x, y) nearest to it
    within n, or None: island pixels and pixels farther than n."""
    isl = [(x, y) for y in range(h) for x in range(w) if mask[y * w + x]]
    out = []
    for y in range(h):
        for x in range(w):
            if mask[y * w + x]:
                out.append(None)
                continue
            best, s = None, []
            for (ix, iy) in isl:
                d = (ix - x) ** 2 + (iy - y) ** 2
                if d > n * n:
                    continue
                if best is None or d < best:
                    best, s = d, [(ix, iy)]
                elif d == best:
                    s.append((ix, iy))
            out.append(set(s) if s else None)
    return out
