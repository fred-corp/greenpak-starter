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
  --out      output prefix: .nvm (raw bit string), .bin (packed bytes,
             LSB-first bit per index), .hex (Intel HEX), .nvm.txt
             (Go Configure Software Hub import format: one line per
             NVM bit, "index\t\tvalue\t\t//")

Usage:
  assemble.py --chipdb slg46826.json --routed routed.json --out dm8200
"""

import argparse
import json
import os
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

def load_default(chipdb, chipdb_path):
    """Start from the device default NVM bits when provided.

    The Go Configure defaultNVM dump is BIT-indexed: one line per NVM
    bit, "<bitindex> <0|1> //", covering the whole device image.
    Relative "default" paths resolve against the chipdb file's
    directory, so chipdbs are relocatable.
    """
    nvm = chipdb.get('nvm', {})
    bits = [False] * int(nvm.get('bits', 0))
    default = nvm.get('default')
    if default and not os.path.isabs(default):
        cand = os.path.join(os.path.dirname(os.path.abspath(chipdb_path)),
                            default)
        default = cand if os.path.exists(cand) else default
    if default and os.path.exists(default):
        with open(default) as f:
            for line in f:
                parts = line.split()
                if len(parts) >= 2 and parts[0].isdigit():
                    idx = int(parts[0])
                    if idx < len(bits):
                        bits[idx] = bool(int(parts[1]) & 1)
    return bits


def cell_truth(cell):
    """Extract a LUT truth table (list of 0/1) from a nextpnr cell."""
    init = cell.get('attrs', {}).get('INIT') or cell.get('params', {}).get('INIT')
    if init is None:
        return None
    init = int(init, 0) if isinstance(init, str) else int(init)
    # count used inputs from ports; nextpnr pads smaller LUTs into LUT4 INIT
    return init


def parse_param(value, width):
    """Parse a cell parameter value from the routed report: yosys
    emits fixed-width params as raw binary digit strings (e.g.
    PATTERN_LEN as '01000'); anything else parses as a number."""
    if value is None:
        return None
    s = str(value)
    if len(s) == width and set(s) <= {'0', '1'}:
        return int(s, 2)
    return int(s, 0)


def parse_init(init, width):
    """Parse a LUT INIT value from the nextpnr report. yosys emits
    truth-table INIT as a raw binary digit string ("10110010") whose
    length is the truth-table size; int(s, 0) would mis-read that as
    decimal, so parse as binary when the digits and length match the
    cell's truth-table width."""
    if init is None:
        return 0
    s = str(init)
    nbits = 1 << width
    if len(s) == nbits and set(s) <= {'0', '1'}:
        return int(s, 2)
    return int(s, 0)


def assemble(chipdb, routed, chipdb_path=''):
    bits = load_default(chipdb, chipdb_path)
    nvm = chipdb.get('nvm', {})
    bel_nvm = {b['name']: b for b in nvm.get('bel_bits', [])}
    pip_nvm = {p['name']: p for p in nvm.get('pip_bits', [])}
    matrix = nvm.get('matrix') or {}
    fields = matrix.get('fields', {})
    msrc = matrix.get('sources', {})
    io_oe = matrix.get('io_oe', {})
    pin_modes = matrix.get('pin_modes', {})
    # fabric->pad wire -> pad BEL name (for the static-OE register lookup)
    wire_pad = {}
    for b in chipdb.get('bels', []):
        if b.get('type') in ('IOB', 'IOB_OUT'):
            w = b.get('ports', {}).get('IN', {}).get('wire')
            if w:
                wire_pad[w] = b['name']
    stats = {'cells': 0, 'pips': 0, 'bits_set': 0,
             'connections': 0, 'unmapped_pips': 0, 'io_outputs': 0}
    written_fields = set()   # matrix fields written by routed pips

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
        ctype = cell.get('type', '')
        if ctype == 'GP_PGEN':
            # Pattern generator (LUT2_3 slot; ground-truth validated):
            # mode register 1235; registers 1164-1167 = pattern size
            # (PATTERN_LEN-1, i.e. the GUI "bit range" high index);
            # registers 1168-1183 = the 16-bit PATTERN_DATA (GUI
            # pattern read LSB-first). CLK/nRST are matrix
            # connections on the slot's IN0/IN1 wires.
            pg = entry.get('pgen')
            if not pg:
                print('  warn: BEL %s has no PGEN mode (cell %s)'
                      % (bel, ctype), file=sys.stderr)
                continue
            setbit(pg['mode'])
            plen = parse_param(cell.get('params', {}).get('PATTERN_LEN'), 5)
            pdata = parse_param(cell.get('params', {}).get('PATTERN_DATA'), 16)
            size = (plen - 1) if plen is not None else 15
            size = max(0, min(15, size))
            for b in range(4):
                if (size >> b) & 1:
                    setbit(pg['size'] + b)
                else:
                    clearbit(pg['size'] + b)
            data = (pdata & 0xFFFF) if pdata is not None else 0
            for b in range(16):
                if (data >> b) & 1:
                    setbit(pg['data'] + b)
                else:
                    clearbit(pg['data'] + b)
            stats['pgens'] = stats.get('pgens', 0) + 1
            continue
        if ctype.startswith('GP_DFF') or ctype.startswith('GP_DLATCH'):
            # DFF/latch mode of a LUT macrocell: set the mode select
            # and the DFF control bits ([7:4] of the truth byte;
            # [3:1] for LUT2 slots). The truth table itself is not
            # used in DFF mode.
            dff = entry.get('dff')
            if not dff:
                print('  warn: BEL %s has no DFF mode (cell %s)'
                      % (bel, ctype), file=sys.stderr)
                continue
            setbit(dff['mode'])
            def setflag(idx, flag):
                if flag:
                    setbit(idx)
                else:
                    clearbit(idx)
            setflag(dff['latch'], ctype.startswith('GP_DLATCH'))
            setflag(dff['out_sel'], ctype.endswith('I'))  # nQ output
            if 'rst_set' in dff:
                # nSET (GP_DFFS/GP_DFFSI) selects SET; nRST/nSR -> RST
                setflag(dff['rst_set'], ctype in ('GP_DFFS', 'GP_DFFSI',
                                                 'GP_DLATCHS', 'GP_DLATCHSI'))
            init = cell.get('params', {}).get('INIT')
            if init is not None:
                setflag(dff['init'], str(init).strip('"') == '1')
            stats['dffs'] = stats.get('dffs', 0) + 1
            continue
        init = cell.get('params', {}).get('INIT')
        if init is not None and 'truth_table_bits' in entry:
            width = {'GP_2LUT': 2, 'GP_3LUT': 3, 'GP_4LUT': 4}.get(cell['type'], 4)
            init = parse_init(init, width)
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
            continue
        # Switch-matrix connection: dst is a matrix output field
        # (6-bit), src is a matrix source; write the source's Matrix
        # Input Number into the field (datasheet Tables 21/22 join).
        src, dst = pip.get('src'), pip.get('dst')
        if dst in fields:
            written_fields.add(dst)
            if src in msrc:
                lo, _hi = fields[dst]
                v = msrc[src]
                for b in range(6):
                    if (v >> b) & 1:
                        setbit(lo + b)
                    else:
                        clearbit(lo + b)
                stats['pips'] += 1
                stats['connections'] += 1
                if dst in wire_pad:
                    # Driving a pad: enable its output. Ground truth
                    # (Go Configure-compiled designs): pads WITH a
                    # static output-enable register use it (bit = 1)
                    # and leave the matrix OE field at default; pads
                    # WITHOUT one (e.g. IO5/IO9/IO10...) set the matrix
                    # "Digital Output OE" field to VDD (source 63).
                    # IO6/IO7 have a static register but NO matrix OE
                    # field at all - checking wire_pad (any pad), not
                    # io_oe membership, is what enables them.
                    pad = wire_pad[dst]
                    pm = pin_modes.get(pad, {})
                    if 'out_en' in pm:
                        setbit(pm['out_en'])
                    elif dst in io_oe:
                        oe_lo = io_oe[dst][0]
                        for b in range(6):
                            if (63 >> b) & 1:
                                setbit(oe_lo + b)
                            else:
                                clearbit(oe_lo + b)
                    else:
                        print('  warn: pad %s has no output-enable '
                              'mechanism in the chipdb' % pad,
                              file=sys.stderr)
                    stats['io_outputs'] += 1
            else:
                stats['unmapped_pips'] += 1
                print('  warn: unmapped matrix source %r -> %r' % (src, dst),
                      file=sys.stderr)

    # DFF control inputs (nRST/nSET) that nothing routed are tied to
    # VDD: the resets are ACTIVE LOW, so the GND default field would
    # hold the macrocell in permanent reset (or set) on hardware.
    bel_ports = {b['name']: b.get('ports', {}) for b in chipdb.get('bels', [])}
    for cname, cell in routed.get('cells', {}).items():
        ctype = cell.get('type', '')
        if not (ctype.startswith('GP_DFF') or ctype.startswith('GP_DLATCH')):
            continue
        ports = bel_ports.get(cell.get('bel'), {})
        for port in ('nRST', 'nSET'):
            wire = ports.get(port, {}).get('wire')
            if wire in fields and wire not in written_fields:
                lo = fields[wire][0]
                for b in range(6):
                    if (63 >> b) & 1:   # VDD
                        setbit(lo + b)
                    else:
                        clearbit(lo + b)
                written_fields.add(wire)
                stats['bits_set'] += 1

    # Pads driven by GP_OBUF: clear the pull resistors (floating).
    # Ground-truth behavior: Go Configure's compiled design for this
    # device clears exactly the pull-resistance bits on its output
    # pins and leaves every other pin-mode register at the default
    # (digital input / push-pull 1x; the matrix OE field enables the
    # driver).
    for cname, cell in routed.get('cells', {}).items():
        if cell.get('type') != 'GP_OBUF':
            continue
        pm = pin_modes.get(cell.get('bel'), {})
        lo = pm.get('pull_res')
        if lo is not None:
            clearbit(lo)
            clearbit(lo + 1)
            stats['bits_set'] += 1

    return bits, stats


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--chipdb', required=True)
    ap.add_argument('--routed', required=True)
    ap.add_argument('--out', required=True, help='output file prefix')
    args = ap.parse_args()

    chipdb = json.load(open(args.chipdb))
    routed = json.load(open(args.routed))

    bits, stats = assemble(chipdb, routed, args.chipdb)
    data = bits_to_bytes(bits)

    with open(args.out + '.bin', 'wb') as f:
        f.write(data)
    with open(args.out + '.hex', 'w') as f:
        intel_hex(data, f)
    with open(args.out + '.nvm', 'w') as f:
        f.write(''.join('1' if b else '0' for b in bits) + '\n')
    # Go Configure Software Hub import format (identical to the app's own
    # "Export NVM" files and its defaultNVM resources): one line per NVM
    # bit, CRLF-terminated, "index\t\tvalue\t\t//"
    with open(args.out + '.nvm.txt', 'w', newline='') as f:
        f.write('index\t\tvalue\t\tcomment\r\n')
        for i, b in enumerate(bits):
            f.write('%d\t\t%d\t\t//\r\n' % (i, 1 if b else 0))

    print('assembled %s: %d bits (%d bytes), %d cells mapped, %d bits changed'
          % (args.out, len(bits), len(data), stats['cells'], stats['bits_set']))
    if stats.get('dffs'):
        print('  DFF/latch macrocells: %d' % stats['dffs'])
    if chipdb.get('nvm', {}).get('matrix'):
        print('  matrix connections written: %d (io outputs: %d, '
              'unmapped sources: %d)'
              % (stats['connections'], stats['io_outputs'],
                 stats['unmapped_pips']))


if __name__ == '__main__':
    main()
