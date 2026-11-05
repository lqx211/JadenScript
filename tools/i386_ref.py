#!/usr/bin/env python3
"""An 80386, as an executable spec for the Scratch machine.

This is the same arrangement `rv64_ref.py` uses: a small, exact model that the
JSW core is a transliteration of, so a disagreement between the two is a bug in
one of them rather than a guess about what a real chip does.

Step 1 is **real mode**, because that is where every PC starts and where the
firmware lives:

    0x0000_0000  the interrupt vector table, 256 entries of (ip, cs)
    0x0000_0400  the BIOS data area (cursor, tick count, equipment word)
    0x0000_0500  DOS / boot sector scratch
    0x0000_7C00  where a boot sector is loaded
    0x0000_8000  the staged disk image: sector n lives at 0x8000 + n * 512
    0x000A_0000  the top of conventional memory
    0x000B_8000  the text plane: 80 x 25 cells of (char, attribute)
    0x000F_0000  the BIOS ROM, 64 KiB, entered at CS=0xF000 IP=0xFFF0

Real mode is 20 bits of address, segment * 16 + offset, wrapped -- there is no
A20 gate here yet, so a segment of 0xFFFF with an offset past 0x10 walks back to
the bottom of memory the way an 8086 does.

Everything a 386 adds to that is deliberately absent for now: one privilege
level, no GDT/IDT, no paging, no protected mode. The decoder is already 32-bit
clean (0x66 switches operand size) because the step after this one turns them on.
"""

from __future__ import annotations

RAM_SIZE = 1 << 20
TEXT_BASE = 0x000B_8000
TEXT_COLS = 80
TEXT_ROWS = 25
BIOS_BASE = 0x000F_0000
RESET_CS = 0xF000
RESET_IP = 0xFFF0
DISK_AT = 0x0000_8000
SECTOR = 512

FLAG_CF = 1 << 0
FLAG_PF = 1 << 2
FLAG_AF = 1 << 4
FLAG_ZF = 1 << 6
FLAG_SF = 1 << 7
FLAG_TF = 1 << 8
FLAG_IF = 1 << 9
FLAG_DF = 1 << 10
FLAG_OF = 1 << 11
FLAG_ALWAYS = 0x2

SEG_ES, SEG_CS, SEG_SS, SEG_DS, SEG_FS, SEG_GS = range(6)
SEG_NAMES = ("es", "cs", "ss", "ds", "fs", "gs")

MASK = {1: 0xFF, 2: 0xFFFF, 4: 0xFFFF_FFFF}
SIGN = {1: 0x80, 2: 0x8000, 4: 0x8000_0000}
REG8 = ("al", "cl", "dl", "bl", "ah", "ch", "dh", "bh")
REG16 = ("ax", "cx", "dx", "bx", "sp", "bp", "si", "di")
REG32 = ("eax", "ecx", "edx", "ebx", "esp", "ebp", "esi", "edi")


#: what a fresh board's battery holds. The clock registers are BCD, the memory
#: size is in KiB across two bytes, and 0x30 is ours: a boot counter that only
#: exists because the battery does.
CMOS_DEFAULTS = {
    0x00: 0x00,          # seconds
    0x01: 0x00,          # seconds alarm
    0x02: 0x00,          # minutes
    0x04: 0x12,          # hours
    0x06: 0x01,          # day of week
    0x07: 0x01,          # day of month
    0x08: 0x01,          # month
    0x09: 0x25,          # year, 2025
    0x0A: 0x26,          # status A: 32 kHz, no divider reset
    0x0B: 0x02,          # status B: 24-hour, BCD
    0x0D: 0x80,          # diagnostic: the battery is good
    0x0F: 0x00,          # shutdown status: a clean power-on
    0x10: 0x20,          # floppy: one 1.44 MB drive
    0x12: 0x00,          # hard disk type
    0x14: 0x21,          # equipment: one diskette, 80x25, one serial
    0x15: 0x80,          # base memory, low byte: 640 KiB = 0x0280
    0x16: 0x02,          # base memory, high byte
    0x17: 0x00,          # extended memory, low
    0x18: 0x00,          # extended memory, high
    0x19: 0x00,          # hard disk 0 type
    0x30: 0x00,          # our boot counter
    0x31: 0x00,          # our POST result
    0x32: 0x20,          # century
}


def sext(value: int, bits: int) -> int:
    value &= (1 << bits) - 1
    return value - (1 << bits) if value & (1 << (bits - 1)) else value


class Machine:
    """A 386 with 1 MiB of RAM, a text plane and an interrupt table."""

    def __init__(self, ram_size: int = RAM_SIZE) -> None:
        self.ram = bytearray(ram_size)
        self.ram_size = ram_size
        self.reg = [0] * 8
        self.sreg = [0] * 6
        self.eip = 0
        self.eflags = FLAG_ALWAYS | FLAG_IF
        self.halted = False
        self.steps = 0
        self.error = ""
        self.out = []
        self.keys = []
        self.ports = {}
        self.ticks = 0
        self.trace = None
        # the battery-backed CMOS: 128 bytes behind ports 0x70/0x71, and the
        # one device whose contents survive a power cycle
        # the board's chips: how many cores, which one is asking, and which
        # have been released. The model runs one core, so a release is modelled
        # as that core checking in at once -- the trampoline's effect, not its
        # timing.
        self.cores = 1
        self.core_id = 1
        self.core_run = [0] * 16
        self.cmos = bytearray(128)
        self.cmos_index = 0
        for reg, value in CMOS_DEFAULTS.items():
            self.cmos[reg] = value

    # ---- memory ------------------------------------------------------

    def phys(self, seg, off):
        return ((seg << 4) + off) & 0xFFFFF

    def read(self, addr, size):
        addr &= 0xFFFFF
        return int.from_bytes(self.ram[addr:addr + size], "little")

    def write(self, addr, value, size):
        addr &= 0xFFFFF
        self.ram[addr:addr + size] = (value & MASK[size]).to_bytes(size, "little")

    # ---- registers ---------------------------------------------------

    def sbase(self, index):
        return self.sreg[index] << 4

    def get8(self, index):
        reg = self.reg[index & 3]
        return (reg >> 8) & 0xFF if index >= 4 else reg & 0xFF

    def set8(self, index, value):
        value &= 0xFF
        i = index & 3
        if index >= 4:
            self.reg[i] = (self.reg[i] & 0xFFFF_00FF) | (value << 8)
        else:
            self.reg[i] = (self.reg[i] & 0xFFFF_FF00) | value

    def getreg(self, index, size):
        if size == 1:
            return self.get8(index)
        return self.reg[index] & MASK[size]

    def setreg(self, index, value, size):
        if size == 1:
            self.set8(index, value)
        elif size == 2:
            self.reg[index] = (self.reg[index] & 0xFFFF_0000) | (value & 0xFFFF)
        else:
            self.reg[index] = value & 0xFFFF_FFFF

    # ---- flags -------------------------------------------------------

    def flag(self, bit):
        return 1 if self.eflags & bit else 0

    def set_flag(self, bit, on):
        self.eflags = (self.eflags | bit) if on else (self.eflags & ~bit)

    # ---- ports -------------------------------------------------------

    def port_in(self, port, size):
        if port == 0x5A0:
            return self.cores
        if port == 0x5A2:
            return self.core_id
        if port == 0x70:
            return self.cmos_index
        if port == 0x71:
            return self.cmos[self.cmos_index & 0x7F]
        if port == 0x3FD:
            return 0x60
        if port == 0x3F8:
            return 0x60
        if port == 0x60:
            return self.keys.pop(0) if self.keys else 0
        if port == 0x64:
            return 0x01 if self.keys else 0
        return self.ports.get(port, 0xFF)

    def port_out(self, port, value, size):
        if port == 0x5A1:
            core = value & 0xFF
            if 1 <= core <= self.cores:
                self.core_run[core] = 1
                # the AP runs the trampoline: it records itself and checks in
                self.write(0x8400 + core, core, 1)
                self.write(0x8500 + core, 1, 1)
            return
        if port == 0x70:
            self.cmos_index = value & 0x7F
            return
        if port == 0x71:
            self.cmos[self.cmos_index & 0x7F] = value & 0xFF
            return
        if port == 0x3F8:
            self.out.append(value & 0xFF)
        self.ports[port] = value & 0xFF

    # ---- decode ------------------------------------------------------

    def fetch8(self):
        value = self.read(self.sbase(SEG_CS) + self.eip, 1)
        self.eip = (self.eip + 1) & 0xFFFF
        return value

    def fetch16(self):
        lo = self.fetch8()
        return lo | (self.fetch8() << 8)

    def fetch32(self):
        value = 0
        for i in range(4):
            value |= self.fetch8() << (8 * i)
        return value

    def modrm(self, seg=SEG_DS):
        """ModR/M -> (mod, reg, rm, address). Register operands share the
        number: on x86 `rm` is a register when mod is 3 and a memory operand
        otherwise, which is why the two live in one field."""
        byte = self.fetch8()
        mod = byte >> 6
        reg = (byte >> 3) & 7
        rm = byte & 7
        if mod == 3:
            return mod, reg, rm, None
        base = {0: 3, 1: 3, 2: 5, 3: 5, 4: 6, 5: 7, 6: 5, 7: 3}[rm]
        index = {0: 6, 1: 7, 2: 6, 3: 7}.get(rm)
        if rm == 6 and mod == 0:
            off = self.fetch16()
        else:
            # an address built on BP defaults to the stack segment, not to DS:
            # that is what makes [bp+6] mean "the caller's flags" in a handler
            if rm in (2, 3) or (rm == 6 and mod != 0):
                seg = SEG_SS
            off = self.reg[base] & 0xFFFF
            if index is not None:
                off += self.reg[index] & 0xFFFF
            if mod == 1:
                off += sext(self.fetch8(), 8)
            elif mod == 2:
                off += self.fetch16()
        return mod, reg, rm, self.phys(self.sreg[seg], off & 0xFFFF)

    def rm_read(self, mod, rm, addr, size):
        if mod == 3:
            return self.getreg(rm, size)
        return self.read(addr, size)

    def rm_write(self, mod, rm, addr, value, size):
        if mod == 3:
            self.setreg(rm, value, size)
        else:
            self.write(addr, value, size)

    # ---- arithmetic --------------------------------------------------

    def _szp(self, result, size):
        result &= MASK[size]
        self.set_flag(FLAG_ZF, result == 0)
        self.set_flag(FLAG_SF, bool(result & SIGN[size]))
        self.set_flag(FLAG_PF, bin(result & 0xFF).count("1") % 2 == 0)

    def alu(self, op, a, b, size):
        """0 add, 1 or, 2 adc, 3 sbb, 4 and, 5 sub, 6 xor, 7 cmp."""
        mask = MASK[size]
        a &= mask
        b &= mask
        if op in (0, 2, 3, 5, 7):
            carry = self.flag(FLAG_CF) if op in (2, 3) else 0
            total = a + b + carry if op in (0, 2) else a - b - carry
            res = total & mask
            self.set_flag(FLAG_CF, total < 0 or total > mask)
            self.set_flag(FLAG_AF, bool((a ^ b ^ res) & 0x10))
            sa, sb, sr = a & SIGN[size], b & SIGN[size], res & SIGN[size]
            if op in (0, 2):
                self.set_flag(FLAG_OF, bool(sa == sb and sr != sa))
            else:
                self.set_flag(FLAG_OF, bool(sa != sb and sr != sa))
            self._szp(res, size)
            return res
        res = {1: a | b, 4: a & b, 6: a ^ b}[op] & mask
        self.set_flag(FLAG_CF, False)
        self.set_flag(FLAG_OF, False)
        self.set_flag(FLAG_AF, False)
        self._szp(res, size)
        return res

    def shift(self, kind, value, count, size):
        mask = MASK[size]
        bits = size * 8
        value &= mask
        count &= 0x1F
        if count == 0:
            return value
        res = value
        for _ in range(count):
            if kind == 0:
                carry = (res >> (bits - 1)) & 1
                res = ((res << 1) | carry) & mask
            elif kind == 1:
                carry = res & 1
                res = (res >> 1) | (carry << (bits - 1))
            elif kind == 2:
                carry = (res >> (bits - 1)) & 1
                res = ((res << 1) | self.flag(FLAG_CF)) & mask
            elif kind == 3:
                carry = res & 1
                res = (res >> 1) | (self.flag(FLAG_CF) << (bits - 1))
            elif kind in (4, 6):
                carry = (res >> (bits - 1)) & 1
                res = (res << 1) & mask
            elif kind == 5:
                carry = res & 1
                res = res >> 1
            else:
                carry = res & 1
                res = (res >> 1) | (res & SIGN[size])
            self.set_flag(FLAG_CF, bool(carry))
        self._szp(res, size)
        return res

    # ---- stack -------------------------------------------------------

    def push(self, value):
        self.reg[4] = (self.reg[4] - 2) & 0xFFFF
        self.write(self.phys(self.sreg[SEG_SS], self.reg[4]), value, 2)

    def pop(self):
        value = self.read(self.phys(self.sreg[SEG_SS], self.reg[4]), 2)
        self.reg[4] = (self.reg[4] + 2) & 0xFFFF
        return value

    def interrupt(self, number):
        self.push(self.eflags & 0xFFFF)
        self.push(self.sreg[SEG_CS])
        self.push(self.eip)
        self.set_flag(FLAG_IF, False)
        self.set_flag(FLAG_TF, False)
        self.sreg[SEG_CS] = self.read(number * 4 + 2, 2)
        self.eip = self.read(number * 4, 2)

    def iret(self):
        self.eip = self.pop()
        self.sreg[SEG_CS] = self.pop()
        self.eflags = (self.eflags & 0xFFFF_0000) | self.pop() | FLAG_ALWAYS

    # ---- output ------------------------------------------------------

    def text(self):
        lines = []
        for row in range(TEXT_ROWS):
            chars = []
            for col in range(TEXT_COLS):
                cell = self.read(TEXT_BASE + (row * TEXT_COLS + col) * 2, 1)
                chars.append(chr(cell) if 32 <= cell < 127 else " ")
            lines.append("".join(chars).rstrip())
        return "\n".join(lines).rstrip()

    def run(self, limit=200_000):
        while self.steps < limit and not self.halted and not self.error:
            self.step()
        if self.error:
            raise RuntimeError(f"{self.error} at {self.sreg[SEG_CS]:04x}:{self.eip:04x}")
        return bytes(self.out).decode("latin-1")

    def step(self):
        if self.halted:
            return
        execute(self)
        self.steps += 1


# ----------------------------------------------------------------------
# the decoder
# ----------------------------------------------------------------------

def execute(m):
    """One instruction, prefixes included.

    The shape is the one the JSW core will keep: fold the prefixes into three
    flags, read the opcode, then dispatch on the opcode -- with the eight-op
    ALU family and the ModR/M groups handled as families rather than as sixty
    separate cases, because that is what they are.
    """
    size = 2
    addr_size = 2
    seg = SEG_DS
    rep = 0
    while True:
        op = m.fetch8()
        if op == 0x66:
            size = 4 if size == 2 else 2
        elif op == 0x67:
            addr_size = 4 if addr_size == 2 else 2
        elif op in (0x26, 0x2E, 0x36, 0x3E, 0x64, 0x65):
            seg = {0x26: SEG_ES, 0x2E: SEG_CS, 0x36: SEG_SS,
                   0x3E: SEG_DS, 0x64: SEG_FS, 0x65: SEG_GS}[op]
        elif op in (0xF2, 0xF3):
            rep = op
        elif op == 0xF0:
            pass                                    # LOCK: single-threaded anyway
        else:
            break
    if addr_size != 2:
        raise NotImplementedError("32-bit addressing is the next step")

    # ---- the eight-op ALU family, 0x00 .. 0x3D ----
    if op < 0x40 and (op & 7) < 6:
        alu_op = op >> 3
        form = op & 7
        if form == 0:                                # r/m8, r8
            mod, reg, rm, addr = m.modrm(seg)
            res = m.alu(alu_op, m.rm_read(mod, rm, addr, 1), m.get8(reg), 1)
            if alu_op != 7:
                m.rm_write(mod, rm, addr, res, 1)
            return
        if form == 1:                                # r/m16|32, r16|32
            mod, reg, rm, addr = m.modrm(seg)
            res = m.alu(alu_op, m.rm_read(mod, rm, addr, size), m.getreg(reg, size), size)
            if alu_op != 7:
                m.rm_write(mod, rm, addr, res, size)
            return
        if form == 2:                                # r8, r/m8
            mod, reg, rm, addr = m.modrm(seg)
            res = m.alu(alu_op, m.get8(reg), m.rm_read(mod, rm, addr, 1), 1)
            if alu_op != 7:
                m.set8(reg, res)
            return
        if form == 3:                                # r16|32, r/m16|32
            mod, reg, rm, addr = m.modrm(seg)
            res = m.alu(alu_op, m.getreg(reg, size), m.rm_read(mod, rm, addr, size), size)
            if alu_op != 7:
                m.setreg(reg, res, size)
            return
        if form == 4:                                # al, imm8
            res = m.alu(alu_op, m.get8(0), m.fetch8(), 1)
            if alu_op != 7:
                m.set8(0, res)
            return
        if form == 5:                                # ax/eax, imm
            imm = m.fetch32() if size == 4 else m.fetch16()
            res = m.alu(alu_op, m.getreg(0, size), imm, size)
            if alu_op != 7:
                m.setreg(0, res, size)
            return

    if 0x40 <= op <= 0x47:
        i = op & 7
        m.setreg(i, m.alu(0, m.getreg(i, size), 1, size), size)
        return
    if 0x48 <= op <= 0x4F:
        i = op & 7
        m.setreg(i, m.alu(5, m.getreg(i, size), 1, size), size)
        return
    if 0x50 <= op <= 0x57:
        if size == 4:
            m.push(m.reg[op & 7] & 0xFFFF)           # 16-bit stack in real mode
        else:
            m.push(m.reg[op & 7] & 0xFFFF)
        return
    if 0x58 <= op <= 0x5F:
        m.setreg(op & 7, m.pop(), 2)
        return
    if op == 0x06:
        m.push(m.sreg[SEG_ES]); return
    if op == 0x0E:
        m.push(m.sreg[SEG_CS]); return
    if op == 0x16:
        m.push(m.sreg[SEG_SS]); return
    if op == 0x1E:
        m.push(m.sreg[SEG_DS]); return
    if op == 0x07:
        m.sreg[SEG_ES] = m.pop(); return
    if op == 0x17:
        m.sreg[SEG_SS] = m.pop(); return
    if op == 0x1F:
        m.sreg[SEG_DS] = m.pop(); return
    if op == 0x60:
        sp = m.reg[4]
        for i in (0, 1, 2, 3):
            m.push(m.reg[i] & 0xFFFF)
        m.push(sp)
        return
    if op == 0x61:
        for i in (7, 6, 5, 4, 3, 2, 1, 0):
            value = m.pop()
            if i != 4:
                m.setreg(i, value, 2)
            elif i == 4:
                m.pop()                              # sp itself is discarded
        return
    if op == 0x68:
        imm = m.fetch32() if size == 4 else m.fetch16()
        m.push(imm & 0xFFFF)
        return
    if op == 0x6A:
        m.push(sext(m.fetch8(), 8) & 0xFFFF)
        return
    if op in (0x69, 0x6B):
        mod, reg, rm, addr = m.modrm(seg)
        a = m.rm_read(mod, rm, addr, size)
        imm = (m.fetch32() if size == 4 else m.fetch16()) if op == 0x69 else sext(m.fetch8(), 8)
        m.setreg(reg, a * imm, size)
        m.set_flag(FLAG_CF, abs(a * imm) > MASK[size])
        m.set_flag(FLAG_OF, abs(a * imm) > MASK[size])
        return
    if 0x70 <= op <= 0x7F:
        cond = branch_condition(m, op & 0x0F)
        rel = sext(m.fetch8(), 8)
        if cond:
            m.eip = (m.eip + rel) & 0xFFFF
        return
    if op in (0x80, 0x81, 0x82, 0x83):
        mod, reg, rm, addr = m.modrm(seg)
        wide = op in (0x81, 0x83)
        osize = size if wide else 1
        if op == 0x81:
            imm = m.fetch32() if size == 4 else m.fetch16()
        elif op == 0x83:
            imm = sext(m.fetch8(), 8)
        else:
            imm = m.fetch8()
        a = m.rm_read(mod, rm, addr, osize)
        res = m.alu(reg, a, imm, osize)
        if reg != 7:
            m.rm_write(mod, rm, addr, res, osize)
        return
    if op in (0x84, 0x85):
        osize = 1 if op == 0x84 else size
        mod, reg, rm, addr = m.modrm(seg)
        m.alu(4, m.rm_read(mod, rm, addr, osize), m.getreg(reg, osize), osize)
        return
    if op in (0x86, 0x87):
        osize = 1 if op == 0x86 else size
        mod, reg, rm, addr = m.modrm(seg)
        a = m.rm_read(mod, rm, addr, osize)
        b = m.getreg(reg, osize)
        m.rm_write(mod, rm, addr, b, osize)
        m.setreg(reg, a, osize)
        return
    if 0x88 <= op <= 0x8B:
        osize = 1 if op in (0x88, 0x8A) else size
        mod, reg, rm, addr = m.modrm(seg)
        if op in (0x88, 0x89):
            m.rm_write(mod, rm, addr, m.getreg(reg, osize), osize)
        else:
            m.setreg(reg, m.rm_read(mod, rm, addr, osize), osize)
        return
    if op == 0x8C:
        mod, reg, rm, addr = m.modrm(seg)
        if reg < 6:
            m.rm_write(mod, rm, addr, m.sreg[reg], 2)
        return
    if op == 0x8E:
        mod, reg, rm, addr = m.modrm(seg)
        value = m.rm_read(mod, rm, addr, 2)
        if reg < 6:
            m.sreg[reg] = value
        return
    if op == 0x8D:
        mod, reg, rm, addr = m.modrm(seg)
        m.setreg(reg, addr if addr is not None else 0, size)
        return
    if op == 0x8F:
        mod, reg, rm, addr = m.modrm(seg)
        m.rm_write(mod, rm, addr, m.pop(), 2)
        return
    if 0x90 <= op <= 0x97:
        i = op & 7
        if i:
            tmp = m.reg[0]
            m.reg[0] = m.reg[i]
            m.reg[i] = tmp
        return
    if op == 0x98:
        m.setreg(0, sext(m.reg[0] & 0xFF, 8) & MASK[size], size)
        return
    if op == 0x99:
        m.setreg(2, 0xFFFF if m.reg[0] & SIGN[size] else 0, size)
        return
    if op == 0x9A:
        off = m.fetch16()
        segv = m.fetch16()
        m.push(m.sreg[SEG_CS])
        m.push(m.eip)
        m.sreg[SEG_CS] = segv
        m.eip = off
        return
    if op == 0x9C:
        m.push(m.eflags & 0xFFFF)
        return
    if op == 0x9D:
        m.eflags = (m.eflags & 0xFFFF_0000) | m.pop() | FLAG_ALWAYS
        return
    if op == 0x9E:
        m.eflags = (m.eflags & ~0xFF) | (m.get8(4) & 0xD5) | FLAG_ALWAYS
        return
    if op == 0x9F:
        m.set8(4, m.eflags & 0xD7)
        return
    if 0xA0 <= op <= 0xA3:
        addr = m.phys(m.sreg[seg], m.fetch16())
        if op in (0xA0, 0xA1):
            osize = 1 if op == 0xA0 else size
            m.setreg(0, m.read(addr, osize), osize)
        else:
            osize = 1 if op == 0xA2 else size
            m.write(addr, m.getreg(0, osize), osize)
        return
    if 0xA4 <= op <= 0xA7 or 0xAA <= op <= 0xAF:
        string_op(m, op, size, seg, rep)
        return
    if op in (0xA8, 0xA9):
        osize = 1 if op == 0xA8 else size
        imm = m.fetch8() if op == 0xA8 else (m.fetch32() if size == 4 else m.fetch16())
        m.alu(4, m.getreg(0, osize), imm, osize)
        return
    if 0xB0 <= op <= 0xB7:
        m.set8(op & 7, m.fetch8())
        return
    if 0xB8 <= op <= 0xBF:
        m.setreg(op & 7, m.fetch32() if size == 4 else m.fetch16(), size)
        return
    if op in (0xC0, 0xC1, 0xD0, 0xD1, 0xD2, 0xD3):
        osize = 1 if op in (0xC0, 0xD0, 0xD2) else size
        mod, reg, rm, addr = m.modrm(seg)
        if op in (0xD0, 0xD1):
            count = 1
        elif op in (0xD2, 0xD3):
            count = m.reg[1] & 0xFF
        else:
            count = m.fetch8()
        m.rm_write(mod, rm, addr, m.shift(reg, m.rm_read(mod, rm, addr, osize), count, osize), osize)
        return
    if op == 0xC2:
        off = m.fetch16()
        m.eip = m.pop()
        m.reg[4] = (m.reg[4] + off) & 0xFFFF
        return
    if op == 0xC3:
        m.eip = m.pop()
        return
    if op in (0xC4, 0xC5):
        mod, reg, rm, addr = m.modrm(seg)
        m.setreg(reg, m.read(addr, 4) & 0xFFFF, 2)
        m.sreg[SEG_ES if op == 0xC4 else SEG_DS] = m.read(addr + 2, 2)
        return
    if op in (0xC6, 0xC7):
        osize = 1 if op == 0xC6 else size
        mod, reg, rm, addr = m.modrm(seg)
        imm = m.fetch8() if op == 0xC6 else (m.fetch32() if size == 4 else m.fetch16())
        m.rm_write(mod, rm, addr, imm, osize)
        return
    if op == 0xC8:
        alloc = m.fetch16()
        depth = m.fetch8() & 0x1F
        m.push(m.reg[5])
        frame = m.reg[4]
        for _ in range(depth):
            m.reg[5] = (m.reg[5] - 2) & 0xFFFF
            m.push(m.read(m.phys(m.sreg[SEG_SS], m.reg[5]), 2))
        if depth:
            m.push(frame)
        m.reg[5] = frame
        m.reg[4] = (m.reg[4] - alloc) & 0xFFFF
        return
    if op == 0xC9:
        m.reg[4] = m.reg[5]
        m.reg[5] = m.pop()
        return
    if op == 0xCA:
        off = m.fetch16()
        m.eip = m.pop()
        m.sreg[SEG_CS] = m.pop()
        m.reg[4] = (m.reg[4] + off) & 0xFFFF
        return
    if op == 0xCB:
        m.eip = m.pop()
        m.sreg[SEG_CS] = m.pop()
        return
    if op == 0xCC:
        m.interrupt(3)
        return
    if op == 0xCD:
        m.interrupt(m.fetch8())
        return
    if op == 0xCE:
        if m.flag(FLAG_OF):
            m.interrupt(4)
        return
    if op == 0xCF:
        m.iret()
        return
    if 0xE0 <= op <= 0xE3:
        rel = sext(m.fetch8(), 8)
        if op == 0xE0:
            m.reg[1] = (m.reg[1] - 1) & 0xFFFF
            taken = m.reg[1] != 0 and not m.flag(FLAG_ZF)
        elif op == 0xE1:
            m.reg[1] = (m.reg[1] - 1) & 0xFFFF
            taken = m.reg[1] != 0 and m.flag(FLAG_ZF)
        elif op == 0xE2:
            m.reg[1] = (m.reg[1] - 1) & 0xFFFF
            taken = m.reg[1] != 0
        else:
            taken = m.reg[1] == 0
        if taken:
            m.eip = (m.eip + rel) & 0xFFFF
        return
    if 0xE4 <= op <= 0xE7:
        port = m.fetch8()
        if op in (0xE4, 0xE5):
            osize = 1 if op == 0xE4 else size
            m.setreg(0, m.port_in(port, osize), osize)
        else:
            osize = 1 if op == 0xE6 else size
            m.port_out(port, m.getreg(0, osize), osize)
        return
    if op in (0xE8, 0xE9):
        rel = m.fetch32() if size == 4 else m.fetch16()   # 66 E8 is call rel32
        rel = sext(rel, 16) if size == 2 else sext(rel, 32)
        if op == 0xE8:
            m.push(m.eip)
        m.eip = (m.eip + rel) & 0xFFFF
        return
    if op == 0xEA:
        off = m.fetch16()
        segv = m.fetch16()
        m.sreg[SEG_CS] = segv
        m.eip = off
        return
    if op == 0xEB:
        rel = sext(m.fetch8(), 8)          # fetch first: m.eip must be the next
        m.eip = (m.eip + rel) & 0xFFFF     # instruction, not the displacement
        return
    if 0xEC <= op <= 0xEF:
        port = m.reg[2] & 0xFFFF
        if op in (0xEC, 0xED):
            osize = 1 if op == 0xEC else size
            m.setreg(0, m.port_in(port, osize), osize)
        else:
            osize = 1 if op == 0xEE else size
            m.port_out(port, m.getreg(0, osize), osize)
        return
    if op == 0xF4:
        m.halted = True
        return
    if op in (0xF6, 0xF7):
        osize = 1 if op == 0xF6 else size
        mod, reg, rm, addr = m.modrm(seg)
        value = m.rm_read(mod, rm, addr, osize)
        if reg == 0:
            imm = m.fetch8() if osize == 1 else (m.fetch32() if size == 4 else m.fetch16())
            m.alu(4, value, imm, osize)
        elif reg == 1:
            m.alu(4, value, value, osize)
        elif reg == 2:
            m.rm_write(mod, rm, addr, ~value & MASK[osize], osize)
        elif reg == 3:
            res = -value & MASK[osize]
            m.alu(5, 0, value, osize)
            m.rm_write(mod, rm, addr, res, osize)
        elif reg == 4:                              # mul
            acc = m.getreg(0, osize)
            total = acc * value
            if osize == 1:
                m.setreg(0, total & 0xFFFF, 2)
            else:
                m.setreg(0, total & MASK[osize], osize)
                m.setreg(2, (total >> (8 * osize)) & MASK[osize], osize)
            m.set_flag(FLAG_CF, total > MASK[osize])
            m.set_flag(FLAG_OF, total > MASK[osize])
        elif reg == 5:                              # imul
            acc = sext(m.getreg(0, osize), osize * 8)
            total = acc * sext(value, osize * 8)
            if osize == 1:
                m.setreg(0, total & 0xFFFF, 2)
            else:
                m.setreg(0, total & MASK[osize], osize)
                m.setreg(2, (total >> (8 * osize)) & MASK[osize], osize)
            fits = -SIGN[osize] <= total < SIGN[osize]
            m.set_flag(FLAG_CF, not fits)
            m.set_flag(FLAG_OF, not fits)
        elif reg in (6, 7):                         # div / idiv
            if value == 0:
                m.interrupt(0)
                return
            if osize == 1:
                dividend = m.getreg(0, 2)
            else:
                dividend = (m.getreg(2, osize) << (8 * osize)) | m.getreg(0, osize)
            if reg == 7:
                if osize == 1:
                    dividend = sext(dividend, 16)
                else:
                    dividend = sext(dividend, osize * 16) if osize == 2 else dividend
                quotient = int(dividend / sext(value, osize * 8))
                remainder = dividend - quotient * sext(value, osize * 8)
            else:
                quotient = dividend // value
                remainder = dividend % value
            if osize == 1:
                m.setreg(0, (remainder << 8) | (quotient & 0xFF), 2)
            else:
                m.setreg(0, quotient, osize)
                m.setreg(2, remainder, osize)
        return
    if op == 0xF5:
        m.set_flag(FLAG_CF, not m.flag(FLAG_CF))
        return
    if op in (0xF8, 0xF9, 0xFA, 0xFB, 0xFC, 0xFD):
        if op == 0xF8:
            m.set_flag(FLAG_CF, False)
        elif op == 0xF9:
            m.set_flag(FLAG_CF, True)
        elif op == 0xFA:
            m.set_flag(FLAG_IF, False)
        elif op == 0xFB:
            m.set_flag(FLAG_IF, True)
        elif op == 0xFC:
            m.set_flag(FLAG_DF, False)
        else:
            m.set_flag(FLAG_DF, True)
        return
    if op in (0xFE, 0xFF):
        osize = 1 if op == 0xFE else size
        mod, reg, rm, addr = m.modrm(seg)
        if op == 0xFE:
            value = m.rm_read(mod, rm, addr, 1)
            res = m.alu(0 if reg == 0 else 5, value, 1, 1)
            m.rm_write(mod, rm, addr, res, 1)
            return
        if reg == 0:
            m.rm_write(mod, rm, addr, m.alu(0, m.rm_read(mod, rm, addr, osize), 1, osize), osize)
        elif reg == 1:
            m.rm_write(mod, rm, addr, m.alu(5, m.rm_read(mod, rm, addr, osize), 1, osize), osize)
        elif reg == 2:
            target = m.rm_read(mod, rm, addr, 2)
            m.push(m.eip)
            m.eip = target
        elif reg == 3:
            target = m.rm_read(mod, rm, addr, 2)
            m.push(m.sreg[SEG_CS])
            m.push(m.eip)
            m.sreg[SEG_CS] = m.read(addr + 2, 2)
            m.eip = target
        elif reg == 4:
            m.eip = m.rm_read(mod, rm, addr, 2)
        elif reg == 5:
            m.sreg[SEG_CS] = m.read(addr + 2, 2)
            m.eip = m.rm_read(mod, rm, addr, 2)
        elif reg == 6:
            m.push(m.rm_read(mod, rm, addr, 2))
        return
    if op == 0x27 or op == 0x2F or op == 0x37 or op == 0x3F:
        return                                   # daa/das/aaa/aas: unused
    if op == 0x0F:
        two_byte(m, size, seg)
        return
    raise NotImplementedError(f"opcode {op:#04x} at {m.eip - 1:04x}")


def branch_condition(m, cond):
    return {
        0x0: m.flag(FLAG_OF),
        0x1: not m.flag(FLAG_OF),
        0x2: m.flag(FLAG_CF),
        0x3: not m.flag(FLAG_CF),
        0x4: m.flag(FLAG_ZF),
        0x5: not m.flag(FLAG_ZF),
        0x6: m.flag(FLAG_CF) or m.flag(FLAG_ZF),
        0x7: not (m.flag(FLAG_CF) or m.flag(FLAG_ZF)),
        0x8: m.flag(FLAG_SF),
        0x9: not m.flag(FLAG_SF),
        0xA: m.flag(FLAG_PF),
        0xB: not m.flag(FLAG_PF),
        0xC: m.flag(FLAG_SF) != m.flag(FLAG_OF),
        0xD: m.flag(FLAG_SF) == m.flag(FLAG_OF),
        0xE: m.flag(FLAG_ZF) or (m.flag(FLAG_SF) != m.flag(FLAG_OF)),
        0xF: not m.flag(FLAG_ZF) and (m.flag(FLAG_SF) == m.flag(FLAG_OF)),
    }[cond]


def string_op(m, op, size, seg, rep):
    """movs / cmps / stos / lods / scas: one element per pass, REP repeats.

    A repeat prefix is not a special instruction, it is a prefix on this one:
    CX counts down, and for the two comparing forms the loop also stops on the
    ZF state that REPE/REPNE asked for.
    """
    delta = -1 if m.flag(FLAG_DF) else 1
    kind = {0xA4: 0, 0xA5: 0, 0xA6: 1, 0xA7: 1, 0xAA: 2, 0xAB: 2,
            0xAC: 3, 0xAD: 3, 0xAE: 4, 0xAF: 4}[op]
    osize = 1 if op in (0xA4, 0xA6, 0xAA, 0xAC, 0xAE) else size
    while True:
        si = m.phys(m.sreg[seg], m.reg[6] & 0xFFFF)
        if kind == 0:
            m.write(m.phys(m.sreg[SEG_ES], m.reg[7] & 0xFFFF), m.read(si, osize), osize)
            m.reg[6] = (m.reg[6] + delta * osize) & 0xFFFF
            m.reg[7] = (m.reg[7] + delta * osize) & 0xFFFF
        elif kind == 1:
            di = m.phys(m.sreg[SEG_ES], m.reg[7] & 0xFFFF)
            m.alu(7, m.read(si, osize), m.read(di, osize), osize)
            m.reg[6] = (m.reg[6] + delta * osize) & 0xFFFF
            m.reg[7] = (m.reg[7] + delta * osize) & 0xFFFF
        elif kind == 2:
            m.write(m.phys(m.sreg[SEG_ES], m.reg[7] & 0xFFFF), m.getreg(0, osize), osize)
            m.reg[7] = (m.reg[7] + delta * osize) & 0xFFFF
        elif kind == 3:
            m.setreg(0, m.read(si, osize), osize)
            m.reg[6] = (m.reg[6] + delta * osize) & 0xFFFF
        else:
            di = m.phys(m.sreg[SEG_ES], m.reg[7] & 0xFFFF)
            m.alu(7, m.getreg(0, osize), m.read(di, osize), osize)
            m.reg[7] = (m.reg[7] + delta * osize) & 0xFFFF
        if not rep:
            return
        m.reg[1] = (m.reg[1] - 1) & 0xFFFF
        if m.reg[1] == 0:
            return
        if kind in (1, 4):
            if rep == 0xF3 and not m.flag(FLAG_ZF):
                return
            if rep == 0xF2 and m.flag(FLAG_ZF):
                return


def two_byte(m, size, seg):
    op = m.fetch8()
    if 0x80 <= op <= 0x8F:
        rel = m.fetch32() if size == 4 else m.fetch16()
        if branch_condition(m, op & 0x0F):
            m.eip = (m.eip + sext(rel, size * 8)) & 0xFFFF
        return
    if 0x90 <= op <= 0x9F:
        mod, reg, rm, addr = m.modrm(seg)
        m.rm_write(mod, rm, addr, 1 if branch_condition(m, op & 0x0F) else 0, 1)
        return
    if op in (0xB6, 0xB7, 0xBE, 0xBF):
        mod, reg, rm, addr = m.modrm(seg)
        from_size = 1 if op in (0xB6, 0xBE) else 2
        value = m.rm_read(mod, rm, addr, from_size)
        if op in (0xBE, 0xBF):
            value = sext(value, from_size * 8)
        m.setreg(reg, value, size)
        return
    if op == 0xAF:
        mod, reg, rm, addr = m.modrm(seg)
        a = m.getreg(reg, size)
        b = m.rm_read(mod, rm, addr, size)
        m.setreg(reg, a * b, size)
        m.set_flag(FLAG_CF, abs(a * b) > MASK[size])
        m.set_flag(FLAG_OF, abs(a * b) > MASK[size])
        return
    if op in (0xA0, 0xA1, 0xA8, 0xA9):        # push/pop fs, push/pop gs
        which = SEG_FS if op in (0xA0, 0xA1) else SEG_GS
        if op in (0xA0, 0xA8):
            m.push(m.sreg[which])
        else:
            m.sreg[which] = m.pop()
        return
    if op == 0xA2:
        raise NotImplementedError("cpuid arrives with the 486-ish device work")
    if op == 0x31:
        m.ticks += 1
        return
    raise NotImplementedError(f"two-byte opcode 0f {op:02x}")
