#!/bin/bash
# Build nextpnr-generic with the forgefpga/greenpak uarch.
#
# oss-cad-suite ships nextpnr-generic, but WITHOUT this repo's uarch
# (uarchs are compiled in) - a one-time source build is required for
# --uarch forgefpga / --uarch greenpak.
#
# Usage:  tools/build-nextpnr.sh
# Result: tools/nextpnr/build/nextpnr-generic
#
# Requires: cmake, boost, eigen3 (macOS: port install boost eigen3),
# a C++17 compiler.

set -euo pipefail

HERE="$(cd "$(dirname "$0")/.." && pwd)"
SRC="$HERE/tools/nextpnr-src"
JOBS=$(sysctl -n hw.ncpu 2>/dev/null || nproc 2>/dev/null || echo 8)

if [ -x "$HERE/tools/nextpnr/build/nextpnr-generic" ] && \
   "$HERE/tools/nextpnr/build/nextpnr-generic" --help 2>&1 | grep -q forgefpga && \
   [ -f "$SRC/generic/viaduct/forgefpga/forgefpga.cc" ] && \
   cmp -s "$HERE/uarch/forgefpga/forgefpga.cc" "$SRC/generic/viaduct/forgefpga/forgefpga.cc" && \
   cmp -s "$HERE/uarch/forgefpga/constids.inc" "$SRC/generic/viaduct/forgefpga/constids.inc"; then
    echo "nextpnr with forgefpga uarch already built (uarch sources current)"
    exit 0
fi
echo "==> uarch sources changed or not yet built: (re)building"

if [ ! -d "$SRC" ]; then
    echo "==> cloning nextpnr"
    git clone --depth 1 https://github.com/YosysHQ/nextpnr.git "$SRC"
fi

echo "==> installing the uarch (2 files + CMake registration)"
mkdir -p "$SRC/generic/viaduct/forgefpga"
cp "$HERE/uarch/forgefpga/forgefpga.cc" "$SRC/generic/viaduct/forgefpga/"
cp "$HERE/uarch/forgefpga/constids.inc" "$SRC/generic/viaduct/forgefpga/"
python3 - <<EOF
import re
p = "$SRC/generic/CMakeLists.txt"
s = open(p).read()
if "forgefpga" not in s:
    s = s.replace("    viaduct/example/constids.inc",
                  "    viaduct/example/constids.inc\n"
                  "    viaduct/forgefpga/constids.inc\n"
                  "    viaduct/forgefpga/forgefpga.cc")
    open(p, "w").write(s)
print("CMakeLists.txt patched")
EOF

echo "==> configuring (cmake -DARCH=generic, GUI/Python off)"
cmake -B "$HERE/tools/nextpnr/build" -S "$SRC" -DARCH=generic \
    -DBUILD_GUI=OFF -DBUILD_PYTHON=OFF -DCMAKE_BUILD_TYPE=Release \
    -DCMAKE_PREFIX_PATH="$(command -v brew >/dev/null && brew --prefix || echo /opt/local)" \
    > /dev/null

echo "==> building (cmake --build -j$JOBS)"
cmake --build "$HERE/tools/nextpnr/build" -j"$JOBS"

echo "==> verifying"
"$HERE/tools/nextpnr/build/nextpnr-generic" --help 2>&1 | grep -A1 "uarch arg"
echo ""
echo "done: $HERE/tools/nextpnr/build/nextpnr-generic"
