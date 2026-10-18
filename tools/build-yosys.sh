#!/bin/bash
# Build a yosys with ENABLE_GREENPAK4 (synth_greenpak4).
#
# Stock yosys distributions - including oss-cad-suite and Homebrew -
# do not enable the greenpak4 architecture, so `synth_greenpak4`
# does not exist there. This script builds yosys from source with
# the option enabled.
#
# Usage:  tools/build-yosys.sh [prefix]
#         (default prefix: <repo>/tools/yosys)
#
# Requires: git, Xcode CLT (clang, make), python3.
# Build takes a few minutes on a modern machine.

set -euo pipefail

HERE="$(cd "$(dirname "$0")/.." && pwd)"
PREFIX="${1:-$HERE/tools/yosys}"
JOBS=$(sysctl -n hw.ncpu 2>/dev/null || echo 8)

if [ -x "$PREFIX/yosys" ] && "$PREFIX/yosys" -p "help synth_greenpak4" > /dev/null 2>&1; then
    echo "yosys with greenpak4 already built at $PREFIX/yosys"
    exit 0
fi

WORK="${YOSYS_BUILD_DIR:-$HOME/Downloads/yosys-build}"
mkdir -p "$WORK"
rm -rf "$WORK/yosys"

echo "==> cloning yosys v0.59 (with submodules) into $WORK"
# greenpak4 was REMOVED from yosys master (moved to CMake, no
# synth_greenpak4). v0.59 is the last line that carries it and is the
# same version Go Configure bundles.
git clone --depth 1 --branch v0.59 --recurse-submodules --shallow-submodules \
    https://github.com/YosysHQ/yosys.git "$WORK/yosys"

echo "==> building (make -j$JOBS ENABLE_GREENPAK4=1)"
make -C "$WORK/yosys" -j"$JOBS" ENABLE_GREENPAK4=1 > "$WORK/build.log" 2>&1 || {
    tail -30 "$WORK/build.log"
    echo "ERROR: build failed - full log: $WORK/build.log"
    exit 1
}

echo "==> verifying synth_greenpak4"
"$WORK/yosys/yosys" -p "help synth_greenpak4" > /dev/null
"$WORK/yosys/yosys" -V

mkdir -p "$PREFIX"
cp "$WORK/yosys/yosys" "$PREFIX/yosys"
cp -R "$WORK/yosys/share" "$PREFIX/share" 2>/dev/null || true

echo ""
echo "done: $PREFIX/yosys (greenpak4 enabled)"
echo "use it via:  YOSYS=$PREFIX/yosys make ..."
