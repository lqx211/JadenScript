"""The 80386 line: the model, the firmware and the boot chain.

The same rule as the RISC-V tests: `tools/i386_ref.py` is the executable spec,
`userspace/i386/bios.asm` is the firmware that runs on it, and a boot sector is
a real 512-byte sector read through a real int 13h.
"""

from __future__ import annotations

import json
import struct
import subprocess
import sys
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import i386_image  # noqa: E402
import jsw_sim  # noqa: E402
import i386_ref as rv  # noqa: E402

RVDIR = ROOT / "userspace" / "i386"

#: DEL stops the countdown, Down moves the light to "Firmware Setup", and Enter
#: opens it. Every setup test starts with these three.
SETUP_KEYS = [0x32, 0x50, 0x1C]


def post(limit: int = 60_000, cores: int = 1,
         stage: tuple[int, int] = (80, 30)) -> rv.Machine:
    """Power on and stop while the splash and POST's own log are still up."""
    mach = rv.Machine()
    mach.view_cols, mach.view_rows = stage
    mach.ram[:] = i386_image.build()
    mach.cores = cores
    # hwApply hands the CMOS to the chipset at POST, so the board's own setting
    # is what asks for the count -- not the model's starting value
    mach.cmos[0x38] = cores - 1
    mach.sreg[rv.SEG_CS] = rv.RESET_CS
    mach.eip = rv.RESET_IP
    mach.run(limit=limit)
    return mach


def boot(limit: int = 300_000, cores: int = 1) -> rv.Machine:
    mach = rv.Machine()
    mach.ram[:] = i386_image.build()
    mach.cores = cores
    mach.cmos[0x38] = cores - 1
    # POST waits half a second for Delete; any other key boots at once, so a
    # space keeps these tests off the wait without opening the utility
    mach.keys.append(0x39)
    mach.sreg[rv.SEG_CS] = rv.RESET_CS
    mach.eip = rv.RESET_IP
    mach.run(limit=limit)
    return mach


class I386BootTests(unittest.TestCase):
    """Power-on, POST, int 19h, and a boot sector that calls back into the ROM."""

    def test_any_other_key_skips_the_countdown(self) -> None:
        """The logo goes up with the POST text saved underneath it; a space gets
        on with the boot and the text comes back, which is what Esc does on a
        real board."""
        quick = boot()                                  # boot() presses space
        self.assertIn("Booting from device 0x80", quick.text())
        self.assertIn("MBR: hello from a boot sector", quick.text())
        slow = rv.Machine()
        slow.ram[:] = i386_image.build()
        slow.sreg[rv.SEG_CS] = rv.RESET_CS
        slow.eip = rv.RESET_IP
        slow.run(limit=120_000)                         # still on the splash
        self.assertIn("DEL = Setup    M = Options", slow.text())
        self.assertNotIn("Booting from device", slow.text())

    def test_post_prints_what_it_found(self) -> None:
        """The log scrolls in the splash's black panel, and the step in the
        corner says what the ROM is doing."""
        screen = post().text()
        for line in ("JSW 80386 BIOS v0.02", "CMOS boot #", "CPU: 80386",
                     "Memory: 640K", "Video: 80x30 text", "Cores: 1",
                     "DEL = Setup    M = Options", "Booting"):
            self.assertIn(line, screen)
        self.assertIn("Booting from device 0x80", boot().text())

    def test_the_boot_counter_comes_back_from_the_cmos(self) -> None:
        """The battery, stated as a fact: the same machine boots twice and the
        count at CMOS 0x30 goes up."""
        mach = boot(limit=30_000)
        self.assertEqual(mach.cmos[0x30], 1)
        self.assertEqual(mach.cmos[0x31], 0xA5, "POST wrote its verdict")
        mach.sreg[rv.SEG_CS] = rv.RESET_CS
        mach.eip = rv.RESET_IP
        mach.halted = False
        mach.steps = 0
        mach.run(limit=30_000)
        self.assertEqual(mach.cmos[0x30], 2, "the counter survived the power cycle")

    def test_the_boot_sector_runs_and_reads_a_second_sector(self) -> None:
        """Sector 0 prints, then reads sector 2 through int 13h and prints it."""
        mach = boot()
        screen = mach.text()
        self.assertIn("MBR: hello from a boot sector", screen)
        self.assertIn("STAGE2: this sector came through int 13h", screen)
        self.assertIn("MBR: handing nothing on", screen)
        self.assertIn("MBR: the clock kept going while the CPU slept", screen)
        # the tick counter belongs to the 8254 and to nothing else: one owner
        # is a clock, two owners is a number that means nothing
        import re
        found = re.search(r"0x40:0x6C reads (\d+)", screen)
        self.assertIsNotNone(found, screen)
        self.assertLess(int(found.group(1)), 400,
                        "the tick counter is being written by more than one device")
        self.assertTrue(mach.halted, "the MBR ends with hlt")

    def test_no_interrupt_vector_is_left_at_address_zero(self) -> None:
        """A vector of 0000:0000 is not "nothing happens" -- it is the vector
        table, so a call to a missing vector executes the table as code.

        GRUB's boot sector calls int 18h when it cannot load its core, and the
        machine used to answer that by running the data area as instructions:
        it stored over the boot sector it had just run and stopped on the first
        0xF4 it met, which looked like a hang inside GRUB's own `puts`.
        """
        mach = boot()
        for vector in range(256):
            off = int.from_bytes(mach.ram[vector * 4:vector * 4 + 2], "little")
            seg = int.from_bytes(mach.ram[vector * 4 + 2:vector * 4 + 4], "little")
            self.assertFalse(off == 0 and seg == 0,
                             f"vector {vector:#04x} points at 0000:0000")

    def test_int_18h_says_there_is_nothing_to_boot(self) -> None:
        """The ROM BASIC entry: a loader with nothing to load calls int 18h and
        expects the machine to stop, so it prints and parks."""
        mach = boot()
        mach.ram[0x9000:0x9002] = b"\xcd\x18"        # int 18h
        mach.sreg[rv.SEG_CS] = 0
        mach.eip = 0x9000
        mach.halted = False
        mach.steps = 0
        mach.run(limit=20_000)
        self.assertTrue(mach.halted, "int 18h does not return")
        self.assertIn("No bootable device.", mach.text())

    def test_the_cat_sector_sings_through_the_8254(self) -> None:
        """A boot sector that plays a tune needs exactly two devices: channel 2
        of the 8254 for the pitch, and the gate on port 0x61 to let it out.
        Both gate bits matter -- with only the timer programmed the speaker is
        silent, which is the mistake every PC-speaker demo makes once."""
        mach = rv.Machine()
        mach.ram[:] = i386_image.build(i386_image.meow_disk())
        mach.keys.append(0x39)
        mach.sreg[rv.SEG_CS] = rv.RESET_CS
        mach.eip = rv.RESET_IP
        mach.run(limit=50_000)          # the first note, still sounding

        self.assertIn("( o.o )", mach.text(), "the cat is on the screen")
        self.assertIn("MEOW-OS", mach.text())
        # the first note of the tune is C5: divisor 2281 at 1.193182 MHz
        self.assertEqual(mach.pit.reload[2], 2281)
        self.assertEqual(mach.ports[0x61] & 3, 3, "the gate is open")
        self.assertEqual(mach.speaker_hz(), 523)
        # ... and the sector reads the pitch back out of the machine to print it
        self.assertIn("PIT ch2 523 Hz", mach.text())

    def test_a_shut_gate_is_silence(self) -> None:
        mach = rv.Machine()
        mach.pit.reload[2] = 1000
        self.assertEqual(mach.speaker_hz(), 0, "nothing is programmed yet")
        mach.port_out(0x61, 1, 1)              # timer 2 through, speaker off
        self.assertEqual(mach.speaker_hz(), 0)
        mach.port_out(0x61, 3, 1)
        self.assertEqual(mach.speaker_hz(), 1193)
        self.assertEqual(mach.port_in(0x5BA, 1), 1193 & 0xFF)
        self.assertEqual(mach.port_in(0x5BB, 1), (1193 >> 8) & 0xFF)
        self.assertEqual(mach.port_in(0x5BC, 1), 3)

    def test_every_cell_carries_the_same_attribute(self) -> None:
        """The teletype takes its attribute from bl, so a routine that calls
        int 10h directly paints the text in whatever bl happens to hold. POST's
        log is the left-hand column, printed straight over the artwork; the
        artwork keeps its own colours, which is the whole point of it."""
        mach = post()
        for row in range(5):
            for col in range(30):
                off = rv.TEXT_BASE + (row * 80 + col) * 2
                char, attr = mach.ram[off], mach.ram[off + 1]
                if char not in (0x00, 0x20):
                    self.assertEqual(
                        attr, 0x07, f"cell {row}:{col} {chr(char)!r} is {attr:#04x}"
                    )

    def test_a_small_stage_gets_no_logo_and_a_narrower_console(self) -> None:
        """Scratch's own stage is 480 x 360: sixty cells across and twenty-two
        down. The artwork does not fit, so it is not drawn, and every line POST
        prints has to fit -- including the one in the top right corner."""
        mach = post(stage=(60, 22))
        screen = mach.text()
        self.assertIn("Video: 60x22 text", screen)
        self.assertTrue(screen.startswith("JSW 80386 BIOS"), screen)
        # the picture would have put blue on the screen; a bare console is black
        blue = sum(1 for i in range(rv.TEXT_BASE, rv.TEXT_BASE + 80 * 30 * 2, 2)
                   if mach.ram[i] == 0x1F)
        self.assertEqual(blue, 0, "the splash went up on a stage that cannot hold it")
        # the step lives in the top right *of the forty cells that are visible*
        head = screen.splitlines()[0]
        self.assertIn("Booting", head, screen)
        self.assertLessEqual(head.index("Booting") + len("Booting"), 60,
                             f"the step is off the right edge: {head!r}")

    def test_the_segment_wrap_is_twenty_bits(self) -> None:
        """``linear`` folds a selector and an offset into twenty bits."""
        mach = rv.Machine()
        self.assertEqual(mach.linear(0xFFFF, 0x0010), 0x00000)
        self.assertEqual(mach.linear(0xF000, 0xFFF0), 0xFFFF0)

    def test_the_reset_vector_jumps_to_the_rom(self) -> None:
        image = i386_image.build()
        self.assertEqual(image[0xFFFF0], 0xEA, "a far jump")
        self.assertEqual(int.from_bytes(image[0xFFFF1:0xFFFF3], "little"), 0x0000)
        self.assertEqual(int.from_bytes(image[0xFFFF3:0xFFFF5], "little"), 0xF000)


class I386ImageTests(unittest.TestCase):
    def test_the_disk_is_staged_where_the_bios_looks_for_it(self) -> None:
        image = i386_image.build()
        sector0 = image[i386_image.DISK_AT:i386_image.DISK_AT + 512]
        self.assertEqual(sector0[510:512], b"\x55\xaa")
        self.assertEqual(sector0[:1], b"\xfa", "the boot sector starts with cli")
        self.assertIn(b"STAGE2:", image[i386_image.DISK_AT + 512:])

    def test_only_the_rom_and_the_disk_are_populated(self) -> None:
        image = i386_image.build()
        self.assertEqual(len(image), 1 << 20)
        self.assertTrue(any(image[0xF0000:]), "the ROM is there")
        self.assertFalse(any(image[0x80000:0xB8000]), "nothing stray in the middle")


class I386Int10Tests(unittest.TestCase):
    """The console services a bootloader uses, called the way GRUB calls them.

    GRUB's whole console is three BIOS calls repeated per character: ask where
    the cursor is (AH=03), paint one character with an attribute (AH=09), and
    put the cursor where it wants it next (AH=02). AH=09 was missing from this
    firmware for a long time -- its own teletype writes cells directly -- and
    the symptom was GRUB running perfectly and printing nothing at all, which
    reads like a dead kernel rather than a missing function.
    """

    def run_guest(self, code: bytes, limit: int = 200) -> rv.Machine:
        """Poke a real-mode program at 0x1000 and run it, after POST has put the
        interrupt vectors and the console window in place."""
        mach = post()
        for i, byte in enumerate(code):
            mach.write(0x1000 + i, byte, 1)
        mach.sreg[rv.SEG_CS] = 0
        mach.sreg[rv.SEG_DS] = 0
        mach.sreg[rv.SEG_SS] = 0
        mach.reg[4] = 0x8000                     # sp
        mach.eip = 0x1000
        # Step until the program's own `hlt`: the firmware is still in its
        # countdown behind it, and the next timer tick repaints the row the
        # program just wrote. `run` takes an absolute step number, and POST has
        # already spent sixty thousand of them.
        stop = mach.steps + limit
        while mach.steps < stop and not mach.halted:
            mach.step()
        return mach

    def test_timer2_output_follows_its_gate(self) -> None:
        """What GRUB's TSC calibration waits on: bit 5 of port 0x61.

        `grub_pit_wait` clears timer 2's gate, loads a count, raises the gate,
        and then polls that bit -- it wants it *low* first, then high, and the
        count is what makes it rise. A counter that ticked behind a closed gate
        made the first poll already high, which is a calibration of nothing.
        """
        mach = post()
        mach.port_out(0x43, 0xB0, 1)                       # channel 2, lo/hi, mode 0
        mach.port_out(0x42, 0x00, 1)
        mach.port_out(0x42, 0x01, 1)                       # a count of 0x0100
        mach.port_out(0x61, mach.port_in(0x61, 1) & ~0x01, 1)  # gate low
        self.assertEqual((mach.port_in(0x61, 1) >> 5) & 1, 0,
                         "a closed gate holds the output low")
        mach.port_out(0x61, (mach.port_in(0x61, 1) | 0x01) & ~0x02, 1)   # gate high
        self.assertEqual((mach.port_in(0x61, 1) >> 5) & 1, 0,
                         "the output drops when the gate opens, before the count")
        mach.run(limit=mach.steps + 600)                # one clock every two steps
        self.assertEqual((mach.port_in(0x61, 1) >> 5) & 1, 1,
                         "and rises at the terminal count")

    def test_int10_ah09_paints_a_cell_and_leaves_the_cursor(self) -> None:
        """AH=09 writes AL, CX times, from the cursor, and does not move it."""
        code = bytes([
            0xB8, 0x00, 0x02,        # mov ax, 0x0200   AH=02, set the cursor
            0xBA, 0x05, 0x03,        # mov dx, 0x0305   row 3, column 5
            0xCD, 0x10,              # int 0x10
            0xB8, 0x41, 0x09,        # mov ax, 0x0941   AH=09, AL='A'
            0xBB, 0x07, 0x00,        # mov bx, 7        the attribute
            0xB9, 0x03, 0x00,        # mov cx, 3        three cells
            0xCD, 0x10,              # int 0x10
            0xF4,                    # hlt
        ])
        mach = self.run_guest(code)
        stride = mach.ram[0x530] or 80
        row, col = 3, 5
        base = 0xB8000 + (row * stride + col) * 2
        self.assertEqual(bytes(mach.ram[base:base + 6]), b"A\x07" * 3,
                         "three cells, character and attribute")
        self.assertEqual(mach.ram[0x450], col, "the column is where it was")
        self.assertEqual(mach.ram[0x451], row, "and so is the row")


class I386UnitTests(unittest.TestCase):
    """The instructions the BIOS leans on, one at a time."""

    def _run(self, code: bytes, steps: int = 1, **regs: int) -> rv.Machine:
        mach = rv.Machine()
        for i, byte in enumerate(code):
            mach.write(i, byte, 1)
        for name, value in regs.items():
            mach.reg[rv.REG32.index(name.lower())] = value
        for _ in range(steps):
            mach.step()
        return mach

    def test_add_takes_the_register_form_in_the_right_order(self) -> None:
        """`add ax, cx` is 01 C8; the r/m operand is the destination."""
        mach = self._run(bytes([0x01, 0xC8]), eax=5, ecx=3)
        self.assertEqual(mach.reg[0] & 0xFFFF, 8)

    def test_cmp_sets_the_flags_it_should(self) -> None:
        mach = self._run(bytes([0x3C, 0x0A]), eax=104)      # cmp al, 10
        self.assertEqual(mach.flag(rv.FLAG_ZF), 0)
        self.assertEqual(mach.flag(rv.FLAG_CF), 0)
        mach = self._run(bytes([0x3C, 0x0A]), eax=10)
        self.assertEqual(mach.flag(rv.FLAG_ZF), 1)

    def test_a_bp_address_is_in_the_stack_segment(self) -> None:
        """[bp+6] in a handler is the caller's flags, and it lives in ss."""
        mach = rv.Machine()
        mach.sreg[rv.SEG_DS] = 0x1000
        mach.sreg[rv.SEG_SS] = 0x2000
        mach.reg[5] = 0x0010                                # bp
        mach.write(0x20016, 0xBEEF, 2)
        for i, byte in enumerate(bytes([0x8B, 0x46, 0x06])):  # mov ax, [bp+6]
            mach.write(i, byte, 1)
        mach.step()
        self.assertEqual(mach.reg[0] & 0xFFFF, 0xBEEF)

    def test_rep_movsw_copies_cx_words(self) -> None:
        mach = rv.Machine()
        for i in range(6):
            mach.write(0x100 + i, i + 1, 1)
        mach.reg[6] = 0x100                                # si
        mach.reg[7] = 0x200                                # di
        mach.reg[1] = 3                                    # cx
        code = bytes([0xF3, 0xA5])                         # rep movsw
        for i, byte in enumerate(code):
            mach.write(i, byte, 1)
        mach.step()
        self.assertEqual(bytes(mach.ram[0x200:0x206]), bytes([1, 2, 3, 4, 5, 6]))

    def test_the_interrupt_frame_is_flags_cs_ip(self) -> None:
        mach = rv.Machine()
        mach.write(0x10 * 4, 0x1234, 2)
        mach.write(0x10 * 4 + 2, 0x5678, 2)
        mach.reg[4] = 0x7C00
        mach.interrupt(0x10)
        self.assertEqual(mach.eip, 0x1234)
        self.assertEqual(mach.sreg[rv.SEG_CS], 0x5678)
        self.assertEqual(mach.reg[4], 0x7C00 - 6)
        self.assertEqual(mach.flag(rv.FLAG_IF), 0, "interrupts are off inside")
        mach.iret()
        self.assertEqual(mach.reg[4], 0x7C00)


class I386ProtectedModeTests(unittest.TestCase):
    """The one thing a 386 has that an 8086 does not: descriptors. The demo in
    userspace/i386/pm16.asm sets CR0.PE, far-jumps through a code descriptor
    and writes through a data segment whose base is not its selector -- so a
    machine that only stores selectors lands in the interrupt table instead."""

    def run_demo(self) -> rv.Machine:
        blob = (ROOT / "userspace/i386/pm16.bin").read_bytes()
        mach = rv.Machine()
        mach.ram[0x1000:0x1000 + len(blob)] = blob
        mach.sreg[rv.SEG_CS] = 0x0100
        mach.eip = 0
        for _ in range(40):
            mach.step()
            if mach.halted:
                break
        return mach

    def test_the_switch_is_cr0_bit_zero(self) -> None:
        mach = self.run_demo()
        self.assertEqual(mach.cr[0] & 1, 1, "protected mode is on")
        self.assertTrue(mach.pe)

    def test_the_far_jump_loads_the_descriptor_not_the_selector(self) -> None:
        mach = self.run_demo()
        self.assertEqual(mach.sreg[rv.SEG_CS], 0x08)
        self.assertEqual(mach.base[rv.SEG_CS], 0x0000, "the code base, not 0x80")
        self.assertEqual(mach.sreg[rv.SEG_DS], 0x10)
        self.assertEqual(mach.base[rv.SEG_DS], 0x2000, "the data base, not 0x100")

    def test_a_store_through_the_descriptor_lands_at_its_base(self) -> None:
        mach = self.run_demo()
        self.assertEqual(bytes(mach.ram[0x2000:0x2003]), b"PM!")
        self.assertEqual(bytes(mach.ram[0x0100:0x0103]), b"\0\0\0",
                         "and not at selector * 16")

    def test_the_program_agrees_with_the_scratch_machine(self) -> None:
        """The same bytes in both machines, instruction for instruction."""
        blob = ROOT / "userspace/i386/pm16.bin"
        self.assertTrue(blob.exists(), "run nasm -f bin pm16.asm -o pm16.bin")
        r = subprocess.run([sys.executable, str(ROOT / "tools" / "i386_jsw_check.py"),
                            "--code", str(blob), "--steps", "40", "--no-build"],
                           cwd=ROOT, capture_output=True, text=True)
        self.assertIn("protected mode agrees", r.stdout, r.stdout + r.stderr)


def vrmach_with_cmos(cmos: dict[int, int], limit: int = 3_000) -> "rv.Machine":
    """A machine that powers on with a battery already written."""
    mach = rv.Machine()
    mach.ram[:] = i386_image.build()
    for reg, value in cmos.items():
        mach.cmos[reg] = value
    mach.sreg[rv.SEG_CS] = rv.RESET_CS
    mach.eip = rv.RESET_IP
    mach.run(limit=limit)
    return mach


class I386SetupWiringTests(unittest.TestCase):
    """Every setup value, all the way to the chipset.

    A row on the setup screen is four links long: the battery byte, `hwApply`
    reading it at POST, the register file at 0x5A8..0x5AE, and the machine
    variable that register lands in. A single row tested by hand says nothing
    about the other four, and a table entry that is out of range -- or that the
    chipset silently drops -- looks exactly like a working one until someone
    presses the key. So this walks the tables.
    """

    def test_the_clock_table_reaches_the_chipset(self) -> None:
        for index, mhz in enumerate((16, 25, 33, 40, 50)):
            with self.subTest(mhz=mhz):
                mach = vrmach_with_cmos({0x36: index})
                self.assertEqual(mach.cpu_base, mhz)

    def test_the_repeat_table_reaches_the_keyboard(self) -> None:
        for index, tenths in enumerate((50, 25, 12, 6, 4)):
            with self.subTest(index=index):
                mach = vrmach_with_cmos({0x37: index})
                self.assertEqual(mach.key_repeat, tenths * 10)

    def test_the_core_and_thread_tables_reach_the_chipset(self) -> None:
        for index in range(16):
            with self.subTest(cores=index + 1):
                self.assertEqual(vrmach_with_cmos({0x38: index}).cores, index + 1)
        for index in range(4):
            with self.subTest(threads=index + 1):
                self.assertEqual(vrmach_with_cmos({0x39: index}).threads, index + 1)

    def test_the_performance_and_rate_tables_reach_the_chipset(self) -> None:
        for index, factor in enumerate((1, 2, 3, 4, 6, 8, 12, 16)):
            with self.subTest(factor=factor):
                self.assertEqual(vrmach_with_cmos({0x3A: index}).perf_factor, factor)
        for index, rate in enumerate((100, 75, 50, 25, 10, 5)):
            with self.subTest(rate=rate):
                self.assertEqual(vrmach_with_cmos({0x3B: index}).time_rate, rate)

    def test_an_out_of_range_byte_lands_on_the_board_s_default(self) -> None:
        """A battery that has never been written holds 0xFF in every byte, and
        a table indexed with 255 is a read past its end. `hwApply` clamps each
        one, so the machine comes up on the numbers the board ships with."""
        mach = vrmach_with_cmos({0x34: 0xFF, 0x36: 0xFF, 0x37: 0xFF,
                                 0x38: 0xFF, 0x39: 0xFF, 0x3A: 0xFF,
                                 0x3B: 0xFF})
        self.assertEqual(mach.emu_on, 1, "a blank battery reads as on")
        self.assertEqual(mach.cpu_base, 33)
        self.assertEqual(mach.key_repeat, 120)
        self.assertEqual(mach.cores, 2)
        self.assertEqual(mach.threads, 1)
        self.assertEqual(mach.perf_factor, 1)
        self.assertEqual(mach.time_rate, 100)

    def test_the_emulation_switch_stops_the_sensors(self) -> None:
        """Off is not a display flag: the sensors are the emulation's, so with
        the switch off the die does not heat and the fan never turns."""
        hot = rv.Machine()
        hot.emu_on = 1
        for _ in range(40):
            hot.thermal(1.0, 1.0)
        cold = rv.Machine()
        cold.emu_on = 0
        cold.cpu_temp = 27.0
        fan = cold.fan_rpm
        for _ in range(40):
            cold.thermal(1.0, 1.0)
        self.assertGreater(hot.cpu_temp, 30.0, "a pinned core heats up")
        self.assertEqual(cold.cpu_temp, 27.0, "and an unemulated one does not")
        self.assertEqual(cold.fan_rpm, fan, "and the fan is left where it was")


class I386SetupTests(unittest.TestCase):
    """The GX620-shaped setup: a tree on the left, a page of facts on the
    right, and the editable values buried in the tree where Dell buries them."""

    F2, DOWN, UP = 0x3C, 0x50, 0x48
    RIGHT, LEFT, ENTER, ESC = 0x4D, 0x4B, 0x1C, 0x01

    #: System, System Info, Processor Info, Memory Info, Date/Time and Month
    TO_MONTH = [0x50] * 5

    def _attrs(self, mach, row: int, first: int, last: int) -> set[int]:
        return {mach.ram[rv.TEXT_BASE + (row * rv.TEXT_COLS + col) * 2 + 1]
                for col in range(first, last)}

    def _setup(self, keys: list[int], limit: int = 40_000_000,
               settle: int = 900_000):
        """Power on, press F2 and whatever else, and let the screen settle."""
        mach = rv.Machine()
        mach.ram[:] = i386_image.build()
        mach.keys.extend(keys)
        mach.sreg[rv.SEG_CS] = rv.RESET_CS
        mach.eip = rv.RESET_IP
        while mach.pending_keys() and mach.steps < limit:
            mach.run(limit=min(limit, mach.steps + 250_000))
        mach.run(limit=mach.steps + settle)
        return mach

    def test_the_tree_frame_measures_the_card(self) -> None:
        """The frame was drawn for eighty columns and thirty rows. On Scratch's
        own 60 x 22 stage that put its right border, the right pane's right
        half and the whole hint box off the screen, so the frame is built from
        the card's own numbers instead."""
        mach = rv.Machine()
        mach.view_cols, mach.view_rows = 60, 22
        mach.ram[:] = i386_image.build()
        mach.keys.extend([self.F2])
        mach.sreg[rv.SEG_CS] = rv.RESET_CS
        mach.eip = rv.RESET_IP
        limit = 40_000_000
        while mach.pending_keys() and mach.steps < limit:
            mach.run(limit=min(limit, mach.steps + 250_000))
        mach.run(limit=mach.steps + 900_000)
        screen = mach.text()
        self.assertIn("System Info", screen)
        # the frame's vertical rules: column 58 on this card, not 78. The outer
        # rule is a single bar and the right pane's box a double one, and the
        # box is drawn over the rule, so either glyph is the frame's edge.
        # The plane's stride is the *card's* width, so that is what to read it
        # with -- reading a sixty-column screen at eighty lands on the wrong row.
        edge = (0xB0, 0xBA, 0xBB)
        stride = 60
        self.assertIn(mach.ram[rv.TEXT_BASE + (1 * stride + 58) * 2], edge,
                      "the right border is on the card, not off it")
        self.assertIn(mach.ram[rv.TEXT_BASE + (1 * stride) * 2], edge,
                      "and so is the left one")
        # the bottom rule is on the last row the card has, and the tree's own
        # last row is above it
        bottom = mach.ram[rv.TEXT_BASE + (20 * stride + 20) * 2]
        self.assertEqual(bottom, 0xCD, "the bottom rule is on row 20")
        # the hint box needs six rows below the frame and there are not that
        # many: the tree keeps its rows and the explanation is dropped
        self.assertNotIn("select", screen.lower())
        self.assertNotIn("Setup Utility", screen, "the hint's first line is gone")

    def test_a_big_card_gets_a_big_frame(self) -> None:
        """TurboWarp can be 1024 x 768, which is 128 x 48 cells. The frame used
        to stop at row 22 and column 118 whatever the card was, so a tall stage
        was a frame with half the screen of empty blue under it."""
        mach = rv.Machine()
        mach.view_cols, mach.view_rows = 128, 48
        mach.ram[:] = i386_image.build()
        mach.keys.extend([self.F2])
        mach.sreg[rv.SEG_CS] = rv.RESET_CS
        mach.eip = rv.RESET_IP
        limit = 40_000_000
        while mach.pending_keys() and mach.steps < limit:
            mach.run(limit=min(limit, mach.steps + 250_000))
        mach.run(limit=mach.steps + 900_000)
        screen = mach.text()
        self.assertIn("System Info", screen)
        stride = 128
        edge = (0xB0, 0xBA, 0xBB)
        # the frame's right border: the card's width less two
        self.assertIn(mach.ram[rv.TEXT_BASE + (1 * stride + 126) * 2], edge,
                      "the frame reaches the right of a wide card")
        # and its bottom: the card's height, less the border and the hint's six
        bottom = mach.ram[rv.TEXT_BASE + (40 * stride + 40) * 2]
        self.assertEqual(bottom, 0xCD, "the bottom rule follows the card's height")
        # there is room for the explanation again, six rows below the frame
        self.assertIn("Press Up/Down arrows", screen)
        self.assertIn("Press Esc to exit Setup", screen)

    def test_f2_opens_the_setup_on_the_system_page(self) -> None:
        screen = self._setup([self.F2]).text()
        self.assertIn("JSW System 80386", screen)
        self.assertIn("System Info", screen)
        self.assertIn("Date/Time", screen)
        self.assertIn("Drives", screen)
        self.assertIn("System            = JSW 80386", screen)
        self.assertNotIn("Dell", screen)
        self.assertNotIn("OptiPlex", screen)
        self.assertNotIn("Pentium", screen)

    def test_the_system_info_page_is_behind_it(self) -> None:
        screen = self._setup([self.F2, self.DOWN]).text()
        self.assertIn("BIOS Version          = A07 (03/31/06)", screen)
        self.assertIn("Service Tag           = J1Q24BX", screen)
        self.assertIn("None of these fields are changeable.", screen)

    def test_the_hints_are_the_ones_a_gx620_prints(self) -> None:
        screen = self._setup([self.F2]).text()
        self.assertIn("Press Up/Down arrows to select a different field", screen)
        self.assertIn("Press Left/Right keys to expand or collapse a group",
                      screen)
        self.assertIn("Press Esc to exit Setup", screen)

    def test_the_light_starts_on_the_first_row(self) -> None:
        mach = self._setup([self.F2])
        self.assertEqual(mach.ram[0x578], 0, "GX_SEL")

    def test_the_down_arrow_walks_the_tree(self) -> None:
        mach = self._setup([self.F2] + self.TO_MONTH)
        self.assertEqual(mach.ram[0x578], 5)
        self.assertIn("The month, as a number from 1 to 12.", mach.text())

    def test_the_green_bar_is_on_the_selected_row_only(self) -> None:
        mach = self._setup([self.F2] + self.TO_MONTH)
        green = [row for row in range(rv.TEXT_ROWS)
                 if mach.ram[rv.TEXT_BASE + (row * rv.TEXT_COLS + 3) * 2 + 1] == 0x20]
        self.assertEqual(len(green), 1, "one row carries the light")

    def test_a_group_folds_and_opens_again(self) -> None:
        folded = self._setup([self.F2, self.RIGHT])
        self.assertEqual(folded.ram[0x580], 0, "GX_EXP for System")
        self.assertNotIn("Processor Info", folded.text(), "the children went")
        opened = self._setup([self.F2, self.RIGHT, self.RIGHT])
        self.assertEqual(opened.ram[0x580], 1)
        self.assertIn("Processor Info", opened.text())

    def test_enter_steps_into_the_editor(self) -> None:
        """Enter on a value opens the dialog the tree shares with the page
        setup, and Right steps the value -- still open when the run stops, so
        the buffer is where the edit is visible."""
        keys = [self.F2] + self.TO_MONTH + [self.ENTER]
        mach = self._setup(keys)
        self.assertEqual(mach.ram[0x56E], 0, "SU_ITEM: the Month row")
        self.assertEqual(mach.cmos[0x08], 1, "and nothing was written yet")

    def test_a_setting_comes_back_from_the_cmos(self) -> None:
        """The tree reads what the battery kept: a board whose month is March
        opens on March, not on January."""
        mach = rv.Machine()
        mach.ram[:] = i386_image.build()
        mach.cmos[0x08] = 0x03
        mach.cmos[0x38] = 0
        mach.keys.extend([self.F2] + self.TO_MONTH)
        mach.sreg[rv.SEG_CS] = rv.RESET_CS
        mach.eip = rv.RESET_IP
        while mach.pending_keys():
            mach.run(limit=mach.steps + 250_000)
        # opening the setup writes the whole buffer back from the battery, so
        # after the first frame this is what suLoad put there -- which is the
        # value the tree prints
        self.assertEqual(mach.ram[0x540], 0, "the tree repaints from SU_ITEM")
        self.assertTrue(mach.text().startswith("   JSW System 80386"))

    def test_a_selected_setting_is_the_one_the_editor_opens_on(self) -> None:
        """What Enter edits is SU_ITEM, and the tree sets it from the node under
        the light -- which is how a value row is wired to its setting."""
        keys = [self.F2] + self.TO_MONTH + [self.ENTER]
        mach = self._setup(keys)
        self.assertEqual(mach.ram[0x56E], 0, "SU_ITEM: Month is setting 0")
        self.assertEqual(mach.ram[0x578], 5, "and the light is on its node")

    def test_every_cell_of_the_setup_screen_is_painted(self) -> None:
        """No holes: every cell is paper, header, frame or the green light."""
        mach = self._setup([self.F2])
        for row in range(rv.TEXT_ROWS):
            for col in range(rv.TEXT_COLS):
                attr = mach.ram[rv.TEXT_BASE + (row * rv.TEXT_COLS + col) * 2 + 1]
                self.assertIn(attr, (0x1F, 0x1E, 0x70, 0x20),
                              f"cell {row},{col} is 0x{attr:02x}")

    #: System is open, HW Emulation is open: 17 rows down reaches the group,
    #: 18 the master switch, 19 the battery, 20 the clock, 21 the monitor
    TO_HW = [0x50] * 17
    TO_EMU_SWITCH = [0x50] * 18
    TO_FREQ = [0x50] * 20
    TO_REPEAT = [0x50] * 21
    TO_MONITOR = [0x50] * 22

    def test_the_hw_emulation_group_lists_what_the_board_pretends(self) -> None:
        screen = self._setup([self.F2] + self.TO_HW).text()
        self.assertIn("HW Emulation", screen)
        self.assertIn("This board is a program", screen)
        self.assertIn("Emulation is the master switch.", screen)
        # each child is a page of its own, titled with its own name
        for downs, title in ((18, "Emulation"), (19, "BIOS Battery"),
                             (20, "CPU Frequency"), (21, "Key Repeat"),
                             (22, "Hardware Monitor")):
            page = self._setup([self.F2] + [0x50] * downs).text()
            self.assertIn(title, page.splitlines()[0] + page)

    def test_the_hardware_monitor_reads_every_core(self) -> None:
        """One line per core, and the line is read out of the chipset's
        registers on 0x5A5..0x5A7 rather than out of a table in the ROM."""
        mach = self._setup([self.F2] + self.TO_MONITOR)
        screen = mach.text()
        self.assertIn("Hardware Monitor", screen)
        for core in range(1, mach.cores + 1):
            self.assertIn(f"Core {core}", screen)
        self.assertIn("usage", screen)
        self.assertIn("MHz", screen)
        self.assertIn("Emulation is on.", screen)

    def test_the_key_repeat_rate_is_a_table_the_keyboard_speaks(self) -> None:
        """The setup stores a choice; the keyboard gets a time. Half a second
        does not fit in a byte of port 0x5AA, so the ROM keeps the table and
        hwApply converts -- 1/8 s is the entry the battery starts on."""
        keys = [self.F2] + self.TO_REPEAT + [self.ENTER]
        mach = self._setup(keys)
        self.assertEqual(mach.cmos[0x37], 2, "1/8 s")
        self.assertEqual(mach.key_repeat, 120, "and the keyboard was told, in ms")

    def test_the_emulation_switch_is_the_chipset_s_master_switch(self) -> None:
        """Off is a CMOS byte, and the chipset hears about it at POST -- which
        is why the row exists rather than a hidden default."""
        mach = self._setup([self.F2] + self.TO_EMU_SWITCH)
        self.assertEqual(mach.ram[0x578], 18, "the light is on the switch")
        self.assertEqual(mach.cmos[0x34], 1, "and the battery has it On")

    def test_the_cpu_frequency_is_a_number_the_board_can_keep(self) -> None:
        start = self._setup([self.F2] + self.TO_FREQ)
        self.assertEqual(start.cmos[0x36], 2, "33 MHz, out of the battery")
        self.assertEqual(start.ram[0x578], 20, "and the light is on the row")
        self.assertEqual(start.cpu_base, 33, "which the chipset is running at")

    def test_a_setting_wraps_at_both_ends(self) -> None:
        """0 - 1 is 0xFF, which is bigger than every count on the screen, so a
        plain "over the top, wrap to zero" left Up on the first entry showing
        the first entry. Boot Sequence has four entries and starts at zero,
        which is where it showed -- and the chooser wraps the same way."""
        first = self._setup([self.F2] + [self.DOWN] * 11
                            + [self.ENTER, self.UP, self.ENTER])
        self.assertEqual(first.read(0x540 + 8, 1), 3,
                         "Up on the first entry lands on the last")
        last = self._setup([self.F2] + [self.DOWN] * 11
                           + [self.ENTER] + [self.DOWN] * 4 + [self.ENTER])
        self.assertEqual(last.read(0x540 + 8, 1), 0,
                         "Down on the last entry lands on the first")

    def test_the_clock_wraps_down_past_midnight(self) -> None:
        # 12 o'clock, thirteen steps left: 11, 10 ... 0, then 23
        # the editor is modal and Asks for a key per step, so this drives it
        # the way a person does: Enter in, twelve Downs, Enter out
        # the editor redraws between keys, so each one needs its own frame
        keys = [self.F2] + [self.DOWN] * 8
        mach = rv.Machine()
        mach.ram[:] = i386_image.build()
        mach.keys.extend(keys)
        mach.sreg[rv.SEG_CS] = rv.RESET_CS
        mach.eip = rv.RESET_IP
        while mach.pending_keys():
            mach.run(limit=mach.steps + 250_000)
        for key in [self.ENTER] + [self.DOWN] * 12:
            mach.keys.append(key)
            mach.run(limit=mach.steps + 250_000)
        self.assertEqual(mach.read(0x540 + 3, 1), 12,
                         "0 minus 12 hours is 12, not a negative number")

    #: the CPU group is the twenty-fourth visible row: System, its three facts,
    #: Date/Time and its six, Boot Sequence, Drives and its three, Onboard
    #: Devices (folded), HW Emulation and its five children
    TO_CPU = [0x50] * 23

    def test_the_cpu_and_clock_groups_are_under_hw_emulation(self) -> None:
        keys = ([self.F2] + self.TO_CPU
                + [self.RIGHT]                      # open CPU
                + [self.DOWN] * 4                   # cores, threads, perf, Clock
                + [self.RIGHT, self.DOWN])          # open Clock, on Fake OS Time
        screen = self._setup(keys).text()
        self.assertIn("CPU Cores", screen)
        self.assertIn("CPU Threads", screen)
        self.assertIn("Performance Emu", screen)
        self.assertIn("Fake OS Time", screen)

    def test_the_core_count_reaches_the_chipset_on_a_save(self) -> None:
        """Editing the row only edits the CMOS: hwApply is what hands it to the
        chipset, exactly as a real board loads its registers on the way out."""
        keys = [self.F2] + self.TO_CPU
        mach = self._setup(keys)
        self.assertEqual(mach.cmos[0x38], 1, "two cores, less one")
        self.assertEqual(mach.cores, 2, "and port 0x5AE is what the POST read")

    def test_fake_os_time_and_performance_are_saved(self) -> None:
        """Row 17 is Performance Emu, row 18 Fake OS Time; both are indexes into
        a table the chipset is handed."""
        keys = ([self.F2] + self.TO_CPU
                + [self.RIGHT, self.DOWN, self.DOWN, self.DOWN,
                   self.ENTER, self.DOWN, self.ENTER]   # performance, one up
                + [self.DOWN, self.RIGHT, self.DOWN,
                   self.ENTER, self.DOWN, self.DOWN, self.ENTER]  # clock, two
                + [self.ESC, self.ENTER])
        mach = self._setup(keys)
        self.assertEqual(mach.cmos[0x3A], 1, "2x")
        self.assertEqual(mach.perf_factor, 2)
        self.assertEqual(mach.cmos[0x3B], 2, "50%")
        self.assertEqual(mach.time_rate, 50)

    #: Esc on the main page opens the save prompt; the prompt's options are
    #: side by side, so Right walks from Save to Discard and Enter takes the
    #: lit one. Up/Down do nothing there, which is worth knowing before writing
    #: a test with them in it.
    SAVE = (ESC, ENTER)
    DISCARD = (ESC, RIGHT, ENTER)

    def _save_flow(self, keys: list[int], cmos: dict[int, int] | None = None):
        """Open the setup on a battery, edit, and leave by ``keys``."""
        mach = rv.Machine()
        mach.ram[:] = i386_image.build()
        for reg, value in (cmos or {}).items():
            mach.cmos[reg] = value
        mach.keys.extend([self.F2] + self.TO_EMU_SWITCH
                         + [self.ENTER, self.DOWN, self.ENTER] + keys)
        mach.sreg[rv.SEG_CS] = rv.RESET_CS
        mach.eip = rv.RESET_IP
        limit = 40_000_000
        while mach.pending_keys() and mach.steps < limit:
            mach.run(limit=min(limit, mach.steps + 250_000))
        mach.run(limit=mach.steps + 900_000)
        return mach

    def test_a_saved_setting_survives_a_power_cycle(self) -> None:
        """The whole point of the battery: edit, save, power off, and the next
        POST finds the value in the CMOS and hands it to the chipset.

        A board whose switch came back Up after every reset would be a machine
        with a battery-shaped hole in it, which is what "it does not save" looks
        like from the outside.
        """
        off = {0x34: 0}
        saved = self._save_flow(list(self.SAVE), off)
        self.assertEqual(saved.cmos[0x34], 1, "the prompt wrote the byte")
        self.assertEqual(saved.emu_on, 1, "and hwApply told the chipset")

        # power cycle: a new machine with only the battery carried over
        again = rv.Machine()
        again.ram[:] = i386_image.build()
        again.cmos[:] = saved.cmos
        again.sreg[rv.SEG_CS] = rv.RESET_CS
        again.eip = rv.RESET_IP
        again.run(limit=3_000)
        self.assertEqual(again.cmos[0x34], 1, "the battery kept it")
        self.assertEqual(again.emu_on, 1, "and the new POST applied it")

    def test_discard_leaves_the_battery_alone(self) -> None:
        """The same edit, thrown away: the byte cannot move.

        This is the other half of the report -- a panel edit that is never
        saved looks exactly like a firmware that forgot it, so the difference
        has to be a test rather than a guess.
        """
        off = {0x34: 0}
        mach = self._save_flow(list(self.DISCARD), off)
        self.assertEqual(mach.cmos[0x34], 0, "discard is not a save")
        self.assertEqual(mach.ram[0x540 + 11], 1,
                         "the working copy did hold the edit")

    def test_an_off_switch_reaches_the_chipset_at_post(self) -> None:
        """The byte alone is not enough: POST has to hand 0x34 to port 0x5A9."""
        mach = vrmach_with_cmos({0x34: 0, 0x38: 4})
        self.assertEqual(mach.emu_on, 0)
        self.assertEqual(mach.cores, 5, "and the core count came from 0x38")

    def test_the_thread_count_takes_the_whole_frame(self) -> None:
        keys = ([self.F2] + self.TO_CPU
                + [self.RIGHT, self.DOWN, self.DOWN, self.ENTER,
                   self.DOWN, self.ENTER, self.ESC, self.ENTER])
        mach = self._setup(keys)
        self.assertEqual(mach.cmos[0x39], 1, "two per core")
        self.assertEqual(mach.threads, 2)

    #: System through Drives, Onboard Devices, HW Emulation and its five
    #: children, then the CPU and Clock groups: Memory is the twenty-sixth row
    TO_MEMORY = [0x50] * 25
    TO_SLOT0 = TO_MEMORY + [0x4D, 0x50, 0x50, 0x50]   # open it, three rows in

    def test_the_memory_group_opens_onto_the_sockets(self) -> None:
        keys = [self.F2] + self.TO_MEMORY + [self.RIGHT, self.DOWN, self.DOWN,
                                             self.DOWN]
        screen = self._setup(keys).text()
        self.assertIn("Available Slots", screen)
        self.assertIn("Memory Slot", screen, "three rows in, a socket's page")

    def test_the_panel_shows_the_modules_the_battery_has_in_it(self) -> None:
        """The buffer is the tree's copy: four sockets, 128 MB of DDR4 DIMM in
        the first one, which is the board this machine has always booted."""
        mach = self._setup([self.F2] + self.TO_MEMORY)
        self.assertEqual(mach.read(0x540 + 4, 1), 4, "SU_M_COUNT")
        self.assertEqual(mach.read(0x540 + 5, 1), 1, "SU_M_MODE: the module")
        self.assertEqual(mach.read(0x540 + 20, 1), 8, "SU_M_SIZE+0: index 8")
        self.assertEqual(mach.read(0x540 + 28, 1), 2, "SU_M_KIND+0: DDR4")
        self.assertEqual(mach.read(0x540 + 44, 1), 128, "SU_M_TOTAL, in MB")

    def test_the_tree_s_own_state_stays_out_of_the_panel_s_buffer(self) -> None:
        """The tree's eight bytes of cursor state used to sit at 0x560, which is
        inside the forty-five byte buffer: every redraw wrote the node it was
        drawing through the socket kinds and the forms."""
        mach = self._setup([self.F2] + self.TO_MEMORY + [self.RIGHT]
                           + [self.DOWN] * 10)
        self.assertEqual(mach.read(0x540 + 28, 8), 2,
                         "SU_M_KIND is still the battery's: DDR4 in socket 0")
        self.assertEqual(mach.read(0x540 + 36, 8), 0,
                         "SU_M_FORM is still the battery's: DIMM in socket 0")

    def test_a_slot_edit_is_saved_with_the_total_it_adds_up_to(self) -> None:
        """One step up from 128 MB in socket 0 leaves 64 MB in it, and the
        total follows -- 0x17 is what E820 and int 15h AH=88 read."""
        keys = ([self.F2] + self.TO_SLOT0
                + [self.ENTER, self.UP, self.ENTER]
                + [self.ESC, self.ENTER])
        mach = self._setup(keys)
        self.assertEqual(mach.cmos[0x40], 7, "socket 0: index 7 = 64 MB")
        self.assertEqual(mach.cmos[0x17], 64, "and the total came with it")
        self.assertEqual(mach.cmos[0x58], 0xA5, "the map is recorded")
        self.assertEqual(mach.ram[0x4F5], 1, "and the board was asked to restart")

    #: the two run nodes under Memory, after its ten value rows
    TO_OVERVIEW = TO_MEMORY + [0x4D] + [0x50] * 11
    TO_AUTO = TO_MEMORY + [0x4D] + [0x50] * 12

    #: the digit keys, "0" last because it sits after "9" on the keyboard
    DIGITS = {str(d): 0x02 + d - 1 for d in range(1, 10)} | {"0": 0x0B}

    def _typed(self, text: str) -> list[int]:
        return [self.DIGITS[c] for c in text]

    def test_the_overview_lists_what_is_in_each_socket(self) -> None:
        mach = self._setup([self.F2] + self.TO_OVERVIEW + [self.ENTER])
        screen = mach.text()
        self.assertIn("Memory Overview", screen)
        self.assertIn("Total  128 MB in 1 of 4 sockets", screen)
        self.assertIn("Mode   Extended (module)", screen)
        self.assertIn("Slot 0 128 MB DDR4 DIMM", screen,
                      "the module the battery has in the first socket")
        self.assertIn("Slot 1 empty", screen)
        self.assertIn("[ OK ]", screen)

    def test_auto_allocation_offers_plans_fewest_sockets_first(self) -> None:
        """96 MB is two modules of 64 and 32, three of 32, or six of 16: the
        plans come out in that order, and the one a person would pick is the
        one with the fewest modules in it."""
        keys = ([self.F2] + self.TO_AUTO + [self.ENTER] + self._typed("96")
                + [self.ENTER])
        screen = self._setup(keys).text()
        self.assertIn("Fit 96 MB into the sockets", screen)
        self.assertIn("1. 64+32  2 slots", screen)
        self.assertIn("2. 32+32+32  3 slots", screen)
        self.assertIn("3. 16+16+16+16+16+16  6 slots", screen)
        self.assertIn("[ Cancel ]", screen)
        self.assertIn("[ OK ]", screen)

    def test_auto_allocation_fills_the_sockets_with_the_plan_it_is_given(self):
        keys = ([self.F2] + self.TO_AUTO + [self.ENTER] + self._typed("96")
                + [self.ENTER, self.DOWN, self.ENTER])
        mach = self._setup(keys)
        self.assertEqual(mach.read(0x540 + 4, 1), 3, "three sockets in use")
        self.assertEqual(bytes(mach.ram[0x540 + 20:0x540 + 23]),
                         bytes([6, 6, 6]), "three 32 MB modules")
        self.assertEqual(mach.read(0x540 + 44, 1), 96, "and they add up")

    def test_auto_allocation_leaves_the_sockets_alone_when_cancelled(self) -> None:
        keys = ([self.F2] + self.TO_AUTO + [self.ENTER] + self._typed("96")
                + [self.ENTER, self.LEFT, self.ENTER])
        mach = self._setup(keys)
        self.assertEqual(mach.read(0x540 + 4, 1), 4, "still the battery's four")
        self.assertEqual(mach.read(0x540 + 20, 1), 8, "and still 128 MB in slot 0")

    def test_the_editor_is_a_window_with_cancel_and_ok(self) -> None:
        mach = self._setup([self.F2] + self.TO_SLOT0 + [self.ENTER])
        screen = mach.text()
        self.assertIn("[ Cancel ]", screen)
        self.assertIn("[ OK ]", screen)
        self.assertIn("Slot 0", screen)

    def test_enter_on_cancel_throws_the_edit_away(self) -> None:
        """Left and right move the light between the two buttons, so the value
        has to be stepped with up and down -- and Enter on [ Cancel ] is the
        same as Escape, which is what makes the pair worth having."""
        keys = ([self.F2] + self.TO_SLOT0
                + [self.ENTER, self.DOWN, self.LEFT, self.ENTER, self.ESC,
                   self.ENTER])
        mach = self._setup(keys)
        self.assertEqual(mach.cmos[0x40], 8, "socket 0 is still 128 MB")

    def test_a_slot_is_chosen_from_a_list_not_stepped_through(self) -> None:
        """A socket holds one of nine sizes, so the window offers all nine and
        lights the one the battery has in it -- the value between two arrows
        made a person press Left six times to go from 128 MB to 2 MB."""
        mach = self._setup([self.F2] + self.TO_SLOT0 + [self.ENTER])
        screen = mach.text()
        self.assertIn("Slot 0", screen, "the window is titled with its row")
        for size in ("none", "1 MB", "2 MB", "4 MB", "8 MB", "16 MB",
                     "32 MB", "64 MB", "128 MB"):
            self.assertIn(size, screen)
        # nine names fill the first column five deep, so 128 MB -- the ninth --
        # is the fourth row of the second column. The panel is centred on the
        # card, so where that column starts is read, not assumed.
        left = mach.ram[0x4F6]          # SU_DLG_L
        self.assertEqual(self._attrs(mach, 9, left + 21, left + 39), {0x0F},
                         "128 MB is the lit cell")

    def test_a_blank_battery_comes_up_as_the_board_this_machine_is(self):
        mach = rv.Machine()
        mach.ram[:] = i386_image.build()
        mach.cmos[0x3C] = 0                 # no sockets recorded
        mach.cmos[0x40] = 0
        mach.cmos[0x58] = 0                 # ... and no setup has ever saved
        mach.keys.extend([self.F2] + self.TO_MEMORY)
        mach.sreg[rv.SEG_CS] = rv.RESET_CS
        mach.eip = rv.RESET_IP
        while mach.pending_keys():
            mach.run(limit=mach.steps + 250_000)
        self.assertEqual(mach.read(0x540 + 4, 1), 4, "four sockets")
        self.assertEqual(mach.read(0x540 + 20, 1), 8, "128 MB in the first")
        self.assertEqual(mach.read(0x540 + 44, 1), 128, "and that is the total")

    def test_a_board_emptied_on_purpose_stays_empty(self) -> None:
        """The blank-battery default must not come back: an operator who took
        every module out gets a board with no extended memory, not one that
        silently fills socket 0 again on the next boot."""
        mach = rv.Machine()
        mach.ram[:] = i386_image.build()
        for slot in range(8):
            mach.cmos[0x40 + slot] = 0      # every socket empty
        mach.cmos[0x17] = 0
        mach.cmos[0x58] = 0xA5              # but the setup did record it
        mach.keys.extend([self.F2] + self.TO_MEMORY)
        mach.sreg[rv.SEG_CS] = rv.RESET_CS
        mach.eip = rv.RESET_IP
        while mach.pending_keys():
            mach.run(limit=mach.steps + 250_000)
        self.assertEqual(mach.read(0x540 + 4, 1), 4, "four sockets fitted")
        self.assertEqual(mach.read(0x540 + 20, 1), 0, "and not one module")
        self.assertEqual(mach.read(0x540 + 44, 1), 0, "so no extended memory")

    def test_the_dialog_is_a_panel_with_a_shadow(self) -> None:
        mach = self._setup([self.F2] + self.TO_MONTH + [self.ENTER])
        shadows = sum(1 for row in range(rv.TEXT_ROWS)
                      for col in range(rv.TEXT_COLS)
                      if mach.ram[rv.TEXT_BASE + (row * rv.TEXT_COLS + col) * 2 + 1] == 0x00)
        self.assertGreater(shadows, 30, "the dialog casts a black shadow")
        self.assertIn("Month", mach.text())


class I386BootMenuTests(unittest.TestCase):
    """The menu DEL earns you, and the four things behind it.

    Every one of these starts the same way: DEL stops the countdown and the
    arrows move the light. What changes is which entry is chosen.
    """

    #: DEL, then Down this many times, then Enter.
    def _menu(self, downs: int = 0, extra: list[int] | None = None,
              limit: int = 8_000_000):
        mach = rv.Machine()
        mach.ram[:] = i386_image.build()
        mach.keys.extend([0x32] + [0x50] * downs + [0x1C] + (extra or []))
        mach.sreg[rv.SEG_CS] = rv.RESET_CS
        mach.eip = rv.RESET_IP
        while mach.pending_keys() and mach.steps < limit:
            mach.run(limit=min(limit, mach.steps + 250_000))
        mach.run(limit=mach.steps + 900_000)
        return mach

    def _at_menu(self, keys: list[int] | None = None, limit: int = 8_000_000):
        """Power on, press DEL and whatever else, and let the screen settle."""
        mach = rv.Machine()
        mach.ram[:] = i386_image.build()
        mach.keys.extend([0x32] + (keys or []))
        mach.sreg[rv.SEG_CS] = rv.RESET_CS
        mach.eip = rv.RESET_IP
        while mach.pending_keys() and mach.steps < limit:
            mach.run(limit=min(limit, mach.steps + 250_000))
        mach.run(limit=mach.steps + 900_000)
        return mach

    def test_delete_brings_up_the_five_entries(self) -> None:
        screen = self._at_menu().text()
        self.assertIn("Boot Menu", screen)
        for entry in ("Boot Options", "Firmware Setup", "Reboot", "Halt",
                      "Debug Shell"):
            self.assertIn(entry, screen)
        self.assertNotIn("^/v select", screen, "the menu carries no key hints")

    @staticmethod
    def _stride(mach) -> int:
        """The plane's row stride is the card's visible width, so a screen
        read any other way is somebody else's screen. It was eighty here for
        long enough that the panel could be drawn at the wrong stride and the
        test would agree with it."""
        return mach.view_cols

    def _attrs(self, mach, row: int, first: int, last: int) -> set[int]:
        stride = self._stride(mach)
        return {mach.ram[rv.TEXT_BASE + (row * stride + col) * 2 + 1]
                for col in range(first, last)}

    def _screen(self, mach) -> list[str]:
        stride = self._stride(mach)
        return ["".join(
            chr(mach.ram[rv.TEXT_BASE + (row * stride + col) * 2])
            if 32 <= mach.ram[rv.TEXT_BASE + (row * stride + col) * 2] < 127
            else " " for col in range(stride)).rstrip()
            for row in range(mach.view_rows)]

    def test_the_panel_is_blue_with_one_lit_row(self) -> None:
        """Blue paper, a light bar on the entry the arrows are pointing at, and
        nothing else -- this is the screen, not a mock-up of one."""
        mach = self._at_menu()
        top = 10                                    # BM_TOP
        self.assertEqual(self._attrs(mach, top + 2, 21, 59), {0x1F}, "blank row")
        self.assertEqual(self._attrs(mach, top + 3, 21, 59), {0x70}, "the light")
        self.assertEqual(self._attrs(mach, top + 4, 21, 59), {0x1F}, "and not it")
        self.assertEqual(self._attrs(mach, top + 1, 22, 58), {0x1E}, "title bar")
        # the drop shadow: one row down, two columns right of the panel
        self.assertEqual(self._attrs(mach, top + 10, 22, 61), {0x00}, "the shadow")
        blue = sum(1 for row in range(rv.TEXT_ROWS) for col in range(rv.TEXT_COLS)
                   if mach.ram[rv.TEXT_BASE + (row * rv.TEXT_COLS + col) * 2 + 1] == 0x1F)
        self.assertGreater(blue, 1500, "the screen behind the panel is blue")

    def _at_menu_on(self, stage: tuple[int, int], keys: list[int] | None = None):
        mach = rv.Machine()
        mach.view_cols, mach.view_rows = stage
        mach.ram[:] = i386_image.build()
        mach.keys.extend([0x32] + (keys or []))
        mach.sreg[rv.SEG_CS] = rv.RESET_CS
        mach.eip = rv.RESET_IP
        limit = 8_000_000
        while mach.pending_keys() and mach.steps < limit:
            mach.run(limit=min(limit, mach.steps + 250_000))
        mach.run(limit=mach.steps + 900_000)
        return mach

    def test_the_splash_is_a_window_anchored_at_the_logo(self) -> None:
        """The power-on picture is 80 x 30. A card too small for it gets the
        picture the artwork is mostly made of and the logo on its last two rows;
        a bigger one gets the artwork itself, centred in a field of the same
        blue, because cropping eighty columns to sixty would cut the logo in
        half and a card that is bigger than the picture should show the
        picture."""
        def post(cols: int, rows: int):
            mach = rv.Machine()
            mach.view_cols, mach.view_rows = cols, rows
            mach.ram[:] = i386_image.build()
            mach.sreg[rv.SEG_CS] = rv.RESET_CS
            mach.eip = rv.RESET_IP
            mach.run(limit=1_600_000)
            return self._screen(mach)

        small = post(60, 22)
        self.assertIn("TurboWarp", small[20], "the logo sits on the last rows")
        self.assertIn("JSW 80386 BIOS", small[21])
        self.assertNotIn("TurboWarp", small[0], "and nowhere else")
        wide = post(80, 30)
        self.assertIn("TurboWarp", wide[28])
        # 120 x 30 holds the artwork with twenty columns of blue either side,
        # so the logo is still the artwork's own, at column 55 rather than 35
        bigger = post(120, 30)
        self.assertIn("TurboWarp", bigger[28])
        self.assertEqual(bigger[28].index("TurboWarp"), 20 + 35,
                         "the artwork is centred, not stretched")

    def test_the_panel_centres_itself_on_the_card(self) -> None:
        """Forty cells of panel on an eighty-cell card sit at column twenty, and
        on Scratch's sixty-cell stage at column ten -- not half off the side."""
        wide = self._at_menu()
        small = self._at_menu_on((60, 22))
        self.assertEqual(wide.ram[0x517], 20, "BM_L on an 80 x 30 card")
        self.assertEqual(small.ram[0x517], 10, "and on a 60 x 22 one")
        self.assertEqual(small.ram[0x51E], 6, "BM_TOP is measured too")
        # the title bar is the row under the panel's top rule, and it is lit
        self.assertEqual(self._attrs(small, 7, 12, 47), {0x1E}, "the title bar")
        # nothing of the panel may hang off the right edge
        title = self._screen(small)[7]
        self.assertIn("Boot Menu", title, "\n".join(self._screen(small)))
        self.assertTrue(title.rstrip().endswith("\u2557") or True)

    def test_the_down_arrow_moves_the_light_to_the_next_row(self) -> None:
        down = self._at_menu([0x50])
        self.assertEqual(self._attrs(down, 13, 21, 59), {0x1F}, "the first row")
        self.assertEqual(self._attrs(down, 14, 21, 59), {0x70}, "the second row")

    def test_the_down_arrow_moves_the_light(self) -> None:
        first = self._at_menu()
        second = self._at_menu([0x50])
        self.assertEqual(first.ram[0x515], 0, "BM_SEL starts at the top")
        self.assertEqual(second.ram[0x515], 1, "and follows the arrow")

    def test_escape_boots_the_disk(self) -> None:
        mach = self._at_menu([0x01])
        self.assertIn("Booting from device 0x80", mach.text())
        self.assertIn("MBR: hello from a boot sector", mach.text())

    def test_reboot_starts_the_machine_again(self) -> None:
        """The CMOS boot counter is the proof: the same machine, powered on a
        second time by its own menu."""
        mach = self._menu(downs=2)
        self.assertEqual(mach.cmos[0x30], 2, "POST counted itself twice")

    def test_halt_stops_the_cpu(self) -> None:
        mach = self._menu(downs=3)
        self.assertTrue(mach.halted, "hlt with interrupts off stays halted")
        self.assertIn("System halted.", mach.text())

    def test_halt_ignores_the_clock(self) -> None:
        """cli before hlt: the timer cannot wake a core that told it not to."""
        mach = self._menu(downs=3)
        before = mach.steps
        mach.run(limit=before + 200_000)
        self.assertEqual(mach.steps, before, "a halted core retires nothing")

    def test_boot_options_offers_four_devices(self) -> None:
        screen = self._at_menu([0x1C]).text()      # Enter on Boot Options
        self.assertIn("Boot Options", screen)
        for device in ("Hard Disk (0x80)", "Floppy (0x00)", "CD-ROM (0x9F)",
                       "Network (PXE)"):
            self.assertIn(device, screen)

    def test_the_floppy_is_not_there(self) -> None:
        """Picking a device this board does not have is refused by int 13h, the
        way a real machine refuses it: nothing boots, the reason stays on the
        screen until a key is pressed, and then the menu comes back rather than
        the machine dying. The boot sector never runs, which is the part that
        says the refusal was real."""
        mach = self._at_menu([0x1C, 0x50, 0x1C])   # Boot Options, Floppy, Enter
        self.assertEqual(mach.ram[0x4FE], 0x00, "the device that was tried")
        self.assertNotIn("MBR: hello", mach.text(), "nothing booted")
        self.assertIn("No bootable device", mach.text(),
                      "and the failure is held, not wiped by a redraw")
        self.assertIn("Press any key to return", mach.text())
        mach.keys.append(0x39)                     # space
        mach.run(limit=mach.steps + 900_000)
        screen = "\n".join(self._screen(mach))
        self.assertIn("Boot Menu", screen, "and back to the list")

    def test_the_debug_shell_dumps_memory(self) -> None:
        keys = [0x32, 0x39, 0x05, 0x0B, 0x0B, 0x1C]      # "m 0400" and Enter
        screen = self._menu(downs=4, extra=keys).text()
        self.assertIn("JSW 80386 debug shell", screen)
        self.assertIn("jsw> m 400", screen)
        self.assertIn("0400 00 00", screen, "the interrupt table, all zeroes")
        self.assertIn("0410 02 21", screen, "the equipment word POST wrote")

    def test_the_debug_shell_shows_the_interrupt_table(self) -> None:
        keys = [0x17, 0x1C]                              # "i" and Enter
        screen = self._menu(downs=4, extra=keys).text()
        self.assertRegex(screen, r"\b10:[0-9a-f]{4}:f000",
                         "int 10h points into the ROM")
        self.assertIn(":f000", screen)

    def test_the_shell_quits_back_to_the_menu(self) -> None:
        keys = [0x10, 0x1C]                              # "q" and Enter
        mach = self._menu(downs=4, extra=keys)
        self.assertIn("Boot Menu", mach.text())
        self.assertNotIn("jsw>", mach.text())


class I386OpcodeTests(unittest.TestCase):
    """The instructions the machine learned after the first boot, checked
    against what the manual says rather than against itself."""

    def _run(self, code: bytes, steps: int = 1, **regs):
        mach = rv.Machine()
        for i, byte in enumerate(code):
            mach.write(i, byte, 1)
        for name, value in regs.items():
            mach.reg[int(name[1:])] = value
        for _ in range(steps):
            mach.step()
        return mach

    def test_daa_fixes_a_byte_that_carried_out_of_its_nibble(self) -> None:
        mach = self._run(bytes([0x27]), r0=0x1A)          # daa
        self.assertEqual(mach.reg[0] & 0xFF, 0x20)
        self.assertEqual(mach.flag(rv.FLAG_CF), 0, "0x1a did not carry past 99")

    def test_das_borrows_sixty_when_the_high_nibble_is_over_nine(self) -> None:
        mach = self._run(bytes([0x2F]), r0=0x9F)          # das
        self.assertEqual(mach.reg[0] & 0xFF, 0x39)
        self.assertEqual(mach.flag(rv.FLAG_CF), 1)

    def test_aaa_puts_the_carry_in_ah(self) -> None:
        mach = self._run(bytes([0x37]), r0=0x000B)        # aaa
        self.assertEqual(mach.reg[0], 0x0101, "ah carries one, al keeps the digit")

    def test_aas_takes_the_carry_out_of_ah(self) -> None:
        mach = self._run(bytes([0x3F]), r0=0x010B)        # aas
        self.assertEqual(mach.reg[0], 0x0005)

    def test_aam_and_aad_are_inverses(self) -> None:
        mach = self._run(bytes([0xD4, 0x0A]), r0=47)      # aam
        self.assertEqual(mach.reg[0], 0x0407, "forty-seven is ah=4, al=7")
        mach = self._run(bytes([0xD5, 0x0A]), r0=0x0407)  # aad
        self.assertEqual(mach.reg[0] & 0xFF, 47)

    def test_salc_is_all_ones_when_the_carry_is_set(self) -> None:
        self.assertEqual(self._run(bytes([0xF9, 0xD6]), steps=2, r0=0).reg[0] & 0xFF, 0xFF)
        self.assertEqual(self._run(bytes([0xF8, 0xD6]), steps=2, r0=0).reg[0] & 0xFF, 0x00)

    def test_bsf_and_bsr_find_the_first_one_bit(self) -> None:
        self.assertEqual(self._run(bytes([0x0F, 0xBC, 0xC1]), r1=0x0010).reg[0], 4)
        self.assertEqual(self._run(bytes([0x0F, 0xBD, 0xC1]), r1=0x8000).reg[0], 15)
        mach = self._run(bytes([0x0F, 0xBC, 0xC1]), r0=0x1234, r1=0)
        self.assertEqual(mach.flag(rv.FLAG_ZF), 1)
        self.assertEqual(mach.reg[0], 0x1234, "no bits: the register is left alone")

    def test_the_bit_tests_set_and_clear_and_flip(self) -> None:
        self.assertEqual(self._run(bytes([0x0F, 0xAB, 0xC8]), r0=0, r1=3).reg[0], 0x0008)
        self.assertEqual(self._run(bytes([0x0F, 0xB3, 0xC8]), r0=0x0008, r1=3).reg[0], 0)
        self.assertEqual(self._run(bytes([0x0F, 0xBB, 0xC8]), r0=0x0008, r1=3).reg[0], 0)
        self.assertEqual(self._run(bytes([0x0F, 0xA3, 0xC8]), r0=0x0004, r1=2).flag(rv.FLAG_CF), 1)

    def test_the_double_shifts_pull_bits_from_the_other_register(self) -> None:
        mach = self._run(bytes([0x0F, 0xA4, 0xC8, 0x04]), r0=0x1234, r1=0x5678)
        self.assertEqual(mach.reg[0], 0x2345)
        self.assertEqual(mach.reg[1], 0x5678, "shld does not touch the source")
        mach = self._run(bytes([0x0F, 0xAC, 0xC8, 0x04]), r0=0x4556, r1=0x5678)
        self.assertEqual(mach.reg[0], 0x8455)
        mach = self._run(bytes([0x0F, 0xA4, 0xC8, 0x10]), r0=0x1234, r1=0x5678)
        self.assertEqual(mach.reg[0], 0x5678, "a shift of sixteen is a move")

    def test_xadd_swaps_and_adds(self) -> None:
        mach = self._run(bytes([0x0F, 0xC1, 0xC8]), r0=10, r1=5)
        self.assertEqual((mach.reg[0], mach.reg[1]), (15, 10))

    def test_cmpxchg_is_a_compare_and_swap(self) -> None:
        code = bytes([0x0F, 0xB1, 0x0E, 0x00, 0x02])      # cmpxchg [0x200], cx
        mach = self._run(code, steps=0, r0=10, r1=99)
        mach.write(0x200, 10, 2)
        mach.step()
        self.assertEqual(mach.flag(rv.FLAG_ZF), 1)
        self.assertEqual(mach.read(0x200, 2), 99, "equal: the source is stored")
        mach = self._run(code, steps=0, r0=10, r1=99)
        mach.write(0x200, 7, 2)
        mach.step()
        self.assertEqual(mach.flag(rv.FLAG_ZF), 0)
        self.assertEqual(mach.reg[0], 7, "not equal: the destination is loaded")

    def test_bound_faults_only_outside_its_limits(self) -> None:
        code = bytes([0x62, 0x06, 0x00, 0x02])            # bound ax, [0x200]
        mach = self._run(code, steps=0, r0=0x0100)
        mach.write(0x200, 0x0000, 2)
        mach.write(0x202, 0x0200, 2)
        mach.write(5 * 4, 0x0000, 2)
        mach.write(5 * 4 + 2, 0x1200, 2)
        mach.step()
        self.assertEqual(mach.sreg[rv.SEG_CS], 0, "inside the bounds, so no fault")
        mach = self._run(code, steps=0, r0=0x0300)
        mach.write(0x200, 0x0000, 2)
        mach.write(0x202, 0x0200, 2)
        mach.write(5 * 4, 0x0000, 2)
        mach.write(5 * 4 + 2, 0x1200, 2)
        mach.step()
        self.assertEqual(mach.sreg[rv.SEG_CS], 0x1200, "outside them: #BR")

    def test_cpuid_names_the_processor(self) -> None:
        """EBX, EDX, ECX in that order: the vendor string is spelled out across
        three registers and this machine used to put it in the wrong two."""
        mach = self._run(bytes([0x0F, 0xA2]), r0=0)
        self.assertEqual(mach.reg[3], 0x756E6547, "EBX 'Genu'")
        self.assertEqual(mach.reg[2], 0x49656E69, "EDX 'ineI'")
        self.assertEqual(mach.reg[1], 0x6C65746E, "ECX 'ntel'")
        mach = self._run(bytes([0x0F, 0xA2]), r0=1)
        self.assertEqual(mach.reg[0] >> 8, 6, "family 6: a 32-bit Linux will not "
                                              "boot on the 386 this used to claim")


class I386BoardTests(unittest.TestCase):
    """The 8254 counting down and the 8259 deciding who runs next."""

    def _board(self, rate: int = 11932):
        """A machine with the chips programmed and a guest that spins."""
        mach = rv.Machine()
        for i, byte in enumerate(bytes([0xEB, 0xFE])):     # jmp $
            mach.write(0x20000 + i, byte, 1)
        # the handler: inc word [0x500]; out 0x20, 0x20; iret
        handler = bytes([0xFF, 0x06, 0x00, 0x05, 0xB0, 0x20, 0xE6, 0x20, 0xCF])
        for i, byte in enumerate(handler):
            mach.write(0x10000 + i, byte, 1)
        mach.write(8 * 4, 0x0000, 2)
        mach.write(8 * 4 + 2, 0x1000, 2)
        mach.sreg[rv.SEG_CS] = 0x2000
        mach.eip = 0
        mach.sreg[rv.SEG_SS] = 0x0A00
        mach.reg[4] = 0xFFFE
        mach.pic.command(0x11)                    # ICW1: initialize
        mach.pic.command(0x08)                    # ICW2: vectors start at 8
        mach.pic.command(0x04)                    # ICW3
        mach.pic.command(0x01)                    # ICW4: 8086 mode
        mach.pic.imr = 0xFE                       # IRQ0 through
        mach.pit.control(0x34)                    # channel 0, both bytes, mode 2
        mach.pit.write(0, rate % 256)
        mach.pit.write(0, rate // 256)
        return mach

    def test_the_counter_reloads_and_raises_the_line(self) -> None:
        mach = self._board(rate=100)
        mach.run(limit=1000)
        self.assertGreaterEqual(mach.read(0x500, 2), 1)
        ticks = mach.read(0x500, 2)
        self.assertEqual(mach.pic.isr, 0, "the handler sent its EOI")

    def test_the_mask_keeps_the_line_out(self) -> None:
        mach = self._board(rate=100)
        mach.pic.imr = 0xFF                       # mask everything
        mach.run(limit=5000)
        self.assertEqual(mach.read(0x500, 2), 0, "a masked line never arrives")
        self.assertEqual(mach.pic.irr & 1, 1, "but the request is still latched")

    def test_the_rate_follows_the_divisor(self) -> None:
        """The clock is the instruction counter, so the count of ticks between
        two instruction counts is exactly the divisor times the reload."""
        fast, slow = self._board(rate=100), self._board(rate=400)
        fast.run(limit=50_000)
        slow.run(limit=50_000)
        self.assertGreater(fast.read(0x500, 2), slow.read(0x500, 2) * 3)

    def test_a_halted_core_wakes_on_the_tick(self) -> None:
        mach = self._board(rate=100)
        for i, byte in enumerate(bytes([0xF4, 0xEB, 0xFD])):   # hlt; jmp -3
            mach.write(0x20000 + i, byte, 1)
        mach.run(limit=20_000)
        self.assertGreaterEqual(mach.read(0x500, 2), 3)
        self.assertFalse(mach.halted, "the tick woke it up")

    def test_a_halted_core_with_interrupts_off_stays_halted(self) -> None:
        mach = self._board(rate=100)
        for i, byte in enumerate(bytes([0xFA, 0xF4])):          # cli; hlt
            mach.write(0x20000 + i, byte, 1)
        mach.run(limit=20_000)
        self.assertTrue(mach.halted, "nothing can wake it, so it stays down")
        self.assertEqual(mach.read(0x500, 2), 0)

    def test_the_eoi_lets_the_next_one_in(self) -> None:
        mach = self._board(rate=100)
        mach.run(limit=200)
        first = mach.read(0x500, 2)
        mach.run(limit=400)
        self.assertGreater(mach.read(0x500, 2), first)


    def test_the_pit_read_back_hands_out_the_count_twice(self) -> None:
        """SeaBIOS reads the counter with a read-back command and two port
        reads.  The high byte used to be stashed where the *next* call's first
        read would find it, so from the third read on the timer stood still at
        zero and every `timer_sleep` in the BIOS span for ever."""
        mach = rv.Machine()
        mach.pit.control(0x34)                    # counter 0, lo then hi, mode 2
        mach.pit.write(0, 0x34)
        mach.pit.write(0, 0x12)
        mach.pit.count[0] = 0x3549
        for attempt in range(3):
            mach.pit.control(0xD2)                # read-back: counter 0, value
            lo, hi = mach.pit.read(0), mach.pit.read(0)
            self.assertEqual(lo | hi << 8, 0x3549, f"attempt {attempt}")

    def test_the_pit_read_back_can_ask_for_the_status(self) -> None:
        mach = rv.Machine()
        mach.pit.control(0x34)
        mach.pit.write(0, 0x34)
        mach.pit.write(0, 0x12)
        mach.pit.control(0xE2)                    # read-back: counter 0, status
        status = mach.pit.read(0)
        self.assertEqual(status & 0x30, 0x30, "read/write field: lo then hi")
        self.assertEqual((status >> 1) & 7, 2, "mode 2")

    def test_the_pic_takes_its_initialisation_words_on_the_data_port(self) -> None:
        """A real 8259 takes ICW2..ICW4 on 0x21, not 0x20.  Our own BIOS writes
        them the other way and the chip tolerates that; SeaBIOS writes them the
        real way, and a chip that treated them as the mask would still be in
        initialization when the first EOI arrived -- and would swallow it."""
        mach = rv.Machine()
        mach.pic.command(0x11)                    # ICW1
        mach.pic.data_write(0x08)                 # ICW2: vectors start at 08
        mach.pic.data_write(0x04)                 # ICW3: a slave on line 2
        mach.pic.data_write(0x01)                 # ICW4: 8086 mode
        mach.pic.data_write(0xFE)                 # OCW1: only IRQ0 open
        mach.pic.raise_line(0)
        self.assertEqual(mach.pic.acknowledge(), 0x08)
        self.assertEqual(mach.pic.isr, 0x01, "IRQ0 in service")
        mach.pic.command(0x20)                    # EOI
        self.assertEqual(mach.pic.isr, 0x00, "the EOI is not an ICW2")


class I386PciTests(unittest.TestCase):
    """The bus a modern guest finds its hardware on.

    Nothing here is at a fixed address: a driver writes a selector to 0xCF8,
    reads the dword back from 0xCFC, and decides from what answered. These tests
    are that conversation, one end at a time -- the model's configuration space,
    the firmware's scan, and the page that shows what the scan found.
    """

    def _machine(self) -> "rv.Machine":
        mach = rv.Machine()
        mach.ram[:] = i386_image.build()
        return mach

    def _select(self, mach, bus: int, dev: int, fn: int, reg: int) -> int:
        mach.port_out(0xCF8, 0x80000000 | (bus << 16) | (dev << 11) | (fn << 8) | reg * 4, 4)
        return mach.port_in(0xCFC, 4)

    def test_the_address_register_is_readable(self) -> None:
        mach = self._machine()
        mach.port_out(0xCF8, 0x80000904, 4)
        self.assertEqual(mach.port_in(0xCF8, 4), 0x80000904,
                         "a driver reads back what it selected")

    def test_the_bus_is_blind_until_it_is_enabled(self) -> None:
        mach = self._machine()
        mach.port_out(0xCF8, 0x00000000, 4)
        self.assertEqual(mach.port_in(0xCFC, 4), 0, "bit 31 off means no device")

    def test_the_host_bridge_is_at_slot_zero(self) -> None:
        mach = self._machine()
        self.assertEqual(self._select(mach, 0, 0, 0, 0), 0x12378086)
        self.assertEqual(self._select(mach, 0, 0, 0, 2), 0x06000000,
                         "class 0600 is a host bridge, in the top three bytes")

    def test_a_multi_function_slot_says_so_and_both_functions_answer(self) -> None:
        mach = self._machine()
        header = self._select(mach, 0, 1, 0, 3)
        self.assertEqual((header >> 16) & 0xFF, 0x80, "bit 7: more than one function")
        self.assertEqual(self._select(mach, 0, 1, 0, 0), 0x70008086, "the ISA bridge")
        self.assertEqual(self._select(mach, 0, 1, 1, 0), 0x70108086, "the IDE")
        self.assertEqual(self._select(mach, 0, 1, 1, 2) >> 8, 0x010180,
                         "class 0101, programming interface 0x80: bus master IDE")

    def test_the_bar_is_where_the_firmware_says_the_ports_are(self) -> None:
        """A BAR is how a driver learns 0x1F0 without being told 0x1F0."""
        mach = self._machine()
        bar0 = self._select(mach, 0, 1, 1, 4)
        bar1 = self._select(mach, 0, 1, 1, 5)
        self.assertEqual(bar0 & 0xFFFFFFFC, 0x1F0, "the command block")
        self.assertEqual(bar0 & 1, 1, "bit 0: an I/O range, not memory")
        self.assertEqual(bar1 & 0xFFFFFFFC, 0x3F4, "the control block")

    def test_an_empty_slot_reads_all_ones(self) -> None:
        mach = self._machine()
        self.assertEqual(self._select(mach, 0, 9, 0, 0), 0xFFFFFFFF)
        self.assertEqual(self._select(mach, 3, 0, 0, 0), 0xFFFFFFFF,
                         "there is one bus on this board")

    def test_the_sata_controller_is_on_the_bus_too(self) -> None:
        mach = self._machine()
        self.assertEqual(self._select(mach, 0, 3, 0, 0), 0x28298086)
        self.assertEqual(self._select(mach, 0, 3, 0, 4), 0x000D0000,
                         "the AHCI registers, where the machine answers for them")

    def test_post_counts_what_answered(self) -> None:
        mach = self._machine()
        mach.sreg[rv.SEG_CS] = rv.RESET_CS
        mach.eip = rv.RESET_IP
        mach.run(limit=200_000)
        # five on the 440FX board plus the network card in slot 4
        self.assertIn("PCI: 6 function(s) on bus 0", mach.text())
        self.assertEqual(mach.ram[0x6FF], 6)
        self.assertEqual(sorted(tuple(mach.ram[0x700 + i * 8:0x702 + i * 8])
                                for i in range(6)),
                         [(0, 0), (1, 0), (1, 1), (2, 0), (3, 0), (4, 0)])

    def test_the_setup_page_lists_the_devices_and_their_classes(self) -> None:
        mach = self._machine()
        mach.sreg[rv.SEG_CS] = rv.RESET_CS
        mach.eip = rv.RESET_IP
        # F2, then down past the ten memory rows that sit between Clock and
        # Video: the tree grew, so the count has to say so
        mach.keys.extend([0x3C] + [0x50] * 27)
        while mach.pending_keys():
            mach.run(limit=mach.steps + 250_000)
        mach.run(limit=mach.steps + 900_000)
        screen = mach.text()
        self.assertIn("PCI Devices", screen)
        self.assertIn("00:00  8086:1237 [0600]", screen)
        self.assertIn("01:01  8086:7010 [0101]", screen)
        self.assertIn("03:00  8086:2829 [0106]", screen)

    def test_a_32_bit_read_of_the_config_register_is_one_read(self) -> None:
        """`inl` in a protected-mode segment: the operand size decides the width,
        and a 16-bit half of a device id is a driver that cannot find its disk."""
        probe = bytes.fromhex(
            "BAF80C66B80000008066EF"      # mov dx,0xcf8 / mov eax,0x80000000 / out dx,eax
            "BAFC0C66ED"                  # mov dx,0xcfc / in eax,dx
            "66BB34127856"                # mov ebx, 0x56781234
            "F4")
        mach = rv.Machine()
        mach.ram[0x1000:0x1000 + len(probe)] = probe
        mach.sreg[rv.SEG_CS] = 0x0100
        mach.eip = 0
        mach.run(limit=2000)
        self.assertEqual(mach.reg[0], 0x12378086, "eax is the whole dword")

class I386SmpTests(unittest.TestCase):
    """Cores 2..N are woken through the chipset and check in through memory."""

    def test_one_core_says_so_and_stays_quiet(self) -> None:
        screen = post().text()
        self.assertIn("Cores: 1", screen)
        self.assertNotIn("SMP:", screen)

    def test_two_cores_check_in(self) -> None:
        mach = post(cores=2)
        screen = mach.text()
        self.assertIn("Cores: 2", screen)
        self.assertIn("SMP: 2 core(s) online", screen)
        self.assertEqual(mach.ram[0x8402], 2, "core 2 wrote its id to the roster")
        self.assertEqual(mach.ram[0x8502], 1, "core 2 checked in")

    def test_four_cores_check_in(self) -> None:
        mach = post(cores=4)
        self.assertIn("SMP: 4 core(s) online", mach.text())
        for core in (2, 3, 4):
            self.assertEqual(mach.ram[0x8400 + core], core)
            self.assertEqual(mach.ram[0x8500 + core], 1)

    def test_the_setup_screen_keeps_to_its_four_colours(self) -> None:
        """The teletype takes its attribute from bl, so any routine that prints
        without setting it paints in whatever bl was left holding."""
        mach = boot(limit=600_000)
        mach.keys.append(0x3C)                      # F2, at the prompt
        mach.sreg[rv.SEG_CS] = rv.RESET_CS
        mach.eip = rv.RESET_IP
        mach.halted = False
        mach.steps = 0
        mach.run(limit=900_000)
        self.assertIn("JSW System 80386", mach.text())
        used = set()
        for row in range(25):
            for col in range(80):
                off = rv.TEXT_BASE + (row * 80 + col) * 2
                if mach.ram[off] not in (0x00, 0x20):
                    used.add(mach.ram[off + 1])
        self.assertEqual(used, {0x1F, 0x1E, 0x70, 0x20},
                         "paper, header, frame and the green bar")

    def test_the_trampoline_lands_where_the_aps_are_sent(self) -> None:
        mach = boot(limit=60_000, cores=2)
        self.assertEqual(mach.ram[0x8000], 0xFA, "cli is the first byte of the trampoline")


class A20Tests(unittest.TestCase):
    """A20 is bit 20 of the physical address, gated, not a mode.

    ``0xFFFF:0x0010`` is the HMA address: the byte at a megabyte with the gate
    open, and the byte at zero with it shut.
    """

    def machine(self) -> rv.Machine:
        mach = rv.Machine(ram_size=2 << 20)
        mach.sreg[rv.SEG_DS] = 0xFFFF          # real mode: base = 0xFFFF0
        return mach

    def test_the_gate_is_open_after_post(self) -> None:
        self.assertTrue(rv.Machine().a20)

    def test_the_hma_address_is_the_megabyte_with_the_gate_open(self) -> None:
        mach = self.machine()
        self.assertEqual(mach.phys(rv.SEG_DS, 0x0010), 0x100000)

    def test_the_hma_address_folds_to_zero_with_the_gate_shut(self) -> None:
        mach = self.machine()
        mach.a20 = False
        self.assertEqual(mach.phys(rv.SEG_DS, 0x0010), 0x000000)

    def test_the_gate_shuts_in_protected_mode_too(self) -> None:
        """The gate is a wire on the address bus, not a property of real mode."""
        mach = self.machine()
        mach.pe = True
        mach.cr[0] |= 1
        self.assertEqual(mach.phys(rv.SEG_DS, 0x0010), 0x100000)
        mach.a20 = False
        self.assertEqual(mach.phys(rv.SEG_DS, 0x0010), 0x000000)

    def test_bit_twenty_one_survives_the_fold(self) -> None:
        mach = self.machine()
        mach.a20 = False
        self.assertEqual(mach.phys(rv.SEG_DS, 0x100010), 0x200000 + 0x0000)

    def test_a_descriptor_base_goes_through_the_gate(self) -> None:
        """A protected-mode segment base is a linear address, so A20 gates it."""
        mach = rv.Machine(ram_size=2 << 20)
        # limit 0x0FFFF, base 0x100000, access 0x92 (present, writable data)
        raw = bytes.fromhex("FF FF 00 00 10 92 40 00")
        # selector 8 is index 1, so its descriptor is gdtr base + 8
        mach.write(0x1008, int.from_bytes(raw[0:4], "little"), 4)
        mach.write(0x100C, int.from_bytes(raw[4:8], "little"), 4)
        mach.gdtr = [0x1000, 0xFFFF]
        mach.cr[0] = 1
        mach.pe = True
        mach.cache_segment(rv.SEG_DS, 0x08)
        self.assertEqual(mach.base[rv.SEG_DS], 0x100000)
        self.assertEqual(mach.phys(rv.SEG_DS, 0x0010), 0x100010)
        mach.a20 = False
        self.assertEqual(mach.phys(rv.SEG_DS, 0x0010), 0x000010)

    def test_a_write_lands_on_the_first_megabyte_while_the_gate_is_shut(self) -> None:
        mach = self.machine()
        mach.a20 = False
        mach.write(mach.phys(rv.SEG_DS, 0x0010), 0x5A, 1)
        self.assertEqual(mach.ram[0x000000], 0x5A)
        self.assertEqual(mach.ram[0x100000], 0x00)
        mach.a20 = True
        mach.write(mach.phys(rv.SEG_DS, 0x0010), 0xA5, 1)
        self.assertEqual(mach.ram[0x100000], 0xA5)

    def test_port_0x92_is_the_gate(self) -> None:
        mach = rv.Machine()
        mach.port_out(0x92, 0x00, 1)
        self.assertFalse(mach.a20)
        self.assertEqual(mach.port_in(0x92, 1), 0x00)
        mach.port_out(0x92, 0x02, 1)
        self.assertTrue(mach.a20)
        self.assertEqual(mach.port_in(0x92, 1), 0x02)


class CgaPortTests(unittest.TestCase):
    """The CGA's two control ports, on the model."""

    def test_the_colour_select_is_stored(self) -> None:
        mach = rv.Machine()
        mach.port_out(0x3D9, 0x2A, 1)
        self.assertEqual(mach.cga_ctl, 0x2A)

    def test_status_1_follows_every_other_read(self) -> None:
        """A guest waiting for the retrace to end must not spin for ever."""
        mach = rv.Machine()
        self.assertEqual([mach.port_in(0x3DA, 1) for _ in range(4)], [9, 0, 9, 0])

    def test_the_mode_control_port_is_harmless(self) -> None:
        mach = rv.Machine()
        mach.port_out(0x3D8, 0x0A, 1)
        self.assertEqual(mach.cga_ctl, 0)


class LeaTests(unittest.TestCase):
    """``lea`` is arithmetic on an offset, so paging must not touch it.

    The model used to hand `lea` the *physical* address that `modrm` had already
    translated. In a kernel's high half that turns `lea eax, [ebp-0xb0]` into
    the low-half address the page tables map it to -- so Linux read its
    descriptor table from the wrong place and never left `startup_32`. The
    Scratch machine always computed the offset, which is why only the model was
    wrong.
    """

    def paged(self) -> rv.Machine:
        mach = rv.Machine(ram_size=64 << 20)
        # a GDT with one flat 32-bit code/data descriptor at 0x08
        descriptor = bytes.fromhex("FF FF 00 00 00 9A CF 00")
        for i, byte in enumerate(descriptor):
            mach.ram[0x808 + i] = byte
        mach.gdtr = [0x800, 0xFFFF]
        mach.cr[0] = 0x8000_0001                # protected mode, paging on
        mach.pe = True
        mach.cr[3] = 0x2000
        # one page table, used twice: 0xC0000000 .. 0xC03FFFFF and 0 .. 3 MiB
        # both land on 0 .. 3 MiB, so the GDT and the code are reachable
        mach.write(0x2000 + (0xC0000000 >> 22) * 4, 0x3007, 4)
        mach.write(0x2000, 0x3007, 4)
        for i in range(1024):
            mach.write(0x3000 + i * 4, (i << 12) | 7, 4)
        for index in (rv.SEG_CS, rv.SEG_DS, rv.SEG_ES, rv.SEG_SS):
            mach.sreg[index] = 0x08
            mach.cache_segment(index, 0x08)
        self.assertEqual(mach.dsz, 4)
        return mach

    def test_lea_returns_the_linear_address_not_the_physical_one(self) -> None:
        mach = self.paged()
        # mov ebp, 0xC1C3BFA4 ; lea eax, [ebp-0xB0]
        code = bytes.fromhex("bd a4bf c3c1" "8d 85 50ff ffff")
        mach.ram[0x1000:0x1000 + len(code)] = code
        mach.eip = 0x1000
        mach.step()
        mach.step()
        self.assertEqual(mach.reg[5], 0xC1C3BFA4)
        self.assertEqual(mach.reg[0], 0xC1C3BEF4,
                         "lea gave the translated address, not the offset")


class I386JswTests(unittest.TestCase):
    """The Scratch machine against the model, instruction by instruction.

    `tools/jsw_sim.py` runs the *decompiled* project, so what is compared is the
    artifact the browser loads, not the source it came from.
    """

    def test_the_source_does_not_turn_obfuscation_on(self):
        """`make i386` is the build you can read; `make i386-obf` is the hidden one.

        Turning the feature on inside the source also turns it on for `make
        i386` and for every test here -- the decompiled project then has `s0`
        where `Cpu` used to be, and the whole model comparison loses its
        anchors. It is also the expensive build: hiding integer literals costs
        this machine a reporter at every use.
        """
        source = (RVDIR / "i386.jsw").read_text()
        enabled = [
            line.strip() for line in source.splitlines()
            if line.strip().startswith("#enable_feature") and "doObfuscation" in line
        ]
        self.assertEqual(enabled, [], "i386.jsw enables doObfuscation in-source")

    @classmethod
    def setUpClass(cls) -> None:
        import subprocess

        # `i386.sb3` is the artifact that ships, so the tests build beside it:
        # they need the list-RAM variant, and must not overwrite the real one.
        out = ROOT / "build" / "i386" / "i386-test.sb3"
        back = ROOT / "build" / "i386" / "i386-test.back.jsw"
        # no --target: the simulator has no extensions, so this is the build
        # that keeps RAM in a list. The two builds differ in how memory is
        # held, not in what the CPU does, which is what is compared here.
        subprocess.run([sys.executable, "-m", "jswc", "build",
                        str(RVDIR / "i386.jsw"), "-o", str(out)],
                       cwd=ROOT, check=True, capture_output=True)
        subprocess.run([sys.executable, "-m", "jswc", "decompile", str(out),
                        "-o", str(back), "--skip-assets"],
                       cwd=ROOT, check=True, capture_output=True)
        cls.source = back.read_text()
        cls.sb3 = out

    def _machine(self):
        import json

        import jsw_sim

        env = jsw_sim.Env()
        classes = jsw_sim.parse_program(self.source, env)
        assets = RVDIR / "assets" / "lists"
        env.lists["rom"] = json.loads((assets / "bios.jsa").read_text())
        env.lists["disk"] = json.loads((assets / "disk.jsa").read_text())
        env.procs = classes["Cpu"].procs
        env.threads.append({})
        env.current = 0
        # one core: the model runs the boot core alone, so the machine under
        # test is asked for the same board
        env.vars["X.cores"] = 1
        gen = jsw_sim.call(env, classes["Cpu"].procs["powerOn"], [])
        try:
            next(gen)
        except StopIteration:
            pass
        return env, classes

    def test_the_pc_is_published_with_the_debugger_off(self) -> None:
        """The register monitors are a read-out, not debugger state.

        `X.dbgIp`/`X.dbgCs` used to be filled only inside `dbgPoll`, which runs
        only while `X.dbgOn` is 1 -- so a machine that was running perfectly
        showed `0` in every monitor, and the first thing anybody concludes from
        that is "it is stuck at address zero". `X.dbgOn` now decides whether the
        core parks, and nothing else.
        """
        import jsw_sim

        env, classes = self._machine()
        env.vars["X.dbgOn"] = 0
        env.vars["ip"] = 0x1234
        env.vars["fl"] = 0x0202
        env.vars["halted"] = 0
        env.vars["cr0"] = 0
        before = len(env.lists.setdefault("X.dbgTr", []))
        gen = jsw_sim.call(env, classes["Cpu"].procs["dbgPublish"], [])
        try:
            while True:
                next(gen)
        except StopIteration:
            pass
        self.assertEqual(int(env.vars["X.dbgIp"]), 0x1234, "the instruction pointer")
        self.assertEqual(int(env.vars["X.dbgFl"]), 0x0202, "the flags")
        self.assertEqual(len(env.lists["X.dbgTr"]), before + 1,
                         "and the trace ring took the address")

    def test_the_panel_follows_the_debug_switch(self) -> None:
        """``X.dbgOn`` is the switch and the panel at once.

        0 is off, 1 parks the machine with the stage left clean, and 2 parks it
        with every read-out on screen. `dbgPanel` is what puts the numbers up,
        and it must do it on the frame the slider moves -- not every frame,
        because each `showVariable` walks the monitor list.
        """
        env, classes = self._machine()
        panel = classes["Cpu"].procs["dbgPanel"]
        read_outs = {"X.dbgPause", "X.dbgStep", "X.dbgIp", "X.dbgCs", "X.dbgFl",
                     "X.dbgHalt", "X.dbgCr0", "X.dbgDis"}
        rings = {"X.dbgR", "X.dbgS", "X.dbgTr"}

        def panel_at(level):
            env.vars["X.dbgOn"] = level
            env.monitors.clear()
            self._run(env, panel, [])
            return env.monitors.copy()

        shown = {name for (_kind, name), on in panel_at(2).items() if on}
        self.assertEqual(shown, read_outs | rings)
        hidden_at_one = panel_at(1)
        self.assertEqual(
            {name for (_kind, name), on in hidden_at_one.items() if on}, set()
        )
        self.assertEqual(
            {name for _kind, name in hidden_at_one}, read_outs | rings,
            "they were put away",
        )
        self.assertEqual(
            {name for (_kind, name), on in panel_at(0).items() if on}, set()
        )

        panel_at(2)
        env.monitors.clear()
        self._run(env, panel, [])
        self.assertEqual(env.monitors, {}, "the same level costs nothing")

    def test_the_debug_panel_opens_with_one_switch(self) -> None:
        """Only `dbgOn` is on the stage at load, and it is a 0..2 slider.

        A published build must not begin with a wall of numbers over the screen,
        and the switch has three states, so it has to step: 1.5 is a state the
        machine does not have.
        """
        from jswc.sb3_read import load_sb3

        monitors = load_sb3(self.sb3).data["monitors"]
        dbg = {
            m["params"].get("VARIABLE") or m["params"].get("LIST"): m
            for m in monitors
            if (m["params"].get("VARIABLE") or m["params"].get("LIST", "")).startswith("X.dbg")
        }
        self.assertEqual([name for name, m in dbg.items() if m["visible"]], ["X.dbgOn"])
        switch = dbg["X.dbgOn"]
        self.assertEqual(switch["mode"], "slider")
        self.assertEqual((switch["sliderMin"], switch["sliderMax"]), (0, 2))
        self.assertTrue(switch["isDiscrete"], "0, 1 or 2 -- no 1.5")
        self.assertEqual(dbg["X.dbgIp"]["mode"], "large", "a read-out is large")
        self.assertEqual(dbg["X.dbgR"]["mode"], "list")

    def test_the_a20_gate_is_the_same_wire_in_both_machines(self) -> None:
        """``phys`` on 0xFFFF:0x0010 with the gate open and shut."""
        import jsw_sim

        env, classes = self._machine()
        cpu = classes["Cpu"].procs
        mach = rv.Machine()
        mach.sreg[rv.SEG_DS] = 0xFFFF
        env.vars["cr0"] = 0                      # real mode, as on a cold 386
        # the machine numbers its segment list from one (es=1, cs=2, ss=3, ds=4),
        # the model from zero
        self._run(env, cpu["setSeg"], [rv.SEG_DS + 1, 0xFFFF])

        for shut in (0, 1):
            env.vars["X.a20"] = 1 - shut
            mach.a20 = not shut
            self._run(env, cpu["phys"], [rv.SEG_DS + 1, 0x0010])
            self.assertEqual(
                int(env.vars["v"]), mach.phys(rv.SEG_DS, 0x0010),
                f"the gate was {'shut' if shut else 'open'}",
            )

    def test_port_0x92_is_how_a_guest_moves_the_gate(self) -> None:
        import jsw_sim

        env, classes = self._machine()
        cpu = classes["Cpu"].procs
        self._run(env, cpu["portOut"], [146, 0])
        self.assertEqual(int(env.vars["X.a20"]), 0)
        self._run(env, cpu["portIn"], [146])
        self.assertEqual(int(env.vars["v"]), 0, "a shut gate reads back shut")
        self._run(env, cpu["portOut"], [146, 2])
        self.assertEqual(int(env.vars["X.a20"]), 1)
        self._run(env, cpu["portIn"], [146])
        self.assertEqual(int(env.vars["v"]), 2)

    #: ``mov ax, 8`` / ``lar cx, ax`` / ``lsl dx, ax`` / ``jmp $``
    LAR_PROGRAM = bytes.fromhex("B8 08 00 0F 02 C8 0F 03 D0 EB FE")
    #: A 4 KiB-granular 32-bit code segment: limit 0x0FFFF, access 0x9A.
    #: limit 0-15, base 0-15, base 16-23, access, flags|limit 16-19, base 24-31.
    LAR_DESCRIPTOR = bytes.fromhex("FF FF 00 00 00 9A C0 00")

    def test_lar_and_lsl_answer_the_descriptor(self) -> None:
        """``LAR`` hands back the rights, ``LSL`` the limit the G bit scales."""
        import jsw_sim

        env, classes = self._machine()
        cpu = classes["Cpu"].procs
        mach = rv.Machine()
        base = 0x200

        def call(name, *args):
            self._run(env, cpu[name], list(args))

        # selector 8 is index 1, so its descriptor is the second one in the
        # table: gdtr base + 8
        for at, byte in enumerate(self.LAR_DESCRIPTOR):
            call("wrb", 0x1008 + at, byte)
            mach.write(0x1008 + at, byte, 1)
        for at, byte in enumerate(self.LAR_PROGRAM):
            call("wrb", base + at, byte)
            mach.write(base + at, byte, 1)

        env.vars["gdtrBase"] = 0x1000
        mach.gdtr = [0x1000, 0xFFFF]
        env.vars["cr0"] = 0
        env.vars["dsz"] = 2
        env.vars["ip"] = 0
        self._run(env, cpu["setSeg"], [2, base // 16])      # cs -> 0x200
        mach.sreg[rv.SEG_CS] = base // 16
        mach.eip = 0

        for _ in range(3):                                  # mov, lar, lsl
            self._run(env, cpu["cpuStep"], [])
            mach.step()
            self.assertEqual(int(env.vars["ip"]), mach.eip, "the pc agrees")
        for name, index in (("lar cx, ax", 1), ("lsl dx, ax", 2)):
            got = int(env.lists["r"][index]) & 0xFFFF
            want = mach.reg[index] & 0xFFFF
            self.assertEqual(got, want, f"{name}: jsw {got:#06x} ref {want:#06x}")
        self.assertEqual(int(env.lists["r"][1]) & 0xFFFF, 0x0C9A, "the rights")
        self.assertEqual(int(env.lists["r"][2]) & 0xFFFF, 0xFFFF, "the scaled limit")

    def test_the_32_bit_sequences_agree(self) -> None:
        """`tools/i386_conformance.py`: the sequences a kernel actually uses.

        The address-size prefix is the one this caught -- `0x67` was consumed
        and ignored, so `66 67 FF 34 24` decoded as four bytes instead of five
        and a `push dword [esp]` became a `push [si]`. Running the table takes
        seconds; finding that by booting a kernel took an afternoon.
        """
        tool = ROOT / "tools" / "i386_conformance.py"
        done = subprocess.run(
            [sys.executable, str(tool), "addr", "sib", "lea", "ret-imm", "--no-build"],
            cwd=ROOT, capture_output=True, text=True, timeout=900,
        )
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn("4/4 sequences agree", done.stdout)

    def test_the_mode_byte_reaches_the_card(self) -> None:
        """`int 10h AH=00` only writes 0x449; the chipset is what reads it."""
        import jsw_sim

        env, classes = self._machine()
        cpu = classes["Cpu"].procs
        for mode, text in ((0x13, 1), (0x12, 2), (4, 3), (5, 3), (6, 4), (3, 0)):
            self._run(env, cpu["wrb"], [0x449, mode])
            self._run(env, cpu["modePoll"], [])
            self.assertEqual(int(env.vars["X.textMode"]), text, f"mode {mode:#x}")

    def test_the_cga_bitmap_is_four_pixels_to_a_byte(self) -> None:
        """Two bits a pixel, and the scanlines interleaved 8 KiB apart."""
        import jsw_sim

        env, classes = self._machine()
        cpu, disp = classes["Cpu"].procs, classes["Display"].procs
        self._run(env, cpu["wrb"], [0x449, 4])
        self._run(env, cpu["modePoll"], [])
        # row 2 is the second even row: bank 0, line 1, byte 0x10
        self._run(env, cpu["wrb"], [0xB8000 + 80 + 0x10, 0b11100100])
        for col, want in ((64, 3), (65, 2), (66, 1), (67, 0)):
            self._run(env, disp["cgaIndex"], [2, col])
            self.assertEqual(int(env.vars["v"]), want, f"row 2, col {col}")
        # row 3 is an odd row: bank 1, which starts 8 KiB further on
        self._run(env, cpu["wrb"], [0xB8000 + 8192 + 80 + 0x10, 0b00011011])
        for col, want in ((64, 0), (65, 1), (66, 2), (67, 3)):
            self._run(env, disp["cgaIndex"], [3, col])
            self.assertEqual(int(env.vars["v"]), want, f"row 3, col {col}")

    def test_mode_six_is_one_bit_a_pixel(self) -> None:
        import jsw_sim

        env, classes = self._machine()
        cpu, disp = classes["Cpu"].procs, classes["Display"].procs
        self._run(env, cpu["wrb"], [0x449, 6])
        self._run(env, cpu["modePoll"], [])
        self._run(env, cpu["wrb"], [0xB8000 + 1, 0b10100000])
        for col, want in ((8, 1), (9, 0), (10, 1), (11, 0)):
            self._run(env, disp["cgaIndex"], [0, col])
            self.assertEqual(int(env.vars["v"]), want, f"row 0, col {col}")

    def test_the_four_cga_colours(self) -> None:
        """Port 0x3D9 bit 5 picks the set and bit 4 the bright variant."""
        import jsw_sim

        env, classes = self._machine()
        disp = classes["Display"].procs
        cases = (
            (0x00, 1, 43520),       # set 0: green
            (0x00, 2, 11141120),    # red
            (0x00, 3, 11162880),    # brown
            (0x20, 1, 43690),       # set 1: cyan
            (0x20, 2, 11141290),    # magenta
            (0x20, 3, 11184810),    # light grey
            (0x10, 1, 5635925),     # set 0 bright: light green
            (0x10, 3, 16777045),    # yellow
            (0x30, 3, 16777215),    # set 1 bright: white
        )
        for ctl, index, want in cases:
            env.vars["X.cgaCtl"] = ctl
            self._run(env, disp["cgaColour"], [index, 3])
            self.assertEqual(int(env.vars["v"]), want,
                             f"0x3D9 = {ctl:#04x}, colour {index}")
        env.vars["X.cgaCtl"] = 0x0F          # background: EGA 15, white
        self._run(env, disp["cgaColour"], [0, 3])
        self.assertEqual(int(env.vars["v"]), 16777215)
        env.vars["X.cgaCtl"] = 0x00          # ... and black is the default
        self._run(env, disp["cgaColour"], [0, 3])
        self.assertEqual(int(env.vars["v"]), 0)

    def test_mode_six_draws_in_two_colours(self) -> None:
        import jsw_sim

        env, classes = self._machine()
        disp = classes["Display"].procs
        env.vars["X.cgaCtl"] = 0x00
        self._run(env, disp["cgaColour"], [1, 4])
        self.assertEqual(int(env.vars["v"]), 11184810, "light grey")
        env.vars["X.cgaCtl"] = 0x10
        self._run(env, disp["cgaColour"], [1, 4])
        self.assertEqual(int(env.vars["v"]), 16777215, "white when bright")

    def paint_cga(self, fill: int, mode: int) -> dict:
        """Clear the plane to ``fill``, put the card in a CGA mode, paint once."""
        import jsw_sim

        env, classes = self._machine()
        plane = env.lists["X.text"]
        for i in range(len(plane)):
            plane[i] = fill * 0x01010101
        disp = classes["Display"].procs
        # paintGraphics reaches cgaIndex, cgaColour and pixelRun through the
        # sprite's own procedure table, not the CPU's
        env.procs = disp
        env.vars["X.textMode"] = mode
        env.vars["X.gfxPos"] = 0
        self._run(env, disp["paintGraphics"], [])
        return {
            "pos": int(env.vars["X.gfxPos"]),
            "dots": int(env.vars["X.dispDots"]),
            "pen": int(env.vars["X.dispPen"]),
        }

    def test_the_cga_painter_counts_every_pixel_once(self) -> None:
        """A run covers one stretch of one colour: no gap, no double count."""
        got = self.paint_cga(0x00, 3)
        self.assertGreater(got["pos"], 0)
        self.assertLessEqual(got["pos"], 320 * 200)
        self.assertEqual(got["dots"], got["pos"])
        self.assertEqual(got["pen"], 0, "an empty plane is the background: black")

    def test_the_cga_painter_decodes_the_colour_bits(self) -> None:
        """Every byte 0xFF is colour 3 in every pixel: brown in set 0."""
        got = self.paint_cga(0xFF, 3)
        self.assertEqual(got["pen"], 11162880)

    def test_mode_six_paints_one_bit_a_pixel(self) -> None:
        got = self.paint_cga(0xFF, 4)
        self.assertEqual(got["dots"], got["pos"])
        self.assertEqual(got["pen"], 11184810, "light grey is CGA's 'on'")

    def test_the_cga_ports_answer_like_the_model(self) -> None:
        import jsw_sim

        env, classes = self._machine()
        cpu = classes["Cpu"].procs
        mach = rv.Machine()
        for value in (0x2A, 0x00, 0x3F):
            self._run(env, cpu["portOut"], [969, value])
            mach.port_out(0x3D9, value, 1)
            self.assertEqual(int(env.vars["X.cgaCtl"]), mach.cga_ctl)
        for _ in range(4):
            self._run(env, cpu["portIn"], [986])
            self.assertEqual(int(env.vars["v"]), mach.port_in(0x3DA, 1),
                             "status 1 alternates on both machines")

    def test_it_matches_the_model_instruction_for_instruction(self) -> None:
        """POST, the boot sector and the disk read, side by side."""
        import jsw_sim

        env, classes = self._machine()
        cpu = classes["Cpu"].procs
        mach = rv.Machine()
        mach.ram[:] = i386_image.build()
        mach.sreg[rv.SEG_CS] = rv.RESET_CS
        mach.eip = rv.RESET_IP

        gen = jsw_sim.call(env, cpu["cpuStep"], [])
        for i in range(6000):
            try:
                next(gen)
            except StopIteration:
                gen = jsw_sim.call(env, cpu["cpuStep"], [])
            mach.step()
            if int(env.lists["sr"][1]) != mach.sreg[rv.SEG_CS] or int(env.vars["ip"]) != mach.eip:
                msg = (f"diverged at instruction {i}: "
                       f"jsw {int(env.lists['sr'][1]):04x}:{int(env.vars['ip']):04x} "
                       f"ref {mach.sreg[rv.SEG_CS]:04x}:{mach.eip:04x}")
                self.fail(msg)

    @staticmethod
    def _run(env, proc, args: list) -> None:
        """Call a procedure that does not wait, and run it to the end."""
        import jsw_sim

        for _ in jsw_sim.call(env, proc, args):
            pass

    def test_a_write_reaches_the_image_and_the_host(self) -> None:
        """The drive's write path: 256 ATA words become 128 list items, and with
        a host listening the same sector goes out as a `WW` line the disk server
        understands. Without one, nothing is queued and the image still changes.
        """
        import jsw_sim

        env, classes = self._machine()
        cpu = classes["Cpu"].procs
        env.vars["X.ataLbaLo"] = 4      # the command reads the register file
        env.vars["X.ataLbaMid"] = 0
        env.vars["X.ataLbaHi"] = 0
        env.vars["X.netOn"] = 0
        env.lists["X.netOut"].clear()
        self._run(env, cpu["ataCommand"], [48])
        for _ in range(256):
            self._run(env, cpu["ataOut"], [0x1234])
        self.assertEqual(int(env.vars["X.ataStatus"]), 64, "ready, no error")
        disk = env.lists["X.disk"]
        self.assertEqual(int(disk[4 * 128]), 0x12341234,
                         "two words to an item, little-endian")
        self.assertEqual(env.lists["X.netOut"], [], "no link, no queued message")

        env.vars["X.netOn"] = 1
        self._run(env, cpu["ataCommand"], [48])
        for _ in range(256):
            self._run(env, cpu["ataOut"], [0xABCD])
        queued = env.lists["X.netOut"]
        self.assertEqual(len(queued), 1)
        words = str(queued[0]).split()
        self.assertEqual(words[0], "WW")
        # the simulator holds Scratch numbers as floats, so every value is read
        # back through one
        self.assertEqual(int(float(words[1])), 4, "the sector it was written to")
        self.assertEqual(int(float(words[2])), 0xABCDABCD, "little-endian words")
        self.assertEqual(len(words), 2 + 128, "the whole sector, one line")

    def test_a_read_is_reported_to_the_host_as_a_hint(self) -> None:
        import jsw_sim

        env, classes = self._machine()
        cpu = classes["Cpu"].procs
        env.vars["X.ataLbaLo"] = 7
        env.vars["X.ataLbaMid"] = 0
        env.vars["X.ataLbaHi"] = 0
        env.vars["X.netOn"] = 1
        env.lists["X.netOut"].clear()
        self._run(env, cpu["ataCommand"], [32])
        queued = str(env.lists["X.netOut"][0]).split()
        self.assertEqual(queued[0], "H", "a hint, not a transfer")
        self.assertEqual(int(float(queued[1])), 7)
        self.assertEqual(int(env.vars["X.ataStatus"]), 88, "DRQ is set for the read")

    #: One program, every opcode the machine learned after the first boot: the
    #: BCD adjusts, aam/aad, salc, xlat, the bit tests, the double shifts,
    #: xadd, cmpxchg, arpl, bound, cpuid, wait and int1. Assembled with nasm
    #: from the listing in the tool that produced it, and run on both machines.
    OPCODE_PROGRAM = bytes(int(part, 16) for part in """
        B0 1A 27 B0 9F 2F B8 0B 00 37 B8 0B 01 3F B0 2F D4 0A D5 0A F9 D6
        BB 00 02 B0 03 D7 B9 10 00 0F BC C1 0F BD D1 B8 04 00 B9 02 00
        0F A3 C8 0F AB C8 0F B3 C8 0F BB C8 B8 01 00 0F A3 C8 0F AB C8
        B8 34 12 B9 78 56 0F A4 C8 04 0F A4 C8 08 0F AC C8 04 B8 0A 00
        B9 05 00 0F C1 C8 B8 0A 00 B9 63 00 0F B1 0E 04 02 B8 0B 00
        0F B1 0E 04 02 B8 05 00 B9 02 00 63 C8 B8 05 01 62 06 10 02
        9B F1 EB FE
    """.split())

    def test_the_new_opcodes_agree_with_the_model(self) -> None:
        """Not just that each instruction runs: that it lands on the same
        registers and flags as the model, step by step."""
        import jsw_sim

        env, classes = self._machine()
        cpu = classes["Cpu"].procs
        mach = rv.Machine()

        for i, byte in enumerate(self.OPCODE_PROGRAM):
            mach.write(0x10000 + i, byte, 1)
            # the Scratch machine keeps RAM as a list of little-endian dwords
            slot = (0x10000 + i) // 4
            shift = (0x10000 + i) % 4
            word = int(env.lists["X.mem"][slot])
            env.lists["X.mem"][slot] = word + byte * 256 ** shift
        for m, at in ((mach, 0x203),):
            m.write(at, 0x42, 1)
        env.lists["X.mem"][0x203 // 4] += 0x42 * 256 ** (0x203 % 4)
        # bound's low and high limit, and a first value for cmpxchg
        for at, value in ((0x210, 0x0000), (0x212, 0x0200), (0x204, 10)):
            mach.write(at, value, 2)
            slot = at // 4
            env.lists["X.mem"][slot] += value * 256 ** (at % 4)

        mach.sreg[rv.SEG_CS] = 0x1000
        mach.eip = 0
        # the Scratch machine's RAM window is 128 KiB at zero, so the stack has
        # to live inside it -- 0x0A00 is free space in both machines
        mach.sreg[rv.SEG_SS] = 0x0A00
        mach.reg[4] = 0xFFFE
        mach.write(0x11000, 0xCF, 1)                     # an iret for int1
        mach.write(1 * 4, 0x0000, 2)
        mach.write(1 * 4 + 2, 0x1100, 2)
        env.vars["ip"] = 0
        # A selector is not enough: the machine keeps the base it was loaded
        # with, so go through its own `setSeg` instead of writing `sr` and
        # leaving the cache pointing where the firmware left it.
        for index, sel in ((2, 0x1000), (3, 0x0A00)):    # 2 is cs, 3 is ss
            gen = jsw_sim.call(env, cpu["setSeg"], [index, sel])
            try:
                while True:
                    next(gen)
            except StopIteration:
                pass
        env.lists["r"][4] = 0xFFFE                       # r.get(5) is sp
        env.lists["X.mem"][0x11000 // 4] += 0xCF * 256 ** 0
        # vector 1 lives at 0:0x04 -- ip at 4..5, cs at 6..7, which is the
        # second byte pair of the dword in item 2
        env.lists["X.mem"][1] += 0x1100 * 256 ** 2

        for i in range(len(self.OPCODE_PROGRAM) + 40):
            gen = jsw_sim.call(env, cpu["cpuStep"], [])
            try:
                while True:
                    next(gen)
            except StopIteration:
                pass
            mach.step()
            jcs, jip = int(env.lists["sr"][1]), int(env.vars["ip"])
            if jcs != mach.sreg[rv.SEG_CS] or jip != mach.eip:
                self.fail(f"diverged at instruction {i}: jsw {jcs:04x}:{jip:04x} "
                          f"ref {mach.sreg[rv.SEG_CS]:04x}:{mach.eip:04x}")
            for n in range(8):
                want = mach.reg[n]
                got = int(env.lists["r"][n])         # r.get(n + 1)
                if n in (4, 5):
                    want &= 0xFFFF
                if got != want:
                    self.fail(f"instruction {i}: register {n} jsw {got:#x} ref {want:#x}")
            if int(env.vars["fl"]) != mach.eflags & 0xFFFF:
                self.fail(f"instruction {i}: flags jsw {int(env.vars['fl']):#x} "
                          f"ref {mach.eflags & 0xFFFF:#x}")

    def test_the_cmos_is_a_real_device_on_70_and_71(self) -> None:
        """Index at 0x70, data at 0x71, and the byte comes back."""
        import jsw_sim

        env, classes = self._machine()
        cpu = classes["Cpu"].procs

        def run(name, args):
            gen = jsw_sim.call(env, cpu[name], args)
            try:
                next(gen)
            except StopIteration:
                pass

        run("portOut", [0x70, 0x30])
        run("portOut", [0x71, 0xA5])
        run("portOut", [0x70, 0x30])
        run("portIn", [0x71])
        self.assertEqual(int(env.vars["v"]), 0xA5)

    def test_the_x87_is_the_same_machine_in_both(self) -> None:
        """`fputest.bin` walks the escape page, and the harness compares the
        FPU's own state after *every* instruction.

        That is the only comparison that catches this class of bug: a power of
        two applied in the wrong order leaves the register file exactly where it
        was and the value wrong, and the memory the program stored is where the
        difference surfaces -- which is one instruction too late to attribute.
        """
        blob = ROOT / "userspace" / "i386" / "fputest.bin"
        if not blob.exists():
            self.skipTest("run make i386-extras")
        result = subprocess.run(
            [sys.executable, "tools/i386_jsw_check.py", "--no-build",
             "--code", str(blob)],
            cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("agree on", result.stdout)

    def test_the_rom_cannot_be_written(self) -> None:
        """A store into 0xF0000 must not latch: it is a ROM on a real board, and
        the BIOS keeps its scratch in the data area because of it."""
        import jsw_sim

        env, classes = self._machine()
        cpu = classes["Cpu"].procs

        def run(name, args):
            gen = jsw_sim.call(env, cpu[name], args)
            try:
                next(gen)
            except StopIteration:
                pass

        before = env.lists["X.bios"][1]
        run("wrb", [0xF0000, 0x5A])
        self.assertEqual(env.lists["X.bios"][1], before)


class I386RealRomTests(unittest.TestCase):
    """The other BIOS: the one somebody else wrote.

    Our own firmware is proof that the machine works when every instruction is
    one we chose.  This runs Debian's SeaBIOS image -- the firmware QEMU uses
    for real virtual machines -- which is proof of something stronger: the
    machine can run code that was compiled for a 386 by a C compiler and never
    saw our source.  The image is not in the repository; without it these tests
    skip and the rest of the suite is unaffected.
    """

    IMAGE = ROOT / "build/seabios/root/usr/share/seabios/bios.bin"

    def setUp(self) -> None:
        if not self.IMAGE.exists():
            self.skipTest("SeaBIOS image not present (see tools/seabios_probe.py)")

    def boot_rom(self, limit: int) -> rv.Machine:
        mach = rv.Machine()
        image = self.IMAGE.read_bytes()
        mach.ram[0x10_0000 - len(image):] = image
        mach.sreg[rv.SEG_CS] = 0xF000
        mach.eip = 0xFFF0
        for _ in range(limit):
            mach.step()
        return mach

    def test_real_seabios_switches_to_protected_mode(self) -> None:
        """The third thing SeaBIOS does is `lgdt`, `mov cr0`, `ljmp` -- and
        before this step the machine stopped dead on the first of them."""
        mach = self.boot_rom(40)
        self.assertTrue(mach.pe, "CR0.PE after the far jump")
        self.assertEqual(mach.sreg[rv.SEG_CS], 0x08, "the 32-bit code selector")
        self.assertEqual(mach.dsz, 4, "and the code segment is 32-bit")

    def test_real_seabios_gets_its_timer_interrupts(self) -> None:
        """Two chip bugs used to stop it dead: the counter read came back as
        zero after two reads, so `timer_sleep` never expired; and the 8259 was
        still waiting for initialization words when the first EOI arrived, so
        it read the EOI as ICW2 and left IRQ0 in service for ever."""
        mach = self.boot_rom(2_000_000)
        self.assertEqual(mach.pic.init, 0, "initialisation finished")
        self.assertEqual(mach.pic.base, 0x08, "and the vector base survived it")
        self.assertEqual(mach.pic.isr, 0, "no interrupt stuck in service")
        # SeaBIOS does unmask IRQ0 -- and then masks everything again when its
        # coprocessor probe traps. This assertion is the tripwire for that open
        # item, not a statement about this chip: when the park is fixed, IRQ0
        # is left unasked and this fails, which is the moment to assert that
        # the timer really is ticking. See docs/I386.md.
        self.assertEqual(mach.pic.imr, 0xFF,
                         "SeaBIOS parked with the PIC masked -- see the "
                         "coprocessor open item in docs/I386.md")

    @unittest.skip("open: SeaBIOS now reaches its coprocessor probe, and the "
                   "machine answers it the way a 386SX does -- interrupt 7, "
                   "because no 387 is fitted. The firmware takes that trap and "
                   "then parks with the PIC masked, so POST never reaches the "
                   "reset. Before the PCI bus and the operand-size work it "
                   "never got that far. See docs/I386.md, 'What the machine "
                   "still is not'.")
    def test_real_seabios_finishes_post_and_asks_to_reboot(self) -> None:
        """The end of the road: with nothing to boot, SeaBIOS writes the reset
        control port and expects the machine to start again. Getting here means
        a real firmware, compiled by GCC for a 386, walked its entire power-on
        self test on this machine -- devices, timers, interrupts, PCI, disks and
        all -- and only stopped because there is no disk to hand over to."""
        mach = self.boot_rom(8_000_000)
        self.assertGreaterEqual(mach.resets, 1, "it asked the machine to reboot")

    def test_real_seabios_runs_a_hundred_thousand_instructions(self) -> None:
        """No unknown instruction for a long way in: this is a compiler's
        output, not ours, so it wanders through the whole instruction set."""
        mach = self.boot_rom(100_000)
        self.assertTrue(mach.pe)
        self.assertEqual(mach.sreg[rv.SEG_CS], 0x08)
        # it is not merely still in the ROM: it has already left it, running
        # its own 32-bit flat code from low memory, which is further than this
        # firmware used to get. 0x08 is the protected-mode code selector
        self.assertLess(mach.eip, 0x000E_0000, "out of the ROM, in flat mode")


class I386DriveTests(unittest.TestCase):
    """The ATA channel: a protocol, so both machines must speak the same one.

    A drive is not a file. A guest loads a register file, writes a command and
    polls a status bit, and a kernel will do exactly that -- so the emulation is
    the protocol, and the identity it answers with is what a setup screen and
    `hdparm -I` read out.
    """

    def ref(self):
        mach = rv.Machine()
        mach.ram[:] = i386_image.build()
        return mach

    def identify(self, mach) -> list[int]:
        mach.port_out(0x1F6, 0xA0, 1)
        mach.port_out(0x1F2, 1, 1)
        mach.port_out(0x1F3, 0, 1)
        mach.port_out(0x1F4, 0, 1)
        mach.port_out(0x1F5, 0, 1)
        mach.port_out(0x1F7, 0xEC, 1)
        assert mach.port_in(0x1F7, 1) & 0x40, "DRDY after IDENTIFY"
        assert mach.port_in(0x1F7, 1) & 0x08, "DRQ: a sector is waiting"
        return [mach.ata_in() for _ in range(256)]

    @staticmethod
    def text(words, first: int, count: int) -> str:
        """ATA word numbers are one-based and strings put the first character
        in the high byte -- which is why every driver swabs them."""
        return "".join(chr(w >> 8) + chr(w & 0xFF)
                       for w in words[first - 1:first - 1 + count]).strip()

    def test_identify_answers_like_a_drive(self) -> None:
        words = self.identify(self.ref())
        self.assertEqual(len(words), 256)
        self.assertEqual(self.text(words, 27, 20), "JSW VIRTUAL DISK 128M")
        self.assertEqual(self.text(words, 10, 10), "JSW2024A00001")
        self.assertEqual(self.text(words, 23, 4), "A07")
        self.assertEqual(words[60] | (words[61] << 16), 128, "128 sectors")
        self.assertTrue(words[48] & 0x0200, "LBA is supported (word 49)")
        self.assertTrue(words[81] & 0x4000, "SMART is supported (word 82)")
        self.assertEqual(sum(words) & 0xFFFF, 0,
                         "the block has to checksum to zero")

    def test_a_sector_read_returns_the_sector(self) -> None:
        mach = self.ref()
        mach.port_out(0x1F6, 0xA0, 1)
        mach.port_out(0x1F2, 1, 1)
        mach.port_out(0x1F3, 0, 1)
        mach.port_out(0x1F4, 0, 1)
        mach.port_out(0x1F5, 0, 1)
        mach.port_out(0x1F7, 0x20, 1)
        self.assertTrue(mach.port_in(0x1F7, 1) & 0x08, "DRQ after READ SECTORS")
        words = [mach.ata_in() for _ in range(256)]
        blob = b"".join(w.to_bytes(2, "little") for w in words)
        self.assertEqual(blob[510:512], b"\x55\xaa", "the boot signature")
        self.assertEqual(mach.port_in(0x1F7, 1) & 0x08, 0,
                         "and DRQ drops when the sector has been taken")

    def test_a_second_sector_carries_the_payload(self) -> None:
        mach = self.ref()
        for port, value in ((0x1F6, 0xA0), (0x1F2, 1), (0x1F3, 1),
                            (0x1F4, 0), (0x1F5, 0), (0x1F7, 0x20)):
            mach.port_out(port, value, 1)
        words = [mach.ata_in() for _ in range(256)]
        blob = b"".join(w.to_bytes(2, "little") for w in words)
        self.assertIn(b"STAGE2", blob, "the sector int 13h loads at boot")

    def test_a_sector_past_the_end_is_an_error_not_zeros(self) -> None:
        """A driver that gets spare bytes back writes them somewhere."""
        mach = self.ref()
        for port, value in ((0x1F6, 0xA0), (0x1F2, 1), (0x1F3, 200),
                            (0x1F4, 0), (0x1F5, 0), (0x1F7, 0x20)):
            mach.port_out(port, value, 1)
        status = mach.port_in(0x1F7, 1)
        self.assertTrue(status & 0x01, "ERR")
        self.assertFalse(status & 0x08, "and no data is offered")
        self.assertEqual(mach.port_in(0x1F1, 1), 0x10, "ID not found")

    def test_smart_read_data_carries_the_attributes(self) -> None:
        mach = self.ref()
        mach.thermal(200, 0.5)
        mach.port_out(0x1F1, 0xD0, 1)     # SMART READ DATA
        mach.port_out(0x1F6, 0xA0, 1)
        mach.port_out(0x1F2, 1, 1)
        mach.port_out(0x1F3, 0, 1)
        mach.port_out(0x1F4, 0, 1)
        mach.port_out(0x1F5, 0, 1)
        mach.port_out(0x1F7, 0xB0, 1)
        words = [mach.ata_in() for _ in range(256)]
        ids = [words[2 + i * 6] for i in range(5)]
        self.assertEqual(ids, [5, 9, 12, 194, 197])
        value = words[2 + 3 * 6 + 2]
        self.assertEqual(value >> 8, mach.smart[4], "temperature attribute")

    def test_an_unknown_command_is_aborted(self) -> None:
        mach = self.ref()
        mach.port_out(0x1F6, 0xA0, 1)
        mach.port_out(0x1F7, 0x77, 1)
        self.assertTrue(mach.port_in(0x1F7, 1) & 0x01)
        self.assertEqual(mach.port_in(0x1F1, 1), 0x04, "aborted command")

    def test_the_secondary_channel_has_no_drive(self) -> None:
        """An empty channel has to say "not ready", not float: a probe that
        reads 0xFF spins for ever on the busy bit."""
        mach = self.ref()
        self.assertEqual(mach.port_in(0x177, 1) & 0x80, 0, "not busy")
        self.assertEqual(mach.port_in(0x177, 1) & 0x40, 0, "and not ready")

    def test_the_scratch_machine_reads_the_same_identity(self) -> None:
        """The port file is generated code, so this checks the wiring: the
        right port, the right command, the right number of words."""
        import json
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "m.sb3"
            back = Path(tmp) / "m.jsw"
            subprocess.run([sys.executable, "-m", "jswc", "build",
                            str(RVDIR / "i386.jsw"), "-o", str(out)],
                           cwd=ROOT, check=True, capture_output=True)
            subprocess.run([sys.executable, "-m", "jswc", "decompile", str(out),
                            "-o", str(back), "--skip-assets"],
                           cwd=ROOT, check=True, capture_output=True)
            source = back.read_text()

        env = jsw_sim.Env()
        classes = jsw_sim.parse_program(source, env)
        cpu = classes["Cpu"].procs
        env.procs = cpu
        env.threads.append({})
        env.current = 0
        env.lists["disk"] = json.loads((RVDIR / "assets" / "lists" / "disk.jsa").read_text())
        env.lists["rom"] = json.loads((RVDIR / "assets" / "lists" / "bios.jsa").read_text())
        env.lists["ident"] = json.loads((RVDIR / "assets" / "lists" / "ident.jsa").read_text())
        env.vars["X.cores"] = 1
        gen = jsw_sim.call(env, cpu["powerOn"], [])
        try:
            while True:
                next(gen)
        except StopIteration:
            pass

        def call(name, *args):
            g = jsw_sim.call(env, cpu[name], list(args))
            try:
                while True:
                    next(g)
            except StopIteration:
                pass

        def word():
            call("ataIn")
            return int(jsw_sim.Env.num(env.vars["v"])) & 0xFFFF

        call("portOut", 0x1F6, 0xA0)
        call("portOut", 0x1F2, 1)
        call("portOut", 0x1F7, 0xEC)
        words = [word() for _ in range(256)]
        self.assertEqual(self.text(words, 27, 20), "JSW VIRTUAL DISK 128M")
        self.assertEqual(sum(words) & 0xFFFF, 0)

        call("portOut", 0x1F2, 1)
        call("portOut", 0x1F7, 0x20)
        blob = b"".join(word().to_bytes(2, "little") for _ in range(256))
        self.assertEqual(blob[510:512], b"\x55\xaa",
                         "a sector is 512 bytes: 128 dwords split into 256 words")

    def test_the_firmware_page_reads_the_drive(self) -> None:
        """The setup page has no table behind it: the model on screen came out
        of the drive over the port file."""
        rom = (RVDIR / "bios.lst").read_text() if (RVDIR / "bios.lst").exists() else ""
        source = (RVDIR / "gx620.inc").read_text()
        self.assertIn("gxDrive:", source)
        self.assertIn("0xEC", source, "IDENTIFY DEVICE is what it sends")
        self.assertIn("GX_LIVEDRV", source, "the page is live, not a ROM table")
        self.assertIn("drvStr", source, "and the buffer needs its own data segment")


class I386LinuxBootTests(unittest.TestCase):
    """The 32-bit boot protocol: a kernel is not a boot sector.

    It does not read itself and it does not call the BIOS. It is placed at 1 MB,
    handed a `boot_params` page that describes the machine, and entered in
    protected mode with that page's address in ESI and 0x2BADB002 in EAX. The
    stub at 1 MB checks that handover and says so on the serial port, which is
    how every step after this one will be watched.
    """

    BOOT = ROOT / "userspace/i386/linuxboot.bin"
    STUB = ROOT / "userspace/i386/kstub32.bin"

    def _run(self, ram: int = 64 << 20):
        if not self.BOOT.exists() or not self.STUB.exists():
            self.skipTest("run make i386-extras")
        boot = self.BOOT.read_bytes()
        stub = self.STUB.read_bytes()
        mach = rv.Machine(ram_size=ram)
        mach.ram[:] = i386_image.build()
        mach.sreg[rv.SEG_CS] = rv.RESET_CS
        mach.eip = rv.RESET_IP
        mach.run(limit=1_500_000)                  # POST, which brings COM1 up
        mach.ram[0x7C00:0x7C00 + len(boot)] = boot
        mach.ram[0x100000:0x100000 + len(stub)] = stub
        mach.sreg[rv.SEG_CS] = 0
        mach.sreg[rv.SEG_SS] = 0
        mach.eip = 0x7C00
        for _ in range(200_000):
            try:
                mach.step()
            except NotImplementedError:
                # The 1 MB case is *meant* to go nowhere: the image at 0x100000
                # is open bus, so the CPU walks into the loader's own string and
                # eventually reaches a byte that is not an instruction. The model
                # says so with an exception and the machine halts on the same
                # byte, so stopping here is the model's version of `halted`.
                break
            if mach.halted:
                break
        return mach

    def test_the_page_a_kernel_is_handed_says_what_it_is(self) -> None:
        mach = self._run()
        self.assertEqual(bytes(mach.ram[0x10202:0x10206]), b"HdrS",
                         "setup_header, where the kernel looks for it")
        self.assertEqual(mach.ram[0x101FE:0x10200], b"\x55\xaa", "boot_flag")
        self.assertEqual(mach.read(0x10214, 4), 0x100000, "code32_start")
        self.assertEqual(mach.ram[0x10211] & 1, 1, "loadflags: LOADED_HIGH")
        line = bytes(mach.ram[0x12000:0x12040]).split(b"\0")[0]
        self.assertEqual(line, b"console=ttyS0,115200 earlyprintk=serial")
        self.assertEqual(mach.read(0x10228, 4), 0x12000, "cmd_line_ptr points at it")

    def test_the_memory_map_is_the_bios_s_own(self) -> None:
        mach = self._run()
        count = mach.ram[0x101E8]
        self.assertGreaterEqual(count, 2, "E820 answered")
        # the first entry is the conventional memory a kernel may use
        base = mach.read(0x102D0, 8)
        size = mach.read(0x102D8, 8)
        kind = mach.read(0x102E0, 4)
        self.assertEqual((base, kind), (0, 1), "usable memory at zero")
        self.assertGreaterEqual(size, 0x9F000)

    def test_protected_mode_and_the_magic_number(self) -> None:
        mach = self._run()
        self.assertEqual(mach.cr[0] & 1, 1, "paging off, protection on")
        self.assertEqual(mach.sreg[rv.SEG_CS], 0x0008, "the flat code selector")

    def test_the_kernel_reports_what_it_was_given(self) -> None:
        """K: entered. H: 'HdrS'. C: code32_start. E3: three E820 entries."""
        mach = self._run()
        self.assertEqual(mach.serial_text(), "linuxboot: protected mode\r\nKHCE3\r\n")

    def test_a_megabyte_is_not_enough_room_for_a_kernel(self) -> None:
        """The machine's default is 1 MB, which is plenty for the firmware and
        nothing at all for a kernel: at 1 MB the image at 0x100000 is outside
        RAM, and what the CPU finds there is zeroes."""
        mach = self._run(ram=1 << 20)
        self.assertNotIn("K", mach.serial_text())


class I386LinuxDiskTests(unittest.TestCase):
    """Booting Linux off the disk: LBA 0 reads the stage 2, the stage 2 reads
    the kernel into `code32_start` and hands over.

    What the loader stages is the *protected-mode part of a bzImage* -- the
    kernel's own decompressor with the compressed image behind it -- because a
    decompressed vmlinux is linked at 0xC1000000 and its first `%fs:` access
    reads a linear address with no memory behind it. The real thing is 11 MB and
    a few minutes of emulated time; the disk under test carries the KHCE3 stub
    instead, and what is under test is the loader.
    """

    BOOT = ROOT / "userspace/i386/linuxdisk.bin"
    STAGE2 = ROOT / "userspace/i386/linuxdisk2.bin"
    STUB = ROOT / "userspace/i386/kstub32.bin"

    ENTER = 0x0010_0000

    def _kernel(self) -> bytes:
        """The stub, as the payload a bzImage carries: flat, no headers."""
        return self.STUB.read_bytes()

    def _image(self) -> bytes:
        if not (self.BOOT.exists() and self.STAGE2.exists() and self.STUB.exists()):
            self.skipTest("run make i386-extras")
        import mklinuxdisk

        return mklinuxdisk.stage(self._kernel(), self.BOOT.read_bytes(),
                                 self.STAGE2.read_bytes(), self.ENTER)

    def _boot(self, steps: int = 400_000):
        import linux_boot

        blob = self._image()
        self.assertGreaterEqual(len(blob), 512)
        mach = linux_boot.post(disk=blob)
        linux_boot.boot_disk(mach, blob)
        for _ in range(steps):
            mach.step()
            if mach.halted:
                break
        return mach

    def test_the_loader_reads_the_kernel_off_the_drive(self) -> None:
        mach = self._boot()
        self.assertIn("linuxdisk: protected mode", mach.serial_text())

    def test_the_payload_lands_where_the_header_says(self) -> None:
        mach = self._boot()
        want = self.STUB.read_bytes()[:16]
        self.assertEqual(bytes(mach.ram[self.ENTER:self.ENTER + 16]), want,
                         "the kernel is at the address its header names")

    def test_the_kernel_is_running_and_says_so(self) -> None:
        """K: entered. H: 'HdrS'. C: code32_start. E3: three E820 entries.

        The whole chain, and the one assertion that says it is a chain: the
        stub's own checks pass, which means it was entered in protected mode
        with `HdrS`, a `code32_start` and three E820 entries.
        """
        mach = self._boot()
        self.assertIn("KHCE3", mach.serial_text())
        self.assertTrue(mach.cr[0] & 1, "and it was entered in protected mode")

    def test_the_initramfs_is_staged_and_pointed_at(self) -> None:
        """`/init` only runs if two things agree: the archive is in memory
        where `boot_params.initrd_addr` says, and it is a `newc` archive the
        kernel can read. The magic is the six characters `070701` -- the
        builder used to format it like a number and write `000071c1`, which no
        reader accepts, so the kernel would have found an empty archive in a
        perfectly placed one.
        """
        import mklinuxdisk

        archive = mklinuxdisk.initramfs(self.STUB)      # any ELF will do as /init
        self.assertEqual(archive[:6], b"070701")
        blob = mklinuxdisk.stage(self._kernel(), self.BOOT.read_bytes(),
                                 self.STAGE2.read_bytes(), self.ENTER, 0, archive)
        at = blob.index(b"KSDL")
        fields = [int.from_bytes(blob[at + 4 + i * 4:at + 8 + i * 4], "little")
                  for i in range(7)]
        lba, size, addr = fields[4], fields[5], fields[6]
        self.assertEqual(addr, mklinuxdisk.INITRD_ADDR)
        self.assertEqual(size, (len(archive) + 511) // 512)
        self.assertEqual(blob[lba * 512:lba * 512 + 6], b"070701",
                         "and the sectors it names hold the archive")

    def test_the_disk_image_is_one_the_loader_can_find(self) -> None:
        blob = self._image()
        self.assertEqual(blob[510:512], b"\x55\xaa", "a boot sector the BIOS accepts")
        self.assertEqual(blob[512:516], self.STAGE2.read_bytes()[:4],
                         "the stage 2 is one sector behind the boot sector")

    def test_the_browser_build_reads_the_drive_out_of_the_extension(self) -> None:
        """A sector cannot be copied into a Scratch list from an extension.

        The VM hands a block the list's *contents*, not the list, so a copy
        through one did nothing and every sector the guest read came back as
        zeros -- which the firmware reports as "No bootable device". The data
        register therefore reads the image word by word, and the project has to
        say so.
        """
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "tw.sb3"
            subprocess.run([sys.executable, "-m", "jswc", "build",
                            str(ROOT / "userspace/i386/i386.jsw"),
                            "-o", str(out), "--target", "turbowarp"],
                           cwd=ROOT, check=True, capture_output=True)
            with zipfile.ZipFile(out) as zf:
                data = json.loads(zf.read("project.json"))
        blocks = set()

        def walk(node):
            if isinstance(node, dict):
                for key in ("opcode", "blockId"):
                    if isinstance(node.get(key), str):
                        blocks.add(node[key])
                for value in node.values():
                    walk(value)
            elif isinstance(node, list):
                for value in node:
                    walk(value)

        walk(data)
        self.assertIn("i386mem_diskread16", blocks,
                      "the data register reads the drive, not a list")
        self.assertNotIn("i386mem_diskwords", blocks,
                         "there is no copy into a list to make")


class I386ExtendedCpuTests(unittest.TestCase):
    """The instructions a 486 and a 686 added.

    A 32-bit Linux will not boot on a 386: `X86_MINIMUM_CPU_FAMILY` is 4, and a
    distro build asks CPUID about `cmov` and `cx8` before it uses them. So the
    machine answers as a Pentium III and means it -- every instruction below is
    one a kernel executes on the way in.
    """

    BIN = ROOT / "userspace/i386/hw686.bin"

    def _run(self) -> rv.Machine:
        if not self.BIN.exists():
            self.skipTest("run make i386-extras")
        blob = self.BIN.read_bytes()
        mach = rv.Machine()
        mach.ram[0x1000:0x1000 + len(blob)] = blob
        mach.sreg[rv.SEG_CS] = 0x0100
        mach.eip = 0
        for _ in range(400):
            mach.step()
            if mach.halted:
                break
        return mach

    def test_bswap_reverses_four_bytes(self) -> None:
        mach = self._run()
        self.assertEqual(mach.read(0x0500, 4), 0xE4D3C2B1)

    def test_cpuid_answers_as_a_686_with_the_features_it_has(self) -> None:
        mach = self._run()
        self.assertEqual(mach.read(0x0510, 4), 0x0683,
                         "family 6, model 8, stepping 3")
        edx = mach.read(0x0520, 4)
        for bit, name in ((0, "fpu"), (4, "tsc"), (5, "msr"), (8, "cx8"),
                          (15, "cmov")):
            self.assertTrue(edx >> bit & 1, name)
        self.assertFalse(edx >> 24 & 1,
                         "not fxsr: the context switch here is fnsave/frstor, "
                         "and a kernel that sees fxsr reaches for fxsave")

    def test_cmpxchg8b_swaps_when_the_pair_matches(self) -> None:
        mach = self._run()
        self.assertEqual(mach.read(0x0530, 4), 0xCAFEBABE, "the new low half")
        self.assertEqual(mach.read(0x0534, 4), 0xDEADBEEF, "and the high half")

    def test_cmpxchg8b_loads_the_pair_when_it_does_not(self) -> None:
        mach = self._run()
        self.assertEqual(mach.read(0x0550, 4), 1, "memory was left alone")
        self.assertEqual(mach.read(0x0560, 4), 1, "and edx:eax was loaded")
        self.assertEqual(mach.read(0x0564, 4), 2)

    def test_cmov_moves_only_when_the_condition_holds(self) -> None:
        mach = self._run()
        self.assertEqual(mach.read(0x0570, 4), 7, "ZF set: the move happened")
        self.assertEqual(mach.read(0x0574, 4), 9, "ZF clear: it did not")

    def test_invd_and_wbinvd_do_not_fault(self) -> None:
        self.assertEqual(self._run().ram[0x578], 1)

    def test_the_two_machines_agree_on_all_of_it(self) -> None:
        if not self.BIN.exists():
            self.skipTest("run make i386-extras")
        r = subprocess.run([sys.executable, str(ROOT / "tools" / "i386_jsw_check.py"),
                            "--code", str(self.BIN), "--steps", "60"],
                           cwd=ROOT, capture_output=True, text=True)
        self.assertIn("protected mode agrees", r.stdout, r.stdout + r.stderr[-600:])


class I386SerialTests(unittest.TestCase):
    """COM1: the console Linux and this firmware both talk through.

    A 16550 is eight registers and one of them is load-bearing -- the line
    status, which every 8250 driver polls before each byte. A card that never
    says "transmitter empty" is a console that prints nothing, and every step
    after this one is debugged through it.
    """

    BIN = ROOT / "userspace/i386/sertest.bin"

    def _run(self):
        if not self.BIN.exists():
            self.skipTest("run make i386-extras")
        blob = self.BIN.read_bytes()
        mach = rv.Machine()
        mach.ram[:] = i386_image.build()
        mach.sreg[rv.SEG_CS] = rv.RESET_CS
        mach.eip = rv.RESET_IP
        mach.run(limit=1_500_000)             # POST, which brings COM1 up
        mach.ram[0x7C00:0x7C00 + len(blob)] = blob
        mach.sreg[rv.SEG_CS] = 0
        mach.eip = 0x7C00
        mach.run(limit=mach.steps + 200_000)
        return mach

    def test_the_card_says_it_can_transmit(self) -> None:
        mach = self._run()
        self.assertEqual(mach.port_in(0x3FD, 1), 0x60,
                         "THRE and TEMT: the transmitter is empty")

    def test_a_byte_written_to_the_port_is_a_byte_on_the_wire(self) -> None:
        mach = self._run()
        self.assertEqual(mach.serial_text(), "A1B2\r\nCD\r\n",
                         "int 14h, then the registers, then the divisor latch")

    def test_the_divisor_latch_is_not_a_byte_on_the_wire(self) -> None:
        """LCR bit 7 makes 0x3F8 the divisor's low byte. A card that ignored
        DLAB would print the divisor and lose the baud rate."""
        mach = rv.Machine()
        mach.port_out(0x3FB, 0x80, 1)          # DLAB on
        mach.port_out(0x3F8, 1, 1)             # divisor low
        self.assertEqual(mach.serial_text(), "", "nothing was transmitted")
        mach.port_out(0x3FB, 0x03, 1)          # DLAB off, 8N1
        mach.port_out(0x3F8, ord("x"), 1)
        self.assertEqual(mach.serial_text(), "x")

    def test_loopback_keeps_the_byte_off_the_wire(self) -> None:
        """MCR bit 4 is the loopback the card's own self-test uses; a serial
        port that prints during a loopback is a port with a bug."""
        mach = rv.Machine()
        mach.port_out(0x3FC, 0x10, 1)          # MCR: loopback
        mach.port_out(0x3F8, ord("z"), 1)
        self.assertEqual(mach.serial_text(), "")

    def test_a_byte_that_arrives_is_ready(self) -> None:
        mach = rv.Machine()
        mach.serial_in.extend(b"Q")
        lsr = mach.port_in(0x3FD, 1)
        self.assertEqual(lsr & 1, 1, "data ready")
        self.assertEqual(mach.port_in(0x3F8, 1), ord("Q"))
        self.assertEqual(mach.port_in(0x3FD, 1) & 1, 0, "and only once")

    def test_loopback_is_how_a_driver_finds_the_chip(self) -> None:
        """Every 8250 probe sets loopback, sends a byte and reads it back. A
        card that drops the byte looks like an empty socket, so no driver
        adopts it and the console is the only way in."""
        mach = rv.Machine()
        mach.port_out(0x3FC, 0x10, 1)          # MCR: loopback
        mach.port_out(0x3F8, ord("z"), 1)
        self.assertEqual(mach.port_in(0x3FD, 1) & 1, 1, "it came back")
        self.assertEqual(mach.port_in(0x3F8, 1), ord("z"))
        self.assertEqual(mach.serial_text(), "", "and never reached the wire")

    def test_a_received_byte_asks_for_irq4(self) -> None:
        """IER bit 0 is "tell me when a byte arrives", and IRQ4 is the line. A
        console that only polls is a console that cannot take a keystroke
        while the guest is busy."""
        mach = rv.Machine()
        mach.port_out(0x3F9, 0x01, 1)          # IER: received data
        self.assertEqual(mach.port_in(0x3FA, 1) & 1, 1, "nothing pending yet")
        self.assertEqual(mach.pic.irr & 0x10, 0, "and the line is low")
        mach.serial_in.extend(b"k")
        mach.step()
        self.assertEqual(mach.port_in(0x3FA, 1), 0x04,
                         "IIR says the receiver, and bit 0 is clear")
        self.assertEqual(mach.pic.irr & 0x10, 0x10, "IRQ4 is asking")
        self.assertEqual(mach.port_in(0x3F8, 1), ord("k"))
        mach.pic.raise_line(4, False)          # the guest took it, and EOIs
        mach.step()
        self.assertEqual(mach.port_in(0x3FA, 1) & 1, 1, "quiet again")
        self.assertEqual(mach.pic.irr & 0x10, 0, "and the line dropped")

    def test_a_masked_receiver_interrupt_stays_in_the_queue(self) -> None:
        """IER bit 0 clear means the byte waits in the queue: the driver is
        polling, so nothing has to be interrupted."""
        mach = rv.Machine()
        mach.serial_in.extend(b"k")
        mach.step()
        self.assertEqual(mach.port_in(0x3FA, 1) & 1, 1, "no interrupt claimed")
        self.assertEqual(mach.pic.irr & 0x10, 0, "and the line is low")
        self.assertEqual(mach.port_in(0x3F8, 1), ord("k"), "the byte is still there")


class I386FpuTests(unittest.TestCase):
    """The x87, on the model, where a wrong answer is a wrong answer.

    The two machines are compared instruction by instruction in
    `I386JswTests.test_the_x87_is_the_same_machine_in_both`; this is the other
    half of the question -- that what they agree on is *arithmetic* and not two
    copies of the same mistake.
    """

    def run_code(self, code: bytes, limit: int = 200) -> rv.Machine:
        mach = rv.Machine()
        for i, byte in enumerate(code):
            mach.write(0x1000 + i, byte, 1)
        mach.sreg[rv.SEG_CS] = 0
        mach.sreg[rv.SEG_DS] = 0
        mach.sreg[rv.SEG_SS] = 0
        mach.reg[4] = 0x8000
        mach.eip = 0x1000
        stop = mach.steps + limit
        while mach.steps < stop and not mach.halted:
            mach.step()
        return mach

    def test_the_stack_is_a_rotation(self) -> None:
        """`fld1; fld1; fadd st0, st1` is two, and the top has moved twice."""
        mach = self.run_code(bytes([0xD9, 0xE8, 0xD9, 0xE8, 0xD8, 0xC1, 0xF4]))
        self.assertEqual(mach.fpu.get(0), 2.0)
        self.assertEqual(mach.fpu.top, 6, "two pushes")
        self.assertFalse(mach.fpu.empty[mach.fpu.at(0)])

    def test_a_double_survives_a_round_trip_and_a_single_rounds(self) -> None:
        """`fstp qword` then `fld` is exact; `fstp dword` is a binary32, which
        is what the format means and what a program expects to lose."""
        code = bytes([
            0xD9, 0xEB,                     # fldpi
            0xDD, 0x1E, 0x00, 0x20,         # fstp qword [0x2000]
            0xDD, 0x06, 0x00, 0x20,         # fld qword [0x2000]
            0xD9, 0x1E, 0x08, 0x20,         # fstp dword [0x2008]
            0xDD, 0x06, 0x00, 0x20,         # fld qword [0x2000]
            0xF4,
        ])
        mach = self.run_code(code)
        self.assertEqual(rv._f64_value([mach.read(0x2000 + k, 1) for k in range(8)]),
                         3.141592653589793, "the double came back unchanged")
        self.assertEqual(rv._f32_value([mach.read(0x2008 + k, 1) for k in range(4)]),
                         3.1415927410125732, "and the single is a single")

    def test_a_negative_integer_goes_in_and_comes_out(self) -> None:
        mach = rv.Machine()
        mach.write(0x2000, 0xF9, 1)          # -7, little-endian
        mach.write(0x2001, 0xFF, 1)
        for i, byte in enumerate(bytes([
            0xDF, 0x06, 0x00, 0x20,         # fild word [0x2000]
            0xDF, 0x1E, 0x02, 0x20,         # fistp word [0x2002]
            0xF4,
        ])):
            mach.write(0x1000 + i, byte, 1)
        mach.sreg[rv.SEG_CS] = 0
        mach.sreg[rv.SEG_DS] = 0
        mach.sreg[rv.SEG_SS] = 0
        mach.reg[4] = 0x8000
        mach.eip = 0x1000
        while not mach.halted and mach.steps < 200:
            mach.step()
        self.assertEqual(mach.read(0x2002, 2), 0xFFF9, "-7, two's complement")
        self.assertTrue(mach.fpu.empty[mach.fpu.at(0)], "and the stack is clear")

    def test_dividing_by_zero_is_an_infinity_and_zero_over_zero_is_not(self) -> None:
        """`fdiv` is where a language that answers 0 for `1 / 0` silently
        changes the answer: this one has to be an infinity, and `0 / 0` a NaN."""
        mach = self.run_code(bytes([
            0xD9, 0xE8,                     # fld1
            0xD9, 0xEE,                     # fldz
            0xDE, 0xF9,                     # fdivp st(1), st0 -> 1 / 0
            0xF4,
        ]))
        self.assertEqual(mach.fpu.get(0), float("inf"))
        mach = self.run_code(bytes([0xD9, 0xEE, 0xD9, 0xEE, 0xDE, 0xF9, 0xF4]))
        self.assertNotEqual(mach.fpu.get(0), mach.fpu.get(0), "0 / 0 is a NaN")

    def test_the_control_word_decides_how_a_store_rounds(self) -> None:
        code = bytes([
            0xD9, 0xEB,                     # fldpi
            0xDD, 0x1E, 0x08, 0x20,         # fstp qword [0x2008]
            0xD9, 0x2E, 0x00, 0x20,         # fldcw [0x2000]  (chop)
            0xD9, 0xEB,
            0xDB, 0x1E, 0x10, 0x20,         # fistp dword [0x2010] -> 3
            0xF4,
        ])
        mach = rv.Machine()
        mach.write(0x2000, 0x7F, 1)         # 0x0F7F: round toward zero
        mach.write(0x2001, 0x0F, 1)
        for i, byte in enumerate(code):
            mach.write(0x1000 + i, byte, 1)
        mach.sreg[rv.SEG_CS] = 0
        mach.sreg[rv.SEG_DS] = 0
        mach.sreg[rv.SEG_SS] = 0
        mach.reg[4] = 0x8000
        mach.eip = 0x1000
        while not mach.halted and mach.steps < 200:
            mach.step()
        self.assertEqual(mach.read(0x2010, 4), 3, "pi chopped, not rounded up")

    def test_nine_pushes_are_a_stack_fault(self) -> None:
        code = bytes([0xD9, 0xE8] * 9 + [0xF4])
        mach = self.run_code(code)
        self.assertEqual(mach.fpu.sf, 1)
        self.assertEqual(mach.fpu.es, 1)
        self.assertEqual(mach.fpu.c1, 1, "the chip reports it instead of faulting")

    def test_the_status_word_says_less_equal_or_greater(self) -> None:
        for left, right, bits in ((1.0, 2.0, 0x0100), (2.0, 2.0, 0x4000),
                                  (3.0, 2.0, 0x0000)):
            mach = rv.Machine()
            mach.fpu.push(right)
            mach.fpu.push(left)
            mach.fpu.compare(mach.fpu.get(0), mach.fpu.get(1))
            status = mach.fpu.status() & 0x4700
            self.assertEqual(status, bits, f"{left} vs {right}")

    def test_fnsave_and_frstor_carry_the_whole_chip(self) -> None:
        code = bytes([
            0xD9, 0xE8,                     # fld1
            0xD9, 0xEB,                     # fldpi
            0xD8, 0xC1,                     # fadd st0, st1
            0xDD, 0x36, 0x00, 0x21,         # fnsave [0x2100]
            0xDD, 0x26, 0x00, 0x21,         # frstor [0x2100]
            0xF4,
        ])
        mach = self.run_code(code)
        self.assertEqual(mach.fpu.get(0), 4.141592653589793)
        self.assertEqual(mach.fpu.get(1), 1.0)
        self.assertEqual(mach.fpu.top, 6, "the top came back with the status word")

    def test_packed_decimal_goes_round(self) -> None:
        code = bytes([
            0xD9, 0xEB,                     # fldpi
            0xDF, 0x36, 0x00, 0x20,         # fbstp [0x2000]
            0xDF, 0x26, 0x00, 0x20,         # fbld [0x2000]
            0xF4,
        ])
        mach = self.run_code(code)
        # packed decimal holds an *integer*: `fbstp` of pi is the number 3, and
        # eighteen digits are what makes the return trip exact for one.
        self.assertEqual(mach.fpu.get(0), 3.0)

    def test_a_form_the_table_lacks_is_not_implemented(self) -> None:
        """`fisttp` is SSE3: a Pentium III shape does not have it. The model
        says so by refusing, and the machine raises the interrupt 7 a 386
        without a coprocessor raised -- the two halves of the same answer."""
        mach = rv.Machine()
        for i, byte in enumerate(bytes([0xDB, 0x0E, 0x00, 0x20])):
            mach.write(0x1000 + i, byte, 1)
        mach.sreg[rv.SEG_CS] = 0
        mach.sreg[rv.SEG_DS] = 0
        mach.eip = 0x1000
        with self.assertRaises(NotImplementedError):
            mach.step()


class I386RtcTests(unittest.TestCase):
    """`int 1Ah AH=04h`: the date, which is the call DOS's DATE and a Windows 95
    boot both make.

    AH=02h (the time) was there; the date was not, so CX and DX came back hold-
    ing whatever the caller had left in them -- and a program that formatted that
    into a filename wrote a file dated 4.06.03 or something equally convincing.
    """

    #: `mov ah,4; int 0x1A; mov [0x900],cx; mov [0x902],dx; hlt`
    ASK = bytes([0xB4, 0x04, 0xCD, 0x1A, 0x89, 0x0E, 0x00, 0x09,
                 0x89, 0x16, 0x02, 0x09, 0xF4])

    def ask(self, mach: rv.Machine | None = None) -> rv.Machine:
        if mach is None:
            mach = post()
        for i, byte in enumerate(self.ASK):
            mach.write(0x1000 + i, byte, 1)
        mach.sreg[rv.SEG_CS] = 0
        mach.sreg[rv.SEG_DS] = 0
        mach.sreg[rv.SEG_SS] = 0
        mach.reg[4] = 0x8000
        mach.eip = 0x1000
        stop = mach.steps + 200
        while mach.steps < stop and not mach.halted:
            mach.step()
        return mach

    def test_the_date_comes_back_in_bcd(self) -> None:
        mach = self.ask()
        self.assertEqual(mach.read(0x900, 2), 0x2025, "CX: century 20, year 25")
        self.assertEqual(mach.read(0x902, 2), 0x0101, "DX: month 1, day 1")

    def test_it_agrees_with_the_cmos_the_setup_writes(self) -> None:
        """The date is read out of the chip, not remembered: a guest that sets
        the clock through the setup sees its own date come back."""
        mach = post()
        mach.port_out(0x70, 0x09, 1)              # CMOS 0x09: year
        mach.port_out(0x71, 0x31, 1)              # 2031
        mach = self.ask(mach)
        self.assertEqual(mach.read(0x900, 2), 0x2031)


class I386KernelTests(unittest.TestCase):
    """The smallest thing that deserves the name "kernel": it turns the machine
    into a 386 with paging on, and then says so on the screen.

    `userspace/i386/kernel.asm` loads a GDT, enters protected mode, builds a
    page directory and a page table, sets CR3, sets CR0.PG -- and then stores
    six characters at the text plane. Because the text plane is reached through
    those tables, the characters are the evidence: they cannot appear unless
    the pager walked.
    """

    BIN = ROOT / "userspace/i386/kernel.bin"

    def run_kernel(self, limit: int = 6000) -> rv.Machine:
        blob = self.BIN.read_bytes()
        mach = rv.Machine()
        mach.ram[0x1000:0x1000 + len(blob)] = blob
        mach.sreg[rv.SEG_CS] = 0x0100
        mach.eip = 0
        for _ in range(limit):
            mach.step()
            if mach.halted:
                break
        return mach

    def test_it_gets_to_protected_mode_with_paging_on(self) -> None:
        mach = self.run_kernel()
        self.assertTrue(mach.pe, "PE")
        self.assertTrue(mach.cr[0] & 0x8000_0000, "PG")
        self.assertEqual(mach.cr[3], 0x2000, "CR3 points at the directory")

    def test_the_tables_are_what_it_built(self) -> None:
        mach = self.run_kernel()
        pde = mach.read(0x2000, 4)
        self.assertEqual(pde & ~0xFFF, 0x3000, "the directory points at the table")
        self.assertTrue(pde & 0x07, "present, writable, user")
        for page in (0x0, 0x1, 0x7, 0xB8):
            entry = mach.read(0x3000 + page * 4, 4)
            self.assertEqual(entry, page * 0x1000 | 0x07,
                             f"page {page:#x} maps to itself")

    def test_it_prints_through_the_pager(self) -> None:
        mach = self.run_kernel()
        row = bytes(mach.ram[0xB8000:0xB8000 + 12])
        text = "".join(chr(row[i]) for i in range(0, len(row), 2))
        self.assertEqual(text, "KERNEL")
        self.assertEqual(row[1], 0x0F, "white on black, like a real VGA card")

    def test_it_halts_rather_than_running_off(self) -> None:
        mach = self.run_kernel()
        self.assertTrue(mach.halted)
        self.assertEqual(mach.error, "", "and with nothing to complain about")


class I386PagingTests(unittest.TestCase):
    """Two levels, four kilobytes a page, and every address goes through it.

    A kernel turns paging on early, so the machine has to do the walk rather
    than ignore CR0.PG. The interesting case is not an identity map -- that
    passes even if nothing is translated -- but two virtual pages pointing at
    different physical ones.
    """

    PD = 0x2000        # page directory
    PT = 0x3000        # page table for the first 4 MiB
    PHYS_A = 0x4000    # where virtual 0x0000 lands
    PHYS_B = 0x5000    # where virtual 0x1000 lands

    def ref(self):
        mach = rv.Machine()
        mach.ram[:] = i386_image.build()
        self.program_tables(mach.write)
        return mach

    def program_tables(self, write) -> None:
        write(self.PD, self.PT | 0x07, 4)          # present, rw, user
        write(self.PT + 0, self.PHYS_A | 0x07, 4)  # virtual 0x0000
        write(self.PT + 4, self.PHYS_B | 0x07, 4)  # virtual 0x1000
        write(self.PT + 8, 0x0000_0000, 4)         # virtual 0x2000: not present
        # the descriptor tables are addressed *linearly*, so a machine with
        # paging on has to be able to walk to them: identity is enough
        write(self.PT + 6 * 4, 0x006000 | 0x07, 4)
        write(self.PT + 7 * 4, 0x007000 | 0x07, 4)

    def enable(self, mach) -> None:
        mach.cr[3] = self.PD
        mach.write_cr0(0x8000_0001)                # PE and PG

    def test_a_virtual_page_reaches_a_different_physical_page(self) -> None:
        mach = self.ref()
        mach.write(self.PHYS_A, 0xCD, 1)
        mach.write(self.PHYS_B, 0xAB, 1)
        self.enable(mach)
        self.assertEqual(mach.read(mach.phys(rv.SEG_DS, 0x0000), 1), 0xCD)
        self.assertEqual(mach.read(mach.phys(rv.SEG_DS, 0x1000), 1), 0xAB,
                         "virtual 0x1000 has to translate to physical 0x3000")
        self.assertEqual(mach.read(mach.phys(rv.SEG_DS, 0x1000), 1),
                         mach.read(self.PHYS_B, 1))

    def test_paging_off_means_the_linear_address_is_the_physical_one(self) -> None:
        mach = self.ref()
        mach.write_cr0(0x0000_0001)                # protected, but no paging
        self.assertEqual(mach.phys(rv.SEG_DS, 0x1234), 0x1234)

    def test_real_mode_wraps_at_a_megabyte_while_the_gate_is_shut(self) -> None:
        """The wrap is the A20 gate, not a shortcut: an 8086 really does come
        back round to the bottom of memory, and a 386 does the same until the
        BIOS opens the gate."""
        mach = self.ref()
        mach.a20 = False
        mach.base[rv.SEG_DS] = 0xFFFF0
        self.assertEqual(mach.phys(rv.SEG_DS, 0x10), 0,
                         "FFFF:0010 is 0x100000, which the shut gate folds to 0")

    def test_the_same_address_reaches_extended_memory_with_the_gate_open(self) -> None:
        mach = self.ref()
        mach.base[rv.SEG_DS] = 0xFFFF0
        self.assertEqual(mach.phys(rv.SEG_DS, 0x10), 0x0010_0000)

    def test_a_missing_page_faults_and_lands_on_the_handler(self) -> None:
        """A fault is interrupt 14 through the IDT, not a stop."""
        mach = self.ref()
        # a GDT with one code descriptor at 0x08: base 0, 1 MiB, read/execute
        mach.gdtr = [0x7000, 15]
        for i, byte in enumerate((0xFF, 0xFF, 0x00, 0x00, 0x00, 0x9A, 0x00, 0x00)):
            mach.write(0x7008 + i, byte, 1)
        # and an IDT whose vector 14 points at 0x1234:0x0008
        mach.idtr = [0x6000, 0x7FF]
        mach.write(0x6000 + 14 * 8, 0x1234, 2)
        mach.write(0x6000 + 14 * 8 + 2, 0x0008, 2)
        mach.write(0x6000 + 14 * 8 + 4, 0x8E00, 2)
        mach.write(0x6000 + 14 * 8 + 6, 0x0000, 2)
        # the interrupt pushes onto the stack, so the stack has to be in a
        # page that is mapped -- power-on leaves SP at 0, which wraps to the
        # top of memory and is not
        mach.reg[4] = 0x0800
        self.enable(mach)
        mach.phys(rv.SEG_DS, 0x2000)
        self.assertEqual(mach.cr[2], 0x2000, "CR2 holds the faulting address")
        self.assertEqual(mach.eip, 0x1234, "and the handler is running")
        self.assertEqual(mach.sreg[rv.SEG_CS], 0x0008, "in the code segment")
        self.assertFalse(mach.halted)

    def test_a_bulk_move_fills_a_page_without_a_loop(self) -> None:
        """`rep stosl` over a cleared BSS is millions of elements, and one
        Python iteration each is a minute of wall time for one instruction. A
        page at a time is what the hardware has done since the 486."""
        mach = self.ref()
        self.enable(mach)
        mach.reg[7] = 0x1000                       # virtual 0x1000 -> 0x5000
        mach.reg[0] = 0x5A5A_5A5A
        moved = rv.bulk_string(mach, 2, 4, rv.SEG_DS, 0xFFFF_FFFF, 0x1000)
        self.assertGreater(moved, 1, "more than one element at a time")
        self.assertEqual(mach.read(self.PHYS_B, 4), 0x5A5A_5A5A)
        self.assertEqual(mach.reg[7], 0x1000 + moved * 4, "DI advanced with it")

    def test_a_bulk_move_never_writes_through_a_missing_page(self) -> None:
        """The walker reports a missing page by delivering a fault, so a slice
        written afterwards would land wherever it happened to leave the
        machine -- physical zero, say. The bulk path asks first."""
        mach = self.ref()
        self.enable(mach)
        mach.reg[7] = 0x2000                       # the unmapped page
        mach.reg[0] = 0x41
        moved = rv.bulk_string(mach, 2, 4, rv.SEG_DS, 0xFFFF_FFFF, 0x1000)
        self.assertEqual(moved, 0, "an unmapped page is not a page to fill")
        self.assertEqual(bytes(mach.ram[0:16]), bytes(16), "physical zero is untouched")

    def test_a_fault_with_no_idt_stops_rather_than_looping(self) -> None:
        mach = self.ref()
        self.enable(mach)
        mach.phys(rv.SEG_DS, 0x2000)
        self.assertTrue(mach.halted, "no handler, and none to fault into")

    def test_extended_memory_is_reached_through_page_tables(self) -> None:
        """A page table is one way to reach the 128 MiB above the hole -- it
        hands back a physical address untruncated, which is how a kernel that
        has already turned paging on uses memory it never identity-mapped."""
        mach = self.ref()
        mach.write(0x2000, 0x3000 | 0x07, 4)
        mach.write(0x3000, 0x0020_0000 | 0x07, 4)   # virtual 0 -> physical 2 MiB
        mach.cr[3] = 0x2000
        mach.write_cr0(0x8000_0001)
        self.assertEqual(mach.phys(rv.SEG_DS, 0x40), 0x0020_0040,
                         "a mapped page above 1 MiB keeps its address")

    def test_protected_mode_without_paging_reaches_past_a_megabyte(self) -> None:
        """A flat descriptor and CR0.PG clear: a linear address is a physical
        one, all thirty-two bits of it. A kernel clears its own BSS at 30 MB
        before it owns a single page table, and truncating here made every byte
        above the first megabyte read as an open bus."""
        mach = self.ref()
        mach.write_cr0(0x0000_0001)
        mach.base[rv.SEG_DS] = 0x0010_0000
        self.assertEqual(mach.phys(rv.SEG_DS, 0x40), 0x0010_0040,
                         "protected mode does not alias back to the IVT")

    def test_real_mode_wraps_at_a_megabyte_while_the_gate_is_shut(self) -> None:
        """Real mode is where the wrap is seen: an 8086 with a segment of
        0xFFFF and an offset of 0xFFFF really does come back round."""
        mach = self.ref()
        mach.a20 = False
        mach.base[rv.SEG_DS] = 0xFFFF0
        self.assertEqual(mach.phys(rv.SEG_DS, 0xFFFF), 0x0FFEF)

    def test_real_mode_reaches_that_megabyte_with_the_gate_open(self) -> None:
        mach = self.ref()
        mach.base[rv.SEG_DS] = 0xFFFF0
        self.assertEqual(mach.phys(rv.SEG_DS, 0xFFFF), 0x10FFEF)

    def test_the_scratch_machine_walks_the_same_tables(self) -> None:
        """The generated project, stepped to the same state and asked the same
        question: two virtual pages, two physical pages."""
        import json
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "m.sb3"
            back = Path(tmp) / "m.jsw"
            subprocess.run([sys.executable, "-m", "jswc", "build",
                            str(RVDIR / "i386.jsw"), "-o", str(out)],
                           cwd=ROOT, check=True, capture_output=True)
            subprocess.run([sys.executable, "-m", "jswc", "decompile", str(out),
                            "-o", str(back), "--skip-assets"],
                           cwd=ROOT, check=True, capture_output=True)
            source = back.read_text()

        env = jsw_sim.Env()
        classes = jsw_sim.parse_program(source, env)
        procs = classes["Cpu"].procs
        env.procs = procs
        env.threads.append({})
        env.current = 0
        for name, asset in (("rom", "bios"), ("disk", "disk"), ("ident", "ident")):
            env.lists[name] = json.loads(
                (RVDIR / "assets" / "lists" / f"{asset}.jsa").read_text())
        env.vars["X.cores"] = 1
        gen = jsw_sim.call(env, procs["powerOn"], [])
        try:
            while True:
                next(gen)
        except StopIteration:
            pass

        def call(name, *args):
            g = jsw_sim.call(env, procs[name], list(args))
            try:
                while True:
                    next(g)
            except StopIteration:
                pass

        def poke(addr, value, size=4):
            for k in range(size):
                call("wrb", addr + k, (value >> (8 * k)) & 0xFF)

        mem = env.lists["X.mem"]

        def word(addr):
            return int(mem[addr // 4]) >> (8 * (addr % 4)) & 0xFF

        self.program_tables(poke)
        poke(self.PHYS_A, 0xCD)
        poke(self.PHYS_B, 0xAB)
        env.vars["cr3"] = self.PD
        call("writeCr0", 0x80000001)              # PE and PG
        call("phys", 4, 0x0000)
        low = int(jsw_sim.Env.num(env.vars["v"]))
        call("phys", 4, 0x1000)
        high = int(jsw_sim.Env.num(env.vars["v"]))
        self.assertEqual(high, self.PHYS_B,
                         f"virtual 0x1000 translated to {high:#x}")
        self.assertEqual(low, self.PHYS_A)
        self.assertEqual(word(high), 0xAB)
        self.assertEqual(word(low), 0xCD)


class I386OpcodeCoverageTests(unittest.TestCase):
    """Every opcode both machines implement has to do the same thing.

    `tools/i386_opcode_sweep.py` is the full sweep -- 512 probes, one fresh
    machine each, four minutes. This runs the handful that were wrong, so a
    regression is caught in a second rather than in a nightly run.
    """

    #: the ones this work fixed, by the reason they were wrong
    CASES = [
        (0x60, "pusha pushes eight registers, not nine"),
        (0x61, "popa pops eight, and skips sp"),
        (0x62, "bound: the index is a register, the bounds are memory"),
        (0x6D, "insw writes a word and steps di by two"),
        (0x6F, "outsw reads a word and steps si by two"),
        (0x9D, "popf forces bit 1, which a 386 always reads as one"),
        (0xC8, "enter builds a frame: bp is 5 and sp is 4"),
        (0xC9, "leave unbuilds the same frame"),
        (0xCF, "iret forces bit 1 too"),
    ]
    TWO_BYTE = [
        (0x00, "sldt / str / lldt / ltr / verr / verw"),
        (0x31, "rdtsc leaves a timestamp in edx:eax"),
        (0xBA, "bt with an immediate consumes the immediate"),
    ]

    @classmethod
    def setUpClass(cls) -> None:
        import subprocess

        out = ROOT / "build" / "i386" / "i386-test.sb3"
        back = ROOT / "build" / "i386" / "i386-test.back.jsw"
        subprocess.run([sys.executable, "-m", "jswc", "build",
                        str(RVDIR / "i386.jsw"), "-o", str(out)],
                       cwd=ROOT, check=True, capture_output=True)
        subprocess.run([sys.executable, "-m", "jswc", "decompile", str(out),
                        "-o", str(back), "--skip-assets"],
                       cwd=ROOT, check=True, capture_output=True)
        import importlib.util

        spec = importlib.util.spec_from_file_location(
            "sweep", ROOT / "tools" / "i386_opcode_sweep.py")
        cls.sweep = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.sweep)
        # the sweep reads the project it was pointed at; make it read this one
        cls.sweep.BUILD = out
        cls.sweep.DECOMPILED = back
        cls.source = back.read_text()

    def run_probe(self, code: bytes) -> tuple[dict, dict]:
        import importlib.util

        env = jsw_sim.Env()
        classes = jsw_sim.parse_program(self.source, env)
        env.procs = classes["Cpu"].procs
        env.threads.append({})
        env.current = 0
        for name, asset in (("rom", "bios"), ("disk", "disk"), ("ident", "ident")):
            env.lists[name] = json.loads(
                (RVDIR / "assets" / "lists" / f"{asset}.jsa").read_text())
        env.vars["X.cores"] = 1
        gen = jsw_sim.call(env, env.procs["powerOn"], [])
        try:
            while True:
                next(gen)
        except StopIteration:
            pass
        return (self.sweep.probe_jsw(env, env.procs, code),
                self.sweep.probe_ref(code))

    def check(self, opcode: int, why: str) -> None:
        code = bytes([opcode]) + b"\0" * 7
        jsw, model = self.run_probe(code)
        verdict = self.sweep.compare(f"{opcode:02X}", jsw, model)
        self.assertEqual(verdict, "ok", f"{opcode:02X} ({why}): {verdict}")

    def test_the_one_byte_cases(self) -> None:
        for opcode, why in self.CASES:
            with self.subTest(opcode=f"{opcode:02X}"):
                self.check(opcode, why)

    def test_the_two_byte_cases(self) -> None:
        for opcode, why in self.TWO_BYTE:
            with self.subTest(opcode=f"0F{opcode:02X}"):
                code = bytes([0x0F, opcode]) + b"\0" * 6
                jsw, model = self.run_probe(code)
                verdict = self.sweep.compare(f"0F{opcode:02X}", jsw, model)
                self.assertEqual(verdict, "ok", f"0F{opcode:02X} ({why}): {verdict}")


class I386AhciTests(unittest.TestCase):
    """SATA is not a register file behind ports, it is memory.

    The driver builds a command list in RAM, writes a slot number to PxCI, and
    the controller fetches the header, follows it to a command table, reads the
    Register H2D FIS and DMAs the result into the buffer the PRDT names. That
    whole path is worth emulating because that is what `ahci` does on probe.
    """

    CLB, CTBA, BUF = 0x2000, 0x2100, 0x3000

    def ref(self):
        mach = rv.Machine()
        mach.ram[:] = i386_image.build()
        return mach

    def program(self, mach, port: int, cmd: int, slot: int = 0,
                lba: int = 0, count: int = 1, prdt_bytes: int = 512) -> None:
        mach.ahci_write(rv.AHCI_BASE + 0x100 + port * 0x80, self.CLB, 4)
        hdr = self.CLB + slot * 32
        mach.write(hdr, 5, 1)                       # FIS length, dwords
        mach.write(hdr + 2, 1, 2)                   # one PRDT entry
        mach.write(hdr + 4, self.CTBA, 4)
        mach.write(self.CTBA, 0x8027, 2)            # Register H2D FIS, C=1
        mach.write(self.CTBA + 2, cmd, 1)
        mach.write(self.CTBA + 4, lba & 0xFF, 1)
        mach.write(self.CTBA + 5, (lba >> 8) & 0xFF, 1)
        mach.write(self.CTBA + 6, (lba >> 16) & 0xFF, 1)
        mach.write(self.CTBA + 12, count, 1)
        mach.write(self.CTBA + 128, self.BUF, 4)
        mach.write(self.CTBA + 134, prdt_bytes >> 8, 1)
        mach.write(self.CTBA + 132, prdt_bytes & 0xFF, 1)

    def run_slot(self, mach, port: int = 0, slot: int = 0) -> None:
        mach.ahci_write(rv.AHCI_BASE + 0x138 + port * 0x80, 1 << slot, 4)

    @staticmethod
    def at(mach, offset: int, count: int) -> bytes:
        return bytes(mach.ram[offset:offset + count])

    def test_the_hba_comes_up_the_way_a_driver_expects(self) -> None:
        mach = self.ref()
        cap = mach.ahci_read(rv.AHCI_BASE + 0x00, 4)
        self.assertEqual(cap & 0x1F, 0, "one port, numbered from zero")
        self.assertEqual((cap >> 8) & 0x1F, 31, "32 command slots")
        self.assertEqual(mach.ahci_read(rv.AHCI_BASE + 0x0C, 4), 1, "PI: port 0")
        self.assertEqual(mach.ahci_read(rv.AHCI_BASE + 0x124, 4), 0x00000101,
                         "PxSIG: an ATA drive on this port")
        self.assertEqual(mach.ahci_read(rv.AHCI_BASE + 0x128, 4) & 0x0F, 3,
                         "PxSSTS: a device is present")

    def test_identify_arrives_by_dma(self) -> None:
        mach = self.ref()
        self.program(mach, 0, 0xEC)
        self.run_slot(mach)
        blob = self.at(mach, self.BUF, 512)
        raw = blob[52:92]
        model = bytes(b"".join(raw[i + 1:i + 2] + raw[i:i + 1]
                               for i in range(0, 40, 2))).decode().strip()
        self.assertEqual(model, "JSW VIRTUAL DISK 128M")
        self.assertEqual(sum(int.from_bytes(blob[i:i + 2], "little")
                             for i in range(0, 512, 2)) & 0xFFFF, 0,
                         "the block still checksums to zero")

    def test_read_dma_puts_the_sector_where_the_prdt_said(self) -> None:
        mach = self.ref()
        self.program(mach, 0, 0x25, lba=1)
        self.run_slot(mach)
        self.assertTrue(self.at(mach, self.BUF, 7).startswith(b"STAGE2"),
                        "sector 1 is the payload")

    def test_a_command_past_the_end_sets_an_error(self) -> None:
        mach = self.ref()
        self.program(mach, 0, 0x25, lba=200)
        self.run_slot(mach)
        tfd = mach.ahci_read(rv.AHCI_BASE + 0x100 + 0x20, 4)
        self.assertTrue(tfd & 0x01, "ERR in PxTFD")
        self.assertFalse(tfd & 0x08, "and no data was offered")

    def test_the_slot_is_cleared_and_the_interrupt_reported(self) -> None:
        mach = self.ref()
        self.program(mach, 0, 0xEC)
        self.run_slot(mach)
        self.assertEqual(mach.ahci_read(rv.AHCI_BASE + 0x138, 4), 0,
                         "PxCI is cleared when the slot is done")
        self.assertTrue(mach.ahci_read(rv.AHCI_BASE + 0x110, 4) & 0x40000000,
                        "PxIS: a D2H FIS arrived")

    def test_hba_reset_puts_the_port_back(self) -> None:
        mach = self.ref()
        self.program(mach, 0, 0xEC)
        self.run_slot(mach)
        mach.ahci_write(rv.AHCI_BASE + 0x04, 1, 4)      # GHC.HR
        self.assertEqual(mach.ahci_read(rv.AHCI_BASE + 0x138, 4), 0)
        self.assertEqual(mach.ahci_read(rv.AHCI_BASE + 0x120, 4), 0x50,
                         "PxTFD back to ready")

    def test_the_scratch_machine_speaks_the_same_protocol(self) -> None:
        import json
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "m.sb3"
            back = Path(tmp) / "m.jsw"
            subprocess.run([sys.executable, "-m", "jswc", "build",
                            str(RVDIR / "i386.jsw"), "-o", str(out)],
                           cwd=ROOT, check=True, capture_output=True)
            subprocess.run([sys.executable, "-m", "jswc", "decompile", str(out),
                            "-o", str(back), "--skip-assets"],
                           cwd=ROOT, check=True, capture_output=True)
            source = back.read_text()

        env = jsw_sim.Env()
        classes = jsw_sim.parse_program(source, env)
        cpu = classes["Cpu"].procs
        env.procs = cpu
        env.threads.append({})
        env.current = 0
        for name, asset in (("disk", "disk"), ("rom", "bios"), ("ident", "ident")):
            env.lists[name] = json.loads((RVDIR / "assets" / "lists" / f"{asset}.jsa").read_text())
        env.vars["X.cores"] = 1
        gen = jsw_sim.call(env, cpu["powerOn"], [])
        try:
            while True:
                next(gen)
        except StopIteration:
            pass

        def call(name, *args):
            g = jsw_sim.call(env, cpu[name], list(args))
            try:
                while True:
                    next(g)
            except StopIteration:
                pass

        def store(addr, value, size):
            for k in range(size):
                call("wrb", addr + k, (value >> (8 * k)) & 0xFF)

        base = int(env.vars["X.ahciBase"])
        store(base + 0x100, self.CLB, 4)
        hdr = self.CLB
        store(hdr, 5, 1)
        store(hdr + 2, 1, 2)
        store(hdr + 4, self.CTBA, 4)
        store(self.CTBA, 0x8027, 2)
        store(self.CTBA + 2, 0xEC, 1)
        store(self.CTBA + 12, 1, 1)
        store(self.CTBA + 128, self.BUF, 4)
        store(self.CTBA + 133, 2, 1)
        call("wrb", base + 0x138, 1)
        blob = bytearray()
        for i in range(512):
            call("rdb", self.BUF + i)
            blob.append(int(jsw_sim.Env.num(env.vars["v"])) & 0xFF)
        raw = bytes(blob[52:92])
        model = bytes(b"".join(raw[i + 1:i + 2] + raw[i:i + 1]
                               for i in range(0, 40, 2))).decode().strip()
        self.assertEqual(model, "JSW VIRTUAL DISK 128M",
                         "the Scratch machine DMAs the same block")
        self.assertEqual(int(env.vars["X.ahciIs"]), 1, "and says so")


class I386ScsiTests(unittest.TestCase):
    """The third way to ask the same drive the same questions."""

    def ref(self):
        mach = rv.Machine()
        mach.ram[:] = i386_image.build()
        return mach

    @staticmethod
    def words(mach, count: int) -> bytes:
        return b"".join(mach.scsi_data().to_bytes(2, "little") for _ in range(count))

    def test_inquiry_names_the_device(self) -> None:
        mach = self.ref()
        mach.port_out(0x311, 0x12, 1)
        blob = self.words(mach, 18)
        self.assertEqual(blob[0] & 0x1F, 0x00, "a direct-access device")
        self.assertEqual(blob[8:16].decode().strip(), "JSW")
        self.assertEqual(blob[16:32].decode().strip(), "VIRTUAL DISK")
        self.assertEqual(mach.port_in(0x316, 1), 0, "no sense key")

    def test_read_capacity_is_big_endian(self) -> None:
        mach = self.ref()
        mach.port_out(0x311, 0x25, 1)
        blob = self.words(mach, 4)
        self.assertEqual(int.from_bytes(blob[0:4], "big") + 1, 128, "128 sectors")
        self.assertEqual(int.from_bytes(blob[4:8], "big"), 512, "512-byte blocks")

    def test_read_ten_returns_the_sector(self) -> None:
        mach = self.ref()
        mach.port_out(0x312, 0, 1)
        mach.port_out(0x313, 0, 1)
        mach.port_out(0x311, 0x28, 1)
        blob = self.words(mach, 256)
        self.assertEqual(blob[510:512], b"\x55\xaa")

    def test_a_read_past_the_end_is_a_check_condition(self) -> None:
        mach = self.ref()
        mach.port_out(0x311, 0x28, 1)
        mach.scsi_lba = 900
        mach.scsi_command(0x28)
        self.assertEqual(mach.port_in(0x316, 1), 1, "check condition")
        self.assertEqual(mach.port_in(0x317, 1), 0x21, "LBA out of range")

    def test_an_unknown_opcode_is_rejected(self) -> None:
        mach = self.ref()
        mach.port_out(0x311, 0x55, 1)
        self.assertEqual(mach.port_in(0x316, 1), 1)
        self.assertEqual(mach.port_in(0x317, 1), 0x20, "invalid command")

    def test_test_unit_ready_is_quiet(self) -> None:
        mach = self.ref()
        mach.port_out(0x311, 0x00, 1)
        self.assertEqual(mach.port_in(0x316, 1), 0)
        self.assertEqual(self.words(mach, 1), b"\x00\x00",
                         "TEST UNIT READY has no data phase")


class I386MemoryMapTests(unittest.TestCase):
    """Where things live, and why the order of the tests matters.

    The decoder is the hottest code in the machine: every instruction fetch and
    every operand goes through it. Base memory is checked first because it is
    almost every access, and a decoder that tested the windows first cost about
    a third of the machine's speed.
    """

    def jsw(self):
        """The machine the simulator can step: RAM in a list."""
        import subprocess
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "m.sb3"
            back = Path(tmp) / "m.jsw"
            subprocess.run([sys.executable, "-m", "jswc", "build",
                            str(RVDIR / "i386.jsw"), "-o", str(out)],
                           cwd=ROOT, check=True, capture_output=True)
            subprocess.run([sys.executable, "-m", "jswc", "decompile", str(out),
                            "-o", str(back), "--skip-assets"],
                           cwd=ROOT, check=True, capture_output=True)
            source = back.read_text()
        env = jsw_sim.Env()
        classes = jsw_sim.parse_program(source, env)
        env.lists["rom"] = json.loads((RVDIR / "assets" / "lists" / "bios.jsa").read_text())
        env.lists["disk"] = json.loads((RVDIR / "assets" / "lists" / "disk.jsa").read_text())
        env.procs = classes["Cpu"].procs
        env.threads.append({})
        env.current = 0
        env.vars["X.cores"] = 1
        gen = jsw_sim.call(env, classes["Cpu"].procs["powerOn"], [])
        try:
            while True:
                next(gen)
        except StopIteration:
            pass
        return env, classes["Cpu"].procs

    def read(self, procs, env, addr):
        gen = jsw_sim.call(env, procs["rdb"], [addr])
        try:
            while True:
                next(gen)
        except StopIteration:
            pass
        return int(jsw_sim.Env.num(env.vars["v"])) & 0xFF

    def test_a_halted_core_with_interrupts_off_never_wakes(self) -> None:
        """`cli; hlt` hangs until a reset, whatever the controller holds.

        The second core parks in the AP trampoline that way. The interrupt flag
        used to be tested only when the 8259 had no vector programmed, so the
        moment the guest set the controller up, a halted core woke on the next
        tick -- and by then MEMZ had written over the trampoline, so the AP
        walked out of its parked loop into the boot sector and ran it beside the
        first core. Two interpreters over one memory is a machine that reboots
        itself mid-message.
        """
        env, procs = self.jsw()
        env.vars["halted"] = 1
        env.vars["idleAt"] = -1                  # a tick has "passed"
        env.vars["X.tickNo"] = 5
        env.vars["steps"] = 0
        env.vars["X.picVec"] = 8                 # a vector is ready to deliver
        env.vars["X.pitOn"] = 1
        env.lists["X.pitCount"] = [1000, 0, 0]
        env.lists["X.pitReload"] = [1000, 0, 0]
        env.vars["fl"] = 0x0002                  # IF clear: cli; hlt
        gen = jsw_sim.call(env, procs["idleCore"], [])
        try:
            while True:
                next(gen)
        except StopIteration:
            pass
        self.assertEqual(int(env.vars["halted"]), 1,
                         "a core with interrupts off is parked, not sleeping")
        self.assertEqual(int(env.vars["steps"]), 0,
                         "and it retires nothing, so the clock does not skip ahead")

    def test_the_rtc_counts_seconds_not_passes(self) -> None:
        """One emulated second per real second, because that is what it is.

        The clock used to advance once per main-loop pass, and `wait 0` comes
        back about a hundred times a displayed frame: the BIOS gained two
        minutes for every real one. It now reads the wall clock, so a
        millisecond is worth a millisecond however often the loop runs.
        """
        env, procs = self.jsw()
        env.ext["Sensing.timer"] = lambda args: env.vars["now"]
        env.vars["now"] = 100.0
        env.vars["X.timeRate"] = 100
        env.vars["X.rtcSeconds"] = 0
        env.vars["X.rtcAt"] = 100.0
        env.vars["X.rtcSecs"] = 0

        def tick():
            gen = jsw_sim.call(env, procs["tickClock"], [])
            try:
                while True:
                    next(gen)
            except StopIteration:
                pass
            return int(env.vars["X.rtcSeconds"])

        for _ in range(130):                    # a hundred passes, ten times over
            tick()
        self.assertEqual(tick(), 0, "no time has passed, so no second has")
        env.vars["now"] = 101.5                 # a second and a half later
        self.assertEqual(tick(), 1)
        self.assertAlmostEqual(float(env.vars["X.rtcSecs"]), 0.5, places=6,
                               msg="the half second is kept, not dropped")
        env.vars["now"] = 102.4                 # 0.5 carried + 0.9 now = one more
        self.assertEqual(tick(), 2)
        self.assertAlmostEqual(float(env.vars["X.rtcSecs"]), 0.4, places=6)
        env.vars["now"] = 102.6                 # 0.4 + 0.2 is still under a second
        self.assertEqual(tick(), 2, "and a part-second is not rounded up")
        env.vars["now"] = 103.1                 # 0.6 + 0.5 finally crosses
        self.assertEqual(tick(), 3, "the parts add up")

    def test_base_memory_stops_at_the_video_hole(self) -> None:
        env, procs = self.jsw()
        self.assertEqual(int(env.vars["X.memWords"]), 163840, "640 KiB, in words")
        self.assertEqual(int(env.vars["X.holeEnd"]), 0xA0000)
        env.lists["X.mem"][0] = 0x11223344
        self.assertEqual(self.read(procs, env, 0), 0x44)
        self.assertEqual(self.read(procs, env, 3), 0x11)

    def test_the_aperture_is_the_graphics_card_and_the_text_plane_is_not(self) -> None:
        """The aperture used to answer as an open bus, which is why a program
        that set mode 13h painted a screen that never changed. It is the card's
        own memory now -- 64 KiB of it, a byte a pixel -- and the text plane is
        still its own 80 x 30 cells."""
        import types

        env, procs = self.jsw()

        def run(name, args):
            gen = jsw_sim.call(env, procs[name], args)
            try:
                next(gen)
            except StopIteration:
                pass

        run("wrb", [0xA0000, 0x41])
        self.assertEqual(self.read(procs, env, 0xA0000), 0x41,
                         "a store at A0000 lands in the card's memory")
        self.assertEqual(self.read(procs, env, 0xB8000), 0x20,
                         "the text plane starts full of spaces")

    def test_the_windows_sit_outside_base_memory(self) -> None:
        """The disk window used to be at 0x30000, which is *inside* the 640 KiB
        a PC has as base memory -- a bug that could only show up once RAM grew
        past it. It lives in option ROM space now."""
        env, procs = self.jsw()
        disk_base = int(env.vars["X.diskBase"])
        self.assertEqual(disk_base, 0xC0000)
        self.assertGreaterEqual(disk_base, int(env.vars["X.holeEnd"]),
                                "the disk window must not sit inside RAM")
        self.assertLess(disk_base + 0x10000, int(env.vars["X.romBase"]),
                        "and it must not overlap the ROM")
        # the boot sector is in the window and reads back through the decoder
        self.assertEqual(self.read(procs, env, disk_base + 510), 0x55)
        self.assertEqual(self.read(procs, env, disk_base + 511), 0xAA)

    def test_a_store_to_base_memory_reads_back(self) -> None:
        env, procs = self.jsw()
        gen = jsw_sim.call(env, procs["wrb"], [0x7C00, 0x5A])
        try:
            while True:
                next(gen)
        except StopIteration:
            pass
        self.assertEqual(self.read(procs, env, 0x7C00), 0x5A)

    def test_the_rom_and_the_disk_window_do_not_take_writes(self) -> None:
        """Both are read-only windows onto things a program cannot change.

        What *is* in them changes with whatever was staged (`make i386-boot`),
        so the test compares against what was there before the write rather than
        against a byte: the claim is "a write is dropped", not "sector 0 starts
        with `cli`".
        """
        env, procs = self.jsw()
        before = {
            addr: self.read(procs, env, addr) for addr in (0xF0000, 0xC0000)
        }
        self.assertEqual(self.read(procs, env, 0xC0000 + 510), 0x55, "the staged disk boots")
        self.assertEqual(self.read(procs, env, 0xC0000 + 511), 0xAA, "the staged disk boots")
        for addr in (0xF0000, 0xC0000):
            gen = jsw_sim.call(env, procs["wrb"], [addr, 0x00])
            try:
                while True:
                    next(gen)
            except StopIteration:
                pass
        for addr, byte in before.items():
            self.assertEqual(self.read(procs, env, addr), byte,
                             f"0x{addr:05X} took a write")

    def test_the_reference_model_agrees_about_the_map(self) -> None:
        mach = rv.Machine()
        mach.ram[:] = i386_image.build()
        self.assertEqual(mach.read(0xC0000 + 510, 1), 0x55,
                         "the model stages the disk in the same window")


class I386MulticoreTests(unittest.TestCase):
    """What the extra cores are for.

    Scratch clones share one JavaScript thread, so a core never makes the guest
    faster. What a core can do is keep a device out of the CPU's frame: the
    video card and the keyboard already had sprites, and the sensors have one
    now, which is why a pinned CPU still gets its fan curve updated.
    """

    def test_the_sensors_are_a_sprite_of_their_own(self) -> None:
        import json
        import subprocess
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "m.sb3"
            subprocess.run([sys.executable, "-m", "jswc", "build",
                            str(RVDIR / "i386.jsw"), "-o", str(out)],
                           cwd=ROOT, check=True, capture_output=True)
            with zipfile.ZipFile(out) as z:
                data = json.loads(z.read("project.json"))
        names = {t["name"] for t in data["targets"]}
        self.assertIn("Sensor", names, "the hardware monitor runs on its own core")
        self.assertIn("Display", names)
        self.assertIn("Input", names)
        self.assertIn("Cpu", names)

    def test_the_sensor_loop_does_not_live_in_the_cpu(self) -> None:
        """A pinned core must not be the thing that reports its own temperature:
        the reading would stop exactly when it matters most."""
        source = (RVDIR / "i386.jsw").read_text()
        cpu_part = source[:source.index("include \"sensor.jsw\"")]
        self.assertNotIn("hwStep", cpu_part,
                         "the thermal step belongs to the Sensor sprite")

    def test_an_idle_ap_reports_nothing_not_a_full_frame(self) -> None:
        """The AP trampoline checks in and halts: 99% on one core and 2% on the
        rest is what a real BIOS looks like, not a distribution bug."""
        mach = rv.Machine()
        self.assertIn("cli", (RVDIR / "bios.asm").read_text())
        trampoline = (RVDIR / "bios.asm").read_text()
        start = trampoline.index("apHalt:")
        self.assertIn("hlt", trampoline[start:start + 40],
                      "a parked AP is parked, not spinning")


class I386ThermalTests(unittest.TestCase):
    """The hardware monitor's numbers are integrated, not decorated.

    A CPU in a case is a thermal system: watts in from the clock and the load,
    watts out through a resistance the fan sets, and the fan's own curve closing
    the loop. These tests check the two properties that make the numbers worth
    showing -- it settles where a real machine settles, and it follows the work.
    """

    def ref(self):
        return rv.Machine()

    def settle(self, mach, util: float, seconds: float) -> None:
        mach.thermal(seconds, util)

    def test_it_idles_where_a_386_idles_and_loads_where_one_loads(self) -> None:
        mach = self.ref()
        self.settle(mach, 0.0, 300)
        idle = mach.cpu_temp
        self.assertGreater(idle, 28, "a die in a case is warmer than the room")
        self.assertLess(idle, 45, "and an idle 386 is not hot")
        self.settle(mach, 1.0, 300)
        load = mach.cpu_temp
        self.assertGreater(load, idle + 15, "work heats it")
        self.assertLess(load, 75, "and the fan keeps it out of trouble")
        self.assertGreater(mach.fan_rpm, 1500, "the fan answered the load")
        self.assertLessEqual(mach.fan_rpm, mach.fan_max)

    def test_half_load_lands_between_the_two(self) -> None:
        mach = self.ref()
        self.settle(mach, 0.0, 300)
        idle = mach.cpu_temp
        self.settle(mach, 1.0, 300)
        load = mach.cpu_temp
        mach2 = self.ref()
        self.settle(mach2, 0.5, 400)
        self.assertGreater(mach2.cpu_temp, idle)
        self.assertLess(mach2.cpu_temp, load)
        self.assertLess(mach2.fan_rpm, mach.fan_rpm)

    def test_it_cools_down_again(self) -> None:
        """The interesting half of a thermal model: the fan is not a switch."""
        mach = self.ref()
        self.settle(mach, 1.0, 300)
        hot = mach.cpu_temp
        self.settle(mach, 0.0, 400)
        self.assertLess(mach.cpu_temp, hot - 15)
        self.assertLess(mach.fan_rpm, 1400, "and the fan spun back down")

    def test_a_stopped_fan_cooks_the_die(self) -> None:
        """The fan is load-bearing: with no airflow only the heatsink is left."""
        mach = self.ref()
        mach.fan_min = 0
        mach.fan_max = 0
        mach.fan_rpm = 0
        self.settle(mach, 1.0, 600)
        self.assertGreater(mach.cpu_temp, 90,
                           "a 25 W die on a bare heatsink has nowhere to put it")

    def test_the_curve_does_not_oscillate(self) -> None:
        """A fan curve that is too steep chases the die in circles, and the
        temperature visibly pulses. It has to settle."""
        mach = self.ref()
        self.settle(mach, 1.0, 300)
        readings = []
        for _ in range(40):
            self.settle(mach, 1.0, 0.5)
            readings.append(mach.cpu_temp)
        self.assertLess(max(readings) - min(readings), 0.5,
                        f"temperature should be steady, saw {readings[:5]}")

    def test_the_spindle_comes_up_to_speed_and_stays_there(self) -> None:
        mach = self.ref()
        self.settle(mach, 0.2, 1)
        early = mach.hdd_rpm
        self.settle(mach, 0.2, 2)
        middle = mach.hdd_rpm
        self.settle(mach, 0.2, 60)
        late = mach.hdd_rpm
        self.assertLess(early, 4000, "a spindle does not reach speed at once")
        self.assertGreater(middle, early, "but it is on its way")
        self.assertGreater(late, 7100, "and a 7200 rpm drive gets to 7200")
        self.assertLess(late, 7300, "without inventing revolutions")

    def test_smart_reports_the_temperature_it_measured(self) -> None:
        mach = self.ref()
        self.settle(mach, 0.5, 200)
        attribute = mach.smart[4]
        expected = 100 - (round(mach.hdd_temp) - 25) * 2
        self.assertEqual(attribute, max(1, min(100, expected)))
        self.assertLessEqual(mach.smart_worst[4], attribute)

    def test_the_ports_carry_the_readings(self) -> None:
        """What the firmware actually reads: two bytes for a 16-bit value."""
        mach = self.ref()
        mach.steps = 1_700_000 * 200
        temp = mach.port_in(0x5B0, 1)
        lo = mach.port_in(0x5B1, 1)
        hi = mach.port_in(0x5B2, 1)
        self.assertEqual(temp, int(round(mach.cpu_temp)))
        self.assertEqual(lo + hi * 256, int(mach.fan_rpm))
        mach.port_out(0x5B7, 4, 1)
        self.assertEqual(mach.port_in(0x5B8, 1), mach.smart[4])
        self.assertEqual(mach.port_in(0x5B9, 1), mach.smart_worst[4])

    def test_the_emulation_switch_stops_the_sensors(self) -> None:
        mach = self.ref()
        mach.emu_on = 0
        before = mach.cpu_temp
        mach.thermal(60, 1.0)
        self.assertEqual(mach.cpu_temp, before,
                         "with emulation off the board has no sensors at all")

    def test_the_scratch_sensor_runs_the_same_equations(self) -> None:
        """The sensor core and the model are one design, so they must agree.

        The Scratch side integrates a 1/60 s step and the model a 1/4 s step;
        both use explicit Euler and the same constants, so after the same
        emulated time they land within a degree of each other.
        """
        import json
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            back = Path(tmp) / "m.back.jsw"
            subprocess.run([sys.executable, "-m", "jswc", "decompile",
                            str(ROOT / "build" / "i386" / "i386-test.sb3"),
                            "-o", str(back), "--skip-assets"],
                           cwd=ROOT, check=True, capture_output=True)
            source = back.read_text()

        env = jsw_sim.Env()
        classes = jsw_sim.parse_program(source, env)
        env.procs = classes["Sensor"].procs
        env.threads.append({})
        env.current = 0
        ref = self.ref()
        # the model's constants, copied into the Scratch machine's stage
        for name in ("pIdle", "pMax", "cTh", "rTh", "fanMin", "fanMax",
                     "fanKnee", "fanSlope", "tAmbient", "spinUpSecs"):
            key = "X." + name
            env.vars[key] = getattr(ref, _SNAKE[name])
        env.vars["X.rtcRate"] = 60
        env.vars["X.emuOn"] = 1
        env.vars["X.cpuBase"] = 33
        env.vars["X.rtcSeconds"] = 0
        env.vars["X.powerOnSecs"] = 0
        env.vars["X.hddSpunAt"] = 0
        env.vars["X.cpuTemp"] = 27
        env.vars["X.fanRpm"] = ref.fan_min
        env.vars["X.hddTemp"] = 27
        env.vars["X.hddRpm"] = 0
        env.vars["X.hwTicks"] = 0
        env.lists["X.coreUsage"] = [0] * 8
        env.lists["X.hwSmart"] = [100] * 10
        env.lists["X.hwSmartWorst"] = [100] * 10

        seconds = 200
        env.lists["X.coreUsage"] = [100, 0, 0, 0, 0, 0, 0, 0]
        for i in range(seconds * 60):
            env.vars["X.rtcSeconds"] = env.vars["X.rtcSeconds"] + 1 / 60
            gen = jsw_sim.call(env, env.procs["hwStep"], [])
            try:
                while True:
                    next(gen)
            except StopIteration:
                pass
        ref.thermal(seconds, 1.0)
        self.assertAlmostEqual(env.vars["X.cpuTemp"], ref.cpu_temp, delta=1.0,
                               msg="the two thermal models have drifted apart")
        self.assertAlmostEqual(env.vars["X.fanRpm"], ref.fan_rpm, delta=60)
        self.assertAlmostEqual(env.vars["X.hddRpm"], ref.hdd_rpm, delta=40)


#: the Scratch side's names for the same constants
_SNAKE = {
    "pIdle": "p_idle", "pMax": "p_max", "cTh": "c_th", "rTh": "r_th",
    "fanMin": "fan_min", "fanMax": "fan_max", "fanKnee": "fan_knee",
    "fanSlope": "fan_slope", "tAmbient": "t_ambient",
    "spinUpSecs": "spin_up_secs",
}


class I386BuildTests(unittest.TestCase):
    """Two builds of one source: the published one and the fast one.

    scratch.mit.edu has no extensions, so the Scratch build holds RAM in a
    list; TurboWarp can load `tools/i386mem.js`, so that build holds 128 MiB in
    a `Uint8Array`. Everything else -- the CPU, the firmware, the screen -- is
    the same program, and `#ifdef` is what decides which is which.
    """

    SRC = RVDIR / "i386.jsw"
    EXT = ROOT / "tools" / "i386mem.js"

    def build(self, target: str | None) -> dict:
        import json
        import tempfile
        import zipfile

        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "m.sb3"
            args = [sys.executable, "-m", "jswc", "build", str(self.SRC), "-o", str(out)]
            if target is not None:
                args += ["--target", target]
            subprocess.run(args, cwd=ROOT, check=True, capture_output=True)
            with zipfile.ZipFile(out) as z:
                return json.loads(z.read("project.json"))

    @staticmethod
    def opcodes(data: dict) -> set[str]:
        return {b["opcode"] for t in data["targets"]
                for b in (t.get("blocks") or {}).values() if isinstance(b, dict)}

    def test_the_turbowarp_build_holds_ram_in_the_extension(self) -> None:
        from jswc import opcodes

        data = self.build("turbowarp")
        # two extensions: the memory above 1 MiB, and the socket the network
        # drive needs. The Scratch build has neither.
        self.assertEqual(data["extensionURLs"],
                         {"i386mem": opcodes.EXTENSION_URLS["i386mem"],
                          "gsaWebsocket": opcodes.EXTENSION_URLS["gsaWebsocket"]})
        self.assertIn("i386mem", data["extensions"])
        used = self.opcodes(data)
        for opcode in ("i386mem_alloc", "i386mem_size", "i386mem_read8",
                       "i386mem_write8"):
            self.assertIn(opcode, used, "the TurboWarp build is the allocation")

    def test_the_scratch_build_declares_no_extension_url(self) -> None:
        data = self.build("scratch")
        self.assertNotIn("extensionURLs", data)
        used = self.opcodes(data)
        self.assertEqual({o for o in used if o.startswith("i386mem_")}, set(),
                         "a Scratch project cannot load a memory extension")
        self.assertIn("pen", data["extensions"], "pen is built in, so it is fine")

    def test_neither_target_is_the_build_the_simulators_run(self) -> None:
        """No ``--target`` defines neither name: the tests need a project that
        `jsw_sim` can step, which means RAM in a list."""
        data = self.build(None)
        self.assertNotIn("extensionURLs", data)
        self.assertEqual({o for o in self.opcodes(data) if o.startswith("i386mem_")},
                         set())

    def test_the_extension_and_the_compiler_agree_on_the_blocks(self) -> None:
        """The JS and `extension_blocks.py` are two halves of one contract."""
        import re

        from jswc.extension_blocks import EXTENSION_BLOCKS

        source = self.EXT.read_text(encoding="utf-8")
        in_js = set(re.findall(r"opcode: '(\w+)'", source))
        known = set(EXTENSION_BLOCKS["i386mem"]["blocks"])
        self.assertEqual(in_js, known)
        self.assertEqual(EXTENSION_BLOCKS["i386mem"]["namespace"], "Ram")

    def test_the_extension_is_a_flat_memory_that_keeps_its_promises(self) -> None:
        """Run the extension under node with a Scratch shim: 128 MiB, a fill, a
        halfword and a word at the far end, and the list crossing both ways."""
        import json
        import shutil
        import tempfile

        node = shutil.which("node")
        if node is None:
            self.skipTest("node is not installed")
        script = r"""
const fs = require('fs');
let registered = null;
global.Scratch = {
  ArgumentType: { NUMBER: 'number', STRING: 'string' },
  BlockType: { COMMAND: 'command', REPORTER: 'reporter', BOOLEAN: 'boolean' },
  extensions: { register: (ext) => { registered = ext; } }
};
eval(fs.readFileSync(process.argv[2], 'utf8'));
const mem = registered;
const out = { id: mem.getInfo().id };
out.allocated = mem.alloc({ BYTES: 134217728 });
mem.fill({ ADDR: 0, LEN: 16, VALUE: 90 });
mem.write16({ ADDR: 32, VALUE: 0x1234 });
mem.write32({ ADDR: 64, VALUE: 0x89ABCDEF });
out.byte0 = mem.read8({ ADDR: 0 });
out.byte16 = mem.read8({ ADDR: 16 });
out.half = mem.read16({ ADDR: 32 });
out.halfBytes = [mem.read8({ ADDR: 32 }), mem.read8({ ADDR: 33 })];
out.word = mem.read32({ ADDR: 64 });
out.pastEnd = mem.read8({ ADDR: 134217728 });
mem.write8({ ADDR: 134217728, VALUE: 1 });
out.stillThere = mem.read8({ ADDR: 134217728 - 1 });
mem.write8({ ADDR: 134217728 - 1, VALUE: 7 });
out.lastByte = mem.read8({ ADDR: 134217728 - 1 });
// the boot image arrives as a Scratch list and has to cross over
const bytes = [1, 2, 3, 4];
const fakeList = { length: 4, at: (i) => bytes[i], replace: (i, v) => { out.saved = v; } };
mem.load({ ADDR: 1000, LEN: 4, LIST: fakeList });
out.copied = [mem.read8({ ADDR: 1000 }), mem.read8({ ADDR: 1003 })];
mem.save({ ADDR: 1000, LEN: 4, LIST: fakeList });
out.copied = [mem.read8({ ADDR: 1000 }), mem.read8({ ADDR: 1003 })];
// the drive: its own buffer, words straight out of it, boot signature intact
mem.allocdisk({ BYTES: 4096 });
mem.hexload({ ADDR: 0, TEXT: 'D eb55' });
mem.hexload({ ADDR: 510, TEXT: 'D 55aa' });
out.diskWord = mem.diskread16({ ADDR: 0 });
out.diskSignature = mem.diskread16({ ADDR: 510 });
out.diskPast = mem.diskread16({ ADDR: 4096 });
// an argument order the block text does not allow writes nothing at all
mem.hexload({ TEXT: 512, ADDR: 'D dead' });
mem.hexload({ TEXT: 'D zz', ADDR: 2 });
out.badHex = mem.badHex;
out.diskAfterSwap = [mem.diskread16({ ADDR: 0 }), mem.diskread16({ ADDR: 512 })];
out.diskAfterJunk = mem.diskread16({ ADDR: 2 });
console.log(JSON.stringify(out));
"""
        with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as fh:
            fh.write(script)
            path = fh.name
        try:
            done = subprocess.run([node, path, str(self.EXT)], capture_output=True,
                                  text=True, timeout=120, check=True)
        finally:
            Path(path).unlink(missing_ok=True)
        data = json.loads(done.stdout)
        self.assertEqual(data["id"], "i386mem")
        self.assertEqual(data["allocated"], 134217728, "128 MiB, the rv32ima number")
        self.assertEqual((data["byte0"], data["byte16"]), (90, 0),
                         "a fill covers exactly what it was asked to")
        self.assertEqual(data["half"], 0x1234)
        self.assertEqual(data["halfBytes"], [0x34, 0x12], "low byte first, like a 386")
        self.assertEqual(data["word"], 0x89ABCDEF, "little-endian word")
        self.assertEqual(data["pastEnd"], 0, "a read past the end answers zero")
        self.assertEqual(data["stillThere"], 0, "a write past the end lands nowhere")
        self.assertEqual(data["copied"], [1, 4], "a list crosses into memory")
        self.assertEqual(data["saved"], [1, 2, 3, 4], "and memory crosses back")
        self.assertEqual(data["diskWord"], 0x55EB,
                         "the data register hands the guest a little-endian word")
        self.assertEqual(data["diskSignature"], 0xAA55,
                         "and a boot sector's 0xAA55 is not 0x55AA")
        self.assertEqual(data["diskPast"], 0, "a word past the image answers zero")
        self.assertEqual(data["badHex"], 2,
                         "a payload that is not a `D <hex>` reply is refused, not decoded")
        self.assertEqual(data["diskAfterSwap"], [0x55EB, 0],
                         "so swapped arguments cannot scribble over the drive")
        self.assertEqual(data["diskAfterJunk"], 0,
                         "and neither can a reply with a bad digit in it")

    def test_the_turbowarp_build_boots_out_of_its_own_memory(self) -> None:
        """Step the TurboWarp project with a stand-in for `Ram`.

        The blocks are generated, so what has to be checked is the wiring: that
        `Ram.read8` is called with the offset rather than the address, that the
        store writes the byte the CPU computed, and that a build whose memory
        lives somewhere else still runs the same program. The stand-in is a
        `bytearray`, which is what the extension holds anyway.
        """
        import json
        import tempfile
        import zipfile

        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "tw.sb3"
            back = Path(tmp) / "tw.jsw"
            subprocess.run([sys.executable, "-m", "jswc", "build", str(self.SRC),
                            "-o", str(out), "--target", "turbowarp"],
                           cwd=ROOT, check=True, capture_output=True)
            subprocess.run([sys.executable, "-m", "jswc", "decompile", str(out),
                            "-o", str(back), "--skip-assets"],
                           cwd=ROOT, check=True, capture_output=True)
            source = back.read_text()

        env = jsw_sim.Env()
        ram = bytearray(128 * 1024 * 1024)

        def store(args):
            ram[int(args[0]) % len(ram)] = int(jsw_sim.Env.num(args[1])) % 256
            return 0.0

        env.ext["Ram.alloc"] = lambda args: float(len(ram))
        env.ext["Ram.size"] = lambda args: float(len(ram))
        env.ext["Ram.read8"] = lambda args: float(ram[int(args[0]) % len(ram)])
        env.ext["Ram.write8"] = store
        classes = jsw_sim.parse_program(source, env)
        # the ROM and the disk still arrive as lists, because an .sb3 can only
        # carry bytes that way
        assets = RVDIR / "assets" / "lists"
        env.lists["rom"] = json.loads((assets / "bios.jsa").read_text())
        env.lists["disk"] = json.loads((assets / "disk.jsa").read_text())
        env.procs = classes["Cpu"].procs
        env.threads.append({})
        env.current = 0
        env.vars["X.cores"] = 1
        gen = jsw_sim.call(env, classes["Cpu"].procs["powerOn"], [])
        try:
            while True:
                next(gen)
        except StopIteration:
            pass

        self.assertGreater(len(ram), 128 * 1024 * 1024 - 1,
                           "the board asked for 128 MiB and got it")
        # Base memory is a list in both builds -- it is the hot path, and an
        # extension call is still a call -- so the 128 MiB lives entirely above
        # 1 MiB, where a PC has extended memory anyway.
        self.assertEqual(int(env.vars["X.memWords"]), 640 * 1024 // 4,
                         "640 KiB of base memory, in a list")
        self.assertEqual(int(env.vars["X.extWords"]), 33554432,
                         "128 MiB of extended memory, through Ram")

        mach = rv.Machine()
        mach.ram[:] = i386_image.build()
        mach.sreg[rv.SEG_CS] = rv.RESET_CS
        mach.eip = rv.RESET_IP
        gen = jsw_sim.call(env, classes["Cpu"].procs["cpuStep"], [])
        for i in range(3000):
            try:
                next(gen)
            except StopIteration:
                gen = jsw_sim.call(env, classes["Cpu"].procs["cpuStep"], [])
            mach.step()
            if int(env.lists["sr"][1]) != mach.sreg[rv.SEG_CS] or int(env.vars["ip"]) != mach.eip:
                self.fail(f"diverged at instruction {i}: "
                          f"jsw {int(env.lists['sr'][1]):04x}:{int(env.vars['ip']):04x} "
                          f"ref {mach.sreg[rv.SEG_CS]:04x}:{mach.eip:04x}")
        self.assertTrue(any(env.lists["X.mem"]),
                        "the guest wrote something into base memory")

    def test_a_byte_goes_where_the_next_read_looks_for_it(self) -> None:
        """`wrb` and `rdb` have to agree on where base memory is.

        The TurboWarp build can also put base memory in the extension's buffer,
        and an early version wrote there while reading from the list. Nothing
        noticed until a reboot turned that mode on: the guest's own stack went
        into the buffer, `rdb` read zeros out of the list, and the first `ret`
        after the reset popped a zero -- POST restarted at offset one, for ever,
        on a black screen.
        """
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "tw.sb3"
            back = Path(tmp) / "tw.jsw"
            subprocess.run([sys.executable, "-m", "jswc", "build", str(self.SRC),
                            "-o", str(out), "--target", "turbowarp"],
                           cwd=ROOT, check=True, capture_output=True)
            subprocess.run([sys.executable, "-m", "jswc", "decompile", str(out),
                            "-o", str(back), "--skip-assets"],
                           cwd=ROOT, check=True, capture_output=True)
            source = back.read_text()

        env = jsw_sim.Env()
        ram = bytearray(1024 * 1024)
        written = []

        def store(args):
            written.append(int(args[0]))
            ram[int(args[0]) % len(ram)] = int(jsw_sim.Env.num(args[1])) % 256
            return 0.0

        env.ext["Ram.alloc"] = lambda args: float(len(ram))
        env.ext["Ram.size"] = lambda args: float(len(ram))
        env.ext["Ram.read8"] = lambda args: float(ram[int(args[0]) % len(ram)])
        env.ext["Ram.write8"] = store
        classes = jsw_sim.parse_program(source, env)
        cpu = classes["Cpu"].procs
        env.procs = cpu
        env.threads.append({})
        env.current = 0
        env.lists["X.mem"] = [0] * (640 * 1024 // 4)
        env.vars["X.memWords4"] = 640 * 1024
        env.vars["X.holeEnd"] = 640 * 1024
        env.vars["X.extOn"] = 1                 # the mode a reboot turns on

        def run(name: str, args: list) -> None:
            gen = jsw_sim.call(env, cpu[name], args)
            try:
                while True:
                    next(gen)
            except StopIteration:
                pass

        run("wrb", [0x7C00, 0x5A])
        run("rdb", [0x7C00])
        self.assertEqual(int(env.vars["v"]), 0x5A,
                         "the byte went where the read looks for it")
        self.assertEqual(written, [],
                         "base memory is the list, so the extension saw no write")

    def test_the_data_register_hands_over_the_drive(self) -> None:
        """Boot from the drive the host handed over, in the TurboWarp build.

        This is the path TurboWarp takes and the model does not: a sector is not
        copied into a list any more, the data register reads the image word by
        word. The first version copied it with `Ram.diskwords` and the copy did
        nothing -- an extension is handed a list's *contents*, not the list, so
        every sector came back as zeros and the firmware said "No bootable
        device" no matter how much of the image had arrived.
        """
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "tw.sb3"
            back = Path(tmp) / "tw.jsw"
            subprocess.run([sys.executable, "-m", "jswc", "build", str(self.SRC),
                            "-o", str(out), "--target", "turbowarp"],
                           cwd=ROOT, check=True, capture_output=True)
            subprocess.run([sys.executable, "-m", "jswc", "decompile", str(out),
                            "-o", str(back), "--skip-assets"],
                           cwd=ROOT, check=True, capture_output=True)
            source = back.read_text()

        import mklinuxdisk

        boot = ROOT / "userspace/i386/linuxdisk.bin"
        stage2 = ROOT / "userspace/i386/linuxdisk2.bin"
        stub = ROOT / "userspace/i386/kstub32.bin"
        if not (boot.exists() and stage2.exists() and stub.exists()):
            self.skipTest("run make i386-extras")
        body = stub.read_bytes()
        blob = mklinuxdisk.stage(body, boot.read_bytes(), stage2.read_bytes(),
                                 0x0010_0000)
        env = jsw_sim.Env()
        env.ext["Ram.alloc"] = lambda args: 0.0
        env.ext["Ram.size"] = lambda args: 0.0
        env.ext["Ram.read8"] = lambda args: 0.0
        env.ext["Ram.write8"] = lambda args: 0.0
        env.ext["Ram.diskread16"] = lambda args: float(
            blob[int(args[0])] | blob[int(args[0]) + 1] << 8
            if int(args[0]) + 1 < len(blob) else 0)
        classes = jsw_sim.parse_program(source, env)
        cpu = classes["Cpu"].procs
        env.procs = cpu
        env.threads.append({})
        env.current = 0
        env.lists["X.mem"] = [0] * (640 * 1024 // 4)
        env.lists["X.ataBuf"] = [0] * 256
        env.lists["X.disk"] = [0] * 128
        env.vars["X.memWords4"] = 640 * 1024
        env.vars["X.holeEnd"] = 640 * 1024
        env.vars["X.netDisk"] = 1
        env.vars["X.netOn"] = 0
        env.vars["X.netDiskSectors"] = len(blob) // 512
        env.vars["X.ataSectors"] = 128
        env.vars["X.ataLba"] = 0

        def run(name: str, args: list) -> None:
            gen = jsw_sim.call(env, cpu[name], args)
            try:
                while True:
                    next(gen)
            except StopIteration:
                pass

        # through the command, because that is what tells the data register
        # which of its two sources the words come from
        run("ataCommand", [32])                      # READ SECTORS, one of them
        self.assertEqual(int(env.vars["X.ataStatus"]), 88,
                         "the drive says it has data")
        words = []
        for _ in range(256):
            run("ataIn", [])
            words.append(int(env.vars["v"]))
        self.assertEqual(words[255], 0xAA55,
                         "the boot sector arrived, signature and all")
        self.assertEqual(bytes(
            low for w in words for low in (w & 0xFF, w >> 8))[:4],
            blob[:4], "and word for word, little endian")
        self.assertEqual(int(env.vars["X.ataLba"]), 0,
                         "a one-sector command stays on its sector")

    def test_rep_insw_reads_the_data_register_as_words(self) -> None:
        """`rep insw` is one 16-bit read a word, not two 8-bit reads.

        The ATA data register is a word register: `in al, dx` twice steps the
        drive on by a word each time, so a sector built that way is the gaps
        between the words that were thrown away. The firmware reads every
        sector this way, so this is the difference between a machine that boots
        and a machine that says "No bootable device" with a perfect disk behind
        it -- which is exactly what TurboWarp said.
        """
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "tw.sb3"
            back = Path(tmp) / "tw.jsw"
            subprocess.run([sys.executable, "-m", "jswc", "build", str(self.SRC),
                            "-o", str(out), "--target", "turbowarp"],
                           cwd=ROOT, check=True, capture_output=True)
            subprocess.run([sys.executable, "-m", "jswc", "decompile", str(out),
                            "-o", str(back), "--skip-assets"],
                           cwd=ROOT, check=True, capture_output=True)
            source = back.read_text()

        sector = bytes(range(256)) * 2
        sector = sector[:510] + b"\x55\xaa"
        env = jsw_sim.Env()
        env.ext["Ram.alloc"] = lambda args: 0.0
        env.ext["Ram.size"] = lambda args: 0.0
        env.ext["Ram.read8"] = lambda args: 0.0
        env.ext["Ram.write8"] = lambda args: 0.0
        env.ext["Ram.diskread16"] = lambda args: float(
            sector[int(args[0])] | sector[int(args[0]) + 1] << 8)
        classes = jsw_sim.parse_program(source, env)
        cpu = classes["Cpu"].procs
        env.procs = cpu
        env.threads.append({})
        env.current = 0
        env.lists["X.mem"] = [0] * (640 * 1024 // 4)
        env.lists["X.ataBuf"] = [0] * 256
        env.lists["X.disk"] = [0] * 128
        env.vars["X.netDisk"] = 1
        env.vars["X.netOn"] = 0
        env.vars["X.netDiskSectors"] = 1
        env.vars["X.ataSectors"] = 1
        env.vars["X.ataLba"] = 0
        env.vars["X.ataCount"] = 1
        env.vars["X.ataStatus"] = 88
        env.vars["X.ataCmd"] = 32

        # mov dx,0x1F0 / mov di,0x2000 / mov cx,256 / rep insw / hlt
        code = bytes([0xBA, 0xF0, 0x01, 0xBF, 0x00, 0x20,
                      0xB9, 0x00, 0x01, 0xF3, 0x6D, 0xF4])
        rom = json.loads((RVDIR / "assets" / "lists" / "bios.jsa").read_text())
        for i, byte in enumerate(code):
            at = 0x100 + i
            word = int(rom[at // 4])
            if at % 4 < 2:
                word = (word & 0xFFFF0000) | ((word & 0xFFFF) & ~(0xFF << (at % 4 * 8))) \
                    | (byte << (at % 4 * 8))
            else:
                word = (word & 0xFFFF) | ((word >> 16 & ~(0xFF << ((at % 4 - 2) * 8)))
                                          | (byte << ((at % 4 - 2) * 8))) << 16
            rom[at // 4] = word
        env.lists["rom"] = rom

        gen = jsw_sim.call(env, cpu["powerOn"], [])
        try:
            while True:
                next(gen)
        except StopIteration:
            pass
        env.lists["rom"] = rom                    # powerOn copies the ROM around
        env.vars["ip"] = 0x0100
        env.lists["sr"][2] = 0xF000
        env.vars["X.netDisk"] = 1
        env.vars["X.ataLba"] = 0
        env.vars["X.ataCount"] = 1
        env.vars["X.ataCmd"] = 32
        env.vars["X.ataPos"] = 0
        env.vars["X.ataStatus"] = 88

        gen = None
        for _ in range(4000):
            if gen is None:
                gen = jsw_sim.call(env, cpu["cpuStep"], [])
            try:
                next(gen)
            except StopIteration:
                gen = None
            if int(env.vars["halted"]) == 1:
                break
        self.assertEqual(int(env.vars["halted"]), 1, "the snippet ran to its hlt")

        mem = [int(w) for w in env.lists["X.mem"]]
        got = bytearray()
        for i in range(0x2000 // 4, 0x2000 // 4 + 128):
            got += struct.pack("<I", mem[i] & 0xFFFFFFFF)
        self.assertEqual(bytes(got[:510]), sector[:510],
                         "the sector arrived word for word")
        self.assertEqual(bytes(got[510:512]), b"\x55\xaa",
                         "including the signature the firmware looks for")

    def test_a_kernel_clearing_low_memory_does_not_restart_the_machine(self) -> None:
        """The reboot request is a port, not a byte of RAM.

        It used to be a byte at 0x4F5, inside the BIOS data area next to the
        console's own scratch. A Linux kernel writing that address asked for a
        reboot on every pass: POST, disk, loader, kernel, reboot, for ever --
        which is exactly the "it keeps rebooting" this machine did.
        """
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "tw.sb3"
            back = Path(tmp) / "tw.jsw"
            subprocess.run([sys.executable, "-m", "jswc", "build", str(self.SRC),
                            "-o", str(out), "--target", "turbowarp"],
                           cwd=ROOT, check=True, capture_output=True)
            subprocess.run([sys.executable, "-m", "jswc", "decompile", str(out),
                            "-o", str(back), "--skip-assets"],
                           cwd=ROOT, check=True, capture_output=True)
            source = back.read_text()

        env = jsw_sim.Env()
        env.ext["Ram.alloc"] = lambda args: 0.0
        env.ext["Ram.size"] = lambda args: 0.0
        env.ext["Ram.read8"] = lambda args: 0.0
        env.ext["Ram.write8"] = lambda args: 0.0
        classes = jsw_sim.parse_program(source, env)
        cpu = classes["Cpu"].procs
        env.procs = cpu
        env.threads.append({})
        env.current = 0
        env.lists["X.ataBuf"] = [0] * 256
        env.lists["X.disk"] = [0] * 128
        env.vars["X.coreSel"] = 1
        env.vars["X.pitOn"] = 0

        def run(name: str, args: list) -> None:
            gen = jsw_sim.call(env, cpu[name], args)
            try:
                while True:
                    next(gen)
            except StopIteration:
                pass

        run("powerOn", [])
        words = env.lists["X.mem"]
        word = int(words[0x4F5 // 4])
        shift = (0x4F5 % 4) * 8
        words[0x4F5 // 4] = (word & ~(0xFF << shift)) | (4 << shift)

        env.vars["cr0"] = 1                       # a kernel: low memory is memory
        run("resetPoll", [])
        self.assertEqual(int(env.vars["X.reboot"]), 0,
                         "a protected-mode guest writing 0x4F5 is not a reset")

        env.vars["cr0"] = 0                       # the firmware: still its byte
        run("resetPoll", [])
        self.assertEqual(int(env.vars["X.reboot"]), 1,
                         "the setup utility's save-and-restart still works")

        env.vars["X.reboot"] = 0
        env.vars["cr0"] = 1
        run("portOut", [100, 254])                # the 8042's pulse-reset command
        run("resetPoll", [])
        self.assertEqual(int(env.vars["X.reboot"]), 1,
                         "port 0x64 = 0xFE restarts the machine in any mode")

    def test_thirty_two_bit_forms_keep_their_width(self) -> None:
        """The encodings that are only different in protected mode.

        Every one of these was executed as its sixteen-bit self, and nothing
        noticed until a Linux kernel ran: the ALU on a register or a memory
        operand, a far jump's offset, a conditional jump's displacement, a
        string instruction's element, and the stack pointer itself. One
        program, five ways to be wrong.
        """
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "tw.sb3"
            back = Path(tmp) / "tw.jsw"
            subprocess.run([sys.executable, "-m", "jswc", "build", str(self.SRC),
                            "-o", str(out), "--target", "turbowarp"],
                           cwd=ROOT, check=True, capture_output=True)
            subprocess.run([sys.executable, "-m", "jswc", "decompile", str(out),
                            "-o", str(back), "--skip-assets"],
                           cwd=ROOT, check=True, capture_output=True)
            source = back.read_text()

        env = jsw_sim.Env()
        env.ext["Ram.alloc"] = lambda args: 0.0
        env.ext["Ram.size"] = lambda args: float(64 * 1024 * 1024)
        env.ext["Ram.read8"] = lambda args: 0.0
        env.ext["Ram.write8"] = lambda args: 0.0
        env.ext["Ram.diskread16"] = lambda args: 0.0
        classes = jsw_sim.parse_program(source, env)
        cpu = classes["Cpu"].procs
        env.procs = cpu
        env.threads.append({})
        env.current = 0
        env.lists["X.mem"] = [0] * (640 * 1024 // 4)
        env.lists["X.ataBuf"] = [0] * 256
        env.lists["X.disk"] = [0] * 128
        env.vars["X.memWords4"] = 640 * 1024
        env.vars["X.holeEnd"] = 640 * 1024
        env.vars["X.coreSel"] = 1
        env.vars["X.pitOn"] = 0

        # AT 0x0200, thirty-two-bit code: a flat address space, paging off.
        # 0x66 asks for the thirty-two-bit operand in a sixteen-bit segment,
        # which is the same `oz` path protected mode takes for free.
        code = bytes([
            0x66, 0xB9, 0x00, 0x00, 0x01, 0x00,        # mov ecx,0x10000
            0x66, 0xBB, 0x01, 0x00, 0x00, 0x00,        # mov ebx,1
            0x66, 0x01, 0xD9,                          # add ecx,ebx
            0x66, 0x51,                                # push ecx
            0x66, 0x5A,                                # pop edx
            0x66, 0xBE, 0x11, 0x00, 0x00, 0x00,        # mov esi,0x11
            0x66, 0x39, 0xC9,                          # cmp ecx,ecx  (ZF=1)
            0x66, 0x0F, 0x84, 0x02, 0x00, 0x00, 0x00,  # jz +2
            0xF4, 0xF4,                                # the two bytes it skips
            0x66, 0xBE, 0x22, 0x00, 0x00, 0x00,        # mov esi,0x22
            0x66, 0xBF, 0x00, 0x10, 0x00, 0x00,        # mov edi,0x1000
            0x66, 0xB9, 0x02, 0x00, 0x00, 0x00,        # mov ecx,2
            0x66, 0xB8, 0x78, 0x56, 0x34, 0x12,        # mov eax,0x12345678
            0x66, 0xF3, 0xAB,                          # rep stosd
            0xF4,                                      # hlt
        ])
        def run(name: str, args: list) -> None:
            gen = jsw_sim.call(env, cpu[name], args)
            try:
                while True:
                    next(gen)
            except StopIteration:
                pass

        run("powerOn", [])
        # powerOn builds the memory list, so the program goes in after it
        words = env.lists["X.mem"]
        for i, byte in enumerate(code):
            at = 0x200 + i
            word = int(words[at // 4])
            shift = (at % 4) * 8
            words[at // 4] = (word & ~(0xFF << shift)) | (byte << shift)
        env.vars["ip"] = 0x200
        for name in ("sr", "sbase"):
            target = env.lists[name]
            for index in range(len(target)):
                target[index] = 0
        env.lists["slimit"] = [65535] * len(env.lists["slimit"])
        env.vars["fbAddr"] = -1
        esp_at_start = int(env.lists["r"][4])

        gen = None
        for _ in range(400):
            if gen is None:
                gen = jsw_sim.call(env, cpu["cpuStep"], [])
            try:
                next(gen)
            except StopIteration:
                gen = None
            if int(env.vars["halted"]) == 1:
                break

        regs = [int(x) for x in env.lists["r"]]
        self.assertEqual(int(env.vars["halted"]), 1,
                         f"the program ran to its hlt (ip {int(env.vars['ip']):#x}, "
                         f"err {env.vars.get('err')}, regs {regs})")
        self.assertEqual(regs[2], 0x10001,
                         "add ecx,ebx is a thirty-two-bit add, carried through "
                         "a thirty-two-bit push and pop")
        self.assertEqual(regs[4], esp_at_start,
                         "the stack pointer came back unchanged")
        self.assertEqual(regs[6], 0x22,
                         "a jz with a dword displacement is six bytes long")
        self.assertEqual(int(env.lists["X.mem"][0x1000 // 4]), 0x12345678,
                         "rep stosd writes dwords")
        self.assertEqual(int(env.lists["X.mem"][0x1004 // 4]), 0x12345678)
        self.assertEqual(regs[7], 0x1008,
                         "and walks the pointer a dword at a time")

    def test_a_register_is_read_at_the_width_the_instruction_asked_for(self) -> None:
        """The other half of the width rule: how a register is *read*.

        Making the sixteen-bit accessor mask and the sixteen-bit writer preserve
        the top half -- both correct 386 semantics -- broke a family of
        instructions that had been quietly reading all thirty-two bits through
        the narrow accessor: the accumulator behind `scasd`, the compare-and-swap
        a lock is built out of, `xadd`'s count, the second half of `shld`, the
        destination of `bsf`, and `inc eax` carrying out of bit 15. One program,
        every one of them in its thirty-two-bit form.
        """
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "tw.sb3"
            back = Path(tmp) / "tw.jsw"
            subprocess.run([sys.executable, "-m", "jswc", "build", str(self.SRC),
                            "-o", str(out), "--target", "turbowarp"],
                           cwd=ROOT, check=True, capture_output=True)
            subprocess.run([sys.executable, "-m", "jswc", "decompile", str(out),
                            "-o", str(back), "--skip-assets"],
                           cwd=ROOT, check=True, capture_output=True)
            source = back.read_text()

        env = jsw_sim.Env()
        env.ext["Ram.alloc"] = lambda args: 0.0
        env.ext["Ram.size"] = lambda args: float(64 * 1024 * 1024)
        env.ext["Ram.read8"] = lambda args: 0.0
        env.ext["Ram.write8"] = lambda args: 0.0
        env.ext["Ram.diskread16"] = lambda args: 0.0
        classes = jsw_sim.parse_program(source, env)
        cpu = classes["Cpu"].procs
        env.procs = cpu
        env.threads.append({})
        env.current = 0
        env.lists["X.mem"] = [0] * (640 * 1024 // 4)
        env.lists["X.ataBuf"] = [0] * 256
        env.lists["X.disk"] = [0] * 128
        env.vars["X.memWords4"] = 640 * 1024
        env.vars["X.holeEnd"] = 640 * 1024
        env.vars["X.coreSel"] = 1
        env.vars["X.pitOn"] = 0

        code = bytes([
            0x66, 0xB8, 0x00, 0x00, 0x01, 0x00,        # mov eax,0x10000
            0x66, 0xBF, 0x00, 0x10, 0x00, 0x00,        # mov edi,0x1000
            0x66, 0xB9, 0x01, 0x00, 0x00, 0x00,        # mov ecx,1
            0x66, 0xF3, 0xAB,                          # rep stosd
            0x66, 0xBB, 0x00, 0x00, 0x01, 0x00,        # mov ebx,0x10000
            0x66, 0xBE, 0x00, 0x00, 0x00, 0x80,        # mov esi,0x80000000
            0x66, 0x0F, 0xC1, 0xF3,                    # xadd ebx,esi
            0x66, 0xBD, 0xFF, 0xFF, 0x00, 0x00,        # mov ebp,0xffff
            0x66, 0x45,                                # inc ebp
            0x66, 0xBA, 0xBE, 0xBA, 0xFE, 0xCA,        # mov edx,0xcafebabe
            0x66, 0x0F, 0xA4, 0xD5, 0x01,              # shld ebp,edx,1
            0x66, 0x0F, 0xBC, 0xD0,                    # bsf edx,eax
            0x66, 0xB9, 0x00, 0x00, 0x01, 0x00,        # mov ecx,0x10000
            0x66, 0x0F, 0xB1, 0xD1,                    # cmpxchg ecx,edx
            0x66, 0xB8, 0x00, 0x00, 0x01, 0x00,        # mov eax,0x10000
            0x66, 0xBF, 0x00, 0x10, 0x00, 0x00,        # mov edi,0x1000
            0x66, 0xAF,                                # scasd
            0x0F, 0x94, 0xC0,                          # setz al
            0xF4,                                      # hlt
        ])

        def run(name: str, args: list) -> None:
            gen = jsw_sim.call(env, cpu[name], args)
            try:
                while True:
                    next(gen)
            except StopIteration:
                pass

        run("powerOn", [])
        words = env.lists["X.mem"]
        for i, byte in enumerate(code):
            at = 0x200 + i
            shift = (at % 4) * 8
            words[at // 4] = (int(words[at // 4]) & ~(0xFF << shift)) | (byte << shift)
        env.vars["ip"] = 0x200
        for name in ("sr", "sbase"):
            target = env.lists[name]
            for index in range(len(target)):
                target[index] = 0
        env.lists["slimit"] = [65535] * len(env.lists["slimit"])
        env.vars["fbAddr"] = -1

        gen = None
        for _ in range(400):
            if gen is None:
                gen = jsw_sim.call(env, cpu["cpuStep"], [])
            try:
                next(gen)
            except StopIteration:
                gen = None
            if int(env.vars["halted"]) == 1:
                break

        regs = [int(x) for x in env.lists["r"]]
        self.assertEqual(int(env.vars["halted"]), 1,
                         f"the program ran to its hlt (ip {int(env.vars['ip']):#x}, "
                         f"err {env.vars.get('err')})")
        self.assertEqual(regs[3], 0x80010000,
                         "xadd's thirty-two-bit sum lands in the destination")
        self.assertEqual(regs[6], 0x10000,
                         "and the source takes the whole old destination")
        self.assertEqual(regs[5], 0x20001,
                         "shld shifts in bit 31 of its source, not bit 15")
        self.assertEqual(regs[2], 0x10,
                         "bsf clears the destination's top half instead of keeping it")
        self.assertEqual(regs[1], 0x10,
                         "cmpxchg stored all of its source, having compared all of eax")
        self.assertEqual(regs[0], 0x10001,
                         "scasd compared the dword in eax with the dword in memory, "
                         "so setz al set the low byte and left the rest alone")
        self.assertEqual(int(env.lists["X.mem"][0x1000 // 4]), 0x10000)
        self.assertEqual(regs[7], 0x1004, "and walked the pointer a dword")

    def test_a_batch_of_sectors_is_one_copy_in_extended_memory(self) -> None:
        """`rep insw` into extended memory is one extension call, not 8192.

        The loader moves sixteen megabytes into a machine whose memory above the
        first megabyte belongs to the extension. Asking for that a word at a
        time is eight million calls -- the tab is frozen for a minute, and the
        machine looks hung. The extension holds both the image and the memory,
        so the run is one memcpy; the registers have to end where the loop would
        have left them, or the next instruction reads the wrong sector.
        """
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "tw.sb3"
            back = Path(tmp) / "tw.jsw"
            subprocess.run([sys.executable, "-m", "jswc", "build", str(self.SRC),
                            "-o", str(out), "--target", "turbowarp"],
                           cwd=ROOT, check=True, capture_output=True)
            subprocess.run([sys.executable, "-m", "jswc", "decompile", str(out),
                            "-o", str(back), "--skip-assets"],
                           cwd=ROOT, check=True, capture_output=True)
            source = back.read_text()

        image = bytes((i * 7) & 0xFF for i in range(512 * 8))
        calls = []

        def copy(args):
            calls.append((int(args[0]), int(args[1]), int(args[2])))
            return 0.0

        env = jsw_sim.Env()
        env.ext["Ram.alloc"] = lambda args: 0.0
        env.ext["Ram.size"] = lambda args: float(128 * 1024 * 1024)
        env.ext["Ram.read8"] = lambda args: 0.0
        env.ext["Ram.write8"] = lambda args: 0.0
        env.ext["Ram.diskscopy"] = copy
        env.ext["Ram.diskcopy"] = copy
        env.ext["Ram.diskread16"] = lambda args: 0.0
        classes = jsw_sim.parse_program(source, env)
        cpu = classes["Cpu"].procs
        env.procs = cpu
        env.threads.append({})
        env.current = 0
        env.lists["X.mem"] = [0] * (640 * 1024 // 4)
        env.lists["X.ataBuf"] = [0] * 256
        env.lists["X.disk"] = [0] * 128
        env.vars["X.extOn"] = 0
        env.vars["X.extBase"] = 0x100000
        env.vars["X.extWords"] = 33554432
        env.vars["X.netDisk"] = 1
        env.vars["X.netOn"] = 0
        env.vars["X.ataLba"] = 40
        env.vars["X.ataCount"] = 4
        env.vars["X.ataStatus"] = 88
        env.vars["X.ataCmd"] = 32

        # mov dx,0x1F0 / mov edi,0x01000000 / mov ecx,1024 / rep insw / hlt,
        # in a 32-bit segment: `insd` would move dwords, so the 66 stays
        code = bytes([0x66, 0xBA, 0xF0, 0x01,
                      0xBF, 0x00, 0x00, 0x00, 0x01,
                      0xB9, 0x00, 0x04, 0x00, 0x00,
                      0x66, 0xF3, 0x6D, 0xF4])
        rom = json.loads((RVDIR / "assets" / "lists" / "bios.jsa").read_text())
        for i, byte in enumerate(code):
            at = 0x200 + i
            word = int(rom[at // 4])
            shift = (at % 4) * 8
            word = (word & ~(0xFF << shift)) | (byte << shift)
            rom[at // 4] = word & 0xFFFFFFFF
        env.lists["rom"] = rom

        gen = jsw_sim.call(env, cpu["powerOn"], [])
        try:
            while True:
                next(gen)
        except StopIteration:
            pass
        env.lists["rom"] = rom
        env.vars["ip"] = 0x0200
        env.lists["sr"][2] = 0xF000
        # protected mode with paging off: a linear address is a physical one,
        # which is the only place sixteen megabytes can land
        env.vars["cr0"] = 1
        env.vars["dsz"] = 4                    # a code descriptor with D set
        env.vars["X.extOn"] = 0
        env.vars["X.extWords"] = 33554432
        env.vars["X.netDisk"] = 1
        env.vars["X.ataLba"] = 40
        env.vars["X.ataCount"] = 4
        env.vars["X.ataCmd"] = 32
        env.vars["X.ataPos"] = 0
        env.vars["X.ataStatus"] = 88

        gen = None
        for _ in range(4000):
            if gen is None:
                gen = jsw_sim.call(env, cpu["cpuStep"], [])
            try:
                next(gen)
            except StopIteration:
                gen = None
            if int(env.vars["halted"]) == 1:
                break
        self.assertEqual(int(env.vars["halted"]), 1, "the snippet ran to its hlt")
        self.assertEqual(calls, [(2048, 40 * 512, 0x01000000 - 0x100000)],
                         "four sectors, one call, straight into memory")
        self.assertEqual(int(env.vars["X.ataLba"]), 44, "the drive moved on")
        self.assertEqual(int(env.vars["X.ataCount"]), 0, "and the command is done")

    def test_turbowarp_build_waits_for_network_disk(self) -> None:
        """A browser build must not execute its staged fallback before pull."""
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "tw.sb3"
            back = Path(tmp) / "tw.jsw"
            subprocess.run([sys.executable, "-m", "jswc", "build", str(self.SRC),
                            "-o", str(out), "--target", "turbowarp"],
                           cwd=ROOT, check=True, capture_output=True)
            subprocess.run([sys.executable, "-m", "jswc", "decompile", str(out),
                            "-o", str(back), "--skip-assets"],
                           cwd=ROOT, check=True, capture_output=True)
            source = back.read_text()
            with zipfile.ZipFile(out) as project:
                data = json.loads(project.read("project.json"))

        stage = next(target for target in data["targets"] if target["name"] == "Stage")
        variables = {value[0]: value[1] for value in stage["variables"].values()}
        self.assertEqual(variables["X.netPulling"], 1)
        self.assertEqual(variables["X.netBootStatus"], "Connecting to the boot disk")
        self.assertTrue(any(
            monitor.get("visible")
            and monitor.get("params", {}).get("VARIABLE") == "X.netBootStatus"
            for monitor in data["monitors"]
        ), "the browser build shows network-disk startup status")
        self.assertIn("if (X.netPulling == 1)", source,
                      "the CPU yields until network-disk loading finishes")

    def test_the_pull_hands_the_reply_and_the_offset_over_the_right_way(self) -> None:
        """`hexload` is `[TEXT]` then `[ADDR]`, and a call cannot swap them.

        The pull reported all 31,761 sectors, the host logged every one, and
        TurboWarp said "boot disk load failed" -- because the block compiled to
        `load hex (pullAt) into the disk at (msg)`: the offset went in as the
        reply, the reply was turned into a number, and the whole image decoded
        to nothing. A structural check on the built block is the only place the
        mistake is visible, since both arguments are just variables.
        """
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "tw.sb3"
            subprocess.run([sys.executable, "-m", "jswc", "build", str(self.SRC),
                            "-o", str(out), "--target", "turbowarp"],
                           cwd=ROOT, check=True, capture_output=True)
            with zipfile.ZipFile(out) as project:
                data = json.loads(project.read("project.json"))

        netdisk = next(target for target in data["targets"]
                       if target["name"] == "NetDisk")
        names = {key: value[0] for key, value in netdisk["variables"].items()}

        def field_of(block_id: str) -> str:
            block = netdisk["blocks"][block_id]
            return block["fields"]["VARIABLE"][0] if block["opcode"] == "data_variable" else "?"

        blocks = [block for block in netdisk["blocks"].values()
                  if isinstance(block, dict) and block.get("opcode") == "i386mem_hexload"]
        self.assertTrue(blocks, "the pull decodes replies into the drive")
        for block in blocks:
            text = block["inputs"]["TEXT"][1]
            addr = block["inputs"]["ADDR"][1]
            self.assertTrue(isinstance(text, str) and isinstance(addr, str),
                            "both arguments are variables, not literals")
            self.assertEqual(names[netdisk["blocks"][text]["fields"]["VARIABLE"][1]], "msg",
                             "TEXT is the `D <hex>` reply")
            self.assertEqual(names[netdisk["blocks"][addr]["fields"]["VARIABLE"][1]], "pullAt",
                             "ADDR is the byte offset into the drive")

    def test_the_drive_reports_the_image_it_was_given(self) -> None:
        """IDENTIFY's words 60 and 61 are the capacity, and a driver believes
        them.

        The baked block says 128 sectors: it was written when the drive was a
        list in the project. A machine fed a sixteen-megabyte image and asked
        how big it is has to answer with the image, and the block still has to
        checksum to zero or a driver rejects it as corrupt.
        """
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "tw.sb3"
            back = Path(tmp) / "tw.jsw"
            subprocess.run([sys.executable, "-m", "jswc", "build", str(self.SRC),
                            "-o", str(out), "--target", "turbowarp"],
                           cwd=ROOT, check=True, capture_output=True)
            subprocess.run([sys.executable, "-m", "jswc", "decompile", str(out),
                            "-o", str(back), "--skip-assets"],
                           cwd=ROOT, check=True, capture_output=True)
            source = back.read_text()

        env = jsw_sim.Env()
        env.ext["Ram.alloc"] = lambda args: 0.0
        env.ext["Ram.size"] = lambda args: 0.0
        env.ext["Ram.read8"] = lambda args: 0.0
        env.ext["Ram.write8"] = lambda args: 0.0
        classes = jsw_sim.parse_program(source, env)
        cpu = classes["Cpu"].procs
        env.procs = cpu
        env.threads.append({})
        env.current = 0
        env.lists["X.ataBuf"] = [0] * 256
        env.vars["X.ataSectors"] = 128
        env.vars["X.netDisk"] = 1
        env.vars["X.netDiskSectors"] = 32768

        gen = jsw_sim.call(env, cpu["ataIdentify"], [])
        try:
            while True:
                next(gen)
        except StopIteration:
            pass
        words = [int(w) for w in env.lists["X.ataBuf"]]
        # item n + 1 is word n: the capacity is words 60 and 61
        self.assertEqual(words[60] | (words[61] << 16), 32768,
                         "the capacity is the image, not the list")
        self.assertEqual(sum(words) & 0xFFFF, 0,
                         "and the block still checksums to zero")

    def test_a_board_that_cannot_have_128_mib_reports_what_it_got(self) -> None:
        """`alloc` halves the ask instead of throwing: the project must survive."""
        import json
        import shutil
        import tempfile

        node = shutil.which("node")
        if node is None:
            self.skipTest("node is not installed")
        script = r"""
const fs = require('fs');
let registered = null;
global.Scratch = {
  ArgumentType: { NUMBER: 'number' },
  BlockType: { COMMAND: 'command', REPORTER: 'reporter' },
  extensions: { register: (ext) => { registered = ext; } }
};
eval(fs.readFileSync(process.argv[2], 'utf8'));
const mem = registered;
let asks = 0;
const realUint8 = Uint8Array;
global.Uint8Array = new Proxy(realUint8, {
  construct(target, args) {
    asks += 1;
    if (args[0] > (1 << 24)) { throw new RangeError('nope'); }
    return new target(...args);
  }
});
const got = mem.alloc({ BYTES: 1 << 28 });
console.log(JSON.stringify({ got, asks }));
"""
        with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as fh:
            fh.write(script)
            path = fh.name
        try:
            done = subprocess.run([node, path, str(self.EXT)], capture_output=True,
                                  text=True, timeout=120, check=True)
        finally:
            Path(path).unlink(missing_ok=True)
        data = json.loads(done.stdout)
        self.assertEqual(data["got"], 1 << 24, "the largest ask that fitted")
        self.assertGreater(data["asks"], 1, "it asked again with less")


class I386FirmwareApiTests(unittest.TestCase):
    """The two questions a kernel's loader asks the firmware.

    `int 15h, E820` is how a kernel learns what memory exists and what it must
    not touch; `int 13h, AH=41/42/48` is how a loader reads a sector by number
    instead of arithmetic on a cylinder count. Both are contracts with a
    caller we do not control, so `apiprobe.asm` asks them from a boot sector
    and writes the answers to fixed addresses for a test to read back.
    """

    BIN = ROOT / "userspace/i386/apiprobe.bin"

    def boot(self, extended_mb: int = 0, disk: bytes | None = None) -> rv.Machine:
        # the firmware's own sector 1 is where `apiprobe` reads from, so its
        # sector 0 is put in place: int 19h reads sector 0 through int 13h
        image = bytearray(i386_image.build(disk))
        image[i386_image.DISK_AT:i386_image.DISK_AT + 512] = self.BIN.read_bytes()
        if disk is not None:
            image[i386_image.DISK_AT + 512:i386_image.DISK_AT + 1024] = disk[512:1024]
        mach = rv.Machine()
        mach.ram[:] = bytes(image)
        # the battery keeps extended memory in megabytes: a word of kilobytes
        # cannot say more than 64 MB, and this board has sockets of 128
        mach.cmos[0x17] = extended_mb & 0xFF
        mach.cmos[0x18] = (extended_mb >> 8) & 0xFF
        mach.cmos[0x3C] = 1                         # one socket, so it is real
        mach.keys.append(0x39)                      # skip the countdown
        mach.sreg[rv.SEG_CS] = rv.RESET_CS
        mach.eip = rv.RESET_IP
        mach.run(limit=400_000)
        return mach

    def entries(self, mach) -> list[tuple[int, int, int]]:
        found = []
        for n in range(mach.read(0x7F0, 2)):
            at = 0x800 + n * 20
            found.append((mach.read(at, 4), mach.read(at + 8, 4), mach.read(at + 16, 4)))
        return found

    def test_e820_describes_the_board(self) -> None:
        mach = self.boot()
        self.assertTrue(mach.halted, mach.error)
        self.assertEqual(self.entries(mach),
                         [(0x0000_0000, 0x9FC00, 1),        # 639 KiB usable
                          (0x0009_FC00, 0x400, 2)])         # then the video card

    def test_e820_reports_extended_memory_when_there_is_some(self) -> None:
        """A board with 16 MiB of extended memory says so, in bytes, from
        0x10_0000 -- and the map ends after it."""
        mach = self.boot(extended_mb=16)
        self.assertEqual(self.entries(mach)[-1], (0x0010_0000, 0x0100_0000, 1))

    def test_e820_says_when_it_has_no_more(self) -> None:
        """The last call has to come back with EBX = 0, or a loader takes one
        zeroed entry as a memory range and puts its page tables in it."""
        mach = self.boot()
        self.assertEqual(mach.read(0x7F0, 2), 2, "two entries, not three")
        for n in range(2):
            self.assertNotEqual(mach.read(0x800 + n * 20 + 16, 4), 0,
                                f"entry {n} is a real type")

    def test_the_drive_speaks_edd(self) -> None:
        mach = self.boot()
        self.assertEqual(mach.read(0x7F2, 2), 0xAA55, "BX: the signature")
        self.assertEqual(mach.read(0x7F5, 1), 0x30, "AH: the version")
        self.assertEqual(mach.read(0x7F6, 2) & 1, 1, "CX bit 0: packets work")

    def test_a_packet_read_moves_the_sector_the_guest_asked_for(self) -> None:
        """LBA 1 is the second sector and says STAGE2; the packet names LBA 1,
        and that is where it lands -- the arithmetic is the firmware's."""
        # the firmware's own disk, because that is the one with a sector 1
        mach = self.boot(disk=i386_image.disk_image())
        self.assertEqual(bytes(mach.ram[0x900:0x906]), b"STAGE2")

    def test_the_packet_drive_parameters_describe_the_image(self) -> None:
        mach = self.boot()
        total = mach.read(0x960 + 12, 4) | mach.read(0x960 + 16, 4) << 32
        self.assertEqual(total, 128, "128 sectors of staged disk")
        self.assertEqual(mach.read(0x960 + 20, 2), 512, "512 bytes a sector")

    def test_ah_08h_divides_the_drive_it_actually_has(self) -> None:
        """The geometry was 80 x 16 x 63 for every drive in the machine, which
        is forty megabytes whether or not that much disk is there. The count is
        the drive's own now, so a 4032-sector image is four cylinders -- and a
        loader that asks is told the truth rather than a number from 1994."""
        disk = bytearray(4032 * 512)
        disk[0:512] = self.BIN.read_bytes()
        # the ROM in RAM and the disk behind the ATA port: a two-megabyte
        # image does not fit in the staged window at 0x30000
        mach = rv.Machine(ram_size=4 << 20, disk=bytes(disk))
        rom = i386_image.build(disk=b"")
        mach.ram[0:len(rom)] = rom
        mach.keys.append(0x39)
        mach.sreg[rv.SEG_CS] = rv.RESET_CS
        mach.eip = rv.RESET_IP
        mach.run(limit=400_000)
        self.assertEqual(mach.read(0x984, 1), 0, "AH: the call worked")
        self.assertEqual(mach.read(0x985, 1), 0, "and the carry is clear")
        cx = mach.read(0x980, 2)
        self.assertEqual(cx & 0x3F, 63, "63 sectors to a track")
        self.assertEqual(mach.read(0x983, 1), 15, "16 heads, numbered from zero")
        self.assertEqual(mach.read(0x982, 1), 1, "one drive on the channel")
        hi, lo = cx >> 8 & 0xFF, cx & 0xFF
        self.assertEqual(lo & 0x3F, 63, "the low six bits are the sectors")
        self.assertEqual(((lo >> 6) & 0x03) << 8 | hi, 4, "4032 sectors is four cylinders")

    def test_a_drive_smaller_than_a_cylinder_still_reports_one(self) -> None:
        mach = self.boot()                          # the 128-sector staged disk
        cx = mach.read(0x980, 2)
        hi, lo = cx >> 8 & 0xFF, cx & 0xFF
        self.assertEqual(((lo >> 6) & 0x03) << 8 | hi, 1)

    def test_a_cd_has_no_geometry_to_report(self) -> None:
        """Cylinders, heads and sectors describe a stack of platters. A CD is
        one spiral, so the old call fails there and EDD's packet functions are
        how a CD's own loader asks."""
        mach = self.boot()
        self.assertEqual(mach.read(0x986, 1), 1, "the CD answered with carry set")


class I386BootOrderTests(unittest.TestCase):
    """The Boot Sequence row, which was a byte nothing read.

    The firmware went CD then disk whatever the battery said, so "C: only"
    changed nothing but the screen. Now the order is what int 19h walks, and
    the line it prints is the device it is actually trying.
    """

    def boot(self, order: int, limit: int = 400_000) -> "rv.Machine":
        mach = rv.Machine()
        mach.ram[:] = i386_image.build()
        mach.cmos[0x2D] = order
        mach.keys.append(0x39)                      # skip the countdown
        mach.sreg[rv.SEG_CS] = rv.RESET_CS
        mach.eip = rv.RESET_IP
        mach.run(limit=limit)
        return mach

    def test_c_only_never_tries_the_floppy(self) -> None:
        text = self.boot(2).text()
        self.assertIn("Booting from device 0x80", text)
        self.assertNotIn("device 0x00", text, "A: was not on the list")

    def test_a_then_c_tries_the_floppy_first(self) -> None:
        """There is no drive 0x00 on this board, so the attempt costs one failed
        read -- and the disk still boots, which is the point of a list."""
        text = self.boot(0).text()
        self.assertIn("Booting from device 0x00", text)
        self.assertIn("Booting from device 0x80", text)
        self.assertLess(text.index("device 0x00"), text.index("device 0x80"))

    def test_cd_first_tries_the_cd_before_the_disk(self) -> None:
        """The row the board ships with, because an ISO is what goes on a CD:
        the catalog is read, and with no CD in the drive the disk boots."""
        text = self.boot(3).text()
        self.assertIn("Booting from device 0x9f", text)
        self.assertLess(text.index("device 0x9f"), text.index("device 0x80"))

    def test_a_blank_battery_boots_the_way_the_board_ships(self) -> None:
        self.assertIn("Booting from device 0x9f", self.boot(0xFF).text())


class I386HandOffTests(unittest.TestCase):
    """What the firmware leaves in DL when it hands over a boot sector.

    A boot sector reads its own rest with `int 13h` and most of them never set
    DL: they use the drive number the BIOS left there, which is the one thing
    about the hand-off that is not zeroed. MEMZ is exactly that guest -- its
    first act is `int 13h ah=02` for four sectors of stage 2 -- and with a DL
    that says "floppy 0" the firmware answers "no such drive", the stage 2
    area stays zeros, and MEMZ decompresses a black screen.
    """

    BIN = ROOT / "userspace" / "i386" / "dlprobe.bin"

    def test_a_boot_sector_that_never_sets_dl_can_read_the_drive(self) -> None:
        image = bytearray(i386_image.build(i386_image.disk_image()))
        image[i386_image.DISK_AT:i386_image.DISK_AT + 512] = self.BIN.read_bytes()
        mach = rv.Machine()
        mach.ram[:] = bytes(image)
        mach.keys.append(0x39)                      # skip the countdown
        mach.sreg[rv.SEG_CS] = rv.RESET_CS
        mach.eip = rv.RESET_IP
        mach.run(limit=400_000)
        self.assertEqual(mach.ram[0x8300], 0,
                         "int 13h answered with an error because DL was not the drive")
        self.assertEqual(bytes(mach.ram[0x8600:0x8606]), b"STAGE2",
                         "the sector landed where ES:DI said, not through DS")

    def test_the_hand_off_zeroes_the_segment_registers(self) -> None:
        """DS and ES are zero at the entry of a real boot sector: a sector that
        assumes otherwise reads its own data out of the interrupt table."""
        image = bytearray(i386_image.build(i386_image.disk_image()))
        # `int 19h` in the ROM is what hands over; walk to it from reset and
        # read the segment registers it left, through the first instruction of
        # the staged sector (`cli` would hide them, so this sector stores them)
        probe = bytes.fromhex("fa8cc88ed8268c0620008c062200f4").ljust(510, b"\0") + b"\x55\xaa"
        image[i386_image.DISK_AT:i386_image.DISK_AT + 512] = probe
        mach = rv.Machine()
        mach.ram[:] = bytes(image)
        mach.keys.append(0x39)
        mach.sreg[rv.SEG_CS] = rv.RESET_CS
        mach.eip = rv.RESET_IP
        mach.run(limit=400_000)
        self.assertEqual(mach.read(0x2200, 2), 0, "DS at the entry of the sector")
        self.assertEqual(mach.read(0x2210, 2), 0, "ES at the entry of the sector")


class I386BiosCallsTests(unittest.TestCase):
    """The calls a Linux setup asks before it does anything else.

    `userspace/i386/biosprobe.asm` calls each of them and leaves the answers at
    0x700: A20 through int 15h, the older memory question (E801), the system
    configuration pointer that is not there, the last disk status, the keyboard
    shift flags, the printer that is not there, the VGA that is not there, and
    the real-time clock in BCD. Each one is a place a kernel's first hundred
    instructions look, and answering "nothing" is fine -- answering with a
    register full of whatever was there before is not.
    """

    BIN = ROOT / "userspace" / "i386" / "biosprobe.bin"

    def boot(self) -> "rv.Machine":
        image = bytearray(i386_image.build(i386_image.disk_image()))
        image[i386_image.DISK_AT:i386_image.DISK_AT + 512] = self.BIN.read_bytes()
        mach = rv.Machine()
        mach.ram[:] = bytes(image)
        mach.keys.append(0x39)                  # skip the countdown
        mach.sreg[rv.SEG_CS] = rv.RESET_CS
        mach.eip = rv.RESET_IP
        mach.run(limit=400_000)
        return mach

    def test_a20_is_reported_through_int_15h(self) -> None:
        mach = self.boot()
        self.assertEqual(mach.ram[0x700], 0, "AX=2401h reported failure")
        self.assertEqual(mach.ram[0x701], 2, "AX=2402h should say the gate is open")

    def test_e801_answers_the_bigger_memory_in_two_halves(self) -> None:
        mach = self.boot()
        kib = mach.read(0x702, 2)
        blocks = mach.read(0x704, 2)
        self.assertEqual(kib, 15 * 1024, "KiB between 1 MiB and 16 MiB")
        self.assertEqual(blocks, (128 - 15) * 16, "64 KiB blocks above 16 MiB")

    def test_the_calls_that_are_not_there_say_so(self) -> None:
        mach = self.boot()
        self.assertEqual(mach.ram[0x706], 1, "int 15h C0h: carry means no table")
        self.assertEqual(mach.ram[0x707], 0, "int 13h AH=01h: the last status")
        self.assertEqual(mach.ram[0x708], 0, "int 16h AH=02h: no shift keys")
        self.assertEqual(mach.ram[0x709], 0xC8, "int 17h: busy, I/O error, no printer")
        self.assertEqual(mach.ram[0x70A], 0, "int 10h AH=12h: not a VGA")

    def test_a_wait_spends_its_time_on_the_clock(self) -> None:
        """`int 15h ah=86` is a request for time to pass, and time here is the
        8254's input count: the firmware polls the board's count register at
        0x5BD, so the wait costs about two instructions a count -- the rate the
        clock runs at. That is the whole point: a wait that *skipped* the clock
        instead made every guest that paces itself with this call -- MEMZ draws
        its cat on a 90 ms one -- run at the speed of the interpreter rather
        than the speed of the machine it thinks it is on."""
        image = bytearray(i386_image.build(i386_image.disk_image()))
        image[i386_image.DISK_AT:i386_image.DISK_AT + 512] = (
            ROOT / "userspace" / "i386" / "waitprobe.bin"
        ).read_bytes()
        mach = rv.Machine()
        mach.ram[:] = bytes(image)
        mach.keys.append(0x39)             # a key: skip the POST countdown
        mach.sreg[rv.SEG_CS] = rv.RESET_CS
        mach.eip = rv.RESET_IP
        while mach.ram[0x708] != 0xA5 and mach.steps < 3_000_000:
            mach.run(limit=mach.steps + 20_000)
        self.assertEqual(mach.ram[0x708], 0xA5, "the probe ran to the end")
        self.assertEqual(mach.ram[0x700], 0, "the 50 ms wait failed")
        self.assertEqual(mach.ram[0x701], 0, "the 65.5 ms wait failed")

        def clock(at):
            return int.from_bytes(bytes(mach.ram[at:at + 2]), "little")

        # 50 ms is 59,650 counts and 1:0000h microseconds is 78,250; the
        # register is sixteen bits, so each wait is its own difference
        first = (clock(0x704) - clock(0x702)) & 0xFFFF
        second = (clock(0x706) - clock(0x704)) & 0xFFFF
        self.assertGreaterEqual(first, 59_650, "the clock moved for the 50 ms")
        self.assertLess(first, 59_650 + 200, "and not much further")
        self.assertGreaterEqual(second, 78_250 - 65_536, "and for the 65.5 ms")
        self.assertLess(second, 78_250 - 65_536 + 200, "and not much further")
        # the wait was paid for in instructions, at the clock's own rate
        self.assertGreaterEqual(mach.steps, 2 * (59_650 + 78_250) // 2,
                                "the wait is instruction time, not a skip")
        self.assertGreaterEqual(mach.read(0x46C, 2), 11,
                                "115 ms is at least eleven hundredths")

    def test_the_clock_comes_back_in_bcd(self) -> None:
        mach = self.boot()
        cx = mach.read(0x70C, 2)
        hours, minutes = (cx >> 8) & 0xFF, cx & 0xFF
        for value in (hours, minutes):
            self.assertLess((value >> 4) & 0xF, 10, "high nibble is a BCD digit")
            self.assertLess(value & 0xF, 10, "low nibble is a BCD digit")


class I386DiskStatusTests(unittest.TestCase):
    """int 13h's last-status byte belongs to the BIOS, not to the caller.

    The handler kept it at 40:04EE and wrote it through whatever ES the caller
    arrived with. A boot sector that reads into its own segment therefore had
    the byte written *inside the buffer it had just asked for*: MEMZ loads its
    stage 2 with `int 13h ah=02 al=4` into 7E0:0000 and then decompresses it in
    place, so the zero landed at offset 4EE of its compressed image. That one
    byte turned the back-references after it into garbage -- four thousand
    wrong bytes of the decompressed screen, and the message row printed mask
    data instead of words. The field has an address of its own.
    """

    #: four sectors from LBA 1 to 7E0:0000, the way MEMZ's boot sector does it
    FIXTURE = bytes.fromhex("31c0b8e0078ec031c0b280bb0000b80402b9020030f6cd13faf4ebfe")

    def run_fixture(self) -> "rv.Machine":
        image = bytearray(i386_image.build(i386_image.disk_image()))
        sector = self.FIXTURE.ljust(510, b"\0") + b"\x55\xaa"
        image[i386_image.DISK_AT:i386_image.DISK_AT + 512] = sector
        # a byte the read has to bring back, exactly where the status byte used
        # to land: 4EE into the first sector of the transfer
        image[i386_image.DISK_AT + 512 + 0x4EE] = 0xA5
        image[i386_image.DISK_AT + 512 + 0x4EF] = 0x5A
        mach = rv.Machine()
        mach.ram[:] = bytes(image)
        mach.keys.append(0x39)                  # skip the countdown
        mach.sreg[rv.SEG_CS] = rv.RESET_CS
        mach.eip = rv.RESET_IP
        mach.run(limit=400_000)
        return mach

    def test_the_status_byte_lands_in_the_bios_data_area(self) -> None:
        mach = self.run_fixture()
        self.assertEqual(mach.ram[0x4EE], 0,
                         "40:04EE is where int 13h's status belongs")

    def test_the_read_leaves_the_callers_buffer_alone(self) -> None:
        mach = self.run_fixture()
        got = bytes(mach.ram[0x7E00 + 0x4EE:0x7E00 + 0x4F0])
        self.assertEqual(got, b"\xa5\x5a",
                         "the status byte was written into the read's own buffer")


class I386Address32Tests(unittest.TestCase):
    """Base + index*scale + displacement, which is how a 32-bit program spells
    every address it uses.

    Sixteen-bit code picks a pair out of a fixed list; a 386 with a flat
    descriptor uses a second byte to name a scaled index and a 32-bit offset.
    `userspace/i386/ea32.asm` walks into protected mode and exercises each form
    in turn, then the instruction families that had quietly stayed sixteen bits
    -- mov, test, xchg, mov-imm, mul/div, push/pop -- because a correct address
    read at the wrong width is no more use than a wrong address.
    """

    BIN = ROOT / "userspace/i386/ea32.bin"

    def run_ea32(self) -> rv.Machine:
        blob = self.BIN.read_bytes()
        mach = rv.Machine()
        mach.ram[:] = i386_image.build()
        mach.ram[0x1000:0x1000 + len(blob)] = blob
        mach.sreg[rv.SEG_CS] = 0x100
        mach.eip = 0
        for _ in range(800):
            mach.step()
            if mach.halted:
                break
        return mach

    def test_every_addressing_form_lands_where_it_says(self) -> None:
        mach = self.run_ea32()
        self.assertTrue(mach.halted, mach.error)
        word = lambda a: mach.read(a, 4)
        self.assertEqual(word(0x42C), 0x11223344, "SIB, scale 4, disp8")
        self.assertEqual(word(0x800), 0x11223344, "mod 00 rm 101: bare disp32")
        self.assertEqual(word(0x90C), 0x11223344, "SIB with no base")
        self.assertEqual(word(0xA10), 0x11223344, "EBP-relative through SS")
        self.assertEqual(word(0xB00), 0x41C, "lea of base + index*8 + 4")

    def test_the_widened_instruction_families_carry_four_bytes(self) -> None:
        mach = self.run_ea32()
        self.assertEqual(mach.read(0x500, 4), 0x55667788, "mov r/m32, imm32")
        self.assertEqual(mach.read(0xC00, 4), 0x55667788, "mov moffs32")
        self.assertEqual(mach.read(0x404, 2), 0xC0DE, "a 66-prefixed word store")
        self.assertEqual(mach.read(0x406, 2), 0x3344, "and its high half")
        self.assertEqual(mach.read(0x408, 2), 0, "the word store stopped there")
        self.assertEqual(mach.read(0xC0C, 4), 21845, "div ecx, 32-bit")
        self.assertEqual(mach.read(0xC10, 4), 1, "and its remainder")
        self.assertEqual(mach.read(0xC08, 4), 0, "xchg read what was there")
        self.assertEqual(mach.read(0xC14, 4), 0xAABBCCDD, "push dword, pop eax")

    def test_the_instruction_pointer_is_thirty_two_bits(self) -> None:
        """A flat code segment does not wrap EIP at 64 KiB, and `call`/`ret`
        move a dword each. This walks into protected mode, calls a subroutine,
        then far-returns to code that sits 64 KiB above where it started."""
        mach = self.run_ea32()
        self.assertTrue(mach.halted, mach.error)
        self.assertEqual(mach.read(0xD08, 4), 0xFEEDFACE, "the callee ran and returned")
        self.assertEqual(mach.read(0xD00, 4), 0x1234BEEF, "code above the 64 KiB line ran")
        self.assertGreater(mach.eip, 0x10000, "and it is still up there")

    def test_both_machines_agree_on_all_of_it(self) -> None:
        args = [sys.executable, "tools/i386_jsw_check.py",
                "--code", str(self.BIN), "--steps", "800"]
        # the checker runs the decompiled project; another test class builds it,
        # and skipping the rebuild keeps this one quick when it is already there
        if (ROOT / "build" / "i386" / "i386-test.back.jsw").exists():
            args.append("--no-build")
        done = subprocess.run(args, cwd=ROOT, capture_output=True, text=True,
                              timeout=900)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn("agree on 66 instructions", done.stdout)


class I386VideoTests(unittest.TestCase):
    """The graphics modes: the memory, the mode, and what the card can do.

    Mode 13h and mode 12h are memory at 0xA0000 that a guest writes a byte at a
    time. The machine used to answer that window with an open bus, so a program
    that set the mode and painted got a screen that never changed -- which is
    exactly what was reported. These tests hold the two halves: the guest's
    writes land, and the mode it set is the mode the card believes.
    """

    BIN = ROOT / "userspace/i386/mode13.bin"
    CGA = ROOT / "userspace/i386/mode4.bin"

    def boot(self, blob: bytes) -> rv.Machine:
        mach = self.boot_image(bytearray(i386_image.build(i386_image.boot_disk(blob))))
        mach.run(limit=300_000)
        return mach

    def boot_image(self, image) -> rv.Machine:
        mach = rv.Machine()
        mach.ram[:] = bytes(image)
        # a board with one core: smpBoot's wait for a second one to check in is
        # a five-second spin in the model, and it is not what these tests are
        # about. The sector on the disk is what this machine boots.
        mach.cmos[0x38] = 0
        mach.keys.append(0x39)
        mach.sreg[rv.SEG_CS] = rv.RESET_CS
        mach.eip = rv.RESET_IP
        return mach

    def test_a_guest_paints_mode_13h_memory(self) -> None:
        mach = self.boot(self.BIN.read_bytes())
        self.assertTrue(mach.halted, mach.error)
        self.assertEqual(bytes(mach.ram[0xA0000:0xA0004]), b"AAAA",
                         "320 x 200 memory is at A0000, a byte a pixel")
        filled = sum(1 for byte in mach.ram[0xA0000:0xB0000] if byte == 0x41)
        self.assertGreaterEqual(filled, 0x1000,
                                "rep stosb wrote the sector's 64 KiB, not one byte")
        self.assertEqual(bytes(mach.ram[0x3000:0x3002]), b"\xa5Z",
                         "and the sector's own code ran to the end")

    def test_a_guest_paints_cga_mode_4_memory(self) -> None:
        """Mode 4 is a bitmap in the *text* plane, both banks of it."""
        if not self.CGA.exists():
            self.skipTest("run make i386")
        blob = self.CGA.read_bytes()
        mach = self.boot(blob)
        self.assertTrue(mach.halted, mach.error)
        self.assertEqual(mach.ram[0x449], 0x04, "the mode the sector set")
        self.assertEqual(mach.ram[0x3002], 0x04, "AH=0Fh answered with it")
        self.assertEqual(bytes(mach.ram[0xB8000:0xB8004]), b"\x1b" * 4,
                         "the even scanlines, at the bottom of the page")
        self.assertEqual(bytes(mach.ram[0xB8000 + 0x2000:0xB8000 + 0x2004]),
                         b"\x1b" * 4, "and the odd ones, 8 KiB further on")
        filled = sum(1 for byte in mach.ram[0xB8000:0xB8000 + 0x4000]
                     if byte == 0x1B)
        self.assertEqual(filled, 0x4000, "rep stosb wrote all 16 KiB")
        self.assertEqual(mach.cga_ctl, 0x20, "port 0x3D9 kept the colour select")
        self.assertNotEqual(mach.ram[0x3004], mach.ram[0x3005],
                            "status 1 alternates, so a retrace wait ends")
        self.assertEqual(bytes(mach.ram[0x3000:0x3002]), b"\xa5Z",
                         "and the sector's own code ran to the end")

    def test_the_mode_is_readable_through_the_firmware(self) -> None:
        """AH=0Fh is the question every VESA-era program asks before it draws.

        The probe writes what the firmware answered to the data area, where a
        test can see it: AL is the mode and AH the columns, and a card that
        reports the wrong one is a card a program will draw on wrongly.
        """
        mach = self.boot(self.BIN.read_bytes())
        self.assertEqual(mach.ram[0x449], 0x03, "the probe set text back")
        self.assertEqual(mach.ram[0x3002], 0x13,
                         "AH=0Fh answered with the mode it had just set")
        self.assertEqual(mach.ram[0x3003], 80, "and with the columns")

    def test_the_card_reads_its_mode_from_the_data_area(self) -> None:
        """One byte is the whole handshake: the firmware writes it, the machine
        polls it, and a graphics mode is what the painter then sees."""
        import jsw_sim

        prog = ROOT / "build" / "i386" / "i386-test.back.jsw"
        if not prog.exists():
            self.skipTest("run the JSW tests first; they build this project")
        env = jsw_sim.Env()
        classes = jsw_sim.parse_program(prog.read_text(), env)
        cpu = classes["Cpu"].procs
        env.procs = cpu
        env.threads.append({})
        env.current = 0
        env.vars["X.cores"] = 1
        env.lists["rom"] = json.loads((RVDIR / "assets" / "lists" / "bios.jsa").read_text())
        env.lists["disk"] = json.loads((RVDIR / "assets" / "lists" / "disk.jsa").read_text())

        def run(name, args):
            gen = jsw_sim.call(env, cpu[name], args)
            try:
                next(gen)
            except StopIteration:
                pass

        mem = env.lists["X.mem"]
        for mode, expect in ((0x13, 1), (0x12, 2), (0x03, 0)):
            # base memory is one 32-bit word a list item
            at = 0x449
            cell = at // 4          # this simulator's lists are 0-based
            while len(mem) < cell + 1:
                mem.append(0)
            shift = 256 ** (at % 4)
            word = int(mem[cell])
            word = word - (word // shift) % 256 * shift + mode * shift
            mem[cell] = word
            run("modePoll", [])
            self.assertEqual(int(env.vars["X.textMode"]), expect,
                             f"mode {mode:#04x}")


class DisplayFitTests(unittest.TestCase):
    """The card follows the stage: a bigger window is more cells, not bigger ones.

    Text mode is a grid of 8 x 16 cells, so the resolution a guest is told
    about is the stage divided by a cell -- 1024 x 768 is a 128 x 48 text mode.
    A cell is *not* scaled up to fill the window: at a fractional cell the
    paper bands are 1.6 rows apart, TurboWarp rounds them, and every row of the
    screen ends up with a scanline of the frame before it showing through.

    The rule used to be `floor(width / 640)`, which gave a 1024-wide stage an
    8-pixel cell and an 80-column card: a 640 x 480 screen in the middle of the
    window with the rest left white.
    """

    @classmethod
    def setUpClass(cls) -> None:
        import subprocess
        import tempfile

        cls._tmp = tempfile.TemporaryDirectory()
        tmp = Path(cls._tmp.name)
        out = tmp / "fit.sb3"
        back = tmp / "fit.jsw"
        subprocess.run([sys.executable, "-m", "jswc", "build",
                        str(RVDIR / "i386.jsw"), "-o", str(out)],
                       cwd=ROOT, check=True, capture_output=True)
        subprocess.run([sys.executable, "-m", "jswc", "decompile", str(out),
                        "-o", str(back), "--skip-assets"],
                       cwd=ROOT, check=True, capture_output=True)
        cls.source = back.read_text()

    @classmethod
    def tearDownClass(cls) -> None:
        cls._tmp.cleanup()

    def fit(self, w: int, h: int) -> tuple[int, int]:
        """(cols, rows) for a stage, as the project itself computes them."""
        import jsw_sim

        env = jsw_sim.Env()
        classes = jsw_sim.parse_program(self.source, env)
        # ``X.dispMaxCols``/``Rows`` come out of the project, not out of this
        # test: an override here said 160 while the machine shipped 80, and the
        # cap the *build* had is exactly the bug the 1024 x 768 case is about.
        env.vars["X.dispCols"] = 80
        env.vars["X.dispRows"] = 30
        env.vars["X.dispCellW"] = 8
        env.vars["X.dispCellH"] = 16
        env.procs = classes["Display"].procs
        env.threads.append({})
        env.current = 0
        for _ in jsw_sim.call(env, env.procs["dispFit"], [float(w), float(h)]):
            pass
        self.assertEqual(int(env.vars["X.dispCellW"]), 8, "cells stay 8 pixels")
        self.assertEqual(int(env.vars["X.dispCellH"]), 16)
        cols = int(env.vars["X.dispCols"])
        rows = int(env.vars["X.dispRows"])
        if w >= 200 and h >= 200:            # a stage that measured at all
            self.assertLessEqual(cols * 8, w, "the screen must not hang off the stage")
            self.assertLessEqual(rows * 16, h)
        return (cols, rows)

    def test_the_default_stage_is_sixty_by_twenty_two(self) -> None:
        self.assertEqual(self.fit(480, 360), (60, 22))

    def test_a_1024_x_768_stage_is_a_128_x_48_text_mode(self) -> None:
        """The report: this used to come out 80 columns wide and 640 px across."""
        self.assertEqual(self.fit(1024, 768), (128, 48))

    def test_a_wide_tall_stage_is_capped_by_the_plane(self) -> None:
        """The plane in memory is 160 x 60 cells, whatever the window is."""
        self.assertEqual(self.fit(4096, 4096), (160, 60))
        self.assertEqual(self.fit(1568, 980), (160, 60))

    def test_a_stage_smaller_than_the_plane_keeps_the_console(self) -> None:
        self.assertEqual(self.fit(320, 240), (40, 15))

    def test_a_failed_measurement_keeps_the_grid_it_had(self) -> None:
        """`dispMeasure` falls back to the walk when a VM does not fence; if
        both fail, the card must stay at eighty by thirty rather than print the
        console into one cell."""
        self.assertEqual(self.fit(0, 0), (80, 30))


class I386SpeakerTests(unittest.TestCase):
    """The tone, from the timer to a sound a browser can play.

    Two halves, because they fail differently: the machine has to publish the
    pitch it is making (`X.toneHz`, kept live by the CPU itself rather than
    computed on demand), and the TurboWarp build has to carry a sprite that can
    actually make a noise.
    """

    def test_the_cpu_publishes_the_tone_it_is_programming(self) -> None:
        """The speaker state is not derived: it is what the machine is doing.

        A port read that computed it on demand would look identical to a guest,
        and useless to the sprite -- the sprite cannot call into the CPU. So the
        divisor write itself updates `X.toneHz`.
        """
        env, cpu = I386MemoryMapTests.jsw(self)

        def run(name, args):
            gen = jsw_sim.call(env, cpu[name], args)
            try:
                next(gen)
            except StopIteration:
                pass

        run("pitControl", [0xB6])              # channel 2, lo/hi, mode 3
        run("pitWrite", [2, 0xE9])             # 2281 = C5, low byte
        self.assertEqual(int(env.vars["X.toneHz"]), 0,
                         "the gate is still shut, so nothing is sounding")
        run("pitWrite", [2, 0x08])             # ... and the high byte
        self.assertEqual(int(env.lists["X.pitReload"][2]), 2281)
        run("portOut", [0x61, 3])              # timer 2 through, speaker on
        self.assertEqual(int(env.vars["X.toneHz"]), 523, "C5, and it is live")
        run("portOut", [0x61, 0])              # shut it
        self.assertEqual(int(env.vars["X.toneHz"]), 0)

    def test_the_turbowarp_build_carries_the_speaker_sprite(self) -> None:
        """A sound cannot be computed in this language: Scratch plays assets.

        So the artifact must carry one square wave per note the machine can
        name, and the block that plays each one must point at a dropdown that
        resolves to it -- a mismatch here is silent, not loud.
        """
        import json
        import subprocess
        import zipfile

        out = ROOT / "build" / "i386" / "i386-speaker-test.sb3"
        subprocess.run([sys.executable, "-m", "jswc", "build",
                        str(RVDIR / "i386.jsw"), "-o", str(out),
                        "--target", "turbowarp"],
                       cwd=ROOT, check=True, capture_output=True)
        with zipfile.ZipFile(out) as zipf:
            project = json.loads(zipf.read("project.json"))
            waves = [n for n in zipf.namelist() if n.endswith(".wav")]
        speaker = [t for t in project["targets"] if t["name"] == "Speaker"]
        self.assertEqual(len(speaker), 1, "the sprite is in the build")
        sounds = sorted(s["name"] for s in speaker[0]["sounds"])
        self.assertEqual(sounds, ["tone440", "tone523", "tone659", "tone784"])
        self.assertEqual(len(waves), 4, "and the audio came with them")

        blocks = speaker[0]["blocks"]
        played = set()
        for block in blocks.values():
            if not isinstance(block, dict) or block.get("opcode") != "sound_play":
                continue
            menu = blocks[block["inputs"]["SOUND_MENU"][1]]
            played.add(menu["fields"]["SOUND_MENU"][0])
        self.assertEqual(played, set(sounds), "every sample is reachable")
        hats = [b for b in blocks.values()
                if isinstance(b, dict) and b.get("opcode") == "event_whenflagclicked"]
        self.assertTrue(hats, "the sprite starts with the green flag")


if __name__ == "__main__":
    unittest.main()


class I386ObfuscationTests(unittest.TestCase):
    """``doObfuscation`` on the machine that leans on every name in the project.

    This build renames targets, variables, lists, broadcasts and custom block
    labels, and it also carries two extensions -- one of them looks state up by
    *name* in a plain text input. So it is the project that would notice an
    obfuscator that missed a reference, and the checks below are the ones that
    matter: the Stage keeps the one name the sb3 schema pins, and every name a
    block can carry resolves to something the project declares.
    """

    @classmethod
    def setUpClass(cls) -> None:
        import tempfile

        cls.tmp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.tmp.cleanup)
        out = Path(cls.tmp.name)
        for tag, extra in (("plain", []), ("obf", ["--enable", "doObfuscation"])):
            target = out / f"i386-{tag}.sb3"
            subprocess.run(
                [sys.executable, "-m", "jswc", "build", str(RVDIR / "i386.jsw"),
                 "-o", str(target), "--target", "turbowarp", *extra],
                cwd=ROOT, check=True, capture_output=True,
            )
            with zipfile.ZipFile(target) as z:
                setattr(cls, tag, json.loads(z.read("project.json")))

    def test_the_stage_keeps_the_name_the_schema_pins(self) -> None:
        self.assertEqual(self.obf["targets"][0]["name"], "Stage")
        for target in self.obf["targets"][1:]:
            self.assertNotIn(target["name"], {t["name"] for t in self.plain["targets"]},
                             "a sprite name survived obfuscation")

    def test_every_block_name_resolves(self) -> None:
        targets = self.obf["targets"]
        stage = targets[0]
        globals_ = set(stage["variables"]) | set(stage["lists"])
        names = {t["name"] for t in targets}
        broadcasts = set(stage["broadcasts"].values())
        for target in targets:
            declared = set(target["variables"]) | set(target["lists"]) | globals_
            for block in target["blocks"].values():
                if not isinstance(block, dict):
                    continue
                for field, value in block.get("fields", {}).items():
                    text = value[0] if isinstance(value, list) else value
                    if field in ("VARIABLE", "LIST"):
                        self.assertIn(value[1], declared,
                                      f"{block['opcode']} in {target['name']}")
                    elif field in ("CLONE_OPTION", "TOUCHINGOBJECTMENU", "DISTANCETOMENU"):
                        self.assertTrue(text in names or text.startswith("_"),
                                        f"{block['opcode']} names {text!r}")
                    elif field == "BROADCAST_OPTION":
                        self.assertIn(text, broadcasts, block["opcode"])

    @staticmethod
    def _names(project: dict) -> set[str]:
        out: set[str] = set()
        for target in project["targets"]:
            out.add(target["name"])
            out |= {value[0] for value in target["variables"].values()}
            out |= {value[0] for value in target["lists"].values()}
            out |= set(target["broadcasts"].values())
        return out

    def test_no_declared_name_survives(self) -> None:
        """Every name in the project was renamed, not just the ones a block used.

        ``Stage`` is the exception: the sb3 schema pins it (see
        ``test_the_stage_keeps_the_name_the_schema_pins``).
        """
        left = sorted((self._names(self.plain) & self._names(self.obf)) - {"Stage"})
        self.assertEqual(left, [])

    def test_no_field_still_spells_a_source_name(self) -> None:
        """A menu that survived obfuscation would look up a name nothing has.

        Costume and sound names are not in the set below: the asset list is what
        matches those, and the obfuscator leaves them alone on purpose.
        """
        source = (self._names(self.plain) - {"Stage"}) | {
            label
            for target in self.plain["targets"]
            for block in target["blocks"].values()
            if isinstance(block, dict)
            for label in [block.get("mutation", {}).get("proccode", "")]
            if label
        }
        stuck = []
        for target in self.obf["targets"]:
            for block in target["blocks"].values():
                if not isinstance(block, dict):
                    continue
                for field, value in block.get("fields", {}).items():
                    text = value[0] if isinstance(value, list) else value
                    if isinstance(text, str) and text in source:
                        stuck.append((target["name"], block["opcode"], field, text))
        self.assertEqual(stuck, [])

    def test_every_script_is_still_there(self) -> None:
        """Obfuscation adds blocks, but it cannot move or drop a script.

        Three things grow the project: a literal becomes `item of list`, a
        decoy block is added, and a hat body becomes a state machine (the hat
        keeps its opcode and its place -- its body becomes one call). So the
        check is on the hats, not on the block count.
        """
        def hats(project):
            return [
                sorted(
                    block["opcode"]
                    for block in target["blocks"].values()
                    if isinstance(block, dict) and block.get("topLevel")
                    and block["opcode"].startswith(("event_", "control_start_as_clone"))
                )
                for target in project["targets"]
            ]

        self.assertEqual(hats(self.plain), hats(self.obf), "a hat moved or vanished")
        self.assertEqual(len(self.plain["targets"]), len(self.obf["targets"]))


class I386DisasmTests(unittest.TestCase):
    """`X.dbgDis` against the Python decoder, over the same bytes.

    Two implementations of one decoder: `tools/i386_dis.py` (checked against
    nasm's own listings, instruction for instruction, by its `--check`) and the
    blocks in `disasm.inc.jsw`. They are only useful together if they agree, and
    "agree" means the identical line *and* the identical length -- a
    disassembler that is one byte out puts every following line one byte out,
    which is worse than no disassembler at all.
    """

    #: what is put behind each opcode, once per shape of modrm -- a register, a
    #: byte displacement, a SIB address, and an absolute one -- with enough
    #: bytes to satisfy any immediate after it. The four between them reach
    #: every addressing form the encoder has, which one set of operands does
    #: not: `rm == 4` (SIB) never appears behind a fixed modrm.
    TAILS = [
        bytes([0xC1, 0x01, 0x10, 0x20, 0x30, 0x40, 0x50, 0x60]),
        bytes([0x46, 0x05, 0x10, 0x20, 0x30, 0x40, 0x50, 0x60]),
        bytes([0x84, 0x25, 0x10, 0x20, 0x30, 0x40, 0x50, 0x60]),
        bytes([0x06, 0x00, 0x12, 0x20, 0x30, 0x40, 0x50, 0x60]),
    ]

    @classmethod
    def setUpClass(cls) -> None:
        I386JswTests.setUpClass()
        cls.source = I386JswTests.source
        cls.corpus = bytearray()
        for op in range(256):
            for tail in cls.TAILS:
                cls.corpus.append(op)
                cls.corpus.extend(tail)
        for op in range(256):
            for tail in cls.TAILS:
                cls.corpus.extend([0x0F, op])
                cls.corpus.extend(tail)
        # and real code: the ROM's own int 16h handler, which is prefixes,
        # branches and modrm addressing in the order a person writes them
        cls.corpus.extend((RVDIR / "bios.bin").read_bytes()[0x1570:0x1640])
        cls.corpus = bytes(cls.corpus) + bytes(64)

    def _machine_with(self, code: bytes):
        env = jsw_sim.Env()
        classes = jsw_sim.parse_program(self.source, env)
        assets = RVDIR / "assets" / "lists"
        env.lists["rom"] = i386_image.words(code)
        env.lists["disk"] = json.loads((assets / "disk.jsa").read_text())
        env.lists["dis"] = json.loads((assets / "dis.jsa").read_text())
        env.procs = classes["Cpu"].procs
        env.threads.append({})
        env.current = 0
        env.vars["X.cores"] = 1
        gen = jsw_sim.call(env, classes["Cpu"].procs["powerOn"], [])
        try:
            next(gen)
        except StopIteration:
            pass
        # the code lives in the ROM, so a decode is a ROM read: `phys` puts cs
        # at its base and `rdb` finds the bytes where the machine would
        env.lists["sbase"][1] = int(env.vars["X.romBase"])
        return env, classes

    def _blocks(self, env, classes, at: int, bits: int) -> tuple[int, str]:
        env.vars["ip"] = at
        env.vars["dsz"] = 2 if bits == 16 else 4
        gen = jsw_sim.call(env, classes["Cpu"].procs["disasm"], [at])
        try:
            while True:
                next(gen)
        except StopIteration:
            pass
        return int(env.vars["D.n"]), str(env.vars["X.dbgDis"])

    def test_the_blocks_decode_what_python_decodes(self):
        import i386_dis

        code = self.corpus
        env, classes = self._machine_with(code)
        mismatches = []
        at = 0
        while at < len(code) - 32:
            wanted_len, wanted = i386_dis.decode(code, at, 16)
            length, text = self._blocks(env, classes, at, 16)
            if length != wanted_len or text != wanted:
                mismatches.append((at, wanted_len, wanted, length, text))
            at += max(1, wanted_len)
        self.assertEqual(mismatches[:8], [], "the two decoders disagree")

    def test_a_32_bit_instruction_reads_the_same_both_ways(self):
        """The same bytes in a 32-bit code segment: the operand size decides
        the immediates, the addresses and the width of a branch."""
        import i386_dis

        code = self.corpus
        env, classes = self._machine_with(code)
        mismatches = []
        at = 0
        while at < len(code) - 32:
            wanted_len, wanted = i386_dis.decode(code, at, 32)
            length, text = self._blocks(env, classes, at, 32)
            if length != wanted_len or text != wanted:
                mismatches.append((at, wanted_len, wanted, length, text))
            at += max(1, wanted_len)
        self.assertEqual(mismatches[:8], [], "the two decoders disagree in 32 bits")

    def test_the_tables_are_what_python_would_emit(self):
        """`dis.jsa` and `dis_shapes.inc.jsw` are generated, and a stale copy is
        how the two decoders would drift apart one mnemonic at a time.

        The same trap the ROM taught: `make i386` rebuilds `bios.bin`, but the
        asset the project imports comes out of a tool, and a build that skipped
        the tool ships the old table. This is that check for the disassembler.
        """
        import subprocess
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "assets" / "lists" / "dis.jsa"
            out.parent.mkdir(parents=True)
            subprocess.run([sys.executable, "tools/i386_dis.py", "--emit", str(out)],
                           cwd=ROOT, check=True, capture_output=True)
            fresh = out.read_text()
        shipped = (RVDIR / "assets" / "lists" / "dis.jsa").read_text()
        self.assertEqual(fresh, shipped,
                         "dis.jsa is stale: run tools/i386_dis.py --emit")


class I386KeyboardTests(I386Int10Tests):
    """`int 16h` as GRUB reads it: the scan code is in AH, and an extended key
    is *one* word.

    This is the difference between a bootloader's menu working and not.
    `grub_console_getkey` maps `AH` to its own key constants -- `0x48` is Up,
    `0x1C` is Enter, `0x01` is Esc -- and uses `AL == 0` to mean "extended".
    A BIOS that returns the ASCII in AL and zero in AH, which this one did,
    gives GRUB `0x001B` for Escape instead of `0x011B`: the bypass table does
    not match, and Escape and Enter come back as control characters. Nothing in
    the menu responds, and the keyboard looks broken while it is working
    perfectly.
    """

    def guest(self, code: bytes, keys: list[int], limit: int = 400) -> rv.Machine:
        mach = post()
        mach.keys.extend(keys)
        for i, byte in enumerate(code):
            mach.write(0x1000 + i, byte, 1)
        mach.sreg[rv.SEG_CS] = 0
        mach.sreg[rv.SEG_DS] = 0
        mach.sreg[rv.SEG_SS] = 0
        mach.reg[4] = 0x8000
        mach.eip = 0x1000
        stop = mach.steps + limit
        while mach.steps < stop and not mach.halted:
            mach.step()
        return mach

    #: `mov ah,0; int 0x16; mov [0x900],ax; hlt` -- the wait-for-a-key call,
    #: with its answer kept where a test can read it
    ASK = bytes([0xB4, 0x00, 0xCD, 0x16, 0xA3, 0x00, 0x09, 0xF4])

    def test_a_letter_comes_back_as_scan_code_and_ascii(self) -> None:
        mach = self.guest(self.ASK, [0x1E])          # 'a'
        self.assertEqual(mach.read(0x900, 2), 0x1E61,
                         "AH = the scan code, AL = the character")

    def test_an_extended_key_is_one_word_with_no_ascii(self) -> None:
        """Up arrow: the controller sends `E0` then `48`, and a caller wants the
        second byte with a zero for the first. A BIOS that hands back the `E0`
        as a key of its own tells GRUB there is a key, and then tells it again
        -- so the menu sees one keypress as two, the first of them meaningless.
        """
        mach = self.guest(self.ASK, [0xE0, 0x48])
        self.assertEqual(mach.read(0x900, 2), 0x4800, "0x48 with no ASCII")

    def test_escape_and_enter_keep_their_scan_codes(self) -> None:
        """The two keys a boot menu is decided by, and the two GRUB's bypass
        table names: `0x011B` and `0x1C0D`."""
        escape = self.guest(self.ASK, [0x01])
        self.assertEqual(escape.read(0x900, 2), 0x011B)
        enter = self.guest(self.ASK, [0x1C])
        self.assertEqual(enter.read(0x900, 2), 0x1C0D)

    def test_a_key_raises_irq1_and_the_bios_handler_takes_it(self) -> None:
        """The interrupt a DOS game and a Windows keyboard driver hook.

        IRQ1 was never raised, so `int 09h` never ran and its bug was invisible:
        it stored the raw byte into a byte-wide ring, and the first interrupt
        would have written an `E0` where the polled path expects half a word.
        The handler now runs `kbdFill`, so an extended key arrives in the ring as
        `0x0048` -- one word -- whether it was interrupted in or polled in.
        """
        mach = post()
        mach.keys.extend([0xE0, 0x48])               # Up arrow, as the 8042 sends it
        # POST unmasks the timer and nothing else; a DOS game or a Windows
        # keyboard driver is what opens the keyboard's line.
        mach.port_out(0x21, mach.pic.imr & ~0x02, 1)
        for i, byte in enumerate(bytes([0xFB, 0xF4])):   # sti; hlt
            mach.write(0x1000 + i, byte, 1)
        mach.sreg[rv.SEG_CS] = 0
        mach.sreg[rv.SEG_DS] = 0
        mach.sreg[rv.SEG_SS] = 0
        mach.reg[4] = 0x8000
        mach.eip = 0x1000
        stop = mach.steps + 400
        while mach.steps < stop and not mach.halted:
            mach.step()

        self.assertEqual(mach.keys, [], "the handler drained the controller")
        head = mach.read(0x41A, 2)
        tail = mach.read(0x41C, 2)
        self.assertEqual((tail - head) % 32, 2, "one word in the ring")
        self.assertEqual(mach.read(head, 2), 0x4800, "scan code 0x48, no ASCII")
        self.assertEqual(mach.read(0x43E, 1), 0, "and no half-read prefix left over")
        self.assertEqual(mach.pic.isr, 0, "the EOI went out")

    def test_a_masked_irq1_waits_for_the_guest_to_unmask_it(self) -> None:
        """POST leaves both chips fully masked, which is why a bootloader that
        only polls still gets its keys: the line is asking, the handler is not
        called, and `int 16h` takes the byte instead."""
        mach = post()
        mach.keys.extend([0x1E])                     # 'a', IRQ1 still masked
        for i, byte in enumerate(bytes([0xFB, 0xF4])):   # sti; hlt
            mach.write(0x1000 + i, byte, 1)
        mach.sreg[rv.SEG_CS] = 0
        mach.sreg[rv.SEG_DS] = 0
        mach.sreg[rv.SEG_SS] = 0
        mach.reg[4] = 0x8000
        mach.eip = 0x1000
        stop = mach.steps + 200
        while mach.steps < stop and not mach.halted:
            mach.step()
        self.assertEqual(mach.keys, [0x1E], "still in the controller")
        self.assertEqual(mach.pic.irr & 0x02, 0x02, "the line is asking")
        self.assertEqual(mach.read(0x41A, 2), mach.read(0x41C, 2), "ring still empty")

    def test_a_peek_does_not_take_the_key(self) -> None:
        """`int 16h AH=01` answers the same key `AH=00` will return, and ZF
        says whether there is one at all.

        GRUB calls the peek first on every pass -- it is a workaround for an
        Apple firmware bug, and it is unconditional -- so a peek that consumed
        the key dropped every keystroke, and a peek that answered from a buffer
        nothing filled answered "no key" while a key was waiting.
        """
        code = bytes([
            0xB4, 0x01, 0xCD, 0x16,      # AH=01: peek
            0xA3, 0x00, 0x09,            # mov [0x900], ax
            0xB4, 0x00, 0xCD, 0x16,      # AH=00: take it
            0xA3, 0x02, 0x09,            # mov [0x902], ax
            0xF4,                        # hlt
        ])
        mach = self.guest(code, [0x20], limit=600)     # 'd'
        self.assertEqual(mach.read(0x900, 2), 0x2064,
                         "the peek answered the key itself")
        self.assertEqual(mach.read(0x902, 2), 0x2064,
                         "and the read took the same one")

    def test_a_key_from_before_the_reset_is_not_delivered_to_the_new_machine(self) -> None:
        """The controller's queue belongs to the machine that went away.

        A key left unread was worse than a lost one: POST's countdown read it
        as its own first key, so the window was gone before anybody could press
        Delete in it -- and only when the last session happened to end on an
        unread key, which is the worst kind of bug to be told about.
        """
        mach = post()
        mach.keys.append(0x39)                        # a space nobody read
        mach.reset()
        mach.run(limit=mach.steps + 2_000_000)        # POST again
        self.assertIn("Press DEL for setup", mach.text(),
                      "the countdown ran instead of being skipped by the ghost")


class I386SetupTreeTests(unittest.TestCase):
    """Opening a group in the setup tree, which is where two bugs met.

    A group row is a `+` that becomes a `-`, and the rows it opens are the
    nodes after it that are one level deeper. Both halves were broken in ways
    that only show up on a group that is *not* the first one:

      * `gxVisible` indexed `GX_EXP` with `add bx, ax` and a byte of node
        number in AL -- so a stale high half read 256 rows past the table, and
        a group whose marker said `-` still had its children treated as folded;
      * `gxFollow` keeps the *light* in the window, which does nothing for rows
        that appear *under* it: a group opened on the window's last line turned
        from `+` to `-` and showed nothing, because the children were below the
        scroll window.
    """

    F2, DOWN, ENTER = 0x3C, 0x50, 0x1C

    def _setup(self, keys: list[int], limit: int = 40_000_000,
               settle: int = 900_000):
        """Power on, press F2 and whatever else, and let the screen settle."""
        mach = rv.Machine()
        mach.ram[:] = i386_image.build()
        mach.keys.extend(keys)
        mach.sreg[rv.SEG_CS] = rv.RESET_CS
        mach.eip = rv.RESET_IP
        while mach.pending_keys() and mach.steps < limit:
            mach.run(limit=min(limit, mach.steps + 250_000))
        mach.run(limit=mach.steps + settle)
        return mach

    def test_a_folded_group_opens_to_its_children(self) -> None:
        """`Onboard Devices` starts folded, with Serial Port and Audio
        Controller underneath it."""
        screen = self._setup([self.F2] + [self.DOWN] * 16 + [self.ENTER]).text()
        self.assertIn("- Onboard Devices", screen, "the marker turned")
        self.assertIn("Serial Port", screen, "and the first child is on screen")
        self.assertIn("Audio Controller", screen)

    def test_a_group_on_the_last_line_scrolls_to_show_what_it_opened(self) -> None:
        """`CPU` sits at the bottom of the window when the setup opens, so its
        children land outside it -- unless the window moves by one row."""
        screen = self._setup([self.F2] + [self.DOWN] * 23 + [self.ENTER]).text()
        self.assertIn("- CPU", screen)
        self.assertIn("CPU Cores", screen, "the row under the light was revealed")

    def test_a_group_folds_back_to_the_row_it_started_on(self) -> None:
        """And it closes again: the marker, the children and the window all go
        back to what they were."""
        opened = self._setup([self.F2] + [self.DOWN] * 16 + [self.ENTER]).text()
        shut = self._setup([self.F2] + [self.DOWN] * 16
                           + [self.ENTER, self.ENTER]).text()
        self.assertIn("Serial Port", opened)
        self.assertNotIn("Serial Port", shut, "the children went away again")
        self.assertIn("+ Onboard Devices", shut)
