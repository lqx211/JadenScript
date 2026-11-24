"""The NotePlayer V5 runtime: the clock and the voice budget.

Two things the script side can get wrong without any audio to hear it:

* the clock. A tick is 1/16 beat, and a song can want more ticks a second than
  the VM has frames. Pacing one tick per frame would play such a song at a
  fraction of its tempo, and the fraction would depend on the frame rate.
* the voice budget. A project may have 300 clones; a clone past the cap is
  silently not created, so a note that asks for one and never gets it also
  never retires, and the count of sounding notes drifts up for good.

Both are measured by running the real build in the real VM (`tools/np5_probe.js`)
rather than by reading the source.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
NP5 = ROOT / "userspace" / "np5"
VM = ROOT / "scratch-vm-develop"
PROBE = ROOT / "tools" / "np5_probe.js"
BANK = NP5 / "bank" / "bank.json"

#: 16 ticks per beat. 198 BPM puts a tick at 19 ms, which is *shorter* than a
#: frame at 30 fps (33 ms) -- the case where pacing one tick per frame would
#: play the song at 57% speed. A slower tempo would not exercise the catch-up.
TEMPO = 198
TICKS_PER_SECOND = TEMPO * 16 / 60

requires_player = unittest.skipUnless(
    BANK.exists() and (VM / "src").is_dir() and shutil.which("node"),
    "needs the generated bank, scratch-vm and node",
)


def note_on(note: int, chan: int = 0) -> dict:
    return {"kind": 0, "chan": chan, "a": note, "b": 100, "voice": 0, "len": 8, "track": 0}


def note_off(note: int, chan: int = 0) -> dict:
    return {"kind": 1, "chan": chan, "a": note, "b": 0, "voice": 0, "len": 0, "track": 0}


def write_song(directory: Path, events: list[dict], tempo: float = TEMPO,
               title: str = "test", lyrics: list[tuple[int, str]] | None = None) -> None:
    """A minimal FLDataV2 song, in the shape the converter writes.

    `title` and `lyrics` stand in for what `tools/midi_to_fl2.py` reads out of
    the file: the sequence name, and the `FF 05` events as (tick, words). The
    debug block shows both, so a test that reads the block needs a song that has
    them.
    """
    lyrics = lyrics or []
    keys = ["tick", "kind", "chan", "a", "b", "voice", "len", "track"]
    columns = {key: [event[key] for event in events] for key in keys}
    # Which sample each note plays. Real bakes always write these; a test that
    # leaves them out gets the "" that Scratch compares equal to 0, which is
    # sprite 0 slot 0 -- the first piano sample.
    for key, default in (("sprite", 0), ("slot", 0), ("semis", 0)):
        columns[key] = [event.get(key, default) for event in events]
    names = {
        "Player.EvTick": columns["tick"],
        "Player.EvKind": columns["kind"],
        "Player.EvChan": columns["chan"],
        "Player.EvA": columns["a"],
        "Player.EvB": columns["b"],
        "Player.EvVoice": columns["voice"],
        "Player.EvLen": columns["len"],
        "Player.EvTrk": columns["track"],
        "Player.EvSprite": columns["sprite"],
        "Player.EvSlot": columns["slot"],
        "Player.EvSemis": columns["semis"],
    }
    directory.mkdir(parents=True, exist_ok=True)
    for name, values in names.items():
        (directory / f"{name}.jsa").write_text(json.dumps(values, separators=(",", ":")))
    (directory / "Player.EvIndex.jsa").write_text(json.dumps([columns["tick"][0]] if events else []))
    notes = [{key: event[key] for key in ("tick", "len", "a", "track")} for event in events if event["kind"] == 0]
    for name, key in (("Note.tick", "tick"), ("Note.len", "len"), ("Note.key", "a"), ("Note.trk", "track")):
        (directory / f"{name}.jsa").write_text(json.dumps([note[key] for note in notes]))
    (directory / "Song.LyricTick.jsa").write_text(json.dumps([tick for tick, _ in lyrics]))
    (directory / "Song.LyricText.jsa").write_text(json.dumps([words for _, words in lyrics]))
    (directory / "fl2.json").write_text(json.dumps({"tempo": tempo}))
    # main.jsw includes song/builtin.jsw, the built-in song as hex for the
    # "empty box after a paste" case. A synthetic song has no MIDI behind it, so
    # the fallback is empty -- Load.src starts at 1 and nothing asks for it.
    (directory / "builtin.jsw").write_text('public Song.hex = [""];\n')
    imports = "\n".join(f'import "song/{name}.jsa";' for name in
                        list(names) + ["Player.EvIndex", "Note.tick", "Note.len", "Note.key",
                                       "Note.trk", "Song.LyricTick", "Song.LyricText"])
    (directory / "song.jsw").write_text(
        "// Generated for a test.\n\n"
        f"{imports}\n\n"
        f"public Song.tempo = {int(tempo)};\n"
        f"public Song.pool = 8;\n"
        "public Song.ticks = 512;\n"
        f"public Song.notes = {len(notes)};\n"
        "public Song.hueStep = 10;\n"
        # the three facts the HUD reads and `loader.jsw` restores: the file's
        # name, its size, and how many lyric lines it carried
        f'public Song.title = {json.dumps(title)};\n'
        f'public Song.baked = {json.dumps(title)};\n'
        "public Song.kb = 1;\n"
        f"public Song.lyricCount = {len(lyrics)};\n"
    )


class PlayerTests(unittest.TestCase):
    """Builds a project around a synthetic song, then runs it in the VM."""

    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls._tmp.cleanup)
        cls.dir = Path(cls._tmp.name)

    def _project(self, name: str, events: list[dict], tempo: float = TEMPO,
                 title: str = "test", lyrics: list[tuple[int, str]] | None = None) -> Path:
        """A copy of the player with `events` as its song."""
        work = self.dir / name
        if work.exists():
            return work / "build.sb3"
        work.mkdir(parents=True)
        # Every module main.jsw names, including the body includes: a clone's
        # whole script is voice.inc.jsw, the page lives in terminal.inc.jsw and
        # settings.jsw, and the native sound path in native.jsw. Copying the
        # source directory's modules instead of a list keeps a new one from
        # breaking every test that builds a project.
        for item in sorted(NP5.glob("*.jsw")):
            if item.name != "main.jsw":
                shutil.copy(item, work / item.name)
        shutil.copytree(NP5 / "assets", work / "assets")
        shutil.copytree(NP5 / "bank", work / "bank")
        write_song(work / "song", events, tempo, title, lyrics)
        # doObfuscation would hide the names the probe reads
        source = (NP5 / "main.jsw").read_text().replace("#enable_feature<doObfuscation>;", "")
        (work / "main.jsw").write_text(source)
        out = work / "build.sb3"
        subprocess.run([sys.executable, "-m", "jswc", "build", str(work / "main.jsw"), "-o", str(out)],
                       cwd=ROOT, check=True, capture_output=True)
        return out

    @staticmethod
    def _mix(result: dict, name: str) -> dict[int, list[float]]:
        """voice -> the distinct `name` values it was given, in order."""
        return {voice: values for voice, values in dict(result["mix"][0]).get(name, []) if voice > 0}

    def _run(self, project: Path, seconds: float, fps: int, *args: str) -> dict:
        done = subprocess.run(
            ["node", str(PROBE), str(project), str(seconds), str(fps), "--json", *args],
            cwd=ROOT, check=True, capture_output=True, text=True,
        )
        return json.loads(done.stdout.strip().splitlines()[-1])

    def test_the_tick_rate_does_not_depend_on_the_frame_rate(self) -> None:
        """A tick shorter than a frame has to be caught up, not waited for."""
        events = []
        for i in range(400):          # a long enough stream to average over
            events.append(dict(note_on(60 + i % 24), tick=i))
            events.append(dict(note_off(60 + i % 24), tick=i + 4))
        events.sort(key=lambda e: (e["tick"], e["kind"]))
        project = self._project("clock", events)

        for fps in (60, 30):
            result = self._run(project, 4, fps)
            self.assertAlmostEqual(
                result["ticksPerSecond"], TICKS_PER_SECOND,
                delta=TICKS_PER_SECOND * 0.12,
                msg=f"at {fps} fps: {result}",
            )

    def test_a_chord_past_the_clone_cap_does_not_strand_a_voice(self) -> None:
        """300 clones is the cap; the 301st note must not be counted as sounding.

        Before the cap was respected, `Player.voices` climbed to the size of the
        chord and stayed there, which pinned every later release to the shortest
        fade. A note owns its clone (voice.inc.jsw), so the count and the clone
        count both have to come back to zero.
        """
        events = [dict(note_on(40 + i % 40), tick=0) for i in range(400)]
        events += [dict(note_off(40 + i % 40), tick=20) for i in range(400)]
        events.sort(key=lambda e: (e["tick"], e["kind"]))
        project = self._project("cluster", events)

        result = self._run(project, 4, 30)
        self.assertLessEqual(result["peakClones"], 300, result)
        # The clone counts itself, so a clone the platform refuses is not counted
        # -- the voice count can never claim more notes than the VM is playing.
        self.assertLessEqual(result["peakVoices"], result["peakClones"], result)
        self.assertEqual(result["finalVoices"], 0,
                         f"the voice count must come back to zero: {result}")
        self.assertEqual(result["finalClones"], 0, result)

    def test_the_settings_page_opens_and_the_arrows_change_a_value(self) -> None:
        """`s`, two downs to FOV, one right: +10, and the page is still up.

        The page is a terminal screen driven from the keyboard, so the keys are
        pressed at it here rather than the values being read back from a
        screenshot nobody can look at in a test.
        """
        events = [dict(note_on(60), tick=0), dict(note_off(60), tick=8)]
        project = self._project("page", events)

        result = self._run(project, 3, 30, "--keys", "s,ArrowDown,ArrowDown,ArrowRight")
        page = result["settings"][0]
        self.assertEqual(page["open"], 1, result)
        self.assertEqual(page["cursor"], 3, result)
        self.assertEqual(page["fov"], 160, f"right must add 10: {result}")

    def test_space_flips_a_switch_and_s_closes_the_page(self) -> None:
        events = [dict(note_on(60), tick=0), dict(note_off(60), tick=8)]
        project = self._project("switch", events)

        # open, down x3 to SOUND MODE, space, then s to hide it again
        result = self._run(project, 4, 30, "--keys",
                           "s,ArrowDown,ArrowDown,ArrowDown,Space,s")
        page = result["settings"][0]
        self.assertEqual(page["sound"], 1, f"space must flip the mode: {result}")
        self.assertEqual(page["open"], 0, f"the second s must close it: {result}")
        self.assertEqual(page["horizon"], 250, "the page must not touch what it did not select")

    def test_scratch_sound_mode_plays_notes_and_keeps_time(self) -> None:
        """SOUND MODE = SCRATCH hands the note to the music extension instead.

        The two blocks it uses are the extension's hidden MIDI ones, which do
        the GM folding inside the VM; if they were not in the project the mode
        would silently play nothing, so the opcodes are checked as well as the
        behaviour.

        The clone is still created, and that is the point: `play note for beats`
        is a *waiting* block, so calling it from the clock's dispatch loop (which
        is what the first attempt did) re-executes it inside the same frame for
        the note's whole length and the project stops keeping time. The clone has
        a thread that can wait, so the wait costs nothing and the clone's
        lifetime becomes the note's lifetime. The tick rate is the measurement
        that would have caught the spin.
        """
        events = [dict(kind=2, chan=0, a=24, b=0, voice=0, len=0, track=0, tick=0)]
        for i in range(40):
            events.append(dict(note_on(60 + i % 12), tick=i * 8))
            events.append(dict(note_off(60 + i % 12), tick=i * 8 + 4))
        events.sort(key=lambda e: (e["tick"], e["kind"]))
        project = self._project("native", events)

        result = self._run(project, 6, 30, "--keys",
                           "s,ArrowDown,ArrowDown,ArrowDown,Space,s")
        page = result["settings"][0]
        self.assertEqual(page["sound"], 1, result)
        self.assertGreater(result["notesPlayed"], 5, result)
        # One clone per *sounding* note, not one per note in the song: the notes
        # are 8 ticks apart and last 4, so the count never gets near 40.
        self.assertLess(result["peakClones"], 10,
                        f"SCRATCH mode should hold a few voices, not the song: {result}")
        # The clock has to keep the song's rate while the extension plays. A
        # spin inside `play note for beats` shows up here first.
        self.assertAlmostEqual(result["ticksPerSecond"], TICKS_PER_SECOND,
                               delta=TICKS_PER_SECOND * 0.2, msg=str(result))

        with zipfile.ZipFile(project) as archive:
            project_json = json.loads(archive.read("project.json"))
        opcodes = {block.get("opcode") for target in project_json["targets"]
                   for block in target["blocks"].values() if isinstance(block, dict)}
        self.assertIn("music_midiSetInstrument", opcodes)
        self.assertIn("music_midiPlayDrumForBeats", opcodes)

        # The extension's `midiSetInstrument` takes a ONE-based GM number: it
        # subtracts 1 and then maps through its own table, so a 0-based program
        # landed one place low -- program 0 (Acoustic Grand) became -1, missed
        # the table and wrapped to the Synth Pad oscillator, which is the square
        # wave this mode used to play for a piano. The song asks for program 24.
        self.assertEqual(result["instruments"], [25],
                         f"SCRATCH mode asked the extension for {result['instruments']}")

    def test_the_second_green_flag_draws_the_keyboard_again(self) -> None:
        """Pressing the green flag twice has to repaint, not only the first time.

        The repaint gate compares `Sensing.timer()` with the timer value of the
        last frame (`Piano.drawn`). The green flag restarts the project timer at
        zero but leaves that variable holding the *previous* run's value, so the
        gate sat subtracting a larger number from a smaller one: the pen layer
        was erased and nothing was drawn over it until the timer caught up --
        which, after a song, is minutes.
        """
        events = [dict(note_on(60), tick=0), dict(note_off(60), tick=8)]
        project = self._project("twice", events)

        result = self._run(project, 2, 30, "--twice")
        first, second = result["strokes"]
        self.assertGreater(first, 0, f"the first run drew nothing: {result}")
        self.assertGreater(second, first * 0.5,
                           f"the second green flag did not repaint: {result}")

    def test_every_note_is_played_until_the_clone_budget(self) -> None:
        """There is no musical filter; only the clone budget can drop a note.

        The player used to thin quiet notes once the mix was crowded, using
        scratch PFA's `velocity x seconds` energy rule. That rule is wrong for a
        black MIDI at exactly the density it was meant to help: it removes real
        notes from the passages that are hardest to hear. So a chord of quiet
        one-tick notes -- the shape the rule dropped -- has to be played in full,
        and a chord past `Player.maxVoices` is the only thing that loses notes.
        """
        events = []
        for k in range(200):                       # quiet and one tick long
            key = 24 + k % 60
            events.append(dict(note_on(key), tick=0, len=1, b=20))
            events.append(dict(note_off(key), tick=1))
        for k in range(1250):                      # past the budget of 1200
            key = 30 + k % 40
            events.append(dict(note_on(key), tick=100, len=20))
            events.append(dict(note_off(key), tick=120))
        events.append(dict(note_on(90), tick=200, len=2))
        events.append(dict(note_off(90), tick=202))
        events.sort(key=lambda e: (e["tick"], e["kind"]))
        project = self._project("unfiltered", events, tempo=480)

        result = self._run(project, 2.5, 60)
        self.assertEqual(result["notesPlayed"], 1451, result)
        # 1250 asked for a clone, 1200 of them could have one.
        self.assertEqual(result["skipped"], 50, result)
        self.assertEqual(result["peakVoices"], 1200, result)
        self.assertLessEqual(result["peakClones"], 1200, result)

    def test_a_key_is_lit_only_while_the_score_says_its_note_sounds(self) -> None:
        """A key must go dark when its note ends, even across a stall.

        The keyboard is redrawn from `Piano.end` -- the tick each key's note
        ends at -- rather than from note-on/note-off counters, because a seek
        moves the cursor past the note-offs of the notes that were down and a
        count then left those keys lit for the rest of the song (visible as keys
        that stay coloured under no falling note). So: every key the player
        lights has to be one the song says is sounding, with and without a stall
        that forces the resync path.
        """
        events = []
        for i in range(400):                       # long enough to stall inside
            events.append(dict(note_on(48 + i % 24), tick=i * 6, len=10))
            events.append(dict(note_off(48 + i % 24), tick=i * 6 + 10))
        events.sort(key=lambda e: (e["tick"], e["kind"]))
        project = self._project("lights", events)

        def sounding_at(tick: int) -> set[int]:
            """The keys the song itself has on the keyboard at `tick`."""
            ended = {}
            for event in events:
                if event["tick"] > tick:
                    break
                if event["kind"] == 0:
                    ended[event["a"]] = max(ended.get(event["a"], 0), event["tick"] + event["len"])
            return {key for key, end in ended.items() if end >= tick}

        for stall, exact in ((0, True), (2, False)):
            with self.subTest(stall=stall):
                result = self._run(project, 2, 30, "--lights", "--stall", str(stall))
                frames = result["lights"][0]
                self.assertGreater(len(frames), 20, result)
                for tick, lit in frames:
                    if tick == 0:
                        continue
                    wanted = sounding_at(tick)
                    extra = sorted(set(lit) - wanted)
                    self.assertEqual(extra, [], f"key {extra} stayed lit at tick {tick} with no note")
                    if exact:
                        # Without a stall every event is dispatched, so the two
                        # have to agree exactly, not just in one direction.
                        self.assertEqual(sorted(lit), sorted(wanted),
                                         f"wrong lights at tick {tick}")

    def test_a_bend_reaches_the_note_that_is_already_sounding(self) -> None:
        """The pitch of a sounding note has to follow the wheel, not stay where it began.

        Scratch keeps sound effects on the *target*, and the engine re-syncs a
        player that is already running when they change (`SoundBank.setEffects`
        walks the live players of that target), so a `set effect` mid-note is
        audible. The player only sends it because the core broadcasts `retune`
        when `Mix.epoch` moves; the voice parks on its own end and has no loop
        of its own. This runs a note held for 64 ticks with the wheel pulled
        fully down at the halfway point and reads the pitches the voice handed
        the engine.
        """
        events = [
            dict(note_on(60), tick=0, len=64),
            dict(kind=3, chan=0, a=-8192, b=0, voice=0, len=0, track=0, tick=32),
            # The clock stops with the last event, and a stopped clock stops the
            # voices with it -- so the song has to outlast the note for the note
            # to be bent while it sounds.
            dict(kind=3, chan=0, a=0, b=0, voice=0, len=0, track=0, tick=70),
        ]
        project = self._project("bend", events)
        result = self._run(project, 1.6, 30, "--pitch")

        trace = {key: values for key, values in result["pitch"][0] if key > 0}
        self.assertEqual(len(trace), 1, f"expected one voice, got {result['pitch']}")
        pitches = next(iter(trace.values()))
        # Nothing shifts the note at its own key, so it starts at 0 tenths; a
        # full downward wheel is -2 semitones at the default range of 2. A
        # converter or loader stores the wheel already centred, so -8192 is the
        # bottom of its travel, not 0.
        self.assertEqual(pitches, [0, -20],
                         f"the bend did not reach the sounding note: {result['pitch']}")

    def test_a_sounding_note_can_be_retuned(self) -> None:
        """A bend has to reach the note that is already playing.

        Both halves of that are invisible without audio, so they are checked on
        the build itself: every sound sprite's `_done` has to start at 0 -- it
        is set to 1 when a note ends and the retune handler ignores finished
        voices, so an initial 1 makes every clone born finished and locks the
        pitch to whatever the note started with -- and every sound sprite has to
        carry the `retune` broadcast hat that the core broadcasts on a bend.
        `_done` used to compile to 1: the first `=` in document order sets a
        variable's initial value, and the only one was the `_done = 1` at the end
        of the note.
        """
        project = self._project("retune", [dict(note_on(60), tick=0), dict(note_off(60), tick=16)])
        with zipfile.ZipFile(project) as archive:
            targets = json.loads(archive.read("project.json"))["targets"]
        sounds = [t for t in targets if t["name"].startswith("synth.")]
        self.assertTrue(sounds)
        for target in sounds:
            variables = {v[0]: v[1] for v in target["variables"].values()}
            self.assertEqual(variables.get("_done"), 0,
                             f"{target['name']} is born finished, so it never retunes")
            hats = [b["fields"]["BROADCAST_OPTION"][0] for b in target["blocks"].values()
                    if isinstance(b, dict) and b.get("opcode") == "event_whenbroadcastreceived"]
            self.assertIn("retune", hats, f"{target['name']} ignores the retune broadcast")

    def test_the_master_volume_knob_reaches_a_note_that_is_already_sounding(self) -> None:
        """VOLUME is row 8, and turning it down has to quiet a note already playing.

        Everything else on the page only changes what the *next* note or frame
        looks like; this row is a fader, and a fader that only affects the next
        note is not a fader. The voice sends `set volume to` at note-on and again
        on every `retune`, and `SoundBank.setEffects` re-syncs the live player, so
        the second number the engine is handed is the proof -- there is no audio
        to listen to here.

        The page is driven from the keyboard: `s` opens it, seven downs put the
        cursor on VOLUME, and two lefts take 100 down to 90.
        """
        events = [
            dict(note_on(60), tick=0, len=200),
            # The clock stops with its last event and a stopped clock stops the
            # voices, so the song has to outlast the note for the note to still
            # be sounding while the keys are pressed.
            dict(note_off(60), tick=240),
        ]
        project = self._project("fader", events)
        result = self._run(project, 4, 30, "--mix", "--keys", "s," + ",".join(["ArrowDown"] * 7) + ",ArrowLeft,ArrowLeft")

        page = result["settings"][0]
        self.assertEqual(page["open"], 1, result)
        self.assertEqual(page["cursor"], 8, result)
        self.assertEqual(page["master"], 90, f"two lefts must take 100 to 90: {result}")

        volumes = self._mix(result, "volume")
        self.assertEqual(len(volumes), 1, f"expected one voice: {result['mix']}")
        values = next(iter(volumes.values()))
        # Velocity 100 squared, and the player opens every channel at a MIDI-ish
        # CC7 of 100 rather than 127, so the note starts at
        # (100/127)^2 * (100/127) * 100 = 48.8 and 90% of that is 43.9. The run
        # outlasts the note on purpose, so the trace ends in the release ramp --
        # hence a membership test for the two values that matter, not first/last.
        start = (100 / 127) ** 2 * (100 / 127) * 100
        self.assertAlmostEqual(values[0], start, delta=0.2,
                              msg=f"the note started at the wrong volume: {values}")
        self.assertTrue(any(abs(value - start * 0.9) < 0.2 for value in values),
                        f"the fader did not reach the sounding note: {values}")

    def test_the_master_pan_knob_reaches_a_note_that_is_already_sounding(self) -> None:
        """PAN is row 9; two rights take a centred note to R20.

        The song has no per-note pan (there is no such column in the event
        stream), so a pan that only worked at note-on would be a knob that did
        nothing at all to anything already playing. The channel's own CC10 is
        added to it rather than replacing it, which is what makes this an offset
        instead of a second, competing control.
        """
        events = [
            dict(note_on(60), tick=0, len=200),
            dict(note_off(60), tick=240),
        ]
        project = self._project("panner", events)
        result = self._run(project, 4, 30, "--mix", "--keys",
                           "s," + ",".join(["ArrowDown"] * 8) + ",ArrowRight,ArrowRight")

        page = result["settings"][0]
        self.assertEqual(page["cursor"], 9, result)
        self.assertEqual(page["pan"], 20, f"two rights must give R20: {result}")

        pans = self._mix(result, "pan")
        self.assertEqual(len(pans), 1, f"expected one voice: {result['mix']}")
        values = next(iter(pans.values()))
        self.assertEqual(values[0], 0, f"a centred note starts at 0: {values}")
        self.assertEqual(values[-1], 20,
                         f"the pan did not reach the sounding note: {values}")

    def test_the_master_pitch_knob_reaches_a_note_that_is_already_sounding(self) -> None:
        """PITCH is row 10, in tenths of a semitone like every other pitch.

        One right is ten tenths, one semitone. Written as an offset from the
        note's own key it composes with the wheel instead of fighting it: the
        voice adds `_semis`, the bend and the master, in that order, and the
        clamp happens on the total rather than on each part.
        """
        events = [
            dict(note_on(60), tick=0, len=200),
            dict(note_off(60), tick=240),
        ]
        project = self._project("transpose", events)
        result = self._run(project, 4, 30, "--mix", "--pitch", "--keys",
                           "s," + ",".join(["ArrowDown"] * 9) + ",ArrowRight")

        page = result["settings"][0]
        self.assertEqual(page["cursor"], 10, result)
        self.assertEqual(page["masterPitch"], 10, f"one right is a semitone: {result}")

        trace = {key: values for key, values in result["pitch"][0] if key > 0}
        self.assertEqual(len(trace), 1, f"expected one voice: {result['pitch']}")
        self.assertEqual(next(iter(trace.values())), [0, 10],
                         f"the transpose did not reach the sounding note: {result['pitch']}")

    def test_the_debug_block_reads_as_the_five_lines_it_is(self) -> None:
        """The block is asserted on the *screen*, not on the variables behind it.

        A pen-font HUD is drawn from twenty-odd joins and formatters, and every
        one of them can be individually right while the line the reader sees is
        wrong -- a line built in the same scratch variable a formatter writes
        loses its head, which is exactly what happened once here. So the probe
        replays the pen strokes into a bitmap, matches the glyphs back into
        characters, and the test reads the five lines the way a person would:

            00:02.91/04:46.49 TICK:84/8251 BPM:108
            FPS:... NPS:... POLYPHONY:...
            POS    bar beat div tick
            MIDI "title" (125 KB)
            -> the lyric

        The labels are capitals because the font cannot tell case apart: a
        Scratch list lookup is case-insensitive (`Cast.compare` lower-cases
        strings), so `indexOf("a")` finds the "A" cell.
        """
        events = [dict(note_on(60), tick=0), dict(note_off(60), tick=8),
                  dict(note_off(60), tick=400)]
        events.sort(key=lambda e: (e["tick"], e["kind"]))
        lyrics = [(0, "first line")]
        project = self._project("debug", events, title="A Song", lyrics=lyrics)
        result = self._run(project, 2, 30, "--art", "--text", "176,159,142,125,108")
        lines = result.get("screen") or []
        self.assertEqual(len(lines), 5, f"the block is five lines: {lines}")

        self.assertRegex(
            lines[0],
            r"^\d\d:\d\d\.\d\d/\d\d:\d\d\.\d\d TICK:\d+/\d+ BPM:\d+$",
            f"line 1 is elapsed/total, tick and tempo: {lines[0]!r}",
        )
        self.assertRegex(lines[1], r"^FPS:\d+ NPS:\d+ POLYPHONY:\d+$", lines[1])
        self.assertRegex(lines[2], r"^POS +\d+ \d+ \d+ +\d+$", lines[2])
        # The title and the size come from the file, not from the notes: the
        # baked song carries both, so they have to survive the bake.
        self.assertEqual(lines[3], 'MIDI "A SONG" (1 KB)')
        # The lyric line is the one the playhead is inside; one lyric at tick 0
        # covers the whole run. Drawn in capitals, because the font has no other
        # case (see the note in `hud`).
        self.assertEqual(lines[4].strip(), "-> FIRST LINE")

    def test_the_lyric_line_follows_the_playhead(self) -> None:
        """The lyric shows the last line whose tick the playhead has reached.

        A lyric is a (tick, words) pair, so "the line the playhead is inside" is
        the last pair at or before the current tick. A cursor that ran ahead
        would sing the next line early; one that lagged would hold the previous
        line past its end. Both are invisible without reading the screen, so the
        block is read at two points of the song and compared with the lyric list
        and the tick the same run reports -- not with a guess at how fast the
        test machine plays.
        """
        events = [dict(note_on(60 + i % 12), tick=i * 16) for i in range(20)]
        events += [dict(note_off(60 + i % 12), tick=i * 16 + 8) for i in range(20)]
        events.sort(key=lambda e: (e["tick"], e["kind"]))
        lyrics = [(0, "one"), (48, "two"), (96, "three")]
        project = self._project("lyrics", events, lyrics=lyrics)

        seen = set()
        for seconds in (0.5, 1.5, 3):
            result = self._run(project, seconds, 30, "--json")
            hud = result["hud"][0]
            self.assertEqual(hud["lyricCount"], len(lyrics), hud)
            wanted = [words for tick, words in lyrics if tick <= result["ticks"]]
            self.assertEqual(hud["lyric"], len(wanted),
                             f"at tick {result['ticks']:.0f} the lines so far are {wanted}: {hud}")
            self.assertEqual(hud["lyricText"], wanted[-1] if wanted else "", hud)
            seen.add(hud["lyricText"])
        # The point of the test is that the line *moves*, so at least two of the
        # three runs have to be showing different ones.
        self.assertGreaterEqual(len(seen), 2, f"the lyric never advanced: {seen}")
    def test_a_note_longer_than_its_sample_keeps_sounding(self) -> None:
        """A held note replays its sample instead of going silent half way.

        Scratch cannot loop a sound, and `Sound.playSound` on a target that is
        already playing starts the file again from the first frame
        (`SoundPlayer.play` builds a fresh source node), so a sample is a hard
        ceiling on how long one note can sound. The bank bakes a sustaining
        instrument to `VoiceDur` seconds -- 3 -- and `voice.inc.jsw` replays the
        file when it runs out, which is the only way a 13-second seashore or a
        35-second pad keeps sounding. Without it the note is silent for most of
        its length, and every expression curve that shapes it lands in the
        silence: that is what "the volume does not change" sounds like.

        The count is of `sound_play`, so it says the voice really restarted the
        sound rather than merely staying alive. TEMPO 198 means 52.8 ticks a
        second, so 340 ticks is 6.4 s and 3 s of audio is replayed at least
        twice more.
        """
        sea = dict(sprite=6, slot=43, semis=-3)        # synth.gm.g slot 43 = Seashore
        events = [
            dict(kind=2, chan=8, a=122, b=0, voice=0, len=0, track=0, tick=0),
            dict(note_on(67, chan=8), tick=0, len=340, **sea),
            dict(note_off(67, chan=8), tick=340),
            # The clock stops with the last event and a stopped clock stops the
            # voices, so the song has to outlast the note for the note to get its
            # third replay.
            dict(kind=6, chan=-1, a=TEMPO, b=0, voice=0, len=0, track=0, tick=520),
        ]
        events.sort(key=lambda e: (e["tick"], e["kind"]))
        project = self._project("held", events)
        result = self._run(project, 7.5, 30, "--plays")
        plays = {voice: count for voice, count in result["plays"][0]}
        self.assertEqual(len(plays), 1, f"one note, one voice: {result['plays']}")
        count = next(iter(plays.values()))
        self.assertGreaterEqual(count, 3,
                                f"6.4 s of note on 3 s of audio has to replay: {count}")

    def test_a_held_piano_note_is_not_restruck(self) -> None:
        """A real recording is a one-shot; replaying it would be a re-strike.

        The bank marks a slot retriggerable only when it was looped out of a few
        milliseconds of sample *and* the preset is a sustaining instrument. The
        piano's samples are real recordings trimmed to 2.5 s, so a note held
        twice that long plays once and rings out -- the alternative, replaying
        it, is a second piano note, not a longer one.
        """
        piano = dict(sprite=8, slot=60, semis=0)      # synth.piano, real recording
        events = [
            dict(kind=2, chan=0, a=0, b=0, voice=0, len=0, track=0, tick=0),
            dict(note_on(60), tick=0, len=340, **piano),
            dict(note_off(60), tick=340),
            dict(kind=6, chan=-1, a=TEMPO, b=0, voice=0, len=0, track=0, tick=520),
        ]
        events.sort(key=lambda e: (e["tick"], e["kind"]))
        project = self._project("heldpiano", events)
        result = self._run(project, 7.5, 30, "--plays")
        plays = {voice: count for voice, count in result["plays"][0]}
        self.assertEqual(list(plays.values()), [1],
                         f"the piano must not be restruck: {result['plays']}")

    def test_the_expression_curve_is_heard_for_the_whole_note(self) -> None:
        """CC11 has to reach a note *past* the length of its sample.

        This is the sea: a long note swelling from quiet to loud. The engine
        re-syncs a live player's volume (`SoundBank.setEffects` walks the players
        of the target), so the numbers the voice hands `set volume to` are the
        evidence -- and the last of them has to be well after the 3 s the first
        sample lasts, which is only true because the voice replays it.
        """
        events = [dict(kind=2, chan=8, a=122, b=0, voice=0, len=0, track=0, tick=0)]
        events.append(dict(note_on(67, chan=8), tick=0, len=340, sprite=6, slot=43, semis=-3))
        # CC11 (kind 9) climbing from 24 to 120 across the note, in 16 steps
        for i in range(16):
            events.append(dict(kind=9, chan=8, a=24 + i * 6, b=0, voice=0, len=0,
                               track=0, tick=i * 20))
        events.append(dict(note_off(67, chan=8), tick=340))
        events.append(dict(kind=6, chan=-1, a=TEMPO, b=0, voice=0, len=0, track=0, tick=520))
        events.sort(key=lambda e: (e["tick"], e["kind"]))
        project = self._project("swell", events)

        result = self._run(project, 7.5, 30, "--mix")
        volumes = {name: dict(entries) for name, entries in result["mix"][0]}["volume"]
        values = next(iter(volumes.values()))
        self.assertGreater(len(values), 6, f"the swell must be stepped: {values}")
        self.assertLess(min(values), max(values) * 0.6,
                        f"the note must start well below its loudest: {values}")


if __name__ == "__main__":
    unittest.main()
