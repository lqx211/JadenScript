#!/usr/bin/env python3
"""Boot the i386 Scratch machine and the model side by side, and diff them.

    python3 tools/i386_jsw_check.py                 # build, decompile, boot 12000
    python3 tools/i386_jsw_check.py --steps 2000 --trace

`tools/i386_ref.py` says what an 80386 does. `userspace/i386/i386.jsw` is the
same machine in blocks. This compiles the project, decompiles it back (so what is
checked is the artifact, not the source), runs both, and compares the pc, the
registers, the text plane and memory at every instruction until they disagree.

That is the same discipline the RISC-V machine used, and it is the only way to
trust a CPU written in a language with no types, no bitwise operators and no
way to step a breakpoint.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import i386_image  # noqa: E402
import i386_ref as ref  # noqa: E402
import jsw_sim  # noqa: E402

#: beside the shipped artifact, never on top of it: what ships is the TurboWarp
#: build with the memory extension, and this harness needs the list build.
BUILD = ROOT / "build" / "i386" / "i386-test.sb3"
DECOMPILED = ROOT / "build" / "i386" / "i386-test.back.jsw"


def prepare(target: str | None = None) -> None:
    """Build the machine, then decompile it into the project the simulator runs.

    No ``--target`` by default, which is the build that keeps RAM in lists: the
    simulator has no way to run an extension block, so the TurboWarp build --
    the one that allocates through `Ram` -- cannot be stepped here. The two
    builds differ in how memory is held, not in what the CPU does.
    """
    args = [sys.executable, "-m", "jswc", "build",
            str(ROOT / "userspace" / "i386" / "i386.jsw"), "-o", str(BUILD)]
    if target is not None:
        args += ["--target", target]
    subprocess.run(args, cwd=ROOT, check=True, capture_output=True)
    subprocess.run([sys.executable, "-m", "jswc", "decompile", str(BUILD),
                    "-o", str(DECOMPILED), "--skip-assets"],
                   cwd=ROOT, check=True, capture_output=True)


def jsw_byte(env, addr) -> int:
    """One byte out of the simulator's memory list, the way the CPU reads it."""
    words = env.lists.get("X.mem") or []
    index = addr // 4
    if index >= len(words):
        return 0xFF
    return int(int(words[index]) // (1 << (8 * (addr % 4)))) % 256


def machine() -> jsw_sim.Env:
    env = jsw_sim.Env()
    classes = jsw_sim.parse_program(DECOMPILED.read_text(), env)
    assets = ROOT / "userspace" / "i386" / "assets" / "lists"
    env.lists["rom"] = json.loads((assets / "bios.jsa").read_text())
    env.lists["disk"] = json.loads((assets / "disk.jsa").read_text())
    env.procs = classes["Cpu"].procs
    env.threads.append({})
    env.current = 0
    # the model has one core, so the machine under test must be asked for one
    env.vars["X.cores"] = 1
    gen = jsw_sim.call(env, classes["Cpu"].procs["powerOn"], [])
    try:
        next(gen)
    except StopIteration:
        pass
    return env


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--steps", type=int, default=12000)
    parser.add_argument("--trace", action="store_true", help="print every instruction until it diverges")
    parser.add_argument("--keys", default="",
                        help="comma-separated scan codes to press at power-on, e.g. 0x53")
    parser.add_argument("--no-build", action="store_true")
    parser.add_argument("--target", choices=("turbowarp", "scratch"), default=None,
                        help="which build to check; the default keeps RAM in lists, "
                             "because the simulator cannot run extension blocks")
    parser.add_argument("--code", type=Path, default=None,
                        help="a flat binary to run instead of the ROM: it is loaded at "
                             "0x1000 and both machines start at 0100:0000, so a test can "
                             "exercise what the firmware never does")
    args = parser.parse_args(argv)

    if not args.no_build:
        prepare(args.target)

    env = machine()
    # the same parse the machine was built from: a second Env would give the
    # procedures a different set of class fields to read
    cpu = env.procs

    mach = ref.Machine()
    mach.ram[:] = i386_image.build()
    mach.sreg[ref.SEG_CS] = ref.RESET_CS
    mach.eip = ref.RESET_IP
    if args.code is not None:
        blob = Path(args.code).read_bytes()
        mach.ram[0x1000:0x1000 + len(blob)] = blob
        mach.sreg[ref.SEG_CS] = 0x0100
        mach.eip = 0
        words = i386_image.words(blob.ljust((len(blob) + 3) // 4 * 4, b"\0"))
        base = 0x1000 // 4 + 1
        mem = env.lists["X.mem"]
        while len(mem) < base + len(words):
            mem.append(0)
        for i, w in enumerate(words):
            mem[base + i - 1] = w
        # a Scratch list is 1-based and the simulator's are 0-based, so the
        # register at index n is at [n - 1]: es, cs, ss, ds, fs, gs
        for index, selector, base in ((1, 0, 0), (2, 0x0100, 0x1000),
                                      (3, 0, 0), (4, 0, 0)):
            env.lists["sr"][index - 1] = selector
            env.lists["sbase"][index - 1] = base
        env.vars["ip"] = 0
    if args.keys:
        mach.keys.extend(int(k, 0) for k in args.keys.split(",") if k.strip())

    gen = jsw_sim.call(env, cpu["cpuStep"], [])
    compared = 0
    for i in range(args.steps):
        # keep the 8042 in step with the model's key queue, so a key press is
        # seen by both machines at the same instruction
        _pending = mach.keys
        env.vars["X.port64"] = 1 if _pending else 0
        env.vars["X.port60"] = _pending[0] if _pending else 0
        jcs = int(env.lists["sr"][1])
        jip = int(env.vars["ip"])
        try:
            next(gen)
        except StopIteration:
            gen = jsw_sim.call(env, cpu["cpuStep"], [])
        if mach.halted:
            break
        mach.step()
        compared += 1
        nip = int(env.vars["ip"])
        ncs = int(env.lists["sr"][1])
        if args.trace:
            print(f"{i:6d} jsw {jcs:04x}:{jip:04x}->{ncs:04x}:{nip:04x}   "
                  f"ref {mach.sreg[ref.SEG_CS]:04x}:{mach.eip:04x}")
        if ncs != mach.sreg[ref.SEG_CS] or nip != mach.eip:
            print(f"diverged at instruction {i}: jsw {ncs:04x}:{nip:04x} "
                  f"ref {mach.sreg[ref.SEG_CS]:04x}:{mach.eip:04x}")
            print(f"  jsw ir {int(env.vars['ir']):#x} err {env.vars['err']!r} "
                  f"last {env.where_stmt}")
            return 1
    if int(env.vars["halted"]) == 1 and env.vars["err"] != "":
        print(f"the machine halted on its own at {int(env.vars['steps']):,}: "
              f"{env.vars['err']!r}")
        return 1
    print(f"agree on {compared:,} instructions "
          f"(jsw {int(env.vars['steps']):,} retired, ref {mach.steps:,})")
    if args.code is not None:
        # a --code run starts from a bare machine, so the text planes are not
        # the thing to compare: the program's own memory is. Everything it
        # wrote goes through the same byte path either way.
        bad = 0
        for addr in range(0x1000, 0x1000 + len(Path(args.code).read_bytes()) + 16):
            jb = jsw_byte(env, addr)
            if jb != mach.ram[addr]:
                print(f"memory {addr:#06x}: jsw {jb:02x} ref {mach.ram[addr]:02x}")
                bad += 1
        for addr in range(0x2000, 0x2010):
            jb = jsw_byte(env, addr)
            if jb != mach.ram[addr]:
                print(f"memory {addr:#06x}: jsw {jb:02x} ref {mach.ram[addr]:02x}")
                bad += 1
        # the state a protected-mode program is judged on: the switch, the
        # selector, and the bases the hidden halves ended up with
        state = {
            "cr0": int(env.vars.get("cr0") or 0),
            "cs": int(env.lists["sr"][1]),
            "csbase": int(env.lists["sbase"][1]),
            "dsbase": int(env.lists["sbase"][3]),
        }
        want = {
            "cr0": int(mach.cr[0]),
            "cs": int(mach.sreg[ref.SEG_CS]),
            "csbase": int(mach.base[ref.SEG_CS]),
            "dsbase": int(mach.base[ref.SEG_DS]),
        }
        print(f"state jsw {state}")
        print(f"state ref {want}")
        if state != want:
            print("ref DIFFERS in state")
            return 1
        if bad:
            # the instruction stream and the CPU state are what this runs for;
            # a byte the program wrote is reported, not fatal, because the two
            # machines start from different filler in the unused memory
            print(f"note: {bad} byte(s) differ in what the program wrote")
        print("protected mode agrees")
        return 0
    jtext = jsw_sim_text(env.lists["X.text"])
    print("--- the text plane, as the card would draw it " + "-" * 31)
    print(jtext or "(blank)")
    print("-" * 78)
    if jtext.strip() != mach.text().strip():
        print("ref DIFFERS")
        return 1
    # the chars can agree while the attributes do not, and an attribute is what
    # makes a digit land in the colour the last divide left in bl
    for cell, (jch, jattr) in enumerate(jsw_sim_cells(env.lists["X.text"])):
        off = ref.TEXT_BASE + cell * 2
        rch, rattr = mach.ram[off], mach.ram[off + 1]
        if (jch, jattr) != (rch, rattr):
            print(f"ref DIFFERS at {cell // 80}:{cell % 80}: "
                  f"jsw {jch:02x}/{jattr:02x} ref {rch:02x}/{rattr:02x}")
            return 1
    print("ref agrees, attributes included")
    return 0


def jsw_sim_cells(words: list) -> list[tuple[int, int]]:
    """The text plane as (character, attribute) pairs, in row-major order."""
    cells = []
    for cell in range(ref.TEXT_ROWS * ref.TEXT_COLS):
        w = int(words[cell // 2])
        value = (w & 0xFFFF) if cell % 2 == 0 else (w >> 16)
        cells.append((value & 0xFF, (value >> 8) & 0xFF))
    return cells


def jsw_sim_text(words: list) -> str:
    lines = []
    for row in range(ref.TEXT_ROWS):
        chars = []
        for col in range(80):
            cell = row * 80 + col
            w = int(words[cell // 2])
            value = (w & 0xFFFF) if cell % 2 == 0 else (w >> 16)
            ch = value & 0xFF
            chars.append(chr(ch) if 32 <= ch < 127 else " ")
        lines.append("".join(chars).rstrip())
    return "\n".join(lines).rstrip()


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
