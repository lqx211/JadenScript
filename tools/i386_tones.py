#!/usr/bin/env python3
"""Write the square waves the speaker sprite plays.

The machine has no samples in it. Channel 2 of the 8254 divides its input clock
and a gate lets the result through, so the only thing a host needs in order to
make the same sound is one square wave per note: play the wrong one and you hear
a wrong note, not a wrong instrument.

These are generated rather than shipped as recordings because a square wave is
arithmetic -- half a period up, half a period down, at the note's frequency.
Each file is 16-bit mono at 22.05 kHz, long enough to cover the longest note the
boot sector holds, with a short fade at each end so that stopping one note and
starting the next does not click.
"""

from __future__ import annotations

import argparse
import math
import struct
import sys
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "userspace" / "i386" / "assets" / "sounds"

RATE = 22_050
SECONDS = 0.6
AMPLITUDE = 0.22
FADE = 0.004

#: the notes the boot sector's tune asks for: A4, C5, E5, G5
NOTES = {440: "A4", 523: "C5", 659: "E5", 784: "G5"}


def square(freq: int, rate: int = RATE, seconds: float = SECONDS) -> list[int]:
    """A square wave, faded in and out so it can be cut off without a click."""
    total = int(rate * seconds)
    fade = int(rate * FADE)
    samples: list[int] = []
    for n in range(total):
        value = 1.0 if (n * freq) % rate < rate / 2 else -1.0
        gain = 1.0
        if n < fade:
            gain = n / fade
        elif n > total - fade:
            gain = max(0.0, (total - n) / fade)
        samples.append(int(value * gain * AMPLITUDE * 32_767))
    return samples


def write(path: Path, samples: list[int], rate: int = RATE) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(rate)
        out.writeframes(struct.pack(f"<{len(samples)}h", *samples))


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", default=str(OUT))
    args = parser.parse_args(argv)
    out = Path(args.out)
    for freq, name in sorted(NOTES.items()):
        path = out / f"tone{freq}.wav"
        samples = square(freq)
        write(path, samples)
        print(f"{path.relative_to(ROOT)}: {name}, {freq} Hz, "
              f"{len(samples) / RATE:.2f} s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
