#!/usr/bin/env python3
"""Verify an assembled GreenPAK NVM image against the source netlist.

Round-trip proof for the bitstream assembler: decode the assembled NVM
image (with the same datasheet-extracted tables the assembler used to
ENCODE it) back into a netlist, simulate it, and compare against a
simulation of the yosys JSON netlist (GP_2LUT/GP_3LUT/GP_IBUF/GP_OBUF
cells) for every combination of the top-level input ports.

Verilog -> yosys -> nextpnr -> assemble -> NVM image -> (decode +
simulate) must behave identically to the synthesized netlist. Combined
with the Go Configure ground-truth validation (analysis/
simulate_nvm.py on a real .gp6 design), the encoding is validated in
both directions.

Usage:
  verify.py --chipdb slg46826.json --routed routed.json \
            --json out.json --image top.bin
"""

import argparse
import json
import sys


def load_image_bin(path, nbits):
    data = open(path, 'rb').read()
    assert len(data) * 8 >= nbits, 'image too small: %d bytes' % len(data)
    return [(data[i // 8] >> (i % 8)) & 1 for i in range(nbits)]


def yosys_reference(path):
    """Parse the yosys JSON netlist into (ports, cells)."""
    data = json.load(open(path))
    mod = data['modules']['top']
    ports = mod['ports']
    cells = {}
    for cname, c in mod['cells'].items():
        if c['type'].startswith('GP_'):
            cells[cname] = {
                'type': c['type'],
                'conns': c['connections'],
                'init': c.get('attributes', {}).get('INIT'),
            }
    return ports, cells


def sim_yosys(ports, cells, invals):
    """Simulate the GP-cell netlist; invals maps '<port>' or '<port>[i]'
    to 0/1. Returns {port: value}."""
    nets = {}
    for pname, p in ports.items():
        if p['direction'] != 'input':
            continue
        for i, b in enumerate(p['bits']):
            if not isinstance(b, int):
                continue
            label = pname if len(p['bits']) == 1 else '%s[%d]' % (pname, i)
            nets[b] = invals[label] & 1
    for _ in range(30):
        changed = False
        for c in cells.values():
            t = c['type']
            if t in ('GP_2LUT', 'GP_3LUT'):
                width = int(t[3])
                idx = 0
                for k in range(width):
                    bit = c['conns'].get('IN%d' % k, [None])[0]
                    if isinstance(bit, int):
                        idx |= nets.get(bit, 0) << k
                init = c['init']
                init = int(init, 0) if isinstance(init, str) else int(init or 0)
                out = (init >> idx) & 1
                obit = c['conns']['OUT'][0]
                if nets.get(obit) != out:
                    nets[obit] = out
                    changed = True
            elif t in ('GP_IBUF', 'GP_OBUF'):
                src = c['conns']['IN' if t == 'GP_IBUF' else 'IN'][0]
                dst = c['conns']['OUT'][0]
                v = nets.get(src, 0)
                if nets.get(dst) != v:
                    nets[dst] = v
                    changed = True
        if not changed:
            break
    outs = {}
    for pname, p in ports.items():
        if p['direction'] != 'output':
            continue
        outs[pname] = sum((nets.get(b, 0) & 1) << i
                          for i, b in enumerate(p['bits']))
    return outs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--chipdb', required=True)
    ap.add_argument('--routed', required=True, help='nextpnr --vopt rpt file')
    ap.add_argument('--json', required=True, help='yosys out.json')
    ap.add_argument('--image', required=True, help='assembled .bin')
    args = ap.parse_args()

    chipdb = json.load(open(args.chipdb))
    routed = json.load(open(args.routed))
    bits = load_image_bin(args.image, chipdb['nvm']['bits'])
    ports, cells = yosys_reference(args.json)

    matrix = chipdb['nvm']['matrix']
    fields = matrix['fields']       # dst wire -> [lo, hi]
    sources = matrix['sources']     # src wire -> matrix input number
    inv_sources = {v: k for k, v in sources.items()}
    bel_ports = {b['name']: b.get('ports', {}) for b in chipdb['bels']}
    bel_bits = {b['name']: b for b in chipdb['nvm']['bel_bits']}

    # LUTs decoded from the image: truth table + input wires
    luts = {}
    for belname, bb in bel_bits.items():
        p = bel_ports.get(belname, {})
        if 'OUT' not in p:
            continue
        ins = [p['IN%d' % k]['wire'] for k in range(4) if 'IN%d' % k in p]
        truth = sum(bits[i] << k for k, i in enumerate(bb['truth_table_bits']))
        luts[belname] = {'ins': ins, 'truth': truth,
                         'out': p['OUT']['wire']}

    def field_val(dstwire):
        lo = fields[dstwire][0]
        return sum(bits[lo + b] << b for b in range(6))

    def eval_image(driven):
        vals = dict(driven)
        for _ in range(20):
            changed = False
            for l in luts.values():
                idx = 0
                for k, w in enumerate(l['ins']):
                    idx |= (vals.get(w, 0) & 1) << k
                out = (l['truth'] >> idx) & 1
                if vals.get(l['out']) != out:
                    vals[l['out']] = out
                    changed = True
            if not changed:
                break
        return vals

    # pad wires per IOB BEL
    iob_wires = {b['name']: (b['ports']['OUT']['wire'],   # pad -> fabric
                             b['ports']['IN']['wire'])    # fabric -> pad
                 for b in chipdb['bels'] if b['type'] == 'IOB'}

    # netbit -> (direction, BEL) from the routed report (the report's
    # GP_IBUF/GP_OBUF cells carry the pad BEL each buffer landed on)
    netbels = {}
    for cname, c in routed['cells'].items():
        if c.get('type') in ('GP_IBUF', 'GP_OBUF') and c.get('bel'):
            netbels[cname] = c
    # netbit -> pad BEL via the yosys cell connections
    bit_pad = {}
    for cname, rc in netbels.items():
        yc = cells.get(cname)
        if not yc:
            continue
        if rc['type'] == 'GP_IBUF':
            bit = yc['conns']['IN'][0]     # driven by a top input port
        else:
            bit = yc['conns']['OUT'][0]    # drives a top output port
        bit_pad[bit] = rc['bel']

    top_in, top_out = [], []
    for pname, p in ports.items():
        for i, b in enumerate(p['bits']):
            if not isinstance(b, int):
                continue
            label = pname if len(p['bits']) == 1 else '%s[%d]' % (pname, i)
            (top_in if p['direction'] == 'input' else top_out).append((b, label))

    n = len(top_in)
    mism = 0
    for combo in range(1 << n):
        invals = {label: (combo >> i) & 1 for i, (b, label) in enumerate(top_in)}
        ref = sim_yosys(ports, cells, invals)

        driven = {}
        for i, (b, label) in enumerate(top_in):
            bel = bit_pad.get(b)
            if bel and bel in iob_wires:
                driven[iob_wires[bel][0]] = (combo >> i) & 1
        vals = eval_image(driven)

        img_out = {}
        for b, label in top_out:
            bel = bit_pad.get(b)
            if not bel or bel not in iob_wires:
                continue
            w = iob_wires[bel][1]
            if w in fields:
                img_out[label] = vals.get(inv_sources.get(field_val(w)), 0)

        for label in ref:
            if label in img_out and img_out[label] != ref[label]:
                mism += 1
                if mism < 8:
                    print('MISMATCH %s: yosys=%d image=%d (combo %d)'
                          % (label, ref[label], img_out[label], combo))
    print('checked %d input combinations (%d inputs, %d outputs): %d mismatches'
          % (1 << n if n < 20 else -1, n, len(top_out), mism))
    return 1 if mism else 0


if __name__ == '__main__':
    sys.exit(main())
