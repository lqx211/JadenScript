"""The runtime song loader: a pasted MIDI becomes the same event stream the
build-time converter would have written.

The oracle is `tools/midi_to_fl2.py`, which parses the same file offline. What
is compared is everything the player acts on -- tick, kind, channel, note,
velocity, length, and the sample a note resolves to -- plus the renderer's note
table and whether every note actually played. The voice ids are *not* compared:
the converter colours them offline and the loader hands out free ones, so they
are different numbers that mean the same thing.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

from midi_to_fl2 import bank_slots, load_bank  # noqa: E402

NP5 = ROOT / "userspace" / "np5"
VM = ROOT / "scratch-vm-develop"
BANK = NP5 / "bank" / "bank.json"
LOAD = ROOT / "tools" / "np5_load.js"

try:
    import mido
except ImportError:  # pragma: no cover - environment problem, not a bug
    mido = None

requires_loader = unittest.skipUnless(
    BANK.exists() and (VM / "src").is_dir() and shutil.which("node") and mido is not None,
    "needs the generated bank, scratch-vm, node and mido",
)

#: The event columns the player reads. `voice` and `trk` are left out on purpose:
#: see the module docstring, and the colour source is the channel either way.
COLUMNS = ("tick", "kind", "chan", "a", "b", "len")


def sample_song() -> "mido.MidiFile":
    """A file that exercises everything the loader has to get right.

    Four tracks, so the events interleave and the counting sort has work to do;
    a program change and two controllers before the first note, so the order
    inside one tick matters; three note-ons in a row, which is running status;
    a pitch bend; a drum channel; and a note-off in the same tick as a later
    note-on, which is the case the two-pass placement exists for.
    """
    mid = mido.MidiFile(ticks_per_beat=480, type=1)
    conductor = mido.MidiTrack()
    mid.tracks.append(conductor)
    conductor.append(mido.MetaMessage("track_name", name="Sample Song", time=0))
    conductor.append(mido.MetaMessage("set_tempo", tempo=mido.bpm2tempo(150), time=0))
    # Two lyric lines, written where a karaoke file writes them. They are not
    # events the player models -- it has to skip them -- so they double as the
    # check that skipping a text meta does not lose the track.
    conductor.append(mido.MetaMessage("lyrics", text="hello", time=0))
    conductor.append(mido.MetaMessage("lyrics", text="world", time=480))

    chords = mido.MidiTrack()
    mid.tracks.append(chords)
    chords.append(mido.Message("program_change", program=0, channel=0, time=0))
    chords.append(mido.Message("control_change", control=7, value=100, channel=0, time=0))
    chords.append(mido.Message("control_change", control=10, value=64, channel=0, time=0))
    chords.append(mido.Message("note_on", note=60, velocity=90, time=0))
    chords.append(mido.Message("note_on", note=64, velocity=88, time=0))
    chords.append(mido.Message("note_on", note=67, velocity=86, time=0))
    chords.append(mido.Message("pitchwheel", pitch=2048, channel=0, time=240))
    chords.append(mido.Message("note_off", note=60, velocity=0, time=240))
    chords.append(mido.Message("note_off", note=64, velocity=0, time=0))
    chords.append(mido.Message("note_off", note=67, velocity=0, time=0))

    melody = mido.MidiTrack()
    mid.tracks.append(melody)
    melody.append(mido.Message("program_change", program=40, channel=1, time=0))
    for note in (72, 74, 76, 77):
        melody.append(mido.Message("note_on", note=note, velocity=70, channel=1, time=120))
        melody.append(mido.Message("note_on", note=note, velocity=0, channel=1, time=120))

    drums = mido.MidiTrack()
    mid.tracks.append(drums)
    for _ in range(4):
        drums.append(mido.Message("note_on", note=38, velocity=100, channel=9, time=120))
        drums.append(mido.Message("note_off", note=38, velocity=0, channel=9, time=60))
    return mid


class LoaderTests(unittest.TestCase):
    """Builds a plain project once, then parses a handful of pastes."""

    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls._tmp.cleanup)
        cls.dir = Path(cls._tmp.name)
        cls.midi = cls.dir / "song.mid"
        sample_song().save(cls.midi)
        cls.hex_file = cls.dir / "song.hex"
        subprocess.run([sys.executable, str(ROOT / "tools" / "midi_to_hex.py"),
                        str(cls.midi), "-o", str(cls.hex_file)],
                       cwd=ROOT, check=True, capture_output=True)
        cls.oracle_dir = cls.dir / "oracle"
        subprocess.run([sys.executable, str(ROOT / "tools" / "midi_to_fl2.py"), "convert",
                        str(cls.midi), "-o", str(cls.oracle_dir), "--bank", str(BANK)],
                       cwd=ROOT, check=True, capture_output=True)
        cls.project = cls._build_plain()

    @classmethod
    def _build_plain(cls) -> Path:
        """The project with `doObfuscation` off, because a hidden build has no
        names for the probe to read."""
        work = cls.dir / "project"
        work.mkdir()
        # every module, body includes too: a new one must not break the fixture
        for item in sorted(NP5.glob("*.jsw")):
            if item.name != "main.jsw":
                shutil.copy(item, work / item.name)
        shutil.copytree(NP5 / "assets", work / "assets")
        shutil.copytree(NP5 / "bank", work / "bank")
        shutil.copytree(NP5 / "song", work / "song")
        source = (NP5 / "main.jsw").read_text(encoding="utf-8")
        (work / "main.jsw").write_text(
            source.replace("#enable_feature<doObfuscation>;", ""), encoding="utf-8")
        out = work / "plain.sb3"
        subprocess.run([sys.executable, "-m", "jswc", "build", str(work / "main.jsw"),
                        "-o", str(out)], cwd=ROOT, check=True, capture_output=True)
        return out

    def _parse(self, hex_file: Path | None, seconds: float = 4) -> dict:
        done = subprocess.run(
            ["node", str(LOAD), str(self.project), str(hex_file or ""),
             "--json", "--seconds", str(seconds)],
            cwd=ROOT, check=True, capture_output=True, text=True,
        )
        return json.loads(done.stdout.strip().splitlines()[-1])

    def _oracle(self) -> dict:
        def load(name: str) -> list:
            return json.loads((self.oracle_dir / f"{name}.jsa").read_text(encoding="utf-8"))
        return {
            "tick": load("Player.EvTick"), "kind": load("Player.EvKind"),
            "chan": load("Player.EvChan"), "a": load("Player.EvA"),
            "b": load("Player.EvB"), "len": load("Player.EvLen"),
            "sprite": load("Player.EvSprite"), "slot": load("Player.EvSlot"),
            "semis": load("Player.EvSemis"),
            "note_tick": load("Note.tick"), "note_len": load("Note.len"),
            "note_key": load("Note.key"),
        }

    def test_the_paste_is_parsed(self) -> None:
        result = self._parse(self.hex_file)
        self.assertEqual(result["error"], 0, result)
        self.assertEqual(result["mode"], 1, "the paste has to win over the built-in")
        self.assertEqual(result["ppq"], 480)
        self.assertEqual(result["tempo"], 150)
        self.assertEqual(result["songNotes"], self._oracle()["kind"].count(0))

    def test_the_event_stream_matches_the_converter(self) -> None:
        """Same events, same ticks, same notes -- only within a tick may the
        order of two non-note events differ, and that is not audible."""
        result = self._parse(self.hex_file)
        oracle = self._oracle()
        rows = sorted(
            tuple(oracle[column][i] for column in COLUMNS)
            for i in range(len(oracle["tick"]))
        )
        got = sorted(
            tuple(result["ev"][column][i] for column in COLUMNS)
            for i in range(len(result["ev"]["tick"]))
        )
        self.assertEqual(len(got), len(rows), f"{len(got)} events vs {len(rows)}")
        self.assertEqual([row[:4] for row in got], [row[:4] for row in rows])

    def test_every_note_resolves_to_the_right_instrument_and_pitch(self) -> None:
        """A pasted song picks the same instrument, and plays the same pitch.

        The *slot* is allowed to differ: the converter allocates slots so two
        notes that sound together never share one (scratch-audio keeps a single
        player per sound per sprite, so a shared slot means the second note stops
        the first), and the runtime loader, which parses a pasted song at run
        time, does not do that -- it takes the bank's own answer. What must agree
        is what is heard: the sprite the note is played from, and the pitch the
        sample is shifted to.
        """
        result = self._parse(self.hex_file)
        oracle = self._oracle()
        bank = load_bank(BANK)
        roots, _ = bank_slots(bank)

        def voices(ev: dict) -> Counter:
            return Counter(
                (ev["a"][i], ev["chan"][i], ev["sprite"][i])
                for i in range(len(ev["kind"])) if ev["kind"][i] == 0
            )

        self.assertEqual(voices(result["ev"]), voices(oracle))
        for name, ev in (("loader", result["ev"]), ("converter", oracle)):
            wrong = [
                (ev["a"][i], ev["semis"][i], ev["sprite"][i], ev["slot"][i])
                for i in range(len(ev["kind"]))
                if ev["kind"][i] == 0
                and abs(ev["a"][i] - ev["semis"][i] - roots[(ev["sprite"][i], ev["slot"][i])]) > 0.01
            ]
            self.assertEqual(wrong, [], f"{name} played a slot at the wrong pitch: {wrong[:5]}")

    def test_the_note_table_matches(self) -> None:
        result = self._parse(self.hex_file)
        oracle = self._oracle()
        got = sorted(zip(result["note"]["tick"], result["note"]["key"]))
        want = sorted(zip(oracle["note_tick"], oracle["note_key"]))
        self.assertEqual(got, want)
        # lengths agree for every note that lasts longer than the one-tick
        # minimum the player enforces
        got_len = sorted(zip(result["note"]["key"], result["note"]["len"]))
        want_len = sorted(zip(oracle["note_key"], oracle["note_len"]))
        self.assertEqual([pair for pair in got_len if pair[1] > 1],
                         [pair for pair in want_len if pair[1] > 1])

    def test_every_note_plays(self) -> None:
        result = self._parse(self.hex_file)
        self.assertEqual(result["played"], len([k for k in result["ev"]["kind"] if k == 0]))
        self.assertEqual(result["played"], result["ev"]["kind"].count(0))

    def test_an_empty_answer_keeps_the_built_in_song(self) -> None:
        result = self._parse(None, seconds=2)
        self.assertEqual(result["mode"], 0)
        self.assertEqual(result["error"], 0)

    def test_text_that_is_not_a_midi_file_is_refused(self) -> None:
        """A bad paste must not leave the player with half a song."""
        bad = self.dir / "bad.hex"
        bad.write_text("00" * 16, encoding="utf-8")
        result = self._parse(bad, seconds=2)
        self.assertEqual(result["mode"], 0, "the built-in song has to survive")
        self.assertNotEqual(result["error"], 0)

    def test_not_hex_at_all_is_refused(self) -> None:
        bad = self.dir / "worse.hex"
        bad.write_text("this is not a song", encoding="utf-8")
        result = self._parse(bad, seconds=2)
        self.assertEqual(result["mode"], 0)
        self.assertNotEqual(result["error"], 0)

    def test_whitespace_in_the_paste_is_ignored(self) -> None:
        """A wrapped dump is still a dump -- and the check cannot be `== " "`,
        because Scratch compares "0" and " " as the same number."""
        spaced = self.dir / "spaced.hex"
        text = self.hex_file.read_text(encoding="utf-8")
        spaced.write_text("\n".join(text[i:i + 40] for i in range(0, len(text), 40)),
                          encoding="utf-8")
        result = self._parse(spaced)
        self.assertEqual(result["error"], 0, result)
        self.assertEqual(result["mode"], 1)

    def test_the_files_name_size_and_lyrics_come_in_with_a_paste(self) -> None:
        """The debug block's bottom two lines are facts about the *file*.

        A baked song can carry them because the converter reads the MIDI, but a
        paste has to find them itself, and the loader is the only thing that
        sees the bytes. The name is the first track's `FF 03`, the size is the
        decoded length (a paste has no file to measure), and the lyrics are the
        `FF 05` events with the tick each starts at -- scaled to the player's
        1/16-beat ticks, which is why "world" lands on 16 and not on 480.
        """
        result = self._parse(self.hex_file)
        self.assertEqual(result["error"], 0, result)
        self.assertEqual(result["title"], "Sample Song", result)
        self.assertGreaterEqual(result["kb"], 1, result)
        self.assertEqual(result["lyricCount"], 2, result)
        self.assertEqual(result["lyricText"], ["hello", "world"], result)
        self.assertEqual(result["lyricTick"], [0, 16], result)

    def test_a_broken_paste_leaves_the_lyrics_of_the_playing_song_alone(self) -> None:
        """The lyrics are read during the parse, and the parse can fail.

        They are collected in the loader's own buffers and only moved into the
        player's lists once the whole file has been accepted (`commitMeta`), so a
        truncated paste cannot leave the HUD singing lines from a song that is
        not the one playing.
        """
        good = self._parse(self.hex_file)
        self.assertEqual(good["lyricCount"], 2, good)

        truncated = self.dir / "short.hex"
        text = self.hex_file.read_text(encoding="utf-8").strip()
        truncated.write_text(text[: len(text) // 2], encoding="utf-8")
        broken = self._parse(truncated, seconds=2)
        self.assertNotEqual(broken["error"], 0, broken)
        # The paste was refused, so the lists are still the built-in song's --
        # which is what the block has to keep showing.
        self.assertEqual(broken["mode"], 0, broken)
        self.assertGreaterEqual(broken["lyricCount"], 0)


if __name__ == "__main__":
    unittest.main()
