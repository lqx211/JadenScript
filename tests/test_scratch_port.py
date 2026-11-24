"""The two tools that answer "can Scratch take this project?".

`scratch_audit` says why it cannot; `scratch_slim` makes the part of it that is
just a format problem go away. Both are about a *file*, so the tests build
small projects by hand rather than compiling something: the interesting cases
are the fields a real project gets wrong, and a hand-built one can be wrong on
purpose.
"""

from __future__ import annotations

import json
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import scratch_audit  # noqa: E402
import scratch_slim  # noqa: E402


def wav_bytes(seconds: float = 0.1, rate: int = 44100) -> bytes:
    """A real WAV: 16-bit stereo silence, with the chunks ffmpeg insists on."""
    frames = int(rate * seconds)
    data = b"\x00\x00" * 2 * frames
    fmt = struct.pack("<HHIIHH", 1, 2, rate, rate * 4, 4, 16)
    body = b"WAVE" + b"fmt " + struct.pack("<I", len(fmt)) + fmt
    body += b"data" + struct.pack("<I", len(data)) + data
    return b"RIFF" + struct.pack("<I", len(body)) + body


def project(opcodes: list[str], sounds: int = 0, extension_urls: dict | None = None,
            extensions: list[str] | None = None) -> dict:
    blocks = {}
    for i, opcode in enumerate(opcodes):
        blocks[f"b{i}"] = {
            "opcode": opcode, "next": None, "parent": None, "inputs": {},
            "fields": {}, "shadow": False, "topLevel": True, "x": 0, "y": 0,
        }
    stage = {
        "isStage": True, "name": "Stage", "variables": {}, "lists": {},
        "broadcasts": {}, "blocks": blocks, "comments": {},
        "currentCostume": 0, "costumes": [], "sounds": [], "volume": 100,
        "layerOrder": 0, "tempo": 60, "videoTransparency": 50,
        "videoState": "on", "textToSpeechLanguage": None,
    }
    data = {
        "targets": [stage], "monitors": [], "extensions": extensions or [],
        "meta": {"semver": "3.0.0", "vm": "0.2.0", "agent": ""},
    }
    if extension_urls is not None:
        data["extensionURLs"] = extension_urls
    return data


def write_sb3(path: Path, data: dict, assets: dict[str, bytes] | None = None) -> None:
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("project.json", json.dumps(data))
        for name, blob in (assets or {}).items():
            archive.writestr(name, blob)


class AuditTests(unittest.TestCase):
    VM = ROOT / "scratch-vm-develop"

    def audit(self, data: dict) -> dict:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "p.sb3"
            write_sb3(path, data)
            return scratch_audit.audit(
                path, self.VM if (self.VM / "src").is_dir() else None)

    def test_core_blocks_pass_cleanly(self) -> None:
        result = self.audit(project([
            "event_whenflagclicked", "control_forever", "control_repeat_until",
            "operator_add", "data_setvariableto", "looks_sayforsecs",
        ]))
        self.assertEqual(result["blockers"], {})
        self.assertEqual(result["foreign_extensions"], [])

    def test_a_turbowarp_block_is_caught_even_though_its_prefix_is_vanilla(self) -> None:
        """`control_while` looks like a built-in block and is not one.

        That is the whole reason the audit exists: the prefix matches a
        namespace Scratch ships, so nothing about the opcode's shape says it
        will not run.
        """
        result = self.audit(project(["control_while", "control_for_each"]))
        self.assertEqual(result["blockers"]["control_while"], 1)
        self.assertEqual(result["blockers"]["control_for_each"], 1)

    def test_a_renamed_stage_is_reported(self) -> None:
        """``targets[0].name`` is pinned to "Stage" by the sb3 schema.

        scratch.mit.edu answers "Could not parse as a valid SB2 or SB3 project"
        and never says why, which is how obfuscation used to break a build.
        """
        data = project(["event_whenflagclicked"])
        data["targets"][0]["name"] = "zqJ57E"
        result = self.audit(data)
        self.assertTrue(result["stage_problems"], result["stage_problems"])
        self.assertIn("'Stage'", " ".join(result["stage_problems"]))
        self.assertIn("Scratch would reject this", scratch_audit.report(result))

    def test_the_stage_of_a_normal_project_is_not_reported(self) -> None:
        result = self.audit(project(["event_whenflagclicked"]))
        self.assertEqual(result["stage_problems"], [])

    def test_shadow_menu_blocks_are_not_reported(self) -> None:
        """`note`, `looks_costume` and `procedures_prototype` are VM-built.

        They appear in project.json but are declared nowhere as an `opcode:`,
        so an audit that only reads block files calls every custom block in the
        project foreign.
        """
        result = self.audit(project([
            "procedures_prototype", "note", "looks_costume",
            "sensing_keyoptions", "music_menu_INSTRUMENT", "pen_clear",
        ]))
        self.assertEqual(result["blockers"], {})

    def test_an_extension_block_and_its_url_are_both_reported(self) -> None:
        result = self.audit(project(
            ["lmsTempVars2_getThreadVariable", "operator_add"],
            extensions=["lmsTempVars2", "pen"],
            extension_urls={"lmsTempVars2": "https://example.invalid/tv.js"},
        ))
        self.assertEqual(result["foreign_extensions"], ["lmsTempVars2"])
        self.assertIn("lmsTempVars2", result["extension_urls"])
        self.assertEqual(result["blockers"]["lmsTempVars2_getThreadVariable"], 1)

    def test_an_oversized_sound_is_reported(self) -> None:
        data = project(["event_whenflagclicked"])
        data["targets"][0]["sounds"] = [{
            "name": "big", "assetId": "a" * 32, "dataFormat": "wav",
            "rate": 44100, "sampleCount": 1, "md5ext": f"{'a' * 32}.wav",
        }]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "p.sb3"
            write_sb3(path, data, {f"{'a' * 32}.wav": b"\0" * (11 * 1024 * 1024)})
            result = scratch_audit.audit(
                path, self.VM if (self.VM / "src").is_dir() else None)
        self.assertEqual(len(result["oversized_assets"]), 1,
                         "11 MB is over the 10 MB a single sound may be")
        self.assertEqual(result["oversized_assets"][0][1], "Stage/big")
        # ... and still under the 50 MB the whole project may be, so this case
        # is about the per-asset limit and nothing else
        self.assertLessEqual(result["uncompressed"], scratch_audit.PROJECT_LIMIT)

    def test_the_report_says_rejected_and_the_exit_code_agrees(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "p.sb3"
            write_sb3(path, project(["control_while"]))
            argv = [str(path)]
            if (self.VM / "src").is_dir():
                argv += ["--vm", str(self.VM)]
            code = scratch_audit.main(argv)
        self.assertEqual(code, 1, "a project with a TurboWarp block is not ready")


@unittest.skipIf(shutil.which("ffmpeg") is None, "ffmpeg is not installed")
class MenuOpcodeTests(unittest.TestCase):
    """A dropdown is a block the VM builds, not one an extension spells out."""

    def test_menu_names_are_read_from_the_menus_table(self):
        source = """
        getInfo () {
            return {
                id: 'pen',
                blocks: [{opcode: 'setPenColorParamTo', blockType: 'command'}],
                menus: {
                    colorParam: {
                        acceptReporters: true,
                        items: ['color', 'saturation']
                    }
                }
            };
        }
        """
        self.assertEqual(scratch_audit.extension_menus(source), {"colorParam"})

    def test_a_nested_object_inside_a_menu_is_not_a_menu(self):
        source = "menus: { colorParam: { items: [{ text: 'a', value: 'a' }] } }"
        self.assertEqual(scratch_audit.extension_menus(source), {"colorParam"})


class SlimTests(unittest.TestCase):
    def build(self, tmp: Path, seconds: float = 0.2) -> tuple[Path, dict]:
        blob = wav_bytes(seconds)
        import hashlib
        digest = hashlib.md5(blob).hexdigest()
        data = project(["sound_play"])
        data["targets"][0]["sounds"] = [{
            "name": "note", "assetId": digest, "dataFormat": "wav",
            "rate": 44100.0, "sampleCount": seconds * 44100.0,
            "md5ext": f"{digest}.wav",
        }]
        src = tmp / "in.sb3"
        write_sb3(src, data, {f"{digest}.wav": blob})
        return src, data

    def test_a_wav_becomes_an_mp3_with_a_new_id_and_no_wav_fields(self) -> None:
        """The four fields that have to move together.

        An mp3 is a different asset: a new md5 (which *is* the id), a new
        `md5ext`, a `dataFormat` that says so, and no `sampleCount`/`rate`,
        which are WAV notions the VM recomputes on load.
        """
        import hashlib

        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            src, data = self.build(tmp)
            out = tmp / "out.sb3"
            result = scratch_slim.slim(src, out, "128k", dry=False)
            self.assertEqual(result["converted"], 1)
            self.assertEqual(result["failed"], [])
            with zipfile.ZipFile(out) as archive:
                saved = json.loads(archive.read("project.json"))
                names = set(archive.namelist())
                sound = saved["targets"][0]["sounds"][0]
                blob = archive.read(sound["md5ext"])

        self.assertEqual(sound["dataFormat"], "mp3")
        self.assertTrue(sound["md5ext"].endswith(".mp3"))
        self.assertNotIn("sampleCount", sound, "a frame count is a WAV notion")
        self.assertNotIn("rate", sound)
        self.assertEqual(sound["assetId"], hashlib.md5(blob).hexdigest(),
                         "the asset id is the md5 of the bytes")
        self.assertIn(sound["md5ext"], names, "and the zip holds that name")
        self.assertEqual(sound["name"], "note", "the name a block plays by")

    def test_a_sound_that_cannot_be_encoded_is_left_alone(self) -> None:
        """A project slightly too big is a problem; one with no sound is worse."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            data = project(["sound_play"])
            data["targets"][0]["sounds"] = [{
                "name": "broken", "assetId": "b" * 32, "dataFormat": "wav",
                "rate": 44100, "sampleCount": 1, "md5ext": f"{'b' * 32}.wav",
            }]
            src = tmp / "in.sb3"
            write_sb3(src, data, {f"{'b' * 32}.wav": b"not a wav at all"})
            out = tmp / "out.sb3"
            result = scratch_slim.slim(src, out, "128k", dry=False)
            with zipfile.ZipFile(out) as archive:
                saved = json.loads(archive.read("project.json"))
                names = set(archive.namelist())

        self.assertEqual(result["converted"], 0)
        self.assertEqual(len(result["failed"]), 1, "and it says which one")
        sound = saved["targets"][0]["sounds"][0]
        self.assertEqual(sound["dataFormat"], "wav")
        self.assertIn(sound["md5ext"], names, "the original bytes still travel")

    def test_a_dry_run_writes_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            src, _ = self.build(tmp)
            out = tmp / "out.sb3"
            result = scratch_slim.slim(src, out, "128k", dry=True)
        self.assertEqual(result["converted"], 1)
        self.assertFalse(out.exists())


if __name__ == "__main__":
    unittest.main()
