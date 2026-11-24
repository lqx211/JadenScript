"""The SoundFont -> NotePlayer V5 bank step.

The fixture is a hand-built one-preset SoundFont, so the parser, the zone
resolution and the loop bake are all checked against numbers we chose instead
of against a third-party font that is not in the repository.
"""

from __future__ import annotations

import io
import json
import struct
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from array import array
from pathlib import Path

from tools.sf2_bank import Sf2, bake, family_of, is_sustaining, main

RATE = 22050


def _chunk(cid: str, payload: bytes) -> bytes:
    return cid.encode("latin-1") + struct.pack("<I", len(payload)) + payload + (b"\0" if len(payload) & 1 else b"")


def _list(kind: str, payload: bytes) -> bytes:
    return _chunk("LIST", kind.encode("latin-1") + payload)


def _name(text: str) -> bytes:
    return text.encode("latin-1")[:20].ljust(20, b"\0")


class FontBuilder:
    """Just enough of the SF2 format to exercise the reader."""

    def __init__(self):
        self.pcm = array("h")
        self.samples: list[dict] = []
        self.instruments: list[list[list[tuple[int, int]]]] = []
        self.presets: list[tuple[int, int, int]] = []

    def add_sample(self, name: str, frames: int, root: int, loop: tuple[int, int], value: int = 1000) -> int:
        start = len(self.pcm)
        self.pcm.extend(array("h", [value if i % 2 == 0 else -value for i in range(frames)]))
        self.samples.append(
            {"name": name, "start": start, "end": start + frames, "loop": (start + loop[0], start + loop[1]), "root": root}
        )
        return len(self.samples) - 1

    def add_instrument(self, name: str, zones: list[list[tuple[int, int]]]) -> int:
        self.instruments.append((name, zones))
        return len(self.instruments) - 1

    def add_preset(self, name: str, bank: int, program: int, instrument: int) -> int:
        self.presets.append((name, bank, program, instrument))
        return len(self.presets) - 1

    def to_bytes(self) -> bytes:
        phdr = b""
        pbag = b""
        pgen = b""
        gen_index = 0
        for _name_, bank, program, instrument in self.presets:
            phdr += _name(_name_) + struct.pack("<HHHIII", program, bank, len(pbag) // 4, 0, 0, 0)
            pbag += struct.pack("<HH", gen_index, 0)
            pgen += struct.pack("<HH", 41, instrument)
            gen_index += 1
        phdr += _name("EOP") + struct.pack("<HHHIII", 0, 0, len(pbag) // 4, 0, 0, 0)
        pbag += struct.pack("<HH", gen_index, 0)
        pgen += struct.pack("<HH", 0, 0)

        inst = b""
        ibag = b""
        igen = b""
        gen_index = 0
        for _name_, zones in self.instruments:
            inst += _name(_name_) + struct.pack("<H", len(ibag) // 4)
            for gens in zones:
                ibag += struct.pack("<HH", gen_index, 0)
                for oper, amount in gens:
                    igen += struct.pack("<HH", oper, amount & 0xFFFF)
                gen_index += len(gens)
        inst += _name("EOI") + struct.pack("<H", len(ibag) // 4)
        ibag += struct.pack("<HH", gen_index, 0)
        igen += struct.pack("<HH", 0, 0)

        shdr = b""
        for sample in self.samples:
            shdr += (
                _name(sample["name"])
                + struct.pack(
                    "<IIIIIBbHH",
                    sample["start"],
                    sample["end"],
                    sample["loop"][0],
                    sample["loop"][1],
                    RATE,
                    sample["root"],
                    0,
                    0,
                    1,
                )
            )
        shdr += _name("EOS") + struct.pack("<IIIIIBbHH", 0, 0, 0, 0, RATE, 0, 0, 0, 0)

        pdta = (
            _chunk("phdr", phdr)
            + _chunk("pbag", pbag)
            + _chunk("pmod", b"\0" * 10)
            + _chunk("pgen", pgen)
            + _chunk("inst", inst)
            + _chunk("ibag", ibag)
            + _chunk("imod", b"\0" * 10)
            + _chunk("igen", igen)
            + _chunk("shdr", shdr)
        )
        data = array("h", self.pcm)
        if sys.byteorder == "big":
            data.byteswap()
        body = _list("sdta", _chunk("smpl", data.tobytes())) + _list("pdta", pdta)
        return b"RIFF" + struct.pack("<I", len(body) + 4) + b"sfbk" + body


def one_preset_font() -> bytes:
    font = FontBuilder()
    soft = font.add_sample("SOFT", 400, 60, (100, 300))
    loud = font.add_sample("LOUD", 200, 72, (50, 150))
    drum = font.add_sample("KICK", 120, 60, (10, 100), value=4000)
    zones = [
        [(43, 0 | (127 << 8)), (44, 0 | (63 << 8)), (54, 1), (53, soft)],
        [(43, 0 | (127 << 8)), (44, 64 | (127 << 8)), (54, 1), (53, loud)],
    ]
    tonal = font.add_instrument("Tonal", zones)
    drum_kit = font.add_instrument("Kit", [[(43, 0 | (127 << 8)), (54, 0), (53, drum)]])
    font.add_preset("Piano-ish", 0, 0, tonal)
    font.add_preset("Strings-ish", 0, 40, tonal)
    font.add_preset("Standard", 128, 0, drum_kit)
    return font.to_bytes()


class RealBankTests(unittest.TestCase):
    """The bank this repository actually ships, if it has been baked.

    Every entry of the note -> sample table has to name a sound that exists on
    the sprite it points at. The voice script plays `"s" + slot`, so a preset
    whose sounds are named after something else is not a wrong sample, it is
    silence: `playSound` with a name nothing matches does nothing and says
    nothing. That is what a piano sprite named `s24, s36, ...` cost once.
    """

    BANK = ROOT_BANK = Path(__file__).resolve().parent.parent / "userspace" / "np5" / "bank"

    def setUp(self) -> None:
        if not (self.BANK / "bank.json").exists():
            self.skipTest("the bank has not been baked (make np5-bank)")

    def test_every_mapped_slot_is_a_sound_the_sprite_has(self) -> None:
        report = json.loads((self.BANK / "bank.json").read_text(encoding="utf-8"))
        sprites = report["sprites"]
        order = sorted(sprites)
        names = {sprite: {sound["name"] for sound in info["sounds"]} for sprite, info in sprites.items()}
        voice = json.loads((self.BANK / "Bank.voice.jsa").read_text(encoding="utf-8"))
        semis = json.loads((self.BANK / "Bank.semis.jsa").read_text(encoding="utf-8"))
        self.assertEqual(len(voice), len(semis))
        checked = 0
        for packed in voice:
            if packed < 0:
                continue                      # "no sample for this note", dropped by the loader
            sprite, slot = divmod(packed, 100000)
            self.assertLess(sprite, len(order), f"packed {packed} names no sprite")
            self.assertIn(f"s{slot}", names[order[sprite]],
                          f"{order[sprite]} has no sound 's{slot}' (it has {sorted(names[order[sprite]])[:6]}...)")
            checked += 1
        self.assertGreater(checked, 20_000, "the table should be mostly playable")


class Sf2BankTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "fixture.sf2"
        self.path.write_bytes(one_preset_font())
        self.sf2 = Sf2(self.path)

    def tearDown(self):
        self.tmp.cleanup()

    def test_reads_presets_and_zones(self):
        self.assertEqual(len(self.sf2.samples), 3)
        self.assertEqual([p.name for p in self.sf2.presets], ["Piano-ish", "Strings-ish", "Standard"])
        self.assertEqual(self.sf2.presets[0].bank, 0)
        self.assertEqual(self.sf2.presets[2].bank, 128)
        self.assertEqual(len(self.sf2.presets[0].zones), 2)

    def test_note_to_sample_and_semitones(self):
        preset = self.sf2.presets[0]
        quiet = self.sf2.lut(preset, 40)
        loud = self.sf2.lut(preset, 100)
        self.assertEqual(quiet[60][0], 0)  # soft layer, root 60
        self.assertEqual(quiet[60][1], 0.0)
        self.assertEqual(loud[60][0], 1)  # loud layer, root 72
        self.assertEqual(loud[60][1], -12.0)
        self.assertEqual(loud[72][1], 0.0)
        self.assertEqual(loud[60][2], 1)  # loop mode reaches the bank

    def test_pitch_correction_moves_the_root_the_other_way(self):
        """A font that stores +48 must not be 96 cents sharp.

        FluidSynth is the reference here: rendering these presets and measuring
        the result put the error at exactly twice the correction when the
        correction is *added* to the key number, so it is subtracted.
        """
        font = FontBuilder()
        flat = font.add_sample("FLAT", 400, 70, (100, 300))
        flat_sample = font.samples[flat]
        instrument = font.add_instrument("One", [[(43, 0 | (127 << 8)), (54, 1), (53, flat)]])
        font.add_preset("Tuned", 0, 0, instrument)
        data = font.to_bytes()
        # Rewrite the correction byte of the first sample: +48 cents.
        marker = _name("FLAT")
        at = data.index(marker)
        data = bytearray(data)
        data[at + 41] = 48
        del flat_sample

        saved = Path(self.tmp.name) / "tuned.sf2"
        saved.write_bytes(bytes(data))
        sf2 = Sf2(saved)
        self.assertEqual(sf2.samples[0].pitch_correction, 48)
        zone = sf2.presets[0].zones[0]
        # Root key 70 sounds 0.48 semitones BELOW key 70, so playing key 70 is
        # +0.48 semitones up from the sample.
        self.assertAlmostEqual(sf2.semitones(zone, 70), 0.48, places=3)
        self.assertAlmostEqual(sf2.semitones(zone, 60), -9.52, places=3)

    def test_drum_zone_is_a_one_shot(self):
        drums = self.sf2.lut(self.sf2.presets[2], 100)
        self.assertEqual(drums[38][0], 2)
        self.assertEqual(drums[38][2], 0)

    def test_bake_repeats_the_loop_to_the_target_length(self):
        sample = self.sf2.samples[0]
        pcm, extended = bake(self.sf2, sample, 1, 0.5)
        self.assertTrue(extended)
        self.assertEqual(len(pcm), int(0.5 * sample.rate))
        # Everything between the fades is the 200-frame loop, repeated.
        loop = pcm_of(self.sf2, sample)[100:300]
        self.assertEqual(pcm[200 : 200 + len(loop)], loop)

    def test_one_shot_keeps_its_length(self):
        sample = self.sf2.samples[2]
        pcm, extended = bake(self.sf2, sample, 0, 2.0)
        self.assertFalse(extended)
        self.assertEqual(len(pcm), sample.frames)

    def test_an_ambience_sample_gets_a_soft_onset_and_headroom(self):
        """Seashore, helicopter and applause arrive at full level instantly.

        Measured on ``gm.sf2``: the seashore is at its steady loudness 10 ms in,
        where a flute takes 60 ms and a string section 30 -- there is no attack
        to preserve, just a step from silence to a wall of noise. So those three
        programs are the one family baked with a long fade-in and a level trim;
        a note keeps its own attack, because rounding off a piano's is worse
        than the problem.
        """
        from tools.sf2_bank import Preset, shape_of

        sea = Preset(0, "Seashore", 0, 122)
        piano = Preset(1, "Piano", 0, 0)
        gunshot = Preset(2, "Gunshot", 0, 127)
        self.assertEqual(shape_of(sea, 0.4, 0.7), (0.4, 0.7))
        self.assertEqual(shape_of(piano, 0.4, 0.7), (0.002, 1.0))
        # a percussive effect is a transient -- it keeps its attack
        self.assertEqual(shape_of(gunshot, 0.4, 0.7), (0.002, 1.0))

    def test_the_fade_and_the_gain_reach_the_baked_pcm(self):
        sample = self.sf2.samples[0]
        plain, _ = bake(self.sf2, sample, 1, 0.5)
        soft, _ = bake(self.sf2, sample, 1, 0.5, fade_in=0.1, gain=0.5)
        self.assertEqual(len(plain), len(soft))
        # Ramped up from silence: the first 10 ms is much quieter than it was.
        head = int(0.01 * sample.rate)
        self.assertLess(max(abs(x) for x in soft[:head]), max(abs(x) for x in plain[:head]) / 2)
        # And the level through the middle is the trim, not the sample.
        mid = slice(len(plain) // 2, len(plain) // 2 + 200)
        self.assertAlmostEqual(max(abs(x) for x in soft[mid]) / max(abs(x) for x in plain[mid]),
                               0.5, delta=0.05)

    def test_families(self):
        self.assertEqual(family_of(self.sf2.presets[0]), "gm.a")
        self.assertEqual(family_of(self.sf2.presets[1]), "gm.c")
        self.assertEqual(family_of(self.sf2.presets[2]), "gm.h")
        self.assertTrue(is_sustaining(self.sf2.presets[1]))
        self.assertFalse(is_sustaining(self.sf2.presets[0]))

    def test_build_writes_a_playable_bank(self):
        out = Path(self.tmp.name) / "bank"
        with redirect_stdout(io.StringIO()):
            code = main(["build", str(self.path), "-o", str(out), "--banks", "0,128", "--sustain", "0.4", "--decay", "0.2"])
        self.assertEqual(code, 0)
        report = json.loads((out / "bank.json").read_text(encoding="utf-8"))
        self.assertEqual(len(report["presets"]), 3)
        self.assertEqual(set(report["sprites"]), {"synth.gm.a", "synth.gm.c", "synth.gm.h"})
        self.assertTrue((out / "synth.gm.jsw").exists())
        for sprite, info in report["sprites"].items():
            self.assertLessEqual(info["count"], 100)
            for sound in info["sounds"]:
                self.assertTrue((out / sound["file"]).exists(), f"{sprite}/{sound['name']} missing")
        # The sustained preset is baked to the requested length, the drum is not.
        strings = next(p for p in report["presets"] if p["name"] == "Strings-ish")
        slot = strings["notes"][60][0]
        wav = next(s for s in report["sprites"][strings["sprite"]]["sounds"] if s["name"] == f"s{slot}")
        self.assertEqual(wav["frames"], int(0.4 * RATE))
        drums = next(p for p in report["presets"] if p["bank"] == 128)
        kick = next(s for s in report["sprites"][drums["sprite"]]["sounds"])
        self.assertEqual(kick["frames"], 120)

    def test_generated_source_compiles(self):
        out = Path(self.tmp.name) / "bank"
        with redirect_stdout(io.StringIO()):
            main(["build", str(self.path), "-o", str(out), "--sustain", "0.2", "--decay", "0.1"])
        from tests.helpers import compile_source

        _, project = compile_source((out / "synth.gm.jsw").read_text(encoding="utf-8"))
        sprites = {t.name for t in project.targets}
        self.assertIn("synth.gm.a", sprites)
        self.assertIn("synth.gm.h", sprites)


def pcm_of(sf2: Sf2, sample) -> array:
    body, size = sf2._sub["smpl"]
    data = array("h")
    data.frombytes(sf2.buf[body : body + size][sample.start * 2 : sample.end * 2])
    if sys.byteorder == "big":
        data.byteswap()
    return data


if __name__ == "__main__":
    unittest.main()
