#!/usr/bin/env python3
"""Boot the i386 machine headlessly, twice, and print the screen each time.

    python3 tools/jswc decompile build/i386/i386.sb3 -o /tmp/i386.jsw
    python3 tools/i386_boot.py /tmp/i386.jsw

This is `jsw_sim.py` pointed at the *whole* machine rather than at one
procedure: the ROM, POST, the card and a reboot from the setup's own flag. It
exists because "the second boot dies on a black screen" is not a sentence
`i386_ref.py` can test -- the model has no card and no reset -- and because the
answer turned out to live in the difference between what `rdb` reads and what
`wrb` writes, which is a property of the blocks, not of the 80386.

Two things the decompiler leaves out are supplied here:

  * `import "assets/lists/*.jsa"` is skipped by the simulator, so the lists the
    decompile wrote next to the source are injected -- a list import is how the
    ROM arrives, and without it the guest executes zeros.
  * the TurboWarp build calls `Ram.*`, which is JavaScript. A bytearray stands
    in for it: same blocks, same little-endian words, `alloc` really allocates.

Options:
    --warm      reset after the first boot (default: do both)
    --cold      only the first boot
    --trace N   print the first N instructions after the reset
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

_spec = importlib.util.spec_from_file_location("jsw_sim", ROOT / "tools" / "jsw_sim.py")
sim = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sim)


def num(v) -> int:
    return int(sim.Env.num(v))


def install_ram_stub(env) -> bytearray:
    """`Ram` is the i386mem extension: one flat buffer, byte at a time."""
    buf = bytearray()

    def alloc(args):
        nonlocal buf
        buf = bytearray(max(0, num(args[0])))
        return float(len(buf))

    def read8(args):
        p = num(args[0])
        return float(buf[p]) if 0 <= p < len(buf) else 0.0

    def write8(args):
        p, v = num(args[0]), num(args[1]) & 255
        if 0 <= p < len(buf):
            buf[p] = v
        return 0.0

    def read32(args):
        p = num(args[0])
        return float(int.from_bytes(buf[p:p + 4], "little")) if 0 <= p <= len(buf) - 4 else 0.0

    def load(args):
        p, n = num(args[0]), num(args[1])
        vals = args[2] if len(args) > 2 and isinstance(args[2], list) else []
        for i in range(min(n, len(vals))):
            if 0 <= p + i < len(buf):
                buf[p + i] = num(vals[i]) & 255
        return 0.0

    def save(args):
        p, n = num(args[0]), num(args[1])
        lst = args[2] if len(args) > 2 else None
        if isinstance(lst, list):
            if len(lst) < n:
                lst.extend([0] * (n - len(lst)))
            for i in range(n):
                lst[i] = float(buf[p + i]) if 0 <= p + i < len(buf) else 0.0
        return 0.0

    env.ext.update({
        "Ram.alloc": alloc, "Ram.read8": read8, "Ram.write8": write8,
        "Ram.read32": read32, "Ram.load": load, "Ram.save": save,
        "Ram.size": lambda args: float(len(buf)),
    })
    return buf


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("source", help="a decompiled .jsw, next to its assets/lists")
    ap.add_argument("--cold", action="store_true", help="only the first boot")
    ap.add_argument("--warm", action="store_true", help="only the reset")
    ap.add_argument("--trace", type=int, default=0, help="instructions to print")
    ap.add_argument("--budget", type=int, default=60_000_000,
                    help="statements per boot before giving up")
    args = ap.parse_args(argv)

    src = Path(args.source)
    if args.trace:
        # the decompiled trace line is an f-string, which the simulator cannot
        # evaluate; an integer carries the same two numbers
        text = src.read_text().replace(
            'trace.add(f"{sr.get(2)}:{ip - 1}:{ir}");', "trace.add(ip * 256 + ir);")
        env = sim.Env()
        classes = sim.parse_program(text, env)
    else:
        env = sim.Env()
        classes = sim.parse_program(src.read_text(), env)

    assets = src.parent / "assets" / "lists"
    for f in assets.glob("*.jsa"):
        data = json.loads(f.read_text())
        env.lists[f.stem] = data          # `NAME.get(i)` reads the list table
        env.vars[f.stem] = data
    install_ram_stub(env)

    threads = []
    for entry in ("Cpu.greenFlag", "Display.greenFlag"):
        klass, _, proc = entry.partition(".")
        k = classes[klass]
        threads.append((sim.Thread(env, k.procs[proc], entry), k))

    def frames(n: int) -> None:
        for _ in range(n):
            for t, k in threads:
                if t.done:
                    continue
                env.procs = k.procs
                t.resume()

    def cells() -> list[int]:
        out = []
        for w in env.lists.get("X.text") or []:
            w = int(w)
            out.append(w % 65536)                  # two cells to a list item
            out.append((w // 65536) % 65536)
        return out

    def screen() -> None:
        c = cells()
        for r in range(30):
            row = "".join(chr(v % 256) if 32 <= v % 256 < 127 else "."
                          for v in c[r * 80:(r + 1) * 80]).rstrip()
            if row:
                print("    " + row)

    def boot(label: str) -> None:
        start = env.steps
        while env.steps - start < args.budget:
            frames(200)
            if any(v % 256 > 32 for v in cells()):
                frames(600)                        # let the line finish
                break
        print(f"--- {label}: +{env.steps - start:,} statements, "
              f"ip={env.vars.get('ip')}, monIp={env.vars.get('X.monIp')}, "
              f"reboots={env.vars.get('X.reboots')}, halted={env.vars.get('halted')}, "
              f"err={env.vars.get('err')!r}, extOn={env.vars.get('X.extOn')}")
        screen()

    if not args.warm:
        boot("cold")
    if not args.cold:
        blank = [0x0720_0720] * (80 * 30 // 2)
        env.lists["X.text"][:] = blank
        env.vars["X.reboot"] = 1
        env.vars["X.rebootExt"] = int(env.vars.get("X.extOn") or 0)
        if args.trace:
            trace = env.lists["trace"]
            env.vars["traceOn"] = 1
            trace.clear()
            frames(1)
            print("  first instructions after the reset:")
            print("    " + " ".join(f"{int(e) // 256}:{int(e) % 256:02x}" for e in trace[:args.trace]))
        boot("warm")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
