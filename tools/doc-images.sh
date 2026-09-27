#!/bin/sh
# Makes docs/images/*.png (tools/doc-images.py) in gimp-console with UV
# Tools from this repository, isolated like the tests (throwaway home
# and profile under tests/output). Run tests/run.sh first: it makes the
# Blender layouts used here.
#
# Copyright 2026 David
# SPDX-License-Identifier: GPL-3.0-or-later
here=$(cd "$(dirname "$0")" && pwd)
src=$(dirname "$here")
out=$src/tests/output
# shellcheck source=../tests/isolate.sh
. "$src/tests/isolate.sh"
profile=$out/profile-docs
rm -rf "$profile"
mkdir -p "$profile/plug-ins/uv-tools"
cp "$src/uv-tools/"*.py "$profile/plug-ins/uv-tools/"
chmod 755 "$profile/plug-ins/uv-tools/uv-tools.py"
gimp_run --timeout=600 --home="$out/docs-home" --filesystem="$src" \
  --env=GIMP3_DIRECTORY="$profile" --env=UVT_SRC="$src" --env=UVT_CASES="$out/cases" -- \
  gimp-console-3.2 --no-interface --no-fonts --batch-interpreter python-fu-eval \
  -b "exec(open('$here/doc-images.py').read())" --quit
