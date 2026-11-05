#!/usr/bin/env python3
"""Flatten a bare-metal RV64 ELF into the byte list the JSW machine boots from.

    python3 tools/gen_rv64_image.py userspace/rv64/payload.elf

Writes ``userspace/rv64/assets/lists/X.image.jsa``: one list item per byte, in
address order from the RAM base. The Scratch machine copies that list into `X.ram`
at power-on, so the payload has to be *positioned* — an ELF linked at
0x8000_0000 becomes a list that starts at that base, not at the ELF file's own
offset 0.
"""

from __future__ import annotations

import json
import struct
import sys
from pathlib import Path

RAM_BASE = 0x8000_0000
ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "userspace" / "rv64" / "assets" / "lists" / "X.image.jsa"
#: which ELF the list was made from, so a test can tell it is looking at the
#: payload it thinks it is looking at (there are several: isa, disk, bios, glyph)
SRC = OUT.with_suffix(".src")


def segments(image: bytes) -> list[tuple[int, bytes, int]]:
    """``(vaddr, bytes, memsz)`` for every PT_LOAD."""
    if image[:4] != b"\x7fELF" or image[4] != 2 or image[5] != 1:
        raise ValueError("need a little-endian ELFCLASS64 image")
    e_phoff, = struct.unpack_from("<Q", image, 0x20)
    e_phentsize, e_phnum = struct.unpack_from("<HH", image, 0x36)
    out = []
    for i in range(e_phnum):
        off = e_phoff + i * e_phentsize
        p_type, _flags, p_offset, p_vaddr, _paddr, p_filesz, p_memsz, _align = struct.unpack_from(
            "<IIQQQQQQ", image, off
        )
        if p_type != 1:
            continue
        out.append((p_vaddr, image[p_offset : p_offset + p_filesz], p_memsz))
    return out


def flatten(image: bytes) -> bytes:
    segs = segments(image)
    if not segs:
        raise ValueError("no loadable segments")
    end = max(vaddr + memsz for vaddr, _data, memsz in segs)
    span = end - RAM_BASE
    if span <= 0:
        raise ValueError("image does not sit at the RAM base")
    memory = bytearray(span)
    for vaddr, data, _memsz in segs:
        start = vaddr - RAM_BASE
        memory[start : start + len(data)] = data
    return bytes(memory)


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__)
        return 2
    elf = Path(argv[1])
    data = flatten(elf.read_bytes())
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(list(data)), encoding="utf-8")
    SRC.write_text(f"{elf}\n", encoding="utf-8")
    print(f"{elf.name}: {len(data)} bytes -> {OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
