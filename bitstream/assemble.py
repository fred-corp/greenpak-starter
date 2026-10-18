#!/usr/bin/env python3
"""Assemble a flashable GreenPAK/ForgeFPGA NVM bitstream from a routed
nextpnr design.

Reverse-engineered NVM model (see docs/findings.md, "NVM architecture"):

  * The device bitstream is a flat bit array (QList<bool> in Go Configure);
    every register ID used by the app (NVM_CLASS::setValueToNVM) is an
    absolute index into that array.
  * Every macrocell property is a list of bit indexes + the value to write
    (selection maps: NvmMap<Enum> -> (nvm value, label)).
  * LUT truth tables: one bit index per truth-table entry
    (LUT_CLASS stores QList<int> regIds; SetLUT::applyChanges writes
    each bit via NVM_CLASS::setValueToNVM(regId, value)).

Inputs:
  --chipdb   chipdb JSON with an "nvm" section (bit count, per-BEL and
             per-PIP bit index tables; gen_chipdb.py emits scaffold
             assignments today, extracted tables will replace them)
  --routed   nextpnr --write JSON (placed and routed design)
  --out      output prefix: .nvm (raw bit image), .bin (packed bytes,
             MSB-first per register), .hex (Intel HEX)

Usage:
  assemble.py --chipdb slg46826.json --routed routed.json --out dm8200
"""

import argparse
import json
import sys


# ------------------------------------------------------------------ helpers --

def intel_hex(data, out):
    """Write packed NVM bytes as Intel HEX (16-bit addresses, record type 00)."""
    def rec(addr, rectype, payload):
        chk = (sum(payload) + len(payload) + (addr >> 8) + (addr & 0xff) + rectype) & 0xff
        body = ''.join('%02X' % b for b in payload)
        out.write(':%02X%04X%02X%s%02X\n' % (len(payload), addr, rectype, body, ((~chk + 1) & 0xff)))
    for base in range(0, len(data), 16):
        rec(base, 0x00, list(data[base:base + 16]))
    rec(0, 0x01, [])


def bits_to_bytes(bits):
    """Pack a little-endian-bit-index array into bytes (Go Configure writes
    register r bit b at image index (r*8 + b); we keep that convention)."""
    out = bytearray((len(bits) + 7) // 8)
    for i, b in enumerate(bits):
        if b:
            out[i // 8] |= 1 << (i % 8)
    return bytes(out)


# --------------------------------------------------------------- assembler --

def load_default(chipdb):
    """Start from the device default NVM bits when provided."""
    nvm = chipdb.get('nvm', {})
    bits = [False] * int(nvm.get('bits', 0))
    default = nvm.get('default')
    if default:
        with open(default) as f:
            for line in f:
                parts = line.split()
                if len(parts) >= 2 and parts[0].isdigit():
                    idx = int(parts[0])
                    val = int(parts[1])
                    for b in range(8):
                        if idx * 8 + b < len(bits):
                            bits[idx * 8 + b] = bool(val >> b & 1)
    return bits


def cell_truth(cell):
    """Extract a LUT truth table (list of 0/1) from a nextpnr cell."""
    init = cell.get('attrs', {}).get('INIT') or cell.get('params', {}).get('INIT')
    if init is None:
        return None
    init = int(init, 0) if isinstance(init, str) else int(init)
    # count used inputs from ports; nextpnr pads smaller LUTs into LUT4 INIT
    return init


def assemble(chipdb, routed):
    bits = load_default(chipdb)
    nvm = chipdb.get('nvm', {})
    bel_nvm = {b['name']: b for b in nvm.get('bel_bits', [])}
    pip_nvm = {p['name']: p for p in nvm.get('pip_bits', [])}
    stats = {'cells': 0, 'pips': 0, 'bits_set': 0}

    def setbit(idx):
        if idx < len(bits):
            if not bits[idx]:
                bits[idx] = True
                stats['bits_set'] += 1
        else:
            raise SystemExit('NVM bit index %d out of range (device has %d bits)'
                             % (idx, len(bits)))

    def clearbit(idx):
        if idx < len(bits):
            if bits[idx]:
                bits[idx] = False
                stats['bits_set'] += 1

    # nextpnr report format (uarch postRoute --vopt rpt=...):
    #   {"cells": {name: {"type", "bel", "params": {INIT: <int>, ...}}},
    #    "pips":  {name: {"net", "src", "dst"}}}
    for cname, cell in routed.get('cells', {}).items():
        bel = cell.get('bel')
        if not bel:
            continue
        entry = bel_nvm.get(bel)
        if not entry:
            continue
        stats['cells'] += 1
        init = cell.get('params', {}).get('INIT')
        if init is not None and 'truth_table_bits' in entry:
            init = str(init)
            try:
                init = int(init, 0)
            except ValueError:
                init = int(init, 2)  # raw binary digits (e.g. "0001")
            for i, idx in enumerate(entry['truth_table_bits']):
                if (init >> i) & 1:
                    setbit(idx)
                else:
                    clearbit(idx)

    for pname, pip in routed.get('pips', {}).items():
        entry = pip_nvm.get(pname)
        if entry and 'bit' in entry:
            stats['pips'] += 1
            setbit(entry['bit'])

    return bits, stats


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--chipdb', required=True)
    ap.add_argument('--routed', required=True)
    ap.add_argument('--out', required=True, help='output file prefix')
    args = ap.parse_args()

    chipdb = json.load(open(args.chipdb))
    routed = json.load(open(args.routed))

    bits, stats = assemble(chipdb, routed)
    data = bits_to_bytes(bits)

    with open(args.out + '.bin', 'wb') as f:
        f.write(data)
    with open(args.out + '.hex', 'w') as f:
        intel_hex(data, f)
    with open(args.out + '.nvm', 'w') as f:
        f.write(''.join('1' if b else '0' for b in bits) + '\n')

    print('assembled %s: %d bits (%d bytes), %d cells mapped, %d bits changed'
          % (args.out, len(bits), len(data), stats['cells'], stats['bits_set']))


if __name__ == '__main__':
    main()
