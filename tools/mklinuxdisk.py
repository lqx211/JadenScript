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

ROOT = Path(__file__).resolve().parents[1]
I386 = ROOT / "userspace" / "i386"

SECTOR = 512
STAGE2_SECTORS = 8          # what the boot sector reads, and what we pad to
KERNEL_LBA = 1 + STAGE2_SECTORS
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


def stage(kernel: bytes, boot: bytes, stage2: bytes) -> bytes:
    """The three pieces, laid out in the order the loader expects."""
    at = stage2.index(MARKER)
    if at + len(MARKER) + 8 > len(stage2):
        raise SystemExit("the marker is too close to the end of the stage 2")
    sectors = (len(kernel) + SECTOR - 1) // SECTOR
    patched = bytearray(stage2)
    patched[at + 4:at + 8] = KERNEL_LBA.to_bytes(4, "little")
    patched[at + 8:at + 12] = sectors.to_bytes(4, "little")
    # the stage 2 occupies its whole eight sectors, because the boot sector
    # reads eight and the kernel's first LBA is a constant both sides know
    stage2_at = bytes(patched) + bytes(STAGE2_SECTORS * SECTOR - len(patched))
    pad = lambda blob: blob + bytes(-len(blob) % SECTOR)  # noqa: E731
    return boot + stage2_at + pad(kernel)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("kernel", type=Path, help="a vmlinuz, a .deb, or a vmlinux")
    parser.add_argument("-o", "--out", type=Path, default=Path("build/linux.img"))
    parser.add_argument("--boot", type=Path, default=I386 / "linuxdisk.bin")
    parser.add_argument("--stage2", type=Path, default=I386 / "linuxdisk2.bin")
    args = parser.parse_args(argv)

    try:
        boot, stage2 = load_loader(args.boot, args.stage2)
        source = args.kernel
        if source.suffix == ".deb":
            import tempfile

            source = mkvmlinux.from_deb(source, Path(tempfile.mkdtemp()) / "vmlinuz")
        image = source.read_bytes()
        stream, kind = mkvmlinux.payload(image)
        elf = mkvmlinux.decompress(stream, kind)
        facts = mkvmlinux.report(elf, image if image[0:2] == b"MZ" else None)
    except mkvmlinux.NotAKernel as problem:
        print(f"{args.kernel}: {problem}", file=sys.stderr)
        return 1

    blob = stage(elf, boot, stage2)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_bytes(blob)

    sectors = len(blob) // SECTOR
    print(f"{args.kernel} -> {args.out}")
    print(f"  {sectors} sectors, {len(blob) >> 20} MB")
    print(f"  loader      lba 0 ({len(boot)} bytes) + lba 1..{STAGE2_SECTORS}"
          f" ({len(stage2)} bytes)")
    kernel_sectors = (len(elf) + SECTOR - 1) // SECTOR
    print(f"  kernel      lba {KERNEL_LBA} .. {KERNEL_LBA + kernel_sectors - 1}"
          f" ({kernel_sectors} sectors)")
    print(mkvmlinux.describe(facts))
    return 0


if __name__ == "__main__":
    sys.exit(main())
