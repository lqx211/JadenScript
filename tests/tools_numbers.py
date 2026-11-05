"""Tiny readers for the generated show block in ``anim3d/core.jsw``."""

from __future__ import annotations

import json
import re
from pathlib import Path

ENGINE = Path(__file__).resolve().parent.parent / "anim3d" / "core.jsw"


def _block() -> str:
    text = ENGINE.read_text(encoding="utf-8")
    return text.split("// >>> show-data BEGIN", 1)[1].split("// <<< show-data END", 1)[0]


def _rows(name: str) -> list[list[float]]:
    body = _block().split(f"public D3.{name} = [", 1)[1].split("];", 1)[0]
    values: list[float] = []
    for line in body.splitlines():
        line = line.split("//", 1)[0].strip().rstrip(",")
        if not line:
            continue
        values.extend(float(v) for v in line.split(","))
    return values


def _shot_rows() -> list[list[str]]:
    """`D3.shots` has a trailing caption column, so split four times, not everywhere."""
    body = _block().split("public D3.shots = [", 1)[1].split("];", 1)[0]
    rows = []
    for line in body.splitlines():
        line = line.split("//", 1)[0].strip().rstrip(",")
        if not line:
            continue
        cols = [c.strip() for c in line.split(",", 4)]
        assert len(cols) == 5, cols
        rows.append(cols)
    return rows


def shot_ticks() -> list[int]:
    return [int(r[0]) for r in _shot_rows()]


def shot_motifs() -> list[int]:
    return [int(r[1]) for r in _shot_rows()]


def shot_captions() -> list[str]:
    return [json.loads(r[4]) for r in _shot_rows()]


def key_ticks() -> list[int]:
    values = _rows("keys")
    return [int(values[i]) for i in range(0, len(values), 5)]


def total_ticks() -> int:
    m = re.search(r"public D3\.totalTicks = (\d+);", _block())
    assert m is not None
    return int(m.group(1))
