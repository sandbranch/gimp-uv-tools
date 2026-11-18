#!/bin/sh
# Tests UV Tools. One command, no network:
#
#   tests/run.sh               everything (the GUI test only with Chrome and node)
#   UVT_GUI=0 tests/run.sh     without the Broadway GUI test
#   UVT_ONLY=bleed tests/run.sh    only these sections of the GIMP test
#                              (procedures, import, select, bleed, fill, pack, speed)
#   UVT_SPEED=0 tests/run.sh   without the 4K timings
#
# Steps: lint (no em or en dashes, Python compiles); the layout model in
# plain Python (tests/unit.py); headless Blender (its Flatpak
# org.blender.Blender if installed, else the blender command)
# makes the test layouts and GIMP Link for Blender's reference islands,
# boundary edges and masks (tests/blender_make.py); the model again on
# those; headless native GIMP (gimp-console) runs the plug-in's
# procedures on them (tests/gimp_test.py); the dialogs and undo in a
# real GIMP on a Broadway display (tests/gui/gui-test.sh).
#
# GIMP and Blender run with throwaway profiles and a throwaway home
# under tests/output (tests/isolate.sh, a copy of gimp-devtools'),
# with no GVFS, so that nothing lands in your folders. Before and after,
# tests/snapshot.sh lists your folders of GIMP, GEGL and Blender (names,
# sizes, times) and the run fails if anything there changed. (Other programs
# using those folders while the tests run make this check fail too.)
#
# Prints PASS, FAIL or SKIP for each case; exits non-zero if any failed.
#
# Copyright 2026 David
# SPDX-License-Identifier: GPL-3.0-or-later
here=$(cd "$(dirname "$0")" && pwd)
src=$(dirname "$here")
out=$here/output
status=0
mkdir -p "$out"
# shellcheck source=SCRIPTDIR/isolate.sh
. "$here/isolate.sh"

"$here/snapshot.sh" > "$out/snapshot-before.txt"

echo "== lint"
python3 - "$src" <<'EOF' || status=1
import os, sys
src = sys.argv[1]
bad = []
broken = []
for root, dirs, files in os.walk(src):
    dirs[:] = [d for d in dirs if d not in (".git", "output", "__pycache__")]
    for f in files:
        p = os.path.join(root, f)
        if f.endswith((".py", ".md", ".sh", ".txt", ".svg", ".json")) or f in ("COPYING", ".gitignore"):
            for n, line in enumerate(open(p, encoding="utf-8"), 1):
                if chr(0x2014) in line or chr(0x2013) in line:
                    bad.append("%s:%d" % (os.path.relpath(p, src), n))
        if f.endswith(".py"):
            try:
                compile(open(p, encoding="utf-8").read(), p, "exec")
            except SyntaxError as e:
                broken.append("%s: %s" % (os.path.relpath(p, src), e))
print("UVT PASS no em or en dashes" if not bad else "UVT FAIL em or en dashes: " + " ".join(bad))
print("UVT PASS the Python compiles" if not broken else "UVT FAIL Python: " + "; ".join(broken))
sys.exit(1 if bad or broken else 0)
EOF

echo "== unit"
python3 "$here/unit.py" || status=1

# the plug-in, installed into a throwaway GIMP profile
profile=$out/profile
rm -rf "$profile/plug-ins"
mkdir -p "$profile/plug-ins/uv-tools"
cp "$src/uv-tools/"*.py "$profile/plug-ins/uv-tools/"
chmod 755 "$profile/plug-ins/uv-tools/uv-tools.py"

echo "== Blender (test layouts)"
cases=$out/cases
gbl=${GBL_REPO:-$src/../gimp-blender-link}
if ! command -v blender >/dev/null 2>&1 &&
   ! { command -v flatpak >/dev/null 2>&1 && flatpak info org.blender.Blender >/dev/null 2>&1; }; then
    echo "UVT SKIP Blender: no blender command and no Flatpak org.blender.Blender"
    cases=
elif [ ! -f "$gbl/blender/gimp_link/islands.py" ]; then
    echo "UVT SKIP Blender: no GIMP Link for Blender at $gbl (GBL_REPO)"
    cases=
else
    gbl=$(cd "$gbl" && pwd)
    rm -rf "$cases" "$out/blender"
    b=$out/blender
    # (in its Flatpak if that is installed, else natively)
    gimp_run --timeout=900 --app=org.blender.Blender --home="$b/home" \
      --filesystem="$src" --filesystem="$gbl:ro" --env=GBL_REPO="$gbl" \
      --env=BLENDER_USER_RESOURCES="$b/res" --env=BLENDER_USER_CONFIG="$b/config" \
      --env=BLENDER_USER_SCRIPTS="$b/scripts" --env=BLENDER_USER_EXTENSIONS="$b/ext" -- \
      blender -b --factory-startup --python "$here/blender_make.py" -- "$cases" \
      >"$out/blender.log" 2>&1
    grep "^MADE" "$out/blender.log"
    if grep -q "^BLENDER MAKE done" "$out/blender.log"; then
        echo "UVT PASS Blender made the test layouts"
    else
        echo "UVT FAIL Blender (see $out/blender.log)"
        grep -E "Traceback|Error" "$out/blender.log" | head
        status=1
        cases=
    fi
fi

if [ -n "$cases" ]; then
    echo "== unit (Blender's layouts)"
    python3 "$here/unit.py" "$cases" | grep -v "PASS unit svg\|PASS unit bbmodel\|PASS unit obj\|PASS unit mask\|PASS unit pixel\|PASS unit flip\|PASS unit ref" || true
    python3 "$here/unit.py" "$cases" >/dev/null 2>&1 || status=1
fi

echo "== GIMP (headless)"
only=$UVT_ONLY
if [ -z "$only" ] && [ "$UVT_SPEED" = 0 ]; then
    only=procedures,import,select,bleed,fill,pack
fi
log=$out/gimp.log
gimp_run --timeout=1800 --home="$out/gimp-home" \
  --env=GIMP3_DIRECTORY="$profile" --env=UVT_SRC="$src" --env=UVT_OUT="$out/work" \
  --env=UVT_CASES="$cases" --env=UVT_ONLY="$only" -- \
  gimp-console-3.2 --no-interface --no-fonts --batch-interpreter python-fu-eval \
  -b "exec(open('$here/gimp_test.py').read())" --quit >"$log" 2>&1
code=$?
grep -E "^UVT (PASS|FAIL|TIME|SKIP)" "$log"
if [ $code = 124 ]; then
    echo "UVT FAIL gimp: GIMP did not finish in time (log: $log)"
    status=1
elif ! grep -q "^UVT GIMP failures: 0$" "$log"; then
    grep -E "Traceback|Error|^  File" "$log" | head -20
    echo "UVT FAIL gimp: see $log"
    status=1
fi
if grep -E "uv-tools.py.*(WARNING|CRITICAL)" "$log"; then
    echo "UVT FAIL gimp: warnings from the plug-in, see $log"
    status=1
fi

if [ "$UVT_GUI" != 0 ]; then
    echo "== GUI (Broadway)"
    "$here/gui/gui-test.sh" || status=1
fi

echo "== your folders of GIMP and Blender"
"$here/snapshot.sh" > "$out/snapshot-after.txt"
if cmp -s "$out/snapshot-before.txt" "$out/snapshot-after.txt"; then
    echo "UVT PASS nothing changed in them ($(wc -l < "$out/snapshot-after.txt") entries)"
else
    echo "UVT FAIL they changed:"
    diff "$out/snapshot-before.txt" "$out/snapshot-after.txt" | head -20
    status=1
fi

[ $status = 0 ] && echo "UVT all passed" || echo "UVT FAILED (logs in $out)"
exit $status
