# UV Tools and GIMP Link for Blender

GIMP Link for Blender (`../gimp-blender-link`) builds a layered XCF from
Blender's files and sends the texture back. Two of its steps are what
UV Tools does: the island paths and islands channel when it builds the
XCF, and the seam bleed when it sends. This is how it would use UV
Tools when UV Tools is installed, and keep its own code when not. Not
done here: GIMP Link's repository is unchanged; this file says exactly
what it would call. The calls are tested as written (tests/gimp_test.py,
`t_link`).

## Is UV Tools there, and which version

```python
pdb = Gimp.get_pdb()
proc = pdb.lookup_procedure("plug-in-uv-tools-version")
if proc is not None:
    res = proc.run(proc.create_config())
    version, api = res.index(1), res.index(2)     # "0.1.0", 1
```

Use UV Tools when the procedure exists and `api` is 1. The interface
level goes up when a procedure's arguments or results change; new
optional arguments at the end do not change it.

A small helper, as GIMP Link's own tests call procedures:

```python
def uvt(name, image, drawables, **args):
    proc = pdb.lookup_procedure(name)
    cfg = proc.create_config()
    cfg.set_property("run-mode", Gimp.RunMode.NONINTERACTIVE)
    cfg.set_property("image", image)
    cfg.set_core_object_array("drawables", drawables)
    for k, v in args.items():
        cfg.set_property(k.replace("_", "-"), v)
    res = proc.run(cfg)
    if res.index(0) != Gimp.PDBStatusType.SUCCESS:
        raise LinkError("%s: %s" % (name, res.index(1)))
    return [res.index(i) for i in range(1, res.length())]
```

## Building the XCF: `import_islands(image, tile)` in gbl_gimp.py

Today it imports `islands-<tile>.svg` with `import_paths_from_file`,
names the paths "Island 0 ..." and makes the channel from their union.
With UV Tools:

```python
islands, faces, outline, channel = uvt(
    "plug-in-uv-tools-import", image, [],
    file=Gio.File.new_for_path(tile["islands_svg"]),
    island_outline=True,      # path "UV Islands": outer boundaries and holes, shown
    island_paths=True,        # paths "UV Island 1..N"
    all_edges=False,
    islands_channel=True,     # channel "UV Islands"
    island_ids_layer=False,
    outline_layer=False,      # GIMP Link has its own link layers for the lines
    replace=True)             # removes what an earlier import made (Update UVs)
mark_helper(channel)
```

- `file`: `islands-<tile>.svg` (GIMP Link's own islands, one
  `<path id="island-N">` each, from the mesh). UV Tools reads such a
  file as those islands, so the island numbers stay the mesh's: GIMP
  Link's `island-N` is UV Tools' path "UV Island N+1". `uv-<tile>.svg`
  works too (UV Tools finds the islands from the faces, and reads the
  halo and line copies of the faces once), but the islands file is
  faster and keeps islands that lie exactly on top of each other apart
  by the mesh.
- The layout's 0..1 is the whole image, so the image must have the
  texture's size (it has).
- Results: the number of islands and faces, the outline path, the
  channel. The island paths are the image's paths with the parasite
  `uv-tools-island` (its data is the number, "1" .. "N"); the outline
  path has `uv-tools-outline`, the channel `uv-tools-islands`. These
  parasites are persistent and undoable, and stay on copies
  (`image.duplicate()`).
- `island_paths(image)` in GIMP Link would look for `uv-tools-island`
  instead of `gimp-blender-link-island`; `uv_changed` runs the same
  import again (`replace=True` removes the old paths and channel).
- The channel is rasterised at pixel centres (a centre on an edge is
  inside), the same rule as GIMP Link's `island-ids-<tile>.png`; the
  tests compare them pixel by pixel (differences only where the SVG's
  3 decimals move an edge across a pixel centre, within 0.0006 px).
- Undo: GIMP Link builds with undo disabled; UV Tools' undo group then
  does nothing, which is fine.

## Sending: `dilate(image, layer, n, paths)` in gbl_gimp.py

Today it bleeds by merging shifted copies of the layer (Chebyshev
distance, the colour of some island pixel within reach, through layer
compositing). With UV Tools, on the flattened copy `dup` that `send`
already makes:

```python
ch = [c for c in dup.get_channels() if c.get_parasite("uv-tools-islands") is not None]
layer, pixels = uvt("plug-in-uv-tools-seam-bleed", dup, [layer],
                    radius=min(n, 100),
                    islands_from="channel",
                    islands_channel=ch[0])
# then write `layer` (the returned one) as the texture
```

- `radius`: 1 to 100 pixels (GIMP Link allows up to 256 in its Send
  argument; UV Tools' keys stay exact in float32 up to 100).
- `islands_from`: "channel" with `islands_channel` set to the copy's
  islands channel (the duplicate keeps it, with its parasite). "alpha"
  (more than half opaque) or "selection" are the other sources.
- Result: **use the returned drawable**. For an exact result UV Tools
  writes into a copy of the layer that takes the old one's place (same
  name, tattoo, mask, mode, opacity and parasites; one undo step), so
  the old layer object is gone. GIMP Link's `send` goes on with
  `for l in dup.get_layers(): if l != layer: dup.remove_layer(l)` and
  `write_texture(dup, layer, ...)` with the returned `layer`.
- What changes for users: every pixel within N (Euclidean) of an island
  gets its nearest island pixel's exact numbers (16-bit and float data
  maps included, any alpha); pixels farther away and the islands stay
  as they are. See [dilation.md](dilation.md). A 2048 x 2048 16-bit
  texture with 833 islands and N = 8 took 1.3 s in the tests.

## Without UV Tools

GIMP Link keeps its current `import_islands` and `dilate` when
`plug-in-uv-tools-version` does not exist or its `api` is not 1.
