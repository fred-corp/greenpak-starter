#!/usr/bin/env python3
"""Emit device_regs LUT tables from datasheet register maps.

Sources (register maps in the official datasheets; reg<high:low>):
- SLG46533 DS v1.31, SLG46537 DS r123: identical LUT layout
  (2x/3x/4-bit LUTs; the PDF has typos for LUT1/LUT9 ranges - the
  ascending bit sequences are unambiguous from the stride patterns)
- SLG47011 DS r19ds0121: Reg[high:low] style, 8-bit LUT3 macrocells

Output: chipdb/device_regs/<DEV>_luts.json in the same format the
decompilation extractor produces (truth_regs ascending = LSB first).
"""

import json
import os
import sys

LUTS_46533_46537 = {
    "LUT2": [(1204, 1207), (1200, 1203), (1212, 1215)],
    "LUT3": [(1216, 1223), (1224, 1231), (1232, 1239), (1240, 1247),
             (1248, 1255), (1536, 1543), (1544, 1551), (1552, 1559),
             (1560, 1567), (1568, 1575), (1256, 1263), (1368, 1375),
             (1376, 1383), (1384, 1391), (1392, 1399), (1400, 1407),
             (1408, 1415), (1416, 1423)],
    "LUT4": [(1576, 1591), (1592, 1607)],
}

# SLG47011: LUT2/LUT3 macrocell truth tables (Reg[high:low])
LUTS_47011 = {
    "LUT2": [(232, 235)],
    "LUT3": [(236, 243), (244, 251), (252, 259), (260, 267), (272, 279),
             (288, 295), (304, 319), (352, 359), (408, 415)],
}


def emit(device, tables, outdir):
    macros = []
    idx = 0
    for kind in ("LUT2", "LUT3", "LUT4"):
        for lo, hi in tables.get(kind, []):
            macros.append({
                "index": idx, "slot": "ds_%s_%d" % (kind, idx),
                "truth_regs": list(range(lo, hi + 1)),
                "lut_out_reg": None, "dff_regs": {},
                "source": "datasheet register map",
            })
            idx += 1
    out = os.path.join(outdir, device + "_luts.json")
    with open(out, "w") as f:
        json.dump({"device": device, "macrocells": macros}, f, indent=1)
    print("%-10s %d macrocells -> %s" % (device, len(macros), out))


def main():
    outdir = sys.argv[1] if len(sys.argv) > 1 else "chipdb/device_regs"
    os.makedirs(outdir, exist_ok=True)
    emit("SLG46533", LUTS_46533_46537, outdir)
    emit("SLG46537", LUTS_46533_46537, outdir)
    emit("SLG47011", LUTS_47011, outdir)


if __name__ == "__main__":
    main()
