"""PXE: the machine boots a file it fetched over the wire.

This is the whole path in one test -- the ROM finds a card on the PCI bus, DHCP
gives it an address and a file name, ARP finds the server, TFTP carries the file
512 bytes at a time, and the loader jumps into it at 0x7C00 like a boot sector.

The wire is the real one: `tools/i386_net.py`'s NAT, in this process, answering
the same frames it answers in production. Nothing is stubbed, so a bug in the
card's ring format or in a byte order shows up here rather than in a boot that
sits still.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import i386_image  # noqa: E402
import i386_net as net  # noqa: E402
import i386_ref as rv  # noqa: E402

TFTP = ROOT / "build" / "i386" / "tftp"


def loader_entry() -> int:
    """Where `pxeTrampoline` is: the marker, then the address to jump to.

    The boot order can name the network without a key being pressed, and a test
    should not have to drive a menu to reach the loader -- so the ROM carries a
    way in that is not a menu.
    """
    rom = i386_image.build()
    mark = rom.index(b"JSWPXE\x00")
    return int.from_bytes(rom[mark + 7:mark + 9], "little")


class NetworkBootTests(unittest.TestCase):
    def _run(self, tftp: Path, limit: int = 1_800_000):
        """Power on, wait for POST to install its vectors, then jump in."""
        nat = net.Nat(tftp)
        nat.dhcp.boot_file = "boot.nbp"
        machine = rv.Machine()
        machine.ram[:] = i386_image.build()
        machine.nic_wire = lambda frame: [machine.nic_receive(f)
                                          for f in nat.from_guest(frame)]
        machine.sreg[rv.SEG_CS] = rv.RESET_CS
        machine.eip = rv.RESET_IP
        while machine.steps < 1_500_000:
            machine.run(limit=min(1_500_000, machine.steps + 100_000))
        machine.write(0x676, 0, 1)          # mode 0: boot the file DHCP names
        machine.sreg[rv.SEG_CS] = 0xF000
        machine.eip = loader_entry()
        while machine.steps < limit and machine.sreg[rv.SEG_CS] != 0x07C0:
            machine.run(limit=min(limit, machine.steps + 2000))
            for frame in nat.poll():
                machine.nic_receive(frame)
        return machine, nat

    def test_the_wire_starts_with_an_offer_and_ends_with_a_jump(self) -> None:
        self.assertTrue((TFTP / "boot.nbp").is_file(),
                        "make i386 puts the boot file there")
        machine, nat = self._run(TFTP)
        self.assertEqual(nat.dhcp.pxe_requests > 0, True, "the vendor class was sent")
        self.assertGreater(nat.dhcp.acks, 0, "DHCP was completed, not just offered")
        self.assertEqual(nat.tftp.errors, 0)
        self.assertEqual(machine.sreg[rv.SEG_CS], 0x07C0,
                         "the loader jumps to the file at 0x7C00")
        self.assertEqual(machine.ram[0x7C00:0x7C08],
                         (TFTP / "boot.nbp").read_bytes()[:8],
                         "and the bytes there are the file's")

    def test_the_payload_that_arrived_is_the_program(self) -> None:
        machine, _ = self._run(TFTP)
        machine.run(limit=machine.steps + 2_000_000)
        screen = machine.text()
        self.assertIn("MEOW-OS", screen, "the fetched boot sector is running")

    def test_a_missing_file_is_a_message_and_not_a_hang(self) -> None:
        """A PXE boot that cannot find its file must say so and give up."""
        empty = TFTP.parent / "empty-tftp"
        empty.mkdir(exist_ok=True)
        machine, nat = self._run(empty, limit=2_400_000)
        self.assertEqual(nat.tftp.reads, 1, "one read request, not a storm")
        self.assertNotEqual(machine.sreg[rv.SEG_CS], 0x07C0, "nothing was booted")
        # the loader's own error byte: 1 is the TFTP server's "no such file",
        # passed through from the ERROR packet rather than invented here
        self.assertEqual(machine.ram[0x675], 1,
                         "the loader recorded why, instead of sitting still")
        self.assertEqual(machine.ram[0x670:0x672], b"\x00\x00", "and loaded nothing")

    def _screen(self, machine) -> str:
        return "".join(chr(machine.ram[0xB8000 + i * 2]) for i in range(80 * 30))

    def test_a_card_with_no_wire_says_so_before_it_waits(self) -> None:
        """The fault a person actually hits is not a DHCP bug: there is no host
        wire, so there is nobody to answer. The card reports the link, so the
        menu says it before a key is pressed and the loader says it again --
        through the menu, because that is the way in a person has."""
        machine = rv.Machine()
        machine.ram[:] = i386_image.build()
        # DEL, Boot Options, Network (PXE), PXE (boot file)
        machine.keys.extend([0x32, 0x1C, 0x50, 0x50, 0x50, 0x1C, 0x1C])
        machine.sreg[rv.SEG_CS] = rv.RESET_CS
        machine.eip = rv.RESET_IP
        limit = 12_000_000
        while machine.keys and machine.steps < limit:
            machine.run(limit=min(limit, machine.steps + 250_000))
        machine.run(limit=machine.steps + 900_000)
        self.assertEqual(machine.nic_register(0x5C, 1) & 4, 4,
                         "MSR says LINK_FAIL with nothing plugged in")
        screen = self._screen(machine)
        self.assertIn("no network link", screen)
        self.assertIn("the host wire is not running", screen)
        self.assertIn("Press any key to return", screen,
                      "the reason is held on the screen, not wiped by a redraw")
        self.assertNotEqual(machine.sreg[rv.SEG_CS], 0x07C0, "nothing was booted")

    def test_the_network_menu_reports_the_link_it_finds(self) -> None:
        """Both answers, on the panel, before anything is waited for."""
        def panel(wire):
            machine = rv.Machine()
            machine.ram[:] = i386_image.build()
            machine.nic_wire = wire
            machine.keys.extend([0x32, 0x1C, 0x50, 0x50, 0x50, 0x1C])
            machine.sreg[rv.SEG_CS] = rv.RESET_CS
            machine.eip = rv.RESET_IP
            limit = 12_000_000
            while machine.keys and machine.steps < limit:
                machine.run(limit=min(limit, machine.steps + 250_000))
            machine.run(limit=machine.steps + 900_000)
            return self._screen(machine)

        self.assertIn("no link - start the host wire", panel(None))
        self.assertIn("link up, 100 Mb/s", panel(lambda frame: None))

    def test_a_card_with_a_wire_reports_the_link_and_boots(self) -> None:
        """The same bit, the other way: a wire is a link, and the boot that
        follows is the one the rest of this file already proves."""
        machine, nat = self._run(TFTP)
        self.assertEqual(machine.sreg[rv.SEG_CS], 0x07C0, "the file was booted")
        self.assertGreater(nat.dhcp.pxe_requests, 0, "and it was a PXE client")


if __name__ == "__main__":
    unittest.main()
