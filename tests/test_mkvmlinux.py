"""`tools/mkvmlinux.py`: what a distro ships, in the shape a loader can use.

The useful output is the report -- where the kernel is linked, how much memory
it needs, which protocol version it speaks -- because those are the numbers a
loader has to be written against, and they are not the ones people assume. A
Debian i686 kernel is linked at 16 MB, not at 1 MB, and it wants 32 MB of RAM.
"""

import gzip
import io
import lzma
import struct
import sys
import tarfile
import tempfile
import unittest

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import mkvmlinux  # noqa: E402


def an_elf(*segments, entry=0x0100_0000) -> bytes:
    """A 32-bit i386 ELF whose program headers load `segments`.

    Each segment is (paddr, filesz, memsz); the file body starts at 0x1000 so
    the offsets in the headers are not zero, which is what makes `payload`'s
    idea of skipping the setup stub testable.
    """
    body = bytes(0x1000)
    headers = b""
    for paddr, filesz, memsz in segments:
        payload_at = len(body)
        body += bytes([0x90]) * filesz
        headers += struct.pack("<8I", 1, payload_at, 0xC000_0000 + paddr,
                               paddr, filesz, memsz, 5, 0x1000)
    phoff = 52
    ident = b"\x7fELF" + bytes([1, 1, 1]) + bytes(9)
    header = ident + struct.pack("<HHIIIIIHHHHHH", 2, 3, 1, entry, phoff, 0,
                                 0, 52, 32, len(segments), 40, 0, 0)
    return header + headers + body[len(header) + len(headers):]


def a_bzimage(payload: bytes, sectors: int = 39) -> bytes:
    """A bzImage-shaped file: a setup stub of `sectors`, then the payload."""
    setup = bytearray((sectors + 1) * 512)
    setup[0:2] = b"MZ"
    struct.pack_into("<H", setup, mkvmlinux.BOOT_FLAG, 0xAA55)
    setup[mkvmlinux.HEADER:mkvmlinux.HEADER + 4] = b"HdrS"
    struct.pack_into("<H", setup, mkvmlinux.VERSION, 0x020F)
    setup[mkvmlinux.SETUP_SECTS] = sectors
    struct.pack_into("<I", setup, mkvmlinux.KERNEL_ALIGNMENT, 0x0100_0000)
    struct.pack_into("<I", setup, mkvmlinux.INIT_SIZE, 0x0100_C000)
    return bytes(setup) + payload


def a_deb(vmlinuz: bytes) -> bytes:
    """A Debian package, as far as its header goes: an `ar` archive with
    control.tar and data.tar, the second holding /boot/vmlinuz."""
    def ar(name: str, body: bytes) -> bytes:
        entry = f"{name:<16}{0:<12}{0:<6}{0:<6}{100644:<8}{len(body):<10}".encode()
        return entry + b"`\n" + body + (b"\n" if len(body) % 2 else b"")

    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as tar:
        info = tarfile.TarInfo("./boot/vmlinuz-6.1.0-50-686")
        info.size = len(vmlinuz)
        tar.addfile(info, io.BytesIO(vmlinuz))
    return b"!<arch>\n" + ar("debian-binary", b"2.0\n") + ar("data.tar", buffer.getvalue())


class PayloadTests(unittest.TestCase):
    def test_the_setup_stub_is_counted_in_sectors(self) -> None:
        image = a_bzimage(b"\x1f\x8b\x08rest")
        self.assertEqual(mkvmlinux.setup_bytes(image), 40 * 512)

    def test_a_zero_means_the_old_four_sector_default(self) -> None:
        image = bytearray(a_bzimage(b""))
        image[mkvmlinux.SETUP_SECTS] = 0
        self.assertEqual(mkvmlinux.setup_bytes(bytes(image)), 5 * 512)

    def test_a_file_that_is_not_a_kernel_says_so(self) -> None:
        with self.assertRaises(mkvmlinux.NotAKernel) as caught:
            mkvmlinux.payload(b"just some text, nothing like a boot sector" * 20)
        self.assertIn("0xAA55", str(caught.exception))

    def test_an_elf_is_already_what_a_loader_wants(self) -> None:
        elf = an_elf((0x0100_0000, 16, 16))
        stream, kind = mkvmlinux.payload(elf)
        self.assertEqual(kind, "none")
        self.assertEqual(mkvmlinux.decompress(stream, kind), elf)

    def test_the_payload_is_found_after_the_stub(self) -> None:
        body = gzip.compress(b"x" * 64)
        stream, kind = mkvmlinux.payload(a_bzimage(body))
        self.assertEqual(kind, "gzip")
        self.assertEqual(mkvmlinux.decompress(stream, kind), b"x" * 64)

    def test_an_xz_payload_is_opened_without_a_helper_program(self) -> None:
        body = lzma.compress(b"y" * 64, format=lzma.FORMAT_XZ)
        stream, kind = mkvmlinux.payload(a_bzimage(body))
        self.assertEqual(kind, "xz")
        self.assertEqual(mkvmlinux.decompress(stream, kind), b"y" * 64)

    def test_a_compressor_we_do_not_have_says_what_to_run(self) -> None:
        stream, kind = mkvmlinux.payload(a_bzimage(b"\x28\xb5\x2f\xfd" + bytes(64)))
        self.assertEqual(kind, "zstd")
        with self.assertRaises(mkvmlinux.NotAKernel) as caught:
            mkvmlinux.decompress(stream, kind)
        self.assertIn("zstd -d", str(caught.exception))


class ReportTests(unittest.TestCase):
    def test_the_kernel_is_linked_where_its_segments_say(self) -> None:
        """Not at 1 MB. Debian's i686 kernels are built with
        CONFIG_PHYSICAL_START = 16 MB, and a loader that assumes otherwise
        jumps at zeroes."""
        elf = an_elf((0x0100_0000, 0x1000, 0x1000), (0x01C3_9000, 0x1000, 0x2_0000))
        facts = mkvmlinux.report(elf)
        self.assertEqual(facts["base"], 0x0100_0000)
        self.assertEqual(facts["entry"], 0x0100_0000)
        self.assertEqual(facts["footprint_end"], 0x01C5_9000)
        self.assertEqual(facts["ram_needed"], 0x0200_0000, "32 MB, rounded up")
        self.assertEqual(len(facts["segments"]), 2)

    def test_the_header_says_which_protocol_and_how_much_it_needs(self) -> None:
        image = a_bzimage(b"\x1f\x8b\x08")
        facts = mkvmlinux.report(an_elf((0x0100_0000, 16, 16)), image)
        self.assertEqual(facts["protocol"], 0x020F)
        self.assertEqual(facts["setup_bytes"], 40 * 512)
        self.assertEqual(facts["init_size"], 0x0100_C000)

    def test_a_payload_that_is_not_an_elf_is_refused(self) -> None:
        with self.assertRaises(mkvmlinux.NotAKernel):
            mkvmlinux.report(b"\x7fELF" + bytes(200))


class PackageTests(unittest.TestCase):
    def test_the_kernel_comes_out_of_a_debian_package(self) -> None:
        elf = an_elf((0x0100_0000, 16, 16))
        with tempfile.TemporaryDirectory() as tmp:
            deb = Path(tmp) / "linux-image.deb"
            deb.write_bytes(a_deb(a_bzimage(gzip.compress(elf))))
            out = Path(tmp) / "vmlinuz"
            self.assertEqual(mkvmlinux.from_deb(deb, out), out)
            stream, kind = mkvmlinux.payload(out.read_bytes())
            self.assertEqual(mkvmlinux.decompress(stream, kind), elf)

    def test_the_command_line_writes_the_elf_and_prints_the_map(self) -> None:
        elf = an_elf((0x0100_0000, 16, 16))
        with tempfile.TemporaryDirectory() as tmp:
            kernel = Path(tmp) / "vmlinuz"
            kernel.write_bytes(a_bzimage(lzma.compress(elf, format=lzma.FORMAT_XZ)))
            out = Path(tmp) / "vmlinux.elf"
            self.assertEqual(mkvmlinux.main([str(kernel), "-o", str(out)]), 0)
            self.assertEqual(out.read_bytes(), elf)

    def test_a_file_it_cannot_read_is_an_error_not_a_traceback(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            kernel = Path(tmp) / "vmlinuz"
            kernel.write_bytes(b"nope" * 200)
            self.assertEqual(mkvmlinux.main([str(kernel)]), 1)


if __name__ == "__main__":
    unittest.main()
