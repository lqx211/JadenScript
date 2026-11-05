#!/usr/bin/env python3
"""Is the text plane a real dot matrix?

    python3 tools/glyph_check.py expected 'A' build/glyph-expected.png
    python3 tools/glyph_check.py compare  'A' build/glyph-live.png

`expected` writes the glyph the card is supposed to put on the top-left corner
of a 640 x 480 stage: black everywhere, an 8 x 15 bitmap pattern at (0, 0), one
image pixel per stage pixel. `compare` reads a screenshot of the running stage
and reports, pixel by pixel, where it differs.

The glyph table is the 8 x 15 terminal font (95 ASCII glyphs, one 8-bit mask per
row with the leftmost pixel in bit 7, row 0 the baseline row and row 14 the top).
"""

from __future__ import annotations

import json
import struct
import sys
import zlib
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
FONT = ROOT / "userspace" / "rv64" / "assets" / "lists" / "font.jsa"
STAGE_W, STAGE_H = 640, 480
CELL_W, CELL_H, GLYPH_H = 8, 16, 15


def glyph_bits(ch: str) -> list[int]:
    """The 15 row masks of `ch`, top row first."""
    values = [int(v) for v in json.loads(FONT.read_text(encoding="utf-8"))]
    index = ord(ch)                     # indexed by character code: CP437
    rows = values[index * GLYPH_H:(index + 1) * GLYPH_H]
    return list(reversed(rows))          # row 14 is the top


def glyph_grid(ch: str) -> list[str]:
    return ["".join("#" if (bits >> (7 - x)) & 1 else "." for x in range(CELL_W))
            for bits in glyph_bits(ch)]


# ---- PNG, in both directions ------------------------------------------------
def write_png(path: Path, rgb: np.ndarray) -> None:
    h, w, _ = rgb.shape
    raw = b"".join(b"\x00" + rgb[y].astype(np.uint8).tobytes() for y in range(h))

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))

    path.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw, 9))
        + chunk(b"IEND", b"")
    )


def read_png(path: Path) -> np.ndarray:
    data = path.read_bytes()
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise SystemExit(f"{path} is not a PNG")
    pos, idat, width, height, depth, colour = 8, b"", 0, 0, 8, 6
    while pos < len(data):
        length, tag = struct.unpack_from(">I4s", data, pos)
        body = data[pos + 8:pos + 8 + length]
        pos += 12 + length
        if tag == b"IHDR":
            width, height, depth, colour = struct.unpack_from(">IIBB", body)
        elif tag == b"IDAT":
            idat += body
        elif tag == b"IEND":
            break
    if depth != 8 or colour not in (2, 6):
        raise SystemExit(f"{path}: only 8-bit RGB/RGBA is read here (depth={depth} colour={colour})")
    channels = 3 if colour == 2 else 4
    raw = zlib.decompress(idat)
    stride = width * channels
    out = np.zeros((height, width), dtype=np.uint8)
    previous = np.zeros(stride, dtype=np.uint8)
    offset = 0
    for y in range(height):
        filter_type = raw[offset]
        line = np.frombuffer(raw, dtype=np.uint8, count=stride, offset=offset + 1).astype(np.int16)
        offset += stride + 1
        current = np.zeros(stride, dtype=np.int16)
        if filter_type == 0:
            current = line
        elif filter_type == 1:                       # Sub
            for i in range(stride):
                current[i] = (line[i] + (current[i - channels] if i >= channels else 0)) & 0xFF
        elif filter_type == 2:                       # Up
            current = (line + previous) & 0xFF
        elif filter_type == 3:                       # Average
            for i in range(stride):
                left = current[i - channels] if i >= channels else 0
                current[i] = (line[i] + ((left + previous[i]) >> 1)) & 0xFF
        elif filter_type == 4:                       # Paeth
            for i in range(stride):
                a = current[i - channels] if i >= channels else 0
                b = previous[i]
                c = previous[i - channels] if i >= channels else 0
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                pred = a if (pa <= pb and pa <= pc) else (b if pb <= pc else c)
                current[i] = (line[i] + pred) & 0xFF
        else:
            raise SystemExit(f"{path}: unknown filter {filter_type}")
        out[y] = (current.reshape(width, channels)[:, :3].mean(axis=1)).astype(np.uint8)
        previous = current.astype(np.uint8)
    return out


# ---- the two jobs ----------------------------------------------------------
def expected(ch: str, path: Path) -> int:
    image = np.zeros((STAGE_H, STAGE_W, 3), dtype=np.uint8)
    for y, row in enumerate(glyph_grid(ch)):
        for x, pixel in enumerate(row):
            if pixel == "#":
                image[y, x] = (255, 255, 255)
    write_png(path, image)
    print(f"{ch!r} -> {path}  ({STAGE_W}x{STAGE_H}, one image pixel per stage pixel)")
    for y, row in enumerate(glyph_grid(ch)):
        print(f"  {y:2d} {row}")
    return 0


def to_stage(image: np.ndarray) -> np.ndarray:
    """Area-average the capture down to one sample per stage pixel."""
    height, width = image.shape
    if (width, height) == (STAGE_W, STAGE_H):
        return image
    out = np.zeros((STAGE_H, STAGE_W), dtype=np.float64)
    for y in range(STAGE_H):
        y0, y1 = (y * height) // STAGE_H, ((y + 1) * height) // STAGE_H
        for x in range(STAGE_W):
            x0, x1 = (x * width) // STAGE_W, ((x + 1) * width) // STAGE_W
            out[y, x] = image[y0:y1, x0:x1].mean()
    return out.astype(np.uint8)


def compare(ch: str, path: Path) -> int:
    raw = read_png(path)
    print(f"capture {raw.shape[1]}x{raw.shape[0]} -> resampled to {STAGE_W}x{STAGE_H}")
    ink = to_stage(raw) > 96
    height, width = ink.shape
    print(f"{path}: {width}x{height}, ink pixels {int(ink.sum())}")
    want = glyph_grid(ch)
    # find the ink bounding box, then report it against the cell origin
    ys, xs = np.nonzero(ink)
    if len(ys) == 0:
        print("nothing inked at all")
        return 1
    y0, y1, x0, x1 = ys.min(), ys.max(), xs.min(), xs.max()
    print(f"ink box x {x0}..{x1}  y {y0}..{y1}  (cell 0 is x 0..7, y 0..15)")
    rows = []
    for y in range(y0, min(y0 + CELL_H, height)):
        rows.append("".join("#" if ink[y, x] else "." for x in range(x0, min(x0 + CELL_W + 2, width))))
    print("as drawn:")
    for i, row in enumerate(rows):
        print(f"  {y0 + i:3d} {row}")
    print("as intended:")
    for i, row in enumerate(want):
        print(f"  {i:3d} {row}")
    same = rows[:GLYPH_H] == want
    print("MATCH" if same else "DIFFERS")
    return 0 if same else 1


def main(argv: list[str]) -> int:
    if len(argv) < 4 or argv[1] not in ("expected", "compare"):
        print(__doc__)
        return 2
    action, ch, path = argv[1], argv[2], Path(argv[3])
    return expected(ch, path) if action == "expected" else compare(ch, path)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
