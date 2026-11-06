#!/usr/bin/env python3
"""Ask both machines what they do with every opcode, and print where they differ.

    python3 tools/i386_opcode_sweep.py            # the whole map
    python3 tools/i386_opcode_sweep.py --only 0F  # just the two-byte opcodes
    python3 tools/i386_opcode_sweep.py --op 0x66  # one of them

The reference model in `tools/i386_ref.py` is the specification, so the useful
question is not "does the Scratch machine run this opcode" -- any opcode can be
made to not crash -- but "does it land on the same registers and flags as the
model". Each probe loads a byte, a handful of zero operand bytes, and runs one
instruction in both machines.

The operand bytes are zero on purpose: `00` mod/rm is `[reg]` with no
displacement, which is the shortest legal encoding of "use memory at the address
in a register", and a probe that needs an immediate gets zero. That is enough to
tell a missing implementation from a working one.
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

BUILD = ROOT / "build" / "i386" / "i386-test.sb3"
DECOMPILED = ROOT / "build" / "i386" / "i386-test.back.jsw"
ASSETS = ROOT / "userspace" / "i386" / "assets" / "lists"

#: a scratch page well above the BIOS's own use, where the probe may write
PROBE_AT = 0x4000
#: where the probe's code lives
CODE_AT = 0x1000


def prepare() -> None:
    subprocess.run([sys.executable, "-m", "jswc", "build",
                    str(ROOT / "userspace" / "i386" / "i386.jsw"), "-o", str(BUILD)],
                   cwd=ROOT, check=True, capture_output=True)
    subprocess.run([sys.executable, "-m", "jswc", "decompile", str(BUILD),
                    "-o", str(DECOMPILED), "--skip-assets"],
                   cwd=ROOT, check=True, capture_output=True)


def machine() -> tuple:
    """The decompiled project, powered on, with its procedures ready."""
    env = jsw_sim.Env()
    source = DECOMPILED.read_text()
    classes = jsw_sim.parse_program(source, env)
    env.procs = classes["Cpu"].procs
    env.threads.append({})
    env.current = 0
    env.lists["rom"] = json.loads((ASSETS / "bios.jsa").read_text())
    env.lists["disk"] = json.loads((ASSETS / "disk.jsa").read_text())
    env.lists["ident"] = json.loads((ASSETS / "ident.jsa").read_text())
    env.vars["X.cores"] = 1
    gen = jsw_sim.call(env, env.procs["powerOn"], [])
    try:
        while True:
            next(gen)
    except StopIteration:
        pass
    return env, env.procs


def put(env, procs, addr: int, blob: bytes) -> None:
    mem = env.lists["X.mem"]
    base = addr // 4
    while len(mem) < base + 1 + (len(blob) + 3) // 4:
        mem.append(0)
    for i, byte in enumerate(blob):
        slot = base + i // 4
        word = int(mem[slot])
        place = 256 ** (i % 4)
        old = (word // place) % 256
        mem[slot] = word - old * place + byte * place


#: every register gets a different, small value, and DI points at the probe
#: page -- sixteen bits wide, because the Scratch machine's operand path is
#: sixteen bits wide and a comparison that pretends otherwise measures the
#: probe rather than the machine.
START_REGS = [0x1111, 0x2222, 0x3333, 0x4444, 0x7FF0, 0x5555, 0x6666, 0x4000]


def reset_jsw(env, procs, cs: int = 0x0100) -> None:
    """Put the CPU back to a known state without refilling 640 KiB of RAM.

    Each probe is an independent question. One that runs in protected mode, or
    with a segment register another probe left pointing somewhere else, is
    answering a different question -- the first sweep reported 255 divergences
    that were all this.
    """
    def call(name, *args):
        g = jsw_sim.call(env, procs[name], list(args))
        try:
            while True:
                next(g)
        except StopIteration:
            pass

    call("writeCr0", 0)
    for index in range(0, 7):
        call("setSeg", index, 0)
    call("setSeg", 2, cs)
    env.vars["ip"] = 0
    env.vars["fl"] = 0x0202
    env.vars["err"] = ""
    env.vars["halted"] = 0
    env.vars["fbAddr"] = -1
    for n, value in enumerate(START_REGS):
        env.lists["r"][n] = value


#: the window a probe can touch: the code, the [BX+SI] page, the string
#: destination and the stack a push or a call writes to
SAVE_FROM = 0x3000
SAVE_TO = 0x8200


def snapshot(env) -> list:
    mem = env.lists["X.mem"]
    return list(mem[SAVE_FROM // 4:SAVE_TO // 4])


def restore(env, saved: list) -> None:
    mem = env.lists["X.mem"]
    mem[SAVE_FROM // 4:SAVE_TO // 4] = saved


def probe_jsw(env, procs, code: bytes, cs: int = 0x0100,
              saved: list | None = None) -> dict:
    """Run one instruction on the Scratch machine."""
    if saved is not None:
        restore(env, saved)
    put(env, procs, CODE_AT, code)
    put(env, procs, PROBE_AT, (0xDEADBEEF).to_bytes(4, "little"))
    reset_jsw(env, procs, cs)
    gen = jsw_sim.call(env, procs["cpuStep"], [])
    try:
        while True:
            next(gen)
    except StopIteration:
        pass
    out = {
        "ip": int(env.vars["ip"]) & 0xFFFF,
        "flags": int(env.vars["fl"]) & 0xFFFF,
        "err": env.vars.get("err") or "",
        "regs": [int(env.lists["r"][n]) & 0xFFFFFFFF for n in range(8)],
    }
    mem = env.lists["X.mem"]
    out["mem"] = int(mem[PROBE_AT // 4]) & 0xFFFFFFFF
    return out


def probe_ref(code: bytes, cs: int = 0x0100) -> dict:
    mach = ref.Machine()
    mach.ram[:] = i386_image.build()
    mach.ram[CODE_AT:CODE_AT + len(code)] = code
    mach.sreg[ref.SEG_CS] = cs
    mach.eip = 0
    mach.eflags = ref.FLAG_ALWAYS | ref.FLAG_IF
    for n, value in enumerate(START_REGS):
        mach.reg[n] = value
    mach.write(PROBE_AT, 0xDEADBEEF, 4)
    try:
        mach.step()
    except NotImplementedError:
        mach.error = "not implemented"
    except Exception as exc:                       # the model refuses some shapes
        mach.error = f"{type(exc).__name__}"
    return {
        "ip": mach.eip & 0xFFFF,
        "flags": mach.eflags & 0xFFFF,
        "err": mach.error,
        "regs": list(mach.reg),
        "mem": mach.read(PROBE_AT, 4),
    }


def compare(name: str, jsw: dict, model: dict) -> str:
    if model["err"] and not jsw["err"]:
        return "ref-fails"
    if jsw["err"] and not model["err"]:
        return f"jsw-fails: {jsw['err'][:40]}"
    if model["err"]:
        return "both-fail"
    if jsw["ip"] != model["ip"]:
        return f"ip {jsw['ip']:04x} != {model['ip']:04x}"
    if jsw["flags"] != model["flags"]:
        return f"flags {jsw['flags']:04x} != {model['flags']:04x}"
    for n, (a, b) in enumerate(zip(jsw["regs"], model["regs"])):
        a, b = a & 0xFFFF, b & 0xFFFF
        if a != b:
            return f"r{n} {a:#x} != {b:#x}"
    if jsw["mem"] != model["mem"]:
        return f"mem {jsw['mem']:#x} != {model['mem']:#x}"
    return "ok"


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--no-build", action="store_true")
    ap.add_argument("--only", choices=("one", "two"), default=None,
                    help="one-byte or two-byte opcodes only")
    ap.add_argument("--op", default=None, help="a single opcode, e.g. 0x66")
    ap.add_argument("--quiet", action="store_true", help="only the summary")
    args = ap.parse_args(argv)

    if not args.no_build:
        prepare()
    env, procs = machine()

    codes: list[tuple[str, bytes]] = []
    if args.op:
        value = int(args.op, 0)
        if value > 0xFF:
            codes.append((f"{value:04X}", bytes([value >> 8, value & 0xFF]) + b"\0" * 6))
        else:
            codes.append((f"{value:02X}", bytes([value]) + b"\0" * 7))
    else:
        if args.only != "two":
            for op in range(256):
                codes.append((f"{op:02X}", bytes([op]) + b"\0" * 7))
        if args.only != "one":
            for op in range(256):
                codes.append((f"0F{op:02X}", bytes([0x0F, op]) + b"\0" * 6))

    counts: dict[str, int] = {}
    failures: list[tuple[str, str]] = []
    for name, code in codes:
        # A fresh machine per probe. Restoring registers and a memory window was
        # not enough: a probe that sets a segment, changes cr0 or leaves the
        # stack somewhere else is answering a different question, and the first
        # two attempts at this reported hundreds of divergences that were all
        # the harness. Powering on costs a fifth of a second and buys a clean
        # question.
        env, procs = machine()
        verdict = compare(name, probe_jsw(env, procs, code), probe_ref(code))
        counts[verdict.split(":")[0]] = counts.get(verdict.split(":")[0], 0) + 1
        if verdict != "ok":
            failures.append((name, verdict))

    if not args.quiet:
        for name, why in failures:
            print(f"{name}  {why}")
    total = sum(counts.values())
    print(f"\n{total} probes: " + ", ".join(f"{k} {v}" for k, v in sorted(counts.items())))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
