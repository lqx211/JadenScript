#!/usr/bin/env python3
"""Compose a bootable image: the BIOS, then a payload staged above it.

    python3 tools/gen_boot_image.py                          # OpenSBI + device tree
    python3 tools/gen_boot_image.py --payload userspace/rv64/smode.elf
    python3 tools/gen_boot_image.py --check                  # boot it in the reference

Every project should show POST, so this is how a payload gets in: the BIOS is
the firmware that runs at the reset vector, posts, and then hands off.

    0x8000_0000   the BIOS              (from bios.elf, 22 KB)
    0x8004_3000   boot record + code    {magic 'JSWB', a1, a2, length} then the copy
    0x8004_4000   the payload           staged here, copied down, then jumped to
    0x8008_8000   device tree           (only when booting firmware)
    0x8008_a000   info                  struct fw_dynamic_info

The copy runs from 0x8004_3000, which is above the range it writes
(0x8000_0000 + payload length), so it survives its own memmove -- and the BIOS,
which sits inside that range, is gone by the time the jump happens. That is what
a boot ROM does, and it is why the record carries the values for `a1`/`a2`
instead: the payload cannot be handed arguments by a launcher that no longer
exists.
"""

from __future__ import annotations

import argparse
import json
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import gen_rv64_image  # noqa: E402
import gen_opensbi_image as obi  # noqa: E402
import rv64_opensbi as fw  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
BIOS = ROOT / "userspace" / "rv64" / "bios.elf"
OUT = gen_rv64_image.OUT
SRC = gen_rv64_image.SRC

#: offsets inside the image, relative to the RAM base
BIOS_AT = 0x00000
RECORD_AT = 0x43000
CODE_AT = RECORD_AT + 16
FW_AT = 0x44000
DTB_AT = 0x88000
INFO_AT = 0x8A000

MAGIC = 0x4A535742          # 'JSWB'
#: the text plane, where the progress bar is painted
DISP_BASE = 0x5000_0000


def lbu(rd: int, rs1: int, imm: int) -> int:
    return ((imm & 0xFFF) << 20) | (rs1 << 15) | (4 << 12) | (rd << 7) | 0x03


def andi(rd: int, rs1: int, imm: int) -> int:
    return ((imm & 0xFFF) << 20) | (rs1 << 15) | (7 << 12) | (rd << 7) | 0x13


def slli(rd: int, rs1: int, shamt: int) -> int:
    return ((shamt & 0x3F) << 20) | (rs1 << 15) | (1 << 12) | (rd << 7) | 0x13


def add(rd: int, rs1: int, rs2: int) -> int:
    return (rs2 << 20) | (rs1 << 15) | (rd << 7) | 0x33


def ld(rd: int, rs1: int, imm: int) -> int:
    return ((imm & 0xFFF) << 20) | (rs1 << 15) | (3 << 12) | (rd << 7) | 0x03


def store(funct3: int, rs2: int, rs1: int, imm: int) -> int:
    value = imm & 0xFFF
    return (((value >> 5) & 0x7F) << 25) | (rs2 << 20) | (rs1 << 15) | (funct3 << 12) \
        | ((value & 0x1F) << 7) | 0x23


def sd(rs2: int, rs1: int, imm: int) -> int:
    return store(3, rs2, rs1, imm)


def sb(rs2: int, rs1: int, imm: int) -> int:
    return store(0, rs2, rs1, imm)


def blt(rs1: int, rs2: int, delta: int) -> int:
    value = delta & 0x1FFF
    return (((value >> 12) & 1) << 31) | (((value >> 5) & 0x3F) << 25) | (rs2 << 20) \
        | (rs1 << 15) | (4 << 12) | (((value >> 1) & 0xF) << 8) \
        | (((value >> 11) & 1) << 7) | 0x63


#: the boot screen's furniture, in cells of the 80x30 text plane (row 20)
ROW = 20
BAR_CELL = ROW * 80 + 4
BAR_STEPS = 56
DONE_CELL = ROW * 80 + 61
PCT_CELL = ROW * 80 + 66          # four cells: " 42%"
SPIN_CELL = ROW * 80 + 72         # one cell, cycling | / - \
PCT_AT = 0x43200                  # the 101-entry percentage table
SPIN_AT = PCT_AT + 101 * 4        # the four spinner glyphs

#: the four spinner frames, in order
SPIN_FRAMES = b"|/-\\"


def percent_table() -> bytes:
    """`"  0%"` .. `"100%"`, four characters each, so the field never shifts."""
    return b"".join(f"{n:>3}%".encode("ascii") for n in range(101))


def copy_code(fw_len: int) -> bytes:
    """Move the payload down to its link address, then jump to it.

    The registers the BIOS filled in -- `a1` the device tree, `a2` the info
    struct -- are left alone, and `a0` is set to the hart id the payload
    expects. Only `t*` and `s*` are touched, so the copy may run straight over
    the program that started it.

    It draws the boot screen's furniture as it goes: a bar that fills, a
    spinner, and a percentage. The copy is tens of thousands of iterations in
    the Scratch core -- a second or two -- and a screen that does not move for
    that long reads as a hang. `s0`-`s11` are free because the BIOS is being
    overwritten, so the counters live there.
    """
    words: list[tuple[int, int]] = []
    bar_period = max(1, fw_len // 8 // BAR_STEPS)
    pct_period = max(1, -(-(fw_len // 8) // 100))      # ceil, so it stops at 100
    spin_period = max(1, fw_len // 8 // 64)

    def at() -> int:
        return fw.RAM_BASE + CODE_AT + len(words) * 4

    def emit(*values: int) -> None:
        for value in values:
            words.append((at(), value))

    def emit_block(rs1: int, rs2: int, block: list[int]) -> None:
        """`blt rs1, rs2, over` followed by the block it skips when taken.

        The offset is relative to the branch, so it has to clear the branch's
        own word as well as the block.
        """
        emit(blt(rs1, rs2, 4 * (len(block) + 1)))
        emit(*block)

    emit(*obi.la(5, fw.RAM_BASE + FW_AT, at()))                # t0 = source
    emit(*obi.la(6, fw.RAM_BASE, at()))                        # t1 = link address
    emit(*obi.la(7, fw.RAM_BASE + FW_AT + fw_len, at()))       # t2 = source end
    emit(*obi.la(8, DISP_BASE + 2 * BAR_CELL, at()))           # s0 = bar cell
    emit(obi.addi(9, 0, bar_period % 4096))                    # s1 = iterations per cell
    emit(obi.addi(18, 0, 0))                                   # s2 = since the last cell
    emit(*obi.la(19, DISP_BASE + 2 * SPIN_CELL, at()))         # s3 = spinner cell
    emit(*obi.la(20, fw.RAM_BASE + SPIN_AT, at()))             # s4 = spinner frames
    emit(obi.addi(21, 0, 0))                                   # s5 = since the last frame
    emit(obi.addi(22, 0, spin_period % 4096))                  # s6 = frames period
    emit(*obi.la(23, DISP_BASE + 2 * PCT_CELL, at()))          # s7 = percent field
    emit(*obi.la(24, fw.RAM_BASE + PCT_AT, at()))              # s8 = percent table
    emit(obi.addi(25, 0, 0))                                   # s9 = since the last percent
    emit(obi.addi(26, 0, pct_period % 4096))                   # s10 = percent period
    emit(obi.addi(27, 0, 0))                                   # s11 = percent
    for cell in range(4):                                      # the field's attributes
        emit(obi.addi(28, 0, 7))
        emit(sb(28, 23, 2 * cell + 1))

    loop = at()
    emit(ld(28, 5, 0), sd(28, 6, 0))                           # ld t3; sd t3
    emit(obi.addi(5, 5, 8), obi.addi(6, 6, 8))                 # both += 8
    emit(obi.addi(18, 18, 1))                                  # s2 += 1
    emit(obi.addi(21, 21, 1))                                  # s5 += 1
    emit(obi.addi(25, 25, 1))                                  # s9 += 1

    # the bar: one cell every `bar_period` iterations
    emit_block(18, 9, [
        obi.addi(18, 0, 0),
        obi.addi(28, 0, 219),                                  # a solid block
        sb(28, 8, 0),
        obi.addi(28, 0, 7),                                    # grey on black
        sb(28, 8, 1),
        obi.addi(8, 8, 2),
    ])

    # the spinner: frame = counter & 3, so it turns without a second counter
    emit_block(21, 22, [
        obi.addi(21, 0, 0),
        andi(28, 18, 3),
        add(28, 28, 20),
        lbu(29, 28, 0),
        sb(29, 19, 0),
        obi.addi(29, 0, 7),
        sb(29, 19, 1),
    ])

    # the percentage: one table row per whole percent, four cells wide
    emit_block(25, 26, [
        obi.addi(25, 0, 0),
        obi.addi(27, 27, 1),
        slli(28, 27, 2),
        add(28, 28, 24),
        lbu(29, 28, 0), sb(29, 23, 0),
        lbu(29, 28, 1), sb(29, 23, 2),
        lbu(29, 28, 2), sb(29, 23, 4),
        lbu(29, 28, 3), sb(29, 23, 6),
    ])

    emit(blt(5, 7, loop - at()))                               # while not at the end
    emit(*obi.la(28, fw.RAM_BASE + PCT_AT + 100 * 4, at()))    # the last table row
    for cell in range(4):                                      # "100%"
        emit(lbu(29, 28, cell))
        emit(sb(29, 23, 2 * cell))
    emit(*obi.la(8, DISP_BASE + 2 * DONE_CELL, at()))
    for char in b"done":
        emit(obi.addi(28, 0, char))                            # the word after the bar
        emit(sb(28, 8, 0))
        emit(obi.addi(28, 0, 7))
        emit(sb(28, 8, 1))
        emit(obi.addi(8, 8, 2))
    emit(obi.addi(10, 0, 0))                                   # a0 = 0 (hart id)
    emit(*obi.la(5, fw.RAM_BASE, at()))                        # t0 = entry
    emit(obi.jalr(0, 5))                                       # jr t0
    return b"".join(struct.pack("<I", word) for _at, word in words)


def record(length: int, dtb: int = 0, info: int = 0) -> bytes:
    """{magic, a1, a2, payload length} -- the BIOS jumps to the code that
    follows, so the entry never has to survive a signed 32-bit load."""
    values = (MAGIC, dtb, info, length)
    if any(value >= 1 << 32 for value in values):
        raise SystemExit("the record holds 32-bit values; this layout is too high")
    return b"".join(struct.pack("<I", value) for value in values)


def build(bios: bytes, payload: bytes, dtb: bytes | None = None) -> bytes:
    """BIOS + record + copy code + payload, laid out for `powerOn`."""
    dtb = dtb or b""
    info = obi.info_struct() if dtb else b""
    end = max(INFO_AT + len(info) if dtb else 0, DTB_AT + len(dtb),
              FW_AT + len(payload), SPIN_AT + len(SPIN_FRAMES))
    image = bytearray(end)
    pieces = [(BIOS_AT, bios), (RECORD_AT, record(len(payload),
                                                  fw.RAM_BASE + DTB_AT if dtb else 0,
                                                  fw.RAM_BASE + INFO_AT if dtb else 0)),
              (CODE_AT, copy_code(len(payload))),
              (PCT_AT, percent_table()), (SPIN_AT, SPIN_FRAMES),
              (FW_AT, payload)]
    if dtb:
        pieces.append((DTB_AT, dtb))
        pieces.append((INFO_AT, info))
    for at, data in pieces:
        if at + len(data) > len(image):
            raise SystemExit(f"the image layout is too small for {len(data)} bytes at {at:#x}")
        image[at:at + len(data)] = data
    if RECORD_AT < len(payload):
        raise SystemExit("the payload would be copied over the boot record")
    if RECORD_AT + 16 + len(pieces[2][1]) > FW_AT:
        raise SystemExit("the boot code would run into the staged payload")
    return bytes(image)


def check(image: bytes, steps: int) -> int:
    """Post, hand off and boot, in the reference model."""
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
    parser.add_argument("--bios", default=str(BIOS), help="the BIOS ELF to boot from")
    parser.add_argument("--payload", help="a bare-metal ELF to boot instead of the firmware")
    parser.add_argument("--blob", help="the fw_dynamic binary to stage")
    parser.add_argument("--dtb", help="a device tree blob (dumped with qemu if missing)")
    parser.add_argument("--check", action="store_true", help="boot it in the reference first")
    parser.add_argument("--steps", type=int, default=2_000_000)
    args = parser.parse_args(argv)

    bios_path = Path(args.bios)
    if not bios_path.exists():
        raise SystemExit(f"{bios_path} is missing; run `make rv64-bios` first")
    bios = gen_rv64_image.flatten(bios_path.read_bytes())

    dtb: bytes | None = None
    if args.payload:
        payload = gen_rv64_image.flatten(Path(args.payload).read_bytes())
        source = Path(args.payload)
    else:
        payload = fw.find_blob(args.blob).read_bytes()
        source = fw.find_blob(args.blob)
        dtb_path = Path(args.dtb) if args.dtb else obi.DTB_PATH
        if not dtb_path.exists():
            dtb_path.parent.mkdir(parents=True, exist_ok=True)
            fw.dump_dtb(dtb_path)
        dtb = dtb_path.read_bytes()

    image = build(bios, payload, dtb)
    if args.check:
        status = check(image, args.steps)
        if status:
            return status

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(list(image)), encoding="utf-8")
    SRC.write_text(f"boot:{bios_path}+{source}\n", encoding="utf-8")
    print(f"boot image: {len(image):,} bytes -> {OUT.relative_to(ROOT)} "
          f"(bios {len(bios):,}, payload {len(payload):,}"
          f"{', device tree ' + format(len(dtb), ',') if dtb else ''})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
