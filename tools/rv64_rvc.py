"""The RISC-V C extension, as an *expansion* rather than a second decoder.

Every compressed instruction is the same operation as a 32-bit one with a
smaller encoding, so the machine reads a halfword, turns it into the 32-bit
instruction it means, and runs the decoder it already has. That is one table
instead of a parallel interpreter, and it is why adding RV64C to the Scratch
build costs a few hundred blocks and not a rewrite.

    python3 tools/rv64_rvc.py                 # the test vectors
    python3 tools/rv64_rvc.py --assemble      # check them against LLVM

The expansions are checked against the *assembler* rather than against my
reading of the spec: `--assemble` asks zig to assemble each pair (the
compressed form and the equivalent wide form) and compares the bytes with what
`expand` produces. An encoding I mis-remember cannot pass that.

Roman numerals below are the spec's own: CI, CR, CSS, CIW, CL, CS, CA, CB, CJ.
"""

from __future__ import annotations

MASK64 = (1 << 64) - 1

OP_LOAD = 0x03
OP_LOAD_FP = 0x07
OP_IMM = 0x13
OP_IMM_32 = 0x1B
OP_STORE = 0x23
OP_STORE_FP = 0x27
OP_REG = 0x33
OP_LUI = 0x37
OP_REG_32 = 0x3B
OP_BRANCH = 0x63
OP_JALR = 0x67
OP_JAL = 0x6F
OP_SYSTEM = 0x73


def _bits(value: int, hi: int, lo: int) -> int:
    return (value >> lo) & ((1 << (hi - lo + 1)) - 1)


def _sext(value: int, width: int) -> int:
    return value - (1 << width) if value >> (width - 1) else value


def _r(op: int, rd: int, f3: int, rs1: int, rs2: int, f7: int = 0) -> int:
    return (f7 << 25) | (rs2 << 20) | (rs1 << 15) | (f3 << 12) | (rd << 7) | op


def _i(op: int, rd: int, f3: int, rs1: int, imm: int) -> int:
    return ((imm & 0xFFF) << 20) | (rs1 << 15) | (f3 << 12) | (rd << 7) | op


def _s(rd_rs2: int, f3: int, rs1: int, imm: int) -> int:
    return (((imm >> 5) & 0x7F) << 25) | (rd_rs2 << 20) | (rs1 << 15) | (f3 << 12) | ((imm & 0x1F) << 7) | OP_STORE


def _b(rs1: int, f3: int, rs2: int, off: int) -> int:
    return (((off >> 12) & 1) << 31) | (((off >> 5) & 0x3F) << 25) | (rs2 << 20) | (rs1 << 15) | (f3 << 12) \
        | (((off >> 1) & 0xF) << 8) | (((off >> 11) & 1) << 7) | OP_BRANCH


def _j(rd: int, off: int) -> int:
    return (((off >> 20) & 1) << 31) | (((off >> 1) & 0x3FF) << 21) | (((off >> 11) & 1) << 20) \
        | (((off >> 12) & 0xFF) << 12) | (rd << 7) | OP_JAL


def _rdp(half: int) -> int:
    """The 3-bit register field of the CIW/CL/CS/CA/CB forms, + 8."""
    return _bits(half, 4, 2) + 8


def expand(half: int) -> int | None:
    """The 32-bit instruction a compressed halfword means, or None.

    None means "not a compressed instruction this machine implements": a
    floating-point form (there is no FPU here), `c.jal` (RV32 only) or a
    reserved encoding. The caller decides whether that traps or stops.
    """
    half &= 0xFFFF
    quadrant = half & 3
    f3 = _bits(half, 15, 13)

    if quadrant == 0:                                    # ---- quadrant 0
        if f3 == 0:                                      # c.addi4spn (CIW)
            nzuimm = (_bits(half, 10, 7) << 6) | (_bits(half, 12, 11) << 4) \
                | (_bits(half, 5, 5) << 3) | (_bits(half, 6, 6) << 2)
            if nzuimm == 0:                              # the all-zero word is illegal
                return None
            return _i(OP_IMM, _rdp(half), 0, 2, nzuimm)
        if f3 == 2:                                      # c.lw (CL)
            off = (_bits(half, 12, 10) << 3) | (_bits(half, 6, 6) << 2) | (_bits(half, 5, 5) << 6)
            return _i(OP_LOAD, _rdp(half), 2, _rdp(half >> 5), off)
        if f3 == 3:                                      # c.ld (CL, RV64)
            off = (_bits(half, 12, 10) << 3) | (_bits(half, 6, 5) << 6)
            return _i(OP_LOAD, _rdp(half), 3, _rdp(half >> 5), off)
        if f3 == 6:                                      # c.sw (CS)
            off = (_bits(half, 12, 10) << 3) | (_bits(half, 6, 6) << 2) | (_bits(half, 5, 5) << 6)
            return _s(_rdp(half), 2, _rdp(half >> 5), off)
        if f3 == 7:                                      # c.sd (CS, RV64)
            off = (_bits(half, 12, 10) << 3) | (_bits(half, 6, 5) << 6)
            return _s(_rdp(half), 3, _rdp(half >> 5), off)
        return None                                      # c.fld/c.fsd: no FPU here

    if quadrant == 1:                                    # ---- quadrant 1
        rd = _bits(half, 11, 7)
        imm6 = _sext((_bits(half, 12, 12) << 5) | _bits(half, 6, 2), 6)
        if f3 == 0:                                      # c.nop / c.addi (CI)
            return _i(OP_IMM, rd, 0, rd, imm6)
        if f3 == 1:                                      # c.addiw (CI, RV64)
            if rd == 0:                                  # the reserved hint form
                return None
            return _i(OP_IMM_32, rd, 0, rd, imm6)
        if f3 == 2:                                      # c.li (CI)
            return _i(OP_IMM, rd, 0, 0, imm6)
        if f3 == 3:                                      # c.addi16sp / c.lui (CI)
            if rd == 2:                                  # c.addi16sp
                nzimm = (_bits(half, 12, 12) << 9) | (_bits(half, 4, 3) << 7) \
                    | (_bits(half, 5, 5) << 6) | (_bits(half, 2, 2) << 5) | (_bits(half, 6, 6) << 4)
                if nzimm == 0:
                    return None
                return _i(OP_IMM, 2, 0, 2, _sext(nzimm, 10))
            nzimm = (_bits(half, 12, 12) << 17) | (_bits(half, 6, 2) << 12)
            if nzimm == 0:                               # c.lui with a zero immediate
                return None
            if nzimm & 0x20000:                          # sign: `lui` extends from bit 31
                nzimm |= 0xFFFC0000
            return (nzimm & 0xFFFFF000) | (rd << 7) | OP_LUI
        if f3 == 4:                                      # the ALU group (CA)
            f2 = _bits(half, 11, 10)
            rs1 = _rdp(half >> 5)
            shamt = (_bits(half, 12, 12) << 5) | _bits(half, 6, 2)
            if f2 == 0:                                  # c.srli
                return _i(OP_IMM, rs1, 5, rs1, shamt)
            if f2 == 1:                                  # c.srai
                return _i(OP_IMM, rs1, 5, rs1, shamt | 0x400)
            if f2 == 2:                                  # c.andi
                return _i(OP_IMM, rs1, 7, rs1, imm6)
            rs2 = _rdp(half)
            which = _bits(half, 6, 5)
            wide = _bits(half, 12, 12) == 1              # RV64: c.subw / c.addw
            if which == 0:
                return _r(OP_REG_32 if wide else OP_REG, rs1, 0, rs1, rs2, 0x20 if wide else 0x20)
            if which == 1 and wide:
                return _r(OP_REG_32, rs1, 0, rs1, rs2, 0x00)      # c.addw
            if which == 1:
                return _r(OP_REG, rs1, 4, rs1, rs2)               # c.xor
            if which == 2:
                return _r(OP_REG, rs1, 6, rs1, rs2)               # c.or
            return _r(OP_REG, rs1, 7, rs1, rs2)                   # c.and
        # f3 == 5/6/7: the jumps and branches (CJ and CB)
        if f3 == 5:                                      # c.j
            off = (_bits(half, 12, 12) << 11) | (_bits(half, 8, 8) << 10) \
                | (_bits(half, 10, 9) << 8) | (_bits(half, 6, 6) << 7) \
                | (_bits(half, 7, 7) << 6) | (_bits(half, 2, 2) << 5) \
                | (_bits(half, 11, 11) << 4) | (_bits(half, 5, 3) << 1)
            return _j(0, _sext(off, 12))
        off = (_bits(half, 12, 12) << 8) | (_bits(half, 6, 5) << 6) \
            | (_bits(half, 2, 2) << 5) | (_bits(half, 11, 10) << 3) | (_bits(half, 4, 3) << 1)
        off = _sext(off, 9)
        return _b(_rdp(half >> 5), 0 if f3 == 6 else 1, 0, off)

    if quadrant == 2:                                    # ---- quadrant 2
        rd = _bits(half, 11, 7)
        rs2 = _bits(half, 6, 2)
        if f3 == 0:                                      # c.slli (CI)
            shamt = (_bits(half, 12, 12) << 5) | _bits(half, 6, 2)
            return _i(OP_IMM, rd, 1, rd, shamt)
        if f3 == 2:                                      # c.lwsp (CI)
            if rd == 0:
                return None
            off = (_bits(half, 12, 12) << 5) | (_bits(half, 6, 4) << 2) | (_bits(half, 3, 2) << 6)
            return _i(OP_LOAD, rd, 2, 2, off)
        if f3 == 3:                                      # c.ldsp (CI, RV64)
            if rd == 0:
                return None
            off = (_bits(half, 12, 12) << 5) | (_bits(half, 6, 5) << 3) | (_bits(half, 4, 2) << 6)
            return _i(OP_LOAD, rd, 3, 2, off)
        if f3 == 4:                                      # c.jr / c.mv / c.ebreak / c.jalr / c.add (CR)
            if _bits(half, 12, 12) == 0:
                if rs2 == 0:                             # c.jr
                    if rd == 0:
                        return None
                    return _i(OP_JALR, 0, 0, rd, 0)
                return _r(OP_REG, rd, 0, 0, rs2)         # c.mv
            if rs2 == 0:
                if rd == 0:                              # c.ebreak
                    return 0x00100073
                return _i(OP_JALR, 1, 0, rd, 0)          # c.jalr
            return _r(OP_REG, rd, 0, rd, rs2)            # c.add
        if f3 == 6:                                      # c.swsp (CSS)
            off = (_bits(half, 12, 9) << 2) | (_bits(half, 8, 7) << 6)
            return _s(rs2, 2, 2, off)
        if f3 == 7:                                      # c.sdsp (CSS, RV64)
            off = (_bits(half, 12, 10) << 3) | (_bits(half, 9, 7) << 6)
            return _s(rs2, 3, 2, off)
        return None                                      # c.fldsp / c.fsdsp

    return None                                          # quadrant 3 would be 32-bit


#: (the instruction as the assembler writes it) -- the vectors the assembler
#: test uses, in the order the spec lists them
VECTORS: tuple[tuple[str, str], ...] = (
    ("c.addi4spn s0, sp, 16", "addi s0, sp, 16"),
    ("c.addi4spn a5, sp, 60", "addi a5, sp, 60"),
    ("c.lw a1, 4(a0)", "lw a1, 4(a0)"),
    ("c.lw s1, 60(a5)", "lw s1, 60(a5)"),
    ("c.ld a1, 8(a0)", "ld a1, 8(a0)"),
    ("c.sw a1, 4(a0)", "sw a1, 4(a0)"),
    ("c.sd a1, 8(a0)", "sd a1, 8(a0)"),
    ("c.nop", "addi zero, zero, 0"),
    ("c.addi a0, -1", "addi a0, a0, -1"),
    ("c.addiw a0, 5", "addiw a0, a0, 5"),
    ("c.li a0, -3", "addi a0, zero, -3"),
    ("c.lui a0, 0x10", "lui a0, 0x10"),
    ("c.lui a0, 0xfffe0", "lui a0, 0xfffe0"),
    ("c.lui a5, 0x3", "lui a5, 0x3"),
    ("c.addi16sp sp, -32", "addi sp, sp, -32"),
    ("c.srli a0, 5", "srli a0, a0, 5"),
    ("c.srai a0, 3", "srai a0, a0, 3"),
    ("c.andi a0, 7", "andi a0, a0, 7"),
    ("c.sub a0, a1", "sub a0, a0, a1"),
    ("c.xor a0, a1", "xor a0, a0, a1"),
    ("c.or a0, a1", "or a0, a0, a1"),
    ("c.and a0, a1", "and a0, a0, a1"),
    ("c.subw a0, a1", "subw a0, a0, a1"),
    ("c.addw a0, a1", "addw a0, a0, a1"),
    ("c.slli a0, 4", "slli a0, a0, 4"),
    ("c.lwsp a0, 4(sp)", "lw a0, 4(sp)"),
    ("c.ldsp a0, 8(sp)", "ld a0, 8(sp)"),
    ("c.mv a0, a1", "add a0, zero, a1"),
    ("c.add a0, a1", "add a0, a0, a1"),
    ("c.swsp a0, 4(sp)", "sw a0, 4(sp)"),
    ("c.sdsp a0, 8(sp)", "sd a0, 8(sp)"),
)


def main(argv: list[str]) -> int:
    import subprocess
    import sys
    import tempfile
    from pathlib import Path

    if "--assemble" not in argv:
        for count, (compressed, wide) in enumerate(VECTORS, 1):
            print(f"{count:2}. {compressed:26} -> {wide}")
        print(f"\n{len(VECTORS)} vectors; --assemble checks them against zig")
        return 0

    failures = 0
    for compressed, wide in VECTORS:
        source = f".option rvc\n.text\n{compressed}\n.option norvc\n{wide}\n"
        with tempfile.TemporaryDirectory() as tmp:
            asm = Path(tmp) / "v.S"
            obj = Path(tmp) / "v.o"
            asm.write_text(source)
            done = subprocess.run(
                # zig's default rv64 target already has C, which is all these
                # integer forms need (zig's -march names a CPU, not an ISA)
                ["zig", "cc", "-target", "riscv64-freestanding",
                 "-c", str(asm), "-o", str(obj)],
                capture_output=True, text=True)
            if done.returncode != 0:
                print(f"FAIL {compressed}: {done.stderr.strip()[:120]}")
                failures += 1
                continue
            data = obj.read_bytes()
            # the two instructions sit in .text, in that order, at the start
            offset = _text_offset(data)
            half = int.from_bytes(data[offset:offset + 2], "little")
            want = int.from_bytes(data[offset + 2:offset + 6], "little")
        got = expand(half)
        if got != want:
            print(f"FAIL {compressed}: half {half:04x} -> {got if got is None else f'{got:08x}'}"
                  f", assembler says {want:08x}")
            failures += 1
    print(f"{len(VECTORS) - failures}/{len(VECTORS)} match the assembler")
    return 1 if failures else 0


def _text_offset(elf: bytes) -> int:
    """Where .text starts in a fresh object file: the first section's offset."""
    import struct

    e_shoff = struct.unpack_from("<Q", elf, 0x28)[0]
    e_shentsize, e_shnum = struct.unpack_from("<HH", elf, 0x3A)
    for i in range(e_shnum):
        header = e_shoff + i * e_shentsize
        sh_type = struct.unpack_from("<I", elf, header + 4)[0]
        if sh_type == 1:                       # SHT_PROGBITS, which is .text here
            return struct.unpack_from("<Q", elf, header + 0x18)[0]
    raise ValueError("no .text")


if __name__ == "__main__":
    raise SystemExit(main(__import__("sys").argv))
