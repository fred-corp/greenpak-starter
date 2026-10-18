#!/usr/bin/env python3
"""Flash a GreenPAK NVM image over I2C using third-party tools.

Backends (--backend):
  i2cdriver   I2CDriver (https://i2cdriver.com), needs `pip install i2cdriver`
  flipper     Flipper Zero running an "I2C Tools CLI" app over USB serial
  smbus       Linux /dev/i2c-N (smbus2 or periphery optional)
  dummy       dry-run: print the I2C transactions, touch nothing

Modes (--mode):
  shadow      write the register-file image to the device's live
              register shadow (runs the design immediately; lost on
              power-down). Safe, reversible - the default.
  otp         program the NVM (one-time programmable!). Requires
              --force. The exact programming command sequence is
              device-dependent (VPP/high-voltage handling per the
              datasheet "I2C programming" chapter); the constants below
              default to the common GreenPAK4-6 register layout and
              can be adjusted via the constants at the top.

Device interface (adjust to your datasheet's I2C chapter):
  - 7-bit I2C address: 0x08 default (some parts 0x09/0x0A/0x0B)
  - register file: control regs 0x00-0x0F, configuration shadow at
    0x10.. (image bytes written sequentially)

Usage:
  greenpak_flash.py --backend dummy top.bin
  greenpak_flash.py --backend i2cdriver --port /dev/ttyUSB0 top.bin
  greenpak_flash.py --backend flipper --port /dev/ttyUSB1 top.bin
  greenpak_flash.py --backend smbus --port 1 top.bin
"""

import argparse
import sys
import time

# Datasheet-exact (SLG46826 DS 3.18 sec. 15.4/15.6):
#   Control Byte = [Control Code 0001 | Block Address A10..A8 | R/W]
#   -> default write control byte = 0b0001_000_0 = 0x08
#   Register configuration data = block 0: word address 0x00-0xFF IS
#   the register map (Table 59 byte addresses); sequential writes
#   auto-increment. I2C reset = register bit [1601]; IO latching
#   during writes = register bit [1602].
I2C_ADDR = 0x08          # 0b0001_000 | R/W
SHADOW_REG = 0x00        # block 0 word address = register map byte
PAGE_SIZE = 256          # full register map in one sequential write
REG_RESET_BIT = 1601     # POR + reload from NVM when set
REG_LATCH_BIT = 1602     # latch IOs during writes when set
OTP_FORCE_DELTA = 5.0    # datasheets require VPP before OTP commands


# ---------------------------------------------------------------- backends

class Dummy:
    def start(self, addr):
        print("i2c start  addr=0x%02x" % addr)

    def write(self, addr, data):
        print("i2c write  addr=0x%02x  [%d bytes] %s..." %
              (addr, len(data), ' '.join('%02X' % b for b in data[:12])))

    def read(self, addr, n):
        print("i2c read   addr=0x%02x  n=%d" % (addr, n))
        return [0x00] * n

    def stop(self):
        print("i2c stop")


class I2CDriverBackend:
    def __init__(self, port):
        import i2cdriver
        self.i2c = i2cdriver.I2C1(port)

    def start(self, addr):
        pass

    def write(self, addr, data):
        self.i2c.start(addr, 0)
        self.i2c.write(bytes(data))
        self.i2c.stop()

    def read(self, addr, n):
        self.i2c.start(addr, 1)
        out = list(self.i2c.read(n))
        self.i2c.stop()
        return out

    def stop(self):
        pass


class FlipperBackend:
    """Flipper Zero with an I2C Tools CLI app over USB serial.

    Line protocol used (matches the common 'I2C Tools' apps; if your
    app version differs, adjust the command templates below):
      write <hexaddr> <hexdata...>
      read <hexaddr> <n>
    Commands are sent over the USB serial console, one per line.
    """
    WRITE_TMPL = "write 0x{addr:02x} {data}\n"
    READ_TMPL = "read 0x{addr:02x} {n}\n"

    def __init__(self, port):
        import serial
        self.ser = serial.Serial(port, 115200, timeout=2)

    def _cmd(self, line):
        self.ser.write(line.encode())
        self.ser.flush()
        time.sleep(0.05)

    def start(self, addr):
        pass

    def write(self, addr, data):
        self._cmd(self.WRITE_TMPL.format(addr=addr,
                                        data=' '.join('%02x' % b for b in data)))

    def read(self, addr, n):
        self._cmd(self.READ_TMPL.format(addr=addr, n=n))
        return [0x00] * n

    def stop(self):
        self.ser.close()


class SMBusBackend:
    def __init__(self, port):
        self.busno = int(port)
        try:
            from smbus2 import SMBus
            self.bus = SMBus(self.busno)
            self.write_i2c_block_data = self.bus.write_i2c_block_data
        except ImportError:
            from periphery import I2C
            self.bus = I2C('/dev/i2c-%d' % self.busno)
            self.write_i2c_block_data = None

    def start(self, addr):
        pass

    def write(self, addr, data):
        if self.write_i2c_block_data:
            for off in range(0, len(data), 32):
                self.bus.write_i2c_block_data(addr, data[off],
                                             data[off + 1:off + 33])
        else:
            msg = self.bus.Message(bytes(data))
            self.bus.transfer(addr, [msg])

    def read(self, addr, n):
        return [0x00] * n

    def stop(self):
        pass


def make_backend(name, port):
    if name == 'dummy':
        return Dummy()
    if name == 'i2cdriver':
        return I2CDriverBackend(port)
    if name == 'flipper':
        return FlipperBackend(port)
    if name == 'smbus':
        return SMBusBackend(port)
    raise SystemExit('unknown backend: %s' % name)


# ---------------------------------------------------------------- flashing

def load_image(path):
    if path.endswith('.hex'):
        data = bytearray()
        with open(path) as f:
            for line in f:
                line = line.strip()
                if not line.startswith(':'):
                    continue
                b = bytes.fromhex(line[1:])
                count, addr_hi, addr_lo, rectype = b[0], b[1], b[2], b[3]
                if rectype == 0:
                    data.extend(b[4:4 + count])
                elif rectype == 1:
                    break
        return data
    with open(path, 'rb') as f:
        return f.read()


def flash_shadow(be, image, addr=I2C_ADDR):
    # single-byte register addressing: only the first 256 registers are
    # addressable in one byte - the device NVM (2048 bits = 256 bytes
    # for GreenPAK6). Clip longer images to the device register space.
    if len(image) > 256:
        print("note: image is %d bytes; flashing the first 256 "
              "(device register space)" % len(image))
        image = image[:256]
    print("shadow download: %d bytes to addr 0x%02x, regs 0x%02x+"
          % (len(image), addr, SHADOW_REG))
    for off in range(0, len(image), PAGE_SIZE):
        chunk = image[off:off + PAGE_SIZE]
        # register-addressed burst: [reg, data...]
        be.write(addr, [SHADOW_REG + off] + list(chunk))
    print("done (design is live; NVM not touched)")


def flash_otp(be, image, addr=I2C_ADDR):
    # NOTE: the OTP sequence below is the common GreenPAK4-6 shape
    # (write-enable control reg, burst the image, program command,
    # busy-wait). The exact control register values are in your
    # device datasheet's "I2C programming" chapter - verify before
    # first use, and never OTP-program without a backup image.
    print("WARNING: OTP programming is irreversible.")
    print("Verify the control-register constants against the datasheet!")
    CTRL_WRITE_ENABLE = 0x01
    CTRL_PROGRAM = 0x02
    CTRL_STATUS = 0x03
    be.write(addr, [CTRL_WRITE_ENABLE, 0x80])
    for off in range(0, len(image), PAGE_SIZE):
        chunk = image[off:off + PAGE_SIZE]
        be.write(addr, [SHADOW_REG + off] + list(chunk))
        be.write(addr, [CTRL_PROGRAM, 0x01])
        time.sleep(OTP_FORCE_DELTA)
        status = be.read(addr + 0, 1)
        if status[0] != 0x00:
            print("status byte 0x%02X" % status[0])
    print("done")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('image', help='.bin or .hex NVM image (make output)')
    ap.add_argument('--backend', default='dummy',
                    choices=['dummy', 'i2cdriver', 'flipper', 'smbus'])
    ap.add_argument('--port', default='/dev/ttyUSB0',
                    help='serial device or i2c bus number')
    ap.add_argument('--addr', type=lambda x: int(x, 0), default=I2C_ADDR)
    ap.add_argument('--mode', default='shadow', choices=['shadow', 'otp'])
    ap.add_argument('--force', action='store_true',
                    help='required for --mode otp')
    args = ap.parse_args()

    image = load_image(args.image)
    be = make_backend(args.backend, args.port)
    if args.mode == 'shadow':
        flash_shadow(be, image, args.addr)
    else:
        if not args.force:
            raise SystemExit('OTP programming requires --force')
        flash_otp(be, image, args.addr)


if __name__ == '__main__':
    main()
