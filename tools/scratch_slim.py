#!/usr/bin/env python3
"""Shrink a project's sounds to mp3, so it fits in a 50 MB upload.

    python3 tools/scratch_slim.py build/np.sb3 -o build/np-slim.sb3
    python3 tools/scratch_slim.py build/np.sb3 -o out.sb3 --bitrate 96k
    python3 tools/scratch_slim.py build/np.sb3 -o out.sb3 --dry-run

Scratch takes 50 MB a project and 10 MB a sound; the note player is 183 MB of
44.1 kHz stereo WAV samples, which is the first thing that has to go. Left
alone, WAV is the worst possible choice for that: the same audio as mp3 at
128 kbps is about a thirteenth of the size, and Scratch plays mp3 natively.

What must change with the format, and is easy to get wrong:

* ``md5ext`` — the name inside the zip is ``<md5>.<ext>``, and the VM looks the
  asset up by that string, so the extension in the field and in the zip have to
  be the same one;
* ``dataFormat`` — the VM reads it back to decide how to decode;
* ``sampleCount`` and ``rate`` — these are WAV-shaped fields. An mp3 has no
  frame count the way a WAV has a sample count, and the VM recomputes the real
  number on load, so the honest thing is to drop them rather than carry a lie;
* the **asset id is the md5 of the bytes** — a new encoding means a new id, and
  a wrong id is a project whose sounds are silent.

A failed conversion is left as WAV: a project that is slightly too big is a
problem, a project whose sounds are gone is a worse one.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

PROJECT_LIMIT = 50 * 1024 * 1024
ASSET_LIMIT = 10 * 1024 * 1024
DEFAULT_BITRATE = "128k"


def encode(data: bytes, bitrate: str) -> bytes:
    """WAV bytes in, mp3 bytes out. Raises on a bad input."""
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "a.mp3"
        done = subprocess.run(
            ["ffmpeg", "-v", "error", "-y", "-i", "pipe:0",
             "-codec:a", "libmp3lame", "-b:a", bitrate,
             # 44.1 kHz is what Scratch's audio engine is built around, and a
             # sample above it is the one thing that still sounds wrong there
             "-ar", "44100", "-ac", "2", str(out)],
            input=data, capture_output=True,
        )
        if done.returncode != 0:
            raise RuntimeError(done.stderr.decode("utf-8", "replace").strip()
                               or "ffmpeg failed")
        return out.read_bytes()


def slim(src: Path, dest: Path, bitrate: str, dry: bool) -> dict:
    with zipfile.ZipFile(src) as archive:
        data = json.loads(archive.read("project.json"))
        entries = {i.filename: archive.read(i.filename) for i in archive.infolist()}

    before = sum(len(v) for k, v in entries.items() if k.endswith(".wav"))
    assets: dict[str, bytes] = {}
    renames: dict[str, str] = {}
    failures: list[str] = []
    converted = 0

    for target in data["targets"]:
        for sound in target.get("sounds", []):
            old = sound.get("md5ext")
            if not old or not old.endswith(".wav"):
                continue
            raw = entries.get(old)
            if raw is None:
                failures.append(f"{target['name']}/{sound.get('name')}: missing {old}")
                continue
            try:
                mp3 = encode(raw, bitrate)
            except RuntimeError as problem:
                failures.append(f"{target['name']}/{sound.get('name')}: {problem}")
                continue
            digest = hashlib.md5(mp3).hexdigest()
            new = f"{digest}.mp3"
            assets[new] = mp3
            renames[old] = new
            sound["assetId"] = digest
            sound["md5ext"] = new
            sound["dataFormat"] = "mp3"
            # WAV-only fields: a frame count for a format that has none
            sound.pop("sampleCount", None)
            sound.pop("rate", None)
            converted += 1

    after = sum(len(v) for v in assets.values())

    # every asset nobody converted still has to travel
    for name, blob in entries.items():
        if name == "project.json" or name in renames:
            continue
        assets[name] = blob

    if not dry:
        dest.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as out:
            out.writestr("project.json", json.dumps(data, separators=(",", ":")))
            for name, blob in assets.items():
                out.writestr(name, blob)

    total = sum(len(v) for v in assets.values())
    return {
        "converted": converted,
        "failed": failures,
        "wav_bytes": before,
        "mp3_bytes": after,
        "project_bytes": dest.stat().st_size if not dry else 0,
        "uncompressed": total,
        "fits": total <= PROJECT_LIMIT,
        "oversized": sorted(
            ((len(b), n) for n, b in assets.items() if len(b) > ASSET_LIMIT),
            reverse=True,
        )[:10],
    }


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("project", type=Path, help="an .sb3 with WAV sounds")
    ap.add_argument("-o", "--out", type=Path, required=True, help="where to write it")
    ap.add_argument("--bitrate", default=DEFAULT_BITRATE,
                    help=f"mp3 bitrate (default {DEFAULT_BITRATE})")
    ap.add_argument("--dry-run", action="store_true", help="measure, write nothing")
    args = ap.parse_args(argv)

    if shutil.which("ffmpeg") is None:
        print("ffmpeg is not installed; it is how a WAV becomes an mp3",
              file=sys.stderr)
        return 2

    result = slim(args.project, args.out, args.bitrate, args.dry_run)
    print(f"{args.project} -> {args.out}"
          + (" (dry run)" if args.dry_run else ""))
    print(f"  {result['converted']} sounds converted to mp3 at {args.bitrate}")
    if result["converted"]:
        saved = result["wav_bytes"] / max(1, result["mp3_bytes"])
        print(f"  {result['wav_bytes'] / 1e6:.1f} MB of WAV -> "
              f"{result['mp3_bytes'] / 1e6:.1f} MB of mp3 ({saved:.1f}x)")
    if not args.dry_run:
        print(f"  {result['project_bytes'] / 1e6:.1f} MB on disk, "
              f"{result['uncompressed'] / 1e6:.1f} MB uncompressed")
        print("  " + ("✔ fits the 50 MB project limit" if result["fits"]
                      else "✖ still over the 50 MB project limit"))
    for size, name in result["oversized"]:
        print(f"  ✖ {size / 1e6:.1f} MB asset (limit 10 MB): {name}")
    for problem in result["failed"]:
        print(f"  ! left as it was: {problem}")
    return 0 if result["fits"] and not result["failed"] else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
