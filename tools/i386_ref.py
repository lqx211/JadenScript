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
TEXT_ROWS = 30
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


#: The 8254's input clock on a real board is a 1.193182 MHz crystal. This board
#: has no crystal -- it has an instruction counter, which the differential
#: checker proves both machines advance in lockstep -- so one input tick is
#: every PIT_DIV retired instructions. At the machine's measured 2.5M
#: instructions a second that is a 1.25 MHz clock, near enough to the crystal
#: that the divisors a guest copies from a datasheet give the rates it expects
#: (11,932 is 100 Hz on both).
PIT_DIV = 2

#: the programmable interrupt controller: IRQ0 the timer, IRQ1 the keyboard,
#: IRQ2 the cascade to the second chip, IRQ3/4 serial, IRQ6 the floppy.
PIC_MASTER = 0x20
PIC_SLAVE = 0xA0
PIC_EOI = 0x20


class Pic:
    """One 8259A: the eight request lines, the mask, and who is in service.

    Only the three registers a guest actually programs are here -- IRR, IMR and
    ISR -- plus the initialization counter, because ICW2 sets the vector base
    and nothing else in the chip works until that has been written.
    """

    def __init__(self, base: int = 0x08) -> None:
        self.irr = 0                     # requested, waiting
        self.imr = 0xFF                  # masked; a fresh chip masks everything
        self.isr = 0                     # being serviced
        self.base = base
        self.init = 0                    # ICWs still expected: 0 = none
        self.need4 = 0
        self.slave_mask = 0              # ICW3: which lines have a slave on them
        self.read_sel = 0                # 0 = IRR, 1 = ISR

    def raise_line(self, irq: int, level: bool = True) -> None:
        bit = 1 << irq
        if level:
            self.irr |= bit
        else:
            self.irr &= ~bit

    def pending(self) -> int:
        """The highest-priority line that may be acknowledged, or -1.

        Fixed priority, fully nested: line 0 wins, and anything in service
        blocks a request at the same or a lower priority. That is the whole
        arbitration rule of the chip.
        """
        for irq in range(8):
            bit = 1 << irq
            if not self.irr & bit or self.imr & bit:
                continue
            if self.isr & ((bit << 1) - 1):
                continue
            return irq
        return -1

    def acknowledge(self) -> int:
        irq = self.pending()
        if irq < 0:
            return -1
        bit = 1 << irq
        self.irr &= ~bit
        self.isr |= bit
        return self.base + irq

    def eoi(self, specific: bool = False, level: int = 0) -> None:
        if specific:
            self.isr &= ~(1 << level)
        else:
            self.isr &= self.isr - 1      # the lowest set bit is the top priority

    def command(self, value: int) -> None:
        if self.init:
            if self.init == 1:
                self.base = value & 0xF8
                self.init = 2 if self.need4 == 2 else 0
                if self.need4 == 1:
                    self.init = 3
            elif self.init == 2:
                self.slave_mask = value & 0xFF
                self.init = 3 if self.need4 else 0
            else:
                self.init = 0
            return
        if value & 0x10:                  # ICW1: start an initialization
            self.irr = 0
            self.isr = 0
            self.need4 = 2 if value & 0x01 else 1
            self.init = 1
            return
        if value & 0x08:                  # OCW3: pick which register is read
            if value & 0x02:
                self.read_sel = value & 0x01
            return
        if value & 0x40:                  # OCW2 with SL: specific EOI
            self.eoi(True, value & 0x07)
        elif not value & 0x10:
            self.eoi()                    # OCW2 non-specific EOI


class Pit:
    """The 8254: three down counters, each with a reload and a mode.

    Counter 0 raises IRQ0 when it reaches its terminal count, counter 1 is the
    DRAM refresh counter, and counter 2 is the speaker -- which is why a guest
    that writes 0x43 and then listens hears nothing: the gate on port 0x61 is
    what decides, and this board has no speaker.
    """

    def __init__(self) -> None:
        self.reload = [0x10000, 18, 0x10000]
        self.count = [0x10000, 18, 0x10000]
        self.access = [3, 2, 3]           # 1 lo, 2 hi, 3 lo then hi
        self.mode = [3, 2, 3]
        self.bcd = [0, 0, 0]
        self.next_byte = [0, 0, 0]        # which of the two has been written
        self.low = [0, 0, 0]
        self.latched = [None, None, None]
        self.cmd = 0                      # the control word being written
        self.cmd_bytes = 0

    def control(self, value: int) -> None:
        channel = value >> 6
        access = (value >> 4) & 3
        if channel == 3:                  # read-back: latch what was asked for
            for i in range(3):
                if not value & (2 << i):
                    self.latched[i] = self.count[i]
            return
        self.access[channel] = access if access else self.access[channel]
        self.mode[channel] = (value >> 1) & 7
        self.bcd[channel] = value & 1
        self.next_byte[channel] = 0
        if access == 0:                   # latch the count for a read
            self.latched[channel] = self.count[channel]

    def write(self, channel: int, value: int) -> None:
        value &= 0xFF
        access = self.access[channel]
        if access == 1:
            self.reload[channel] = value or 0x100
        elif access == 2:
            self.reload[channel] = (value << 8) or 0x10000
        elif self.next_byte[channel] == 0:
            self.low[channel] = value
            self.next_byte[channel] = 1
            return
        else:
            self.reload[channel] = ((value << 8) | self.low[channel]) or 0x10000
            self.next_byte[channel] = 0
        if self.reload[channel] == 0:
            self.reload[channel] = 0x10000
        self.count[channel] = self.reload[channel]

    def read(self, channel: int) -> int:
        if self.latched[channel] is not None:
            value = self.latched[channel]
            self.latched[channel] = None
        else:
            value = self.count[channel]
        if self.access[channel] == 1:
            return value & 0xFF
        if self.access[channel] == 2:
            return (value >> 8) & 0xFF
        low = value & 0xFF
        self.latched[channel] = (value >> 8) & 0xFF
        return low

    def tick(self) -> int:
        """One input clock. Returns the channels that reached their terminal
        count, which is what raises an interrupt."""
        fired = 0
        for i in range(3):
            self.count[i] -= 1
            if self.count[i] > 0:
                continue
            self.count[i] = self.reload[i]
            fired |= 1 << i
            if self.mode[i] in (0, 1):
                self.count[i] = 0x10000   # one shot: the output stays high
        return fired

    def to_terminal(self) -> int:
        """Input ticks until counter 0 next reaches its terminal count."""
        return max(1, self.count[0])

    def underflow(self) -> None:
        """Counter 0 reached its terminal count while the CPU was asleep, so
        the clock skipped straight there instead of counting down every tick."""
        self.count[0] = 0x10000 if self.mode[0] in (0, 1) else self.reload[0]


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
        # the board's clock and interrupt controller
        self.pit = Pit()
        self.pic = Pic(0x08)
        self.pic2 = Pic(0x70)
        self.pic2.imr = 0xFF
        self.pit_acc = 0
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
        if 0x40 <= port <= 0x42:
            return self.pit.read(port - 0x40)
        if port == 0x43:
            return self.pit.cmd
        if port == PIC_MASTER:
            return self.pic.isr if self.pic.read_sel else self.pic.irr
        if port == PIC_MASTER + 1:
            return self.pic.imr
        if port == PIC_SLAVE:
            return self.pic2.isr if self.pic2.read_sel else self.pic2.irr
        if port == PIC_SLAVE + 1:
            return self.pic2.imr
        if port == 0x61:                  # the speaker port: timer 2's gate
            return self.ports.get(0x61, 0)
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
        if port == 0x43:
            self.pit.control(value & 0xFF)
            return
        if 0x40 <= port <= 0x42:
            self.pit.write(port - 0x40, value)
            return
        if port == PIC_MASTER:
            self.pic.command(value & 0xFF)
            return
        if port == PIC_MASTER + 1:
            self.pic.imr = value & 0xFF
            return
        if port == PIC_SLAVE:
            self.pic2.command(value & 0xFF)
            return
        if port == PIC_SLAVE + 1:
            self.pic2.imr = value & 0xFF
            return
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

    def raise_irq(self, irq: int) -> None:
        """A device pulls a line. Lines 8..15 live on the second chip, and its
        output is the cascade into line 2 of the first -- which is why nothing
        on the slave ever arrives until IRQ2 is unmasked."""
        if irq >= 8:
            self.pic2.raise_line(irq - 8)
            self.pic.raise_line(2, self.pic2.pending() >= 0)
        else:
            self.pic.raise_line(irq)

    def pending_vector(self) -> int:
        """The vector the controller would hand over now, or -1."""
        irq = self.pic.pending()
        if irq < 0:
            return -1
        if irq == 2 and self.pic2.pending() >= 0:
            return self.pic2.base + self.pic2.pending()
        return self.pic.base + irq

    def acknowledge(self) -> int:
        irq = self.pic.pending()
        if irq < 0:
            return -1
        if irq == 2 and self.pic2.pending() >= 0:
            self.pic.acknowledge()
            return self.pic2.acknowledge()
        return self.pic.acknowledge()

    def clock_board(self) -> None:
        """One instruction's worth of board time."""
        self.pit_acc += 1
        if self.pit_acc < PIT_DIV:
            return
        self.pit_acc = 0
        fired = self.pit.tick()
        if fired & 1:
            self.raise_irq(0)

    def service_irq(self) -> None:
        """An interrupt is taken between instructions, and only when the
        guest has interrupts on. That is the whole of the rule."""
        if not self.flag(FLAG_IF):
            return
        vector = self.acknowledge()
        if vector >= 0:
            self.interrupt(vector)

    def idle(self) -> None:
        """A halted core still has a clock.

        The CPU retires nothing, but the timer does not stop for it, so the
        instruction counter is moved to the moment the next tick lands -- which
        is exactly the time that passes while the core sleeps, computed rather
        than guessed, so both machines agree on it. A core that halted with
        interrupts off stays halted, as a real one does.
        """
        if self.pending_vector() < 0:
            if not self.flag(FLAG_IF):
                return
            self.steps += self.pit.to_terminal() * PIT_DIV - self.pit_acc
            self.pit_acc = 0
            self.pit.underflow()
            self.raise_irq(0)
            if self.pending_vector() < 0:
                return
        self.halted = False

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
        while self.steps < limit and not self.error:
            if self.halted:
                self.idle()
                if self.halted:
                    break        # parked with nothing left to wake it
                continue
            self.step()
        if self.error:
            raise RuntimeError(f"{self.error} at {self.sreg[SEG_CS]:04x}:{self.eip:04x}")
        return bytes(self.out).decode("latin-1")

    def step(self):
        if self.halted:
            self.idle()
            return
        self.service_irq()
        execute(self)
        self.steps += 1
        self.clock_board()


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
    if op in (0xD4, 0xD5):
        # aam / aad: multiply and divide by a base, ten unless told otherwise.
        # They are the 8080's decimal adjust and nothing in this century uses
        # them, which is exactly why an emulator that skips them is caught out
        # by an old test suite.
        base = m.fetch8() or 10
        if op == 0xD4:                       # aam
            al = m.get8(0)
            m.set8(4, al // base)
            m.set8(0, al % base)
        else:                                # aad
            al = (m.get8(0) + m.get8(4) * base) & 0xFF
            m.set8(4, 0)
            m.set8(0, al)
        m._szp(m.get8(0), 1)
        return
    if op == 0xD6:                           # salc: all ones when the carry is set
        m.set8(0, 0xFF if m.flag(FLAG_CF) else 0x00)
        return
    if op == 0xD7:                           # xlat: al = [seg:bx + al]
        addr = m.phys(m.sreg[seg], (m.reg[3] + m.get8(0)) & 0xFFFF)
        m.set8(0, m.read(addr, 1))
        return
    if op == 0x9B:                           # wait: there is no coprocessor
        return
    if op == 0xF1:                           # int1: a breakpoint with a name
        m.interrupt(1)
        return
    if op == 0x63:                           # arpl
        mod, reg, rm, addr = m.modrm(seg)
        dest = m.rm_read(mod, rm, addr, 2)
        src = m.getreg(reg, 2)
        take = (dest & 3) < (src & 3)
        m.set_flag(FLAG_ZF, take)
        if take:
            m.rm_write(mod, rm, addr, (dest & 0xFFFC) | (src & 3), 2)
        return
    if op == 0x62:                           # bound: #BR when out of range
        mod, reg, rm, addr = m.modrm(seg)
        index = m.getreg(reg, 2)
        low = m.read(addr, 2)
        high = m.read(addr + 2, 2)
        if not sext(low, 16) <= sext(index, 16) <= sext(high, 16):
            m.interrupt(5)
        return
    if 0x6C <= op <= 0x6F:
        # ins / outs: the string forms of `in` and `out` at port dx. This board
        # has no device that streams, so it moves one item per pass like any
        # other string instruction and lets the port answer what it answers.
        osize = 1 if op in (0x6C, 0x6E) else size
        delta = -osize if m.flag(FLAG_DF) else osize
        count = 1
        if rep == 0xF3:
            count = m.reg[1] & 0xFFFF
            if count == 0:
                return
        for _ in range(count):
            if op in (0x6C, 0x6D):           # ins: port -> es:di
                value = m.port_in(m.reg[2] & 0xFFFF, osize)
                m.write(m.phys(m.sreg[SEG_ES], m.reg[7] & 0xFFFF), value, osize)
                m.reg[7] = (m.reg[7] + delta) & 0xFFFF
            else:                            # outs: ds:si -> port
                value = m.read(m.phys(m.sreg[seg], m.reg[6] & 0xFFFF), osize)
                m.port_out(m.reg[2] & 0xFFFF, value, osize)
                m.reg[6] = (m.reg[6] + delta) & 0xFFFF
        if rep == 0xF3:
            m.reg[1] = 0
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
    if op in (0x27, 0x2F, 0x37, 0x3F):
        # daa / das / aaa / aas: the decimal adjusts. They are here because the
        # 8086 had to keep the 8080's BCD arithmetic working, and a BIOS's date
        # code still uses them. Both tests read the *old* AL -- doing the second
        # one on the already-adjusted byte is a classic and quiet bug.
        al = m.get8(0)
        old_al = al
        old_cf = m.flag(FLAG_CF)
        if op in (0x27, 0x2F):
            step = (al & 0x0F) > 9 or m.flag(FLAG_AF)
            if step:
                if op == 0x27:
                    m.set_flag(FLAG_CF, old_cf or old_al > 0xF9)
                    al = (al + 6) & 0xFF
                else:
                    m.set_flag(FLAG_CF, old_cf or old_al < 0x06)
                    al = (al - 6) & 0xFF
            m.set_flag(FLAG_AF, step)
            if old_cf or old_al > 0x99:
                al = (al + 0x60 if op == 0x27 else al - 0x60) & 0xFF
                m.set_flag(FLAG_CF, True)
            m.set8(0, al)
            m._szp(al, 1)
            return
        # aaa / aas: the low nibble is made decimal and the carry goes into AH
        if (al & 0x0F) > 9 or m.flag(FLAG_AF):
            tweak = 0x106 if op == 0x37 else -0x106
            m.setreg(0, (m.getreg(0, 2) + tweak) & 0xFFFF, 2)
            m.set_flag(FLAG_AF, True)
            m.set_flag(FLAG_CF, True)
        else:
            m.set_flag(FLAG_AF, False)
            m.set_flag(FLAG_CF, False)
        m.set8(0, m.get8(0) & 0x0F)
        return
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
    if op in (0xA3, 0xAB, 0xB3, 0xBB):
        # bt / bts / btr / btc, register form: the bit number comes from a
        # register, and everything but bt writes the bit back
        mod, reg, rm, addr = m.modrm(seg)
        value = m.rm_read(mod, rm, addr, size)
        index = m.getreg(reg, size) % (size * 8)
        m.set_flag(FLAG_CF, bool(value >> index & 1))
        if op != 0xA3:
            if op == 0xAB:
                value |= 1 << index
            elif op == 0xB3:
                value &= ~(1 << index)
            else:
                value ^= 1 << index
            m.rm_write(mod, rm, addr, value & MASK[size], size)
        return
    if op in (0xA4, 0xA5, 0xAC, 0xAD):
        # shld / shrd: a double shift, the second half coming from a register
        mod, reg, rm, addr = m.modrm(seg)
        value = m.rm_read(mod, rm, addr, size)
        source = m.getreg(reg, size)
        count = (m.fetch8() if op in (0xA4, 0xAC) else m.reg[1]) & 0x1F
        bits = size * 8
        if count > bits:
            count = bits                  # beyond the operand the answer is undefined
        if count:
            # one bit at a time: the second half of every step comes in from the
            # other register, and the last bit out is the carry
            carry = 0
            result = value
            for _ in range(count):
                if op in (0xA4, 0xA5):    # shld
                    carry = (result >> (bits - 1)) & 1
                    result = ((result << 1) | ((source >> (bits - 1)) & 1)) & MASK[size]
                    source = ((source << 1) | carry) & MASK[size]
                else:                     # shrd
                    carry = result & 1
                    result = ((result >> 1) | ((source & 1) << (bits - 1))) & MASK[size]
                    source = (source >> 1) | (carry << (bits - 1))
            m.set_flag(FLAG_CF, bool(carry))
            m.rm_write(mod, rm, addr, result, size)
            m._szp(result, size)
        return
    if op in (0xBC, 0xBD):
        # bsf / bsr: where the first one bit is. With no bits at all the
        # register is left alone and ZF says so -- which is the one thing a
        # caller is allowed to know.
        mod, reg, rm, addr = m.modrm(seg)
        value = m.rm_read(mod, rm, addr, size)
        bits = size * 8
        if value == 0:
            m.set_flag(FLAG_ZF, True)
            return
        m.set_flag(FLAG_ZF, False)
        if op == 0xBC:
            index = 0
            while not value >> index & 1:
                index += 1
        else:
            index = bits - 1
            while not value >> index & 1:
                index -= 1
        m.setreg(reg, index, size)
        return
    if op in (0xB0, 0xB1):
        # cmpxchg: compare the accumulator with the destination, and either
        # store the source or load the accumulator. The compare-and-swap an
        # operating system builds a lock out of.
        osize = 1 if op == 0xB0 else size
        mod, reg, rm, addr = m.modrm(seg)
        dest = m.rm_read(mod, rm, addr, osize)
        acc = m.getreg(0, osize)
        if acc == dest:
            m.set_flag(FLAG_ZF, True)
            m.rm_write(mod, rm, addr, m.getreg(reg, osize), osize)
        else:
            m.set_flag(FLAG_ZF, False)
            m.setreg(0, dest, osize)
        return
    if op in (0xC0, 0xC1):
        # xadd: swap, then add. `lock xadd` is how a reference count is taken.
        osize = 1 if op == 0xC0 else size
        mod, reg, rm, addr = m.modrm(seg)
        dest = m.rm_read(mod, rm, addr, osize)
        source = m.getreg(reg, osize)
        m.rm_write(mod, rm, addr, m.alu(0, dest, source, osize), osize)
        m.setreg(reg, dest, osize)
        return
    if op == 0x06:                           # clts: no task-switch bit to clear
        return
    if op == 0xA2:                           # cpuid: say what we are
        leaf = m.reg[0]
        if leaf == 0:
            m.reg[0] = 1
            m.reg[3], m.reg[2], m.reg[1] = 0x756E6547, 0x6C65746E, 0x49656E69
        elif leaf == 1:
            m.reg[0] = 0x0403                 # family 4, model 0, stepping 3
            m.reg[1] = m.reg[2] = m.reg[3] = 0
        else:
            m.reg[0] = m.reg[1] = m.reg[2] = m.reg[3] = 0
        return
    if op == 0x31:
        m.ticks += 1
        return
    raise NotImplementedError(f"two-byte opcode 0f {op:02x}")
