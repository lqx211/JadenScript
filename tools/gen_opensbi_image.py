#!/usr/bin/env python3
"""Wrap QEMU's OpenSBI blob in a boot image for the *Scratch* machine.

    python3 tools/gen_opensbi_image.py            # write assets/lists/X.image.jsa
    python3 tools/gen_opensbi_image.py --check    # and run it in the reference first

`tools/rv64_opensbi.py` boots the firmware in Python by setting `a0`/`a1`/`a2`
before the first instruction. A Scratch project cannot do that: the machine
starts at the RAM base with every register zero, so the image carries a stub
that finishes the hand-off QEMU would have done:

    0x8000_0000   firmware    fw_dynamic.bin at its link address, word 0 patched
    0x8004_3000   stub        finishes the hand-off, then re-enters the firmware
    0x8004_4000   device tree dumped from qemu, 5 KB of it
    0x8004_6000   info        struct fw_dynamic_info, six eight-byte fields

The firmware has to sit at its link address: this build does **not** relocate.
Loaded anywhere else it walks into its own `_start_hang` and does nothing at
all. And the hand-off has to happen *before* its first instruction, so the reset
vector itself is patched: word 0 becomes `jal x0, stub`, and the stub sets
`a1`/`a2`, replays the instruction it displaced, and jumps back to `+4` with the
hart id still in `a0`. The firmware cannot tell the difference -- it runs the
same instruction stream, six instructions later.
"""

from __future__ import annotations

import argparse
import json
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import rv64_opensbi as fw  # noqa: E402
from gen_rv64_image import OUT, ROOT, SRC  # noqa: E402

#: where each part of the image lives, relative to the RAM base
STUB_AT = 0x43000
FW_AT = 0x0000
DTB_AT = 0x44000
INFO_AT = 0x46000

DTB_PATH = ROOT / "build" / "opensbi" / "virt.dtb"


def auipc(rd: int, pages: int) -> int:
    return ((pages & 0xFFFFF) << 12) | (rd << 7) | 0x17


def addi(rd: int, rs1: int, imm: int) -> int:
    return ((imm & 0xFFF) << 20) | (rs1 << 15) | (rd << 7) | 0x13


def jalr(rd: int, rs1: int, imm: int = 0) -> int:
    return ((imm & 0xFFF) << 20) | (rs1 << 15) | (rd << 7) | 0x67


def jal(rd: int, delta: int) -> int:
    """`jal rd, pc + delta` -- a J-type immediate, so +-1 MiB of reach."""
    value = delta & 0x1FFFFF
    return (((value >> 20) & 1) << 31) | (((value >> 1) & 0x3FF) << 21) \
        | (((value >> 11) & 1) << 20) | (((value >> 12) & 0xFF) << 12) \
        | (rd << 7) | 0x6F


def la(rd: int, target: int, at: int) -> list[int]:
    """`rd = target`, as the two-instruction pc-relative pair the assembler emits.

    Not `lui` + `addi`: on RV64 `lui` sign-extends from bit 31, so
    `lui a0, 0x80000` loads 0xFFFFFFFF_80000000 rather than 0x8000_0000. And not
    a bare `auipc`: its immediate is relative to *its own* address, which is not
    the start of the stub for anything but the first instruction.
    """
    delta = target - at
    hi, lo = (delta + 0x800) >> 12, delta & 0xFFF
    if lo >= 0x800:
        lo -= 0x1000
    return [auipc(rd, hi), addi(rd, rd, lo)]


def stub(where: dict[str, int], displaced: int) -> bytes:
    """Finish the hand-off QEMU would have done, then re-enter the firmware."""
    words: list[int] = []
    for rd, target in ((11, where["dtb"]), (12, where["info"])):
        at = fw.RAM_BASE + where["stub"] + len(words) * 4
        words.extend(la(rd, fw.RAM_BASE + target, at))
    words.append(displaced)                                   # what we replaced
    words.append(jal(0, (where["fw"] + 4) - (where["stub"] + len(words) * 4)))
    return b"".join(struct.pack("<I", word) for word in words)


def patchable(blob: bytes) -> int:
    """The firmware's first word, if the stub may replay it from another address.

    Only plain ALU work qualifies: `add s0, a0, zero` is where fw_dynamic saves
    the hart id, and a pc-relative instruction there would not survive the jump.
    """
    word = struct.unpack_from("<I", blob, 0)[0]
    if word & 0x7F not in (0x33, 0x3B, 0x13, 0x1B):
        raise SystemExit(f"the firmware starts with {word:#010x}, which the stub "
                         f"cannot replay; check the blob")
    return word


def info_struct() -> bytes:
    values = (fw.INFO_MAGIC, fw.INFO_VERSION, fw.RAM_BASE + FW_AT,
              fw.NEXT_MODE_S, 0, 0)
    return b"".join(struct.pack("<Q", value) for value in values)


def build(blob: bytes, dtb: bytes) -> bytes:
    end = fw.RAM_BASE + INFO_AT + len(info_struct())
    image = bytearray(end - fw.RAM_BASE)
    places = {"stub": STUB_AT, "dtb": DTB_AT, "info": INFO_AT, "fw": FW_AT}
    entry = jal(0, STUB_AT - FW_AT)
    pieces = ((FW_AT, struct.pack("<I", entry) + blob[4:]),
              (STUB_AT, stub(places, patchable(blob))),
              (DTB_AT, dtb), (INFO_AT, info_struct()))
    for at, data in pieces:
        if at + len(data) > len(image):
            raise SystemExit(f"the image layout is too small for {len(data)} bytes at {at:#x}")
        image[at:at + len(data)] = data
    return bytes(image)


def check(image: bytes, steps: int) -> int:
    """Boot the wrapped image in the reference, the way the Scratch machine would."""
    import rv64_ref

    mach = rv64_ref.Machine(ram_size=fw.RAM_MIB << 20, mem_mib=fw.RAM_MIB,
                            max_mem_mib=fw.RAM_MIB)
    for i, byte in enumerate(image):
        mach.write(fw.RAM_BASE + i, byte, 1)
    mach.pc = fw.RAM_BASE
    mach.run(limit=steps)
    print(f"image boot  pc={mach.pc:#010x} after {mach.steps:,} instructions, "
          f"uart {bytes(mach.out)[:80]!r} err {mach.error}")
    return 0 if mach.error is None else 1


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--blob", help="the fw_dynamic binary to wrap")
    parser.add_argument("--dtb", help="a device tree blob (dumped with qemu if missing)")
    parser.add_argument("--check", action="store_true",
                        help="run the image in the reference model before writing it")
    parser.add_argument("--steps", type=int, default=1_000_000)
    args = parser.parse_args(argv)

    blob = fw.find_blob(args.blob).read_bytes()
    dtb_path = Path(args.dtb) if args.dtb else DTB_PATH
    if not dtb_path.exists():
        dtb_path.parent.mkdir(parents=True, exist_ok=True)
        fw.dump_dtb(dtb_path)
    dtb = dtb_path.read_bytes()
    if struct.unpack_from(">I", dtb, 0)[0] != 0xD00DFEED:
        raise SystemExit(f"{dtb_path} is not a device tree")

    image = build(blob, dtb)
    if args.check:
        status = check(image, args.steps)
        if status:
            return status

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(list(image)), encoding="utf-8")
    SRC.write_text(f"opensbi:{fw.find_blob(args.blob)}\n", encoding="utf-8")
    print(f"opensbi: {len(image):,} bytes -> {OUT.relative_to(ROOT)} "
          f"(firmware {len(blob):,}, device tree {len(dtb):,})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
