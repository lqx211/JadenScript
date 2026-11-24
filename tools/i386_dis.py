#!/usr/bin/env python3
"""A disassembler for the machine, and the tables the block version reads.

    python3 tools/i386_dis.py -b userspace/i386/bios.bin -a 0x5C0 -n 4
    python3 tools/i386_dis.py --check userspace/i386/bios.bin
    python3 tools/i386_dis.py --emit userspace/i386/disasm.inc.jsw

`X.dbgDis` in the machine is a live view of the instruction at the parked
instruction pointer, and it has to be right about one thing above all: *how
long the instruction is*. A decoder that renders `mov ax,[bx]` beautifully and
then claims it is three bytes long puts every following line one byte out, and
a debugger that is one byte out is worse than no debugger.

So the length is what this file is verified on: `--check` disassembles a flat
binary here and with `ndisasm`, instruction for instruction, and reports the
first place the two disagree about where an instruction ends. The mnemonic has
to agree too; the operand text is compared loosely, because two assemblers will
always spell a memory operand differently.

The block version in `userspace/i386/disasm.inc.jsw` is the same tables and the
same shapes -- `--emit` writes them -- and `tests/test_i386.py` runs both over
the same bytes and requires the identical text. Two implementations that agree
with each other and with ndisasm is as close to "this is right" as a decoder
gets.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

R8 = ["al", "cl", "dl", "bl", "ah", "ch", "dh", "bh"]
R16 = ["ax", "cx", "dx", "bx", "sp", "bp", "si", "di"]
R32 = ["eax", "ecx", "edx", "ebx", "esp", "ebp", "esi", "edi"]
SREG = ["es", "cs", "ss", "ds", "fs", "gs"]
CC = ["o", "no", "b", "ae", "e", "ne", "be", "a",
      "s", "ns", "p", "np", "l", "ge", "le", "g"]
ALU = ["add", "or", "adc", "sbb", "and", "sub", "xor", "cmp"]
SHIFT = ["rol", "ror", "rcl", "rcr", "shl", "shr", "sal", "sar"]
GRP3 = ["test", "test", "not", "neg", "mul", "imul", "div", "idiv"]
GRP4 = ["inc", "dec", "?", "?", "?", "?", "?", "?"]
GRP5 = ["inc", "dec", "call", "call", "jmp", "jmp", "push", "?"]
GRP6 = ["sldt", "str", "lldt", "ltr", "verr", "verw", "?", "?"]
GRP7 = ["sgdt", "sidt", "lgdt", "lidt", "smsw", "?", "lmsw", "invlpg"]
GRP8 = ["?", "?", "?", "?", "bt", "bts", "btr", "btc"]
X87 = {
    0xD8: ["fadd", "fmul", "fcom", "fcomp", "fsub", "fsubr", "fdiv", "fdivr"],
    0xDC: ["fadd", "fmul", "fcom", "fcomp", "fsub", "fsubr", "fdiv", "fdivr"],
    0xDE: ["faddp", "fmulp", "fcomp", "fcompp", "fsubrp", "fsubp", "fdivrp", "fdivp"],
}

#: operand shapes. The block version implements exactly these, and the table
#: below is what tells it which one an opcode wants. The order is the numbering
#: both sides agree on, so it may be appended to but never reordered.
SHAPE_NAMES = [
    "NONE", "EB_GB", "EV_GV", "GB_EB", "GV_EV", "AL_IB", "RAX_IV", "EB_IB",
    "EV_IV", "EV_IBS", "JB", "JV", "IB", "IW", "OB", "OV", "ZB", "ZV", "G1B",
    "G1V", "G1VS", "G2B", "G2V", "G3B", "G3V", "G4B", "G5V", "AP", "EW", "G6",
    "G7", "CV", "X87OP", "JCC32", "SETCC", "CMOV", "MOVZX", "IMUL3", "NOP0F",
    "SSE", "DB", "STRING", "LOOP", "IN_IB", "IN_DX", "AAM", "RETF_IW", "MOFFS",
    "BSWAP", "XADD", "NOPFX", "PUSHA", "ENTER", "BOUND", "LAR", "SHLD", "BT",
    "GRP8B", "CMPXCHG8", "SLDT", "CLTS", "MOV_TREG", "ZREG", "SEGIMP",
    "MOV_EW_SREG", "MOV_SREG_EW",
]
for _i, _name in enumerate(SHAPE_NAMES):
    globals()[_name] = _i

PREFIX = {
    0xF0: "lock", 0xF2: "repne", 0xF3: "rep", 0x2E: "cs:", 0x36: "ss:",
    0x3E: "ds:", 0x26: "es:", 0x64: "fs:", 0x65: "gs:",
}


def _table() -> tuple[list[str], list[int], list[int], list[int], list[int]]:
    """(mnemonics, code, shape) for one byte, and code/shape for 0F xx."""
    mn: list[str] = []
    index: dict[str, int] = {}

    def word(name: str) -> int:
        if name not in index:
            mn.append(name)
            index[name] = len(mn) - 1
        return index[name]

    code = [-1] * 256
    shape = [DB] * 256

    def put(op: int, name: str, form: int) -> None:
        code[op] = word(name)
        shape[op] = form

    for k, name in enumerate(ALU):
        put(0x00 + k * 8, name, EB_GB)
        put(0x01 + k * 8, name, EV_GV)
        put(0x02 + k * 8, name, GB_EB)
        put(0x03 + k * 8, name, GV_EV)
        put(0x04 + k * 8, name, AL_IB)
        put(0x05 + k * 8, name, RAX_IV)
    for op, name, reg in ((0x06, "push", "es"), (0x07, "pop", "es"),
                          (0x0E, "push", "cs"), (0x16, "push", "ss"),
                          (0x17, "pop", "ss"), (0x1E, "push", "ds"),
                          (0x1F, "pop", "ds")):
        put(op, name + " " + reg, SEGIMP)
    for op, name in ((0x27, "daa"), (0x2F, "das"), (0x37, "aaa"), (0x3F, "aas")):
        put(op, name, NONE)
    for op in range(0x26, 0x40, 8):
        code[op] = None          # a segment prefix, handled before this table
        shape[op] = NONE
    for i in range(8):
        put(0x40 + i, "inc", ZREG)
        put(0x48 + i, "dec", ZREG)
        put(0x50 + i, "push", ZREG)
        put(0x58 + i, "pop", ZREG)
        put(0x90 + i, "xchg", ZREG)
        put(0xB0 + i, "mov", ZB)
        put(0xB8 + i, "mov", ZV)
    put(0x60, "pusha", PUSHA)
    put(0x61, "popa", NONE)
    put(0x62, "bound", BOUND)
    put(0x63, "arpl", EW)
    put(0x68, "push", EV_IV)
    put(0x69, "imul", IMUL3)
    put(0x6A, "push", EB_IB)
    put(0x6B, "imul", EV_IBS)
    for op, name in ((0x6C, "insb"), (0x6D, "insw"), (0x6E, "outsb"), (0x6F, "outsw")):
        put(op, name, STRING)
    for i in range(16):
        put(0x70 + i, "j" + CC[i], JB)
    put(0x80, "grp1", G1B)
    put(0x81, "grp1", G1V)
    put(0x82, "grp1", G1B)
    put(0x83, "grp1", G1VS)
    put(0x84, "test", EB_GB)
    put(0x85, "test", EV_GV)
    put(0x86, "xchg", EB_GB)
    put(0x87, "xchg", EV_GV)
    put(0x88, "mov", EB_GB)
    put(0x89, "mov", EV_GV)
    put(0x8A, "mov", GB_EB)
    put(0x8B, "mov", GV_EV)
    put(0x8C, "mov", MOV_EW_SREG)
    put(0x8D, "lea", GV_EV)
    put(0x8E, "mov", MOV_SREG_EW)
    put(0x8F, "pop", G5V)
    put(0x98, "cbw", NONE)
    put(0x99, "cwd", NONE)
    put(0x9A, "call", AP)
    put(0x9B, "wait", NONE)
    put(0x9C, "pushf", NONE)
    put(0x9D, "popf", NONE)
    put(0x9E, "sahf", NONE)
    put(0x9F, "lahf", NONE)
    put(0xA0, "mov", OB)
    put(0xA1, "mov", OV)
    put(0xA2, "mov", OB)
    put(0xA3, "mov", OV)
    put(0xA4, "movsb", STRING)
    put(0xA5, "movsw", STRING)
    put(0xA6, "cmpsb", STRING)
    put(0xA7, "cmpsw", STRING)
    put(0xA8, "test", AL_IB)
    put(0xA9, "test", RAX_IV)
    put(0xAA, "stosb", STRING)
    put(0xAB, "stosw", STRING)
    put(0xAC, "lodsb", STRING)
    put(0xAD, "lodsw", STRING)
    put(0xAE, "scasb", STRING)
    put(0xAF, "scasw", STRING)
    put(0xC0, "grp2", G2B)
    put(0xC1, "grp2", G2V)
    put(0xC2, "ret", IW)
    put(0xC3, "ret", NONE)
    put(0xC4, "les", GV_EV)
    put(0xC5, "lds", GV_EV)
    put(0xC6, "mov", EB_IB)
    put(0xC7, "mov", EV_IV)
    put(0xC8, "enter", ENTER)
    put(0xC9, "leave", NONE)
    put(0xCA, "retf", RETF_IW)
    put(0xCB, "retf", NONE)
    put(0xCC, "int3", NONE)
    put(0xCD, "int", IB)
    put(0xCE, "into", NONE)
    put(0xCF, "iret", NONE)
    for i, name in enumerate(SHIFT):
        put(0xD0 + i, name, G2B)
        put(0xD8 + i, name, G2V)
    for i, name in enumerate(SHIFT):
        put(0xD2 + i, name, G2B)
        put(0xDA + i, name, G2V)
    put(0xD4, "aam", AAM)
    put(0xD5, "aad", AAM)
    put(0xD7, "xlat", NONE)
    for i in range(8):
        put(0xD8 + i, "esc", X87OP)      # fadd/fmul... by the modrm's reg
    put(0xE0, "loopne", LOOP)
    put(0xE1, "loope", LOOP)
    put(0xE2, "loop", LOOP)
    put(0xE3, "jcxz", LOOP)
    put(0xE4, "in", IN_IB)
    put(0xE5, "in", IN_IB)
    put(0xE6, "out", IN_IB)
    put(0xE7, "out", IN_IB)
    put(0xE8, "call", JV)
    put(0xE9, "jmp", JV)
    put(0xEA, "jmp", AP)
    put(0xEB, "jmp", JB)
    put(0xEC, "in", IN_DX)
    put(0xED, "in", IN_DX)
    put(0xEE, "out", IN_DX)
    put(0xEF, "out", IN_DX)
    put(0xF1, "int1", NONE)
    put(0xF4, "hlt", NONE)
    put(0xF5, "cmc", NONE)
    put(0xF6, "grp3", G3B)
    put(0xF7, "grp3", G3V)
    put(0xF8, "clc", NONE)
    put(0xF9, "stc", NONE)
    put(0xFA, "cli", NONE)
    put(0xFB, "sti", NONE)
    put(0xFC, "cld", NONE)
    put(0xFD, "std", NONE)
    put(0xFE, "grp4", G4B)
    put(0xFF, "grp5", G5V)

    code0f = [-1] * 256
    shape0f = [DB] * 256

    def put0(op: int, name: str, form: int) -> None:
        code0f[op] = word(name)
        shape0f[op] = form

    put0(0x00, "grp6", G6)
    put0(0x01, "grp7", G7)
    put0(0x02, "lar", LAR)
    put0(0x03, "lsl", LAR)
    put0(0x06, "clts", CLTS)
    put0(0x08, "invd", NONE)
    put0(0x09, "wbinvd", NONE)
    put0(0x0B, "ud2", NONE)
    for i in range(8):
        put0(0x18 + i, "nop", NOP0F)
        put0(0x40 + i, "cmov" + CC[i], CMOV)
        put0(0x48 + i, "cmov" + CC[i], CMOV)
        put0(0x90 + i, "set" + CC[i], SETCC)
        put0(0xC8 + i, "bswap", BSWAP)
    for i in range(16):
        put0(0x80 + i, "j" + CC[i], JCC32)
    for op in range(0x20, 0x24):
        put0(op, "mov", CV)
    for op in range(0x24, 0x28):
        put0(op, "mov", MOV_TREG)
    put0(0x30, "wrmsr", NONE)
    put0(0x31, "rdtsc", NONE)
    put0(0x32, "rdmsr", NONE)
    put0(0x33, "rdpmc", NONE)
    put0(0x34, "sysenter", NONE)
    put0(0x35, "sysexit", NONE)
    put0(0xA0, "push fs", SEGIMP)
    put0(0xA1, "pop fs", SEGIMP)
    put0(0xA2, "cpuid", NONE)
    put0(0xA3, "bt", BT)
    put0(0xA4, "shld", SHLD)
    put0(0xA5, "shld", SHLD)
    put0(0xA8, "push gs", SEGIMP)
    put0(0xA9, "pop gs", SEGIMP)
    put0(0xAA, "rsm", NONE)
    put0(0xAB, "bts", BT)
    put0(0xAC, "shrd", SHLD)
    put0(0xAD, "shrd", SHLD)
    put0(0xAF, "imul", IMUL3)
    put0(0xB0, "cmpxchg", EB_GB)
    put0(0xB1, "cmpxchg", EV_GV)
    put0(0xB2, "lss", GV_EV)
    put0(0xB3, "btr", BT)
    put0(0xB4, "lfs", GV_EV)
    put0(0xB5, "lgs", GV_EV)
    put0(0xB6, "movzx", MOVZX)
    put0(0xB7, "movzx", MOVZX)
    put0(0xBA, "grp8", GRP8B)
    put0(0xBB, "btc", BT)
    put0(0xBE, "movsx", MOVZX)
    put0(0xBF, "movsx", MOVZX)
    put0(0xC0, "xadd", EB_GB)
    put0(0xC1, "xadd", EV_GV)
    put0(0xC7, "cmpxchg8b", CMPXCHG8)
    return mn, code, shape, code0f, shape0f


MNEMONICS, CODE, SHAPE, CODE0F, SHAPE0F = _table()


class Reader:
    def __init__(self, mem: bytes, pc: int) -> None:
        self.mem = mem
        self.pc = pc

    def peek(self, i: int) -> int:
        at = self.pc + i
        return self.mem[at] if 0 <= at < len(self.mem) else 0

    def u8(self, i: int) -> int:
        return self.peek(i)

    def s8(self, i: int) -> int:
        b = self.peek(i)
        return b - 256 if b >= 128 else b

    def u16(self, i: int) -> int:
        return self.peek(i) | (self.peek(i + 1) << 8)

    def s16(self, i: int) -> int:
        v = self.u16(i)
        return v - 65536 if v >= 32768 else v

    def u32(self, i: int) -> int:
        return self.u16(i) | (self.u16(i + 2) << 16)

    def s32(self, i: int) -> int:
        v = self.u32(i)
        return v - 4294967296 if v >= 2147483648 else v


def _hex(value: int, width: int) -> str:
    return f"0x{value:0{width}x}"


def _mask(value: int, bits: int) -> int:
    return value & (0xFFFFFFFF if bits == 32 else 0xFFFF)


def _imm(value: int) -> str:
    """A displacement or immediate, the way a person reads one: small negative
    numbers keep their sign, because `[bp-4]` says what `[bp+0xfffc]` means."""
    return _hex(value & 0xFFFFFFFF, 4 if value > 0xFF or value < -0x80 else 2)


def decode(mem: bytes, pc: int, bits: int = 16) -> tuple[int, str]:
    """One instruction: how many bytes it is, and how it reads."""
    r = Reader(mem, pc)
    i = 0
    osz = bits                        # operand size
    asz = bits                        # address size
    prefix = ""
    seg = ""
    while True:
        b = r.u8(i)
        if b == 0x66:
            osz = 32 if bits == 16 else 16
        elif b == 0x67:
            asz = 32 if bits == 16 else 16
        elif b in (0x26, 0x2E, 0x36, 0x3E, 0x64, 0x65):
            seg = PREFIX[b]
        elif b in (0xF0, 0xF2, 0xF3):
            prefix = PREFIX[b] + " "
        else:
            break
        i += 1

    op = r.u8(i)
    i += 1
    name = ""
    if op == 0x0F:
        op2 = r.u8(i)
        i += 1
        if CODE0F[op2] is not None and CODE0F[op2] >= 0:
            name = MNEMONICS[CODE0F[op2]]
            shape = SHAPE0F[op2]
            op = 0x100 + op2
        else:
            # two-byte and not one this machine has: show both bytes, so the
            # line after it still starts where the CPU would have stopped
            return i, f"{prefix}{seg}db {_hex(0x0F, 2)},{_hex(op2, 2)}"
    elif CODE[op] is not None and CODE[op] >= 0:
        name = MNEMONICS[CODE[op]]
        shape = SHAPE[op]
    else:
        return i, f"{prefix}{seg}db {_hex(op, 2)}"

    text, i = _render(r, i, shape, op, name, osz, asz, seg, prefix, bits)
    return i, text


def _regs(osz: int) -> list[str]:
    return R32 if osz == 32 else R16


def _modrm(r: Reader, i: int, osz: int, asz: int, size8: bool,
           seg: str) -> tuple[str, int, int]:
    """(operand text, bytes used, the modrm's reg field)."""
    m = r.u8(i)
    i += 1
    mod, reg, rm = m >> 6, (m >> 3) & 7, m & 7
    if mod == 3:
        table = R8 if size8 else _regs(osz)
        return table[rm], i, reg
    parts: list[str] = []
    width = 2 if asz == 16 else 4
    if asz == 16:
        base = [["bx", "si"], ["bx", "di"], ["bp", "si"], ["bp", "di"],
                ["si"], ["di"], ["bp"], ["bx"]][rm]
        disp = 0
        have = False
        if mod == 0 and rm == 6:
            base = []                # 0x1234 absolute, not [bp+0x1234]
            disp = r.u16(i)
            i += 2
            have = True
        elif mod == 1:
            disp = r.s8(i)
            i += 1
            have = True
        elif mod == 2:
            disp = r.s16(i)
            i += 2
            have = True
        parts.extend(base)
        tail = ""
        if have:
            if not parts:
                # an address with no register in it is as wide as the address
                # size: masking it to the printed width cut `0x576` to `0x76`
                tail = _hex(_mask(disp, 16 if asz == 16 else 32), width)
            elif disp > 0:
                tail = "+" + _hex(disp, width)
            elif disp < 0:
                tail = "-" + _hex(-disp, width)
    else:
        have = False
        disp = 0
        tail = ""
        base = ""
        scale = 0
        if rm == 4:
            sib = r.u8(i)
            i += 1
            scale, index, base_no = sib >> 6, (sib >> 3) & 7, sib & 7
            if index != 4:
                parts.append(R32[index] + ("*" + str(1 << scale) if scale else ""))
            if base_no == 5 and mod == 0:
                disp = r.u32(i)
                i += 4
                have = True
            else:
                base = R32[base_no]
        elif mod == 0 and rm == 5:
            disp = r.u32(i)
            i += 4
            have = True
        else:
            base = R32[rm]
        if base:
            parts.insert(0, base)
        if mod == 1:
            disp = r.s8(i)
            i += 1
            have = True
        elif mod == 2:
            disp = r.u32(i)
            i += 4
            have = True
        if have:
            if not parts:
                tail = _hex(disp, width)
            elif disp > 0:
                tail = "+" + _hex(disp, width)
            elif disp < 0:
                tail = "-" + _hex(-disp, width)
    body = "+".join(parts) + tail
    kind = ("byte " if size8 else ("word " if osz == 16 else "dword ")) if not size8 else "byte "
    if not size8:
        kind = "word " if osz == 16 else "dword "
    return f"{kind}[{seg}{body}]", i, reg


def _render(r: Reader, i: int, shape: int, op: int, name: str, osz: int,
            asz: int, seg: str, prefix: str, bits: int = 16) -> tuple[str, int]:
    def done(text: str, at: int) -> tuple[str, int]:
        return f"{prefix}{text}", at

    if shape == NONE:
        return done(name, i)
    if shape == PUSHA:
        return done("pusha" if osz == 16 else "pushad", i)
    if shape == ENTER:
        return done(f"enter {_hex(r.u16(i), 4)},{_hex(r.u8(i + 2), 2)}", i + 3)
    if shape == RETF_IW:
        return done(f"{name} {_hex(r.u16(i), 4)}", i + 2)
    if shape in (IB, AAM):
        if shape == AAM:
            return done(f"{name} {_hex(r.u8(i), 2)}", i + 1)
        return done(f"{name} {_hex(r.u8(i), 2)}", i + 1)
    if shape == IW:
        return done(f"{name} {_hex(r.u16(i), 4)}", i + 2)
    if shape == LOOP:
        target = (r.pc + i + 1 + r.s8(i)) & 0xFFFF
        return done(f"{name} {_hex(target, 4)}", i + 1)
    if shape == JB:
        target = (r.pc + i + 1 + r.s8(i)) & 0xFFFF
        return done(f"{name} {_hex(target, 4)}", i + 1)
    if shape == JCC32:
        cond = "j" + CC[op & 0xF]
        if osz == 32:
            return done(f"{cond} {_hex(_mask(r.pc + i + 4 + r.s32(i), bits), 8)}", i + 4)
        return done(f"{cond} {_hex(_mask(r.pc + i + 2 + r.s16(i), bits), 4)}", i + 2)
    if shape == JV:
        if osz == 32:
            target = _mask(r.pc + i + 4 + r.s32(i), bits)
            return done(f"{name} {_hex(target, 8 if bits == 32 else 4)}", i + 4)
        target = (r.pc + i + 2 + r.s16(i)) & 0xFFFF
        return done(f"{name} {_hex(target, 4)}", i + 2)
    if shape == AP:
        off = r.u16(i)
        segv = r.u16(i + 2)
        return done(f"{name} {_hex(segv, 4)}:{_hex(off, 4)}", i + 4)
    if shape == OB:
        addr = r.u16(i) if asz == 16 else r.u32(i)
        width = 4 if asz == 16 else 8
        if op == 0xA0:
            return done(f"mov al, byte [{seg}{_hex(addr, width)}]", i + (2 if asz == 16 else 4))
        return done(f"mov byte [{seg}{_hex(addr, width)}], al", i + (2 if asz == 16 else 4))
    if shape == OV:
        addr = r.u16(i) if asz == 16 else r.u32(i)
        width = 4 if asz == 16 else 8
        used = 2 if asz == 16 else 4
        acc = "ax" if osz == 16 else "eax"
        kind = "word" if osz == 16 else "dword"
        if op == 0xA1:
            return done(f"mov {acc}, {kind} [{seg}{_hex(addr, width)}]", i + used)
        return done(f"mov {kind} [{seg}{_hex(addr, width)}], {acc}", i + used)
    if shape == MOFFS:
        return done(name, i)
    if shape == EW:
        # arpl is the only opcode left on this shape, and it is two operands:
        # the word it adjusts and the one it adjusts it against. The mod == 3
        # branch here used to read a segment register, which was the leftover
        # of `mov Ew,Sreg` -- and `63 C1` is `arpl cx, ax`, not `arpl es`.
        dst, i, reg = _modrm(r, i, osz, asz, False, seg)
        return done(f"arpl {dst}, {R16[reg]}", i)
    if shape == ZB:
        return done(f"{name} {R8[op & 7]}, {_hex(r.u8(i), 2)}", i + 1)
    if shape == SEGIMP:
        return done(name, i)
    if shape in (MOV_EW_SREG, MOV_SREG_EW):
        reg = (r.u8(i) >> 3) & 7
        sreg = SREG[reg] if reg < 6 else "?"
        dst, i, _ = _modrm(r, i, osz, asz, False, seg)
        if shape == MOV_EW_SREG:
            return done(f"mov {dst}, {sreg}", i)
        return done(f"mov {sreg}, {dst}", i)
    if shape == ZREG:
        return done(f"{name} {_regs(osz)[op & 7]}", i)
    if shape == ZV:
        if osz == 32:
            return done(f"{name} {_regs(osz)[op & 7]}, {_hex(r.u32(i), 8)}", i + 4)
        return done(f"{name} {_regs(osz)[op & 7]}, {_hex(r.u16(i), 4)}", i + 2)
    if shape == AL_IB:
        return done(f"{name} al, {_hex(r.u8(i), 2)}", i + 1)
    if shape == RAX_IV:
        if osz == 32:
            return done(f"{name} eax, {_hex(r.u32(i), 8)}", i + 4)
        return done(f"{name} ax, {_hex(r.u16(i), 4)}", i + 2)
    if shape == IN_IB:
        acc = "al" if (op & 1) == 0 else ("ax" if osz == 16 else "eax")
        if name == "out":
            return done(f"out {_hex(r.u8(i), 2)}, {acc}", i + 1)
        return done(f"in {acc}, {_hex(r.u8(i), 2)}", i + 1)
    if shape == IN_DX:
        acc = "al" if (op & 1) == 0 else ("ax" if osz == 16 else "eax")
        if name == "out":
            return done(f"out dx, {acc}", i)
        return done(f"in {acc}, dx", i)
    if shape == STRING:
        return done(name, i)
    if shape in (EB_GB, EV_GV, GB_EB, GV_EV, EB_IB, EV_IV, EV_IBS,
                 G1B, G1V, G1VS, G2B, G2V, G3B, G3V, G4B, G5V, G6, G7,
                 SETCC, CMOV, MOVZX, IMUL3, NOP0F, BOUND, LAR, SHLD, BT,
                 GRP8B, CMPXCHG8, SSE, X87OP, CV, MOV_TREG):
        return _render_modrm(r, i, shape, op, name, osz, asz, seg, prefix)
    return done(name, i)


def _render_modrm(r: Reader, i: int, shape: int, op: int, name: str, osz: int,
                  asz: int, seg: str, prefix: str) -> tuple[str, int]:
    def done(text: str, at: int) -> tuple[str, int]:
        return f"{prefix}{text}", at

    if shape == EB_GB:
        dst, i, reg = _modrm(r, i, osz, asz, True, seg)
        return done(f"{name} {dst}, {R8[reg]}", i)
    if shape == GB_EB:
        src, i, reg = _modrm(r, i, osz, asz, True, seg)
        return done(f"{name} {R8[reg]}, {src}", i)
    if shape == EV_GV:
        dst, i, reg = _modrm(r, i, osz, asz, False, seg)
        return done(f"{name} {dst}, {_regs(osz)[reg]}", i)
    if shape == GV_EV:
        if name in ("lea", "les", "lds", "lss", "lfs", "lgs"):
            m = r.u8(i)
            if name == "lea":
                text, i, _ = _modrm(r, i, osz, asz, False, "")
                return done(f"lea {_regs(osz)[(m >> 3) & 7]}, {text}", i)
            text, i, reg = _modrm(r, i, osz, asz, False, seg)
            return done(f"{name} {_regs(osz)[reg]}, {text}", i)
        if shape == GV_EV and name == "mov":
            src, i, reg = _modrm(r, i, osz, asz, False, seg)
            return done(f"mov {_regs(osz)[reg]}, {src}", i)
        src, i, reg = _modrm(r, i, osz, asz, False, seg)
        return done(f"{name} {_regs(osz)[reg]}, {src}", i)
    if shape == EB_IB:
        dst, i, _ = _modrm(r, i, osz, asz, True, seg)
        return done(f"{name} {dst}, {_hex(r.u8(i), 2)}", i + 1)
    if shape == EV_IV:
        dst, i, _ = _modrm(r, i, osz, asz, False, seg)
        if osz == 32:
            return done(f"{name} {dst}, {_hex(r.u32(i), 8)}", i + 4)
        return done(f"{name} {dst}, {_hex(r.u16(i), 4)}", i + 2)
    if shape == EV_IBS:
        dst, i, reg = _modrm(r, i, osz, asz, False, seg)
        imm = r.s8(i)
        if name == "grp1":
            return done(f"{ALU[reg]} {dst}, {_imm(imm)}", i + 1)
        if name == "imul":
            return done(f"imul {_regs(osz)[reg]}, {dst}, {_imm(imm)}", i + 1)
        return done(f"{name} {dst}, {_imm(imm)}", i + 1)
    if shape in (G1B, G1V, G1VS):
        size8 = shape == G1B
        dst, i, reg = _modrm(r, i, osz, asz, size8, seg)
        if shape == G1VS:
            return done(f"{ALU[reg]} {dst}, {_imm(r.s8(i))}", i + 1)
        if size8:
            return done(f"{ALU[reg]} {dst}, {_hex(r.u8(i), 2)}", i + 1)
        if osz == 32:
            return done(f"{ALU[reg]} {dst}, {_hex(r.u32(i), 8)}", i + 4)
        return done(f"{ALU[reg]} {dst}, {_hex(r.u16(i), 4)}", i + 2)
    if shape in (G2B, G2V):
        size8 = shape == G2B
        dst, i, reg = _modrm(r, i, osz, asz, size8, seg)
        if op % 256 in (0xC0, 0xC1):
            return done(f"{SHIFT[reg]} {dst}, {_hex(r.u8(i), 2)}", i + 1)
        if op % 256 in (0xD0, 0xD1):
            return done(f"{SHIFT[reg]} {dst}, 1", i)
        return done(f"{SHIFT[reg]} {dst}, cl", i)
    if shape in (G3B, G3V):
        size8 = shape == G3B
        dst, i, reg = _modrm(r, i, osz, asz, size8, seg)
        if reg == 0:
            if size8:
                return done(f"test {dst}, {_hex(r.u8(i), 2)}", i + 1)
            if osz == 32:
                return done(f"test {dst}, {_hex(r.u32(i), 8)}", i + 4)
            return done(f"test {dst}, {_hex(r.u16(i), 4)}", i + 2)
        if reg == 1:
            return done(f"test {dst}", i)       # the second encoding, unused
        return done(f"{GRP3[reg]} {dst}", i)
    if shape == G4B:
        dst, i, reg = _modrm(r, i, osz, asz, True, seg)
        return done(f"{GRP4[reg]} {dst}", i)
    if shape == G5V:
        dst, i, reg = _modrm(r, i, osz, asz, False, seg)
        if reg in (3, 5):
            return done(f"call far {dst}", i)
        if reg == 2:
            return done(f"call {dst}", i)
        if reg == 4:
            return done(f"jmp {dst}", i)
        if reg == 6:
            return done(f"push {dst}", i)
        return done(f"{GRP5[reg]} {dst}", i)
    if shape == G6:
        dst, i, reg = _modrm(r, i, osz, asz, False, seg)
        return done(f"{GRP6[reg]} {dst}", i)
    if shape == G7:
        dst, i, reg = _modrm(r, i, osz, asz, False, seg)
        return done(f"{GRP7[reg]} {dst}", i)
    if shape == SETCC:
        dst, i, _ = _modrm(r, i, osz, asz, True, seg)
        return done(f"set{CC[op & 0xF]} {dst}", i)
    if shape == CMOV:
        src, i, reg = _modrm(r, i, osz, asz, False, seg)
        return done(f"cmov{CC[op & 0xF]} {_regs(osz)[reg]}, {src}", i)
    if shape == MOVZX:
        m = r.u8(i)
        src8 = (m >> 3) & 7
        src, i, reg = _modrm(r, i, osz, asz, False, seg)
        return done(f"{name} {_regs(osz)[reg]}, {src}", i)
    if shape == IMUL3:
        reg = (r.u8(i) >> 3) & 7
        if op % 256 == 0x69:
            src, i, _ = _modrm(r, i, osz, asz, False, seg)
            if osz == 32:
                return done(f"imul {_regs(osz)[reg]}, {src}, {_hex(r.u32(i), 8)}", i + 4)
            return done(f"imul {_regs(osz)[reg]}, {src}, {_hex(r.u16(i), 4)}", i + 2)
        src, i, _ = _modrm(r, i, osz, asz, False, seg)
        if op == 0xAF:
            return done(f"imul {_regs(osz)[reg]}, {src}", i)
        return done(f"imul {_regs(osz)[reg]}, {src}", i)
    if shape == NOP0F:
        dst, i, _ = _modrm(r, i, osz, asz, False, seg)
        return done(f"nop {dst}", i)
    if shape == BOUND:
        dst, i, reg = _modrm(r, i, osz, asz, False, seg)
        return done(f"bound {_regs(osz)[reg]}, {dst}", i)
    if shape == LAR:
        src, i, reg = _modrm(r, i, osz, asz, False, seg)
        return done(f"{name} {_regs(osz)[reg]}, {src}", i)
    if shape == SHLD:
        # the source is the modrm's reg field, and `i` has moved past the
        # displacement by the time the operand comes back -- reading the byte
        # before it read the last byte *of* the displacement, so `shld [si+0x1025],dx`
        # came out as `dx` when the modrm said `ax`
        dst, i, reg = _modrm(r, i, osz, asz, False, seg)
        src = _regs(osz)[reg]
        if op % 256 in (0xA4, 0xAC):
            return done(f"{name} {dst}, {src}, {_hex(r.u8(i), 2)}", i + 1)
        return done(f"{name} {dst}, {src}, cl", i)
    if shape == BT:
        src, i, reg = _modrm(r, i, osz, asz, False, seg)
        return done(f"{name} {src}, {_regs(osz)[reg]}", i)
    if shape == GRP8B:
        dst, i, reg = _modrm(r, i, osz, asz, False, seg)
        return done(f"{GRP8[reg]} {dst}, {_hex(r.u8(i), 2)}", i + 1)
    if shape == CMPXCHG8:
        dst, i, _ = _modrm(r, i, osz, asz, False, seg)
        return done(f"cmpxchg8b {dst}", i)
    if shape == CV:
        m = r.u8(i)
        i += 1
        reg = m & 7
        if op % 256 in (0x20, 0x21):
            return done(f"mov {R32[reg]}, cr{(m >> 3) & 7}", i)
        return done(f"mov {R32[reg]}, dr{(m >> 3) & 7}", i)
    if shape == MOV_TREG:
        m = r.u8(i)
        i += 1
        reg = m & 7
        if op % 256 in (0x24, 0x25):
            return done(f"mov tr{(m >> 3) & 7}, {R32[reg]}", i)
        return done(f"mov {R32[reg]}, tr{(m >> 3) & 7}", i)
    if shape == X87OP:
        # no coprocessor is fitted on this board, so the escape traps. Naming
        # the float operation it *would* have been is a guess about a 387 that
        # is not there; the bytes are an escape and the operand is a modrm.
        m = r.u8(i)
        if (m >> 6) == 3:
            return done(f"esc st{m & 7}", i + 1)
        dst, i, _ = _modrm(r, i, osz, asz, False, seg)
        return done(f"esc {dst}", i)
    if shape == SSE:
        return done(name, i)
    return done(name, i)


def disassemble(mem: bytes, start: int, count: int, bits: int = 16) -> list[tuple[int, int, str]]:
    out = []
    pc = start
    for _ in range(count):
        n, text = decode(mem, pc, bits)
        if n <= 0:
            n = 1
        out.append((pc, n, text))
        pc += n
    return out


def _ndisasm(path: Path, bits: int, count: int, start: int) -> list[tuple[int, int, str]]:
    proc = subprocess.run(["ndisasm", "-b", str(bits), "-o", str(start), str(path)],
                          capture_output=True, text=True, check=True)
    out = []
    for line in proc.stdout.splitlines()[:count]:
        m = re.match(r"^([0-9A-F]+)\s+([0-9A-F]+)\s+(\S+)(?:\s+(.*))?$", line)
        if not m:
            continue
        addr = int(m.group(1), 16)
        nbytes = len(m.group(2)) // 2
        text = (m.group(3) + (" " + m.group(4) if m.group(4) else ""))
        out.append((addr, nbytes, text))
    return out


#: the aliases an assembler is allowed to choose from. Two names for one opcode
#: are not a disagreement about the instruction, and comparing them as one keeps
#: the check about what matters -- the length and the operation.
ALIAS = {
    "jz": "je", "jnz": "jne", "jc": "jb", "jnc": "jae", "jnae": "jb",
    "jnb": "jae", "jna": "jbe", "jnbe": "ja", "jnge": "jl", "jnl": "jge",
    "jng": "jle", "jnle": "jg", "jpe": "jp", "jpo": "jnp",
    "retn": "ret", "sal": "shl", "loopz": "loope", "loopnz": "loopne",
    "setz": "sete", "setnz": "setne", "setc": "setb", "setnc": "setae",
    "cmovz": "cmove", "cmovnz": "cmovne", "cmovc": "cmovb", "cmovnc": "cmovae",
}

#: a listing line: the line number, the offset, the bytes, the source. The
#: bytes may carry a resolved symbol in brackets -- `BA[C607]` is three bytes,
#: a `mov dx` and the word it points at -- and those count towards the length.
_LST = re.compile(r"^\s*\d+\s+([0-9A-F]{8})\s+((?:[0-9A-F]|\[[0-9A-F]+\])+)\s*(.*)$")


def check_lst(listing: Path, binary: Path, bits: int = 16) -> int:
    """Every instruction a listing names, decoded and compared with nasm.

    The listing is the assembler's own account of where each instruction starts
    and how long it is, which is exactly what a disassembler has to get right.
    Data (`db`, `dw`, `times`) is skipped: nothing there is an instruction, and
    a decoder asked to make sense of a string would only be wrong in a way that
    means nothing.
    """
    mem = binary.read_bytes()
    bad = 0
    total = 0
    for line in listing.read_text().splitlines():
        m = _LST.match(line)
        if not m:
            continue
        offset = int(m.group(1), 16)
        raw = m.group(2)
        source = re.sub(r"^<\d+>\s*", "", m.group(3).strip())
        source = re.sub(r"^(?:-\s*)?[A-Za-z_.$][\w.$]*:\s*", "", source)
        if ("<" in raw or not source
                or source.startswith((";", "db ", "dw ", "dd ", "times", "incbin",
                                      "resb", "resw", "equ", "- ", "<rep", "-"))):
            continue
        given = source.split()[0].lower()
        if given in ("rep", "repe", "repne", "repnz", "lock") and len(source.split()) > 1:
            given = source.split()[1].lower()
        if given in ("db", "dw", "dd", "dq", "equ", "resb", "resw", "align"):
            continue
        if given.startswith("["):
            continue
        total += 1
        given_len = len(raw.replace("[", "").replace("]", "")) // 2
        mine_len, mine = decode(mem, offset, bits)
        parts = mine.split()
        mine_name = parts[0].lower() if parts else ""
        if mine_name in ("lock", "rep", "repne") and len(parts) > 1:
            mine_name = parts[1].lower()
        mine_name = mine_name.split(",")[0]
        if given in ("jmp", "call") and mine_name in ("jmp", "call"):
            mine_name = given
        if ALIAS.get(mine_name, mine_name) != ALIAS.get(given, given) or \
                mine_len != given_len:
            bad += 1
            if bad <= 30:
                print(f"{offset:06X}  nasm {given_len:2d} {source:<28} "
                      f"mine {mine_len:2d} {mine}")
    print(f"{listing.name}: {total} instructions, {bad} differ")
    return bad


def check(path: Path, bits: int, count: int, start: int = 0) -> int:
    mem = path.read_bytes()
    theirs = _ndisasm(path, bits, count, start)
    bad = 0
    for addr, nbytes, text in theirs:
        mine_len, mine = decode(mem, addr, bits)
        mine_name = mine.split()[0] if mine.split() else ""
        if mine.startswith(("lock ", "rep ", "repne ")):
            mine_name = mine.split()[1] if len(mine.split()) > 1 else mine_name
        their_name = text.split()[0]
        if their_name in ("db", "dw", "dd"):
            continue          # ndisasm fell into data deliberately
        if mine_len != nbytes or mine_name != their_name:
            bad += 1
            if bad <= 25:
                print(f"{addr:08X}  ndisasm {nbytes}:{text:<34} "
                      f"mine {mine_len}:{mine}")
    print(f"{path.name}: {len(theirs)} instructions, {bad} differ")
    return bad


def emit(path: Path) -> None:
    """Write the tables the block version reads, and the shape names it uses.

    The `.jsa` is one list with a header, so the decoder indexes it directly:

        [1]           how many mnemonics there are
        [2 .. n+1]    the mnemonics
        [n+2 ..]      code, then shape, then code0f, then shape0f (256 each)

    `dis_shapes.inc.jsw` is the other half: `D.K_NONE` and friends, so the
    if-chain in `disasm.inc.jsw` reads as names instead of as numbers. Both
    files come out of here together so they cannot drift apart.
    """
    import json
    flat = [len(MNEMONICS), *MNEMONICS, *CODE, *SHAPE, *CODE0F, *SHAPE0F]
    small = [("R8", R8), ("R16", R16), ("R32", R32), ("SREG", SREG), ("CC", CC),
             ("ALU", ALU), ("SHIFT", SHIFT), ("G3", GRP3), ("G4", GRP4),
             ("G5", GRP5), ("G6", GRP6), ("G7", GRP7), ("G8", GRP8),
             ("SCALE", ["1", "2", "4", "8"])]
    bases = {}
    for name, values in small:
        bases[name] = len(flat) + 1          # one-based, as the project's lists are
        flat.extend(values)
    path.write_text(json.dumps(flat), encoding="utf-8")
    print(f"wrote {path} ({len(flat)} items, {len(MNEMONICS)} mnemonics)")
    shapes = path.parent.parent.parent / "dis_shapes.inc.jsw"
    lines = ["/* Generated by tools/i386_dis.py --emit. The numbering is the order of",
             "   SHAPE_NAMES in that file, and both sides are written by it; the",
             "   D.T_* bases are where each name table starts in `dis`. */", "",
             f"public D.MN = {len(MNEMONICS)};"]
    for name, _values in small:
        lines.append(f"public D.T_{name} = {bases[name]};")
    lines.append("")
    for i, name in enumerate(SHAPE_NAMES):
        lines.append(f"public D.K_{name} = {i};")
    shapes.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"wrote {shapes} ({len(SHAPE_NAMES)} shapes)")


def _emit_the_old_way(path: Path) -> None:
    """Write the tables the block version reads, as one `.jsa` list.

    A `.jsa` is what the project already imports for the ROM and the disk, and
    it exists for the same reason: a thousand `add to list` blocks at green
    flag is a boot cost, and a list imported from the project file is a copy.
    The layout is one list with a header, so the decoder indexes it directly:

        [1]           how many mnemonics there are
        [2 .. n+1]    the mnemonics
        [n+2 ..]      code, then shape, then code0f, then shape0f (256 each)
    """
    import json
    flat = [len(MNEMONICS), *MNEMONICS, *CODE, *SHAPE, *CODE0F, *SHAPE0F]
    small = [("R8", R8), ("R16", R16), ("R32", R32), ("SREG", SREG), ("CC", CC),
             ("ALU", ALU), ("SHIFT", SHIFT), ("G3", GRP3), ("G4", GRP4),
             ("G5", GRP5), ("G6", GRP6), ("G7", GRP7), ("G8", GRP8),
             ("SCALE", ["1", "2", "4", "8"])]
    bases = {}
    for name, values in small:
        bases[name] = len(flat) + 1          # one-based, as the project's lists are
        flat.extend(values)
    path.write_text(json.dumps(flat), encoding="utf-8")
    print(f"wrote {path} ({len(flat)} items, {len(MNEMONICS)} mnemonics)")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("-b", "--binary", type=Path)
    ap.add_argument("-a", "--at", type=lambda s: int(s, 0), default=0)
    ap.add_argument("-n", "--count", type=int, default=16)
    ap.add_argument("--bits", type=int, default=16, choices=(16, 32))
    ap.add_argument("--check", type=Path, help="compare instruction boundaries with ndisasm")
    ap.add_argument("--lst", type=Path, help="a nasm listing to check against")
    ap.add_argument("--bin", type=Path, help="... and the flat binary it lists")
    ap.add_argument("--emit", type=Path, help="write the block version's tables")
    args = ap.parse_args()
    if args.emit:
        emit(args.emit)
        return 0
    if args.lst:
        if not args.bin:
            ap.error("--lst needs --bin")
        return 1 if check_lst(args.lst, args.bin, args.bits) else 0
    if args.check:
        check(args.check, args.bits, args.count, args.at)
        return 0
    if not args.binary:
        ap.error("one of --binary, --check or --emit")
    mem = args.binary.read_bytes()
    for pc, n, text in disassemble(mem, args.at, args.count, args.bits):
        raw = " ".join(f"{b:02x}" for b in mem[pc:pc + n])
        print(f"{pc:08x}  {raw:<20}  {text}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
