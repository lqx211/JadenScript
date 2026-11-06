"""The 80386 line: the model, the firmware and the boot chain.

The same rule as the RISC-V tests: `tools/i386_ref.py` is the executable spec,
`userspace/i386/bios.asm` is the firmware that runs on it, and a boot sector is
a real 512-byte sector read through a real int 13h.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import i386_image  # noqa: E402
import jsw_sim  # noqa: E402
import i386_ref as rv  # noqa: E402

RVDIR = ROOT / "userspace" / "i386"


def boot(limit: int = 300_000, cores: int = 1) -> rv.Machine:
    mach = rv.Machine()
    mach.ram[:] = i386_image.build()
    mach.cores = cores
    # POST waits half a second for Delete; any other key boots at once, so a
    # space keeps these tests off the wait without opening the utility
    mach.keys.append(0x39)
    mach.sreg[rv.SEG_CS] = rv.RESET_CS
    mach.eip = rv.RESET_IP
    mach.run(limit=limit)
    return mach


class I386BootTests(unittest.TestCase):
    """Power-on, POST, int 19h, and a boot sector that calls back into the ROM."""

    def test_any_other_key_skips_the_setup_wait(self) -> None:
        """POST waits half a second for Delete; a space gets on with the boot."""
        quick = boot()                                  # boot() presses space
        self.assertIn("Booting from disk 0x80", quick.text())
        slow = rv.Machine()
        slow.ram[:] = i386_image.build()
        slow.sreg[rv.SEG_CS] = rv.RESET_CS
        slow.eip = rv.RESET_IP
        slow.run(limit=120_000)                         # still inside the wait
        self.assertIn("Press DEL or F2", slow.text())
        self.assertNotIn("Booting from disk", slow.text())

    def test_post_prints_what_it_found(self) -> None:
        mach = boot(limit=300_000)
        screen = mach.text()
        for line in ("JSW 80386 BIOS v0.02", "CMOS boot #", "CPU: 80386",
                     "Memory: 640K", "Video: 80x30 text", "Booting from disk 0x80"):
            self.assertIn(line, screen)

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

    def test_every_cell_carries_the_same_attribute(self) -> None:
        """The teletype takes its attribute from bl, so a routine that calls
        int 10h directly paints the text in whatever bl happens to hold."""
        mach = boot(limit=45_000)
        for row in range(25):
            for col in range(80):
                off = rv.TEXT_BASE + (row * 80 + col) * 2
                char, attr = mach.ram[off], mach.ram[off + 1]
                if char not in (0x00, 0x20):
                    self.assertEqual(
                        attr, 0x07, f"cell {row}:{col} {chr(char)!r} is {attr:#04x}"
                    )

    def test_the_segment_wrap_is_twenty_bits(self) -> None:
        """0xFFFF:0x0010 is the bottom of memory on a machine with no A20 gate."""
        mach = rv.Machine()
        self.assertEqual(mach.phys(0xFFFF, 0x0010), 0x00000)
        self.assertEqual(mach.phys(0xF000, 0xFFF0), 0xFFFF0)

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


class I386SetupTests(unittest.TestCase):
    """The setup screen: a key press, a changed byte, and a POST that reads it."""

    def _setup(self, keys: list[int], limit: int = 8_000_000,
               settle: int = 900_000):
        """Boot to setup, feed the keys, and let one redraw finish.

        The model is a Python interpreter, so a key costs what the machine
        spends on a repaint: the budget has to be generous, but it stops as
        soon as the last key is taken and the screen settles.
        """
        mach = rv.Machine()
        mach.ram[:] = i386_image.build()
        mach.keys.extend(keys)
        mach.sreg[rv.SEG_CS] = rv.RESET_CS
        mach.eip = rv.RESET_IP
        while mach.keys and mach.steps < limit:
            mach.run(limit=min(limit, mach.steps + 250_000))
        mach.run(limit=mach.steps + settle)
        return mach

    def test_delete_opens_it_on_the_main_page(self) -> None:
        mach = self._setup([0x53])                     # Delete
        screen = mach.text()
        self.assertIn("JSW BIOS Setup Utility", screen)
        tabs = screen.split("\n")[2]          # the menu bar, under the border
        for name in ("Main", "Info", "Exit"):
            self.assertIn(name, tabs)
        self.assertIn("Base memory", screen)
        self.assertIn("Battery: OK", screen)

    def test_the_right_arrow_walks_to_the_information_page(self) -> None:
        mach = self._setup([0x53, 0x4D])               # Del, Right
        screen = mach.text()
        self.assertIn("System information", screen)
        self.assertIn("Video: VGA text, 80x30", screen)
        self.assertNotIn("Base memory", screen)

    def test_enter_edits_the_selected_value(self) -> None:
        keys = [0x53, 0x50, 0x50, 0x1C, 0x4D, 0x1C]    # Del, Down x2, Enter, Right, Enter
        mach = self._setup(keys)
        self.assertIn("Year", mach.text())
        self.assertIn("2026", mach.text(), "the year row went from 25 to 26")
        self.assertEqual(mach.cmos[0x09], 0x25, "nothing is written until it is saved")

    def test_escape_in_the_dialog_puts_the_old_value_back(self) -> None:
        keys = [0x53, 0x50, 0x50, 0x1C, 0x4D, 0x01]    # ... then Esc
        mach = self._setup(keys)
        self.assertIn("2025", mach.text(), "the edit was thrown away")

    def test_saving_from_the_exit_page_and_the_next_post_reads_it_back(self) -> None:
        keys = [0x53] + [0x50] * 9 + [0x1C, 0x4B, 0x1C]   # memory, edit, -1, keep
        keys += [0x4D, 0x4D, 0x1C]                        # Exit page, "Save and exit"
        mach = self._setup(keys)
        self.assertEqual(mach.cmos[0x16] * 256 + mach.cmos[0x15], 576)
        # and a fresh POST believes it
        mach.sreg[rv.SEG_CS] = rv.RESET_CS
        mach.eip = rv.RESET_IP
        mach.halted = False
        mach.steps = 0
        mach.run(limit=300_000)
        self.assertIn("Memory: 576K conventional", mach.text())

    def test_the_exit_page_saves(self) -> None:
        keys = [0x53, 0x4D, 0x4D, 0x1C]                # Del, Right, Right, Enter
        mach = self._setup(keys)
        self.assertEqual(mach.cmos[0x31], 0xA5, "the setup checksum was written")

    def test_every_cell_of_the_setup_screen_is_painted(self) -> None:
        """A clear loop that stops early leaves the POST paper showing."""
        mach = self._setup([0x53])
        for row in range(30):
            for col in range(80):
                if col in (0, 45, 79):
                    continue        # the frame's own columns
                cell = mach.read(0xB8000 + (row * 80 + col) * 2, 2)
                self.assertNotEqual(cell, 0x0720, f"cell {row},{col} kept the POST paper")
        # the strip along the bottom is eighty cells, the last one included
        self.assertEqual(mach.read(0xB8000 + (29 * 80 + 79) * 2, 2), 0x7020)

    def test_the_exit_page_keeps_its_own_rows(self) -> None:
        mach = self._setup([0x53, 0x4D, 0x4D, 0x50])   # Del, Right x2, Down
        screen = mach.text()
        self.assertIn("Save and exit", screen)
        self.assertIn("Discard and exit", screen)
        self.assertNotIn("Month", screen, "the exit page repainted itself")

    def test_discard_and_exit_boots_and_writes_nothing(self) -> None:
        keys = [0x53, 0x50, 0x50, 0x1C, 0x4D, 0x1C]   # Year -> 2026, kept
        keys += [0x4D, 0x4D, 0x50, 0x1C]               # Exit, Discard, Enter
        mach = self._setup(keys)
        self.assertEqual(mach.cmos[0x09], 0x25, "the year was not written")
        self.assertIn("Booting from disk 0x80", mach.text())

    def test_escape_asks_before_it_leaves(self) -> None:
        mach = self._setup([0x53, 0x01])               # Del, Esc
        screen = mach.text()
        self.assertIn("Save the changes?", screen)
        self.assertIn("Cancel", screen)

    def test_discarding_keeps_the_old_values(self) -> None:
        keys = [0x53, 0x50, 0x50, 0x1C, 0x4D, 0x1C, 0x01, 0x4D, 0x1C]
        # Del, Down x2, Enter, Right, Enter, Esc, Right (Discard), Enter
        mach = self._setup(keys)
        self.assertEqual(mach.cmos[0x09], 0x25, "the year was not written")


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
        mach = self._run(bytes([0x0F, 0xA2]), r0=0)
        self.assertEqual(mach.reg[3], 0x756E6547, "'Genu'")
        self.assertEqual(mach.reg[2], 0x6C65746E, "'ntel'")
        self.assertEqual(mach.reg[1], 0x49656E69, "'ineI'")
        mach = self._run(bytes([0x0F, 0xA2]), r0=1)
        self.assertEqual(mach.reg[0] >> 8, 4, "family 4")


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


class I386SmpTests(unittest.TestCase):
    """Cores 2..N are woken through the chipset and check in through memory."""

    def test_one_core_says_so_and_stays_quiet(self) -> None:
        screen = boot(limit=60_000).text()
        self.assertIn("Cores: 001", screen)
        self.assertNotIn("SMP:", screen)

    def test_two_cores_check_in(self) -> None:
        mach = boot(limit=60_000, cores=2)
        screen = mach.text()
        self.assertIn("Cores: 002", screen)
        self.assertIn("SMP: 002 core(s) online", screen)
        self.assertEqual(mach.ram[0x8402], 2, "core 2 wrote its id to the roster")
        self.assertEqual(mach.ram[0x8502], 1, "core 2 checked in")

    def test_four_cores_check_in(self) -> None:
        mach = boot(limit=60_000, cores=4)
        self.assertIn("SMP: 004 core(s) online", mach.text())
        for core in (2, 3, 4):
            self.assertEqual(mach.ram[0x8400 + core], core)
            self.assertEqual(mach.ram[0x8500 + core], 1)

    def test_the_setup_screen_keeps_to_its_three_colours(self) -> None:
        """The teletype takes its attribute from bl, so any routine that prints
        without setting it paints in whatever bl was left holding."""
        mach = boot(limit=600_000)
        mach.keys.append(0x53)                      # Delete, at the prompt
        mach.sreg[rv.SEG_CS] = rv.RESET_CS
        mach.eip = rv.RESET_IP
        mach.halted = False
        mach.steps = 0
        mach.run(limit=600_000)
        self.assertIn("JSW BIOS Setup Utility", mach.text())
        used = set()
        for row in range(25):
            for col in range(80):
                off = rv.TEXT_BASE + (row * 80 + col) * 2
                if mach.ram[off] not in (0x00, 0x20):
                    used.add(mach.ram[off + 1])
        self.assertEqual(used, {0x1F, 0x1E, 0x70}, "paper, header and selection")

    def test_the_trampoline_lands_where_the_aps_are_sent(self) -> None:
        mach = boot(limit=60_000, cores=2)
        self.assertEqual(mach.ram[0x8000], 0xFA, "cli is the first byte of the trampoline")


class I386JswTests(unittest.TestCase):
    """The Scratch machine against the model, instruction by instruction.

    `tools/jsw_sim.py` runs the *decompiled* project, so what is compared is the
    artifact the browser loads, not the source it came from.
    """

    @classmethod
    def setUpClass(cls) -> None:
        import subprocess

        out = ROOT / "build" / "i386" / "i386.sb3"
        back = ROOT / "build" / "i386" / "i386.back.jsw"
        subprocess.run([sys.executable, "-m", "jswc", "build",
                        str(RVDIR / "i386.jsw"), "-o", str(out)],
                       cwd=ROOT, check=True, capture_output=True)
        subprocess.run([sys.executable, "-m", "jswc", "decompile", str(out),
                        "-o", str(back), "--skip-assets"],
                       cwd=ROOT, check=True, capture_output=True)
        cls.source = back.read_text()

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
        env.lists["sr"][1] = 0x1000                      # sr.get(2) is cs
        env.lists["sr"][2] = 0x0A00                      # sr.get(3) is ss
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


if __name__ == "__main__":
    unittest.main()
