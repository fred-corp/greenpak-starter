#!/usr/bin/env python3
"""Generate nextpnr-generic (--uarch forgefpga) chipdb JSON for Renesas
ForgeFPGA / GreenPAK devices from the data extracted out of Go Configure.

Inputs available today:
  * analysis/extracted/gp6_qrc/defaultnvm/  - per-device default NVM register
    maps (register counts) for GreenPAK 3/4/5/6.
  * yosys greenpak4 cell library (GP_2LUT/GP_3LUT/GP_4LUT/GP_DFF*...) as the
    cell types the synthesis flow targets.
  * decompiled Slg47xxxMacrocellsEntities / NvmArrayMap classes
    (analysis/extracted/gp6_decomp_macrocells/) - the source for the real
    macrocell coordinates and switch-matrix crosspoints, to be folded in
    as the RE progresses (see docs/findings.md).

Until the crosspoint tables are lifted from the decompilation, the
generator emits a structurally valid fabric scaffold (LUT BELs, matrix
wires, full-crossbar PIPs, IO pads) parameterized per device, so the
nextpnr flow can be exercised end-to-end and refined incrementally.

Usage:
  gen_chipdb.py forgefpga SLG47125V > slg47125.json
  gen_chipdb.py greenpak SLG46826V  > slg46826.json
"""

import argparse
import json
import os
import re
import sys


def nvm_register_count(default_nvm_path):
    """Count NVM registers from a Go Configure defaultNVM txt dump."""
    n = 0
    with open(default_nvm_path) as f:
        for line in f:
            parts = line.split()
            if parts and parts[0].isdigit():
                n = max(n, int(parts[0]))
    return n + 1


def forgefpga_scaffold(part="SLG47125V", x_tiles=2, y_tiles=1, slices_per_tile=4):
    """SLG47xxx-style fabric: LUT4 macrocells with a matrix crossbar.

    The SLG47125 has 16 LUT4 macrocells (SLG47004/47006: 8); the scaffold
    models the macrocell island as a tile grid. Coordinates will be
    replaced with extracted values from the MacrocellsEntities decomp.
    """
    wires, bels, pips = [], [], []
    matrix_wires = []

    for ty in range(y_tiles):
        for tx in range(x_tiles):
            for s in range(slices_per_tile):
                # LUT4 macrocell
                bel = {
                    "name": "LUT%d_%d_%d" % (tx, ty, s),
                    "type": "LUT4",
                    "x": tx, "y": ty, "z": s,
                    "ports": {},
                }
                for i in range(4):
                    wname = "MI%d_%d_%d_%d" % (tx, ty, s, i)
                    wires.append({"name": wname, "type": "MATRIX", "x": tx, "y": ty})
                    bel["ports"]["I%d" % i] = {"dir": "in", "wire": wname}
                fw = "MF%d_%d_%d" % (tx, ty, s)
                wires.append({"name": fw, "type": "MATRIX", "x": tx, "y": ty})
                bel["ports"]["F"] = {"dir": "out", "wire": fw}
                bels.append(bel)
                matrix_wires.append((tx, ty, fw))
                for i in range(4):
                    matrix_wires.append((tx, ty, "MI%d_%d_%d_%d" % (tx, ty, s, i)))

                # DFF macrocell sharing the slice
                dwire = "MD%d_%d_%d" % (tx, ty, s)
                qwire = "MQ%d_%d_%d" % (tx, ty, s)
                clk = "CLK%d_%d_%d" % (tx, ty, s)
                for wname in (dwire, qwire, clk):
                    wires.append({"name": wname, "type": "MATRIX", "x": tx, "y": ty})
                bels.append({
                    "name": "DFF%d_%d_%d" % (tx, ty, s), "type": "DFF",
                    "x": tx, "y": ty, "z": 4 + s,
                    "ports": {"D": {"dir": "in", "wire": dwire},
                              "CLK": {"dir": "in", "wire": clk},
                              "Q": {"dir": "out", "wire": qwire}},
                })
                matrix_wires.append((tx, ty, qwire))
                matrix_wires.append((tx, ty, dwire))
                matrix_wires.append((tx, ty, clk))

    # Full crossbar between macrocell input/output matrix wires; a real
    # device has a restricted crosspoint set (extracted data pending).
    for (tx, ty, dst) in matrix_wires:
        for (sx, sy, src) in matrix_wires:
            if src == dst or src.startswith("MF") and dst.startswith("MF"):
                continue
            if (tx, ty) == (sx, sy) and src.split("_")[0] == "MF" and src.split("_")[2] == str(dst.split("_")[2]):
                pass  # same slice feedback is allowed via matrix
            pips.append({
                "name": "%s.%s" % (src, dst), "src": src, "dst": dst,
                "x": tx, "y": ty, "delay": 0.05,
            })

    # IO pads around the island
    npads = 16
    io_x = x_tiles  # dedicated IO column just outside the fabric
    for p in range(npads):
        wname = "IOPAD%d" % p
        iw = "IOPAD%d_I" % p
        ow = "IOPAD%d_O" % p
        for w, t in ((wname, "IO"), (iw, "IO"), (ow, "IO")):
            wires.append({"name": w, "type": t, "x": io_x, "y": p})
        bels.append({
            "name": "IO_%d" % p, "type": "IOB", "x": io_x, "y": p, "z": 0,
            "ports": {"PAD": {"dir": "inout", "wire": wname},
                      "I": {"dir": "in", "wire": iw},
                      "O": {"dir": "out", "wire": ow}},
        })
        matrix_wires.append((io_x, p, ow))
        # INBUF path: bel O output feeds the matrix inputs
        for (tx, ty, dst) in matrix_wires:
            if dst.startswith("MI") or dst.startswith("MD") or dst.startswith("CLK"):
                pips.append({"name": "%s.%s" % (ow, dst), "src": ow, "dst": dst,
                             "x": tx, "y": ty, "delay": 0.1})
        # OUTBUF path: matrix outputs drive the bel I input
        for (tx, ty, src) in matrix_wires:
            if src.startswith("MF") or src.startswith("MQ"):
                pips.append({"name": "%s.%s" % (src, iw), "src": src, "dst": iw,
                             "x": tx, "y": ty, "delay": 0.1})

    return {
        "device": part,
        "wires": wires,
        "bels": bels,
        "pips": pips,
        "notes": "scaffold fabric; replace BEL/PIP tables with data extracted "
                 "from the MacrocellsEntities decompilation",
    }


# Package pinouts extracted from Go Configure design-file IOPad
# captions ("<IOPad id=n ... caption='PIN k (IOx)'>"). These become the
# chipdb IO BEL names so pinout constraints reference physical pins.
GREENPAK_PINOUTS = {
    # SLG46826V / SLG46824V, TSSOP-20 style: 18 IOs, PIN 1 = VDD,
    # PIN 11/14 reserved
    "SLG46826": ["PIN2", "PIN3", "PIN4", "PIN5", "PIN6", "PIN7",
                 "PIN8", "PIN9", "PIN10", "PIN12", "PIN13", "PIN15",
                 "PIN16", "PIN17", "PIN18", "PIN19", "PIN20"],
}


def greenpak_base(part):
    """Resolve a part number to its base device (variant handling)."""
    base = re.sub(r'[A-Za-z]+$', '', part)
    vp = os.path.join(os.path.dirname(__file__), 'device_regs', 'variants.json')
    if os.path.exists(vp):
        with open(vp) as f:
            variants = json.load(f)
        base = variants.get(base, {}).get('base', base)
    return base


def greenpak_pin_names(part):
    base = re.sub(r'[A-Za-z]+$', '', part)
    vp = os.path.join(os.path.dirname(__file__), 'device_regs', 'variants.json')
    if os.path.exists(vp):
        with open(vp) as f:
            variants = json.load(f)
        base = variants.get(base, {}).get('base', base)
    return GREENPAK_PINOUTS.get(base)


def greenpak_pad_caps(part):
    """Per-pad matrix connectivity from the datasheet tables:
      'io'   - matrix input + output (normal GPIO)
      'out'  - output only (e.g. IO6/IO7 GPOs)
      'in'   - input only
      'none' - not connectable to the matrix at all (e.g. SCL/SDA,
               which route only to the I2C block)
    Falls back to 'io' for every pad when the tables are missing."""
    base = re.sub(r'[A-Za-z]+$', '', part)
    vp = os.path.join(os.path.dirname(__file__), 'device_regs', 'variants.json')
    if os.path.exists(vp):
        with open(vp) as f:
            variants = json.load(f)
        base = variants.get(base, {}).get('base', base)
    dr = os.path.join(os.path.dirname(__file__), 'device_regs')

    def load(name):
        p = os.path.join(dr, name)
        return json.load(open(p)) if os.path.exists(p) else None

    pins, inputs, outputs = (load(base + '_pins.json'),
                             load(base + '_matrix_inputs.json'),
                             load(base + '_matrix_outputs.json'))
    if not (pins and inputs and outputs):
        return {}
    pkg = next(iter(pins['packages'].values()))
    in_names = {s['name'] for s in inputs['sources']}
    out_funcs = {o['function'] for o in outputs['outputs']}
    caps = {}
    for pin, func in pkg.items():
        if not func.startswith('IO'):
            caps['PIN%s' % pin] = 'none'   # SCL/SDA/GND/VDD
            continue
        has_in = ('%s Digital Input' % func) in in_names
        has_out = ('%s Digital Output' % func) in out_funcs
        caps['PIN%s' % pin] = ('io' if has_in and has_out
                               else 'out' if has_out
                               else 'in' if has_in else 'none')
    return caps


# Datasheet-confirmed device inventory (SLG46826 DS rev 3.18): logic
# macrocells outside the multifunction blocks. LUT2_3 doubles as PGEN,
# LUT3_6 as Pipe Delay/Ripple Counter (both usable as plain LUTs).
GREENPAK_INVENTORY = {
    # 4x LUT2 (LUT2_3 doubles as PGEN) + 7x LUT3 (LUT3_6 doubles as
    # Pipe Delay/Ripple Counter) + the 8 multifunction-block LUTs
    # (LUT3_7..13 in MULTFUNC_8BIT_1..7, LUT4_0 in MULTFUNC_16BIT_0).
    # MF LUT mode select is 00000 = all-zero default, so no mode bits
    # are needed to use them as LUTs.
    'SLG46826': {'n2': 4, 'n3': 14, 'n4': 1, 'ndff': 0},
}

# Datasheet-confirmed LUT truth-table bit ranges (Table 59 / sections
# 7.2-7.4). These override the decompiler-extracted tables when present:
# the extraction's longest-consecutive-run heuristic mis-captures the
# LUT3_0 slot (it grabs the adjacent 16-bit PGen counter-data range
# 1168-1183 instead of the truth range 1184-1191). Validated bit-exact
# against a real Go Configure design (see analysis/simulate_nvm.py).
# MF-block LUT ranges are datasheet-derived but NOT yet ground-truth
# validated (the reference design does not use MF LUTs).
DATASHEET_TRUTH = {
    'SLG46826': {
        'LUT2': [[1152 + 4 * i + k for k in range(4)] for i in range(4)],
        'LUT3': [[1184 + 8 * i + k for k in range(8)] for i in range(6)]
                + [[1248 + k for k in range(8)]]      # LUT3_6 (pipe dly)
                + [[1344 + k for k in range(8)]]      # LUT3_7 (MF 8BIT_1)
                + [[1376 + k for k in range(8)]]      # LUT3_8 (MF 8BIT_2)
                + [[1416 + k for k in range(8)]]      # LUT3_9 (MF 8BIT_3)
                + [[1448 + k for k in range(8)]]      # LUT3_10 (MF 8BIT_4)
                + [[1488 + k for k in range(8)]]      # LUT3_11 (MF 8BIT_5)
                + [[1520 + k for k in range(8)]]      # LUT3_12 (MF 8BIT_6)
                + [[1560 + k for k in range(8)]],     # LUT3_13 (MF 8BIT_7)
        'LUT4': [[1288 + k for k in range(16)]],       # LUT4_0 (MF 16BIT_0)
    },
}

# Datasheet-confirmed DFF mode of the LUT macrocells (Table 59):
# per BEL: the LUT-or-DFF mode select register, and the DFF control bits
# (latch select, Q/nQ output select, nRST/nSET select, initial polarity)
# which are the high bits of the same truth-table byte. LUT2 slots have
# no reset input; the MF 16BIT_0 block has both nRST and nSET (no
# select bit). LUT2_3 (PGEN) and LUT3_6 (pipe delay) have no DFF mode.
# The DFF port mapping on the fabric wires is fixed by the datasheet
# (Matrix A/B/C/D = IN3/IN2/IN1/IN0 for 16BIT, IN2/IN1/IN0 for 8BIT;
# main LUT3 slots: IN0=CLK, IN1=D, IN2=nRST/nSET; LUT2 slots: IN0=CLK,
# IN1=D) - encoded in greenpak_scaffold().
# Pattern generator (LUT2_3 slot; ground-truth validated by
# LUT2-LUT3.gp6): mode = register 1235; registers 1164-1167 hold the
# pattern size (the GUI "bit range n:0" stores the high index n =
# PATTERN_LEN-1); registers 1168-1183 hold the 16-bit pattern data
# (GUI pattern read LSB-first). CLK = matrix OUT2 (IN0), nRST = OUT3
# (IN1); output = matrix source 17 (LUT2_3_PGEN_OUT).
DATASHEET_PGEN = {
    'SLG46826': {'LUT2_3': {'mode': 1235, 'size': 1164, 'data': 1168}},
}

# MF-block CNT/DLY counters (datasheet Table 59 + section 8 block
# diagrams; the MF-DFF-test.gp6 ground truth covers CNT0's defaults).
# Per block: mode_bits = the block-mode value bits 0..3 (LSB first,
# from the decompiled 0x340 field order; bit 4 = the DFF bit, kept in
# DATASHEET_DFF); cnt_regs = the 4-bit "function and edge mode" field
# LSB ([1:0] = DLY/oneshot/freqdet/CNT, [3:2] = edge), clock source
# (4 bits; 1110 = External via Matrix B, 0010 = 2M OSC1 = the app's
# ground-truth default), initial value (2 bits), output polarity,
# 8-bit counter data. out_src = the block's DLY_CNT_OUT Matrix Input
# Number. Block mode values: 1 = counter only, 3 = LUT -> counter,
# 7 = DFF -> counter (16 for DFF alone, 0 for LUT alone).
DATASHEET_CNT = {
    'SLG46826': {
        'CNT0': {'lut_bel': 'LUT4_0', 'mode_bits': [1280, 1281, 1283, 1284],
                 'cnt_regs': {'mode': 1304, 'clock': 1308, 'init': 1313,
                              'pol': 1312, 'data': 1320},
                 'ext_clk_wire': 'MI_L4_0_2', 'dly_in_wire': 'MI_L4_0_3',
                 'out_src': 47},
        'CNT1': {'lut_bel': 'LUT3_7', 'mode_bits': [1337, 1338, 1340, 1341],
                 'cnt_regs': {'mode': 1356, 'clock': 1352, 'init': 1342,
                              'pol': 1368, 'data': 1360},
                 'ext_clk_wire': 'MI_L3_7_1', 'dly_in_wire': 'MI_L3_7_2',
                 'out_src': 29},
        'CNT2': {'lut_bel': 'LUT3_8', 'mode_bits': [1372, 1373, 1374, 1375],
                 'cnt_regs': {'mode': 1388, 'clock': 1384, 'init': 1392,
                              'pol': 1395, 'data': 1400},
                 'ext_clk_wire': 'MI_L3_8_1', 'dly_in_wire': 'MI_L3_8_2',
                 'out_src': 33},
        'CNT3': {'lut_bel': 'LUT3_9', 'mode_bits': [1409, 1410, 1412, 1413],
                 'cnt_regs': {'mode': 1428, 'clock': 1424, 'init': 1414,
                              'pol': 1440, 'data': 1432},
                 'ext_clk_wire': 'MI_L3_9_1', 'dly_in_wire': 'MI_L3_9_2',
                 'out_src': 34},
        'CNT4': {'lut_bel': 'LUT3_10', 'mode_bits': [1444, 1445, 1446, 1447],
                 'cnt_regs': {'mode': 1460, 'clock': 1456, 'init': 1464,
                              'pol': 1467, 'data': 1472},
                 'ext_clk_wire': 'MI_L3_10_1', 'dly_in_wire': 'MI_L3_10_2',
                 'out_src': 35},
        'CNT5': {'lut_bel': 'LUT3_11', 'mode_bits': [1481, 1482, 1484, 1485],
                 'cnt_regs': {'mode': 1500, 'clock': 1496, 'init': 1486,
                              'pol': 1512, 'data': 1504},
                 'ext_clk_wire': 'MI_L3_11_1', 'dly_in_wire': 'MI_L3_11_2',
                 'out_src': 36},
        'CNT6': {'lut_bel': 'LUT3_12', 'mode_bits': [1516, 1517, 1518, 1519],
                 'cnt_regs': {'mode': 1532, 'clock': 1528, 'init': 1536,
                              'pol': 1539, 'data': 1544},
                 'ext_clk_wire': 'MI_L3_12_1', 'dly_in_wire': 'MI_L3_12_2',
                 'out_src': 37},
        'CNT7': {'lut_bel': 'LUT3_13', 'mode_bits': [1552, 1553, 1554, 1555],
                 'cnt_regs': {'mode': 1572, 'clock': 1568, 'init': 1576,
                              'pol': 1557, 'data': 1584},
                 'ext_clk_wire': 'MI_L3_13_1', 'dly_in_wire': 'MI_L3_13_2',
                 'out_src': 38},
    },
}

DATASHEET_DFF = {
    'SLG46826': {
        'LUT2_0': {'mode': 1232, 'latch': 1155, 'out_sel': 1154, 'init': 1153},
        'LUT2_1': {'mode': 1233, 'latch': 1159, 'out_sel': 1158, 'init': 1157},
        'LUT2_2': {'mode': 1234, 'latch': 1163, 'out_sel': 1162, 'init': 1161},
        'LUT3_0': {'mode': 1236, 'latch': 1191, 'out_sel': 1190,
                   'rst_set': 1189, 'init': 1188},
        'LUT3_1': {'mode': 1238, 'latch': 1199, 'out_sel': 1198,
                   'rst_set': 1197, 'init': 1196},
        'LUT3_2': {'mode': 1239, 'latch': 1207, 'out_sel': 1206,
                   'rst_set': 1205, 'init': 1204},
        'LUT3_3': {'mode': 1240, 'latch': 1215, 'out_sel': 1214,
                   'rst_set': 1213, 'init': 1212},
        'LUT3_4': {'mode': 1241, 'latch': 1223, 'out_sel': 1222,
                   'rst_set': 1221, 'init': 1220},
        'LUT3_5': {'mode': 1242, 'latch': 1231, 'out_sel': 1230,
                   'rst_set': 1229, 'init': 1228},
        'LUT4_0': {'mode': 1282, 'latch': 1303, 'out_sel': 1302, 'init': 1301},
        'LUT3_7': {'mode': 1339, 'latch': 1351, 'out_sel': 1350,
                   'rst_set': 1349, 'init': 1348},
        'LUT3_8': {'mode': 1394, 'latch': 1383, 'out_sel': 1382,
                   'rst_set': 1381, 'init': 1380},
        'LUT3_9': {'mode': 1411, 'latch': 1423, 'out_sel': 1422,
                   'rst_set': 1421, 'init': 1420},
        'LUT3_10': {'mode': 1466, 'latch': 1455, 'out_sel': 1454,
                    'rst_set': 1453, 'init': 1452},
        'LUT3_11': {'mode': 1483, 'latch': 1495, 'out_sel': 1494,
                    'rst_set': 1493, 'init': 1492},
        'LUT3_12': {'mode': 1538, 'latch': 1527, 'out_sel': 1526,
                    'rst_set': 1525, 'init': 1524},
        'LUT3_13': {'mode': 1556, 'latch': 1567, 'out_sel': 1566,
                    'rst_set': 1565, 'init': 1564},
    },
}


def greenpak_dff_ports(belname):
    """DFF-mode port wiring for a LUT macrocell BEL: port name ->
    (fabric wire, direction). Returns {} when the slot has no DFF.

    Alias ports: 'nQ' (the *I cell variants output the inverted Q -
    the hardware "Output Select Q/nQ" bit) shares the Q wire, and
    'nCLK' (the GP_DLATCH* cells' active-low latch enable) shares the
    CLK wire. Only the port the bound cell uses is connected to a net.
    """
    base = _greenpak_dff_base_ports(belname)
    if 'Q' in base:
        base['nQ'] = (base['Q'][0], 'out')
    if 'CLK' in base:
        base['nCLK'] = (base['CLK'][0], 'in')
    return base


def _greenpak_dff_base_ports(belname):
    m2 = re.match(r'^LUT2_(\d)$', belname)
    m3 = re.match(r'^LUT3_(\d+)$', belname)
    m4 = re.match(r'^LUT4_(\d)$', belname)
    if m2 and int(m2.group(1)) <= 2:  # LUT2_3 is the PGEN slot
        i = m2.group(1)
        return {'CLK': ('MI_L2_%s_0' % i, 'in'), 'D': ('MI_L2_%s_1' % i, 'in'),
                'Q': ('MF_L2_%s' % i, 'out')}
    if m3:
        i = int(m3.group(1))
        if i > 6:  # MF 8BIT blocks: D on IN2, nSET/nRST on IN1
            return {'CLK': ('MI_L3_%d_0' % i, 'in'),
                    'nRST': ('MI_L3_%d_1' % i, 'in'),
                    'nSET': ('MI_L3_%d_1' % i, 'in'),
                    'nSR': ('MI_L3_%d_1' % i, 'in'),
                    'D': ('MI_L3_%d_2' % i, 'in'),
                    'Q': ('MF_L3_%d' % i, 'out')}
        if i < 6:  # main LUT3 slots: D on IN1, nSET/nRST on IN2
            return {'CLK': ('MI_L3_%d_0' % i, 'in'),
                    'D': ('MI_L3_%d_1' % i, 'in'),
                    'nRST': ('MI_L3_%d_2' % i, 'in'),
                    'nSET': ('MI_L3_%d_2' % i, 'in'),
                    'nSR': ('MI_L3_%d_2' % i, 'in'),
                    'Q': ('MF_L3_%d' % i, 'out')}
        return {}  # LUT3_6 = pipe delay slot
    if m4:
        i = m4.group(1)
        return {'CLK': ('MI_L4_%s_0' % i, 'in'),
                'nRST': ('MI_L4_%s_1' % i, 'in'),
                'nSET': ('MI_L4_%s_2' % i, 'in'),
                'nSR': ('MI_L4_%s_1' % i, 'in'),
                'D': ('MI_L4_%s_3' % i, 'in'),
                'Q': ('MF_L4_%s' % i, 'out')}
    return {}


def greenpak_scaffold(part="SLG46826V", nvm_path=None, n2=16, n3=16, n4=8, ndff=8):
    """GreenPAK-style fabric: fixed-position logic components connected
    through the switch matrix. yosys greenpak4 cells (GP_2LUT, GP_3LUT,
    GP_DFF*, GP_COUNT8/14) map directly to the macrocell BELs."""
    wires, bels, pips = [], [], []

    # Digital macrocells in a GreenPAK6 device: n x 2-bit LUT, n x 3-bit
    # LUT, n/2 x ACMP/DFF...; numbers per-device, extracted values pending.
    n2 = int(n2); n3 = int(n3); n4 = int(n4); ndff = int(ndff)
    matrix = []
    for i in range(n2):
        bel = {"name": "LUT2_%d" % i, "type": "GP_2LUT", "x": 0, "y": 0, "z": i, "ports": {}}
        for k in range(2):
            w = "MI_L2_%d_%d" % (i, k)
            wires.append({"name": w, "type": "MATRIX", "x": 0, "y": 0})
            bel["ports"]["IN%d" % k] = {"dir": "in", "wire": w}
            matrix.append(w)
        f = "MF_L2_%d" % i
        wires.append({"name": f, "type": "MATRIX", "x": 0, "y": 0})
        bel["ports"]["OUT"] = {"dir": "out", "wire": f}
        matrix.append(f)
        bels.append(bel)
    for i in range(n3):
        bel = {"name": "LUT3_%d" % i, "type": "GP_3LUT", "x": 0, "y": 0, "z": 32 + i, "ports": {}}
        for k in range(3):
            w = "MI_L3_%d_%d" % (i, k)
            wires.append({"name": w, "type": "MATRIX", "x": 0, "y": 0})
            bel["ports"]["IN%d" % k] = {"dir": "in", "wire": w}
            matrix.append(w)
        f = "MF_L3_%d" % i
        wires.append({"name": f, "type": "MATRIX", "x": 0, "y": 0})
        bel["ports"]["OUT"] = {"dir": "out", "wire": f}
        matrix.append(f)
        bels.append(bel)
    for i in range(n4):
        bel = {"name": "LUT4_%d" % i, "type": "GP_4LUT", "x": 0, "y": 0, "z": 48 + i, "ports": {}}
        for k in range(4):
            w = "MI_L4_%d_%d" % (i, k)
            wires.append({"name": w, "type": "MATRIX", "x": 0, "y": 0})
            bel["ports"]["IN%d" % k] = {"dir": "in", "wire": w}
            matrix.append(w)
        f = "MF_L4_%d" % i
        wires.append({"name": f, "type": "MATRIX", "x": 0, "y": 0})
        bel["ports"]["OUT"] = {"dir": "out", "wire": f}
        matrix.append(f)
        bels.append(bel)
    for i in range(ndff):
        d = "MF_DFF_%d" % i
        q = "MF_Q_%d" % i
        wires.append({"name": d, "type": "MATRIX", "x": 0, "y": 0})
        wires.append({"name": q, "type": "MATRIX", "x": 0, "y": 0})
        matrix.append(q)
        bels.append({"name": "DFF_%d" % i, "type": "GP_DFF", "x": 0, "y": 0, "z": 64 + i,
                     "ports": {"D": {"dir": "in", "wire": d},
                               "CLK": {"dir": "in", "wire": "MI_CLK"},
                               "Q": {"dir": "out", "wire": q}}})
    wires.append({"name": "MI_CLK", "type": "MATRIX", "x": 0, "y": 0})
    matrix.append("MI_CLK")

    # constant sources (nextpnr's frontend creates VCC/GND cells with a
    # Y output per constant-driven net - provide several of each)
    for cname, ctype, z0 in (("VCC_NET", "VCC", 96), ("GND_NET", "GND", 104)):
        for k in range(4):
            wires.append({"name": "%s%d" % (cname, k), "type": "MATRIX", "x": 0, "y": 0})
            bels.append({"name": "%s%d" % (ctype, k), "type": ctype,
                         "x": 0, "y": 0, "z": z0 + k,
                         "ports": {"Y": {"dir": "out", "wire": "%s%d" % (cname, k)}}})
            matrix.append("%s%d" % (cname, k))

    # DFF-mode ports on the LUT macrocells: one BEL per macrocell can
    # host either a LUT cell or a DFF cell (the hardware mode select);
    # sharing the BEL makes LUT+DFF double-booking structurally
    # impossible. Only BELs with a datasheet DFF entry get the ports.
    dff_table = DATASHEET_DFF.get(greenpak_base(part), {})
    for bel in bels:
        if bel['name'] not in dff_table:
            continue
        for pname, (wire, pdir) in greenpak_dff_ports(bel['name']).items():
            bel['ports'][pname] = {'dir': pdir, 'wire': wire}

    # Pattern-generator ports on the LUT2_3/PGEN slot (GP_PGEN cells
    # bind CLK/nRST/OUT; the wires are the slot's matrix inputs)
    pgen_table = DATASHEET_PGEN.get(greenpak_base(part), {})
    for bel in bels:
        if bel['name'] in pgen_table:
            bel['ports']['CLK'] = {'dir': 'in', 'wire': 'MI_L2_3_0'}
            bel['ports']['nRST'] = {'dir': 'in', 'wire': 'MI_L2_3_1'}

    # MF-block CNT/DLY counters: one BEL per block, sharing the block's
    # matrix input wires with the LUT/DFF (in "Single CNT/DLY" mode the
    # counter takes Matrix A = DLY_IN/CNT reset and Matrix B = EXT_CLK;
    # the block-mode value combines counter + LUT/DFF usage). The
    # counter output is its own matrix source - a dedicated wire.
    cnt_table = DATASHEET_CNT.get(greenpak_base(part), {})
    for name, ct in cnt_table.items():
        out_w = 'MF_%s_OUT' % name
        wires.append({'name': out_w, 'type': 'MATRIX', 'x': 0, 'y': 0})
        matrix.append(out_w)
        bels.append({'name': name, 'type': 'GP_COUNT8_ADV', 'x': 0, 'y': 0,
                     'z': 200 + int(name[-1]),
                     'ports': {'CLK': {'dir': 'in', 'wire': ct['ext_clk_wire']},
                               'RST': {'dir': 'in', 'wire': ct['dly_in_wire']},
                               'OUT': {'dir': 'out', 'wire': out_w}}})

    # Switch matrix crossbar (real crosspoint set pending extraction).
    # matrix sources: macrocell outputs AND the constant nets - in
    # hardware GND/VDD are matrix sources (Table 21 values 0/63)
    outputs = [w for w in matrix if w.startswith("MF_") and not w.startswith("MF_DFF")]
    outputs += [w for w in matrix if re.match(r'^(VCC|GND)_NET\d+$', w)]
    inputs = [w for w in matrix if w.startswith("MI_") or w.startswith("MF_DFF")]
    for dst in inputs:
        for src in outputs:
            pips.append({"name": "%s.%s" % (src, dst), "src": src, "dst": dst,
                         "x": 0, "y": 0, "delay": 0.05})
    # LUT/DFF outputs also feed DFF data inputs directly
    for dst in ["MF_DFF_%d" % i for i in range(ndff)]:
        for src in outputs:
            pips.append({"name": "%s.%s" % (src, dst), "src": src, "dst": dst,
                         "x": 0, "y": 0, "delay": 0.05})

    # IO pads: GP_IBUF (IN->OUT), GP_OBUF (IN->OUT), GP_IOBUF (IN/OE/OUT/IO)
    pin_names = greenpak_pin_names(part) or ["IO_%d" % i for i in range(20)]
    pad_caps = greenpak_pad_caps(part)
    for p, bel_name in enumerate(pin_names):
        cap = pad_caps.get(bel_name)
        if cap == 'none':
            continue  # not connectable to the matrix (e.g. SCL/SDA)
        has_in = cap != 'out'    # 'io' (default when unknown) or 'in'
        has_out = cap != 'in'
        io_w = "IOPAD%d" % p
        in_w = "IOPAD%d_IN" % p   # fabric -> pad (drives GP_OBUF.IN)
        out_w = "IOPAD%d_OUT" % p  # pad -> fabric (from GP_IBUF.OUT)
        for w in (io_w, in_w, out_w):
            wires.append({"name": w, "type": "IO", "x": 2, "y": p})
        bels.append({"name": bel_name, "type": "IOB" if has_in else "IOB_OUT",
                     "x": 2, "y": p, "z": 0, "pin": bel_name,
                     "ports": {"IO": {"dir": "inout", "wire": io_w},
                               "IN": {"dir": "inout", "wire": in_w},
                               "OUT": {"dir": "inout", "wire": out_w}}})
        # GP_IBUF output feeds the matrix
        if has_in:
            for dst in inputs:
                pips.append({"name": "%s.%s" % (out_w, dst), "src": out_w, "dst": dst,
                             "x": 0, "y": 0, "delay": 0.1})
        # matrix drives GP_OBUF input
        if has_out:
            for src in outputs:
                pips.append({"name": "%s.%s" % (src, in_w), "src": src, "dst": in_w,
                             "x": 0, "y": 0, "delay": 0.1})

    chipdb = {
        "device": part,
        "wires": wires,
        "bels": bels,
        "pips": pips,
        "notes": "GreenPAK scaffold; replace with the per-device switch matrix "
                 "crosspoint table extracted from Go Configure",
    }
    if nvm_path:
        chipdb["nvm"] = greenpak_nvm_section(
            part, nvm_path, bels,
            device_regs=os.path.join(os.path.dirname(__file__), 'device_regs'))
    return chipdb


def matrix_join(part, bels, device_regs):
    """Build the switch-matrix -> NVM join tables from the datasheet-
    extracted device_regs tables (datasheet Tables 21/22 + pin map):

      nvm.matrix.fields  = {dst wire: [lo, hi]}   6-bit connection field
      nvm.matrix.sources = {src wire: value}      Matrix Input Number

    A routed PIP (src -> dst) with both sides mapped encodes to "write
    sources[src] into bits fields[dst]" - the complete GreenPAK matrix
    connection encoding, from datasheet data only.
    """
    def load(name):
        p = os.path.join(device_regs, name)
        return json.load(open(p)) if os.path.exists(p) else None

    base = re.sub(r'[A-Za-z]+$', '', part)
    vp = os.path.join(device_regs, 'variants.json')
    if os.path.exists(vp):
        variants = json.load(open(vp))
        base = variants.get(base, {}).get('base', base)
    inputs = load(base + '_matrix_inputs.json')
    outputs = load(base + '_matrix_outputs.json')
    pins = load(base + '_pins.json')
    pinmodes = load(base + '_pinmodes.json')
    if not (inputs and outputs):
        return None

    by_name = {s['name']: s['value'] for s in inputs['sources']}
    cnt_src = {k: ct['out_src'] for k, ct in
               DATASHEET_CNT.get(base, {}).items()}
    fields = {wire: [6 * out, 6 * out + 5]
              for wire, out in outputs['wire_map'].items()}
    # IO output path: "IOx Digital Output" is itself a matrix output
    # (datasheet Table 22: OUT67-91), so the fabric->pad wire gets the
    # same 6-bit field treatment.
    func2out = {o['function']: o['output'] for o in outputs['outputs']}
    io_oe = {}  # fabric->pad wire -> OE field bits ("IOx Digital Output OE")
    pin_modes = {}  # pad BEL -> per-pin mode register bit positions

    sources = {}
    for bel in bels:
        for port in bel.get('ports', {}).values():
            w = port.get('wire', '')
            m = re.match(r'^MF_L([234])_(\d+)$', w)
            if m:
                # the single Table 21 source named LUT<w>_<i>_...
                cands = {v for n, v in by_name.items()
                         if n.startswith('LUT%s_%s_' % (m.group(1), m.group(2)))}
                if not cands and m.group(1) == '3' and int(m.group(2)) >= 7:
                    # multifunction-block LUT3_7..13: the matrix source
                    # is named after the block, not the LUT
                    n = 'MULTFUNC_8BIT_%d: LUT_DFF_OUT' % (int(m.group(2)) - 6)
                    if n in by_name:
                        cands = {by_name[n]}
                if not cands and m.group(1) == '4':
                    n = 'MULTFUNC_16BIT_0: LUT_DFF_OUT'
                    if n in by_name:
                        cands = {by_name[n]}
                if len(cands) == 1:
                    sources[w] = next(iter(cands))
            elif re.match(r'^MF_CNT\d+_OUT$', w):
                # MF-block counter output: its matrix source value comes
                # from the datasheet counter table (Table 21 DLY_CNT_OUT)
                m = re.match(r'^MF_(CNT\d)_OUT$', w)
                ct = cnt_src.get(m.group(1))
                if ct is not None:
                    sources[w] = ct
            elif re.match(r'^(VCC|GND)_NET\d+$', w):
                v = by_name.get('VDD' if w.startswith('VCC') else 'GND')
                if v is not None:
                    sources[w] = v
        if bel.get('type') in ('IOB', 'IOB_OUT') and pins:
            pkg = next(iter(pins['packages'].values()))
            # IOB names are "PIN<k>"; the pin-map keys are bare numbers
            func = pkg.get(re.sub(r'^PIN', '', bel['name']))
            if not func:
                continue
            v = by_name.get('%s Digital Input' % func)
            if v is not None:
                sources[bel['ports']['OUT']['wire']] = v
            out = func2out.get('%s Digital Output' % func)
            if out is not None:
                fields[bel['ports']['IN']['wire']] = [6 * out, 6 * out + 5]
            oe = func2out.get('%s Digital Output OE' % func)
            if oe is not None:
                io_oe[bel['ports']['IN']['wire']] = [6 * oe, 6 * oe + 5]
            if pinmodes and func in pinmodes.get('pins', {}):
                pm = dict(pinmodes['pins'][func])
                pm['iofunc'] = func
                pin_modes[bel['name']] = pm
    return {
        'model': 'field for matrix output n = bits [6n, 6n+5]; content '
                 '= Matrix Input Number (datasheet Tables 21/22)',
        'fields': fields,
        'sources': sources,
        'io_oe': io_oe,
        'pin_modes': pin_modes,
    }


def greenpak_nvm_section(part, nvm_path, bels, device_regs=None):
    """NVM layout. Datasheet-confirmed ranges (DATASHEET_TRUTH) take
    priority; then curated device_regs/<part>_luts.json (analysis/
    extract_lut_regs.py); otherwise scaffold sequential allocation."""
    # the defaultNVM file is BIT-indexed (one 0/1 line per NVM bit):
    # its entry count is the device bit count, not a register count
    nbits = nvm_register_count(nvm_path)
    free = nbits  # scaffold allocations continue past the default image
    bel_bits = []

    lut_tables = None
    ds_tables = None
    dff_table = {}
    if device_regs:
        # Variants share a base part's ChipPart model in Go Configure
        # (same dispatch, same NVM size); see device_regs/variants.json
        # for known component-inventory differences (e.g. SLG46824 has
        # no temperature sensor but uses the SLG46826 register map).
        base = re.sub(r'[A-Za-z]+$', '', part)
        variants = {}
        vp = os.path.join(device_regs, 'variants.json')
        if os.path.exists(vp):
            with open(vp) as f:
                variants = json.load(f)
        if base in variants:
            base = variants[base]['base']
        ds_tables = DATASHEET_TRUTH.get(base)
        dff_table = DATASHEET_DFF.get(base, {})
        candidates = [part + '_luts.json',
                      re.sub(r'[A-Za-z]+$', '', part) + '_luts.json',
                      base + '_luts.json']
        for cand in candidates:
            rp = os.path.join(device_regs, cand)
            if os.path.exists(rp):
                with open(rp) as f:
                    dr = json.load(f)
                lut_tables = {}
                for mc in dr['macrocells']:
                    t = mc.get('truth_regs') or []
                    n = len(t)
                    if n in (4, 8, 16) and t[-1] - t[0] + 1 == n:
                        kind = {4: 'LUT2', 8: 'LUT3', 16: 'LUT4'}[n]
                        lut_tables.setdefault(kind, []).append(t)
                break

    counts = {}
    for bel in bels:
        t = bel["type"]
        width = {"GP_2LUT": 4, "GP_3LUT": 8, "GP_4LUT": 16}.get(t)
        if width is None:
            continue
        kind = {"GP_2LUT": "LUT2", "GP_3LUT": "LUT3", "GP_4LUT": "LUT4"}[t]
        idx = counts.get(kind, 0)
        counts[kind] = idx + 1
        if ds_tables and idx < len(ds_tables.get(kind, [])):
            truth = ds_tables[kind][idx][:width]
            src = "datasheet Table 59 (validated)"
        elif lut_tables and idx < len(lut_tables.get(kind, [])):
            truth = lut_tables[kind][idx][:width]
            src = "extracted (device_regs)"
        else:
            truth = list(range(free, free + width))
            free += width
            src = "scaffold"
        bel_bits.append({
            "name": bel["name"], "type": t,
            "truth_table_bits": truth, "source": src,
        })
        if bel["name"] in dff_table:
            bel_bits[-1]["dff"] = dff_table[bel["name"]]
        pgen_table = DATASHEET_PGEN.get(greenpak_base(part), {})
        if bel["name"] in pgen_table:
            bel_bits[-1]["pgen"] = pgen_table[bel["name"]]

    # MF-block counter BELs carry their register table directly
    for name, ct in DATASHEET_CNT.get(greenpak_base(part), {}).items():
        bel_bits.append({"name": name, "type": "GP_COUNT8_ADV", "cnt": ct})
    return {
        "bits": max(free, nbits),
        "default": os.path.abspath(nvm_path),
        "bit_order": "register r bit b -> image index r*8+b",
        "bel_bits": bel_bits,
        "matrix": matrix_join(part, bels, device_regs) if device_regs else None,
    }


def list_devices(data_dir):
    """List every part number known to the extracted Go Configure data."""
    import glob
    parts = {}
    models = os.path.join(data_dir, 'gp6_qrc')
    if os.path.isdir(models):
        for gen in ('gpd3models', 'gpd4models', 'gpd5models', 'gpd6models'):
            for m in glob.glob(os.path.join(models, gen, '*', '*_f.gz')):
                part = os.path.basename(m).replace('_f.gz', '')
                parts.setdefault(part, []).append(gen.replace('models', ''))
        for f in glob.glob(os.path.join(models, 'defaultnvm', '*', '*DefaultNVM.txt')):
            part = os.path.basename(f).replace('DefaultNVM.txt', '')
            parts.setdefault(part, []).append('defaultNVM')
    for part in sorted(parts):
        print('%-14s %s' % (part, ', '.join(sorted(set(parts[part])))))
    print('total: %d parts' % len(parts))


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("family", choices=["forgefpga", "greenpak"], nargs="?", default=None)
    p.add_argument("part", nargs="?", default=None)
    p.add_argument("--nvm", help="defaultNVM txt to read the register count from")
    p.add_argument("--x", type=int, default=2, help="forgefpga: tile grid X")
    p.add_argument("--y", type=int, default=1, help="forgefpga: tile grid Y")
    p.add_argument("--slices", type=int, default=4, help="forgefpga: slices/tile")
    p.add_argument("--n2", type=int, default=None,
                   help="greenpak: 2-bit LUT count (datasheet default "
                        "for known parts)")
    p.add_argument("--n3", type=int, default=None,
                   help="greenpak: 3-bit LUT count (datasheet default "
                        "for known parts)")
    p.add_argument("--n4", type=int, default=None,
                   help="greenpak: 4-bit LUT count (datasheet default "
                        "for known parts)")
    p.add_argument("--ndff", type=int, default=None,
                   help="greenpak: DFF count (datasheet default "
                        "for known parts)")
    p.add_argument("--list", action="store_true",
                   help="list part numbers from the extracted data")
    p.add_argument("--data-dir", default=os.path.join(os.path.dirname(__file__), '..',
                                                      'analysis', 'extracted'),
                   help="root of extracted Go Configure data")
    args = p.parse_args()

    if args.list:
        list_devices(args.data_dir)
        return
    if not args.family or not args.part:
        p.error("family and part required (use --list to see parts)")

    if args.family == "forgefpga":
        chipdb = forgefpga_scaffold(args.part, args.x, args.y, args.slices)
    else:
        # datasheet-confirmed inventory for known parts; CLI --n2/--n3/
        # --n4/--ndff still override for experimentation
        base = re.sub(r'[A-Za-z]+$', '', args.part)
        vp = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          'device_regs', 'variants.json')
        if os.path.exists(vp):
            with open(vp) as f:
                variants = json.load(f)
            base = variants.get(base, {}).get('base', base)
        inv = GREENPAK_INVENTORY.get(base, {})
        n2 = args.n2 if args.n2 is not None else inv.get('n2', 16)
        n3 = args.n3 if args.n3 is not None else inv.get('n3', 16)
        n4 = args.n4 if args.n4 is not None else inv.get('n4', 8)
        ndff = args.ndff if args.ndff is not None else inv.get('ndff', 8)
        chipdb = greenpak_scaffold(args.part, args.nvm, n2, n3, n4, ndff)
    json.dump(chipdb, sys.stdout, indent=1)
    print()


if __name__ == "__main__":
    main()
