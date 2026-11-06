#!/usr/bin/env python3
"""Compose an i386 boot image: a BIOS ROM, a disk, and the RAM between them.

    python3 tools/i386_image.py                 # build and describe
    python3 tools/i386_image.py --check         # and boot it in the model
    python3 tools/i386_image.py --out img.bin   # a flat 1 MiB file as well

A PC is three memories, not one, and this keeps them apart the way a board does:

    0x0000_0000 .. 0x000F_FFFF   1 MiB of RAM
      0x000F_0000  the BIOS ROM   (bios.bin, assembled at zero, run at 0xF000:0)
    0x0003_0000 (in RAM)         the staged disk: segment 0x3000, sector n at +n*512

The staged disk is a stand-in for the ATA device that lands in step 2; it exists
so int 13h reads real sectors from somewhere instead of from a hard-coded
buffer, and so a boot sector can be written exactly as it would be on a real
machine (no relocations, 0xAA55 at the end, loaded at 0x7C00).

Both images are flat binaries straight out of nasm: the ROM is `org 0` because
its offsets must fit in sixteen bits once it runs at 0xF000:0, and the boot
sector is `org 0x7C00` because that is where int 19h loads it.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import i386_ref  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
RVDIR = ROOT / "userspace" / "i386"
BIOS = RVDIR / "bios.bin"
BOOT = RVDIR / "mbr.bin"
OUT = ROOT / "userspace" / "i386" / "assets" / "X.image.jsa"
SRC = ROOT / "userspace" / "i386" / "assets" / "X.image.src"

RAM_SIZE = 1 << 20
BIOS_AT = 0x000F_0000
ROM_TOP = 0x0010_0000
SECTOR = 512

#: the staged disk: sector 0 is the boot sector, sector 1 is the payload it reads
DISK_AT = 0x000C_0000
DISK_SECTORS = 128

STAGE2 = b"STAGE2: this sector came through int 13h\r\n"


def rom() -> bytes:
    """The 64 KiB ROM: assembled at offset zero, run at 0xF000:0."""
    data = BIOS.read_bytes()
    if len(data) != 0x10000:
        raise SystemExit(f"{BIOS.name} is {len(data)} bytes, not a 64 KiB ROM")
    return data


def boot_sector() -> bytes:
    data = BOOT.read_bytes()
    if data[510:512] != b"\x55\xaa":
        raise SystemExit("the boot sector does not end with 0xAA55")
    return data


def disk_image() -> bytes:
    """Sector 0: the boot sector. Sector 1: the text it reads back."""
    stage2 = STAGE2.ljust(SECTOR, b"\0")
    disk = boot_sector() + stage2
    return disk.ljust(DISK_SECTORS * SECTOR, b"\0")


def build() -> bytes:
    image = bytearray(RAM_SIZE)
    image[BIOS_AT:ROM_TOP] = rom()
    disk = disk_image()
    image[DISK_AT:DISK_AT + len(disk)] = disk
    return bytes(image)


def words(data: bytes) -> list[int]:
    """Bytes to 32-bit little-endian words, zero-padded: the shape the JSW
    machine's memory list takes, so a byte costs a divide and a modulo rather
    than a list node of its own."""
    if len(data) % 4:
        data = data + b"\0" * (4 - len(data) % 4)
    return [int.from_bytes(data[i:i + 4], "little") for i in range(0, len(data), 4)]


def emit_jsw() -> None:
    """Write the ROM and the disk as `.jsa` lists the project can import."""
    assets = RVDIR / "assets" / "lists"
    assets.mkdir(parents=True, exist_ok=True)
    for name, data in (("bios.jsa", rom()), ("disk.jsa", disk_image())):
        (assets / name).write_text(json.dumps(words(data)), encoding="utf-8")
        print(f"{name}: {len(data):,} bytes -> {len(words(data)):,} words")


def check(image: bytes, steps: int) -> int:
    mach = i386_ref.Machine()
    mach.ram[:] = image
    mach.sreg[i386_ref.SEG_CS] = i386_ref.RESET_CS
    mach.eip = i386_ref.RESET_IP
    mach.run(limit=steps)
    print(f"boot: {mach.steps} instructions, halted {mach.halted}")
    print("--- the text plane " + "-" * 58)
    print(mach.text())
    print("-" * 78)
    return 0


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="boot it in the model")
    parser.add_argument("--out", help="write a flat binary as well")
    parser.add_argument("--steps", type=int, default=200_000)
    args = parser.parse_args(argv)

    image = build()
    print(f"image {len(image):,} bytes: bios {BIOS.name}, "
          f"disk {DISK_SECTORS} sectors at {DISK_AT:#07x}")
    if args.out:
        Path(args.out).write_bytes(image)
        print(f"wrote {args.out}")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(list(image)), encoding="utf-8")
    SRC.write_text("i386: bios.elf + mbr.elf\n", encoding="utf-8")
    emit_jsw()
    if args.check:
        return check(image, args.steps)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
