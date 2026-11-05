"""The RV64C expander as `userspace/rv64/core.jsw` writes it, in Python.

The machine's expander has to agree with `rv64_rvc.expand` -- which the
assembler has verified -- on *every* compressed halfword, and the only way to
prove that without booting TurboWarp is to hold a copy of the JSW arithmetic
here and diff the two tables.

So this file is a transliteration, not an abstraction: the expression in each
`if` is the expression in the matching block of `expandC0`/`expandC1`/
`expandC2` in [core.jsw](../userspace/rv64/core.jsw), operator for operator.
**If you change one, change the other.** `tests/test_rv64.py` runs the diff.
"""

from __future__ import annotations

import math

import rv64_rvc as ref


def fl(v):
    return math.floor(v)


def encI(op, rd, f3, rs1, imm): return imm % 4096 * 1048576 + rs1 * 32768 + f3 * 4096 + rd * 128 + op
def encR(op, rd, f3, rs1, rs2, f7): return f7 * 33554432 + rs2 * 1048576 + rs1 * 32768 + f3 * 4096 + rd * 128 + op
def encS(rs2, f3, rs1, imm):
    _e = imm % 4096
    return fl(_e / 32) % 128 * 33554432 + rs2 * 1048576 + rs1 * 32768 + f3 * 4096 + _e % 32 * 128 + 35


def encB(rs1, f3, rs2, off):
    _e = off % 8192
    return (fl(_e / 4096) % 2 * 2147483648 + fl(_e / 32) % 64 * 33554432 + rs2 * 1048576
            + rs1 * 32768 + f3 * 4096 + fl(_e / 2) % 16 * 256 + fl(_e / 2048) % 2 * 128 + 99)


def encJ(rd, off):
    _e = off % 2097152
    return (fl(_e / 1048576) % 2 * 2147483648 + fl(_e / 2) % 1024 * 2097152
            + fl(_e / 2048) % 2 * 1048576 + fl(_e / 4096) % 256 * 4096 + rd * 128 + 111)


def expandC0(half, f3):
    _p2 = fl(half / 4) % 8 + 8
    _p1 = fl(half / 128) % 8 + 8
    if f3 == 0:
        _u = fl(half/128)%16*64 + fl(half/2048)%4*16 + fl(half/32)%2*8 + fl(half/64)%2*4
        return encI(19, _p2, 0, 2, _u) if _u > 0 else 0
    if f3 == 2:
        _o = fl(half/1024)%8*8 + fl(half/64)%2*4 + fl(half/32)%2*64
        return encI(3, _p2, 2, _p1, _o)
    if f3 == 3:
        _o = fl(half/1024)%8*8 + fl(half/32)%4*64
        return encI(3, _p2, 3, _p1, _o)
    if f3 == 6:
        _o = fl(half/1024)%8*8 + fl(half/64)%2*4 + fl(half/32)%2*64
        return encS(_p2, 2, _p1, _o)
    if f3 == 7:
        _o = fl(half/1024)%8*8 + fl(half/32)%4*64
        return encS(_p2, 3, _p1, _o)
    return 0


def expandC1(half, f3, rd, shamt):
    _i6 = fl(half / 4) % 32 + fl(half / 4096) % 2 * 32
    if _i6 > 31: _i6 -= 64
    if f3 == 0: return encI(19, rd, 0, rd, _i6)
    if f3 == 1: return encI(27, rd, 0, rd, _i6) if rd > 0 else 0
    if f3 == 2: return encI(19, rd, 0, 0, _i6)
    if f3 == 3:
        if rd == 2:
            _nz = (fl(half/4096)%2*512 + fl(half/8)%4*128 + fl(half/32)%2*64
                   + fl(half/4)%2*32 + fl(half/64)%2*16)
            if _nz > 511: _nz -= 1024
            return encI(19, 2, 0, 2, _nz) if _nz != 0 else 0
        _lui = fl(half / 4) % 32
        _s = fl(half / 4096) % 2
        if _lui == 0 and _s == 0: return 0
        v = (_lui + _s * 32) * 4096 + rd * 128 + 55
        if _s == 1: v += 4294705152
        return v
    if f3 == 4:
        _f2 = fl(half / 1024) % 4
        _p1 = fl(half / 128) % 8 + 8
        _p2 = fl(half / 4) % 8 + 8
        if _f2 == 0: return encI(19, _p1, 5, _p1, shamt)
        if _f2 == 1: return encI(19, _p1, 5, _p1, shamt + 1024)
        if _f2 == 2: return encI(19, _p1, 7, _p1, _i6)
        _which = fl(half / 32) % 4
        _wide = fl(half / 4096) % 2
        if _which == 0 and _wide == 0: return encR(51, _p1, 0, _p1, _p2, 32)
        if _which == 0: return encR(59, _p1, 0, _p1, _p2, 32)
        if _which == 1 and _wide == 1: return encR(59, _p1, 0, _p1, _p2, 0)
        if _which == 1: return encR(51, _p1, 4, _p1, _p2, 0)
        if _which == 2: return encR(51, _p1, 6, _p1, _p2, 0)
        return encR(51, _p1, 7, _p1, _p2, 0)
    if f3 == 5:
        _o = (fl(half/4096)%2*2048 + fl(half/2048)%2*16 + fl(half/512)%4*256 + fl(half/256)%2*1024
              + fl(half/64)%2*128 + fl(half/128)%2*64 + fl(half/8)%8*2 + fl(half/4)%2*32)
        if _o > 2047: _o -= 4096
        return encJ(0, _o)
    _o = (fl(half/4096)%2*256 + fl(half/1024)%4*8 + fl(half/32)%4*64 + fl(half/8)%4*2 + fl(half/4)%2*32)
    if _o > 255: _o -= 512
    return encB(fl(half / 128) % 8 + 8, f3 - 6, 0, _o)


def expandC2(half, f3, rd, rs2, shamt):
    if f3 == 0: return encI(19, rd, 1, rd, shamt)
    if f3 == 2:
        _o = fl(half/4096)%2*32 + fl(half/16)%8*4 + fl(half/4)%4*64
        return encI(3, rd, 2, 2, _o) if rd > 0 else 0
    if f3 == 3:
        _o = fl(half/4096)%2*32 + fl(half/32)%4*8 + fl(half/4)%8*64
        return encI(3, rd, 3, 2, _o) if rd > 0 else 0
    if f3 == 4:
        if fl(half / 4096) % 2 == 0:
            if rs2 == 0: return encI(103, 0, 0, rd, 0) if rd > 0 else 0
            return encR(51, rd, 0, 0, rs2, 0)
        if rs2 == 0:
            if rd == 0: return 1048691
            return encI(103, 1, 0, rd, 0)
        return encR(51, rd, 0, rd, rs2, 0)
    if f3 == 6:
        _o = fl(half/512)%16*4 + fl(half/128)%4*64
        return encS(rs2, 2, 2, _o)
    if f3 == 7:
        _o = fl(half/1024)%8*8 + fl(half/128)%8*64
        return encS(rs2, 3, 2, _o)
    return 0


def expandC(half):
    _q = half % 4
    _cf3 = fl(half / 8192) % 8
    _cd = fl(half / 128) % 32
    _cs2 = fl(half / 4) % 32
    _csh = fl(half / 4) % 32 + fl(half / 4096) % 2 * 32
    if _q == 0: return expandC0(half, _cf3)
    if _q == 1: return expandC1(half, _cf3, _cd, _csh)
    if _q == 2: return expandC2(half, _cf3, _cd, _cs2, _csh)
    return 0
