#!/usr/bin/env python3
"""Rebuild ``userspace/rv64/assets/lists/font.jsa`` as a CP437-shaped table.

The text plane stores one byte per cell, so the glyph table has to be indexed
by that byte. ASCII is what we had; a BIOS screen wants the line-drawing and
arrow glyphs a PC has, which in CP437 sit at 0x10..0x1F, 0xB0..0xDF and a
handful of others.

The table is 256 glyphs of 15 rows, each row a byte, most significant bit on
the left. ``index = code * 15``, which is what `paintCell` walks.

The ASCII half is kept exactly as it was (it came from the terminal font in
Linux.jsw); everything else is generated here, so the shapes are checked in and
diff-able rather than living inside a draw call.

    python3 tools/gen_rv64_font.py            # rewrite the asset
    python3 tools/gen_rv64_font.py --check    # verify it is up to date
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FONT = ROOT / "userspace" / "rv64" / "assets" / "lists" / "font.jsa"

ROWS = 15
COLS = 8
ASCII_FIRST = 32
ASCII_LAST = 126


def blank() -> list[int]:
    return [0] * ROWS


def put(rows: list[int], row: int, col: int) -> None:
    """Ink one pixel. Row 0 is the top of the glyph."""
    if 0 <= row < ROWS and 0 <= col < COLS:
        rows[row] |= 0x80 >> col


def hline(rows: list[int], row: int, first: int = 0, last: int = COLS - 1) -> None:
    for x in range(first, last + 1):
        put(rows, row, x)


def vline(rows: list[int], col: int, first: int = 0, last: int = ROWS - 1) -> None:
    for y in range(first, last + 1):
        put(rows, y, col)


#: single-line box drawing: (left, right, up, down)
SINGLE = {
    0xB3: (0, 0, 1, 1),   # │
    0xB4: (1, 0, 0, 0),   # ┤
    0xB5: (0, 1, 0, 0),   # ┴
    0xB6: (0, 0, 1, 0),   # ┬
    0xB7: (1, 1, 0, 0),   # ├
    0xBF: (0, 1, 0, 1),   # ┐
    0xC0: (0, 0, 0, 1),   # └  (row/col handled below)
    0xC1: (1, 1, 0, 1),   # ┴-like
    0xC2: (0, 0, 1, 1),
    0xC3: (1, 1, 1, 1),   # ┼
    0xC4: (1, 1, 0, 0),   # ─
    0xC5: (0, 1, 1, 1),
    0xD9: (0, 1, 0, 0),   # ┘
    0xDA: (0, 0, 0, 1),   # ┌
}


def single_at(rows: list[int], mid: int, col: int,
              up: int, down: int, left: int, right: int) -> None:
    """Draw a single-line cross-centre at (mid, col) into `rows`."""
    if left:
        hline(rows, mid, 0, col)
    if right:
        hline(rows, mid, col, COLS - 1)
    if up:
        vline(rows, col, 0, mid)
    if down:
        vline(rows, col, mid, ROWS - 1)
    if not (up or down or left or right):
        hline(rows, mid)


def single_line(up: int, down: int, left: int, right: int) -> list[int]:
    rows = blank()
    single_at(rows, ROWS // 2, COLS // 2 - 1, up, down, left, right)
    return rows


def double_line(up: int, down: int, left: int, right: int) -> list[int]:
    """Two single lines, one inside the other: that is what a double line is."""
    rows = blank()
    mid, col = ROWS // 2, COLS // 2 - 1
    single_at(rows, mid - 1, col - 1, up, down, left, right)
    single_at(rows, mid + 1, col + 1, up, down, left, right)
    return rows


def shade(level: int) -> list[int]:
    """0 = empty, 1 = light, 2 = medium, 3 = heavy, 4 = solid."""
    rows = blank()
    if level == 4:
        return [0xFF] * ROWS
    for y in range(ROWS):
        for x in range(COLS):
            if level == 1:
                ink = (x + y) % 4 == 0
            elif level == 2:
                ink = (x + y) % 2 == 0
            else:
                ink = (x + y) % 4 != 0
            if ink:
                put(rows, y, x)
    return rows


def arrow(direction: str, double: bool = False) -> list[int]:
    rows = blank()
    mid = ROWS // 2
    cx = COLS // 2 - 1
    if direction in ("up", "down"):
        y = 0 if direction == "up" else ROWS - 1
        step = 1 if direction == "up" else -1
        for i in range(4):
            for x in range(cx - i + 1, cx + i + 1):
                put(rows, y + i * step, x)
        for i in range(3, 8):
            put(rows, y + i * step, cx)
        if double:
            put(rows, y, cx - 1)
            put(rows, y, cx + 1)
    else:
        x = 0 if direction == "left" else COLS - 1
        step = 1 if direction == "left" else -1
        for i in range(4):
            for y in range(mid - i + 1, mid + i + 1):
                put(rows, y, x + i * step)
        for i in range(3, 8):
            put(rows, mid, x + i * step)
        if double:
            put(rows, mid - 1, x)
            put(rows, mid + 1, x)
    return rows


def triangle(up: bool) -> list[int]:
    """CP437 0x1E/0x1F: a filled triangle, apex up or down."""
    rows = blank()
    cx = COLS // 2
    for i in range(7):
        y = 3 + i if up else 10 - i
        half = i // 2 + (1 if i >= 6 else 0)
        for x in range(cx - half, cx + half + 1):
            put(rows, y, x)
    return rows


def block(kind: str) -> list[int]:
    rows = blank()
    if kind == "full":
        return [0xFF] * ROWS
    if kind == "lower":
        return [0] * 7 + [0xFF] * 8
    if kind == "upper":
        return [0xFF] * 7 + [0] * 8
    if kind == "left":
        return [0xF0] * ROWS
    if kind == "right":
        return [0x0F] * ROWS
    if kind == "small":
        return [0] * 4 + [0x3C] * 7 + [0] * 4
    return rows


def build_extras() -> dict[int, list[int]]:
    out: dict[int, list[int]] = {}

    # CP437 0x10..0x1F: the arrow and pointer glyphs
    out[0x10] = arrow("right")
    out[0x11] = arrow("left")
    out[0x12] = arrow("up")
    out[0x13] = arrow("down")
    out[0x18] = arrow("up")
    out[0x19] = arrow("down")
    out[0x1A] = arrow("right")
    out[0x1B] = arrow("left")
    out[0x1D] = arrow("left")
    out[0x1E] = triangle(True)
    out[0x1F] = triangle(False)
    out[0x11] = arrow("left")

    # the single-line frame
    out[0xDA] = single_line(0, 1, 0, 1)   # ┌
    out[0xBF] = single_line(0, 1, 1, 0)   # ┐
    out[0xC0] = single_line(1, 0, 0, 1)   # └
    out[0xD9] = single_line(1, 0, 1, 0)   # ┘
    out[0xC4] = single_line(0, 0, 1, 1)   # ─
    out[0xB3] = single_line(1, 1, 0, 0)   # │
    out[0xC3] = single_line(1, 1, 1, 1)   # ┼
    out[0xC2] = single_line(0, 1, 1, 1)   # ┬
    out[0xC1] = single_line(1, 0, 1, 1)   # ┴
    out[0xB4] = single_line(1, 1, 1, 0)   # ┤
    out[0xB7] = single_line(1, 1, 0, 1)   # ├

    # the double-line frame
    out[0xC9] = double_line(0, 1, 0, 1)   # ╔
    out[0xBB] = double_line(0, 1, 1, 0)   # ╗
    out[0xC8] = double_line(1, 0, 0, 1)   # ╚
    out[0xBC] = double_line(1, 0, 1, 0)   # ╝
    out[0xCD] = double_line(0, 0, 1, 1)   # ═
    out[0xBA] = double_line(1, 1, 0, 0)   # ║
    out[0xCC] = double_line(1, 1, 0, 1)   # ╠
    out[0xB9] = double_line(1, 1, 1, 0)   # ╣
    out[0xCB] = double_line(0, 1, 1, 1)   # ╦
    out[0xCA] = double_line(1, 0, 1, 1)   # ╩
    out[0xCE] = double_line(1, 1, 1, 1)   # ╬

    # shading and blocks
    out[0xB0] = shade(1)
    out[0xB1] = shade(2)
    out[0xB2] = shade(3)
    out[0xDB] = block("full")
    out[0xDC] = block("lower")
    out[0xDD] = block("left")
    out[0xDE] = block("right")
    out[0xDF] = block("upper")
    out[0xFE] = block("small")
    out[0xA0] = block("small")

    # a few symbols a setup screen uses
    check = blank()
    for i, (y, x) in enumerate([(9, 1), (10, 2), (11, 3), (8, 4), (7, 5), (6, 6), (5, 7)]):
        put(check, y, x)
        put(check, y - 1, x)
    out[0xFB] = check                      # √
    plusmin = blank()
    hline(plusmin, 5, 2, 5)
    vline(plusmin, 3, 3, 7)
    hline(plusmin, 10, 2, 5)
    out[0xF1] = plusmin                    # ±
    dot = blank()
    for y in range(6, 9):
        for x in range(3, 6):
            put(dot, y, x)
    out[0xFA] = dot                        # ·
    return out


def build() -> list[str]:
    """256 glyphs of 15 rows, indexed by character code."""
    # The ASCII glyphs come from the file itself, so regenerating is a no-op
    # and the originals (decoded from the terminal font) are never invented.
    # Both the plain 95-glyph layout and an already-expanded table are accepted.
    old = [int(v) for v in json.loads(FONT.read_text())]
    if len(old) == 256 * ROWS:
        ascii_rows = old[ASCII_FIRST * ROWS : (ASCII_LAST + 1) * ROWS]
    elif len(old) == (ASCII_LAST - ASCII_FIRST + 1) * ROWS:
        ascii_rows = old
    else:
        raise SystemExit(f"{FONT}: {len(old)} rows is not 95 or 256 glyphs")

    table = [0] * (256 * ROWS)
    for code in range(ASCII_FIRST, ASCII_LAST + 1):
        base = (code - ASCII_FIRST) * ROWS
        table[code * ROWS : (code + 1) * ROWS] = ascii_rows[base : base + ROWS]
    # Everything above is drawn top row first, but the table (like the ASCII
    # rows it was seeded from) is stored bottom row first, so flip on the way in.
    for code, rows in build_extras().items():
        table[code * ROWS : (code + 1) * ROWS] = rows[::-1]
    return [str(v) for v in table]


def main(argv: list[str]) -> int:
    data = build()
    text = json.dumps(data, separators=(", ", ": "))
    if "--check" in argv:
        current = FONT.read_text().strip()
        if current != text.strip():
            print(f"{FONT} is stale: run python3 tools/gen_rv64_font.py", file=sys.stderr)
            return 1
        print(f"{FONT} is up to date ({len(data) // ROWS} glyphs)")
        return 0
    FONT.write_text(text)
    print(f"{FONT}: {len(data) // ROWS} glyphs ({len(data)} rows)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
