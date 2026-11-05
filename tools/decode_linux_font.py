"""Decode Linux.jsw's terminal font into the row bitmaps `D3.gfont` holds.

`drawGlyph` in Linux.jsw walks each entry of ``assets/lists/font.jsa`` while it
draws, which is cheap to write but expensive to run: every glyph costs a
``letterOf`` per run plus a pen move per column. This module replays that walk
once, offline, and hands back the pixels it would have inked.

The walk, transcribed from Linux.jsw:

* character 1 of the string is the starting pen state, ``1`` meaning down;
* every following character is a run length in hex, ``0`` standing for 15;
  a non-zero run toggles the pen *after* the run is drawn;
* runs accumulate in ``_3`` and the pen climbs the current column; when ``_3``
  passes 15 the pen jumps to the bottom of the next column.

A glyph is 15 rows tall and occupies columns 4 to 10 of a 16-wide cell, so the
result is shifted four columns left to fit an 8-bit row.

Run it directly to eyeball a string::

    python3 tools/decode_linux_font.py "Hello, World!"
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FONT = ROOT / "assets" / "lists" / "font.jsa"

#: Rows per glyph in the decoded table.
ROWS = 15
#: Columns per glyph, one byte of each row.
COLS = 8
#: The glyphs sit four columns into the cell; shift them to the left edge.
SHIFT = 4
#: Printable ASCII, the range the table covers.
FIRST = 32
LAST = 126


def walk(entry: str) -> set[tuple[int, int]]:
    """The ``(column, row)`` pixels one run-length entry inks, row 0 lowest."""
    ink: set[tuple[int, int]] = set()
    state = entry[0] == "1"
    acc = 0
    x = y = 0
    for ch in entry[1:]:
        drawing = state
        run = int(ch, 16)
        if run > 0:
            state = not state
        else:
            run = 15
        acc += run
        if acc > 15:
            while acc > 15:
                x += 1
                y = 0
                acc -= 16
            drawn = acc
        else:
            drawn = run
        if drawing:
            ink.update((x, y + k) for k in range(drawn))
        y += drawn
    return ink


def glyphs() -> list[set[tuple[int, int]]]:
    """Every printable ASCII glyph, in code point order."""
    entries = json.loads(FONT.read_text(encoding="utf-8"))
    return [walk(entries[c]) for c in range(FIRST, LAST + 1)]


def decode_font() -> list[int]:
    """The flat table `D3.gfont` holds: `ROWS` row bitmaps per glyph."""
    table: list[int] = []
    for ink in glyphs():
        for row in range(ROWS):
            bits = 0
            for col in range(COLS):
                bits = (bits << 1) | (1 if (col + SHIFT, row) in ink else 0)
            table.append(bits)
    return table


def render(text: str) -> str:
    """A text block of ``text``, for checking the decode by eye."""
    table = decode_font()
    ink = set()
    for i, ch in enumerate(text):
        base = (ord(ch) - FIRST) * ROWS
        if not FIRST <= ord(ch) <= LAST:
            continue
        for row in range(ROWS):
            bits = table[base + row]
            for col in range(COLS):
                if (bits >> (COLS - 1 - col)) & 1:
                    ink.add((i * COLS + col, row))
    if not ink:
        return ""
    width = max(x for x, _ in ink) + 1
    return "\n".join(
        "".join("#" if (x, y) in ink else "." for x in range(width))
        for y in range(ROWS - 1, -1, -1)
    )


def main(argv: list[str]) -> int:
    text = " ".join(argv[1:]) or "Hello, World!"
    print(render(text))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
