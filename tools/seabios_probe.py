#!/usr/bin/env python3
"""How far does real SeaBIOS get on this machine?

Our own BIOS is hand-written assembly: every instruction in it is one we put
there.  This tool asks a different question -- point the machine at the ROM
image Debian ships for QEMU (`/usr/share/seabios/bios.bin`, 128 KiB, the real
thing that boots real VMs) and see where it stops.

It is a probe, not a test.  It runs until something the machine cannot do, then
prints what that something was and disassembles the neighbourhood, so the next
step is chosen from evidence instead of from guesses.

    python3 tools/seabios_probe.py
    python3 tools/seabios_probe.py --limit 2000000
    python3 tools/seabios_probe.py --image build/seabios/root/usr/share/seabios/bios-256k.bin
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import i386_ref as ref  # noqa: E402

DEFAULT_IMAGE = ROOT / "build/seabios/root/usr/share/seabios/bios.bin"


def load(m: ref.Machine, image: bytes) -> int:
    """Copy the image so its last byte lands on 0xFFFFF, the way a ROM does."""
    base = 0x10_0000 - len(image)
    m.ram[base:0x10_0000] = image
    return base


def disasm(m: ref.Machine, starts, count: int = 12) -> list[str]:
    try:
        import capstone
    except ImportError:                       # pragma: no cover - optional
        return []
    lines = []
    for entry in starts:
        cs, eip, start = entry[0], entry[1], entry[2]
        mode = capstone.CS_MODE_32 if entry[4] == 4 else capstone.CS_MODE_16
        md = capstone.Cs(capstone.CS_ARCH_X86, mode)
        for ins in md.disasm(entry[3], start):
            lines.append(f"    {cs:04x}:{eip:08x}  {ins.address:05X}  "
                         f"{ins.bytes.hex():<18} {ins.mnemonic} {ins.op_str}")
            count -= 1
            if count <= 0:
                return lines
    return lines


def registers(m: ref.Machine) -> str:
    names = ("ax", "cx", "dx", "bx", "sp", "bp", "si", "di")
    regs = " ".join(f"{names[i]}={m.reg[i] & 0xFFFF:04x}" for i in range(8))
    segs = " ".join(f"{n}={m.sreg[i]:04x}"
                    for i, n in enumerate(("es", "cs", "ss", "ds", "fs", "gs")))
    mode = (f"cr0={m.cr[0]:08x} pe={int(m.pe)} dsz={m.dsz} "
            f"gdt={m.gdtr[0]:05x}/{m.gdtr[1]:04x} csbase={m.base[ref.SEG_CS]:05x}")
    return f"{regs}  {segs}  ip={m.eip:08x}\n  {mode}"


def probe(image: bytes, limit: int, trace: int = 0) -> int:
    m = ref.Machine()
    base = load(m, image)
    # a real machine starts with CS=FFFF and IP=FFF0 mapping to FFFF0, which is
    # the last 16 bytes of the ROM: SeaBIOS puts a far jump there
    m.sreg[ref.SEG_CS] = 0xF000
    m.eip = 0xFFF0
    m.sreg[ref.SEG_SS] = 0x0000
    m.reg[4] = 0x0700
    m.eflags |= ref.FLAG_IF
    print(f"image  {len(image)} bytes at {base:05x}, entry "
          f"{m.ram[0xFFFF0]:02x}{m.ram[0xFFFF1]:02x} "
          f"{m.ram[0xFFFF2]:02x}{m.ram[0xFFFF3]:02x} {m.ram[0xFFFF4]:02x}")

    trail: list[tuple[int, int, int, bytes, int]] = []
    while m.steps < limit:
        at = m.phys(ref.SEG_CS, m.eip)
        trail.append((m.sreg[ref.SEG_CS], m.eip, at,
                      bytes(m.ram[at:at + 12]), m.dsz))
        del trail[:-16]
        if trace and m.steps < trace:
            print(f"  {m.steps:6d} {m.sreg[ref.SEG_CS]:04x}:{m.eip:08x} pe={int(m.pe)} "
                  f"{trail[-1][3][:6].hex(' ')}")
        try:
            m.step()
        except Exception as exc:                       # noqa: BLE001 - the point
            where = m.phys(ref.SEG_CS, m.eip)
            print(f"\nstopped after {m.steps} instructions")
            print(f"  at {m.sreg[ref.SEG_CS]:04x}:{m.eip:04x} -> {where:05x}")
            print(f"  {type(exc).__name__}: {exc}")
            print(f"  bytes {bytes(m.ram[where:where + 12]).hex(' ')}")
            print(f"  {registers(m)}")
            print(f"  port writes seen: {sorted(m.ports)[:12]}")
            print(f"  text: {' | '.join(m.text().splitlines()[:3])!r}")
            lines = disasm(m, trail)
            if lines:
                print("  the road that led here:")
                print("\n".join(lines))
            return 1
    print(f"\nran {m.steps} instructions without an instruction the machine "
          f"does not know")
    print(f"  {registers(m)}")
    print(f"  port writes seen: {sorted(m.ports)[:12]}")
    lines = disasm(m, trail)
    if lines:
        print("  the road that led here:")
        print("\n".join(lines))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--image", type=Path, default=DEFAULT_IMAGE)
    ap.add_argument("--limit", type=int, default=200_000)
    ap.add_argument("--trace", type=int, default=0, help="print the first N instructions")
    args = ap.parse_args()
    if not args.image.exists():
        print(f"no image at {args.image}\n"
              f"  the ROM ships in Debian's seabios package:\n"
              f"  curl -sLO https://deb.debian.org/debian/pool/main/s/seabios/"
              f"seabios_1.17.0-1_all.deb\n"
              f"  ar x seabios_1.17.0-1_all.deb && tar xf data.tar.xz "
              f"-C build/seabios/root ./usr/share/seabios")
        return 2
    return probe(args.image.read_bytes(), args.limit, args.trace)


if __name__ == "__main__":
    raise SystemExit(main())
