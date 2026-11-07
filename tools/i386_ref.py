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

import math

PIT_HZ = 1_193_182                 # the 8254's input clock
RAM_SIZE = 1 << 20
TEXT_BASE = 0x000B_8000
TEXT_COLS = 80
TEXT_ROWS = 30
BIOS_BASE = 0x000F_0000
RESET_CS = 0xF000
RESET_IP = 0xFFF0
#: the disk image is staged where the firmware's int 13h window looks for it,
#: so the model and the machine agree about where a sector lives
DISK_AT = 0x000C_0000

#: IDENTIFY DEVICE, word for word: the answer every ATA drive has to give, and
#: the one a setup utility reads the model name out of. The checksum in the last
#: word makes the block sum to zero, which is what a strict driver checks.
ATA_IDENT = [
    0x0040, 0x0080, 0x0000, 0x0004, 0x0000, 0x0000, 0x003F, 0x0000, 0x0000, 0x4A53,
    0x5732, 0x3032, 0x3441, 0x3030, 0x3030, 0x3120, 0x2020, 0x2020, 0x2020, 0x0000,
    0x0000, 0x0000, 0x4130, 0x3720, 0x2020, 0x2020, 0x4A53, 0x5720, 0x5649, 0x5254,
    0x5541, 0x4C20, 0x4449, 0x534B, 0x2031, 0x3238, 0x4D20, 0x2020, 0x2020, 0x2020,
    0x2020, 0x2020, 0x2020, 0x2020, 0x2020, 0x2020, 0x8000, 0x0000, 0x0200, 0x0000,
    0x0000, 0x0000, 0x0001, 0x0080, 0x0004, 0x003F, 0x0080, 0x0000, 0x0000, 0x0080,
    0x0000, 0x0000, 0x0000, 0x0000, 0x0000, 0x0000, 0x0000, 0x0000, 0x0000, 0x0000,
    0x0000, 0x0000, 0x0000, 0x0000, 0x0000, 0x0000, 0x0000, 0x0000, 0x0000, 0x0010,
    0x0000, 0x4000, 0x0400, 0x0000, 0x0400, 0x0400, 0x4000, 0x0000, 0x0000, 0x0000,
    0x0000, 0x0000, 0x0000, 0x0000, 0x0000, 0x0000, 0x0000, 0x0000, 0x0000, 0x0000,
    0x0000, 0x0000, 0x0000, 0x0000, 0x0000, 0x0000, 0x0000, 0x0000, 0x0000, 0x0000,
    0x0000, 0x0000, 0x0000, 0x0000, 0x0000, 0x0000, 0x0000, 0x0000, 0x0000, 0x0000,
    0x0000, 0x0000, 0x0000, 0x0000, 0x0000, 0x0000, 0x0000, 0x0000, 0x0000, 0x0000,
    0x0000, 0x0000, 0x0000, 0x0000, 0x0000, 0x0000, 0x0000, 0x0000, 0x0000, 0x0000,
    0x0000, 0x0000, 0x0000, 0x0000, 0x0000, 0x0000, 0x0000, 0x0000, 0x0000, 0x0000,
    0x0000, 0x0000, 0x0000, 0x0000, 0x0000, 0x0000, 0x0000, 0x0000, 0x0000, 0x0000,
    0x0000, 0x0000, 0x0000, 0x0000, 0x0000, 0x0000, 0x0000, 0x0000, 0x0000, 0x0000,
    0x0000, 0x0000, 0x0000, 0x0000, 0x0000, 0x0000, 0x0000, 0x0000, 0x0000, 0x0000,
    0x0000, 0x0000, 0x0000, 0x0000, 0x0000, 0x0000, 0x0000, 0x0000, 0x0000, 0x0000,
    0x0000, 0x0000, 0x0000, 0x0000, 0x0000, 0x0000, 0x0000, 0x0000, 0x0000, 0x0000,
    0x0000, 0x0000, 0x0000, 0x0000, 0x0000, 0x0000, 0x0000, 0x0000, 0x0000, 0x0000,
    0x0000, 0x0000, 0x0000, 0x0000, 0x0000, 0x0000, 0x0000, 0x0000, 0x0000, 0x0000,
    0x0000, 0x0000, 0x0000, 0x0000, 0x0000, 0x0000, 0x0000, 0x0000, 0x0000, 0x0000,
    0x0000, 0x0000, 0x0000, 0x0000, 0x0000, 0x0000, 0x0000, 0x0000, 0x0000, 0x0000,
    0x0000, 0x0000, 0x0000, 0x0000, 0x0000, 0x0000, 0x0000, 0x0000, 0x0000, 0x0000,
    0x0000, 0x0000, 0x0000, 0x0000, 0x0000, 0xFB13
]

#: the five SMART attributes, in the order both boards report them
ATA_SMART_IDS = [5, 9, 12, 194, 197]

#: where the AHCI controller's registers live, and how big the window is
AHCI_BASE = 0x000D_0000
AHCI_WORDS = 2048

#: a standard INQUIRY answer: direct-access device, JSW, VIRTUAL DISK, 1.0
SCSI_INQUIRY = [0x0000, 0x0205, 0x001F, 0x0000, 0x534A, 0x2057, 0x2020, 0x2020, 0x4956, 0x5452, 0x4155, 0x204C, 0x4944, 0x4B53, 0x2020, 0x2020, 0x2E31, 0x2030]

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
#: The PCI bus, as a 440FX board has one: a host bridge, an ISA bridge, the IDE
#: controller and its bus-master function, a video card, and the AHCI controller
#: this machine really does answer for. A guest finds hardware by reading this
#: table through 0xCF8/0xCFC, which is the whole point of PCI: nothing is at a
#: fixed address that a driver may assume.
#:   (bus, device, function, vendor, device, class<<16|sub<<8|progif, bars...)
PCI_DEVICES = [
    (0, 0, 0, 0x8086, 0x1237, 0x060000, []),                # 440FX host bridge
    (0, 1, 0, 0x8086, 0x7000, 0x068000, []),                # PIIX3 ISA bridge
    (0, 1, 1, 0x8086, 0x7010, 0x010180,                     # PIIX3 IDE, native
     [0x000001F1, 0x000003F5, 0x00000171, 0x00000375, 0x0000C001]),
    (0, 2, 0, 0x1234, 0x1111, 0x030000, [0xE0000000]),      # JSW VGA
    (0, 3, 0, 0x8086, 0x2829, 0x010601, [0x000D0000]),      # AHCI (SATA)
]

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
    0x34: 0x01,          # emulation: on, so the board has a clock to report
    0x35: 0x01,          # battery: fitted
    0x36: 0x02,          # CPU frequency: 33 MHz, the setup's table entry 2
    0x37: 0x02,          # key repeat: 1/8 s, the entry that feels like a PC
    0x38: 0x01,          # cores, less one: two, which is what X.cores starts at
    0x39: 0x00,          # threads per core
    0x3A: 0x00,          # Performance Emu: 1x
    0x3B: 0x00,          # Fake OS Time: 100%, real time
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

    def icw(self, value: int) -> None:
        """ICW2, ICW3 and ICW4, which on a real 8259 arrive on the data port
        (0x21) right after ICW1 has gone to the command port.

        Accepting them on the command port too is a kindness: our own BIOS was
        written that way, and a chip that refuses them there would deadlock on
        the first EOI -- which is exactly what happened to SeaBIOS.
        """
        if self.init == 1:
            self.base = value & 0xF8
            self.init = 2
        elif self.init == 2:
            self.slave_mask = value & 0xFF
            self.init = 3 if self.need4 else 0
        else:
            self.init = 0

    def command(self, value: int) -> None:
        if self.init:
            self.icw(value)
            return
        if value & 0x10:                  # ICW1: start an initialization
            self.irr = 0
            self.isr = 0
            self.need4 = 1 if value & 0x01 else 0
            self.init = 1
            return
        if value & 0x08:                  # OCW3: pick which register is read
            if value & 0x02:
                self.read_sel = value & 0x01
            return
        if value & 0x40:                  # OCW2 with SL: specific EOI
            self.eoi(True, value & 0x07)
        else:
            self.eoi()                    # OCW2 non-specific EOI

    def data_write(self, value: int) -> None:
        """The data port: an initialization word while one is expected, and
        the mask otherwise."""
        if self.init:
            self.icw(value)
        else:
            self.imr = value & 0xFF


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
        self.latched = [None, None, None]   # counts held by a latch command
        self.status = [None, None, None]    # status bytes held the same way
        self.hi = [None, None, None]        # the high byte of a lo/hi read
        self.cmd = 0                      # the control word being written
        self.cmd_bytes = 0

    def control(self, value: int) -> None:
        channel = value >> 6
        access = (value >> 4) & 3
        if channel == 3:                  # the read-back command
            # Bits 5:4 are a two-bit field, not two flags, and the values are
            # the ones the guests actually use: 1 means "the count", 2 means
            # "the status byte", 0 means "the status byte, then the count".
            # SeaBIOS writes 0xD2 -- counter 0, "value" -- and then reads two
            # bytes as lo and hi; a chip that answered with the status byte
            # first would leave its timer running 256 times too slow.
            kind = (value >> 4) & 3
            for i in range(3):
                if value & (2 << i):      # a 1 in the field picks the counter
                    if kind == 2:
                        self.status[i] = self.status_byte(i)
                    elif kind == 1:
                        self.latched[i] = self.count[i]
                    else:
                        self.status[i] = self.status_byte(i)
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
        self.hi[channel] = None           # a reload starts a fresh read

    def status_byte(self, channel: int) -> int:
        """What the 8254 hands back when the read-back command asks for it:
        the output pin, whether the count has been loaded, and the control
        word's own fields, packed into one byte."""
        null_count = 1 if self.count[channel] == self.reload[channel] else 0
        out = 0                           # this board wires no gates to it
        return ((out << 7) | (null_count << 6) | (self.access[channel] << 4)
                | (self.mode[channel] << 1) | self.bcd[channel])

    def read(self, channel: int) -> int:
        if self.status[channel] is not None:
            value = self.status[channel]
            self.status[channel] = None
            return value
        if self.latched[channel] is not None:
            value = self.latched[channel]
            self.latched[channel] = None
        elif self.hi[channel] is not None:
            # the second byte of a lo-then-hi read, stashed by the first
            value = self.hi[channel]
            self.hi[channel] = None
        else:
            value = self.count[channel]
        if self.access[channel] == 1:
            return value & 0xFF
        if self.access[channel] == 2:
            return (value >> 8) & 0xFF
        self.hi[channel] = (value >> 8) & 0xFF
        return value & 0xFF

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


class Segments(list):
    """The six segment registers, with their hidden halves attached.

    On a real 386 a segment register is two things: a selector you can see and
    a base/limit/attributes triple you cannot.  The visible half changes when
    you load it; the hidden half changes at the same moment and *never* any
    other time -- which is why clearing CR0.PE leaves the previous segment
    working, and why SeaBIOS can switch to real mode and jump through a
    descriptor it loaded while still protected.
    """

    def __init__(self, machine, values):
        super().__init__(values)
        self.machine = machine

    def __setitem__(self, index, value):
        value &= 0xFFFF
        super().__setitem__(index, value)
        self.machine.cache_segment(index, value)


class Machine:
    """A 386 with 1 MiB of RAM, a text plane and an interrupt table."""

    def __init__(self, ram_size: int = RAM_SIZE) -> None:
        self.ram = bytearray(ram_size)
        self.ram_size = ram_size
        self.reg = [0] * 8
        # the control registers and the two table registers, before the segment
        # registers because loading a selector reads the descriptor table
        self.cr = [0] * 5
        self.gdtr = [0, 0]
        self.idtr = [0, 0]
        self.ldtr = 0
        self.tr = 0
        self.pe = False
        self.dsz = 2                 # default operand size of the code we run
        self.ipmask = 0xFFFF
        self.spmask = 0xFFFF
        # the segment registers' hidden halves: what each selector last meant
        self.base = [0] * 6
        self.limit = [0xFFFF] * 6
        self.sreg = Segments(self, [0] * 6)
        self.eip = 0
        self.eflags = FLAG_ALWAYS | FLAG_IF
        self.halted = False
        self.steps = 0
        # ---- the thermal system ------------------------------------------
        # The same equations the sensor core runs in the Scratch machine, so
        # the two boards report the same temperatures. See hwStep in
        # userspace/i386/sensor.jsw for why each constant is what it is.
        self.cpu_temp = 27.0
        self.fan_rpm = 800.0
        self.fan_target = 800.0
        self.hdd_temp = 27.0
        self.hdd_rpm = 0.0
        self.t_ambient = 25.0
        self.p_idle = 4.0
        self.p_max = 25.0
        self.c_th = 8.0
        self.r_th = 1.6
        self.fan_min = 800.0
        self.fan_max = 3000.0
        self.fan_knee = 32.0
        self.fan_slope = 45.0
        self.spin_up_secs = 4.5
        self.emu_on = 1
        self.cpu_base = 33
        # ---- the ATA channel --------------------------------------------
        self.ata_err = 0
        self.ata_count = 0
        self.ata_lba = 0
        self.ata_cmd = 0
        self.ata_feat = 0
        self.ata_buf = []
        self.ata_pos = 0
        self.ata_status = 0x50
        self.ata_lo = 0
        self.ata_mid = 0
        self.ata_hi = 0
        self.ata_drv = 0
        self.ata_smart_on = 0
        self.ata_sectors = 128
        # ---- AHCI ------------------------------------------------------
        # A register file the guest reaches with ordinary stores, and a
        # command list it builds in RAM: the SATA controller is memory, not
        # ports.
        self.ahci = [0] * AHCI_WORDS
        self.ahci_ports = 1
        self._ahci_init()
        # ---- SCSI ------------------------------------------------------
        self.scsi_buf = [0] * 256
        self.scsi_pos = 0
        self.scsi_status = 0
        self.scsi_sense = 0
        self.scsi_lba = 0
        self.scsi_len = 1
        self.scsi_cmd = 0
        self.thermal_done = 0.0      # emulated seconds already integrated
        self.hw_ticks = 0
        self.smart_sel = 1
        self.smart = [100] * 10
        self.smart_worst = [100] * 10
        self.error = ""
        self.out = []
        self.keys = []
        self.ports = {}
        self.ticks = 0
        self.reset_pending = False
        self.resets = 0
        self.trace = None
        # the battery-backed CMOS: 128 bytes behind ports 0x70/0x71, and the
        # one device whose contents survive a power cycle
        # the board's chips: how many cores, which one is asking, and which
        # have been released. The model runs one core, so a release is modelled
        # as that core checking in at once -- the trampoline's effect, not its
        # timing.
        self.cores = 1
        self.core_id = 1
        # what the display card can show: the whole 80 x 30 plane unless a test
        # asks for a smaller stage, the way Scratch's own 480 x 360 is 60 x 22
        self.view_cols = 80
        self.view_rows = 30
        # the hardware monitor: a selected core, and the emulated clock
        self.core_sel = 1
        self.cpu_base = 33
        self.emu_on = 1
        self.key_repeat = 120             # ms between repeats of a held key
        self.core_run = [0] * 16
        self.pci_addr = 0                 # 0xCF8: which dword the next read means
        self.pci_devices = PCI_DEVICES
        self.threads = 1                  # instruction budget multiplier per core
        self.perf_factor = 1              # multiplier on the reported clock
        self.time_rate = 100              # per cent: how fast the guest's clock runs
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
        """seg is an index into the six segment registers, not a selector."""
        lin = (self.sbase(seg) + off) & 0xFFFF_FFFF
        if not self.pe:
            return lin & 0xFFFFF          # real mode wraps at a megabyte
        if not self.cr[0] & 0x8000_0000:
            # Protected mode with paging off still wraps at a megabyte here,
            # which is what this board did before paging existed and what a real
            # SeaBIOS turns out to depend on. It is not what the hardware does:
            # on a 386, a linear address above the installed RAM reads an open
            # bus. Extended memory is reached the way a kernel reaches it --
            # through page tables, which return a physical address untruncated.
            return lin & 0xFFFFF
        return self.translate(lin)

    # ---- paging ------------------------------------------------------

    def translate(self, lin: int) -> int:
        """Two levels, four kilobytes a page. A missing entry is a page fault:
        interrupt 14, with the address that caused it left in CR2."""
        pde_at = (self.cr[3] & ~0xFFF) + ((lin >> 22) & 0x3FF) * 4
        pde = self.fetch_physical(pde_at)
        if not pde & 1:
            self.page_fault(lin)
            return 0
        pte_at = (pde & ~0xFFF) + ((lin >> 12) & 0x3FF) * 4
        pte = self.fetch_physical(pte_at)
        if not pte & 1:
            self.page_fault(lin)
            return 0
        return (pte & ~0xFFF) + (lin & 0xFFF)

    def fetch_physical(self, addr: int) -> int:
        """A dword out of physical memory, without asking the pager again."""
        at = addr & 0xFFFF_FFFF
        if at + 4 > self.ram_size:
            return 0xFFFF_FFFF
        return int.from_bytes(self.ram[at:at + 4], "little")

    def page_fault(self, lin: int) -> None:
        if getattr(self, "_in_fault", False):
            self.error = f"page fault in a page fault at {lin:#x}"
            self.halted = True
            return
        self.cr[2] = lin & 0xFFFF_FFFF
        self._in_fault = True
        try:
            self.interrupt(14)
        finally:
            self._in_fault = False

    def linear(self, selector, off):
        """The 20-bit address a real-mode selector:offset pair names."""
        return ((selector << 4) + off) & 0xFFFFF

    def read(self, addr, size):
        addr &= 0xFFFF_FFFF
        # the chipset forwards the last megabyte of the four-gigabyte space to
        # the top megabyte of the real one, which is where a protected-mode
        # BIOS looks for its own ROM
        if addr >= 0xFFF0_0000:
            addr = addr - 0xFFF0_0000 + 0xF0000
        if addr + size > self.ram_size:
            return MASK[size]      # nothing there: an open bus reads all ones
        return int.from_bytes(self.ram[addr:addr + size], "little")

    def write(self, addr, value, size):
        addr &= 0xFFFF_FFFF
        if addr >= 0xFFF0_0000:
            addr = addr - 0xFFF0_0000 + 0xF0000
        if addr + size > self.ram_size:
            return                 # and a write to nothing goes nowhere
        self.ram[addr:addr + size] = (value & MASK[size]).to_bytes(size, "little")

    # ---- registers ---------------------------------------------------

    def sbase(self, index):
        """Where a segment starts: always the cached answer, never a fresh
        computation.  The cache is only rewritten when the selector is."""
        return self.base[index]

    # ---- protected mode ----------------------------------------------

    def descriptor(self, selector):
        """The hidden half of a segment register: base, limit and flags, read
        out of the descriptor table the selector points into."""
        if selector & 4:
            table = self.ldtr if self.ldtr else 0
        else:
            table = self.gdtr[0]
        at = table + (selector & ~7)
        raw = self.read(at, 4) | (self.read(at + 4, 4) << 32)
        base = ((raw >> 16) & 0xFFFF) | (((raw >> 32) & 0xFF) << 16) | ((raw >> 56) << 24)
        limit = (raw & 0xFFFF) | (((raw >> 48) & 0x0F) << 16)
        # the two attribute bytes are not adjacent: the access byte is next to
        # the base, the flags nibble (which holds D, the 32-bit switch) sits
        # above the limit. Reading D out of the access byte is a quiet way to
        # stay in 16-bit mode for ever.
        return base, limit, (raw >> 40) & 0xFF, (raw >> 52) & 0x0F

    def write_cr0(self, value):
        """CR0's low bit is the whole 8086-to-386 switch. Entering protected
        mode changes nothing until a segment is reloaded, which is what makes
        the far jump after it meaningful; leaving reloads the six registers
        back to plain real-mode bases, because the selectors they hold no
        longer index anything."""
        was = self.pe
        self.cr[0] = value & 0xFFFF_FFFF
        self.pe = bool(value & 1)
        if not was and self.pe:
            for i in range(6):          # the hidden halves keep their values
                self.base[i] = self.sreg[i] << 4
                self.limit[i] = 0xFFFF
        # clearing PE does not reload anything: a segment keeps the base it was
        # loaded with until the next `mov sreg`. SeaBIOS's __call16 depends on
        # exactly that -- it clears PE and jumps through a 16-bit descriptor
        # whose base is the ROM, all without touching the cach again.

    def loadseg(self, index, selector):
        """Point a segment register at a selector; the cache follows."""
        self.sreg[index] = selector

    def cache_segment(self, index, selector):
        """Fill in the hidden half of a segment register."""
        flags = 0
        if self.pe:
            self.base[index], self.limit[index], _attr, flags = self.descriptor(selector)
        else:
            self.base[index] = selector << 4
            self.limit[index] = 0xFFFF
        if index == SEG_CS:
            # the D bit decides what an unprefixed instruction means: a 32-bit
            # code segment reads `push eax` as four bytes, a 16-bit one as two
            wide = self.pe and bool(flags & 0x4)
            self.dsz = 4 if wide else 2
            self.ipmask = 0xFFFF_FFFF if wide else 0xFFFF
            self.spmask = 0xFFFF_FFFF if wide else 0xFFFF

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

    def core_usage(self, index):
        """What the core reports it did with its last frame.

        The model retires instructions flat out with no frames at all, so there
        is nothing real to measure; this invents a number that moves with the
        instruction counter and stays the same across two runs of the same
        program, which is what a test wants.
        """
        return (self.steps // 997 + index * 13) % 61 + 20

    # ---- the thermal system -----------------------------------------

    def thermal(self, seconds: float, util: float) -> None:
        """Integrate the die's temperature for `seconds` at `util` load.

        Explicit Euler in quarter-second steps, exactly as the sensor core does
        it, so the two machines cannot drift apart: watts in from the clock and
        the load, watts out through a resistance the fan sets, and the fan's own
        curve closing the loop.
        """
        if not self.emu_on:
            return                 # hardware emulation off: the board has no sensors
        end = self.thermal_done + seconds
        while self.thermal_done < end:
            dt = 0.25
            self.thermal_done += dt
            util = max(0.0, min(1.0, util))
            want = self.fan_min + (self.cpu_temp - self.fan_knee) * self.fan_slope
            self.fan_target = max(self.fan_min, min(self.fan_max, want))
            self.fan_rpm += (self.fan_target - self.fan_rpm) * 0.10
            if self.fan_rpm < 1:
                self.fan_rpm = 0.0
            # a board whose fan curve has been pinned to zero still has to
            # answer: no divider is worth a divide by zero
            flow = self.fan_rpm / self.fan_max if self.fan_max else 0.0
            r = self.r_th / (0.40 + 0.90 * flow)
            p = self.p_idle + (self.p_max - self.p_idle) * util * (self.cpu_base / 33)
            t = self.cpu_temp + (p - (self.cpu_temp - self.t_ambient) / r) / self.c_th * dt
            self.cpu_temp = max(self.t_ambient, min(110.0, t))
            # the drive: a spindle on its way up, and a body with more mass
            frac = min(1.0, self.thermal_done / self.spin_up_secs)
            rpm = 7200 * math.sqrt(frac)
            self.hw_ticks += 1
            if rpm > 60:
                rpm += self.hw_ticks % 7 - 3
            self.hdd_rpm = round(rpm)
            want_t = self.t_ambient + 6 + 14 * (rpm / 7200)
            self.hdd_temp += (want_t - self.hdd_temp) * dt / 40
            self.hdd_temp = min(65.0, self.hdd_temp)
            self._smart_update()

    def _smart_update(self) -> None:
        hours = int(self.thermal_done // 3600)
        self.smart[1] = max(1, 100 - int(self.thermal_done // 3600 // 20))
        self.smart[2] = min(255, hours)          # 9: power-on hours, raw
        value = 100 - (round(self.hdd_temp) - 25) * 2
        self.smart[4] = max(1, min(100, value))  # 194: temperature
        self.smart[3] = 100                      # 12: power cycles
        self.smart[5] = 100                      # 197: pending sectors
        for i in range(1, 6):
            self.smart_worst[i] = min(self.smart_worst[i], self.smart[i])

    # ---- the ATA channel ---------------------------------------------
    #
    # The same register file and the same status bits the Scratch machine
    # implements, because a drive is a protocol, not an implementation detail:
    # a guest that polls DRDY has to see the same answer on both boards.

    def ata_in(self) -> int:
        """One 16-bit read of the data register."""
        value = 0
        if self.ata_pos < 256:
            value = self.ata_buf[self.ata_pos]
            self.ata_pos += 1
        if self.ata_pos >= 256:
            if self.ata_cmd == 0x20 and self.ata_count > 1:
                self.ata_count -= 1
                self.ata_lba += 1
                self._ata_load()
            else:
                self.ata_count = 0
                self.ata_status = 0x50
        return value & 0xFFFF

    def _ata_load(self) -> None:
        start = DISK_AT + self.ata_lba * 512
        blob = self.ram[start:start + 512]
        self.ata_buf = [blob[i] | (blob[i + 1] << 8) for i in range(0, len(blob), 2)]
        self.ata_pos = 0
        self.ata_status = 0x58

    def ata_command(self, cmd: int) -> None:
        self.ata_cmd = cmd
        self.ata_err = 0
        self.ata_lba = self.ata_lo | (self.ata_mid << 8) | (self.ata_hi << 16)
        if self.ata_lba >= self.ata_sectors:
            self.ata_err = 0x10
            self.ata_status = 0x41
            return
        if cmd == 0xEC:
            self.ata_buf = list(ATA_IDENT)
            self.ata_pos = 0
            self.ata_status = 0x58
            return
        if cmd in (0x20, 0x24, 0x21):
            self._ata_load()
            return
        if cmd == 0xB0:
            if self.ata_feat == 0xD8:
                self.ata_smart_on = 1
                self.ata_status = 0x50
                return
            if self.ata_feat == 0xD9:
                self.ata_smart_on = 0
                self.ata_status = 0x50
                return
            if self.ata_feat in (0xD0, 0xD1):
                buf = [0] * 256
                buf[0] = 0x0010
                for i in range(5):
                    b = 2 + i * 6
                    buf[b] = ATA_SMART_IDS[i]
                    buf[b + 2] = ((self.smart[i + 1] & 0xFF) << 8) | (self.smart_worst[i + 1] & 0xFF)
                self.ata_buf = buf
                self.ata_pos = 0
                self.ata_status = 0x58
                return
            self.ata_err = 0x04
            self.ata_status = 0x41
            return
        if cmd in (0x91, 0xEF, 0xE1, 0xF9):
            self.ata_status = 0x50
            return
        self.ata_err = 0x04
        self.ata_status = 0x41

    # ---- AHCI --------------------------------------------------------

    def _ahci_init(self) -> None:
        self.ahci[0] = 0x00141F00          # CAP: 32 slots (bits 8-12), one port
        self.ahci[3] = self.ahci_ports     # PI
        self.ahci[4] = 0x00010300          # VS: AHCI 1.3
        self.ahci_port_reset(0)

    def ahci_port_reset(self, port: int) -> None:
        b = (256 + port * 128) // 4
        self.ahci[b + 9] = 0x00000101      # PxSIG: an ATA drive here
        self.ahci[b + 10] = 0x00000123     # PxSSTS: present and active
        self.ahci[b + 8] = 0x00000050      # PxTFD: ready
        self.ahci[b + 14] = 0               # PxCI

    def ahci_read(self, addr: int, size: int) -> int:
        off = addr - AHCI_BASE
        if off < 0 or off + size > AHCI_WORDS * 4:
            return MASK[size]
        value = 0
        for i in range(size):
            o = off + i
            word = self.ahci[o // 4]
            value |= ((word >> (8 * (o % 4))) & 0xFF) << (8 * i)
        return value

    def ahci_write(self, addr: int, value: int, size: int) -> None:
        off = addr - AHCI_BASE
        if off < 0 or off + size > AHCI_WORDS * 4:
            return
        for i in range(size):
            o = off + i
            sh = 8 * (o % 4)
            old = self.ahci[o // 4]
            byte = (value >> (8 * i)) & 0xFF
            self.ahci[o // 4] = (old & ~(0xFF << sh)) | (byte << sh)
        if off == 4:
            if value & 1:                  # HR: reset the ports
                self.ahci_port_reset(0)
            self.ahci[(256 + 0) // 4 + 6] = 0
        if off == 0x138:                   # PxCI: run the slots named
            self.ahci_start(0)

    def ahci_start(self, port: int) -> None:
        b = (256 + port * 128) // 4
        ci = self.ahci[b + 14]
        if not ci:
            return
        clb = self.ahci[b + 0]
        for slot in range(32):
            if ci & (1 << slot):
                self.ahci_slot(port, slot, clb)
        self.ahci[b + 14] = 0
        self.ahci[b + 4] = 0x40000000

    def ahci_slot(self, port: int, slot: int, clb: int) -> None:
        hdr = clb + slot * 32
        ctba = self.read(hdr + 4, 4)
        prdtl = self.read(hdr + 2, 2)
        cmd = self.read(ctba + 2, 1)
        lba = (self.read(ctba + 4, 3) | (self.read(ctba + 8, 3) << 24))
        count = self.read(ctba + 12, 1)
        b = (256 + port * 128) // 4
        if lba >= self.ata_sectors and cmd != 0xEC:
            self.ahci[b + 8] = 0x00000051
            return
        dba = self.read(ctba + 128, 4) if prdtl else 0
        if cmd == 0xEC:
            for i in range(256):
                self.write(dba + i * 2, ATA_IDENT[i], 2)
        else:
            n = count or 1
            for s in range(n):
                start = DISK_AT + (lba + s) * 512
                self.ram[dba + s * 512:dba + s * 512 + 512] = self.ram[start:start + 512]
        self.ahci[b + 8] = 0x00000050

    # ---- SCSI --------------------------------------------------------

    def scsi_command(self, op: int) -> None:
        self.scsi_cmd = op
        self.scsi_pos = 0
        self.scsi_status = 0
        self.scsi_sense = 0
        self.scsi_buf = [0] * 256
        if op == 0x00:
            return
        if op == 0x12:
            self.scsi_buf[:18] = SCSI_INQUIRY
            return
        if op == 0x25:
            # READ CAPACITY(10): the last LBA and the block size, both
            # big-endian, packed into 16-bit words the way the machine packs
            # them -- first byte in the low half.
            last = self.ata_sectors - 1
            self.scsi_buf[0] = ((last >> 24) & 0xFF) | (((last >> 16) & 0xFF) << 8)
            self.scsi_buf[1] = ((last >> 8) & 0xFF) | ((last & 0xFF) << 8)
            self.scsi_buf[2] = 0
            self.scsi_buf[3] = 2                      # 0x0000_0200: 512
            return
        if op == 0x28:
            if self.scsi_lba >= self.ata_sectors:
                self.scsi_status = 1
                self.scsi_sense = 0x21
                return
            start = DISK_AT + self.scsi_lba * 512
            blob = self.ram[start:start + 512]
            self.scsi_buf[:256] = [blob[i] | (blob[i + 1] << 8) for i in range(0, 512, 2)]
            return
        self.scsi_status = 1
        self.scsi_sense = 0x20

    def scsi_data(self) -> int:
        value = 0
        if self.scsi_pos < 256:
            value = self.scsi_buf[self.scsi_pos]
            self.scsi_pos += 1
        return value

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
        if port == 0x5BC:                 # how open the gate is
            return self.ports.get(0x61, 0) & 3
        if port in (0x5BA, 0x5BB):        # the tone the speaker is making
            hz = self.speaker_hz()
            return hz & 0xFF if port == 0x5BA else (hz >> 8) & 0xFF
        if port == 0x310:
            return self.scsi_data()
        if port == 0x316:
            return self.scsi_status
        if port == 0x317:
            return self.scsi_sense
        if port == 0x1F0:
            return self.ata_in()
        if port == 0x1F1:
            return self.ata_err
        if port == 0x1F2:
            return self.ata_count
        if port == 0x1F3:
            return self.ata_lo
        if port == 0x1F4:
            return self.ata_mid
        if port == 0x1F5:
            return self.ata_hi
        if port == 0x1F6:
            return self.ata_drv
        if port in (0x1F7, 0x3F6):
            return self.ata_status
        if port == 0xCF8:
            return self.pci_addr & 0xFFFFFFFF
        if 0xCFC <= port <= 0xCFF:
            return self.pci_read(port - 0xCFC, size)
        if port == 0x5A0:
            return self.cores
        if port == 0x5AB:
            return self.threads
        if port == 0x5AC:
            return self.perf_factor
        if port == 0x5AD:
            return self.time_rate
        if port == 0x5AE:
            return self.cores
        if port == 0x5A6:                 # the core selected on 0x5A5
            return self.core_usage(self.core_sel)
        if port == 0x5A7:                 # ... and the clock it reports, in MHz.
            # The model has no wall clock to divide by, so it answers with the
            # figure the real machine measures on this host -- two and a half
            # million instructions a second, which is a 25 MHz 80386 -- rather
            # than with a number it cannot know.
            return 25 if self.emu_on else 0
        if port == 0x5A3:                 # the card: cells across the stage
            return self.view_cols
        if port == 0x5A4:                 # ... and cells down
            return self.view_rows
        if port == 0x5A2:
            return self.core_id
        # ---- the sensors, behind the addresses the sensor core publishes.
        # The model has no frames, so it advances the thermal system by the
        # emulated time the instruction counter represents: a machine that has
        # retired a second's worth of instructions has also been hot for a
        # second.
        if 0x5B0 <= port <= 0x5B9:
            self.thermal(self.steps / 1_700_000 - self.thermal_done,
                         self.core_usage(1) / 100)
            if port == 0x5B0:
                return int(round(self.cpu_temp))
            if port == 0x5B1:
                return int(self.fan_rpm) & 0xFF
            if port == 0x5B2:
                return (int(self.fan_rpm) >> 8) & 0xFF
            if port == 0x5B3:
                return int(round(self.hdd_temp))
            if port == 0x5B4:
                return int(self.hdd_rpm) & 0xFF
            if port == 0x5B5:
                return (int(self.hdd_rpm) >> 8) & 0xFF
            if port == 0x5B6:
                return int(self.thermal_done // 3600) & 0xFF
            if port == 0x5B8:
                return self.smart[self.smart_sel]
            if port == 0x5B9:
                return self.smart_worst[self.smart_sel]
            return 0
        if port == 0x70:
            return self.cmos_index
        if port == 0x71:
            return self.cmos[self.cmos_index & 0x7F]
        # The primary channel is a real drive now (see ata_command above); the
        # secondary channel is still empty, and an empty channel has to answer
        # "not busy, not ready" rather than 0xFF -- a floating bus makes an ATA
        # probe spin for ever on the busy bit, which is what SeaBIOS did.
        if port in (0x177, 0x376):
            return 0x00
        if 0x170 <= port <= 0x176:
            return 0xFF
        if port == 0x3FD:
            return 0x60
        if port == 0x3F8:
            return 0x60
        if port == 0x60:
            return self.keys.pop(0) if self.keys else 0
        if port == 0x64:
            return 0x01 if self.keys else 0
        return self.ports.get(port, 0xFF)

    def speaker_hz(self) -> int:
        """What the speaker sounds like: channel 2's divisor, behind a gate.

        0x61 bit 0 lets timer 2's square wave through and bit 1 enables the
        speaker behind it, so both have to be set -- a guest that programs the
        divisor and stops hears nothing, which is the classic PC-speaker bug.
        """
        if self.ports.get(0x61, 0) & 3 != 3:
            return 0
        divisor = self.pit.reload[2] or 0x10000
        return PIT_HZ // divisor

    def port_out(self, port, value, size):
        if port == 0xCF8:
            self.pci_addr = value & 0xFFFFFFFF
            return
        if 0xCFC <= port <= 0xCFF:
            self.pci_write(port - 0xCFC, value, size)
            return
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
            self.pic.data_write(value)
            return
        if port == PIC_SLAVE:
            self.pic2.command(value & 0xFF)
            return
        if port == PIC_SLAVE + 1:
            self.pic2.data_write(value)
            return
        if port == 0x5A1:
            core = value & 0xFF
            if 1 <= core <= self.cores:
                self.core_run[core] = 1
                # the AP runs the trampoline: it records itself and checks in
                self.write(0x8400 + core, core, 1)
                self.write(0x8500 + core, 1, 1)
            return
        if port == 0x5A5:                 # which core the monitor reads mean
            if 1 <= (value & 0xFF) <= self.cores:
                self.core_sel = value & 0xFF
            return
        if port == 0x5A8:                 # the clock the board should report
            self.cpu_base = value & 0xFF
            return
        if port == 0x5A9:                 # the emulation master switch
            self.emu_on = value & 0x01
            return
        if port == 0x5AA:                 # key repeat, in units of 10 ms
            self.key_repeat = (value & 0xFF) * 10
            return
        if port == 0x5AB:                 # instruction budget per core
            if 1 <= (value & 0xFF) <= 4:
                self.threads = value & 0xFF
            return
        if port == 0x5AC:                 # the performance multiplier
            if 1 <= (value & 0xFF) <= 16:
                self.perf_factor = value & 0xFF
            return
        if port == 0x5AD:                 # the guest's clock, per cent
            if 10 <= (value & 0xFF) <= 255:
                self.time_rate = value & 0xFF
            return
        if port == 0x5AE:                 # how many cores there should be
            cores = value & 0xFF
            if 1 <= cores <= 16:
                self.cores = cores
                for index in range(cores + 1, 16):
                    self.core_run[index] = 0
                if self.core_sel > cores:
                    self.core_sel = cores
            return
        if port == 0x311:
            self.scsi_command(value & 0xFF)
            return
        if port == 0x312:
            self.scsi_lba = self.scsi_lba - self.scsi_lba % 256 + (value & 0xFF)
            return
        if port == 0x313:
            self.scsi_lba = (self.scsi_lba - self.scsi_lba % 65536
                             + (value & 0xFF) * 256 + self.scsi_lba % 256)
            return
        if port == 0x314:
            self.scsi_lba = (value & 0xFF) * 65536 + self.scsi_lba % 65536
            return
        if port == 0x315:
            self.scsi_len = value & 0xFF
            return
        if port == 0x1F0:
            self.ata_pos += 1             # the image is read-only: nothing latches
            return
        if port == 0x1F1:
            self.ata_feat = value & 0xFF
            return
        if port == 0x1F2:
            self.ata_count = value & 0xFF
            return
        if port == 0x1F3:
            self.ata_lo = value & 0xFF
            return
        if port == 0x1F4:
            self.ata_mid = value & 0xFF
            return
        if port == 0x1F5:
            self.ata_hi = value & 0xFF
            return
        if port == 0x1F6:
            self.ata_drv = value & 0xFF
            if self.ata_drv >= 0xA0:
                self.ata_hi = self.ata_drv & 0x0F
            return
        if port == 0x1F7:
            self.ata_command(value & 0xFF)
            return
        if port in (0x1F7, 0x3F6) or port == 0x3F6:
            return
        if port == 0x5B7:                 # which SMART attribute to read next
            if 1 <= (value & 0xFF) <= 5:
                self.smart_sel = value & 0xFF
            return
        if port == 0xCF9:                 # the reset control register
            if value & 0x04:              # SYS_RST: start again from the ROM
                self.reset_pending = True
            return
        if port == 0x70:
            self.cmos_index = value & 0x7F
            return
        if port == 0x71:
            self.cmos[self.cmos_index & 0x7F] = value & 0xFF
            return
        if port in (0x3F8, 0x402):
            # 0x3F8 is the first serial port; 0x402 is the port QEMU and
            # SeaBIOS agree to use for debug output. Both end up in `out`.
            self.out.append(value & 0xFF)
        self.ports[port] = value & 0xFF

    # ---- decode ------------------------------------------------------

    def fetch8(self):
        value = self.read(self.sbase(SEG_CS) + self.eip, 1)
        self.eip = (self.eip + 1) & self.ipmask
        return value

    def fetch16(self):
        lo = self.fetch8()
        return lo | (self.fetch8() << 8)

    def fetch32(self):
        value = 0
        for i in range(4):
            value |= self.fetch8() << (8 * i)
        return value

    def modrm(self, seg=SEG_DS, addr_size=2):
        """ModR/M -> (mod, reg, rm, address). Register operands share the
        number: on x86 `rm` is a register when mod is 3 and a memory operand
        otherwise, which is why the two live in one field.

        32-bit addressing adds one indirection: `[base + index*scale + disp]`
        is spelled with a second byte (the SIB) when rm is 4, and the offsets
        are 32 bits wide.  The 16-bit form is a fixed table of base and index
        registers instead, so the two are written out separately rather than
        forced through one formula.
        """
        byte = self.fetch8()
        mod = byte >> 6
        reg = (byte >> 3) & 7
        rm = byte & 7
        if mod == 3:
            return mod, reg, rm, None
        if addr_size == 4:
            return mod, reg, rm, self.phys_32(seg, mod, rm)
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
        return mod, reg, rm, self.phys(seg, off & 0xFFFF)

    def phys_32(self, seg, mod, rm):
        """The 32-bit ModR/M addressing forms, SIB included."""
        off = 0
        if rm == 4:                                # a SIB byte follows
            sib = self.fetch8()
            scale = 1 << (sib >> 6)
            index = (sib >> 3) & 7
            base = sib & 7
            if index != 4:                         # index 4 is "no index"
                off = (self.reg[index] & 0xFFFF_FFFF) * scale
            if base == 5 and mod == 0:
                off += self.fetch32()
            else:
                off += self.reg[base] & 0xFFFF_FFFF
                if base in (4, 5):                 # [esp] and [ebp] read the stack
                    seg = SEG_SS
        elif rm == 5 and mod == 0:                 # a bare disp32
            off = self.fetch32()
        else:
            off += self.reg[rm] & 0xFFFF_FFFF
            if rm == 5:
                seg = SEG_SS
        if mod == 1:
            off += sext(self.fetch8(), 8)
        elif mod == 2:
            off += self.fetch32()
        return self.phys(seg, off & 0xFFFF_FFFF)

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

    def push(self, value, size=2):
        self.reg[4] = (self.reg[4] - size) & self.spmask
        self.write(self.phys(SEG_SS, self.reg[4]), value, size)

    def pop(self, size=2):
        value = self.read(self.phys(SEG_SS, self.reg[4]), size)
        self.reg[4] = (self.reg[4] + size) & self.spmask
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
        size = self.dsz
        self.push(self.eflags & MASK[size], size)
        self.push(self.sreg[SEG_CS], size)
        self.push(self.eip & MASK[size], size)
        self.set_flag(FLAG_IF, False)
        self.set_flag(FLAG_TF, False)
        if self.pe:
            # an interrupt gate is eight bytes: offset, selector, attributes,
            # and the top half of the offset on a 386
            at = self.idtr[0] + number * 8
            off = self.read(at, 2) | (self.read(at + 6, 2) << 16)
            self.loadseg(SEG_CS, self.read(at + 2, 2))
            self.eip = off & self.ipmask
        else:
            self.loadseg(SEG_CS, self.read(number * 4 + 2, 2))
            self.eip = self.read(number * 4, 2)

    def iret(self):
        size = self.dsz
        self.eip = self.pop(size) & self.ipmask
        self.loadseg(SEG_CS, self.pop(size))
        self.eflags = (self.eflags & ~MASK[size]) | self.pop(size) | FLAG_ALWAYS

    # ---- the PCI bus -------------------------------------------------

    def pci_dword(self, bus, device, function, index):
        """One dword of a device's 256-byte configuration space.

        Only the first 64 bytes exist on this bus; the rest reads as zero, which
        is what a device that has no extended capability list reports.
        """
        for entry in self.pci_devices:
            if (entry[0], entry[1], entry[2]) != (bus, device, function):
                continue
            _, _, _, vendor, ident, cls, bars = entry
            if index == 0:
                return vendor | (ident << 16)
            if index == 1:
                # status: capability list absent, DEVSEL medium, and the
                # command register at power-on: I/O and memory space both off
                return 0x0200 << 16
            if index == 2:
                return (cls << 8) | 0x00            # class, subclass, prog-if, rev
            if index == 3:
                # the header type, in bits 16..23: bit 7 says this slot has more
                # than one function, which is how a scan knows to look past fn 0
                siblings = any(other[0] == bus and other[1] == device
                               and other[2] != function
                               for other in self.pci_devices)
                return (0x80 if siblings else 0x00) << 16
            if 4 <= index <= 9:
                slot = index - 4
                if slot < len(bars):
                    return bars[slot]
                return 0
            if index == 11:
                return vendor << 16 | ident         # subsystem: the same pair
            if index == 15:
                return 0x0100 | (device + 10)       # INTA, and a plausible line
            return 0
        return 0xFFFFFFFF                            # nothing there

    def pci_read(self, byte_lane, size):
        lane = byte_lane & 3
        if not self.pci_addr & 0x80000000:
            return 0
        bus = (self.pci_addr >> 16) & 0xFF
        device = (self.pci_addr >> 11) & 0x1F
        function = (self.pci_addr >> 8) & 0x07
        index = (self.pci_addr >> 2) & 0x3F
        dword = self.pci_dword(bus, device, function, index)
        if size == 1:
            return (dword >> (lane * 8)) & 0xFF
        if size == 2:
            return (dword >> (lane * 8)) & 0xFFFF
        return dword & 0xFFFFFFFF

    def pci_write(self, byte_lane, value, size):
        # every writable field on this board is a claim the BIOS makes, not one
        # the guest gets to change, so a write is recorded and otherwise ignored
        self.ports[0xCFC + (byte_lane & 3)] = value
        return

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

    def reset(self) -> None:
        """A hardware reset: the CPU starts again at the top of the ROM.

        RAM keeps what it had -- a real reset does not clear memory -- but
        every register, the control registers, the descriptor tables and both
        chips go back to the state they come out of the factory in. SeaBIOS
        asks for this by writing 0xCF9 after it finds nothing to boot, which is
        the point at which its POST has finished.
        """
        self.reg = [0] * 8
        self.cr = [0] * 5
        self.gdtr = [0, 0]
        self.idtr = [0, 0]
        self.ldtr = 0
        self.tr = 0
        self.pe = False
        self.dsz = 2
        self.ipmask = 0xFFFF
        self.spmask = 0xFFFF
        self.sreg[SEG_DS] = 0
        self.sreg[SEG_ES] = 0
        self.sreg[SEG_SS] = 0
        self.sreg[SEG_FS] = 0
        self.sreg[SEG_GS] = 0
        self.sreg[SEG_CS] = 0xF000
        self.eip = 0xFFF0
        self.eflags = FLAG_ALWAYS | FLAG_IF
        self.halted = False
        self.pit = Pit()
        self.pic = Pic(0x08)
        self.pic2 = Pic(0x70)
        self.pic2.imr = 0xFF
        self.pit_acc = 0
        self.reset_pending = False
        self.resets += 1

    def step(self):
        if self.reset_pending:
            self.reset()
            return
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
    size = None                       # None means "whatever the code segment says"
    addr_size = None
    seg = SEG_DS
    rep = 0
    while True:
        op = m.fetch8()
        if op == 0x66:
            base = m.dsz if size is None else size
            size = 4 if base == 2 else 2
        elif op == 0x67:
            base = m.dsz if addr_size is None else addr_size
            addr_size = 4 if base == 2 else 2
        elif op in (0x26, 0x2E, 0x36, 0x3E, 0x64, 0x65):
            seg = {0x26: SEG_ES, 0x2E: SEG_CS, 0x36: SEG_SS,
                   0x3E: SEG_DS, 0x64: SEG_FS, 0x65: SEG_GS}[op]
        elif op in (0xF2, 0xF3):
            rep = op
        elif op == 0xF0:
            pass                                    # LOCK: single-threaded anyway
        else:
            break
    if size is None:
        size = m.dsz
    if addr_size is None:
        addr_size = m.dsz
    amask = MASK[addr_size]

    # ---- the eight-op ALU family, 0x00 .. 0x3D ----
    if op < 0x40 and (op & 7) < 6:
        alu_op = op >> 3
        form = op & 7
        if form == 0:                                # r/m8, r8
            mod, reg, rm, addr = m.modrm(seg, addr_size)
            res = m.alu(alu_op, m.rm_read(mod, rm, addr, 1), m.get8(reg), 1)
            if alu_op != 7:
                m.rm_write(mod, rm, addr, res, 1)
            return
        if form == 1:                                # r/m16|32, r16|32
            mod, reg, rm, addr = m.modrm(seg, addr_size)
            res = m.alu(alu_op, m.rm_read(mod, rm, addr, size), m.getreg(reg, size), size)
            if alu_op != 7:
                m.rm_write(mod, rm, addr, res, size)
            return
        if form == 2:                                # r8, r/m8
            mod, reg, rm, addr = m.modrm(seg, addr_size)
            res = m.alu(alu_op, m.get8(reg), m.rm_read(mod, rm, addr, 1), 1)
            if alu_op != 7:
                m.set8(reg, res)
            return
        if form == 3:                                # r16|32, r/m16|32
            mod, reg, rm, addr = m.modrm(seg, addr_size)
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
        m.push(m.reg[op & 7], size)
        return
    if 0x58 <= op <= 0x5F:
        m.setreg(op & 7, m.pop(size), size)
        return
    if op == 0x06:
        m.push(m.sreg[SEG_ES], size); return
    if op == 0x0E:
        m.push(m.sreg[SEG_CS], size); return
    if op == 0x16:
        m.push(m.sreg[SEG_SS], size); return
    if op == 0x1E:
        m.push(m.sreg[SEG_DS], size); return
    if op == 0x07:
        m.loadseg(SEG_ES, m.pop(size)); return
    if op == 0x17:
        m.loadseg(SEG_SS, m.pop(size)); return
    if op == 0x1F:
        m.loadseg(SEG_DS, m.pop(size)); return
    if op == 0x60:
        sp = m.reg[4]
        for i in (0, 1, 2, 3):                  # AX CX DX BX
            m.push(m.reg[i], size)
        m.push(sp, size)                        # the original SP
        for i in (5, 6, 7):                     # BP SI DI
            m.push(m.reg[i], size)
        return
    if op == 0x61:
        for i in (7, 6, 5, 4, 3, 2, 1, 0):
            value = m.pop(size)
            if i != 4:
                m.setreg(i, value, size)
            elif i == 4:
                pass                                 # sp itself is discarded
        return
    if op == 0x68:
        imm = m.fetch32() if size == 4 else m.fetch16()
        m.push(imm, size)
        return
    if op == 0x6A:
        m.push(sext(m.fetch8(), 8), size)
        return
    if op in (0x69, 0x6B):
        mod, reg, rm, addr = m.modrm(seg, addr_size)
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
            m.eip = (m.eip + rel) & m.ipmask
        return
    if op in (0x80, 0x81, 0x82, 0x83):
        mod, reg, rm, addr = m.modrm(seg, addr_size)
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
        mod, reg, rm, addr = m.modrm(seg, addr_size)
        m.alu(4, m.rm_read(mod, rm, addr, osize), m.getreg(reg, osize), osize)
        return
    if op in (0x86, 0x87):
        osize = 1 if op == 0x86 else size
        mod, reg, rm, addr = m.modrm(seg, addr_size)
        a = m.rm_read(mod, rm, addr, osize)
        b = m.getreg(reg, osize)
        m.rm_write(mod, rm, addr, b, osize)
        m.setreg(reg, a, osize)
        return
    if 0x88 <= op <= 0x8B:
        osize = 1 if op in (0x88, 0x8A) else size
        mod, reg, rm, addr = m.modrm(seg, addr_size)
        if op in (0x88, 0x89):
            m.rm_write(mod, rm, addr, m.getreg(reg, osize), osize)
        else:
            m.setreg(reg, m.rm_read(mod, rm, addr, osize), osize)
        return
    if op == 0x8C:
        mod, reg, rm, addr = m.modrm(seg, addr_size)
        if reg < 6:
            m.rm_write(mod, rm, addr, m.sreg[reg], 2)
        return
    if op == 0x8E:
        mod, reg, rm, addr = m.modrm(seg, addr_size)
        value = m.rm_read(mod, rm, addr, 2)
        if reg < 6:
            m.loadseg(reg, value)
        return
    if op == 0x8D:
        mod, reg, rm, addr = m.modrm(seg, addr_size)
        m.setreg(reg, addr if addr is not None else 0, size)
        return
    if op == 0x8F:
        mod, reg, rm, addr = m.modrm(seg, addr_size)
        m.rm_write(mod, rm, addr, m.pop(size), size)
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
        m.push(m.sreg[SEG_CS], size)
        m.push(m.eip, size)
        m.loadseg(SEG_CS, segv)
        m.eip = off & m.ipmask
        return
    if op == 0x9C:
        m.push(m.eflags & MASK[size], size)
        return
    if op == 0x9D:
        m.eflags = (m.eflags & ~MASK[size]) | m.pop(size) | FLAG_ALWAYS
        return
    if op == 0x9E:
        m.eflags = (m.eflags & ~0xFF) | (m.get8(4) & 0xD5) | FLAG_ALWAYS
        return
    if op == 0x9F:
        m.set8(4, m.eflags & 0xD7)
        return
    if 0xA0 <= op <= 0xA3:
        # moffs: the displacement is as wide as an address, not as wide as the
        # data it moves
        addr = m.phys(seg, m.fetch32() if addr_size == 4 else m.fetch16())
        if op in (0xA0, 0xA1):
            osize = 1 if op == 0xA0 else size
            m.setreg(0, m.read(addr, osize), osize)
        else:
            osize = 1 if op == 0xA2 else size
            m.write(addr, m.getreg(0, osize), osize)
        return
    if 0xA4 <= op <= 0xA7 or 0xAA <= op <= 0xAF:
        string_op(m, op, size, seg, rep, addr_size)
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
        mod, reg, rm, addr = m.modrm(seg, addr_size)
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
        m.eip = m.pop(size) & m.ipmask
        m.reg[4] = (m.reg[4] + off) & 0xFFFF
        return
    if op == 0xC3:
        m.eip = m.pop(size) & m.ipmask
        return
    if op in (0xC4, 0xC5):
        mod, reg, rm, addr = m.modrm(seg, addr_size)
        if mod == 3:                         # `les ax, ax` is #UD, not a crash
            return
        m.setreg(reg, m.read(addr, 4) & 0xFFFF, 2)
        m.loadseg(SEG_ES if op == 0xC4 else SEG_DS, m.read(addr + 2, 2))
        return
    if op in (0xC6, 0xC7):
        osize = 1 if op == 0xC6 else size
        mod, reg, rm, addr = m.modrm(seg, addr_size)
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
            m.push(m.read(m.phys(SEG_SS, m.reg[5]), 2))
        if depth:
            m.push(frame)
        m.reg[5] = frame
        m.reg[4] = (m.reg[4] - alloc) & 0xFFFF
        return
    if op == 0xC9:
        m.reg[4] = m.reg[5]
        m.reg[5] = m.pop(size)
        return
    if op == 0xCA:
        off = m.fetch16()
        m.eip = m.pop(size) & m.ipmask
        m.loadseg(SEG_CS, m.pop(size))
        m.reg[4] = (m.reg[4] + off) & 0xFFFF
        return
    if op == 0xCB:
        m.eip = m.pop(size) & m.ipmask
        m.loadseg(SEG_CS, m.pop(size))
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
            m.eip = (m.eip + rel) & m.ipmask
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
            m.push(m.eip, size)
        m.eip = (m.eip + rel) & m.ipmask
        return
    if op == 0xEA:
        off = m.fetch32() if size == 4 else m.fetch16()
        segv = m.fetch16()
        m.loadseg(SEG_CS, segv)
        m.eip = off
        return
    if op == 0xEB:
        rel = sext(m.fetch8(), 8)          # fetch first: m.eip must be the next
        m.eip = (m.eip + rel) & m.ipmask   # instruction, not the displacement
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
        mod, reg, rm, addr = m.modrm(seg, addr_size)
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
        addr = m.phys(seg, (m.reg[3] + m.get8(0)) & amask)
        m.set8(0, m.read(addr, 1))
        return
    if op == 0x9B:                           # wait: there is no coprocessor
        return
    if op == 0xF1:                           # int1: a breakpoint with a name
        m.interrupt(1)
        return
    if op == 0x63:                           # arpl
        mod, reg, rm, addr = m.modrm(seg, addr_size)
        dest = m.rm_read(mod, rm, addr, 2)
        src = m.getreg(reg, 2)
        take = (dest & 3) < (src & 3)
        m.set_flag(FLAG_ZF, take)
        if take:
            m.rm_write(mod, rm, addr, (dest & 0xFFFC) | (src & 3), 2)
        return
    if op == 0x62:                           # bound: #BR when out of range
        mod, reg, rm, addr = m.modrm(seg, addr_size)
        if mod == 3:                         # a register operand is #UD
            return
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
                # the port model is byte-granular, so a 16-bit read is two of
                # them -- the same thing `in ax, dx` does
                value = m.port_in(m.reg[2] & 0xFFFF, 1)
                if osize == 2:
                    value |= m.port_in(m.reg[2] & 0xFFFF, 1) << 8
                m.write(m.phys(SEG_ES, m.reg[7] & amask), value, osize)
                m.reg[7] = (m.reg[7] + delta) & amask
            else:                            # outs: ds:si -> port
                value = m.read(m.phys(seg, m.reg[6] & amask), osize)
                m.port_out(m.reg[2] & 0xFFFF, value, osize)
                m.reg[6] = (m.reg[6] + delta) & amask
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
        mod, reg, rm, addr = m.modrm(seg, addr_size)
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
            target = m.rm_read(mod, rm, addr, size)
            m.push(m.eip, size)
            m.eip = target & m.ipmask
        elif reg == 3:
            target = m.rm_read(mod, rm, addr, size)
            m.push(m.sreg[SEG_CS], size)
            m.push(m.eip, size)
            m.loadseg(SEG_CS, m.read(addr + size, 2))
            m.eip = target & m.ipmask
        elif reg == 4:
            m.eip = m.rm_read(mod, rm, addr, size) & m.ipmask
        elif reg == 5:
            m.loadseg(SEG_CS, m.read(addr + size, 2))
            m.eip = m.rm_read(mod, rm, addr, size) & m.ipmask
        elif reg == 6:
            m.push(m.rm_read(mod, rm, addr, size), size)
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
        two_byte(m, size, seg, addr_size)
        return
    if 0xD8 <= op <= 0xDF:
        # no coprocessor is fitted, so the escape traps: interrupt 7, exactly
        # what a 386SX does when a program tries to use a 387 that is not there
        m.interrupt(7)
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


def string_op(m, op, size, seg, rep, addr_size=2):
    """movs / cmps / stos / lods / scas: one element per pass, REP repeats.

    A repeat prefix is not a special instruction, it is a prefix on this one:
    CX counts down, and for the two comparing forms the loop also stops on the
    ZF state that REPE/REPNE asked for.
    """
    delta = -1 if m.flag(FLAG_DF) else 1
    amask = MASK[addr_size]
    kind = {0xA4: 0, 0xA5: 0, 0xA6: 1, 0xA7: 1, 0xAA: 2, 0xAB: 2,
            0xAC: 3, 0xAD: 3, 0xAE: 4, 0xAF: 4}[op]
    osize = 1 if op in (0xA4, 0xA6, 0xAA, 0xAC, 0xAE) else size
    while True:
        si = m.phys(seg, m.reg[6] & amask)
        if kind == 0:
            m.write(m.phys(SEG_ES, m.reg[7] & amask), m.read(si, osize), osize)
            m.reg[6] = (m.reg[6] + delta * osize) & amask
            m.reg[7] = (m.reg[7] + delta * osize) & amask
        elif kind == 1:
            di = m.phys(SEG_ES, m.reg[7] & amask)
            m.alu(7, m.read(si, osize), m.read(di, osize), osize)
            m.reg[6] = (m.reg[6] + delta * osize) & amask
            m.reg[7] = (m.reg[7] + delta * osize) & amask
        elif kind == 2:
            m.write(m.phys(SEG_ES, m.reg[7] & amask), m.getreg(0, osize), osize)
            m.reg[7] = (m.reg[7] + delta * osize) & amask
        elif kind == 3:
            m.setreg(0, m.read(si, osize), osize)
            m.reg[6] = (m.reg[6] + delta * osize) & amask
        else:
            di = m.phys(SEG_ES, m.reg[7] & amask)
            m.alu(7, m.getreg(0, osize), m.read(di, osize), osize)
            m.reg[7] = (m.reg[7] + delta * osize) & amask
        if not rep:
            return
        m.reg[1] = (m.reg[1] - 1) & amask
        if m.reg[1] == 0:
            return
        if kind in (1, 4):
            if rep == 0xF3 and not m.flag(FLAG_ZF):
                return
            if rep == 0xF2 and m.flag(FLAG_ZF):
                return


def two_byte(m, size, seg, addr_size=2):
    op = m.fetch8()
    if op == 0x01:
        mod, reg, rm, addr = m.modrm(seg, addr_size)
        if mod == 3:
            return
        if reg in (0, 1):                        # sgdt / sidt
            table = m.gdtr if reg == 0 else m.idtr
            limit, base = table
            m.write(addr, limit, 2)
            m.write(addr + 2, base, 4 if size == 4 else 3)
        elif reg in (2, 3):                      # lgdt / lidt
            limit = m.read(addr, 2)
            base = m.read(addr + 2, 4 if size == 4 else 3)
            if reg == 2:
                m.gdtr = [base, limit]
            else:
                m.idtr = [base, limit]
        elif reg == 4:                           # smsw
            m.rm_write(mod, rm, addr, m.cr[0], 2)
        elif reg == 6:                           # lmsw
            m.write_cr0((m.cr[0] & ~0x0F) | (m.rm_read(mod, rm, addr, 2) & 0x0F))
        return
    if op == 0x00:                               # sldt / str / lldt / ltr / verr / verw
        mod, reg, rm, addr = m.modrm(seg, addr_size)
        if reg == 0:
            m.rm_write(mod, rm, addr, m.ldtr, 2)
        elif reg == 1:
            m.rm_write(mod, rm, addr, m.tr, 2)
        elif reg == 2:
            m.ldtr = m.rm_read(mod, rm, addr, 2) & ~7
        elif reg == 3:
            m.tr = m.rm_read(mod, rm, addr, 2) & ~7
        else:                                    # verr / verw: can this
            selector = m.rm_read(mod, rm, addr, 2)   # selector be used?
            m.set_flag(FLAG_ZF, selector & ~3 != 0)
        return
    if op in (0x20, 0x22):                       # mov r32, cr / mov cr, r32
        mod = m.fetch8()
        reg = (mod >> 3) & 7
        rm = mod & 7
        if op == 0x20:
            m.setreg(rm, m.cr[reg] if reg < 5 else 0, 4)
        else:
            if reg == 0:
                m.write_cr0(m.getreg(rm, 4))
            elif reg < 5:
                m.cr[reg] = m.getreg(rm, 4)
        return
    if 0x80 <= op <= 0x8F:
        rel = m.fetch32() if size == 4 else m.fetch16()
        if branch_condition(m, op & 0x0F):
            m.eip = (m.eip + sext(rel, size * 8)) & m.ipmask
        return
    if 0x90 <= op <= 0x9F:
        mod, reg, rm, addr = m.modrm(seg, addr_size)
        m.rm_write(mod, rm, addr, 1 if branch_condition(m, op & 0x0F) else 0, 1)
        return
    if op in (0xB6, 0xB7, 0xBE, 0xBF):
        mod, reg, rm, addr = m.modrm(seg, addr_size)
        from_size = 1 if op in (0xB6, 0xBE) else 2
        value = m.rm_read(mod, rm, addr, from_size)
        if op in (0xBE, 0xBF):
            value = sext(value, from_size * 8)
        m.setreg(reg, value, size)
        return
    if op == 0xAF:
        mod, reg, rm, addr = m.modrm(seg, addr_size)
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
            m.loadseg(which, m.pop())
        return
    if op in (0xA3, 0xAB, 0xB3, 0xBB):
        # bt / bts / btr / btc, register form: the bit number comes from a
        # register, and everything but bt writes the bit back
        mod, reg, rm, addr = m.modrm(seg, addr_size)
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
    if op == 0xBA:
        # the same four bit instructions with the bit number spelled out: the
        # register field chooses which one, and /4 .. /7 are the only legal
        # values, which is why this is a group rather than a family
        mod, reg, rm, addr = m.modrm(seg, addr_size)
        value = m.rm_read(mod, rm, addr, size)
        index = m.fetch8() % (size * 8)
        m.set_flag(FLAG_CF, bool(value >> index & 1))
        if reg in (5, 6, 7):
            if reg == 5:
                value |= 1 << index
            elif reg == 6:
                value &= ~(1 << index)
            else:
                value ^= 1 << index
            m.rm_write(mod, rm, addr, value & MASK[size], size)
        return
    if op in (0xA4, 0xA5, 0xAC, 0xAD):
        # shld / shrd: a double shift, the second half coming from a register
        mod, reg, rm, addr = m.modrm(seg, addr_size)
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
        mod, reg, rm, addr = m.modrm(seg, addr_size)
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
        mod, reg, rm, addr = m.modrm(seg, addr_size)
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
        mod, reg, rm, addr = m.modrm(seg, addr_size)
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
    if op == 0x31:                               # rdtsc: a Pentium instruction,
        m.ticks += 1                             # kept because a kernel times
        m.setreg(0, m.ticks & 0xFFFF, 2)         # itself with it
        m.setreg(2, (m.ticks >> 16) & 0xFFFF, 2)
        return
    raise NotImplementedError(f"two-byte opcode 0f {op:02x}")
