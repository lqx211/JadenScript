#!/usr/bin/env python3
"""Build a disk this machine can boot Linux from, out of what a distro ships.

    python3 tools/mklinuxdisk.py vmlinuz-6.1.0-50-686 -o build/linux.img
    python3 tools/mklinuxdisk.py linux-image-..._i386.deb -o build/linux.img
    python3 tools/i386_disk.py build/linux.img      # and serve it

The image is what a PC would call a hard disk with no partition table: the
loader in sector 0 does not read one, because it knows where it put things.

    LBA 0            linuxdisk.bin   -- reads the stage 2 and jumps
    LBA 1 .. 8       linuxdisk2.bin  -- E820, the zero page, protected mode,
                                        the ATA reads, every PT_LOAD segment
    LBA 9 ..         the kernel      -- an uncompressed vmlinux

The stage 2 has two numbers patched into it (found by the 'KSDL' marker at its
end) so the loader never has to assume how big a kernel is or where it starts.

`make linuxdisk KERNEL=...` runs this; `make i386-extras` builds the two .bin
files it needs.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import mkvmlinux  # noqa: E402
import mklinuxiso  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
I386 = ROOT / "userspace" / "i386"
INIT = I386 / "linuxinit.bin"

SECTOR = 512
STAGE2_SECTORS = 8          # what the boot sector reads, and what we pad to
KERNEL_LBA = 1 + STAGE2_SECTORS
INITRD_ADDR = 0x0400_0000   # 64 MB: past the kernel's own decompression area
MARKER = b"KSDL"


def load_loader(boot: Path, stage2: Path) -> tuple[bytes, bytes]:
    """The two halves of the loader, checked for the shapes the disk needs."""
    for path in (boot, stage2):
        if not path.exists():
            raise SystemExit(
                f"{path} is missing: run `make i386-extras` to build the loader"
            )
    first = boot.read_bytes()
    second = stage2.read_bytes()
    if len(first) != SECTOR:
        raise SystemExit(f"{boot} is {len(first)} bytes, not one sector")
    if first[-2:] != b"\x55\xaa":
        raise SystemExit(f"{boot} does not end in the boot signature")
    if len(second) > STAGE2_SECTORS * SECTOR:
        raise SystemExit(
            f"{stage2} is {len(second)} bytes and the boot sector only loads "
            f"{STAGE2_SECTORS * SECTOR}"
        )
    if MARKER not in second:
        raise SystemExit(f"{stage2} has no {MARKER.decode()} marker to patch")
    return first, second


def initramfs(init: Path) -> bytes:
    """The `newc` archive `/init` is executed from.

    The kernel has no filesystem of its own, so the program that becomes PID 1
    has to arrive in memory beside it. This is the same archive the GRUB ISO
    carries: `/dev/console` (a Debian kernel has CONFIG_DEVTMPFS_MOUNT unset,
    so nothing else creates it) and `/init` as a static ELF.
    """
    return mklinuxiso.cpio([
        ("dev", b"", 0o040755, 0, 0),
        ("dev/console", b"", 0o020600, 5, 1),
        ("dev/null", b"", 0o020666, 1, 3),
        ("init", mklinuxiso.elf32(init.read_bytes()), 0o100755, 0, 0),
    ])


def stage(kernel: bytes, boot: bytes, stage2: bytes, enter: int,
          offset: int = 0, initrd: bytes = b"") -> bytes:
    """The three pieces, laid out in the order the loader expects.

    `kernel` is the whole bzImage: the loader reads its first sector to copy the
    setup header into `boot_params`, and the protected-mode part from `offset`
    sectors in.
    """
    if kernel[0x1FE:0x200] != b"\x55\xaa" or kernel[0x202:0x206] != b"HdrS":
        # a payload that is not a bzImage -- a stub, in the tests -- still has
        # to carry the header the loader copies: without it the guest is handed
        # zeros where 'HdrS' and code32_start belong, and a kernel that checks
        # its own boot_params reports that it was not booted at all
        # room for the whole header first: assigning past the end of a
        # bytearray appends rather than filling, so a 120-byte stub would get
        # its 'HdrS' at offset 126
        kernel = bytearray(kernel) + bytes(max(0, 2 * SECTOR - len(kernel)))
        kernel[0x1FE:0x200] = b"\x55\xaa"      # boot_flag
        kernel[0x202:0x206] = b"HdrS"
        kernel[0x206:0x208] = b"\x0f\x02"      # protocol 2.15
        kernel[0x214:0x218] = enter.to_bytes(4, "little")
        kernel = bytes(kernel)
    at = stage2.index(MARKER)
    if at + len(MARKER) + 28 > len(stage2):
        raise SystemExit("the marker is too close to the end of the stage 2")
    sectors = (len(kernel) - offset * SECTOR + SECTOR - 1) // SECTOR
    initrd_lba = KERNEL_LBA + (len(kernel) + SECTOR - 1) // SECTOR
    initrd_sectors = (len(initrd) + SECTOR - 1) // SECTOR
    patched = bytearray(stage2)
    patched[at + 4:at + 8] = KERNEL_LBA.to_bytes(4, "little")
    patched[at + 8:at + 12] = sectors.to_bytes(4, "little")
    patched[at + 12:at + 16] = enter.to_bytes(4, "little")
    patched[at + 16:at + 20] = offset.to_bytes(4, "little")
    patched[at + 20:at + 24] = initrd_lba.to_bytes(4, "little")
    patched[at + 24:at + 28] = initrd_sectors.to_bytes(4, "little")
    patched[at + 28:at + 32] = INITRD_ADDR.to_bytes(4, "little")
    # the stage 2 occupies its whole eight sectors, because the boot sector
    # reads eight and the kernel's first LBA is a constant both sides know
    stage2_at = bytes(patched) + bytes(STAGE2_SECTORS * SECTOR - len(patched))
    # the loader reads two sectors of setup header, so even a stub is padded to
    # two: a drive asked for a sector it does not have answers with an error,
    # and the loader reports that it found no kernel at all
    pad = lambda blob: blob + bytes(max(0, 2 * SECTOR - len(blob))) + bytes(-len(blob) % SECTOR)  # noqa: E731
    return boot + stage2_at + pad(kernel) + pad(initrd)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("kernel", type=Path, help="a vmlinuz, a .deb, or a vmlinux")
    parser.add_argument("-o", "--out", type=Path, default=Path("build/linux.img"))
    parser.add_argument("--boot", type=Path, default=I386 / "linuxdisk.bin")
    parser.add_argument("--stage2", type=Path, default=I386 / "linuxdisk2.bin")
    parser.add_argument("--init", type=Path, default=I386 / "linuxinit.bin",
                        help="the flat binary the initramfs wraps as /init")
    args = parser.parse_args(argv)

    try:
        boot, stage2 = load_loader(args.boot, args.stage2)
        source = args.kernel
        if source.suffix == ".deb":
            import tempfile

            source = mkvmlinux.from_deb(source, Path(tempfile.mkdtemp()) / "vmlinuz")
        image = source.read_bytes()
        # The protected-mode part of a bzImage, from the sector after the setup
        # stub to the end of the file, is what a BIOS loader puts at
        # `code32_start`: it begins with the kernel's own decompressor, and the
        # compressed image sits behind it at the offset that decompressor was
        # built against. Handing the kernel the *decompressed* vmlinux instead
        # skips that stub, and the payload then runs at its link address with
        # no page tables -- which is a triple fault on its first %fs: access.
        start = mkvmlinux.setup_bytes(image)
        blob_kernel = image                 # the whole bzImage, setup and all
        offset = start // SECTOR
        enter = int.from_bytes(image[0x214:0x218], "little") or 0x100000
    except mkvmlinux.NotAKernel as problem:
        print(f"{args.kernel}: {problem}", file=sys.stderr)
        return 1

    archive = initramfs(INIT)
    blob = stage(blob_kernel, boot, stage2, enter, offset, archive)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_bytes(blob)

    sectors = len(blob) // SECTOR
    print(f"{args.kernel} -> {args.out}")
    print(f"  {sectors} sectors, {len(blob) >> 20} MB")
    print(f"  loader      lba 0 ({len(boot)} bytes) + lba 1..{STAGE2_SECTORS}"
          f" ({len(stage2)} bytes)")
    image_sectors = (len(blob_kernel) + SECTOR - 1) // SECTOR
    print(f"  kernel      lba {KERNEL_LBA} .. {KERNEL_LBA + image_sectors - 1}"
          f" ({image_sectors} sectors; setup {offset}, loaded at {enter:#x})")
    print(f"  initramfs   lba {KERNEL_LBA + image_sectors} .. "
          f"{(len(blob) // SECTOR) - 1} ({len(archive):,} bytes at {INITRD_ADDR:#x})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
