#!/bin/sh
# The dialogs of UV Tools and their undo in a real GIMP on a Broadway
# display (tests/gui/gui_test.py). Screenshots in tests/output/gui/.
# Run tests/run.sh first (it makes the layouts with Blender).
#
#   tests/gui/gui-test.sh
#   UVT_GUI_LOOK=1 tests/gui/gui-test.sh   only the dialogs' screenshots
#
# Skips (exit 0) without Chrome, node 22 or gimp-devtools (next
# to this repository, or GIMP_PLUGIN_DEVTOOLS) for gui/cdp.mjs.
#
# Copyright 2026 David
# SPDX-License-Identifier: GPL-3.0-or-later
here=$(cd "$(dirname "$0")" && pwd)
tests=$(dirname "$here")
src=$(dirname "$tests")
out=$tests/output/gui
devtools=${GIMP_PLUGIN_DEVTOOLS:-$src/../gimp-devtools}
command -v google-chrome >/dev/null || command -v chromium >/dev/null ||
  command -v chromium-browser >/dev/null || { echo "UVT SKIP gui: no Chrome or Chromium"; exit 0; }
node -e 'process.exit(typeof WebSocket === "undefined" ? 1 : 0)' 2>/dev/null ||
  { echo "UVT SKIP gui: needs node 22 or later"; exit 0; }
[ -f "$devtools/gui/cdp.mjs" ] || { echo "UVT SKIP gui: no $devtools/gui/cdp.mjs"; exit 0; }
[ -f "$tests/output/cases/monkey/layout.svg" ] ||
  { echo "UVT FAIL gui: no test layouts (run tests/run.sh, it makes them with Blender)"; exit 1; }
devtools=$(cd "$devtools" && pwd)

rm -rf "$out"
mkdir -p "$out"
profile=$tests/output/profile-gui
rm -rf "$profile"
mkdir -p "$profile/plug-ins/uv-tools"
cp "$src/uv-tools/"*.py "$profile/plug-ins/uv-tools/"
chmod 755 "$profile/plug-ins/uv-tools/uv-tools.py"
# one window at the top left, no welcome dialog
cat >"$profile/sessionrc" <<'EOF'
(session-info "toplevel"
    (factory-entry "gimp-single-image-window")
    (position 0 0)
    (size 1360 860))
(single-window-mode yes)
EOF
version=$(gimp-console-3.2 --version | sed 's/.* //')
printf '(config-version "%s")\n(show-welcome-dialog no)\n' "$version" >"$profile/gimprc"

UVT_GUI_OUT=$out UVT_CASES=$tests/output/cases UVT_GUI_PROFILE=$profile \
  GIMP_PLUGIN_DEVTOOLS=$devtools timeout 1200 python3 "$here/gui_test.py"
