"""The RV64 machine stays equal to QEMU.

Three things have to agree for the Scratch emulator to be worth anything:

1. ``qemu-system-riscv64`` running ``userspace/rv64/payload.elf`` — the oracle.
2. ``tools/rv64_ref.py``, the Python reading of the same ISA — the spec.
3. ``userspace/rv64/rv64.jsw``, the Scratch build — the implementation.

(1) and (2) are compared here, automatically. (3) cannot run in a test suite
without a browser, so its verified output is checked in beside the oracle
(``rv64_jsw_verified.txt``) and a change to either golden has to be deliberate.

The payload itself is the interesting part: it exercises the places where a
64-bit machine built out of 32-bit halves usually goes wrong — carry and borrow
across the halves, word operations that sign-extend, shifts that only look at
the low 6 bits, multiply-high with mixed signs, and division by zero.
"""

from __future__ import annotations

import json
import struct
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RVDIR = ROOT / "userspace" / "rv64"
JSW = RVDIR / "rv64.jsw"
#: the machine is four files: the board, the cpu, the display and the controller
MODULES = [RVDIR / "rv64.jsw", RVDIR / "core.jsw", RVDIR / "display.jsw", RVDIR / "net.jsw",
           RVDIR / "input.jsw"]
ELF = RVDIR / "payload.elf"
BIOS = RVDIR / "bios.elf"
IMAGE = RVDIR / "assets" / "lists" / "X.image.jsa"
DATA = ROOT / "tests" / "data"
QEMU_OUT = DATA / "rv64_qemu.txt"
JSW_OUT = DATA / "rv64_jsw_verified.txt"
DISK_IMAGE = RVDIR / "disk.img"
TIMER_ELF = RVDIR / "timer.elf"
TIMER_OUT = DATA / "rv64_timer_qemu.txt"
JSW_DISK_OUT = DATA / "rv64_disk_jsw.txt"
FONT = RVDIR / "assets" / "lists" / "font.jsa"
ROWS_PER_GLYPH = 15

sys.path.insert(0, str(ROOT / "tools"))


class Rv64ReferenceTests(unittest.TestCase):
    """The Python reference has to reproduce the physical machine."""

    @classmethod
    def setUpClass(cls) -> None:
        if not ELF.exists():
            raise unittest.SkipTest(f"{ELF} is not built (see docs/RV64.md)")
        if not QEMU_OUT.exists():
            raise unittest.SkipTest(f"{QEMU_OUT} is missing")

    def _run(self) -> tuple[str, int, str | None]:
        import rv64_ref

        mach = rv64_ref.Machine()
        mach.pc = rv64_ref.load_elf(mach, ELF.read_bytes())
        while mach.steps < 400_000:
            mach.step()
            if b"== done ==" in bytes(mach.out):
                break
        return mach.out.decode("latin-1"), mach.steps, mach.error

    def test_matches_qemu(self) -> None:
        text, steps, err = self._run()
        self.assertIsNone(err, err)
        self.assertEqual(text.rstrip("\n"), QEMU_OUT.read_text(encoding="latin-1").rstrip("\n"),
                         "the reference and QEMU disagree")

    def test_retires_the_expected_number_of_instructions(self) -> None:
        _text, steps, _err = self._run()
        self.assertGreater(steps, 9_000)
        self.assertLess(steps, 12_000, "the payload changed; the checked-in reference output needs regenerating")

    def test_scratch_build_was_verified_against_the_same_output(self) -> None:
        """The checked-in JSW result is the oracle, or the port drifted."""
        self.assertEqual(JSW_OUT.read_text(encoding="latin-1").rstrip("\n"),
                         QEMU_OUT.read_text(encoding="latin-1").rstrip("\n"),
                         "the Scratch run no longer matches QEMU")

    def test_the_two_the_32_bit_halves_are_exact(self) -> None:
        """Every value the machine holds per half must be a double-exact integer."""
        import rv64_ref

        mach = rv64_ref.Machine()
        mach.pc = rv64_ref.load_elf(mach, ELF.read_bytes())
        for _ in range(2_000):
            mach.step()
            for value in mach.regs:
                self.assertLess(value, 1 << 64)


class Rv64ImageTests(unittest.TestCase):
    """`X.image` is generated, so it has to match the ELF it came from."""

    def test_image_matches_the_elf(self) -> None:
        """Whichever payload is in the slot has to be the one the list holds."""
        if not ELF.exists() or not IMAGE.exists():
            self.skipTest("payload not built")
        sys.path.insert(0, str(ROOT / "tools"))
        import gen_rv64_image

        source_elf = ELF
        stamp = IMAGE.with_suffix(".src")
        if stamp.exists():
            named = Path(stamp.read_text(encoding="utf-8").strip())
            if not named.is_absolute():
                named = ROOT / named
            if named.exists():
                source_elf = named
        expected = gen_rv64_image.flatten(source_elf.read_bytes())
        got = bytes(json.loads(IMAGE.read_text(encoding="utf-8")))
        self.assertEqual(len(got), len(expected),
                         f"{source_elf.name}: regenerate with tools/gen_rv64_image.py")
        self.assertEqual(got, expected, "regenerate with tools/gen_rv64_image.py")

    def test_the_image_fits_the_declared_ram(self) -> None:
        if not IMAGE.exists():
            self.skipTest("image not generated")
        text = JSW.read_text(encoding="utf-8")
        import re

        size = int(re.search(r"public X\.ramSize = (\d+);", text).group(1))
        items = len(json.loads(IMAGE.read_text(encoding="utf-8")))
        self.assertLess(items, size - 8192, "leave room for a stack")


class Rv64SourceTests(unittest.TestCase):
    """Guards for the two mistakes this port actually made."""

    def test_pow2_is_indexed_one_up(self) -> None:
        """`X.pow2` is a Scratch list: item k+1 holds 2^k, never item k."""
        import re

        board = JSW.read_text(encoding="utf-8")
        body = board.split("public X.pow2 = [", 1)[1].split("];", 1)[0]
        self.assertEqual(len([v for v in body.split(",") if v.strip()]), 64)
        for path in MODULES:
            for expr in re.findall(r"X\.pow2\.get\(([^)]*)\)", path.read_text(encoding="utf-8")):
                self.assertTrue(expr.strip().endswith("+ 1"),
                                f"{path.name}: X.pow2.get({expr}) is off by one")

    def test_no_javascript_math_left_in_the_source(self) -> None:
        """This dialect has Operators.math(...); Math.* silently compiles to an error."""
        for path in MODULES:
            self.assertNotIn("Math.", path.read_text(encoding="utf-8"), path.name)

    def test_the_modules_are_included_and_resolve(self) -> None:
        """Every `// include` names a file that exists, and nothing is included twice."""
        from jswc import include

        source = include.expand(JSW)
        for name in ("core.jsw", "display.jsw", "net.jsw"):
            self.assertTrue(any(f.endswith(name) for f in source.files), name)
        self.assertEqual(len(source.files), len(set(source.files)), "a module is included twice")

    def test_each_module_owns_one_sprite(self) -> None:
        """A module is a class is a sprite: the CPU, the painter and the controller."""
        for name, cls in (("core.jsw", "class Core {"), ("display.jsw", "class Display {"),
                          ("net.jsw", "class Net {"), ("input.jsw", "class Input {")):
            text = (RVDIR / name).read_text(encoding="utf-8")
            self.assertIn(cls, text, name)
            self.assertEqual(text.count("class "), 1, f"{name} declares more than one class")

    def test_only_the_display_touches_the_pen(self) -> None:
        """Pen blocks outside display.jsw would let another module fight the painter."""
        for path in MODULES:
            if path.name == "display.jsw":
                continue
            text = path.read_text(encoding="utf-8")
            for call in ("Pen.penDown", "Pen.penUp", "Pen.eraseAll", "Pen.setPenSize", "Pen.setPenColor"):
                self.assertNotIn(call, text, f"{path.name} uses {call}")

    def test_only_the_controller_touches_the_socket(self) -> None:
        """One owner for the socket, one for the pen: that is the split."""
        for path in MODULES:
            if path.name == "net.jsw":
                continue
            text = path.read_text(encoding="utf-8")
            self.assertNotIn("WebSocket.", text.replace("// hook: WebSocket", ""), path.name)

    def test_the_pen_size_is_exactly_one_and_never_changes(self) -> None:
        """One dot must be one pixel: size 1, set once, never touched again."""
        text = (RVDIR / "display.jsw").read_text(encoding="utf-8")
        self.assertEqual(text.count("Pen.setPenSize("), 1, "the size is set in one place only")
        self.assertIn("Pen.setPenSize(1);", text)
        for other in ("Pen.changePenSize", "Pen.setPenSize(_", "Pen.setPenSize(X."):
            self.assertNotIn(other, text)

    def test_the_pen_has_to_move_to_draw(self) -> None:
        """`pen down` with no movement stamps nothing in the compiled runtime."""
        text = (RVDIR / "display.jsw").read_text(encoding="utf-8")
        body = text.split("private void pixel(", 1)[1].split("\n    }", 1)[0]
        self.assertIn("Pen.penDown();", body)
        self.assertIn("Motion.setXY(_px + 0.01, _py)", body, "a dot needs a movement to stamp")

    def test_pixels_are_addressed_on_integer_stage_coordinates(self) -> None:
        """Measured: a dot at stage x lights column x+320, at stage y row 239-y.

        So pixel centres are on *integers*. A half-integer coordinate straddles
        two pixels and smears the glyph over them -- the bug that made the text
        look fat.
        """
        text = (RVDIR / "display.jsw").read_text(encoding="utf-8")
        body = text.split("private void pixel(", 1)[1].split("\n    }", 1)[0]
        self.assertIn("_px = ix - X.stageW / 2", body)
        self.assertIn("_py = X.stageH / 2 - 1 - iy", body)
        self.assertNotIn("+ 0.5", body.replace("_px + 0.01", ""), "no half-pixel offsets")

    def test_the_painter_never_offsets_a_whole_pixel_by_a_half(self) -> None:
        """Every pen coordinate in the painter comes from `pixel(...)`."""
        text = (RVDIR / "display.jsw").read_text(encoding="utf-8")
        painter = text.split("private void paintCell(", 1)[1]
        for line in painter.splitlines():
            if "Motion.setXY(" in line:
                self.fail(f"paintCell places dots itself: {line.strip()}")

    def test_the_key_table_has_one_entry_per_printable_character(self) -> None:
        """83 names: an unshifted name and a shifted name never collide.

        `isKeyPressed` uppercases a one-character argument, so "h" already
        answers for a stored "H": a second entry for "H" would push the same
        scan code twice and every shifted letter would print twice.
        """
        import re

        text = (RVDIR / "input.jsw").read_text(encoding="utf-8")
        body = text.split("public K.names = [", 1)[1].split("];", 1)[0]
        names = re.findall(r'"((?:[^"\\]|\\.)*)"', body)
        self.assertEqual(len(names), 83)
        self.assertEqual(len(set(names)), 83, "a name is polled twice")
        self.assertNotIn("A", names, "uppercase letters are redundant")
        for shifted in ("!", "$", "?", "{", '<', ":", "_"):
            self.assertIn(shifted, names, shifted)

    def test_a_shifted_character_presses_and_releases_shift(self) -> None:
        """The shift the controller makes has to be given back, or it leaks.

        A `!` is shift+1, but a browser reports `!` as one character, so the
        controller wraps it. If it only pressed shift, every key after the
        first symbol came out shifted -- `2` printed `@`.
        """
        text = (RVDIR / "input.jsw").read_text(encoding="utf-8")
        body = text.split("private void scanKeys(", 1)[1].split("\n    }", 1)[0]
        self.assertIn("push(18);", body, "a shifted key has to press shift")
        self.assertIn("push(K.release);\n                    push(18);", body,
                      "and release it again on the key's own break")

    def test_the_painter_invalidates_from_the_plane_not_the_shadow(self) -> None:
        """An erase blanks the shadow, so a dirty cell must be judged from the plane.

        Deciding "is this cell dirty?" with `X.dispTextNow` dropped every cell
        that had never been drawn: the POST screen came out blank and only the
        cells written afterwards appeared.
        """
        text = (RVDIR / "display.jsw").read_text(encoding="utf-8")
        body = text.split("private void dispInvalidate(", 1)[1].split("\n    }", 1)[0]
        self.assertIn("X.dispText.get(", body, "the decision has to come from the plane")
        self.assertNotIn("X.dispTextNow.get(", body, "the shadow is blank after an erase")

    def test_erasing_the_glass_blanks_the_shadow(self) -> None:
        """`Pen.eraseAll` makes every cell "not drawn", or nothing repaints."""
        text = (RVDIR / "display.jsw").read_text(encoding="utf-8")
        body = text.split("private void clearScreen(", 1)[1].split("\n    }", 1)[0]
        self.assertIn("X.dispTextNow.set(_i, -1)", body)

    def test_the_build_is_clean(self) -> None:
        r = subprocess.run(
            [sys.executable, "-m", "jswc", "build", str(JSW), "-o", str(ROOT / "build" / "rv64" / "rv64.sb3")],
            cwd=ROOT, capture_output=True, text=True,
        )
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertNotIn("errors", r.stdout, r.stdout)



class Rv64KeymapTests(unittest.TestCase):
    """The BIOS key tables, read straight out of the assembly."""

    @classmethod
    def setUpClass(cls) -> None:
        import re

        text = (RVDIR / "bios.S").read_text(encoding="utf-8")

        def table(name: str) -> list[int]:
            body = text.split(f"\n{name}:\n", 1)[1].split("\n\n", 1)[0]
            values = [int(v) for v in re.findall(r"\d+", body)]
            assert len(values) == 128, (name, len(values))
            return values

        cls.normal = table("keymap")
        cls.shifted = table("keymap_shift")

    def test_a_modifier_maps_to_nothing(self) -> None:
        for code in (0x12, 0x59, 0x14, 0x11):   # shifts, control, alt
            self.assertEqual(self.normal[code], 0, hex(code))
            self.assertEqual(self.shifted[code], 0, hex(code))

    def test_letters_and_digits(self) -> None:
        self.assertEqual(self.normal[0x1C], ord("a"))
        self.assertEqual(self.shifted[0x1C], ord("A"))
        self.assertEqual(self.normal[0x33], ord("h"))
        self.assertEqual(self.shifted[0x33], ord("H"))
        self.assertEqual(self.normal[0x25], ord("4"))
        self.assertEqual(self.shifted[0x25], ord("$"))

    def test_the_keys_a_console_needs(self) -> None:
        self.assertEqual(self.normal[0x29], 32)      # space
        self.assertEqual(self.normal[0x5A], 10)      # enter
        self.assertEqual(self.normal[0x66], 8)       # backspace
        self.assertEqual(self.shifted[0x5A], 10, "shift must not change enter")
        self.assertEqual(self.shifted[0x66], 8, "shift must not change backspace")

    def test_only_one_scan_code_is_a_space(self) -> None:
        """A stray space in the echo means the table is lying."""
        spaces = [i for i, v in enumerate(self.normal) if v == 32]
        self.assertEqual(spaces, [0x29])


class Rv64TimerTests(unittest.TestCase):
    """Machine-mode traps, the CLINT and the clock.

    The payload arms `mtimecmp`, enables MTIE and waits in `wfi`; the handler
    counts the interrupts and rearms. What it prints is deliberately free of
    anything that depends on host speed, so QEMU, the reference and the Scratch
    build have to agree word for word.
    """

    @classmethod
    def setUpClass(cls) -> None:
        if not TIMER_ELF.exists():
            raise unittest.SkipTest(f"{TIMER_ELF} is not built (see docs/RV64.md)")
        if not TIMER_OUT.exists():
            raise unittest.SkipTest(f"{TIMER_OUT} is missing")

    def test_the_reference_matches_qemu(self) -> None:
        import rv64_ref

        mach = rv64_ref.Machine()
        rv64_ref.load_elf(mach, TIMER_ELF.read_bytes())
        got = mach.run_frames(40)
        self.assertIsNone(mach.error, mach.error)
        self.assertEqual(got, TIMER_OUT.read_text(encoding="latin-1"),
                         "the reference and QEMU disagree about the timer")

    def test_three_interrupts_were_taken_and_came_from_the_wait_loop(self) -> None:
        import rv64_ref

        mach = rv64_ref.Machine()
        rv64_ref.load_elf(mach, TIMER_ELF.read_bytes())
        mach.run_frames(40)
        self.assertEqual(len(mach.traps), 3)
        for cause, epc, _pc in mach.traps:
            self.assertEqual(cause, rv64_ref.CAUSE_MTIMER)
            self.assertGreaterEqual(epc, 0x80000000)

    def test_mtime_only_moves_when_the_clock_ticks(self) -> None:
        """The machine has no other source of time: `steps` does not move it."""
        import rv64_ref

        mach = rv64_ref.Machine()
        rv64_ref.load_elf(mach, TIMER_ELF.read_bytes())
        before = mach.mtime
        for _ in range(100):
            mach.step()
        self.assertEqual(mach.mtime, before, "an instruction moved the clock")
        mach.tick(1000)
        self.assertEqual(mach.mtime, before + 1000)

    def test_mtip_follows_mtime_and_clears_when_rearmed(self) -> None:
        import rv64_ref

        mach = rv64_ref.Machine()
        mach.mtimecmp = 500
        mach.tick(499)
        self.assertEqual(mach.mip & rv64_ref.MIP_MTIP, 0)
        mach.tick(1)
        self.assertEqual(mach.mip & rv64_ref.MIP_MTIP, rv64_ref.MIP_MTIP)
        mach.mtimecmp = 10_000
        mach.tick(1)
        self.assertEqual(mach.mip & rv64_ref.MIP_MTIP, 0, "a rearm has to clear MTIP")

    def test_an_interrupt_needs_all_three_of_mtvec_mie_and_mie(self) -> None:
        import rv64_ref

        mach = rv64_ref.Machine()
        mach.mtimecmp = 1
        mach.tick(10)
        mach.mstatus = rv64_ref.MSTATUS_MIE
        self.assertEqual(mach.pending_interrupt(), 0, "no handler installed")
        mach.mtvec = 0x8000_1000
        self.assertEqual(mach.pending_interrupt(), 0, "MTIE is still clear")
        mach.mie = rv64_ref.MIP_MTIP
        self.assertEqual(mach.pending_interrupt(), rv64_ref.MIP_MTIP)
        mach.mstatus = 0
        self.assertEqual(mach.pending_interrupt(), 0, "mstatus.MIE is clear")

    def test_the_scratch_core_has_the_same_wiring(self) -> None:
        core = (RVDIR / "core.jsw").read_text(encoding="utf-8")
        for needle in ("trapPending()", "takeTrap(", "mret()", "clint(", "clockTick()",
                       "X.mtvec", "X.mtimecmpLo", "X.ticksPerFrame"):
            self.assertIn(needle, core, needle)
        board = JSW.read_text(encoding="utf-8")
        for needle in ("public X.mtvec", "public X.mtimecmpLo", "public X.clockMode",
                       "public X.ticksPerFrame", "public X.clintBase"):
            self.assertIn(needle, board, needle)

    def test_the_host_clock_reply_is_microseconds(self) -> None:
        """The project parses this reply itself, so the shape is a contract."""
        import rv64_disk

        class Image:
            size = 4096
            blocks = 1

        reply = rv64_disk.handle(Image(), "T")
        self.assertTrue(reply.startswith("T "), reply)
        value = reply.split(" ", 1)[1]
        self.assertTrue(value.isdigit(), value)
        self.assertEqual(len(value), 16, "seconds * 1e6 is 16 digits today")
        # microseconds, not nanoseconds: a Scratch number is exact to 2^53 and
        # 19 digits would silently lose the low ones
        self.assertLess(len(value), 19)
        self.assertAlmostEqual(int(value) / 1e6, __import__("time").time(), delta=5)

    def test_the_clock_has_three_modes(self) -> None:
        core = (RVDIR / "core.jsw").read_text(encoding="utf-8")
        body = core.split("private void clockTick(", 1)[1].split("\n    }", 1)[0]
        self.assertIn("X.clockMode == 1", body, "mode 1: the Scratch project timer")
        self.assertIn("X.clockMode == 2", body, "mode 2: a host timestamp")
        self.assertIn("X.mtimeLo = X.mtimeLo + X.ticksPerFrame", body, "mode 0: counted")
        self.assertIn("if (_hi == X.mtimeHi && _lo > X.mtimeLo)", core,
                      "a wall clock has to be clamped, or time goes backwards")


class Rv64ConsoleTests(unittest.TestCase):
    """The BIOS console, driven by scan codes into a real PS/2 controller.

    This is the only copy of the BIOS that a test can run, so the terminal
    behaviour lives here: the prompt, wrapping at the right margin, a backspace
    that stops at the prompt, and scrolling once the region is full.
    """

    #: set-2 make codes for the keys these tests press
    CODES = {"h": 0x33, "i": 0x43, "a": 0x1C, "b": 0x32, "c": 0x21, "z": 0x1A,
             "space": 0x29, "enter": 0x5A, "backspace": 0x66,
             "4": 0x25, "2": 0x1E, "1": 0x16,
             "shift": 0x12, "shift_r": 0x59}

    def setUp(self) -> None:
        if not BIOS.exists():
            self.skipTest(f"{BIOS} is not built (see docs/RV64.md)")
        import rv64_ref

        self.mach = rv64_ref.Machine()
        rv64_ref.load_elf(self.mach, BIOS.read_bytes())
        self._go(300_000)

    def _go(self, count: int) -> None:
        target = self.mach.steps + count
        while self.mach.steps < target and not self.mach.halted:
            self.mach.step()

    def _key(self, name: str) -> None:
        code = self.CODES[name]
        self.mach.ps2_fifo.append(code)
        self._go(4000)
        self.mach.ps2_fifo += [0xF0, code]
        self._go(4000)

    def _tap(self, name: str) -> None:
        """A press and a release, with a modifier that is already down."""
        self._key(name)

    def _row(self, index: int) -> str:
        text = self.mach.text
        line = "".join(chr(text[(index * 80 + x) * 2] or 32) for x in range(80)).rstrip()
        if line.endswith("\xdb"):          # the block cursor is not content
            line = line[:-1].rstrip()
        return line

    def test_the_prompt_is_on_the_first_line_of_the_console(self) -> None:
        self.assertEqual(self._row(17), "keyboard>")

    def _attr(self, row: int, col: int) -> int:
        return self.mach.text[(row * 80 + col) * 2 + 1]

    def test_the_prompt_is_yellow_and_the_echo_is_white(self) -> None:
        """The console is the one screen a user types at, so its two inks have
        to differ: an echo written in the background ink is invisible, which is
        exactly what attribute 0 is."""
        self._key("h")
        self.assertEqual(self._attr(17, 2), 0x0E, "the prompt is yellow")
        self.assertEqual(self._attr(17, 10), 0x0F, "the echoed key is white")
        self.assertNotEqual(self._attr(17, 10), 0x00,
                            "attribute 0 is black ink on black paper: never echo in it")

    def test_a_control_character_is_echoed_in_white_too(self) -> None:
        self.mach.ps2_fifo.append(self.CODES.get("ctrl", 0x14))
        self._go(3000)
        self._key("c")
        self.assertEqual(self._row(17), "keyboard> ^C")
        self.assertEqual(self._attr(17, 10), 0x0F, "the ^ is white")

    def test_the_cursor_is_a_lit_block_that_blinks(self) -> None:
        """A block, not a bar, and the guest draws it: the cell after the prompt
        is a filled 0xDB on grey paper, and it blinks every thirty frames."""
        cell = 17 * 80 + 10
        states = []
        for _ in range(70):
            self.mach.run_frames(1, batch=20_000)
            states.append(chr(self.mach.text[cell * 2] or 32))
        self.assertIn("\xdb", states, "the block is lit at some point")
        flips = sum(1 for a, b in zip(states, states[1:]) if a != b)
        self.assertGreaterEqual(flips, 1, f"and it blinks: {states[:40]}")
        lit = self.mach.text[cell * 2 + 1]
        self.assertIn(lit, (0x07, 0x00), "light grey ink when lit, blank when not")

    def test_a_key_is_echoed_after_the_prompt(self) -> None:
        for key in ("h", "i", "space", "a"):
            self._key(key)
        self.assertEqual(self._row(17), "keyboard> hi a")

    def test_enter_runs_the_line_and_leaves_a_fresh_prompt(self) -> None:
        """The prompt is a shell now: Enter parses the line, and the next prompt
        goes under whatever the command printed."""
        for key in ("h", "i", "enter"):
            self._key(key)
        self.assertEqual(self._row(17), "keyboard> hi")
        self.assertIn("? for the commands", self._row(18), "an unknown command says so")
        self._key("b")
        self.assertTrue(self._row(19).startswith("keyboard> b"), self._row(19))

    def test_backspace_never_eats_the_prompt(self) -> None:
        self._key("enter")
        for _ in range(4):
            self._key("backspace")
        self.assertEqual(self._row(18), "keyboard>")

    def test_a_full_line_wraps_onto_the_next_row(self) -> None:
        for _ in range(75):
            self._key("a")
        self.assertEqual(self._row(17), "keyboard> " + "a" * 70, "70 characters fit beside the prompt")
        self.assertEqual(self._row(18), "keyboard> " + "a" * 5, "the rest, after a fresh prompt")

    def test_the_region_scrolls_instead_of_writing_past_the_glass(self) -> None:
        for _ in range(20):
            self._key("enter")      # an empty line: the shell just prompts again
        console = [self._row(r) for r in range(17, 29)]
        self.assertTrue(all(line == "keyboard>" for line in console),
                        "every console row should hold a prompt: " + repr(console))
        self.assertIsNone(self.mach.error, self.mach.error)

    def test_shift_is_a_modifier_and_never_a_character(self) -> None:
        """`0x12` has no character of its own: it only picks the upper table.

        This is what a PS/2 controller leaves to the guest, so a BIOS that maps
        the scan code itself would print a character for every shift press.
        """
        self._key("shift")
        self.assertEqual(self._row(17), "keyboard>", "shift printed something")

    def test_shift_uppercases_a_letter(self) -> None:
        self.mach.ps2_fifo.append(self.CODES["shift"])
        self._go(3000)
        self._key("h")
        self.mach.ps2_fifo += [0xF0, self.CODES["shift"]]
        self._go(3000)
        self._key("i")
        self.assertEqual(self._row(17), "keyboard> Hi")

    def test_shift_selects_the_upper_symbol_of_a_digit(self) -> None:
        self.mach.ps2_fifo.append(self.CODES["shift"])
        self._go(3000)
        self._key("1")
        self.mach.ps2_fifo += [0xF0, self.CODES["shift"]]
        self._go(3000)
        self._key("1")
        self.assertEqual(self._row(17), "keyboard> !1")

    def _press(self, name: str) -> None:
        """A make code on its own, with no release."""
        self.mach.ps2_fifo.append(self.CODES[name])
        self._go(3000)

    def _release(self, name: str) -> None:
        self.mach.ps2_fifo += [0xF0, self.CODES[name]]
        self._go(3000)

    def test_a_shift_the_controller_pressed_cannot_outlive_the_key(self) -> None:
        """Shift is counted: one press from the key, one from the controller.

        The controller wraps `!` in a shift of its own, so the count reaches two
        and the controller's break leaves one -- a held shift survives. When
        nothing but the controller pressed it, the count returns to zero and the
        next key is unshifted.
        """
        self._press("shift")          # the controller's shift
        self._key("1")                # -> !
        self._release("shift")        # give it back
        self._key("2")                # must be a plain 2
        self.assertEqual(self._row(17), "keyboard> !2")

    def test_shift_held_across_two_keys_stays_down(self) -> None:
        self._press("shift")
        self._key("h")
        self._key("i")
        self._release("shift")
        self._key("2")
        self.assertEqual(self._row(17), "keyboard> HI2")

    def test_the_status_bar_is_not_part_of_the_console(self) -> None:
        """Scrolling must not drag row 29 up: it put POST text in the echo."""
        for _ in range(20):
            self._key("enter")
            self._key("c")
        self.assertIn("POST complete", self._row(29), "the status bar was scrolled away")

    def test_the_console_costs_five_instructions_a_frame_while_idle(self) -> None:
        """WFI has to park the batch, or the guest burns the frame in a hot poll."""
        text = (RVDIR / "core.jsw").read_text(encoding="utf-8")
        self.assertIn("X.waiting = 1;", text, "wfi has to stop runBatch")
        self.assertIn("X.waiting == 0", text, "and runBatch has to look at it")
        self.assertIn("Control.wait(0.001)", text,
                      "`wait 0` only yields a tick: a parked guest would spin")


class Rv64StorageTests(unittest.TestCase):
    """The two storage regions: a ROM window and a battery-backed NVRAM.

    They are deliberately different devices. The ROM answers reads out of the
    firmware image and swallows writes; the NVRAM is a byte-addressable page
    whose contents survive a reset, because that is what a battery does.
    """

    BY='rom_base'

    def setUp(self) -> None:
        import rv64_ref

        self.rv = rv64_ref
        self.mach = rv64_ref.Machine()
        rv64_ref.load_elf(self.mach, BIOS.read_bytes())

    def test_the_rom_window_answers_out_of_the_image(self) -> None:
        image = BIOS.read_bytes()
        for offset in (0, 4, 0x100, len(image) - 1):
            self.assertEqual(self.mach.read(self.rv.ROM_BASE + offset, 1), image[offset])

    def test_unwritten_flash_reads_as_all_ones(self) -> None:
        self.assertEqual(self.mach.read(self.rv.ROM_BASE + 0x8_0000, 1), 0xFF)

    def test_a_store_into_the_rom_goes_nowhere(self) -> None:
        before = self.mach.read(self.rv.ROM_BASE, 1)
        self.mach.write(self.rv.ROM_BASE, 0x00, 1)
        self.assertEqual(self.mach.read(self.rv.ROM_BASE, 1), before)
        self.assertEqual(self.mach.rom_writes, 1, "a swallowed write should still be visible")

    def test_the_nvram_page_reads_and_writes(self) -> None:
        self.mach.write(self.rv.NVRAM_BASE + 7, 0x42, 1)
        self.assertEqual(self.mach.read(self.rv.NVRAM_BASE + 7, 1), 0x42)
        self.assertEqual(self.mach.nvram_writes, 1)

    def test_the_nvram_starts_erased(self) -> None:
        self.assertEqual(set(self.mach.nvram), {0xFF})

    def _boot(self, nvram: bytes | None = None):
        if nvram is not None:
            self.mach.nvram[:] = nvram
        self.mach.run_frames(30, batch=20_000)
        self.assertIsNone(self.mach.error, self.mach.error)
        return nvram

    def _row(self, index: int) -> str:
        text = self.mach.text
        line = "".join(chr(text[(index * 80 + x) * 2] or 32) for x in range(80)).rstrip()
        if line.endswith("\xdb"):          # the block cursor is not content
            line = line[:-1].rstrip()
        return line

    def test_a_blank_nvram_is_a_first_boot_with_defaults(self) -> None:
        self._boot()
        self.assertIn("no record: defaults written", self._row(9))
        page = bytes(self.mach.nvram)
        self.assertEqual(page[0:4], b"JSW2", "the record was not written")
        self.assertEqual(page[4], 2, "version")
        self.assertEqual(page[8] | (page[9] << 8), 8,
                         "a fresh record asks for what the board already gives")

    def test_a_saved_record_is_adopted_on_the_next_boot(self) -> None:
        self._boot()
        saved = bytes(self.mach.nvram)

        self.mach = self.rv.Machine()
        self.rv.load_elf(self.mach, BIOS.read_bytes())
        self._boot(saved)
        self.assertIn("settings loaded", self._row(9))

    def test_a_damaged_record_falls_back_to_defaults(self) -> None:
        self._boot()
        page = bytearray(self.mach.nvram)
        page[6] ^= 0xFF                     # the attribute, behind the checksum

        self.mach = self.rv.Machine()
        self.rv.load_elf(self.mach, BIOS.read_bytes())
        self._boot(bytes(page))
        self.assertIn("no record", self._row(9), "a bad checksum must not be trusted")
        self.assertNotEqual(bytes(self.mach.nvram)[6], page[6], "the record was not repaired")

    def test_the_checksum_covers_the_header(self) -> None:
        self._boot()
        page = bytes(self.mach.nvram)
        self.assertEqual(sum(page[0:10]) & 0xFFFF, page[10] | (page[11] << 8))

    def test_delete_during_post_opens_setup(self) -> None:
        self.mach.ps2_fifo.append(0x71)     # delete
        self.mach.run_frames(30, batch=20_000)
        tabs = self._row(0)
        self.assertIn("Main", tabs)
        self.assertIn("Info", tabs)
        self.assertIn("Exit", tabs)

    def test_a_key_release_inside_setup_does_not_eat_the_next_key(self) -> None:
        """`getMake` swallows the code after `0xF0`, and the call it makes there
        used to clobber its own return address: the first *release* left the
        screen deaf. A browser always sends releases, so every key after the
        first did nothing at all."""
        self.mach.ps2_fifo.append(0x71)     # delete: into setup
        self.mach.run_frames(10, batch=20_000)
        self.mach.ps2_fifo += [0xF0, 0x71]  # ...and let go of it
        self.mach.run_frames(4, batch=20_000)
        self.mach.ps2_fifo.append(0x5A)     # enter
        self.mach.run_frames(4, batch=20_000)
        self.assertIn("None", self._row(14), "the list of values is up")

    def test_arrow_keys_still_work_after_a_release(self) -> None:
        self.mach.ps2_fifo.append(0x71)
        self.mach.run_frames(10, batch=20_000)
        self.mach.ps2_fifo += [0xF0, 0x71]
        self.mach.run_frames(4, batch=20_000)
        self.mach.ps2_fifo += [0xE0, 0x72]  # down
        self.mach.run_frames(4, batch=20_000)
        self.mach.ps2_fifo += [0xF0, 0xE0, 0x72]
        self.mach.run_frames(4, batch=20_000)
        self.assertIn("Console ink", self._row(5)[0:44], "the bar moved down")
        self.mach.ps2_fifo += [0xE0, 0x74]  # right: straight to the Info tab
        self.mach.run_frames(4, batch=20_000)
        self.assertIn("System information", self._row(2))

    def test_enter_opens_a_list_of_values_and_entering_one_keeps_it(self) -> None:
        self.mach.ps2_fifo.append(0x71)     # delete during POST
        self.mach.run_frames(10, batch=20_000)
        self.mach.ps2_fifo.append(0x5A)     # enter on "Boot device"
        self.mach.run_frames(4, batch=20_000)
        self.assertIn("None", self._row(14), "the list of values is up")
        self.assertIn("Disk", self._row(15))
        self.assertIn("Enter accept", self._row(17), "and it says how to use it")
        self.mach.ps2_fifo += [0xE0, 0x72]  # down: Disk wraps round to None
        self.mach.run_frames(3, batch=20_000)
        self.mach.ps2_fifo.append(0x5A)     # enter: take it
        self.mach.run_frames(4, batch=20_000)
        self.assertNotIn("None", self._row(14), "the list closed")
        self.assertIn("None", self._row(4), "and the menu shows the new value")
        self.mach.ps2_fifo.append(0x1B)     # 's' saves it
        self.mach.run_frames(40, batch=20_000)
        self.assertEqual(bytes(self.mach.nvram)[5], 0, "boot device should be None now")

    def test_escape_asks_before_it_throws_anything_away(self) -> None:
        self.mach.ps2_fifo.append(0x71)
        self.mach.run_frames(10, batch=20_000)
        before = bytes(self.mach.nvram)
        self.mach.ps2_fifo.append(0x76)     # escape
        self.mach.run_frames(4, batch=20_000)
        self.assertIn("Unsaved changes", self._row(12), "the question is up")
        self.assertIn("Keep editing", self._row(17))
        self.mach.ps2_fifo += [0xE0, 0x72]  # down: the safe choice is lit, so go
        self.mach.run_frames(3, batch=20_000)
        self.mach.ps2_fifo += [0xE0, 0x72]  # ...down to "Throw them away"
        self.mach.run_frames(3, batch=20_000)
        self.mach.ps2_fifo += [0xE0, 0x72]  # wrap to "Save them and exit"
        self.mach.run_frames(3, batch=20_000)
        self.mach.ps2_fifo.append(0x5A)     # enter: keep editing
        self.mach.run_frames(4, batch=20_000)
        self.assertIn("Main settings", self._row(2), "and we are back in the menu")
        self.assertEqual(bytes(self.mach.nvram), before, "nothing was written")

    def test_escape_then_save_writes_the_record(self) -> None:
        self.mach.ps2_fifo.append(0x71)
        self.mach.run_frames(10, batch=20_000)
        self.mach.ps2_fifo.append(0x76)     # escape
        self.mach.run_frames(4, batch=20_000)
        self.mach.ps2_fifo.append(0x1B)     # 's' -- the shortcut inside the question
        self.mach.run_frames(40, batch=20_000)
        self.assertEqual(bytes(self.mach.nvram)[0:4], b"JSW2")

    def test_the_arrow_keys_walk_the_tabs_and_enter_does_not(self) -> None:
        self.mach.ps2_fifo.append(0x71)
        self.mach.run_frames(10, batch=20_000)
        self.assertIn("Main settings", self._row(2))
        self.mach.ps2_fifo += [0xE0, 0x74]  # right: the Info tab
        self.mach.run_frames(3, batch=20_000)
        self.assertIn("System information", self._row(2))
        self.assertIn("CPU", self._row(4))
        self.mach.ps2_fifo += [0xE0, 0x74]  # right: Exit
        self.mach.run_frames(3, batch=20_000)
        self.assertIn("Save or discard", self._row(2))
        self.mach.ps2_fifo += [0xE0, 0x74]  # right again: back to Main
        self.mach.run_frames(3, batch=20_000)
        self.assertIn("Main settings", self._row(2))
        self.mach.ps2_fifo += [0xE0, 0x6B]  # left: Exit, the way round
        self.mach.run_frames(3, batch=20_000)
        self.assertIn("Save or discard", self._row(2))

    def test_the_detail_panel_is_narrow_and_the_menu_is_wide(self) -> None:
        """The rule sits at column 45: the menu needs the room, the paragraph
        does not, and a 42-cell paragraph reads badly."""
        self.mach.ps2_fifo.append(0x71)
        self.mach.run_frames(10, batch=20_000)
        text = self.mach.text
        cell = 4 * 80 + 45
        self.assertEqual(chr(text[cell * 2]), "\xba", "the column rule")
        self.assertEqual(text[cell * 2 + 1], 0x1F, "white on blue")
        self.assertIn("Boot device", self._row(4)[0:44])
        self.assertIn("Which device", self._row(4)[47:])
        self.assertEqual(self._row(4)[44], " ", "a gap on both sides of the rule")

    def test_a_dialog_has_a_shadow(self) -> None:
        self.mach.ps2_fifo.append(0x71)
        self.mach.run_frames(10, batch=20_000)
        self.mach.ps2_fifo.append(0x5A)     # enter: the list of values
        self.mach.run_frames(4, batch=20_000)
        text = self.mach.text
        # the box is rows 11..17, columns 19..60; the shadow is one row down
        # and two columns right of it, dithered black
        cell = 18 * 80 + 62
        self.assertEqual(text[cell * 2 + 1], 0x00, "the shadow is solid black")
        self.assertEqual(text[(12 * 80 + 30) * 2 + 1], 0x1F, "a blue title bar")
        self.assertEqual(chr(text[(11 * 80 + 19) * 2]), "\xc9", "a top-left corner")
        self.assertEqual(text[(13 * 80 + 30) * 2 + 1], 0x70, "grey body under it")

    def test_the_scratch_machine_maps_the_same_two_regions(self) -> None:
        core = (RVDIR / "core.jsw").read_text(encoding="utf-8")
        self.assertIn("private void rom8(", core)
        self.assertIn("private void nvram8(", core)
        self.assertIn("X.romWrites += 1", core, "a ROM store has to be swallowed")
        board = JSW.read_text(encoding="utf-8")
        self.assertIn("public X.romBase = 4096", board)
        self.assertIn("public X.nvramBase = 268443648", board)
        self.assertIn("public X.nvram = []", board)

    def test_the_scratch_nvram_is_only_created_once(self) -> None:
        """A battery does not care about reset: green flag must not wipe it."""
        core = (RVDIR / "core.jsw").read_text(encoding="utf-8")
        body = core.split("public void storageBoot(", 1)[1].split("\n    }", 1)[0]
        self.assertIn("if (X.nvram.length() > 0)", body, "an existing page must be kept")
        self.assertNotIn("X.nvram.clear()", body)


    def test_leaving_setup_clears_the_console_rows(self) -> None:
        """A dialog can reach row 17, and the console row is longer than the
        prompt that replaces it, so coming back has to blank the rows it owns."""
        self.mach.ps2_fifo.append(0x71)
        self.mach.run_frames(10, batch=20_000)
        self.mach.ps2_fifo += [0xF0, 0x71]
        self.mach.run_frames(4, batch=20_000)
        self.mach.ps2_fifo += [0xE0, 0x74]  # right, right: the Exit tab
        self.mach.run_frames(4, batch=20_000)
        self.mach.ps2_fifo += [0xE0, 0x74]
        self.mach.run_frames(4, batch=20_000)
        self.mach.ps2_fifo += [0xE0, 0x72]  # down: "Exit without saving"
        self.mach.run_frames(4, batch=20_000)
        self.mach.ps2_fifo.append(0x5A)     # enter: the question
        self.mach.run_frames(6, batch=20_000)
        self.mach.ps2_fifo.append(0x64)     # 'd': throw them away
        self.mach.run_frames(80, batch=20_000)
        self.assertEqual(self._row(17), "keyboard>", "the console row is clean")
        self.assertEqual(self._row(18), "", "and the row under the prompt")
        self.assertNotIn("Throw them away", "".join(self._row(r) for r in range(11, 19)),
                         "no dialog text survived")

    def test_escape_at_the_prompt_opens_setup(self) -> None:
        """Delete only works inside the POST window; Escape always does."""
        # past the POST window, which is a second of *frames*
        self.mach.run_frames(80, batch=20_000)
        self.assertIn("keyboard>", self._row(17))
        self.mach.ps2_fifo.append(0x76)          # escape
        self.mach.run_frames(6, batch=20_000)
        self.assertIn("Main", self._row(0), "the tab bar should be up")
        self.assertIn("\x18/\x19 item", self._row(29), "and the key legend")


class Rv64MemExtensionTests(unittest.TestCase):
    """The board's own JavaScript extension: a typed-array memory.

    An .sb3 cannot carry JavaScript — the format is project.json plus assets and
    an extension is a URL in `extensionURLs` — so the file is served from the
    same local server the project and the disk come from, and the compiler
    declares it. A gigabyte there is one `Uint8Array`, which is the whole point.
    """

    ROOT = Path(__file__).resolve().parent.parent
    EXT = ROOT / "tools" / "rv64mem.js"
    DEMO = ROOT / "userspace" / "mem" / "mem.jsw"

    def test_the_compiler_declares_it_as_an_extension_url(self) -> None:
        from jswc import extensions, opcodes

        self.assertIn("rv64mem", opcodes.EXTENSION_URLS)
        self.assertEqual(opcodes.EXTENSION_URLS["rv64mem"],
                         "http://localhost:8000/tools/rv64mem.js")
        self.assertIsNotNone(extensions.resolve("Mem", "read8"),
                             "Mem.read8 has to resolve to rv64mem_read8")
        self.assertIsNone(extensions.resolve("Mem", "notABlock"))

    def test_every_extension_url_loads_without_a_permission_prompt(self) -> None:
        """TurboWarp only auto-loads extensions under a trusted prefix.

        Anything else stops the project at "Downloading assets (2/2) …" behind a
        modal that is not always drawn, and the editor then crashes with
        "Permission to load extension denied": the port is load-bearing.
        """
        from jswc import opcodes

        for name, url in opcodes.EXTENSION_URLS.items():
            with self.subTest(extension=name):
                self.assertTrue(url.startswith(opcodes.TRUSTED_EXTENSION_PREFIXES),
                                f"{url} would ask the user for permission")

    def test_the_demo_compiles_to_the_extension_blocks(self) -> None:
        from tests.helpers import compile_source

        source = self.DEMO.read_text(encoding="utf-8")
        _, project = compile_source(source)
        self.assertEqual(project.extension_urls["rv64mem"],
                         "http://localhost:8000/tools/rv64mem.js")
        used = {b.opcode for t in project.targets for b in t.blocks.values()}
        for opcode in ("rv64mem_alloc", "rv64mem_fill", "rv64mem_read8",
                       "rv64mem_read32", "rv64mem_write32"):
            self.assertIn(opcode, used)

    def test_the_extension_moves_a_gigabyte(self) -> None:
        """Run the extension itself under node with a Scratch shim: 1 GiB, a
        fill, a word, and a read past the end that must answer 0."""
        import shutil
        import subprocess
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
const out = { id: mem.getInfo().id, blocks: mem.getInfo().blocks.length };
out.allocated = mem.alloc({ BYTES: 1073741824 });
mem.fill({ ADDR: 0, LEN: 16, VALUE: 90 });
mem.write32({ ADDR: 64, VALUE: 305419896 });
out.byte0 = mem.read8({ ADDR: 0 });
out.byte15 = mem.read8({ ADDR: 15 });
out.byte16 = mem.read8({ ADDR: 16 });
out.word = mem.read32({ ADDR: 64 });
out.pastEnd = mem.read8({ ADDR: 1073741824 });
// Writing every byte proves the whole gigabyte is really there: a buffer that
// was not backed by memory would throw long before the last page.
const t0 = Date.now();
mem.fill({ ADDR: 0, LEN: 1073741824, VALUE: 90 });
out.fillMs = Date.now() - t0;
out.lastByte = mem.read8({ ADDR: 1073741823 });
mem.write32({ ADDR: 1073741816, VALUE: 305419896 });
out.tailWord = mem.read32({ ADDR: 1073741816 });
out.tailLast = mem.read8({ ADDR: 1073741823 });
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
        self.assertEqual(data["id"], "rv64mem")
        self.assertEqual(data["allocated"], 1 << 30, "a gigabyte")
        self.assertEqual((data["byte0"], data["byte15"], data["byte16"]), (90, 90, 0),
                         "fill covers exactly what it was asked to")
        self.assertEqual(data["word"], 305419896, "little-endian word")
        self.assertEqual(data["pastEnd"], 0, "a read past the end answers zero")
        self.assertEqual(data["lastByte"], 90,
                         "the fill reached the very last byte of the gigabyte")
        self.assertEqual(data["tailWord"], 305419896, "a word at the far end")
        self.assertEqual(data["tailLast"], 90,
                         "a word four bytes from the end leaves the last byte alone")

    def test_an_allocation_that_does_not_fit_is_halved_not_fatal(self) -> None:
        """A browser can refuse a gigabyte. Throwing there would kill the whole
        project, so the ask is halved until something fits and the caller reads
        the real length back -- which is what the POST prints."""
        import shutil
        import subprocess
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
// A browser with a small heap: anything over 16 MiB throws, as a real
// allocation can. Everything below it must still work.
const Real = Uint8Array;
const LIMIT = 16 * 1024 * 1024;
global.Uint8Array = new Proxy(Real, {
  construct(target, args) {
    if (args[0] > LIMIT) throw new RangeError('Array buffer allocation failed');
    return new target(...args);
  }
});
eval(fs.readFileSync(process.argv[2], 'utf8'));
const mem = registered;
const out = {};
out.asked = 64 * 1024 * 1024;
out.got = mem.alloc({ BYTES: out.asked });
out.size = mem.size();
mem.write8({ ADDR: out.got - 1, VALUE: 7 });
out.lastByte = mem.read8({ ADDR: out.got - 1 });
out.zero = mem.alloc({ BYTES: 0 });
console.log(JSON.stringify(out));
"""
        with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as fh:
            fh.write(script)
            path = fh.name
        try:
            done = subprocess.run([node, path, str(self.EXT)], capture_output=True,
                                  text=True, timeout=60, check=True)
        finally:
            Path(path).unlink(missing_ok=True)
        data = json.loads(done.stdout)
        self.assertEqual(data["got"], 16 * 1024 * 1024, "halved until it fitted")
        self.assertEqual(data["size"], data["got"], "size agrees with what alloc said")
        self.assertEqual(data["lastByte"], 7, "and the memory it did get works")
        self.assertEqual(data["zero"], 0)


class Rv64MemoryPathTests(unittest.TestCase):
    """Which memory the core actually reads, and what happens without it.

    The Scratch build keeps RAM in the extension; the list stays as the fallback
    for a build that could not load it, because a machine that silently reads
    nothing is worse than a slow one.
    """

    def setUp(self) -> None:
        from jswc import include
        from jswc.codegen import Codegen
        from jswc.lexer import tokenize
        from jswc.parser import Parser

        # the board is four files joined by `include`, so it has to be expanded
        # the way the compiler expands it, not parsed as one file
        loaded = include.expand(JSW)
        self.source = loaded.text
        program = Parser(tokenize(self.source)).parse()
        self.project = Codegen(program, seed="rv64", base_dir=JSW.parent,
                               allow_undefined=True,
                               features=set(program.features)).compile()
        self.opcodes = {b.opcode
                        for t in self.project.targets for b in t.blocks.values()}

    def test_the_core_reads_and_writes_the_extension(self) -> None:
        self.assertIn("rv64mem_read8", self.opcodes)
        self.assertIn("rv64mem_write8", self.opcodes)
        self.assertEqual(self.project.extension_urls["rv64mem"],
                         "http://localhost:8000/tools/rv64mem.js")

    def test_the_list_is_still_the_fallback(self) -> None:
        """`memExt == 0` has to keep working: both arms have to exist."""
        self.assertIn("X.memExt", self.source)
        self.assertGreaterEqual(self.source.count("X.ram.get("), 1,
                                "the list read survived the extension")
        self.assertGreaterEqual(self.source.count("X.ram.set("), 1)

    def test_the_card_is_found_by_allocating_not_by_asking_its_size(self) -> None:
        """`size` is zero until something has been allocated, so a boot that
        tested `size` first believed there was no card and then tried to grow a
        list to a gigabyte: `RangeError: Invalid array length` on the first
        green flag. Asking for the memory is the only honest test."""
        self.assertNotIn("Mem.size()", self.source)
        self.assertIn("_n = Mem.alloc(", self.source)
        self.assertIn("if (_n > 0)", self.source)
        self.assertIn("if (X.memMiB > 8)", self.source, "the list fallback shrinks")

    def test_the_chipset_register_is_not_clamped_byte_by_byte(self) -> None:
        """A 32-bit store reaches the board as four byte writes, so the value
        between them is half written on purpose. Clamping there threw away the
        byte the guest wanted: 1024 -> 16 became 1024 -> 1, and the machine
        rebooted into a mebibyte. The clamp belongs where the size is spent."""
        board = self.source[self.source.index("private void board8"):
                             self.source.index("private void nvram8")]
        self.assertNotIn("X.memMaxMiB", board)
        power_on = self.source[self.source.index("public void powerOn()"):
                               self.source.index("private void load8(")]
        self.assertIn("X.memMaxMiB", power_on)
        self.assertIn("Mem.alloc(", power_on)

    def test_the_card_is_booted_on_request_not_on_its_own_green_flag(self) -> None:
        """A green flag clears the pen layer, and the two green-flag scripts run
        in an order nobody chooses. A card that cleared the screen whenever its
        own script started could clear it *after* the guest had drawn POST: the
        planes said every cell was painted and the glass was black. The CPU asks
        (`dispReq`), the card answers (`dispAck`), and the order stops mattering."""
        display = (JSW.parent / "display.jsw").read_text(encoding="utf-8")
        start = display[display.index("public void displayStart()"):
                        display.index("private void dispBoot()")]
        self.assertIn("X.dispReq == 1", start)
        self.assertIn("X.dispAck = 1", start)
        self.assertNotIn("dispBoot();\n        while (1)", start)
        core = self.source[self.source.index("public void coreStart()"):
                           self.source.index("public void powerOn()")]
        self.assertIn("X.dispReq = 1", core)
        self.assertIn("while (X.dispAck < 1)", core)

    def test_the_board_state_declares_the_chipset(self) -> None:
        self.assertIn("public X.boardBase = 268451840", self.source)
        self.assertIn("public X.resetWant = 0", self.source)
        self.assertIn("public X.memExt", self.source)


class Rv64NetTests(unittest.TestCase):
    """The host end of the NIC: a relay, not a bridge.

    It answers ARP and DHCP itself and mirrors ICMP, which is what lets the
    machine have a network without root and without touching `en0`.
    """

    def setUp(self) -> None:
        import rv64_net

        self.net = rv64_net
        self.bridge = rv64_net.Bridge()

    def _arp_request(self, target: str) -> bytes:
        return (b"\xff" * 6 + self.net.GUEST_MAC + b"\x08\x06"
                + b"\x00\x01\x08\x00\x06\x04\x00\x01"
                + self.net.GUEST_MAC + self.net.ip_bytes(self.net.GUEST_IP)
                + b"\x00" * 6 + self.net.ip_bytes(target))

    def _ping(self, target: str, payload: bytes = b"hello bios") -> bytes:
        icmp = bytearray(b"\x08\x00\x00\x00\x12\x34\x00\x01") + payload
        icmp[2:4] = struct.pack(">H", self.net.checksum(bytes(icmp)))
        ip = self.net._ipv4(self.net.ip_bytes(self.net.GUEST_IP),
                            self.net.ip_bytes(target), self.net.IP_ICMP, bytes(icmp))
        return self.net._ethernet(self.net.HOST_MAC, self.net.GUEST_MAC,
                                  self.net.ETH_IPV4, ip)

    def test_arp_for_the_gateway_is_answered(self) -> None:
        replies = self.bridge.from_guest(self._arp_request(self.net.HOST_IP))
        self.assertEqual(len(replies), 1)
        frame = replies[0]
        self.assertEqual(struct.unpack(">H", frame[20:22])[0], 2, "an ARP reply")
        self.assertEqual(frame[22:28], self.net.HOST_MAC, "our MAC")
        self.assertEqual(self.net.ip_text(frame[28:32]), self.net.HOST_IP)

    def test_arp_for_a_stranger_is_ignored(self) -> None:
        self.assertEqual(self.bridge.from_guest(self._arp_request("192.168.8.1")), [])

    def test_ping_is_answered_with_the_same_payload(self) -> None:
        replies = self.bridge.from_guest(self._ping(self.net.HOST_IP))
        self.assertEqual(len(replies), 1)
        ip = replies[0][14:]
        ihl = (ip[0] & 0xF) * 4
        icmp = ip[ihl:]
        self.assertEqual(icmp[0], 0, "an echo reply")
        self.assertEqual(icmp[8:], b"hello bios", "and the payload comes back")
        self.assertEqual(self.net.ip_text(ip[12:16]), self.net.HOST_IP, "from the gateway")
        self.assertEqual(self.net.checksum(ip), 0, "the IP header sums to zero")

    def test_dhcp_discover_gets_an_offer(self) -> None:
        bootp = bytearray(236)
        bootp[0] = 1
        bootp[4:8] = b"\x01\x02\x03\x04"
        bootp[28:34] = self.net.GUEST_MAC
        bootp += bytes([0x63, 0x82, 0x53, 0x63, 53, 1, 1, 255])
        udp = struct.pack(">HHHH", 68, 67, 8 + len(bootp), 0) + bytes(bootp)
        ip = self.net._ipv4(b"\x00" * 4, b"\xff" * 4, self.net.IP_UDP, udp)
        frame = self.net._ethernet(b"\xff" * 6, self.net.GUEST_MAC,
                                   self.net.ETH_IPV4, ip)
        replies = self.bridge.from_guest(frame)
        self.assertEqual(len(replies), 1, "an offer")
        offer = replies[0][14 + 20 + 8:]
        self.assertEqual(offer[0], 2, "a BOOTREPLY")
        self.assertEqual(self.net.ip_text(offer[16:20]), self.net.GUEST_IP)
        self.assertEqual(self.net.ip_text(offer[20:24]), self.net.HOST_IP)
        self.assertIn(bytes([53, 1, 2]), bytes(offer), "message type OFFER")

    def test_the_disk_server_forwards_frames_too(self) -> None:
        import rv64_disk

        class NoImage:
            size = 0
            blocks = 0

        reply = rv64_disk.handle(NoImage(), "N " + self._arp_request(self.net.HOST_IP).hex(),
                                 self.bridge)
        self.assertTrue(reply.startswith("N "), reply[:20])
        self.assertEqual(self.bridge.arp_count, 1)


class Rv64AtomicTests(unittest.TestCase):
    """`lr`/`sc` are what a kernel's spinlocks are built from, and RV64A is in
    the machine's name, so the reservation has to be here."""

    def setUp(self) -> None:
        import rv64_ref

        self.rv = rv64_ref
        self.mach = rv64_ref.Machine()

    def _run(self, words: list[int], regs: dict[int, int] | None = None) -> None:
        base = self.rv.RAM_BASE
        for i, word in enumerate(words):
            self.mach.write(base + i * 4, word, 4)
        for reg, value in (regs or {}).items():
            self.mach.set_reg(reg, value)
        self.mach.pc = base
        for _ in range(len(words)):
            self.mach.step()

    @staticmethod
    def _lr(funct5: int, rd: int, rs1: int, rs2: int = 0, funct3: int = 2) -> int:
        return ((funct5 << 27) | (rs2 << 20) | (rs1 << 15) | (funct3 << 12) | (rd << 7) | 0x2F)

    def test_lr_sets_a_reservation_and_sc_takes_it(self) -> None:
        addr = self.rv.RAM_BASE + 0x1000
        self.mach.write(addr, 41, 8)
        # lr.w t0, (a0) ; addi t0, t0, 1 ; sc.w t1, t0, (a0)
        self._run([self._lr(0b00010, 5, 10), 0x00128293, self._lr(0b00011, 6, 10, rs2=5)],
                  {10: addr})
        self.assertEqual(self.mach.read(addr, 8), 42, "sc wrote the new value")
        self.assertEqual(self.mach.get_reg(6), 0, "sc reports success with zero")

    def test_sc_without_a_reservation_fails_and_stores_nothing(self) -> None:
        addr = self.rv.RAM_BASE + 0x1000
        self.mach.write(addr, 7, 8)
        self._run([self._lr(0b00011, 6, 10, rs2=5)], {10: addr, 5: 99})
        self.assertEqual(self.mach.get_reg(6), 1, "no reservation, no store")
        self.assertEqual(self.mach.read(addr, 8), 7, "the word is untouched")

    def test_amoadd_still_works(self) -> None:
        addr = self.rv.RAM_BASE + 0x1000
        self.mach.write(addr, 10, 8)
        self._run([self._lr(0b00000, 5, 10, rs2=6)], {10: addr, 6: 5})
        self.assertEqual(self.mach.read(addr, 8), 15)
        self.assertEqual(self.mach.get_reg(5), 10, "and returns the old value")


class Rv64OpenSbiTests(unittest.TestCase):
    """The firmware on the machine, not only in the model.

    `tools/gen_opensbi_image.py` wraps QEMU's `fw_dynamic` for a project that
    starts with every register at zero: the reset vector is patched to a stub
    that sets `a1` and `a2`, replays the instruction it displaced and jumps
    back. The wrapper is right when the firmware cannot tell the difference --
    the same instruction stream, six instructions later."""

    def _parts(self):
        import gen_opensbi_image
        import rv64_opensbi
        import rv64_ref

        try:
            blob = rv64_opensbi.find_blob().read_bytes()
        except SystemExit as exc:                       # qemu is not installed
            self.skipTest(str(exc))
        if not gen_opensbi_image.DTB_PATH.exists():
            self.skipTest("no device tree; run tools/gen_opensbi_image.py once")
        return rv64_ref, rv64_opensbi, gen_opensbi_image, blob, gen_opensbi_image.DTB_PATH.read_bytes()

    @staticmethod
    def _fresh(rv):
        return rv.Machine(ram_size=96 << 20, mem_mib=96, max_mem_mib=96)

    def _harness(self, rv, fw, gen, blob, dtb, steps):
        """The firmware booted the way `tools/rv64_opensbi.py` does it."""
        mach = self._fresh(rv)
        for i, byte in enumerate(blob):
            mach.write(rv.RAM_BASE + i, byte, 1)
        for i, byte in enumerate(dtb):
            mach.write(rv.RAM_BASE + gen.DTB_AT + i, byte, 1)
        for offset, value in ((0, fw.INFO_MAGIC), (8, fw.INFO_VERSION),
                              (16, rv.RAM_BASE + gen.FW_AT), (24, fw.NEXT_MODE_S), (32, 0), (40, 0)):
            mach.write(rv.RAM_BASE + gen.INFO_AT + offset, value, 8)
        mach.pc = rv.RAM_BASE
        mach.set_reg(10, 0)
        mach.set_reg(11, rv.RAM_BASE + gen.DTB_AT)
        mach.set_reg(12, rv.RAM_BASE + gen.INFO_AT)
        mach.run(limit=steps)
        return mach

    def _image(self, rv, gen, blob, dtb, steps):
        """The same firmware, wrapped for a project that starts at the RAM base."""
        image = gen.build(blob, dtb)
        mach = self._fresh(rv)
        for i, byte in enumerate(image):
            mach.write(rv.RAM_BASE + i, byte, 1)
        mach.pc = rv.RAM_BASE
        mach.run(limit=steps)
        return mach

    def test_the_wrapper_costs_exactly_six_instructions(self) -> None:
        rv, fw, gen, blob, dtb = self._parts()
        for steps in (50_000, 200_000):
            direct = self._harness(rv, fw, gen, blob, dtb, steps)
            wrapped = self._image(rv, gen, blob, dtb, steps + 6)
            self.assertEqual(direct.pc, wrapped.pc,
                             f"after {steps:,} instructions the two boots diverge")
            self.assertIsNone(wrapped.error)

    def test_the_firmware_gets_past_its_own_hand_off_checks(self) -> None:
        """A wrong magic, version or `next_mode` parks the hart in `_start_hang`
        at 0x3b8 with no output at all; getting to the boot handshake is the
        proof that the info struct is being read."""
        rv, _fw, gen, blob, dtb = self._parts()
        mach = self._image(rv, gen, blob, dtb, 1_000_000)
        self.assertIsNone(mach.error)
        self.assertFalse(mach.halted)
        self.assertIn(mach.pc, {0x80000DC0, 0x80000DC4, 0x80000DC6, 0x80000DCA},
                      "expected the fw_dynamic boot-handshake wait, not the reset hang")

    def test_the_stub_instructions_are_the_isa_ones(self) -> None:
        """The encoders are hand-written, so pin them to the ISA's spelling."""
        import gen_opensbi_image as gen

        self.assertEqual(gen.jal(0, 0x43000), 0x0004306F, "jal x0, +0x43000")
        self.assertEqual(gen.la(11, 0x80044000, 0x80043000), [0x00001597, 0x00058593],
                         "auipc a1, 1; addi a1, a1, 0")
        self.assertEqual(gen.jal(0, (gen.FW_AT + 4) - (gen.STUB_AT + 16)), 0xFF5BC06F,
                         "jal x0, back into the firmware")

    def test_the_image_layout_keeps_the_device_tree_clear_of_the_firmware(self) -> None:
        """The firmware runs in place at its link address, so the stub and the
        device tree have to sit above everything it is given."""
        import gen_opensbi_image as gen

        firmware_end = gen.FW_AT + 273_048            # the blob QEMU ships
        self.assertGreater(gen.STUB_AT, firmware_end)
        self.assertGreater(gen.DTB_AT, gen.STUB_AT + 32)
        self.assertGreater(gen.INFO_AT, gen.DTB_AT + 5_028)


class Rv64Sv39Tests(unittest.TestCase):
    """Sv39: three levels, 4 KiB and superpages, and a fault that reaches the
    handler the kernel installed. The walker is the gate between "S-mode runs"
    and "a kernel can run"."""

    def setUp(self) -> None:
        import rv64_ref

        self.rv = rv64_ref
        self.mach = rv64_ref.Machine()

    def _table(self, at: int, entries: dict[int, int]) -> int:
        for index, pte in entries.items():
            self.mach.write(at + index * 8, pte, 8)
        return at

    def _enable(self, root: int) -> None:
        """Sv39 on, root table at `root`, and an S-mode handler to catch faults."""
        rv = self.rv
        self.mach.priv = rv.PRIV_S
        self.mach.satp = (8 << 60) | (root >> 12)
        self.mach.stvec = rv.RAM_BASE + 0x8000
        self.mach.medeleg = 0xFFFF_FFFF          # everything to S-mode

    @staticmethod
    def _leaf(pa: int, flags: int) -> int:
        return ((pa >> 12) << 10) | flags

    def test_the_payload_walks_its_own_tables(self) -> None:
        """`sv39.elf` is the end-to-end proof: M-mode builds the tables and turns
        `satp` on, then S-mode reads and writes the same word under two names,
        checks that the walk set A and the store set D, and finally takes a load
        fault that `medeleg` sends to `stvec`."""
        mach = self.rv.Machine()
        self.rv.load_elf(mach, (RVDIR / "sv39.elf").read_bytes())
        mach.run(limit=20000)
        self.assertIsNone(mach.error)
        self.assertEqual(bytes(mach.out), b"SV39-OK\n")
        self.assertEqual(mach.traps[-1][0], self.rv.FAULT_LOAD)
        root = self.rv.RAM_BASE + 0x1000
        import struct

        leaf = struct.unpack_from("<Q", mach.ram, 0x1000 + 255 * 8)[0]
        self.assertTrue(leaf & self.rv.PTE_A, "the walk set accessed")
        self.assertTrue(leaf & self.rv.PTE_D, "the store set dirty")
        self.assertEqual((leaf >> 10) << 12, self.rv.RAM_BASE, "255 GiB -> RAM")
        self.assertEqual(root & 0xFFF, 0)

    def test_the_core_has_the_walker_too(self) -> None:
        """The machine is a transliteration of this file, so the pieces the model
        has to have -- a walker, a mode and a base -- must be in the core."""
        core = (RVDIR / "core.jsw").read_text()
        for name in ("private void translate(", "X.satpMode", "X.satpPpn",
                     "takeTrap(13, 0", "takeTrap(15, 0", "takeTrap(12, 0"):
            self.assertIn(name, core)

    def test_a_two_mebibyte_page_maps_a_gigabyte_of_ram(self) -> None:
        """One leaf at level 1 covers 2 MiB; the RAM base is one of them."""
        rv = self.rv
        root = self._table(rv.RAM_BASE + 0x1000, {
            2: self._leaf(rv.RAM_BASE + 0x2000, rv.PTE_V),        # -> a level-1 table
        })
        self._table(rv.RAM_BASE + 0x2000, {
            0: self._leaf(rv.RAM_BASE, rv.PTE_V | rv.PTE_R | rv.PTE_W | rv.PTE_X),
        })
        self._enable(root)
        self.mach.write(rv.RAM_BASE + 0x3100, 0x1122, 4)          # through the table
        self.assertEqual(self.mach.translate(rv.RAM_BASE + 0x3100, rv.FAULT_LOAD),
                         rv.RAM_BASE + 0x3100)
        self.assertEqual(self.mach.translate(rv.RAM_BASE + 0x2100000, rv.FAULT_LOAD),
                         None, "2 MiB up is outside the mapped page")

    def test_an_unmapped_load_traps_to_the_supervisor(self) -> None:
        """The code page is mapped, so the *fetch* succeeds and the fault comes
        from the data address -- which is what a kernel's own handler sees."""
        rv = self.rv
        root = self._table(rv.RAM_BASE + 0x1000, {
            2: self._leaf(rv.RAM_BASE + 0x2000, rv.PTE_V),
        })
        self._table(rv.RAM_BASE + 0x2000, {
            0: self._leaf(rv.RAM_BASE, rv.PTE_V | rv.PTE_R | rv.PTE_W | rv.PTE_X),
        })
        self._enable(root)
        self.mach.pc = rv.RAM_BASE + 0x4000
        self.mach.write(rv.RAM_BASE + 0x4000, 0x00003503, 4)      # ld a0, 0(zero)
        self.mach.step()
        self.assertEqual(self.mach.traps[-1][0], rv.FAULT_LOAD, "load page fault")
        self.assertEqual(self.mach.scause, rv.FAULT_LOAD)
        self.assertEqual(self.mach.stval, 0, "stval is the faulting address")
        self.assertEqual(self.mach.sepc, rv.RAM_BASE + 0x4000)
        self.assertEqual(self.mach.pc, self.mach.stvec, "and it went to stvec")

    def test_a_write_to_a_read_only_page_faults(self) -> None:
        rv = self.rv
        root = self._table(rv.RAM_BASE + 0x1000, {
            2: self._leaf(rv.RAM_BASE + 0x2000, rv.PTE_V),
        })
        self._table(rv.RAM_BASE + 0x2000, {
            0: self._leaf(rv.RAM_BASE, rv.PTE_V | rv.PTE_R | rv.PTE_X),   # no W
        })
        self._enable(root)
        self.assertIsNone(self.mach.translate(rv.RAM_BASE, rv.FAULT_STORE))
        self.assertEqual(self.mach.translate(rv.RAM_BASE, rv.FAULT_LOAD), rv.RAM_BASE)

    def test_the_walk_sets_the_accessed_and_dirty_bits(self) -> None:
        rv = self.rv
        root = self._table(rv.RAM_BASE + 0x1000, {
            2: self._leaf(rv.RAM_BASE + 0x2000, rv.PTE_V),
        })
        table = self._table(rv.RAM_BASE + 0x2000, {
            0: self._leaf(rv.RAM_BASE, rv.PTE_V | rv.PTE_R | rv.PTE_W),
        })
        self._enable(root)
        self.mach.translate(rv.RAM_BASE, rv.FAULT_LOAD)
        pte = self.mach.read(table, 8)
        self.assertTrue(pte & rv.PTE_A, "a load marks the page accessed")
        self.assertFalse(pte & rv.PTE_D, "a load does not mark it dirty")
        self.mach.translate(rv.RAM_BASE, rv.FAULT_STORE)
        self.assertTrue(self.mach.read(table, 8) & rv.PTE_D, "a store does")

    def test_paging_is_off_in_machine_mode(self) -> None:
        rv = self.rv
        root = self._table(rv.RAM_BASE + 0x1000, {})
        self.mach.satp = (8 << 60) | (root >> 12)
        self.mach.priv = rv.PRIV_M
        self.assertFalse(self.mach.paging(), "M-mode addresses are physical")
        self.assertEqual(self.mach.translate(0x1234, rv.FAULT_LOAD), 0x1234)


class Rv64SupervisorTests(unittest.TestCase):
    """The privilege boundary: `mret` into S-mode, an SBI `ecall` back into
    M-mode, and a trap the firmware delegates to `stvec`."""

    def setUp(self) -> None:
        import rv64_ref

        self.rv = rv64_ref

    def _boot(self):
        mach = self.rv.Machine()
        self.rv.load_elf(mach, (RVDIR / "smode.elf").read_bytes())
        mach.run(limit=5000)
        return mach

    def test_the_payload_makes_the_round_trip(self) -> None:
        mach = self._boot()
        self.assertIsNone(mach.error)
        self.assertEqual(bytes(mach.out), b"S-DOK\n",
                         "S-mode ran, made SBI calls, took a delegated trap and "
                         "came back through sret")

    def test_every_trap_is_accounted_for(self) -> None:
        """Three SBI calls reach M-mode, and only the fourth ecall -- after the
        firmware delegated it -- lands on `stvec`."""
        mach = self._boot()
        causes = [cause for cause, _epc, _pc in mach.traps]
        targets = [pc for _cause, _epc, pc in mach.traps]
        self.assertEqual(causes, [9, 9, 9, 9], "all four are ecall from S")
        self.assertEqual(targets[:3], [mach.mtvec & ~0b11] * 3, "the SBI path")
        self.assertEqual(targets[3], mach.stvec & ~0b11, "the delegated one")

    def test_s_mode_cannot_touch_a_machine_csr(self) -> None:
        """Writing `medeleg` from S-mode is an illegal instruction, and the trap
        goes to M-mode -- which is why the payload asks the SBI to do it."""
        mach = self.rv.Machine()
        base = self.rv.RAM_BASE
        # csrw medeleg, t0 ; then stop
        words = [0x30229073, 0x0000006B]          # the second is a stand-in
        for i, word in enumerate(words):
            mach.write(base + i * 4, word, 4)
        mach.mtvec = base + 0x100
        mach.write(base + 0x100, 0x50050301, 4)
        mach.priv = self.rv.PRIV_S
        mach.set_reg(5, 1 << 9)
        mach.pc = base
        mach.step()
        self.assertEqual(mach.traps[0][0], 2, "illegal instruction")
        self.assertEqual(mach.mcause, 2)
        self.assertEqual(mach.mstatus >> 11 & 0b11, self.rv.PRIV_S,
                         "MPP records that the trap came from S-mode")

    def test_the_core_has_the_supervisor_bank(self) -> None:
        core = (RVDIR / "core.jsw").read_text()
        for csr in (256, 261, 321, 322, 384, 770, 771):
            self.assertIn(f"csr == {csr}", core, f"CSR {csr:#x} is missing")
        self.assertIn("270532723", core, "sret is missing")
        self.assertIn("delegated(", core)
        self.assertIn("machineOnly(", core)
        war = JSW.read_text()
        for name in ("X.priv", "X.stvec", "X.medeleg", "X.mideleg", "X.satp"):
            self.assertIn(f"public {name}", war, f"{name} is not declared")


class Rv64BootChainTests(unittest.TestCase):
    """POST, then hand off — the way a board boots what is staged above it.

    `tools/gen_boot_image.py` stages the firmware and a boot record the BIOS
    looks for; the record's code copies the payload down over the BIOS before
    jumping, because this firmware only runs at its link address."""

    def _parts(self):
        import gen_boot_image
        import rv64_opensbi

        if not gen_boot_image.BIOS.exists():
            self.skipTest("bios.elf not built")
        try:
            blob = rv64_opensbi.find_blob().read_bytes()
        except SystemExit as exc:
            self.skipTest(str(exc))
        dtb_path = gen_boot_image.obi.DTB_PATH
        if not dtb_path.exists():
            self.skipTest("no device tree; run tools/gen_boot_image.py once")
        import gen_rv64_image

        return (gen_boot_image, gen_rv64_image.flatten(gen_boot_image.BIOS.read_bytes()),
                blob, dtb_path.read_bytes())

    def test_the_bios_finds_the_record_and_the_firmware_runs(self) -> None:
        import rv64_ref

        gen, bios, blob, dtb = self._parts()
        image = gen.build(bios, blob, dtb)
        mach = rv64_ref.Machine(ram_size=96 << 20, mem_mib=96, max_mem_mib=96)
        for i, byte in enumerate(image):
            mach.write(rv64_ref.RAM_BASE + i, byte, 1)
        mach.pc = rv64_ref.RAM_BASE
        mach.run(limit=2_500_000)
        self.assertIsNone(mach.error)
        self.assertFalse(mach.halted)
        self.assertIn(mach.pc, {0x80000DC0, 0x80000DC4, 0x80000DC6, 0x80000DCA},
                      "the firmware should be in its boot handshake, not POST")

    def test_the_copy_runs_above_what_it_overwrites(self) -> None:
        """The stub has to survive its own memmove: the copy writes the firmware
        at 0x8000_0000 upward, so the code doing it must sit past the last byte
        it writes -- and below the staged payload it is reading."""
        gen = self._parts()[0]

        destination_end = 273_048                      # what lands at 0x8000_0000
        code_end = gen.RECORD_AT + 16 + len(gen.copy_code(273_048))
        self.assertGreater(gen.RECORD_AT, destination_end,
                           "the copy would overwrite the record and its code")
        self.assertLessEqual(code_end, gen.FW_AT, "the copy code runs into the payload")

    def test_the_record_is_four_thirty_two_bit_words(self) -> None:
        gen = self._parts()[0]
        recorded = gen.record(273_048, 0x80088000, 0x8008A000)
        self.assertEqual(len(recorded), 16)
        magic, dtb, info, length = (int.from_bytes(recorded[i:i + 4], "little")
                                   for i in range(0, 16, 4))
        self.assertEqual(magic, 0x4A535742, "'JSWB'")
        self.assertEqual(dtb, 0x80088000)
        self.assertEqual(info, 0x8008A000)
        self.assertEqual(length, 273_048)
        # a bare-metal payload gets no arguments: a1 = a2 = 0
        _magic, bare_a1, bare_a2, _len = (int.from_bytes(gen.record(4096)[i:i + 4], "little")
                                         for i in range(0, 16, 4))
        self.assertEqual((bare_a1, bare_a2), (0, 0))


class Rv64CompressedTests(unittest.TestCase):
    """RV64C: the core executes 16-bit instructions by expanding them first, so
    the expander is the whole extension and it has to be exactly right.

    `rv64_rvc.expand` is the spec -- `python3 tools/rv64_rvc.py --assemble`
    checks it against zig's assembler. `rv64_rvc_mirror` is the same arithmetic
    as `core.jsw`, written out in Python so the two can be diffed here without
    booting TurboWarp."""

    def setUp(self) -> None:
        import rv64_rvc
        import rv64_rvc_mirror

        self.rvc = rv64_rvc
        self.mirror = rv64_rvc_mirror

    def test_the_jsw_expander_agrees_on_every_compressed_halfword(self) -> None:
        bad = []
        for half in range(0x10000):
            if half % 4 == 3:
                continue                      # that quadrant is a 32-bit word
            want = self.rvc.expand(half) or 0
            got = self.mirror.expandC(half)
            if got != want:
                bad.append((half, got, want))
        self.assertEqual(bad[:5], [], f"{len(bad)} of 49152 compressed halfwords differ")

    def test_the_forms_that_were_wrong_stay_right(self) -> None:
        # a negative c.lui has to sign-extend into bits 31:18, not just bit 17
        self.assertEqual(self.rvc.expand(0x700D), 0xFFFE3037, "c.lui x0, -29")
        # c.j scatters its offset: half bit 6 is imm[7], bit 7 is imm[6]
        self.assertEqual(self.rvc.expand(0xA045), 0x0A00006F, "c.j +160")
        # c.mv / c.swsp take a full five-bit rs2, unlike the x8-x15 fields
        self.assertEqual(self.rvc.expand(0x8006), 0x00100033, "c.mv x0, x1")
        self.assertEqual(self.rvc.expand(0xC02A), 0xA12023, "c.swsp x10, 0(sp)")

    def test_the_assembler_still_agrees_with_the_table(self) -> None:
        """Needs zig; the vectors themselves live next to the expander."""
        try:
            out = subprocess.run([sys.executable, str(ROOT / "tools" / "rv64_rvc.py"), "--assemble"],
                                 capture_output=True, text=True, timeout=300)
        except (OSError, subprocess.SubprocessError) as exc:      # pragma: no cover
            self.skipTest(f"could not run the assembler: {exc}")
        if "no assembler" in out.stdout + out.stderr or "not found" in out.stdout + out.stderr:
            self.skipTest("zig is not installed")
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        self.assertIn("match", out.stdout)

    def test_the_probe_runs_on_the_reference(self) -> None:
        """`userspace/rv64/cprobe.S` prints C-OK only if every compressed form
        it uses produced the right answer."""
        import rv64_ref

        mach = rv64_ref.Machine()
        rv64_ref.load_elf(mach, (RVDIR / "cprobe.elf").read_bytes())
        self.assertEqual(mach.pc, 0x80000000)
        mach.run(limit=100_000)
        self.assertEqual(bytes(mach.out), b"C-OK\n", f"pc {mach.pc:#x}, err {mach.error!r}")
        self.assertTrue(mach.halted)

    def test_the_core_expands_before_it_decodes(self) -> None:
        """The expansion happens inside `fetch`, so pc moves by two and the
        decoder never sees a compressed word."""
        core = (RVDIR / "core.jsw").read_text()
        self.assertIn("expandC(", core)
        self.assertIn("advancePc(", core)
        fetch = core[core.index("private void fetch("):]
        fetch = fetch[:fetch.index("\n    }")]
        self.assertIn("expandC(", fetch, "fetch expands")
        self.assertIn("% 4", fetch, "and only for quadrant 0-2 halfwords")


class Rv64ShellTests(unittest.TestCase):
    """The prompt is a shell: the line is kept in memory and parsed on Enter.

    The dialect is deliberately debug.exe-shaped, because the point is to look
    at memory and at the machine's own CSRs from inside the machine.
    """

    #: set-2 make codes for the characters these tests type
    SET2 = {
        "a": 0x1C, "b": 0x32, "c": 0x21, "d": 0x23, "e": 0x24, "f": 0x2B,
        "h": 0x33, "i": 0x43, "l": 0x4B, "m": 0x3A, "o": 0x44, "r": 0x2D,
        "s": 0x1B, "t": 0x2C, "v": 0x2A, "w": 0x1D, "x": 0x22, "z": 0x1A,
        "0": 0x45, "1": 0x16, "2": 0x1E, "3": 0x26, "4": 0x25,
        "5": 0x2E, "6": 0x36, "7": 0x3D, "8": 0x3E, "9": 0x46,
        " ": 0x29, "?": 0x4A,
    }

    def setUp(self) -> None:
        import rv64_ref

        self.rv = rv64_ref
        self.mach = rv64_ref.Machine()
        rv64_ref.load_elf(self.mach, BIOS.read_bytes())
        self.mach.run_frames(80, batch=20_000)
        self.assertIn("keyboard>", self._row(17), "the shell should be up")

    def _key(self, code: int) -> None:
        self.mach.ps2_fifo.append(code)
        self.mach.run_frames(2, batch=20_000)
        self.mach.ps2_fifo += [0xF0, code]
        self.mach.run_frames(2, batch=20_000)

    def _type(self, text: str) -> None:
        for ch in text:
            self._key(self.SET2[ch])

    def _enter(self) -> None:
        self.mach.ps2_fifo.append(0x5A)
        self.mach.run_frames(3, batch=20_000)
        self.mach.ps2_fifo += [0xF0, 0x5A]
        self.mach.run_frames(3, batch=20_000)

    def _row(self, index: int) -> str:
        text = self.mach.text
        line = "".join(chr(text[(index * 80 + x) * 2] or 32) for x in range(80)).rstrip()
        if line.endswith("\xdb"):
            line = line[:-1].rstrip()
        return line

    def test_md_dumps_bytes_with_an_ascii_column(self) -> None:
        self._type("md 80000000 8")
        self._enter()
        line = self._row(18)
        self.assertTrue(line.startswith("80000000 "), line)
        self.assertEqual(len(line), 8 + 1 + 8 * 3 + 8, "address, eight bytes, ascii: " + line)

    def test_mw_writes_and_md_reads_it_back(self) -> None:
        self._type("mw 80001000 41 42 43")
        self._enter()
        self.assertIn("bytes written", self._row(18))
        self._type("md 80001000 4")
        self._enter()
        tail = " ".join(self._row(r) for r in range(17, 24))
        self.assertIn("41 42 43", tail, "the bytes went where they were told")

    def test_mw_without_an_address_refuses_instead_of_faulting(self) -> None:
        """A missing address parses as zero, and a store to zero is unmapped --
        which used to stop the whole machine with `unmapped store at 0`. A
        debugger that dies on a typo is not a debugger."""
        self._type("mw")
        self._enter()
        self.assertIsNone(self.mach.error, self.mach.error)
        self.assertIn("address is required", " ".join(self._row(r) for r in (17, 18, 19)))

    def test_mw_reaches_the_chipset_a_byte_at_a_time(self) -> None:
        """The shell is how the new registers get poked by hand, and this is
        the path that caught the per-byte clamp."""
        self._type("mw 10004000 10 00 00 00")
        self._enter()
        self.mach.run_frames(4, batch=20_000)
        self.assertEqual(self.mach.mem_mib, 16)
        self._type("mw 10004000 08 00 00 00")
        self._enter()
        self.mach.run_frames(4, batch=20_000)
        self.assertEqual(self.mach.mem_mib, 8)

    def test_an_unknown_command_says_where_to_look(self) -> None:
        self._type("zz")
        self._enter()
        self.assertIn("? for the commands", self._row(18))
        self.assertTrue(self._row(19).startswith("keyboard>"), "and a fresh prompt")

    def test_r_prints_the_trap_state(self) -> None:
        self._key(0x2D)                     # r
        self._enter()
        lines = " ".join(self._row(r) for r in (18, 19, 20))
        for word in ("pc", "sp", "mtvec", "mepc", "mcause"):
            self.assertIn(word, lines)

    def test_n_puts_a_frame_on_the_wire_and_reports_the_reply(self) -> None:
        """The shortest path that proves the whole card: frame out, host
        bridge, frame back in, and the guest reads it."""
        self._key(0x31)                     # n
        self._enter()
        line = " ".join(self._row(r) for r in range(18, 21))
        self.assertIn("arp reply", line)
        self.assertIn("52:54:00:12:34:56", line, "the gateway's MAC")
        self.assertIn("0A000202", line, "and its address, 10.0.2.2")
        self.assertEqual(self.mach.net.arp_count, 1, "one request, one reply")

    def test_v_sets_a_640_by_480_plane(self) -> None:
        self._key(0x2A)                     # v
        self._key(0x29)                     # space
        self._key(0x1E)                     # 2
        self.mach.ps2_fifo.append(0x5A)     # enter: the mode set happens at once
        self.mach.run_frames(2, batch=20_000)
        self.assertEqual(self.mach.read(self.rv.DISP_BASE + self.rv.DISP_CTL_OFF, 1), 2)
        self.assertEqual((self.mach.gfx_w, self.mach.gfx_h), (640, 480))

    def test_a_640_by_480_pixel_lands_where_it_was_put(self) -> None:
        self.mach.write(self.rv.DISP_BASE + self.rv.DISP_CTL_OFF, 2, 1)
        base = self.rv.DISP_BASE + self.rv.DISP_GFX_OFF
        for x, y in ((0, 0), (639, 0), (0, 479), (639, 479), (320, 240)):
            self.mach.write(base + y * 640 + x, 0x2A, 1)
            self.assertEqual(self.mach.read(base + y * 640 + x, 1), 0x2A, f"({x},{y})")
        self.assertEqual(len(self.mach.gfx), 640 * 480)


class Rv64MemoryTests(unittest.TestCase):
    """The board's RAM: how big it is, who decides, and how a change happens.

    The Scratch machine keeps its memory in the `rv64mem` extension, so its
    default is a gigabyte. The reference model is a bytearray and caps itself at
    eight megabytes, which is the same *registers* with a smaller allocation --
    the firmware cannot tell the difference, which is the point.
    """

    def setUp(self) -> None:
        import rv64_ref

        self.rv = rv64_ref
        self.mach = rv64_ref.Machine()
        rv64_ref.load_elf(self.mach, BIOS.read_bytes())

    def _row(self, index: int) -> str:
        text = self.mach.text
        line = "".join(chr(text[(index * 80 + x) * 2] or 32) for x in range(80)).rstrip()
        if line.endswith("\xdb"):
            line = line[:-1].rstrip()
        return line

    def _record(self, mib: int, boot: int = 1, attr: int = 15, gfx: int = 0) -> bytes:
        body = bytes([0x4A, 0x53, 0x57, 0x32, 2, boot, attr, gfx, mib & 0xFF, mib >> 8])
        total = sum(body) & 0xFFFF
        return body + bytes([total & 0xFF, total >> 8]) + b"\xFF" * (256 - 12)

    def _boot(self, machine=None, record: int | None = None) -> None:
        if machine is not None:
            self.mach = machine
            self.rv.load_elf(self.mach, BIOS.read_bytes())
        if record is not None:
            self.mach.nvram[:] = self._record(record)
        self.mach.run_frames(160, batch=20_000)
        self.assertIsNone(self.mach.error, self.mach.error)

    def test_the_scratch_board_defaults_to_a_gigabyte(self) -> None:
        board = JSW.read_text(encoding="utf-8")
        self.assertIn("public X.memMiB = 1024", board)
        self.assertIn("public X.memMaxMiB = 1024", board)
        self.assertIn("0x1000_4000", board, "the chipset window is documented")

    def test_the_post_prints_the_size_the_board_reports(self) -> None:
        self._boot()
        self.assertIn("8192 KiB of ram at 0x80000000", self._row(4))

    def test_the_board_hands_out_the_size_the_record_asks_for(self) -> None:
        """The record is the setup screen's word; the board applies it and
        reboots once, which is how a memory change reaches the guest."""
        self._boot(self.rv.Machine(max_mem_mib=64), record=16)
        self.assertEqual(self.mach.ram_size, 16 << 20, "the new size is live")
        self.assertEqual(self.mach.resets, 1, "exactly one reboot")
        self.assertIn("16384 KiB of ram", self._row(4))
        self.assertIn("16384K OK", self._row(15))

    def test_a_matching_size_costs_no_reboot(self) -> None:
        self._boot(self.rv.Machine(mem_mib=16, max_mem_mib=64), record=16)
        self.assertEqual(self.mach.resets, 0)

    def test_a_size_the_board_cannot_give_is_adopted_not_retried(self) -> None:
        """Sixty-four mebibytes on a board capped at eight: one reboot, then the
        record takes what the board gave, so the next boot is quiet."""
        self._boot(self.rv.Machine(max_mem_mib=8), record=64)
        self.assertEqual(self.mach.ram_size, 8 << 20)
        self.assertEqual(int.from_bytes(self.mach.nvram[8:10], "little"), 8,
                         "the record was corrected")
        self.mach.run_frames(80, batch=20_000)
        self.assertEqual(self.mach.resets, 1, "no second reboot")

    def test_the_chipset_registers_read_back(self) -> None:
        self.mach.write(self.rv.BOARD_BASE + self.rv.BOARD_MEM_MIB, 4, 4)
        self.assertEqual(self.mach.read(self.rv.BOARD_BASE + self.rv.BOARD_MEM_MIB, 4), 4)
        self.assertEqual(self.mach.read(self.rv.BOARD_BASE + self.rv.BOARD_NOW, 4), 8,
                         "NOW is what is live, not what was asked for")
        self.mach.write(self.rv.BOARD_BASE + self.rv.BOARD_CMD, 1, 4)
        self.assertTrue(self.mach.reset_pending, "CMD 1 is the reset button")
        self.mach.reset()
        self.assertEqual(self.mach.read(self.rv.BOARD_BASE + self.rv.BOARD_NOW, 4), 4)
        self.assertEqual(self.mach.read(self.rv.BOARD_BASE + self.rv.BOARD_RESETS, 4), 1)

    def test_the_chipset_refuses_a_size_it_cannot_run(self) -> None:
        """A nonsense size is refused where it is spent, not where it is
        written: a byte-wise store is allowed to look absurd half way."""
        self.mach.write(self.rv.BOARD_BASE + self.rv.BOARD_MEM_MIB, 0, 4)
        self.assertEqual(self.mach.read(self.rv.BOARD_BASE + self.rv.BOARD_MEM_MIB, 4), 0)
        self.mach.write(self.rv.BOARD_BASE + self.rv.BOARD_CMD, 1, 4)
        self.mach.reset()
        self.assertEqual(self.mach.ram_size, 1 << 20, "one mebibyte is the floor")

    def test_a_word_store_reaches_the_chipset_as_four_bytes(self) -> None:
        """The bug this caught in the browser: the register was clamped on
        every byte, so 1024 -> 16 arrived as 1 and the machine rebooted into a
        mebibyte. Four byte writes must assemble exactly what was stored."""
        self.mach.mem_mib = 1024
        self.mach.write(self.rv.BOARD_BASE + self.rv.BOARD_MEM_MIB, 16, 4)
        self.assertEqual(self.mach.mem_mib, 16)
        self.mach.write(self.rv.BOARD_BASE + self.rv.BOARD_MEM_MIB, 1024, 4)
        self.assertEqual(self.mach.mem_mib, 1024)

    def test_the_post_prints_the_size_it_will_use(self) -> None:
        self.mach.run_frames(80, batch=20_000)
        self.assertIn("8192 KiB of ram at 0x80000000", self._row(4))

    def test_the_post_counts_the_memory_and_says_ok(self) -> None:
        self.mach.run_frames(80, batch=20_000)
        self.assertIn("memory test", self._row(15))
        self.assertIn("8192K OK", self._row(15))

    def test_a_key_stops_the_count_early(self) -> None:
        """Space is the key people press, but any key does it, and the count
        still finishes at the full size."""
        self.mach.run_frames(4, batch=20_000)
        self.assertLess(len(self._row(15)) > 0, 2)
        self.mach.ps2_fifo.append(0x29)     # space
        self.mach.run_frames(10, batch=20_000)
        line = self._row(15)
        self.assertIn("8192K OK", line, f"the count jumped to the end: {line!r}")

    def test_memory_above_the_old_sixty_four_kib_works(self) -> None:
        self.mach.run_frames(80, batch=20_000)
        for offset in (0x10000, 0x40000, 0x7FF000):
            self.mach.write(self.rv.RAM_BASE + offset, 0x5A, 1)
            self.assertEqual(self.mach.read(self.rv.RAM_BASE + offset, 1), 0x5A,
                             f"a byte at +0x{offset:x}")
        self.assertIsNone(self.mach.error, self.mach.error)


class Rv64MemorySetupTests(unittest.TestCase):
    """The memory item on the setup screen: a window with a number you type.

    A 2017 setup utility does exactly this -- the size is a field, not a list of
    three choices -- because the interesting values are 1, 8, 64 and 1024 and a
    menu of those is a menu of the author's guesses.
    """

    DELETE = 0x71
    ENTER = 0x5A
    ESCAPE = 0x76
    BACKSPACE = 0x66
    DOWN = 0x72
    DIGITS = {str(d): code for d, code in zip(
        "0123456789", (0x45, 0x16, 0x1E, 0x26, 0x25, 0x2E, 0x36, 0x3D, 0x3E, 0x46))}

    def setUp(self) -> None:
        import rv64_ref

        self.rv = rv64_ref
        self.mach = rv64_ref.Machine(max_mem_mib=64)
        rv64_ref.load_elf(self.mach, BIOS.read_bytes())
        self.mach.run_frames(40, batch=20_000)
        self.mach.ps2_fifo.append(self.DELETE)
        self.mach.run_frames(10, batch=20_000)
        for _ in range(3):                  # down to "Memory size"
            self._key(self.DOWN, extended=True)

    def _key(self, code: int, extended: bool = False) -> None:
        prefix = [0xE0] if extended else []
        self.mach.ps2_fifo += prefix + [code]
        self.mach.run_frames(3, batch=20_000)
        self.mach.ps2_fifo += [0xF0] + prefix + [code]
        self.mach.run_frames(3, batch=20_000)

    def _type(self, text: str) -> None:
        for ch in text:
            self._key(self.DIGITS[ch])

    def _row(self, index: int) -> str:
        text = self.mach.text
        line = "".join(chr(text[(index * 80 + x) * 2] or 32) for x in range(80)).rstrip()
        if line.endswith("\xdb"):
            line = line[:-1].rstrip()
        return line

    def _menu_memory(self) -> str:
        return self._row(7)[0:44]

    def test_enter_opens_a_window_with_the_size_in_it(self) -> None:
        self._key(self.ENTER)
        self.assertIn("How much RAM to give, in MiB", self._row(11))
        self.assertIn("8", self._row(13), "the value in force is already typed in")
        self.assertIn("Enter accept", self._row(16))
        self.assertIn("Esc cancel", self._row(16))

    def test_digits_replace_the_size_and_enter_keeps_it(self) -> None:
        self._key(self.ENTER)
        self._key(self.BACKSPACE)
        self._type("16")
        self.assertIn("16", self._row(13))
        self._key(self.ENTER)
        self.assertIn("16 MiB", self._menu_memory())
        self.assertEqual(int.from_bytes(self.mach.nvram[8:10], "little"), 8,
                         "nothing is written until the screen is saved")

    def test_saving_a_new_size_reboots_into_it(self) -> None:
        self._key(self.ENTER)
        self._key(self.BACKSPACE)
        self._type("16")
        self._key(self.ENTER)
        self._key(0x1B)                     # 's' -- save and exit
        self.mach.run_frames(200, batch=20_000)
        self.assertEqual(self.mach.ram_size, 16 << 20, "the board gave the new size")
        self.assertEqual(self.mach.resets, 1, "one reboot, like a real setup utility")
        self.assertIn("16384 KiB of ram", self._row(4))
        self.assertEqual(int.from_bytes(self.mach.nvram[8:10], "little"), 16)

    def test_a_size_out_of_range_is_refused_in_the_window(self) -> None:
        """Two thousand is more than the field will promise, and the window has
        to say so instead of quietly booting something else."""
        self._key(self.ENTER)
        for _ in range(4):
            self._key(self.BACKSPACE)
        self._type("2000")
        self._key(self.ENTER)
        self.assertIn("Out of range", self._row(15))
        self.assertIn("How much RAM", self._row(11), "still in the window")
        self.assertEqual(int.from_bytes(self.mach.nvram[8:10], "little"), 8)

    def test_escape_throws_nothing_away(self) -> None:
        before = bytes(self.mach.nvram)
        self._key(self.ENTER)
        self._key(self.BACKSPACE)
        self._type("32")
        self._key(self.ESCAPE)
        self.assertIn("8 MiB", self._menu_memory(), "the old value came back")
        self.assertEqual(bytes(self.mach.nvram), before)
        self.assertEqual(self.mach.ram_size, 8 << 20)

    def test_backspace_past_the_start_is_harmless(self) -> None:
        self._key(self.ENTER)
        for _ in range(6):
            self._key(self.BACKSPACE)
        self._key(self.ENTER)               # an empty field is not a size
        self.assertIn("Out of range", self._row(15))
        self._key(self.ESCAPE)


class Rv64FontTests(unittest.TestCase):
    """The glyph table is CP437-shaped: one glyph per byte, 0..255.

    The text plane stores a byte a cell, so the table has to be indexed by that
    byte. ASCII alone cannot draw a frame or an arrow, and a BIOS screen wants
    both.
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls.rows = [int(v) for v in json.loads(FONT.read_text())]

    def test_there_is_one_glyph_for_every_byte(self) -> None:
        self.assertEqual(len(self.rows), 256 * ROWS_PER_GLYPH)

    def test_ascii_is_still_where_it_was(self) -> None:
        """'A' must still be the letter A, not something shifted by the table."""
        base = ord("A") * ROWS_PER_GLYPH
        glyph = self.rows[base : base + ROWS_PER_GLYPH]
        self.assertTrue(all(v == 0 for v in glyph[:2]), "the top two rows are empty")
        self.assertNotEqual(glyph, [0] * ROWS_PER_GLYPH, "A has no ink at all")

    def test_the_box_drawing_glyphs_exist(self) -> None:
        for code, name in ((0xC9, "top left"), (0xBB, "top right"), (0xC8, "bottom left"),
                           (0xBC, "bottom right"), (0xCD, "horizontal"), (0xBA, "vertical"),
                           (0xCB, "top junction"), (0xCA, "bottom junction")):
            base = code * ROWS_PER_GLYPH
            glyph = self.rows[base : base + ROWS_PER_GLYPH]
            self.assertNotEqual(glyph, [0] * ROWS_PER_GLYPH, f"{code:#04x} ({name}) is blank")

    def test_a_horizontal_rule_is_a_full_width_row(self) -> None:
        base = 0xCD * ROWS_PER_GLYPH
        glyph = self.rows[base : base + ROWS_PER_GLYPH]
        self.assertIn(0xFF, glyph, "a horizontal rule has to reach both edges")

    def test_a_vertical_rule_is_one_column(self) -> None:
        base = 0xBA * ROWS_PER_GLYPH
        glyph = self.rows[base : base + ROWS_PER_GLYPH]
        self.assertEqual(sum(1 for v in glyph if v), ROWS_PER_GLYPH, "the column is unbroken")
        self.assertLessEqual(max(bin(v).count("1") for v in glyph), 2, "it is a double line")

    def test_the_arrows_point_the_way_they_say(self) -> None:
        def width(code, row):
            return bin(self.rows[code * ROWS_PER_GLYPH + row]).count("1")

        # 0x1E is up: narrow at the top, wide at the bottom
        self.assertLess(width(0x1E, 3), width(0x1E, 9))
        # 0x1F is down: wide at the top
        self.assertGreater(width(0x1F, 4), width(0x1F, 10))

    def test_the_painter_indexes_by_character_code(self) -> None:
        text = (RVDIR / "display.jsw").read_text(encoding="utf-8")
        body = text.split("private void paintCell(", 1)[1].split("\n    }", 1)[0]
        self.assertIn("_base = _ch * X.dispGlyphH", body)
        self.assertNotIn("_ch - 32", body, "the table is not ASCII-offset any more")

    def test_the_asset_is_not_stale(self) -> None:
        r = subprocess.run([sys.executable, str(ROOT / "tools" / "gen_rv64_font.py"), "--check"],
                           cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)


class Rv64ControlKeyTests(unittest.TestCase):
    """Control keys: the 8042 sends the code, the firmware translates it."""

    CODES = {"h": 0x33, "i": 0x43, "r": 0x2D, "c": 0x21, "d": 0x31, "2": 0x1E}

    def setUp(self) -> None:
        if not BIOS.exists():
            raise unittest.SkipTest(f"{BIOS} is not built (see docs/RV64.md)")
        import rv64_ref

        self.mach = rv64_ref.Machine()
        rv64_ref.load_elf(self.mach, BIOS.read_bytes())
        self._go(600_000)          # one WFI is one frame here, so this is fast

    def _go(self, count: int) -> None:
        target = self.mach.steps + count
        while self.mach.steps < target and not self.mach.halted:
            self.mach.step()

    def _key(self, name: str) -> None:
        code = self.CODES[name]
        self.mach.ps2_fifo.append(code)
        self._go(60_000)
        self.mach.ps2_fifo += [0xF0, code]
        self._go(60_000)

    def _row(self, index: int) -> str:
        text = self.mach.text
        line = "".join(chr(text[(index * 80 + x) * 2] or 32) for x in range(80)).rstrip()
        if line.endswith("\xdb"):          # the block cursor is not content
            line = line[:-1].rstrip()
        return line

    def _hold_control(self) -> None:
        self.mach.ps2_fifo.append(0x14)

    def _release_control(self) -> None:
        self.mach.ps2_fifo += [0xF0, 0x14]

    def test_control_alone_prints_nothing(self) -> None:
        self._hold_control()
        self._go(60_000)
        self._release_control()
        self._go(60_000)
        self.assertEqual(self._row(17), "keyboard>")

    def test_control_c_echoes_and_starts_a_new_line(self) -> None:
        for name in ("h", "i", "r"):
            self._key(name)
        self._hold_control()
        self._key("c")
        self._release_control()
        self._go(60_000)
        self.assertEqual(self._row(17), "keyboard> hir^C")
        self.assertEqual(self._row(18), "keyboard>", "a fresh prompt after the interrupt")

    def test_a_letter_without_control_is_still_a_letter(self) -> None:
        self._key("c")
        self.assertEqual(self._row(17), "keyboard> c")

    def test_control_beats_shift(self) -> None:
        """^C is 0x03 whether or not shift is down, as on a real keyboard."""
        self.mach.ps2_fifo += [0x12, 0x14]      # shift + control
        self._go(60_000)
        self._key("c")
        self.mach.ps2_fifo += [0xF0, 0x14, 0xF0, 0x12]
        self._go(60_000)
        self.assertIn("^C", self._row(17), "shift should not turn ^C into something else")

    def test_the_controller_latches_a_modifier_the_poll_missed(self) -> None:
        """`lastKeyPressed` proves a press happened even if it ended in a frame."""
        inp = (RVDIR / "input.jsw").read_text(encoding="utf-8")
        body = inp.split("private void latchModifier(", 1)[1].split("\n    }", 1)[0]
        self.assertIn("TurboWarp.lastKeyPressed", body)
        self.assertIn("push(K.release)", body, "a latched tap must end: never stuck down")
        self.assertIn("if (K.down.get(_at) > 0)", body, "a key the poll saw must not be doubled")


class Rv64DiskTests(unittest.TestCase):
    """The block device: one file, hex over a localhost WebSocket, 8 slots.

    The whole point is that the disk is *not* a list — a root filesystem of any
    size stays in ``disk.img`` and only the blocks in flight live in Scratch.
    """

    @classmethod
    def setUpClass(cls) -> None:
        if not DISK_IMAGE.exists():
            raise unittest.SkipTest(f"{DISK_IMAGE} is not built (see docs/RV64.md)")
        sys.path.insert(0, str(ROOT / "tools"))
        import rv64_disk

        cls.disk = rv64_disk

    def image(self):
        image = self.disk.Image(DISK_IMAGE)
        self.addCleanup(image.fh.close)
        return image

    def test_the_image_is_a_known_pattern(self) -> None:
        data = DISK_IMAGE.read_bytes()
        self.assertEqual(len(data), 16 * 4096)
        self.assertEqual(data, bytes((i * 7 + 13) & 0xFF for i in range(len(data))))

    def test_a_reply_is_the_block_in_hex(self) -> None:
        reply = self.disk.handle(self.image(), "R 3")
        kind, index, payload = reply.split(" ")
        self.assertEqual((kind, index), ("D", "3"))
        self.assertEqual(len(payload), 2 * 4096)
        self.assertEqual(bytes.fromhex(payload), DISK_IMAGE.read_bytes()[3 * 4096:4 * 4096])

    def test_the_server_caches_a_block_it_has_read(self) -> None:
        image = self.image()
        self.assertEqual(self.disk.handle(image, "R 5"), self.disk.handle(image, "R 5"))
        self.assertEqual((image.reads, image.hits), (2, 1))

    def test_bad_requests_answer_an_error(self) -> None:
        image = self.image()
        for text in ("", "Q 1", "R x", "R 99", "R -1", "R"):
            self.assertTrue(self.disk.handle(image, text).startswith("E"), text)

    def test_the_scratch_run_read_the_blocks_out_of_the_file(self) -> None:
        """`userspace/rv64/disk.S` loads 0, 1, 7, 4095, 4096, 4097 and 20480.

        The bytes it printed can only come from the file: block 0 and block 5
        both start with 0x0d, while the neighbour addresses differ.
        """
        data = DISK_IMAGE.read_bytes()
        expected = "\n".join(f"{data[a]:016x}" for a in (0, 1, 7, 4095, 4096, 4097, 20480))
        self.assertEqual(JSW_DISK_OUT.read_text(encoding="latin-1").rstrip("\n"), expected)

    def test_the_project_has_one_outstanding_request(self) -> None:
        """A second `R` before the reply would decode into the wrong slot.

        Two kinds of message go out on this socket now: `R <block>` for the
        disk and `T` for the host clock. Each has its own in-flight flag, and
        each flag is cleared by the reply that answers it -- the invariant is
        one request of each kind, never two of the same.
        """
        text = "\n".join(
            line for line in (RVDIR / "net.jsw").read_text(encoding="utf-8").splitlines()
            if not line.lstrip().startswith(("*", "//", "/*"))
        )
        self.assertEqual(text.count('join("R ", X.reqBlock)'), 1)
        self.assertEqual(text.count("WebSocket.sendMessage"), 3,
                         "R, T and N: a block, the clock and a frame")
        self.assertEqual(text.count("X.reqSent = 1"), 1)
        self.assertEqual(text.count("X.timeSent = 1"), 1)
        # every flag is cleared by a reply, not by a timer
        self.assertIn("X.reqSent = 0", text)
        self.assertIn("X.timeSent = 0", text)
        # and the CPU only raises a request line; it never formats one
        core = (RVDIR / "core.jsw").read_text(encoding="utf-8")
        self.assertIn("X.reqPending = 1", core)
        self.assertNotIn("sendMessage", core)

    def test_the_hat_only_parks_the_reply(self) -> None:
        """No loops in a hat: a reply restarts it, and the work is lost."""
        text = (RVDIR / "net.jsw").read_text(encoding="utf-8")
        hat = text.split("public void onDiskMessage()", 1)[1].split("\n    }", 1)[0]
        self.assertIn("X.msgReady = 1", hat)
        self.assertNotIn("diskCache", hat)
        self.assertNotIn("while (", hat)
        # the clock reply is parsed in a warp procedure, not in the hat
        self.assertIn("hostUsFrom(_msg)", hat)

    def test_no_break_inside_a_loop(self) -> None:
        """`break` is `stop this script` here, so it ends `greenFlag` itself."""
        from jswc.codegen import Codegen
        from jswc.lexer import tokenize
        from jswc.parser import Parser

        from jswc import include

        program = Parser(tokenize(include.expand(JSW).text)).parse()
        codegen = Codegen(program, base_dir=RVDIR, allow_undefined=True)
        codegen.compile()
        loop_breaks = [d for d in codegen.diagnostics if "W0025" in d]
        self.assertEqual(loop_breaks, [], "\n".join(loop_breaks))


if __name__ == "__main__":
    unittest.main()
