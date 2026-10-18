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


def greenpak_pin_names(part):
    base = re.sub(r'[A-Za-z]+$', '', part)
    vp = os.path.join(os.path.dirname(__file__), 'device_regs', 'variants.json')
    if os.path.exists(vp):
        with open(vp) as f:
            variants = json.load(f)
        base = variants.get(base, {}).get('base', base)
    return GREENPAK_PINOUTS.get(base)


# Datasheet-confirmed device inventory (SLG46826 DS rev 3.18): logic
# macrocells outside the multifunction blocks. LUT2_3 doubles as PGEN,
# LUT3_6 as Pipe Delay/Ripple Counter (both usable as plain LUTs).
GREENPAK_INVENTORY = {
    'SLG46826': {'n2': 4, 'n3': 7, 'n4': 0, 'ndff': 0},
}

# Datasheet-confirmed LUT truth-table bit ranges (Table 59 / sections
# 7.2-7.4). These override the decompiler-extracted tables when present:
# the extraction's longest-consecutive-run heuristic mis-captures the
# LUT3_0 slot (it grabs the adjacent 16-bit PGen counter-data range
# 1168-1183 instead of the truth range 1184-1191). Validated bit-exact
# against a real Go Configure design (see analysis/simulate_nvm.py).
DATASHEET_TRUTH = {
    'SLG46826': {
        'LUT2': [[1152 + 4 * i + k for k in range(4)] for i in range(4)],
        'LUT3': [[1184 + 8 * i + k for k in range(8)] for i in range(6)]
                + [[1248 + k for k in range(8)]],
    },
}


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

    # Switch matrix crossbar (real crosspoint set pending extraction).
    outputs = [w for w in matrix if w.startswith("MF_") and not w.startswith("MF_DFF")]
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
    for p, bel_name in enumerate(pin_names):
        io_w = "IOPAD%d" % p
        in_w = "IOPAD%d_IN" % p   # fabric -> pad (drives GP_OBUF.IN)
        out_w = "IOPAD%d_OUT" % p  # pad -> fabric (from GP_IBUF.OUT)
        for w in (io_w, in_w, out_w):
            wires.append({"name": w, "type": "IO", "x": 2, "y": p})
        bels.append({"name": bel_name, "type": "IOB", "x": 2, "y": p, "z": 0,
                     "pin": bel_name,
                     "ports": {"IO": {"dir": "inout", "wire": io_w},
                               "IN": {"dir": "inout", "wire": in_w},
                               "OUT": {"dir": "inout", "wire": out_w}}})
        # GP_IBUF output feeds the matrix
        for dst in inputs:
            pips.append({"name": "%s.%s" % (out_w, dst), "src": out_w, "dst": dst,
                         "x": 0, "y": 0, "delay": 0.1})
        # matrix drives GP_OBUF input
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
    if not (inputs and outputs):
        return None

    by_name = {s['name']: s['value'] for s in inputs['sources']}
    fields = {wire: [6 * out, 6 * out + 5]
              for wire, out in outputs['wire_map'].items()}
    # IO output path: "IOx Digital Output" is itself a matrix output
    # (datasheet Table 22: OUT67-91), so the fabric->pad wire gets the
    # same 6-bit field treatment.
    func2out = {o['function']: o['output'] for o in outputs['outputs']}
    io_oe = {}  # fabric->pad wire -> OE field bits ("IOx Digital Output OE")

    sources = {}
    for bel in bels:
        for port in bel.get('ports', {}).values():
            w = port.get('wire', '')
            m = re.match(r'^MF_L([234])_(\d+)$', w)
            if m:
                # the single Table 21 source named LUT<w>_<i>_...
                cands = {v for n, v in by_name.items()
                         if n.startswith('LUT%s_%s_' % (m.group(1), m.group(2)))}
                if len(cands) == 1:
                    sources[w] = next(iter(cands))
            elif re.match(r'^(VCC|GND)_NET\d+$', w):
                v = by_name.get('VDD' if w.startswith('VCC') else 'GND')
                if v is not None:
                    sources[w] = v
        if bel.get('type') == 'IOB' and pins:
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
    return {
        'model': 'field for matrix output n = bits [6n, 6n+5]; content '
                 '= Matrix Input Number (datasheet Tables 21/22)',
        'fields': fields,
        'sources': sources,
        'io_oe': io_oe,
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
