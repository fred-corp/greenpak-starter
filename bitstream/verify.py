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
            attrs = c.get('attributes', {})
            params = c.get('parameters', {})
            cells[cname] = {
                'type': c['type'],
                'conns': c['connections'],
                'init': params.get('INIT') or attrs.get('INIT'),
            }
    return ports, cells


def parse_init(init, width):
    """yosys/nextpnr emit truth-table INIT as raw binary digits whose
    length is the truth-table size; all-digit strings must not be read
    as decimal."""
    if init is None:
        return 0
    s = str(init)
    nbits = 1 << width
    if len(s) == nbits and set(s) <= {'0', '1'}:
        return int(s, 2)
    return int(s, 0)


def sim_yosys(ports, cells, invals, dffstate=None):
    """Simulate the GP-cell netlist; invals maps '<port>' or '<port>[i]'
    to 0/1. dffstate: {cellname: Q} for sequential cells (async set/
    reset applied inside). Returns (outs, dff_inputs) where dff_inputs
    maps each DFF cell to its sampled (D, nRST, nSET) for the edge."""
    if dffstate is None:
        dffstate = {}
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
        for cname, c in cells.items():
            t = c['type']
            if is_dff_type(t):
                q = dffstate.get(cname, 0)
                if 'nRST' in c['conns'] and nets.get(c['conns']['nRST'][0], 1) == 0:
                    q = 0
                if 'nSET' in c['conns'] and nets.get(c['conns']['nSET'][0], 1) == 0:
                    q = 1
                obit = c['conns'].get('Q', [None])[0]
                if obit is not None and nets.get(obit) != q:
                    nets[obit] = q
                    changed = True
                continue
            if t in ('GP_2LUT', 'GP_3LUT', 'GP_4LUT'):
                width = int(t[3])
                idx = 0
                for k in range(width):
                    bit = c['conns'].get('IN%d' % k, [None])[0]
                    if isinstance(bit, int):
                        idx |= nets.get(bit, 0) << k
                init = c['init']
                out = (parse_init(init, width) >> idx) & 1
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
    # DFF input samples for the clock edge
    dff_in = {}
    for cname, c in cells.items():
        if not is_dff_type(c['type']):
            continue
        d = c['conns'].get('D', [None])[0]
        dff_in[cname] = (
            nets.get(d, 0) if isinstance(d, int) else 0,
            nets.get(c['conns']['nRST'][0], 1) if 'nRST' in c['conns'] else 1,
            nets.get(c['conns']['nSET'][0], 1) if 'nSET' in c['conns'] else 1,
        )
    return outs, dff_in


def is_dff_type(t):
    return t.startswith('GP_DFF') or t.startswith('GP_DLATCH')


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

    # LUTs and DFFs decoded from the image. A macrocell whose DFF mode
    # select bit is set is a DFF; otherwise it is a LUT (truth table).
    dffs = {}
    luts = {}
    for belname, bb in bel_bits.items():
        p = bel_ports.get(belname, {})
        d = bb.get('dff')
        if d and bits[d['mode']]:
            dffs[belname] = {
                'clk': p.get('CLK', {}).get('wire'),
                'd': p.get('D', {}).get('wire'),
                'nrst': p.get('nRST', {}).get('wire'),
                'nset': p.get('nSET', {}).get('wire'),
                'q': p.get('Q', {}).get('wire'),
                'init': bits[d['init']],
                'nq': bits[d['out_sel']],
            }
            continue
        if 'OUT' not in p:
            continue
        ins = [p['IN%d' % k]['wire'] for k in range(4) if 'IN%d' % k in p]
        truth = sum(bits[i] << k for k, i in enumerate(bb['truth_table_bits']))
        luts[belname] = {'ins': ins, 'truth': truth,
                         'out': p['OUT']['wire']}

    def field_val(dstwire):
        lo = fields[dstwire][0]
        return sum(bits[lo + b] << b for b in range(6))

    # matrix "input" fields: every field except the IO output/OE wires
    # (IOPAD*_IN) - their value selects the source wire that drives
    # the destination
    mi_fields = {w: f for w, f in fields.items() if not w.startswith('IOPAD')}
    vdd_wires = [w for w in sources if w.startswith('VCC_NET')]

    def eval_image(driven, dffq=None):
        vals = dict(driven)
        if dffq is None:
            dffq = {}
        for w in vdd_wires:
            vals[w] = 1  # VDD source
        for _ in range(20):
            changed = False
            # matrix connections: each field selects its driving source
            for w in mi_fields:
                srcw = inv_sources.get(field_val(w))
                nv = vals.get(srcw, 0) if srcw is not None else 0
                if vals.get(w) != nv:
                    vals[w] = nv
                    changed = True
            # DFF macrocells (clock edges handled by the caller)
            for belname, d in dffs.items():
                q = dffq.get(belname, d['init'])
                if d['nrst'] and vals.get(d['nrst'], 1) == 0:
                    q = 0
                if d['nset'] and vals.get(d['nset'], 1) == 0:
                    q = 1
                if d['nq']:
                    q ^= 1
                if vals.get(d['q']) != q:
                    vals[d['q']] = q
                    changed = True
            # LUTs
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

    def build_driven(invals):
        driven = {}
        for b, label in top_in:
            bel = bit_pad.get(b)
            if bel and bel in iob_wires:
                driven[iob_wires[bel][0]] = invals[label] & 1
        return driven

    def image_outputs(vals):
        out = {}
        for b, label in top_out:
            bel = bit_pad.get(b)
            if not bel or bel not in iob_wires:
                continue
            w = iob_wires[bel][1]
            if w in fields:
                out[label] = vals.get(inv_sources.get(field_val(w)), 0)
        return out

    ref_dffs = {cname: c for cname, c in cells.items() if is_dff_type(c['type'])}
    mism = 0

    if ref_dffs or dffs:
        # ---- sequential: cycle-based comparison -----------------------
        # clock = the top-level input(s) driving DFF CLK pins
        clk_bits = set()
        for c in ref_dffs.values():
            cb = c['conns'].get('CLK', c['conns'].get('nCLK', [None]))[0]
            if isinstance(cb, int):
                clk_bits.add(cb)
        clk_labels = {label for b, label in top_in if b in clk_bits}
        other = [(b, l) for b, l in top_in if l not in clk_labels]
        m = len(other)
        cycles = 24  # enough for 4-bit counter rollovers
        combos = 1 << m if m <= 12 else 64  # cap for wide inputs
        for combo in range(combos):
            base = {l: (combo >> i) & 1 for i, (b, l) in enumerate(other)}
            # initial states (INIT)
            rstate = {cname: (1 if str(c['init']).strip('"') == '1' else 0)
                      for cname, c in ref_dffs.items()}
            istate = {bn: d['init'] for bn, d in dffs.items()}
            for t in range(cycles):
                # clock low: settle, sample DFF data inputs
                low = dict(base)
                for l in clk_labels:
                    low[l] = 0
                _, rin = sim_yosys(ports, cells, low, rstate)
                ivals = eval_image(build_driven(low), istate)
                # rising edge: update states
                rstate = {cname: (0 if v[1] == 0 else
                                  1 if v[2] == 0 else v[0])
                          for cname, v in rin.items()}
                for bn, d in dffs.items():
                    q = ivals.get(d['d'], 0)
                    if d['nrst'] and ivals.get(d['nrst'], 1) == 0:
                        q = 0
                    if d['nset'] and ivals.get(d['nset'], 1) == 0:
                        q = 1
                    istate[bn] = q
                # clock high: settle, compare outputs
                high = dict(base)
                for l in clk_labels:
                    high[l] = 1
                ref, _ = sim_yosys(ports, cells, high, rstate)
                vals = eval_image(build_driven(high), istate)
                img = image_outputs(vals)
                for label in ref:
                    if label in img and img[label] != ref[label]:
                        mism += 1
                        if mism < 8:
                            print('MISMATCH %s: yosys=%d image=%d '
                                  '(combo %d, cycle %d)'
                                  % (label, ref[label], img[label], combo, t))
        print('sequential check: %d input combos x %d clock cycles '
              '(%d DFFs): %d mismatches'
              % (combos, cycles, len(ref_dffs), mism))
        return 1 if mism else 0

    # ---- combinational: exhaustive comparison --------------------------
    n = len(top_in)
    for combo in range(1 << n):
        invals = {label: (combo >> i) & 1 for i, (b, label) in enumerate(top_in)}
        ref, _ = sim_yosys(ports, cells, invals)
        vals = eval_image(build_driven(invals))
        img_out = image_outputs(vals)
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
