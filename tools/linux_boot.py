#!/usr/bin/env python3
"""Hand a 32-bit Linux the machine, from Python, so it can be watched.

This is the reference implementation of the boot protocol: what
`userspace/i386/linuxboot.asm` does in a boot sector, written where it can be
stepped and printed instead of guessed at. It is also the harness behind the
opt-in test that boots a *real* kernel:

    python3 tools/mkvmlinux.py vmlinuz-6.1.0-50-686 -o /tmp/vmlinux.elf
    JSW_VMLINUX=/tmp/vmlinux.elf python -m unittest tests.test_i386 -k RealKernel

The three things that are easy to get wrong, and which this file therefore says
out loud:

    the load address   comes out of the ELF, not from a constant. A Debian i686
                       kernel is linked at 16 MB; `code32_start` is its entry.
    the memory map     is E820 out of `int 15h`, and it only tells the truth if
                       the CMOS agrees with the RAM the machine really has.
    the registers      are handed over in `reg[]`, not as attributes: eax is
                       index 0, ecx 1, edx 2, ebx 3, esp 4, ebp 5, esi 6, edi 7.
"""

from __future__ import annotations

import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import i386_ref as rv  # noqa: E402

ZERO_PAGE = 0x10000          # where boot_params goes, and what ESI points at
CMDLINE = 0x12000            # the command line, where cmd_line_ptr points
GDT = 0x13000                # two flat descriptors and a null
PFN = 2 * 1024 * 1024        # a 4 KB page table fits anywhere; 2 MB is empty

PROTOCOL = 0x020F            # 2.15: what this loader fills in
DEFAULT_CMDLINE = b"console=ttyS0,115200 earlyprintk=serial,ttyS0,115200"


def load_elf(mach, image: bytes) -> dict:
    """Copy every PT_LOAD segment to the physical address it asks for."""
    if image[0:4] != b"\x7fELF":
        raise ValueError("not an ELF image")
    if struct.unpack_from("<H", image, 18)[0] != 3:
        raise ValueError("not an i386 ELF")
    entry = struct.unpack_from("<I", image, 24)[0]
    phoff = struct.unpack_from("<I", image, 28)[0]
    entsize, count = struct.unpack_from("<HH", image, 42)
    placed = []
    for i in range(count):
        kind, offset, _vaddr, paddr, filesz, memsz, _flags, _align = struct.unpack_from(
            "<8I", image, phoff + i * entsize
        )
        if kind != 1:
            continue
        if paddr + memsz > mach.ram_size:
            raise ValueError(
                f"the kernel wants memory to {paddr + memsz:#x} and the machine "
                f"has {mach.ram_size:#x}; give it more RAM"
            )
        mach.ram[paddr:paddr + filesz] = image[offset:offset + filesz]
        if memsz > filesz:
            mach.ram[paddr + filesz:paddr + memsz] = bytes(memsz - filesz)
        placed.append({"paddr": paddr, "filesz": filesz, "memsz": memsz})
    return {"entry": entry, "segments": placed}


def e820(mach, count_limit: int = 16) -> int:
    """Ask the firmware for the memory map and write it into the zero page.

    `phys` and `interrupt` are separate steps here for the same reason they are
    on real hardware: the handler runs when the CPU gets to it.
    """
    mach.loadseg(rv.SEG_ES, ZERO_PAGE >> 4)
    mach.cache_segment(rv.SEG_ES, ZERO_PAGE >> 4)
    di = 0x2D0
    index = 0
    entries = 0
    while entries < count_limit:
        mach.reg[0] = 0xE820
        mach.reg[3] = index
        mach.reg[1] = 20
        mach.reg[2] = 0x534D4150
        mach.reg[7] = di
        mach.interrupt(0x15)
        mach.run(limit=mach.steps + 2000)
        index = mach.reg[3] & 0xFFFF_FFFF
        entries += 1
        di += 20
        if index == 0:
            break
    mach.ram[ZERO_PAGE + 0x1E8] = entries
    return entries


def boot_params(mach, entry: int, cmdline: bytes = DEFAULT_CMDLINE) -> None:
    """The page a kernel reads before it runs: `HdrS`, the map, the line."""
    page = ZERO_PAGE
    mach.ram[page:page + 4096] = bytes(4096)

    line = cmdline if cmdline.endswith(b"\0") else cmdline + b"\0"
    mach.ram[CMDLINE:CMDLINE + len(line)] = line

    # screen_info: the console asks the card what it was, even with a serial
    # console on the command line
    mach.write(page + 0x04, 16384, 2)         # ext_mem_k
    mach.write(page + 0x08, 3, 2)             # orig_video_mode: 80x25 text
    mach.write(page + 0x0A, 80, 2)            # cols
    mach.write(page + 0x0C, 3, 2)             # orig_video_ega_bx
    mach.write(page + 0x0E, 25, 2)            # lines
    mach.ram[page + 0x0F] = 1                 # orig_video_isVGA
    mach.write(page + 0x10, 16, 2)            # points

    mach.write(page + 0x1FE, 0xAA55, 2)       # boot_flag
    mach.write(page + 0x202, 0x53726448, 4)   # hdr: 'HdrS'
    mach.write(page + 0x206, PROTOCOL, 2)
    mach.ram[page + 0x210] = 0xFF             # type_of_loader: undefined
    mach.ram[page + 0x211] = 0x01             # loadflags: LOADED_HIGH
    mach.write(page + 0x20A, 0xFFFF, 2)       # vid_mode: leave it alone
    mach.write(page + 0x214, entry, 4)        # code32_start
    mach.write(page + 0x218, 0, 4)            # no initramfs yet
    mach.write(page + 0x21C, 0, 4)
    mach.write(page + 0x224, 0x8000, 2)       # heap_end_ptr: real mode only
    mach.write(page + 0x228, CMDLINE, 4)      # cmd_line_ptr
    mach.write(page + 0x22C, 0x37FFFFFF, 4)   # initrd_addr_max
    mach.write(page + 0x230, 0x100000, 4)     # kernel_alignment
    mach.ram[page + 0x234] = 1                # relocatable_kernel
    mach.write(page + 0x238, 2047, 4)         # cmdline_size
    mach.ram[page + 0x1EF] = 0xFF             # sentinel


def enter(mach, entry: int) -> None:
    """The descriptors, CR0.PE, and the three registers the protocol names."""
    table = struct.pack("<Q", 0)
    table += struct.pack("<Q", 0x00CF9A000000FFFF)   # code: base 0, 4 GB, 32-bit
    table += struct.pack("<Q", 0x00CF92000000FFFF)   # data: base 0, 4 GB
    mach.ram[GDT:GDT + len(table)] = table
    mach.gdtr = [GDT, len(table) - 1]

    mach.cr[0] |= 1
    mach.pe = True
    mach.eip = entry
    for seg, selector in ((rv.SEG_CS, 0x0008), (rv.SEG_DS, 0x0010),
                          (rv.SEG_SS, 0x0010), (rv.SEG_ES, 0x0010),
                          (rv.SEG_FS, 0x0010), (rv.SEG_GS, 0x0010)):
        # a selector is not a descriptor: the cache has to be told as well
        mach.loadseg(seg, selector)
        mach.cache_segment(seg, selector)
    mach.reg[4] = 0x90000          # esp
    mach.reg[6] = ZERO_PAGE        # esi: boot_params
    mach.reg[0] = 0x2BADB002       # eax: the magic number
    mach.reg[3] = mach.reg[5] = mach.reg[7] = 0
    mach.eflags = 2


def boot(mach, image: bytes, cmdline: bytes = DEFAULT_CMDLINE) -> dict:
    """Everything above, in the order a bootloader does it."""
    facts = load_elf(mach, image)
    e820(mach)
    boot_params(mach, facts["entry"], cmdline)
    enter(mach, facts["entry"])
    return facts


def post(ram: int = 64 << 20, disk: bytes | None = None):
    """A machine that has just finished POST, with the RAM and disk we mean."""
    import i386_image

    mach = rv.Machine(ram_size=ram, disk=disk)
    rom = i386_image.build()
    mach.ram[0:len(rom)] = rom
    mach.sreg[rv.SEG_CS] = rv.RESET_CS
    mach.eip = rv.RESET_IP
    mach.run(limit=2_000_000)                   # POST, which brings COM1 up
    mach.cmos[0x17] = ((ram >> 20) - 1) & 0xFF   # the RAM this board really has
    mach.cmos[0x18] = (((ram >> 20) - 1) >> 8) & 0xFF
    return mach


def boot_disk(mach, blob: bytes) -> None:
    """Boot sector 0 the way the firmware would: loaded at 0x7C00, jumped to.

    The disk itself is the image the host server serves, so the loader's sector
    reads go through the same ATA device a browser would drive."""
    if len(blob) < 512 or blob[510:512] != b"\x55\xaa":
        raise ValueError("that disk has no boot sector")
    mach.ram[0x7C00:0x7E00] = blob[0:512]
    for seg in (rv.SEG_CS, rv.SEG_SS, rv.SEG_DS, rv.SEG_ES):
        mach.loadseg(seg, 0)          # a selector is not a descriptor
        mach.cache_segment(seg, 0)
    mach.eip = 0x7C00
    mach.reg[7] = 0
    mach.reg[3] = 0
    mach.reg[2] = 0x80                          # dl: the first hard disk


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__.split("\n\n")[1], file=sys.stderr)
        return 2
    image = Path(argv[1]).read_bytes()
    steps = int(argv[2]) if len(argv) > 2 else 200_000
    disk = "--disk" in argv

    mach = post(disk=image if disk else None)
    if disk:
        boot_disk(mach, image)
        facts = {"entry": 0, "segments": []}
    else:
        facts = boot(mach, image)
    print(f"entry {facts['entry']:#x}, "
          + ", ".join(f"{s['paddr']:#x}+{s['memsz']:#x}" for s in facts["segments"]))

    high = False
    for _ in range(steps):
        mach.step()
        if mach.eip >= 0xC000_0000 and mach.cr[0] >> 31:
            high = True
        if mach.halted:
            break
    print(f"after {mach.steps} steps: eip {mach.eip:#x} "
          f"paging {'on' if mach.cr[0] >> 31 else 'off'} "
          f"cr3 {mach.cr[3]:#x} high half reached: {high}")
    print("serial:", repr(mach.serial_text()[-400:]))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
