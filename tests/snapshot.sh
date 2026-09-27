#!/bin/sh
# Prints a listing of the user's own folders of GIMP and Blender (every
# file and folder with its type, size and modification time, or
# "absent"), so that tests/run.sh can check that a test run changed none
# of them: it takes one listing before and one after and compares them.
#
#   tests/snapshot.sh > before.txt
#
# Only names, sizes and times are read, never the contents of the files.
#
# Copyright 2026 David
# SPDX-License-Identifier: GPL-3.0-or-later
for dir in \
    "$HOME/.config/GIMP/3.2" \
    "$HOME/.var/app/org.gimp.GIMP" \
    "$HOME/.var/app/org.blender.Blender" \
    "$HOME/.config/blender"
do
    if [ -e "$dir" ]; then
        find "$dir" -printf '%y %s %T@ %p\n' 2>/dev/null | LC_ALL=C sort -k4
    else
        echo "absent $dir"
    fi
done
