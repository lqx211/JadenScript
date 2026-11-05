#!/usr/bin/env python3
"""The screen, twice: the guest's cells blitted here, and the pen's own buffer.

A display can be wrong in two independent places. The bytes may be wrong (the
CPU wrote something else -- that is what the reference interpreter checks), or
the *pixels* may be wrong even though the bytes are right: a fattened glyph, a
pen size that is not one, a cell drawn in the wrong place. This tool covers the
second one.

    python3 tools/framebuffer_check.py cells userspace/rv64/bios.elf
    python3 tools/framebuffer_check.py hash build/bios-cells.txt

The blitter here is deliberately naive and shares no code with the project: an
8 x 15 glyph out of `font.jsa`, one cell 8 x 16 pixels, 80 x 30 cells, black
paper. If the pen's 640 x 480 buffer digests the same way, the painter is exact.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FONT = ROOT / "userspace" / "rv64" / "assets" / "lists" / "font.jsa"
COLS, ROWS = 80, 30
CELL_W, CELL_H, GLYPH_H = 8, 16, 15
W, H = COLS * CELL_W, ROWS * CELL_H


def font_rows() -> list[int]:
    return [int(v) for v in json.loads(FONT.read_text(encoding="utf-8"))]


def blit(cells: list[str], up: int = 1) -> list[bytearray]:
    """cells[row][col] -> H rows of W bytes, 1 = ink. `up` picks lower/upper case."""
    font = font_rows()
    screen = [bytearray(W) for _ in range(H)]
    for row, text in enumerate(cells):
        for col, ch in enumerate(text):
            code = ord(ch)
            if code < 0 or code > 255:
                continue
            index = code                     # CP437: one glyph per code
            base = index * GLYPH_H
            for gr in range(GLYPH_H):
                bits = font[base + gr] if base + gr < len(font) else 0
                if not bits:
                    continue
                # the project places glyph row gr at image row 16*row + 15 - gr:
                # one blank row on top of the cell, the baseline at the bottom
                y = row * CELL_H + (GLYPH_H - gr)
                if y < 0 or y >= H:
                    continue
                for c in range(CELL_W):
                    if bits & (128 >> c):
                        x = col * CELL_W + c
                        if 0 <= x < W:
                            screen[y][x] = 1
    return screen


def digests(screen: list[bytearray]) -> list[str]:
    """The same digest the browser side computes: h = h*31 + bit, mod 2^32."""
    out = []
    for row in screen:
        h = 0
        for bit in row:
            h = (h * 31 + bit) & 0xFFFFFFFF
        out.append(f"{h:08x}")
    return out


def cells_from_reference(elf: Path) -> list[str]:
    sys.path.insert(0, str(ROOT / "tools"))
    import rv64_ref

    machine = rv64_ref.Machine()
    machine.pc = rv64_ref.load_elf(machine, elf.read_bytes())
    machine.run()
    return [
        "".join(chr(machine.text[(row * COLS + col) * 2] & 0x7F) for col in range(COLS))
        for row in range(ROWS)
    ]


def main(argv: list[str]) -> int:
    if len(argv) < 3 or argv[1] not in ("cells", "hash"):
        print(__doc__)
        return 2
    if argv[1] == "hash":
        cells = Path(argv[2]).read_text(encoding="utf-8").splitlines()
    else:
        cells = cells_from_reference(Path(argv[2]))
        (ROOT / "build" / "bios-cells.txt").write_text("\n".join(cells) + "\n", encoding="utf-8")
        print("\n".join(line.rstrip() for line in cells))
    for row in digests(blit(cells)):
        print(row)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
