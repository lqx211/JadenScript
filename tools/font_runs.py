#!/usr/bin/env python3
"""The rv64 console font, pre-decoded into pen strokes.

``userspace/rv64/assets/lists/font.jsa`` holds the CP437 8x15 bitmap the i386
and rv64 projects draw their consoles with: one 8-bit row mask per glyph row,
256 glyphs x 15 rows. Their renderer walks the bits at run time -- eight tests
per row, 120 per character -- which is fine for a console that repaints a few
dirty cells, and far too slow for a HUD that four lines pay for on every frame.

The bits never change, so the runs are computed here instead: for every
printable character, the horizontal runs of set pixels as ``x, y, length``
triples. A downstream frame then reads three numbers per stroke and draws it
with one pen line, and the glyph costs the number of strokes it has rather than
the number of pixels in its box.

    python3 tools/font_runs.py userspace/rv64/assets/lists/font.jsa -o out/
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

WIDTH = 8
HEIGHT = 15
FIRST = 32
LAST = 126


def runs_of_glyph(rows: list[int]) -> list[tuple[int, int, int]]:
    """Horizontal runs of set pixels, as (x, y, length) from the top.

    The table is stored bottom row first -- the rv64 renderer reads row *gr* at
    ``y0 + 15 - gr`` -- so the rows are walked backwards and ``y = 0`` is the
    top of the glyph, which is the direction a pen draws in.
    """
    out: list[tuple[int, int, int]] = []
    for y, mask in enumerate(reversed(rows)):
        x = 0
        while x < WIDTH:
            if mask & (0x80 >> x):
                start = x
                while x < WIDTH and mask & (0x80 >> x):
                    x += 1
                out.append((start, y, x - start))
            else:
                x += 1
    return out


def decode(font: list) -> dict:
    """The three lists the project imports, plus the numbers that go with them."""
    chars: list[str] = []
    start: list[int] = []        # 1-based offsets into runs, then a sentinel
    runs: list[int] = []
    for code in range(FIRST, LAST + 1):
        base = code * HEIGHT
        rows = [int(v) for v in font[base:base + HEIGHT]]
        if len(rows) < HEIGHT:
            rows += [0] * (HEIGHT - len(rows))
        runs_ = runs_of_glyph(rows)
        chars.append(chr(code))
        start.append(len(runs) + 1)
        for x, y, length in runs_:
            runs.extend((x, y, length))
    start.append(len(runs) + 1)   # sentinel, so the last glyph has an end
    return {
        "chars": chars,
        "start": start,
        "runs": runs,
        "width": WIDTH,
        "height": HEIGHT,
        "strokes": len(runs) // 3,
    }


def write(data: dict, out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    for name, values in (("Font.chars", data["chars"]), ("Font.start", data["start"]),
                         ("Font.runs", data["runs"])):
        (out / f"{name}.jsa").write_text(json.dumps(values, separators=(",", ":")), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("font", help="font.jsa from the rv64 project")
    ap.add_argument("-o", "--out", required=True, help="directory for Font.*.jsa")
    args = ap.parse_args(argv)
    font = json.loads(Path(args.font).read_text(encoding="utf-8"))
    data = decode(font)
    write(data, Path(args.out))
    print(f"{len(data['chars'])} glyphs, {data['strokes']} strokes, "
          f"{len(data['runs'])} numbers -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
