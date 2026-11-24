#!/usr/bin/env python3
"""Build a GRUB image that boots Linux on this machine.

    python3 tools/mklinuxiso.py build/i386/linux-image.deb -o build/i386/linux.iso

The GRUB rescue ISO that `docs/I386.md` describes boots here -- menu, keyboard
and all -- but it carries no kernel, so the menu's `linux` entry cannot find
one. This puts one inside it:

    /boot/vmlinuz-...    the distro's bzImage, straight out of the package. The
                         *guest* decompresses it, which is the real thing: the
                         kernel runs its own xz decoder in 32-bit protected mode
                         under this machine's paging.
    /boot/initramfs.cpio a newc archive holding `/init` -- a small static ELF
                         this project assembles -- and `/dev/console`. The
                         kernel needs the node because `CONFIG_DEVTMPFS_MOUNT`
                         is not set on a Debian build.
    /boot/grub/grub.cfg  one menu entry, five-second timeout, serial console.

The image stays a **hybrid**: an El Torito ISO whose sector 0 is also a GRUB
MBR. It has to, because this machine's BIOS boots a hard disk -- it reads LBA 0
and jumps -- and has no CD support at all.

That means the ISO cannot be rebuilt by `xorriso -as mkisofs` alone: the boot
images have to come out of the GRUB ISO that already works, which is why this
script extracts them from `--grub` and feeds them back to the same command
`grub-mkrescue` would have used.
"""

from __future__ import annotations

import argparse
import shutil
import struct
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
I386 = ROOT / "userspace" / "i386"

#: What the kernel is told. `console=ttyS0` is the only output this machine can
#: give a kernel before it has a screen driver, and it is what the 16550 model
#: in `i386.jsw` is for.
CMDLINE = "console=ttyS0,115200 earlyprintk=serial,ttyS0 ignore_loglevel"

#: Where a static i386 executable is linked by convention, and where the kernel
#: will therefore find this one.
ELF_BASE = 0x08048000


def elf32(code: bytes, base: int = ELF_BASE) -> bytes:
    """A flat binary as an ET_EXEC the kernel's ELF loader will accept.

    There is no i386 ELF linker on a Mac, and there does not need to be: one
    PT_LOAD that covers the headers and the code, entry at the first byte of
    code, is a complete executable. The header is *inside* the segment, which is
    why the alignment is 1 -- an offset and a virtual address that disagree
    modulo the page size is the thing `dl_load` refuses.
    """
    ehsize, phentsize, phnum = 52, 32, 1
    offset = ehsize + phentsize          # code starts right after the headers
    entry = base + offset
    ident = b"\x7fELF" + bytes([1, 1, 1, 0]) + bytes(8)
    header = struct.pack(
        "<16sHHIIIIIHHHHHH",
        ident,
        2,                               # e_type: ET_EXEC
        3,                               # e_machine: EM_386
        1,                               # e_version
        entry,                           # e_entry
        ehsize,                          # e_phoff
        0,                               # e_shoff
        0,                               # e_flags
        ehsize, phentsize, phnum,
        0, 0, 0,                         # no section headers
    )
    phdr = struct.pack(
        "<8I",
        1,                               # p_type: PT_LOAD
        0,                               # p_offset: from the start of the file
        base,                            # p_vaddr
        base,                            # p_paddr
        offset + len(code),              # p_filesz
        offset + len(code),              # p_memsz
        5,                               # p_flags: R|X
        1,                               # p_align: 1, see the docstring
    )
    return header + phdr + code


def cpio(entries: list[tuple[str, bytes, int, int, int]]) -> bytes:
    """A `newc` archive: name, mode, and for a device node its major and minor.

    The format is six hex fields and a name, then the data padded to four
    bytes -- there is no compression to get wrong, and the kernel reads it
    straight out of memory where GRUB put it.
    """
    out = bytearray()
    for name, data, mode, major, minor in entries:
        name_bytes = name.encode() + b"\0"
        # the magic is the six ASCII characters "070701", not the hex of the
        # octal number 0o070701 -- writing it through the same formatter as the
        # numeric fields produced "000071c1", which no cpio reader accepts, and
        # a kernel handed that archive has no /init in it
        fields = [0, mode, 0, 0, 1, 0, len(data),
                  0, 0, major, minor, len(name_bytes), 0]
        header = b"070701" + "".join(f"{value:08x}" for value in fields).encode()
        out += header
        out += name_bytes
        out += bytes(-len(out) % 4)
        out += data
        out += bytes(-len(out) % 4)
    # the trailer: a record with no name and no data, which is how the kernel
    # knows the archive ended rather than being truncated
    out += b"070701" + "".join(f"{value:08x}" for value in
                            (0, 0, 0, 0, 1, 0, 0, 0, 0, 0, 0, 11, 0)).encode()
    out += b"TRAILER!!!\0"
    out += bytes(-len(out) % 4)
    return bytes(out)


def build(deb: Path, grub_iso: Path, out: Path, init: Path) -> None:
    sys.path.insert(0, str(ROOT / "tools"))
    import mkvmlinux

    if not init.exists():
        raise SystemExit(f"{init} is missing: run `make i386-extras`")
    if not grub_iso.exists():
        raise SystemExit(f"{grub_iso} is missing: it is the GRUB rescue image")
    if shutil.which("xorriso") is None:
        raise SystemExit("xorriso is not installed (`brew install xorriso`)")

    with tempfile.TemporaryDirectory() as tmp_name:
        tmp = Path(tmp_name)
        tree = tmp / "tree"
        # the boot images come out of the ISO that already boots here
        subprocess.run(["xorriso", "-osirrox", "on", "-indev", str(grub_iso),
                        "-extract", "/", str(tree)],
                       check=True, capture_output=True)
        # xorriso restores the permissions it finds, and an ISO's files are
        # read-only: the tree has to be writable before anything can be added.
        for path in tree.rglob("*"):
            path.chmod(0o755 if path.is_dir() else 0o644)
        tree.chmod(0o755)

        mbr = tmp / "mbr.bin"
        mbr.write_bytes(grub_iso.read_bytes()[:512])
        if mbr.read_bytes()[510:512] != b"\x55\xaa":
            raise SystemExit("the GRUB image has no boot signature to copy")

        boot = tree / "boot"
        boot.mkdir(exist_ok=True)
        if deb.suffix == ".deb":
            vmlinuz = mkvmlinux.from_deb(deb, tmp / "vmlinuz").read_bytes()
        else:
            vmlinuz = deb.read_bytes()
        if vmlinuz[0x1FE:0x200] != b"\x55\xaa" or vmlinuz[0x202:0x206] != b"HdrS":
            raise SystemExit(f"{deb.name} is not a bzImage")
        (boot / "vmlinuz").write_bytes(vmlinuz)

        initramfs = cpio([
            ("dev", b"", 0o040755, 0, 0),
            ("dev/console", b"", 0o020600, 5, 1),
            ("dev/null", b"", 0o020666, 1, 3),
            ("init", elf32(init.read_bytes()), 0o100755, 0, 0),
        ])
        (boot / "initramfs.cpio").write_bytes(initramfs)

        cfg = boot / "grub" / "grub.cfg"
        cfg.parent.mkdir(parents=True, exist_ok=True)
        cfg.write_text(f"""set timeout=5
set default=jswlinux

menuentry "Linux 6.1.0-50-686 on this machine" --id jswlinux {{
    set root=(hd0)
    linux /boot/vmlinuz {CMDLINE}
    initrd /boot/initramfs.cpio
}}

menuentry "GRUB command line" {{
    set root=(hd0)
}}
""")

        out.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run([
            "xorriso", "-as", "mkisofs", "-o", str(out),
            # -R, or the names GRUB looks up are stored as NORMAL.MOD;1 and the
            # module loader finds nothing. grub-mkrescue passes it for this
            # reason; an ISO9700 with plain ISO9660 is a GRUB that says
            # "Welcome" and then falls over.
            "-R",
            "-b", "boot/grub/i386-pc/eltorito.img",
            "-no-emul-boot", "-boot-load-size", "4", "-boot-info-table",
            "-isohybrid-mbr", str(mbr),
            str(tree),
        ], check=True, capture_output=True)

    image = bytearray(out.read_bytes())
    if image[510:512] != b"\x55\xaa":
        raise SystemExit("the image is not bootable: no 0xAA55 at 510")
    # `bi_file` in the boot-info table stays what xorriso wrote: 49, the El
    # Torito image's own 2 KiB ISO block. That is the number the *CD* path
    # needs -- diskboot is entered from the catalog entry, reads sector
    # `bi_file + 1` with 2048-byte sectors and finds the rest of the core at
    # 0x8000 -- and this firmware boots the ISO as a CD (drive 0x9F, El Torito
    # from the volume descriptors), so the hybrid MBR at sector 0 is not the
    # path anything takes. Patching it to a 512-byte sector number made the CD
    # read the wrong block, which is what "GRUB Read Error" was.
    core_lba = int.from_bytes(image[0x1B0:0x1B4], "little")
    if not 0 < core_lba < len(image) // 512:
        raise SystemExit("the boot sector does not name a core image")
    table = core_lba * 512
    bi_file = int.from_bytes(image[table + 0xC:table + 0x10], "little")
    out.write_bytes(image)
    print(f"{out}: {len(image):,} bytes, {len(image) // 512:,} sectors, "
          f"kernel {len(vmlinuz):,} bytes, initramfs {len(initramfs):,} bytes")
    print(f"  El Torito image at LBA {core_lba}, boot-info bi_file {bi_file}")
    print(f"  serve it:  python3 tools/i386_disk.py {out}")


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("kernel", type=Path,
                        help="a .deb, or a vmlinuz")
    parser.add_argument("-o", "--out", type=Path,
                        default=ROOT / "build" / "i386" / "linux.iso")
    parser.add_argument("--grub", type=Path,
                        default=ROOT / "build" / "i386" / "grub.img",
                        help="the GRUB rescue image to take the boot images from")
    parser.add_argument("--init", type=Path, default=I386 / "linuxinit.bin")
    args = parser.parse_args(argv)
    build(args.kernel, args.grub, args.out, args.init)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
