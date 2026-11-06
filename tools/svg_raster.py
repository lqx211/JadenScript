#!/usr/bin/env python3
"""Rasterise an SVG into `build/i386/logo.ega`, one EGA colour per pixel.

The splash is drawn with the text card's own font, so what it wants is a small
indexed image: each pixel becomes the nearest of the card's sixteen colours,
and the page itself (white, because Quick Look draws SVG on white) becomes a
dot, meaning "let the splash show through". Quick Look does the rasterising --
it is the only SVG renderer this machine always has -- and the result is
cropped to its bounding box.

    python3 tools/svg_raster.py ~/Downloads/turbowarp.svg
"""

from __future__ import annotations

import argparse
import struct
import subprocess
import tempfile
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "build" / "i386" / "logo.ega"
BG = 235                     # anything lighter than this is page, not ink

#: the sixteen EGA colours, in the order attributes number them
EGA = [(0, 0, 0), (0, 0, 170), (0, 170, 0), (0, 170, 170),
       (170, 0, 0), (170, 0, 170), (170, 85, 0), (170, 170, 170),
       (85, 85, 85), (85, 85, 255), (85, 255, 85), (85, 255, 255),
       (255, 85, 85), (255, 85, 255), (255, 255, 85), (255, 255, 255)]

#: the four entries that have no colour in them, which a saturated pixel is
#: not allowed to become
GREYS = {0, 7, 8, 15}

HEX = "0123456789abcdef"


def read_png(path: Path) -> tuple[int, int, int, list[bytes]]:
    data = path.read_bytes()
    pos, idat, w, h, ct = 8, b"", 0, 0, 0
    while pos < len(data):
        length, kind = struct.unpack(">I4s", data[pos:pos + 8])
        pos += 8
        if kind == b"IHDR":
            w, h, _, ct = struct.unpack(">IIBB", data[pos:pos + 10])
        elif kind == b"IDAT":
            idat += data[pos:pos + length]
        pos += length + 4
    raw = zlib.decompress(idat)
    bpp = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}[ct]
    stride = w * bpp
    rows: list[bytes] = []
    prev = bytearray(stride)
    i = 0
    for _ in range(h):
        filt = raw[i]
        i += 1
        line = bytearray(raw[i:i + stride])
        i += stride
        for x in range(stride):
            a = line[x - bpp] if x >= bpp else 0
            b = prev[x]
            c = prev[x - bpp] if x >= bpp else 0
            if filt == 1:
                line[x] = (line[x] + a) & 255
            elif filt == 2:
                line[x] = (line[x] + b) & 255
            elif filt == 3:
                line[x] = (line[x] + (a + b) // 2) & 255
            elif filt == 4:
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                line[x] = (line[x] + (a if pa <= pb and pa <= pc else
                                      b if pb <= pc else c)) & 255
        rows.append(bytes(line))
        prev = line
    return w, h, bpp, rows


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("svg", type=Path)
    ap.add_argument("-s", "--size", type=int, default=800)
    ap.add_argument("-o", "--out", type=Path, default=OUT)
    args = ap.parse_args()

    with tempfile.TemporaryDirectory() as tmp:
        subprocess.run(["qlmanage", "-t", "-s", str(args.size), "-o", tmp,
                        str(args.svg)], check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        png = next(Path(tmp).glob("*.png"))
        w, h, bpp, rows = read_png(png)

    ink = [[0] * w for _ in range(h)]
    for y in range(h):
        line = rows[y]
        for x in range(w):
            o = x * bpp
            if bpp >= 3:
                if min(line[o], line[o + 1], line[o + 2]) < BG:
                    ink[y][x] = 1
            elif line[o] < BG:
                ink[y][x] = 1

    def ega(x: int, y: int) -> str:
        """The nearest of the sixteen colours, or '.' for the page itself."""
        line = rows[y]
        o = x * bpp
        if bpp >= 3:
            rgb = (line[o], line[o + 1], line[o + 2])
        else:
            rgb = (line[o], line[o], line[o])
        if min(rgb) >= BG:
            return "."
        # A pale colour is nearer to mid grey than to the saturated entry that
        # actually matches its hue, so greys are only offered to pixels that
        # are themselves grey. Without this the pink head comes out white and
        # the green blob comes out grey.
        coloured = max(rgb) - min(rgb) > 30
        best, bestd = 0, 1 << 30
        for i, (r, g, b) in enumerate(EGA):
            if coloured and i in GREYS:
                continue
            if not coloured and i not in GREYS:
                continue
            d = (rgb[0] - r) ** 2 + (rgb[1] - g) ** 2 + (rgb[2] - b) ** 2
            if d < bestd:
                best, bestd = i, d
        return HEX[best]

    ys = [y for y in range(h) if any(ink[y])]
    xs = [x for x in range(w) if any(ink[y][x] for y in range(h))]
    top, bottom, left, right = ys[0], ys[-1], xs[0], xs[-1]
    body = ["".join(ega(x, y) for x in range(left, right + 1))
            for y in range(top, bottom + 1)]

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(f"{len(body[0])}x{len(body)}\n" + "\n".join(body) + "\n")
    print(f"{args.out}  {len(body[0])} x {len(body)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
