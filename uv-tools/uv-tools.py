#!/usr/bin/env python3
# -*- coding: utf-8 -*-
#
# UV Tools for GIMP 3
# Copyright 2026 David
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""UV Tools for GIMP 3: painting textures on UV layouts.

Image > UV Tools: Import UV Layout (SVG, OBJ, Blockbench) as island
outlines without internal edges, one path per island and an islands
channel; Select UV Islands; Seam Bleed; Fill Outside Islands; Pack and
Unpack Channels. See README.md.
"""

import os
import sys
import traceback

import gi
gi.require_version("Gimp", "3.0")
gi.require_version("Gegl", "0.4")
from gi.repository import Gimp, Gegl, Gio, GLib, GObject  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import uv_gimp as ug  # noqa: E402
import uv_model as um  # noqa: E402
import uv_dilate as ud  # noqa: E402

BINARY = "uv-tools"
MENU = "<Image>/Image/UV Tools"
AUTHOR = "David"
YEAR = "2026"

P_IMPORT = "plug-in-uv-tools-import"
P_SELECT = "plug-in-uv-tools-select-islands"
P_DILATE = "plug-in-uv-tools-seam-bleed"
P_FILL = "plug-in-uv-tools-fill-outside"
P_PACK = "plug-in-uv-tools-pack-channels"
P_UNPACK = "plug-in-uv-tools-unpack-channels"

ISLANDS_FROM = [("channel", "The UV Islands channel (from Import UV Layout)"),
                ("selection", "The selection"),
                ("alpha", "The layer's alpha (more than half opaque)")]
SELECT_MODES = [("touching", "Islands the selection touches"),
                ("centre", "The island at the centre of the selection"),
                ("number", "Island number"),
                ("all", "All islands")]
OPERATIONS = [("replace", "Replace the selection"), ("add", "Add to the selection"),
              ("subtract", "Subtract from the selection"),
              ("intersect", "Intersect with the selection")]
FILLS = [("color", "Colour"), ("transparent", "Transparent")]
CHANNELS = [("red", "Red"), ("green", "Green"), ("blue", "Blue"), ("alpha", "Alpha")]


def choice(table):
    c = Gimp.Choice.new()
    for i, (nick, label) in enumerate(table):
        c.add(nick, i, label, "")
    return c


def error(procedure, run_mode, e, status=Gimp.PDBStatusType.EXECUTION_ERROR):
    if run_mode == Gimp.RunMode.INTERACTIVE:
        Gimp.message("UV Tools: %s" % e)
    return procedure.new_return_values(status, GLib.Error(str(e)))


def dialog(procedure, config, title, props, setup=None):
    gi.require_version("GimpUi", "3.0")
    from gi.repository import GimpUi
    GimpUi.init(BINARY)
    d = GimpUi.ProcedureDialog.new(procedure, config, title)
    if setup:
        setup(d)
    d.fill(props)
    try:
        return d.run()
    finally:
        d.destroy()


def values(procedure, *vals):
    r = procedure.new_return_values(Gimp.PDBStatusType.SUCCESS, GLib.Error())
    for i, v in enumerate(vals, 1):
        r.remove(i) if r.length() > i else None
        r.insert(i, v)
    return r


def guarded(fn):
    """Any unexpected error comes back as an execution error with its
    message, never as a crash without return values."""
    def run(self, procedure, run_mode, image, drawables, config, data):
        try:
            return fn(self, procedure, run_mode, image, drawables, config, data)
        except Exception as e:
            traceback.print_exc()
            return error(procedure, run_mode, "%s: %s" % (type(e).__name__, e))
    run.__name__ = fn.__name__
    return run


class UVTools(Gimp.PlugIn):
    def do_query_procedures(self):
        return [P_IMPORT, P_SELECT, P_DILATE, P_FILL, P_PACK, P_UNPACK]

    def do_set_i18n(self, name):
        return False

    def do_create_procedure(self, name):
        rw = GObject.ParamFlags.READWRITE
        p = Gimp.ImageProcedure.new(self, name, Gimp.PDBProcType.PLUGIN,
                                    getattr(self, "run_" + name.split("uv-tools-")[1]
                                            .replace("-", "_")), None)
        p.set_image_types("*")
        p.set_attribution(AUTHOR, AUTHOR, YEAR)
        any_ = (Gimp.ProcedureSensitivityMask.DRAWABLE | Gimp.ProcedureSensitivityMask.DRAWABLES
                | Gimp.ProcedureSensitivityMask.NO_DRAWABLES)
        if name == P_IMPORT:
            p.set_menu_label("_Import UV Layout...")
            p.set_documentation(
                "Import a UV layout as island outlines, paths and a channel",
                "Reads a UV layout (SVG from Blender's File > Export UV Layout or other "
                "programs, an OBJ, or a Blockbench .bbmodel), scales it to the image (the "
                "layout's 0..1 is the whole image) and adds: a path 'UV Islands' with the "
                "outer boundaries of the islands (and their holes), without the internal "
                "edges; one path per island; optionally all edges; a channel 'UV Islands' "
                "with all islands. One undo step.", name)
            p.set_sensitivity_mask(any_)
            p.add_file_argument("file", "UV layout _file",
                                "SVG, OBJ or Blockbench .bbmodel with the UVs",
                                Gimp.FileChooserAction.OPEN, False, None, rw)
            p.add_boolean_argument("flip-v", "_Flip V (mirror top to bottom)",
                                   "Mirror the layout top to bottom, for UVs whose V points "
                                   "down (e.g. DirectX-style) in a file that assumes up",
                                   False, rw)
            p.add_string_argument("material", "_Material or texture",
                                  "OBJ: only faces after 'usemtl <this>'; Blockbench: only "
                                  "faces of this texture (name or index). Empty: all", "", rw)
            p.add_int_argument("udim-tile", "_UDIM tile", "OBJ: the UDIM tile to take "
                               "(1001 is 0..1)", 1001, 1100, 1001, rw)
            p.add_boolean_argument("island-outline", "Island _outline path",
                                   "A path 'UV Islands' with the islands' outer boundaries "
                                   "and holes (no internal edges), shown on the canvas",
                                   True, rw)
            p.add_boolean_argument("island-paths", "One _path per island",
                                   "A path 'UV Island N' for each island (Select > From "
                                   "Path selects it)", True, rw)
            p.add_boolean_argument("all-edges", "All _edges path",
                                   "A path 'UV Edges' with every face edge", False, rw)
            p.add_boolean_argument("islands-channel", "Islands _channel",
                                   "A channel 'UV Islands': all islands, pixel centres "
                                   "inside a face", True, rw)
            p.add_boolean_argument("island-ids-layer", "Island _IDs layer",
                                   "A hidden layer 'UV Island IDs', each island in its own "
                                   "colour, for the Fuzzy Select tool", False, rw)
            p.add_boolean_argument("outline-layer", "Outline _layer",
                                   "A layer 'UV Outline' with the island outline drawn in",
                                   False, rw)
            p.add_color_argument("outline-color", "Outline colo_ur",
                                 "Colour of the outline layer", True,
                                 Gegl.Color.new("#ff00ff"), rw)
            p.add_boolean_argument("replace", "_Replace an earlier import",
                                   "Remove the paths, channel and layers of an earlier "
                                   "import first", True, rw)
            p.add_int_return_value("islands", "Islands", "Number of islands", 0, 1 << 30, 0, rw)
            p.add_int_return_value("faces", "Faces", "Number of faces", 0, 1 << 30, 0, rw)
            p.add_path_return_value("outline", "Outline", "The outline path", True, rw)
            p.add_channel_return_value("channel", "Channel", "The islands channel", True, rw)
        elif name == P_SELECT:
            p.set_menu_label("_Select UV Islands...")
            p.add_menu_path("<Image>/Select")
            p.set_documentation(
                "Select UV islands",
                "Selects the islands the current selection touches (drag a small rectangle "
                "over an island, then run this), the island at the centre of the selection, "
                "an island by number, or all islands; replacing, adding to, subtracting "
                "from or intersecting the selection. Needs Import UV Layout first.", name)
            p.set_sensitivity_mask(any_)
            p.add_choice_argument("mode", "_Select", "Which islands", choice(SELECT_MODES),
                                  "touching", rw)
            p.add_int_argument("island", "Island _number", "Island number (1..N, as in the "
                               "path names), for mode 'number'", 1, 1 << 30, 1, rw)
            p.add_choice_argument("operation", "_Mode", "What to do with the selection",
                                  choice(OPERATIONS), "replace", rw)
            p.add_int_return_value("count", "Count", "Islands selected", 0, 1 << 30, 0, rw)
        elif name == P_DILATE:
            p.set_menu_label("Seam _Bleed...")
            p.set_documentation(
                "Spread the islands' colours outward (edge padding, dilation)",
                "Every pixel outside the islands within the given distance of an island "
                "(Euclidean, between pixel centres) gets the exact value of its nearest "
                "island pixel; island pixels and pixels farther away are not changed. "
                "Works on the stored numbers (colour and data maps alike, 8, 16 and 32 bit). "
                "With islands from the channel or the alpha, a selection limits where pixels "
                "change. One undo step.", name)
            p.set_sensitivity_mask(Gimp.ProcedureSensitivityMask.DRAWABLE)
            p.add_int_argument("radius", "_Pixels", "How far to spread (1 to %d)"
                               % ud.MAX_RADIUS, 1, ud.MAX_RADIUS, 8, rw)
            p.add_choice_argument("islands-from", "_Islands from", "Where the islands are",
                                  choice(ISLANDS_FROM), "channel", rw)
            p.add_channel_argument("islands-channel", "Islands c_hannel",
                                   "A channel with the islands (for 'channel'); none: the "
                                   "'UV Islands' channel of Import UV Layout", True, rw)
            p.add_drawable_return_value("drawable", "Drawable",
                                        "The result: for a layer a new layer object in the "
                                        "old one's place (same name, tattoo, mask and "
                                        "properties), for a channel the same one", False, rw)
            p.add_int_return_value("pixels", "Pixels", "Pixels in the areas worked on", 0,
                                   1 << 30, 0, rw)
        elif name == P_FILL:
            p.set_menu_label("_Fill Outside Islands...")
            p.set_documentation(
                "Fill everything outside the UV islands",
                "Fills the pixels outside the islands with a colour or makes them "
                "transparent. 'Colour as stored numbers' stores the colour's components "
                "as they are (for data maps in linear images). One undo step.", name)
            p.set_sensitivity_mask(Gimp.ProcedureSensitivityMask.DRAWABLE)
            p.add_choice_argument("islands-from", "_Islands from", "Where the islands are",
                                  choice(ISLANDS_FROM), "channel", rw)
            p.add_channel_argument("islands-channel", "Islands c_hannel",
                                   "A channel with the islands (for 'channel'); none: the "
                                   "'UV Islands' channel of Import UV Layout", True, rw)
            p.add_choice_argument("fill", "_Fill with", "Colour or transparent",
                                  choice(FILLS), "color", rw)
            p.add_color_argument("color", "_Colour", "The fill colour", True,
                                 Gegl.Color.new("black"), rw)
            p.add_boolean_argument("as-numbers", "Colour as stored _numbers (data maps)",
                                   "Store the colour's components as they are, without "
                                   "converting to the image's encoding", False, rw)
        elif name == P_PACK:
            p.set_menu_label("_Pack Channels...")
            p.set_documentation(
                "Pack grey layers into the red, green, blue (and alpha) of one layer",
                "Makes a new layer whose channels are the stored numbers of one channel of "
                "each source layer (e.g. occlusion, roughness, metallic into R, G, B), at "
                "the image's precision, without any colour conversion.", name)
            p.set_sensitivity_mask(any_)
            p.add_layer_argument("red", "_Red from", "Layer for the red channel", True, rw)
            p.add_layer_argument("green", "_Green from", "Layer for the green channel", True, rw)
            p.add_layer_argument("blue", "_Blue from", "Layer for the blue channel", True, rw)
            p.add_layer_argument("alpha", "_Alpha from", "Layer for the alpha channel (none: "
                                 "no alpha)", True, rw)
            p.add_choice_argument("source-channel", "Take each layer's",
                                  "Which channel of each source layer (grey layers have "
                                  "R = G = B)", choice(CHANNELS[:3]), "red", rw)
            p.add_string_argument("name", "Layer _name", "Name of the new layer", "Packed", rw)
            p.add_layer_return_value("layer", "Layer", "The packed layer", False, rw)
        elif name == P_UNPACK:
            p.set_menu_label("_Unpack Channels")
            p.set_documentation(
                "Unpack a layer into grey layers, one per channel",
                "Adds a grey layer (R = G = B) for the red, green, blue and, optionally, "
                "alpha of the layer, with the stored numbers as they are.", name)
            p.set_sensitivity_mask(Gimp.ProcedureSensitivityMask.DRAWABLE)
            p.add_boolean_argument("with-alpha", "Also _alpha", "Also a layer for the alpha",
                                   True, rw)
            p.add_int_return_value("count", "Count", "Layers made", 0, 4, 0, rw)
        p.add_menu_path(MENU)
        return p

    # ---------------------------------------------------------------- run

    @guarded
    def run_import(self, procedure, run_mode, image, drawables, config, data):
        if run_mode == Gimp.RunMode.INTERACTIVE:
            def setup(d):
                pass
            if not dialog(procedure, config, "Import UV Layout",
                          ["file", "island-outline", "island-paths", "all-edges",
                           "islands-channel", "island-ids-layer", "outline-layer",
                           "outline-color", "flip-v", "material", "udim-tile", "replace"],
                          setup):
                return procedure.new_return_values(Gimp.PDBStatusType.CANCEL, GLib.Error())
        f = config.get_property("file")
        path = f.get_path() if f is not None else None
        if not path:
            return error(procedure, run_mode, "choose a UV layout file",
                         Gimp.PDBStatusType.CALLING_ERROR)
        try:
            r = ug.import_layout(
                image, path, flip_v=config.get_property("flip-v"),
                material=config.get_property("material") or "",
                tile=config.get_property("udim-tile"),
                outline=config.get_property("island-outline"),
                island_paths=config.get_property("island-paths"),
                edges=config.get_property("all-edges"),
                channel=config.get_property("islands-channel"),
                ids_layer=config.get_property("island-ids-layer"),
                guide_layer=config.get_property("outline-layer"),
                guide_color=config.get_property("outline-color"),
                replace=config.get_property("replace"))
        except (um.LayoutError, ug.UVError, OSError) as e:
            return error(procedure, run_mode, e)
        except Exception as e:
            traceback.print_exc()
            return error(procedure, run_mode, e)
        if run_mode == Gimp.RunMode.INTERACTIVE and r["aspect"] and abs(
                r["aspect"] - image.get_width() / image.get_height()) > 0.01:
            Gimp.message("UV Tools: the layout is %.3g:1 but the image is %.3g:1; it was "
                         "stretched to the image." % (r["aspect"], image.get_width()
                                                     / image.get_height()))
        return values(procedure, GObject.Value(GObject.TYPE_INT, r["islands"]),
                      GObject.Value(GObject.TYPE_INT, r["faces"]),
                      GObject.Value(Gimp.Path, r["outline"]),
                      GObject.Value(Gimp.Channel, r["channel"]))

    @guarded
    def run_select_islands(self, procedure, run_mode, image, drawables, config, data):
        if run_mode == Gimp.RunMode.INTERACTIVE:
            if not dialog(procedure, config, "Select UV Islands", ["mode", "island", "operation"]):
                return procedure.new_return_values(Gimp.PDBStatusType.CANCEL, GLib.Error())
        try:
            got = ug.select_islands(image, config.get_property("mode"),
                                    config.get_property("island"),
                                    config.get_property("operation"))
        except ug.UVError as e:
            return error(procedure, run_mode, e)
        return values(procedure, GObject.Value(GObject.TYPE_INT, len(got)))

    @guarded
    def run_seam_bleed(self, procedure, run_mode, image, drawables, config, data):
        if len(drawables) != 1:
            return error(procedure, run_mode, "choose one layer",
                         Gimp.PDBStatusType.CALLING_ERROR)
        if run_mode == Gimp.RunMode.INTERACTIVE:
            if not dialog(procedure, config, "Seam Bleed",
                          ["radius", "islands-from", "islands-channel"]):
                return procedure.new_return_values(Gimp.PDBStatusType.CANCEL, GLib.Error())
        try:
            d, px, rects = ug.dilate(image, drawables[0], config.get_property("radius"),
                                     config.get_property("islands-from"),
                                     config.get_property("islands-channel"))
        except (ug.UVError, ValueError) as e:
            return error(procedure, run_mode, e)
        if not rects and run_mode == Gimp.RunMode.INTERACTIVE:
            Gimp.message("UV Tools: nothing to bleed (no pixels outside the islands near them)")
        return values(procedure, GObject.Value(Gimp.Drawable, d),
                      GObject.Value(GObject.TYPE_INT, px))

    @guarded
    def run_fill_outside(self, procedure, run_mode, image, drawables, config, data):
        if len(drawables) != 1:
            return error(procedure, run_mode, "choose one layer",
                         Gimp.PDBStatusType.CALLING_ERROR)
        if run_mode == Gimp.RunMode.INTERACTIVE:
            if not dialog(procedure, config, "Fill Outside Islands",
                          ["islands-from", "islands-channel", "fill", "color", "as-numbers"]):
                return procedure.new_return_values(Gimp.PDBStatusType.CANCEL, GLib.Error())
        try:
            ug.fill_outside(image, drawables[0], config.get_property("islands-from"),
                            config.get_property("islands-channel"), config.get_property("fill"),
                            config.get_property("color"), config.get_property("as-numbers"))
        except ug.UVError as e:
            return error(procedure, run_mode, e)
        return procedure.new_return_values(Gimp.PDBStatusType.SUCCESS, GLib.Error())

    @guarded
    def run_pack_channels(self, procedure, run_mode, image, drawables, config, data):
        if run_mode == Gimp.RunMode.INTERACTIVE:
            # the selected layers, top first, as red, green, blue, alpha
            layers = [d for d in drawables if isinstance(d, Gimp.Layer)]
            for prop, lay in zip(["red", "green", "blue", "alpha"], layers):
                if config.get_property(prop) is None:
                    config.set_property(prop, lay)
            if not dialog(procedure, config, "Pack Channels",
                          ["red", "green", "blue", "alpha", "source-channel", "name"]):
                return procedure.new_return_values(Gimp.PDBStatusType.CANCEL, GLib.Error())
        try:
            lay = ug.pack(image, config.get_property("red"), config.get_property("green"),
                          config.get_property("blue"), config.get_property("alpha"),
                          config.get_property("name") or "Packed",
                          config.get_property("source-channel"))
        except ug.UVError as e:
            return error(procedure, run_mode, e)
        return values(procedure, GObject.Value(Gimp.Layer, lay))

    @guarded
    def run_unpack_channels(self, procedure, run_mode, image, drawables, config, data):
        if len(drawables) != 1:
            return error(procedure, run_mode, "choose one layer",
                         Gimp.PDBStatusType.CALLING_ERROR)
        try:
            out = ug.unpack(image, drawables[0], config.get_property("with-alpha"))
        except ug.UVError as e:
            return error(procedure, run_mode, e)
        return values(procedure, GObject.Value(GObject.TYPE_INT, len(out)))


if __name__ == "__main__":
    Gimp.main(UVTools.__gtype__, sys.argv)
