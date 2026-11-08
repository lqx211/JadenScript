#!/usr/bin/env python3
"""Turn a Linux kernel package into the file this machine can actually boot.

    python3 tools/mkvmlinux.py vmlinuz-6.1.0-50-686
    python3 tools/mkvmlinux.py linux-image-6.1.0-50-686_6.1.176-1_i386.deb
    python3 tools/mkvmlinux.py vmlinuz --json

A distro ships a `bzImage`: a real-mode setup stub, then a *compressed* payload
that decodes itself into the kernel once something jumps into it. Booting that
means making the guest run a decompressor over five megabytes -- tens of
millions of instructions before Linux has printed a single character, which is
most of a day at the speed of a guest that runs inside Scratch.

So the decompression happens here instead, on the host, where it costs a
second. What comes out is what a bootloader would have produced in memory:

    vmlinux.elf     an ELF32 executable, one LOAD segment per piece of the
                    kernel, each with the physical address it must be loaded at
    a report        the load addresses, the memory the kernel needs, and the
                    fields of `boot_params` a loader has to fill in for it

The report is the useful half. A Debian i686 kernel, measured:

    entry         0x0100_0000      and the segments are linked *there*, not at
    first segment 0x0100_0000      1 MB -- `CONFIG_PHYSICAL_START` is 16 MB on
    last byte     0x0200_c000      this build, so 1 MB of RAM is not enough and
    memory        >= 64 MB         32 MB is the floor
"""

from __future__ import annotations

import argparse
import io
import json
import struct
import sys
import tarfile
import tempfile
import zlib
from pathlib import Path

SETUP_SECTS = 0x1F1
BOOT_FLAG = 0x1FE
HEADER = 0x202
VERSION = 0x206
KERNEL_ALIGNMENT = 0x230
INIT_SIZE = 0x260

# what the payload of a bzImage can be, and how to open each one
MAGIC = (
    (b"\x1f\x8b\x08", "gzip"),
    (b"\xfd7zXZ\x00", "xz"),
    (b"\x28\xb5\x2f\xfd", "zstd"),
    (b"BZh", "bzip2"),
    (b"\x02\x21\x4c\x18", "lz4"),
)
HINTS = {
    "zstd": "zstd -d < payload > vmlinux",
    "bzip2": "bzip2 -d < payload > vmlinux",
    "lz4": "lz4 -d < payload > vmlinux",
}


class NotAKernel(Exception):
    """The file is not a kernel this tool understands, and the message says
    what it is instead."""


def setup_bytes(image: bytes) -> int:
    """Where the compressed payload starts: the setup stub is counted in
    sectors of 512 bytes, and a zero there means the old four-sector default."""
    if len(image) < 0x210:
        raise NotAKernel("too short to hold a boot sector and a setup header")
    if struct.unpack_from("<H", image, BOOT_FLAG)[0] != 0xAA55:
        raise NotAKernel("not a bzImage: no 0xAA55 where the boot flag belongs")
    if image[HEADER:HEADER + 4] != b"HdrS":
        raise NotAKernel("not a bzImage: no 'HdrS' header")
    sectors = image[SETUP_SECTS] or 4
    return (sectors + 1) * 512


def payload(image: bytes) -> tuple[bytes, str]:
    """The compressed stream out of a bzImage, and what compressed it.

    The stream starts somewhere after the setup stub and runs to the end of the
    file; there is no length in the header, which is why the kernels' own
    decompressor just keeps reading until it is done.
    """
    if image[0:4] == b"\x7fELF":
        return image, "none"
    start = setup_bytes(image)
    for magic, name in MAGIC:
        at = image.find(magic, start)
        if at >= 0:
            return image[at:], name
    raise NotAKernel(
        "the payload is not in a format this tool can open -- "
        "it is not gzip, xz, zstd, bzip2 or lz4"
    )


def decompress(stream: bytes, kind: str) -> bytes:
    if kind == "none":
        return stream
    if kind == "xz":
        import lzma

        # one stream, and then whatever padding the image carries
        return lzma.LZMADecompressor(lzma.FORMAT_XZ).decompress(stream)
    if kind == "gzip":
        out = bytearray()
        rest = stream
        while rest[:2] == b"\x1f\x8b":
            engine = zlib.decompressobj(16 + zlib.MAX_WBITS)
            out += engine.decompress(rest)
            out += engine.flush()
            rest = engine.unused_data
        return bytes(out)
    raise NotAKernel(
        f"the payload is {kind}-compressed, which has no decompressor here: "
        f"{HINTS.get(kind, 'decompress it with the same tool')}"
    )


def segments(elf: bytes) -> list[dict]:
    """The program headers that say where each part of the kernel goes.

    Only PT_LOAD matters: the note segment is metadata for a crash dump tool,
    and nothing loads it.
    """
    if elf[0:4] != b"\x7fELF":
        raise NotAKernel("the payload did not decompress into an ELF image")
    if elf[4] != 1 or elf[5] != 1:
        raise NotAKernel("not a 32-bit little-endian ELF")
    machine = struct.unpack_from("<H", elf, 18)[0]
    if machine != 3:
        raise NotAKernel(f"not an i386 ELF (e_machine {machine})")
    entry = struct.unpack_from("<I", elf, 24)[0]
    phoff = struct.unpack_from("<I", elf, 28)[0]
    entsize, count = struct.unpack_from("<HH", elf, 42)
    out = []
    for i in range(count):
        at = phoff + i * entsize
        kind, offset, vaddr, paddr, filesz, memsz, flags, align = struct.unpack_from(
            "<8I", elf, at
        )
        if kind != 1:
            continue
        out.append(
            {
                "offset": offset,
                "vaddr": vaddr,
                "paddr": paddr,
                "filesz": filesz,
                "memsz": memsz,
                "flags": flags,
                "align": align,
            }
        )
    return {"entry": entry, "loads": out}


def report(elf: bytes, image: bytes | None = None) -> dict:
    """Everything a loader has to satisfy, and the one number that decides
    whether it can: the highest byte the kernel writes to."""
    found = segments(elf)
    loads = found["loads"]
    if not loads:
        raise NotAKernel("the kernel has no PT_LOAD segments")
    top = max(s["paddr"] + s["memsz"] for s in loads)
    base = min(s["paddr"] for s in loads)
    facts = {
        "entry": found["entry"],
        "base": base,
        "footprint_end": top,
        "ram_needed": 1 << (top - 1).bit_length() if top > 1 else 0x100000,
        "segments": loads,
    }
    if image is not None:
        facts["protocol"] = struct.unpack_from("<H", image, VERSION)[0]
        facts["kernel_alignment"] = struct.unpack_from("<I", image, KERNEL_ALIGNMENT)[0]
        facts["init_size"] = struct.unpack_from("<I", image, INIT_SIZE)[0]
        facts["setup_bytes"] = setup_bytes(image)
    return facts


def describe(facts: dict) -> str:
    lines = [
        f"entry          {facts['entry']:#010x}",
        f"load at        {facts['base']:#010x}",
        f"last byte      {facts['footprint_end']:#010x}",
        f"RAM to give it {facts['ram_needed'] >> 20} MB at least",
    ]
    if "protocol" in facts:
        lines += [
            f"protocol       {facts['protocol'] >> 8}.{facts['protocol'] & 0xFF}",
            f"setup stub     {facts['setup_bytes']} bytes ({facts['setup_bytes'] // 512} sectors)",
            f"alignment      {facts['kernel_alignment']:#x}",
            f"init_size      {facts['init_size']:#x} (what a decompressor would need)",
        ]
    for s in facts["segments"]:
        lines.append(
            f"  file {s['offset']:#010x} -> {s['paddr']:#010x}, "
            f"{s['filesz']:#x} bytes on disk, {s['memsz']:#x} in memory"
        )
    return "\n".join(lines)


def from_deb(deb: Path, into: Path) -> Path:
    """The kernel out of a Debian package: `data.tar.*` holds /boot/vmlinuz-*."""
    data = deb.read_bytes()
    at = 8
    found = None
    while at + 60 <= len(data):
        name = data[at:at + 16].rstrip(b" \0").decode("latin-1")
        size = int(data[at + 48:at + 58].decode("latin-1").strip() or 0)
        body = data[at + 60:at + 60 + size]
        if name.startswith("data.tar"):
            found = body
            break
        at += 60 + size + (size & 1)
    if found is None:
        raise NotAKernel(f"{deb.name} has no data.tar in it")
    with tarfile.open(fileobj=io.BytesIO(found)) as tar:
        for member in tar.getmembers():
            if member.name.startswith("./boot/vmlinuz") or member.name.startswith("boot/vmlinuz"):
                source = tar.extractfile(member)
                if source is None:
                    continue
                into.write_bytes(source.read())
                return into
    raise NotAKernel(f"{deb.name} has no /boot/vmlinuz in it")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("kernel", type=Path, help="a vmlinuz, a .deb, or a vmlinux")
    parser.add_argument("-o", "--out", type=Path, help="where to write the ELF")
    parser.add_argument("--json", action="store_true", help="print the report as JSON")
    args = parser.parse_args(argv)

    temporary = None
    try:
        source = args.kernel
        if source.suffix == ".deb":
            temporary = Path(tempfile.mkdtemp()) / "vmlinuz"
            source = from_deb(source, temporary)
        image = source.read_bytes()
        stream, kind = payload(image)
        elf = decompress(stream, kind)
        header_ok = True
        try:
            setup_bytes(image)
        except NotAKernel:
            header_ok = False
        facts = report(elf, image if header_ok else None)
    except NotAKernel as problem:
        print(f"{args.kernel}: {problem}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(facts, indent=2))
    else:
        print(f"{args.kernel} -> {kind if kind != 'none' else 'already an ELF'}")
        print(describe(facts))
    if args.out:
        args.out.write_bytes(elf)
        print(f"wrote {args.out} ({len(elf)} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
