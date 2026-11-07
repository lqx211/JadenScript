"""The i386's network disk: the volume, the protocol, the panel, the failures.

`tools/i386_disk.py` is the one part of this machine that lives outside the
project, so it is the one part a Scratch test cannot reach. These tests drive it
directly -- the same functions the WebSocket handler calls -- so a change to the
protocol or the fault model fails here rather than in a boot that mysteriously
stops halfway.
"""

from __future__ import annotations

import json
import socket
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import i386_disk as disk  # noqa: E402


def _image(sectors: int = 8) -> Path:
    """A small volume in a temporary directory, filled with a pattern."""
    handle = tempfile.NamedTemporaryFile(suffix=".img", delete=False)
    handle.write(b"".join(bytes([(i + j) & 0xFF for j in range(disk.SECTOR)])
                          for i in range(sectors)))
    handle.close()
    return Path(handle.name)


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


class VolumeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.path = _image()
        self.volume = disk.Volume(self.path)
        self.volume.mount()

    def tearDown(self) -> None:
        self.volume.unmount()
        self.path.unlink(missing_ok=True)
        Path(str(self.path) + ".smart.json").unlink(missing_ok=True)

    def test_a_mounted_volume_reads_what_is_in_the_file(self) -> None:
        ok, payload = self.volume.read(1, 2)
        self.assertTrue(ok)
        blob = bytes.fromhex(payload)
        self.assertEqual(len(blob), 1024)
        self.assertEqual(blob[:4], bytes([1, 2, 3, 4]))
        self.assertEqual(self.volume.smart.reads, 2, "two sectors were served")

    def test_a_read_past_the_end_is_an_error_not_a_short_read(self) -> None:
        ok, why = self.volume.read(7, 4)
        self.assertFalse(ok)
        self.assertIn("past the end", why)
        self.assertEqual(self.volume.errors, 1)

    def test_an_unmounted_volume_answers_nothing(self) -> None:
        self.volume.unmount()
        ok, why = self.volume.read(0, 1)
        self.assertFalse(ok)
        self.assertEqual(why, "offline")

    def test_a_write_lands_in_the_file_at_once(self) -> None:
        ok, why = self.volume.write(3, b"\xA5" * disk.SECTOR)
        self.assertTrue(ok, why)
        self.assertEqual(self.path.read_bytes()[3 * disk.SECTOR], 0xA5)
        self.assertEqual(self.volume.smart.writes, 1)

    def test_a_write_fault_reallocates_the_sector(self) -> None:
        self.volume.set_faults(write_error_rate=1.0)
        ok, why = self.volume.write(0, b"\x11" * disk.SECTOR)
        self.assertFalse(ok)
        self.assertIn("write error", why)
        self.assertEqual(self.volume.smart.reallocated, 1)
        self.assertTrue(self.volume.smart.dirty, "a reallocation is not clean")

    def test_an_accident_takes_the_drive_offline_and_leaves_it_dirty(self) -> None:
        self.volume.accident()
        self.assertTrue(self.volume.faults.offline)
        self.assertTrue(self.volume.smart.dirty)
        ok, why = self.volume.read(0, 1)
        self.assertFalse(ok, "a drive that lost power does not answer")
        self.volume.mount()
        self.assertTrue(self.volume.read(0, 1)[0], "and comes back when mounted")
        self.assertTrue(self.volume.smart.dirty,
                        "the dirty bit survives the remount: it is what fsck reads")

    def test_latency_is_a_delay_and_not_a_failure(self) -> None:
        self.volume.set_faults(latency_ms=40.0)
        started = time.monotonic()
        ok, _ = self.volume.read(0, 1)
        elapsed = (time.monotonic() - started) * 1000
        self.assertTrue(ok)
        self.assertGreaterEqual(elapsed, 35.0)

    def test_progress_survives_a_new_volume_on_the_same_file(self) -> None:
        self.volume.smart.power_cycles = 3
        self.volume.write(1, b"\x5A" * disk.SECTOR)
        self.volume.progress()
        again = disk.Volume(self.path)
        again.mount()
        try:
            self.assertEqual(again.smart.power_cycles, 4, "the last mount was counted")
            self.assertEqual(again.smart.writes, 1, "the write is still in the log")
        finally:
            again.unmount()

    def test_identify_is_what_a_drive_says_about_itself(self) -> None:
        ident = self.volume.identify()
        self.assertEqual(ident["model"], disk.MODEL)
        self.assertEqual(ident["sectors"], 8)
        self.assertEqual(ident["bytes"], 8 * disk.SECTOR)


class ProtocolTests(unittest.TestCase):
    def setUp(self) -> None:
        self.path = _image()
        self.volume = disk.Volume(self.path)
        self.volume.mount()

    def tearDown(self) -> None:
        self.volume.unmount()
        self.path.unlink(missing_ok=True)
        Path(str(self.path) + ".smart.json").unlink(missing_ok=True)

    def test_read_and_write_round_trip(self) -> None:
        self.assertTrue(disk.handle(self.volume, "W 0 " + "AB" * 512).startswith("W ok"))
        reply = disk.handle(self.volume, "R 0 1")
        self.assertTrue(reply.startswith("D "))
        self.assertEqual(bytes.fromhex(reply[2:])[:2], b"\xAB\xAB")

    def test_the_word_write_is_little_endian(self) -> None:
        """`WW` is the form the machine sends: its disk is a list of signed
        32-bit words, and the drive's byte order is the guest's."""
        self.assertEqual(disk.handle(self.volume, "WW 2 305419896 -1"), "W ok")
        blob = self.path.read_bytes()[2 * disk.SECTOR:2 * disk.SECTOR + 8]
        self.assertEqual(blob, bytes.fromhex("78563412ffffffff"))

    def test_the_line_the_machine_sends_is_one_the_drive_accepts(self) -> None:
        """The two halves of this protocol live in different languages, so the
        one thing that has to be true is that a line the machine builds is a line
        the drive parses. A Scratch number prints as a double (`4.0`), which is
        why the drive reads its numbers back through a float."""
        words = [0xEEFBEEFB] * 128
        line = "WW 4.0 " + " ".join(f"{word}.0" for word in words)
        self.assertEqual(disk.handle(self.volume, line), "W ok")
        blob = self.path.read_bytes()[4 * disk.SECTOR:4 * disk.SECTOR + 8]
        self.assertEqual(blob, bytes.fromhex("fbee fbee fbee fbee".replace(" ", "")))

    def test_a_hint_counts_a_read_without_sending_the_data(self) -> None:
        self.assertEqual(disk.handle(self.volume, "H 1 3"), "H ok")
        self.assertEqual(self.volume.smart.reads, 3)
        self.assertEqual(self.volume.served, 1, "one request served")

    def test_the_clock_is_microseconds(self) -> None:
        reply = disk.handle(self.volume, "T")
        self.assertTrue(reply.startswith("T "))
        self.assertAlmostEqual(int(reply[2:]), time.time_ns() // 1000, delta=5_000_000)

    def test_a_bad_request_says_why(self) -> None:
        self.assertTrue(disk.handle(self.volume, "").startswith("E "))
        self.assertIn("sector", disk.handle(self.volume, "R"))
        self.assertIn("numbers", disk.handle(self.volume, "R x y"))
        self.assertIn("unknown", disk.handle(self.volume, "Q 1 2"))

    def test_an_offline_drive_answers_every_request_with_an_error(self) -> None:
        self.volume.set_faults(offline=True)
        self.assertEqual(disk.handle(self.volume, "R 0 1"), "E offline")
        self.assertEqual(disk.handle(self.volume, "WW 0 1 2"), "W offline")
        self.assertEqual(disk.handle(self.volume, "H 0 1"), "H ok",
                         "a hint is a log entry, not a transfer")


class PanelTests(unittest.TestCase):
    """The panel is a page and an API, and the API is what the page uses."""

    def setUp(self) -> None:
        self.path = _image()
        self.volume = disk.Volume(self.path)
        self.volume.mount()
        self.port = _free_port()
        disk.start_http(self.volume, self.port)
        time.sleep(0.15)

    def tearDown(self) -> None:
        self.volume.unmount()
        self.path.unlink(missing_ok=True)
        Path(str(self.path) + ".smart.json").unlink(missing_ok=True)

    def _get(self, path: str) -> tuple[int, str]:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{self.port}{path}",
                                        timeout=5) as answer:
                return answer.status, answer.read().decode()
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read().decode()

    def _post(self, path: str, body: dict) -> dict:
        request = urllib.request.Request(
            f"http://127.0.0.1:{self.port}{path}",
            data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(request, timeout=5) as answer:
            return json.loads(answer.read())

    def test_the_panel_is_served_at_the_root(self) -> None:
        code, body = self._get("/")
        self.assertEqual(code, 200)
        self.assertIn("<title>i386 disk</title>", body)
        for control in ("Accident", "Take offline", "write error rate", "SMART"):
            self.assertIn(control, body)

    def test_status_carries_everything_the_panel_draws(self) -> None:
        code, body = self._get("/api/status")
        self.assertEqual(code, 200)
        status = json.loads(body)
        for key in ("model", "path", "sectors", "clients", "served", "errors",
                    "faults", "smart"):
            self.assertIn(key, status)
        self.assertEqual(status["sectors"], 8)

    def test_a_slider_in_the_panel_is_a_post_to_the_api(self) -> None:
        status = self._post("/api/faults", {"write_error_rate": 0.5})
        self.assertEqual(status["faults"]["write_error_rate"], 0.5)
        self.assertEqual(self.volume.faults.write_error_rate, 0.5)

    def test_the_accident_button_is_an_accident(self) -> None:
        status = self._post("/api/accident", {})
        self.assertTrue(status["faults"]["offline"])
        self.assertTrue(status["smart"]["dirty"])

    def test_unmount_and_mount_from_the_panel(self) -> None:
        self.assertFalse(self._post("/api/unmount", {})["mounted"])
        self.assertEqual(disk.handle(self.volume, "R 0 1"), "E offline")
        self.assertTrue(self._post("/api/mount", {})["mounted"])
        self.assertTrue(disk.handle(self.volume, "R 0 1").startswith("D "))

    def test_an_unknown_route_is_a_404_with_a_reason(self) -> None:
        code, body = self._get("/api/nothing")
        self.assertEqual(code, 404)
        self.assertIn("no such thing", body)


if __name__ == "__main__":
    unittest.main()
