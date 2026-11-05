#!/usr/bin/env python3
"""Boot QEMU's OpenSBI blob in the RV64 reference model and report the wall.

    python3 tools/rv64_opensbi.py                 # dump a device tree, boot, report
    python3 tools/rv64_opensbi.py --steps 5000000

This is the honest "can it run real firmware" probe. The blob is the firmware
QEMU itself boots from -- ``opensbi-riscv64-generic-fw_dynamic.bin`` -- so
nothing about it is adapted to this machine. Getting it to run means getting a
real M-mode/S-mode boundary right, not a hello world.

The hand-off QEMU performs before jumping to the firmware, reproduced here:

    a0 = hart id (0)
    a1 = physical address of the flat device tree
    a2 = physical address of ``struct fw_dynamic_info``

and the struct is six ``unsigned long`` fields (not four-byte ones):

    +0x00 magic     0x4942534F  ('OSBI')
    +0x08 version   2            major << 16 | minor, and 2 means 2
    +0x10 next_addr address the firmware jumps to when it is done
    +0x18 next_mode 1            S-mode
    +0x20 options   0
    +0x28 boot_hart 0

Getting any of those wrong parks the hart in a ``wfi`` idle loop at
``0x8000_03b8`` without a single byte of output, which is why the script prints
the reason it stopped rather than just a program counter.
"""

from __future__ import annotations

import argparse
import struct
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import rv64_ref  # noqa: E402

#: QEMU virt memory map, which is what the blob is built for
RAM_BASE = 0x8000_0000
FW_ADDR = RAM_BASE
DTB_ADDR = 0x87E0_0000
INFO_ADDR = 0x87DF_F000
NEXT_ADDR = 0x8020_0000

INFO_MAGIC = 0x4942534F
INFO_VERSION = 2
NEXT_MODE_S = 1

#: 160 MiB: the blob, the device tree at 126 MiB and room above both
RAM_MIB = 160

BLOB_CANDIDATES = (
    "/opt/homebrew/share/qemu/opensbi-riscv64-generic-fw_dynamic.bin",
    "/usr/share/qemu/opensbi-riscv64-generic-fw_dynamic.bin",
    "/usr/local/share/qemu/opensbi-riscv64-generic-fw_dynamic.bin",
)


def find_blob(explicit: str | None = None) -> Path:
    if explicit:
        return Path(explicit)
    for candidate in BLOB_CANDIDATES:
        path = Path(candidate)
        if path.exists():
            return path
    raise SystemExit("no OpenSBI binary found; pass --blob or install qemu")


def dump_dtb(path: Path) -> None:
    """Ask QEMU for the device tree it would hand to the firmware."""
    subprocess.run(
        ["qemu-system-riscv64", "-machine", f"virt,dumpdtb={path}", "-bios", "none",
         "-nographic", "-display", "none"],
        check=True, capture_output=True,
    )


def boot(blob: bytes, dtb: bytes, steps: int) -> rv64_ref.Machine:
    mach = rv64_ref.Machine(ram_size=RAM_MIB << 20, mem_mib=RAM_MIB, max_mem_mib=RAM_MIB)
    for i, byte in enumerate(blob):
        mach.write(FW_ADDR + i, byte, 1)
    for i, byte in enumerate(dtb):
        mach.write(DTB_ADDR + i, byte, 1)
    for offset, value in ((0x00, INFO_MAGIC), (0x08, INFO_VERSION), (0x10, NEXT_ADDR),
                          (0x18, NEXT_MODE_S), (0x20, 0), (0x28, 0)):
        mach.write(INFO_ADDR + offset, value, 8)

    mach.pc = FW_ADDR
    mach.set_reg(10, 0)            # a0 = hart id
    mach.set_reg(11, DTB_ADDR)     # a1 = fdt
    mach.set_reg(12, INFO_ADDR)    # a2 = struct fw_dynamic_info
    mach.run(limit=steps)
    return mach


def describe(mach: rv64_ref.Machine, blob_len: int) -> str:
    """What the last 4,000 instructions did, in one line each."""
    from collections import Counter

    pcs = Counter()
    for _ in range(4000):
        if mach.halted or mach.error:
            break
        pcs[mach.pc] += 1
        mach.step()
    if not pcs:
        return "already stopped"
    hot = pcs.most_common(1)[0]
    return (f"{len(pcs)} distinct pcs in the last 4,000 steps; "
            f"hottest {hot[0]:#x} x{hot[1]} "
            f"(offset {hot[0] - FW_ADDR:#x} of {blob_len:#x})")


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--blob", help="the fw_dynamic binary to boot")
    parser.add_argument("--dtb", help="a device tree blob (dumped with qemu if missing)")
    parser.add_argument("--steps", type=int, default=2_000_000,
                        help="instruction budget (default 2,000,000)")
    args = parser.parse_args(argv)

    blob_path = find_blob(args.blob)
    blob = blob_path.read_bytes()
    print(f"firmware   {blob_path} ({len(blob):,} bytes)")

    with tempfile.TemporaryDirectory() as tmp:
        dtb_path = Path(args.dtb) if args.dtb else Path(tmp) / "virt.dtb"
        if not dtb_path.exists():
            try:
                dump_dtb(dtb_path)
            except (OSError, subprocess.CalledProcessError) as exc:
                raise SystemExit(f"could not dump a device tree from qemu: {exc}")
        dtb = dtb_path.read_bytes()
        magic, = struct.unpack_from(">I", dtb, 0)
        if magic != 0xD00DFEED:
            raise SystemExit(f"{dtb_path} is not a device tree (magic {magic:#x})")
        print(f"device tree {dtb_path} ({len(dtb):,} bytes)")

    print(f"loading    firmware at {FW_ADDR:#x}, fdt at {DTB_ADDR:#x}, "
          f"info at {INFO_ADDR:#x}, next_addr {NEXT_ADDR:#x}")
    mach = boot(blob, dtb, args.steps)

    print()
    print(f"stopped at pc={mach.pc:#010x} after {mach.steps:,} instructions")
    print(f"uart       {bytes(mach.out)[:200]!r}")
    print(f"error      {mach.error}")
    print(f"halted     {mach.halted}")
    print(f"mtvec      {mach.mtvec:#x}   mcause {mach.mcause:#x}   mepc {mach.mepc:#x}")
    print(f"activity   {describe(mach, len(blob))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
