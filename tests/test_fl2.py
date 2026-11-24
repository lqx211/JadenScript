"""FLDataV2: the event stream NotePlayer V5 plays from.

The converter takes plain messages, so these tests never need a MIDI file --
``mido`` is only used by the ``convert`` command itself.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from tools.midi_to_fl2 import (
    KIND_BEND,
    KIND_CC7,
    KIND_OFF,
    KIND_ON,
    KIND_PROGRAM,
    KIND_TEMPO,
    Message,
    apply_bank,
    bank_slots,
    expand,
    load_bank,
    notes_of,
    read,
    thin,
    write,
)

PPQ = 480  # one beat; 16 ticks per beat means 1 tick = 30 MIDI ticks


def on(tick: int, note: int, velocity: int = 100, chan: int = 0) -> Message:
    return Message(tick, "on", chan, note, velocity)


def off(tick: int, note: int, chan: int = 0) -> Message:
    return Message(tick, "off", chan, note)


def cc(tick: int, control: int, value: int, chan: int = 0) -> Message:
    return Message(tick, "cc", chan, control=control, value=value)


class ExpandTests(unittest.TestCase):
    def test_a_note_becomes_on_and_off_on_the_same_voice(self):
        fl2 = expand([on(0, 60), off(PPQ, 60)], ppq=PPQ)
        self.assertEqual(fl2.kinds, [KIND_ON, KIND_OFF])
        self.assertEqual(fl2.ticks, [0, 16])
        self.assertEqual(fl2.voice[0], fl2.voice[1])
        self.assertEqual(fl2.a[0], 60)
        self.assertEqual(fl2.b[0], 100)

    def test_ticks_are_sixteenth_notes(self):
        fl2 = expand([on(0, 60), off(PPQ * 3, 60)], ppq=PPQ)
        self.assertEqual(fl2.ticks[1], 48)

    def test_sustain_pedal_extends_the_note_to_the_pedal_release(self):
        messages = [on(0, 60), cc(PPQ, 64, 127), off(PPQ * 2, 60), cc(PPQ * 4, 64, 0)]
        fl2 = expand(messages, ppq=PPQ)
        notes = notes_of(fl2)
        self.assertEqual(len(notes), 1)
        self.assertEqual(notes[0]["tick"], 0)
        self.assertEqual(notes[0]["length"], 64, "pedal release at beat 4 -> tick 64")

    def test_pedal_only_holds_its_own_channel(self):
        messages = [
            on(0, 60, chan=0),
            cc(0, 64, 127, chan=0),
            on(0, 62, chan=1),
            off(PPQ, 60, chan=0),
            off(PPQ, 62, chan=1),
            cc(PPQ * 2, 64, 0, chan=0),
        ]
        notes = {n["channel"]: n for n in notes_of(expand(messages, ppq=PPQ))}
        self.assertEqual(notes[0]["length"], 32)
        self.assertEqual(notes[1]["length"], 16)

    def test_overlapping_notes_never_share_a_voice(self):
        messages = [on(0, 60), on(PPQ // 2, 64), off(PPQ, 60), off(PPQ * 2, 64)]
        notes = notes_of(expand(messages, ppq=PPQ))
        self.assertEqual(len({n["voice"] for n in notes}), 2)

    def test_a_finished_voice_is_reused(self):
        messages = [on(0, 60), off(PPQ, 60), on(PPQ * 2, 62), off(PPQ * 3, 62)]
        notes = notes_of(expand(messages, ppq=PPQ))
        self.assertEqual(notes[0]["voice"], notes[1]["voice"])
        self.assertEqual(expand(messages, ppq=PPQ).meta["polyphony"], 1)

    def test_program_bend_volume_and_tempo_survive(self):
        messages = [
            Message(0, "program", 0, program=40),
            Message(0, "bend", 0, value=-2048),
            cc(0, 7, 90),
            Message(PPQ, "tempo", -1, value=132.5),
            on(PPQ, 60),
            off(PPQ * 2, 60),
        ]
        fl2 = expand(messages, ppq=PPQ)
        self.assertIn(KIND_PROGRAM, fl2.kinds)
        self.assertIn(KIND_BEND, fl2.kinds)
        self.assertIn(KIND_CC7, fl2.kinds)
        self.assertIn(KIND_TEMPO, fl2.kinds)
        self.assertEqual(fl2.a[fl2.kinds.index(KIND_TEMPO)], 132.5)
        self.assertEqual(fl2.a[fl2.kinds.index(KIND_BEND)], -2048)

    def test_unclosed_notes_end_at_the_last_message(self):
        fl2 = expand([on(0, 60), Message(PPQ, "tempo", -1, value=120)], ppq=PPQ)
        notes = notes_of(fl2)
        self.assertEqual(len(notes), 1)
        self.assertEqual(notes[0]["tick"] + notes[0]["length"], 16)


class NoteTableTests(unittest.TestCase):
    """The renderer's table: one row per note, in start order, with its span."""

    def test_every_note_gets_a_row_with_its_length(self):
        fl2 = expand([on(0, 60), off(PPQ // 2, 60), on(PPQ, 67), off(PPQ * 2, 67)], ppq=PPQ)
        self.assertEqual(fl2.n_ticks, [0, 16])
        self.assertEqual(fl2.n_lens, [8, 16])
        self.assertEqual(fl2.n_keys, [60, 67])
        self.assertEqual(fl2.n_trks, [0, 0])

    def test_rows_are_in_start_order_even_when_notes_overlap(self):
        fl2 = expand(
            [on(0, 60), on(PPQ // 4, 64), off(PPQ, 60), off(PPQ * 2, 64)], ppq=PPQ
        )
        self.assertEqual(fl2.n_ticks, sorted(fl2.n_ticks))
        self.assertEqual(fl2.n_keys, [60, 64])

    def test_the_pedal_lengthens_the_row(self):
        fl2 = expand(
            [cc(0, 64, 127), on(0, 60), off(PPQ // 2, 60), cc(PPQ * 2, 64, 0)],
            ppq=PPQ,
        )
        self.assertEqual(fl2.n_lens, [32])

    def test_a_note_on_carries_its_length_and_other_events_carry_zero(self):
        fl2 = expand(
            [on(0, 60), Message(PPQ // 2, "bend", 0, value=8192), off(PPQ, 60)], ppq=PPQ
        )
        by_kind = {k: fl2.evlen[i] for i, k in enumerate(fl2.kinds)}
        self.assertEqual(by_kind[KIND_ON], 16)
        self.assertEqual(by_kind[KIND_OFF], 0)
        self.assertEqual(by_kind[KIND_BEND], 0)

    def test_the_colour_column_is_the_track_when_there_are_several(self):
        """The example colours notes by MIDI track, so that is what we carry."""
        fl2 = expand(
            [
                Message(0, "on", 0, 60, 100, track=0),
                Message(0, "on", 0, 64, 100, track=3),
                Message(PPQ, "off", 0, 60, track=0),
                Message(PPQ, "off", 0, 64, track=3),
            ],
            ppq=PPQ,
        )
        self.assertEqual(fl2.n_trks, [0, 3])

    def test_auto_hue_falls_back_to_the_channel_on_one_track(self):
        """A format-0 file has one track, which separates nothing -- the channel
        is what tells the instruments apart there."""
        fl2 = expand(
            [
                Message(0, "on", 0, 60, 100, track=0),
                Message(0, "on", 5, 64, 100, track=0),
                Message(PPQ, "off", 0, 60, track=0),
                Message(PPQ, "off", 5, 64, track=0),
            ],
            ppq=PPQ,
            hue="auto",
        )
        self.assertEqual(fl2.n_trks, [0, 5])
        self.assertEqual(
            [fl2.trks[i] for i, k in enumerate(fl2.kinds) if k == KIND_ON], fl2.n_trks
        )

    def test_the_channel_can_be_asked_for_by_name(self):
        fl2 = expand(
            [Message(0, "on", 7, 60, 100, track=0), Message(PPQ, "off", 7, 60, track=0)],
            ppq=PPQ,
            hue="channel",
        )
        self.assertEqual(fl2.n_trks, [7])

    def test_the_table_agrees_with_the_event_stream(self):
        """``Note.len`` must be the same length the note-on event carries."""
        fl2 = expand([on(0, 60), off(PPQ // 2, 60), on(PPQ, 64), off(PPQ * 3, 64)], ppq=PPQ)
        lengths = [fl2.evlen[i] for i, k in enumerate(fl2.kinds) if k == KIND_ON]
        self.assertEqual(lengths, fl2.n_lens)


class ThinTests(unittest.TestCase):
    """Thinning is what fits a black MIDI into a project; *what* it drops is the
    difference between a wall of clicks and the piece."""

    @staticmethod
    def cluster(on: int, count: int, length: int) -> list[dict]:
        return [{"on": on, "off": on + length, "chan": 0, "note": 30 + i,
                 "vel": 100, "program": 0, "track": 0} for i in range(count)]

    def test_a_sustained_note_survives_a_crowd_of_staccato_ones(self):
        notes = self.cluster(0, 200, 1) + [{"on": 0, "off": 64, "chan": 0, "note": 36,
                                            "vel": 100, "program": 0, "track": 0}]
        kept = thin(notes, per_tick=8, voices=200, sustain=4)
        self.assertTrue(any(note["off"] - note["on"] == 64 for note in kept),
                        "the held note is the sound of the passage")

    def test_the_voice_budget_is_never_exceeded(self):
        notes = self.cluster(0, 400, 8)
        kept = thin(notes, per_tick=0, voices=100, sustain=4)
        load: dict[int, int] = {}
        for note in kept:
            for tick in range(note["on"], note["off"] + 1):
                load[tick] = load.get(tick, 0) + 1
        self.assertLessEqual(max(load.values()), 100)

    def test_per_tick_caps_the_density(self):
        notes = self.cluster(0, 500, 1)
        kept = thin(notes, per_tick=16, voices=0)
        self.assertEqual(len(kept), 16)

    def test_the_kept_notes_are_spread_across_the_pitches(self):
        """Not the first sixteen, which would bias the chord to its low end."""
        notes = self.cluster(0, 128, 1)
        kept = thin(notes, per_tick=16, voices=0)
        pitches = sorted(note["note"] for note in kept)
        self.assertEqual(pitches[0], 30)
        self.assertGreaterEqual(pitches[-1] - pitches[0], 100)

    def test_without_a_budget_nothing_is_dropped(self):
        notes = self.cluster(0, 10, 4)
        self.assertEqual(thin(notes, per_tick=0, voices=0), notes)


class BankTests(unittest.TestCase):
    def bank(self) -> dict:
        out = Path(self.tmp.name) / "bank.json"
        notes = [[7, -2.0] for _ in range(128)]
        out.write_text(
            json.dumps(
                {
                    "sprites": {"synth.gm.a": {"sounds": []}, "synth.gm.h": {"sounds": []}},
                    "presets": [
                        {"bank": 0, "program": 0, "name": "Piano", "sprite": "synth.gm.a", "notes": notes},
                        {
                            "bank": 0,
                            "program": 40,
                            "name": "Violin",
                            "sprite": "synth.gm.a",
                            "notes": [[9, 1.5] for _ in range(128)],
                        },
                        {
                            "bank": 128,
                            "program": 0,
                            "name": "Kit",
                            "sprite": "synth.gm.h",
                            "notes": [[3, 0.0] for _ in range(128)],
                        },
                    ],
                }
            ),
            encoding="utf-8",
        )
        return load_bank(out)

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def test_program_change_selects_the_sample(self):
        messages = [Message(0, "program", 0, program=40), on(PPQ, 60), off(PPQ * 2, 60)]
        fl2 = expand(messages, ppq=PPQ)
        apply_bank(fl2, self.bank())
        index = fl2.kinds.index(KIND_ON)
        self.assertEqual(fl2.sprites[index], 0)
        self.assertEqual(fl2.slots[index], 9)
        self.assertEqual(fl2.semis[index], 1.5)

    def many_slot_bank(self, slots: int = 4) -> dict:
        """One program, `slots` samples spread over the keyboard.

        Each sample has one root note, exactly as a real bank does: the semitones
        in a row are ``note - root``, which is what makes a slot reusable for
        another pitch.
        """
        out = Path(self.tmp.name) / "many.json"
        roots = [(k + 0.5) * 128 / slots for k in range(slots)]
        notes = [[min(slots - 1, int(note / (128 / slots))),
                  note - roots[min(slots - 1, int(note / (128 / slots)))]]
                 for note in range(128)]
        out.write_text(json.dumps({
            "sprites": {"synth.gm.a": {"sounds": []}},
            "presets": [{"bank": 0, "program": 0, "name": "Organ", "sprite": "synth.gm.a", "notes": notes}],
        }), encoding="utf-8")
        return load_bank(out)

    def test_a_second_note_takes_another_slot_instead_of_cutting_the_first(self):
        """One sound is one player, so two sounding notes need two slots.

        scratch-audio keeps one player per sound per sprite and stops the old one
        when the same sound is started again, so a bank lookup that gives every
        pitch its nearest sample turns a four-note chord on a four-sample program
        into one sounding note (and on a one-sample program, into nothing but the
        top note). The allocator has to hand each note its own slot.
        """
        messages = [on(0, 60), on(0, 62), on(0, 64), on(0, 65),
                    off(PPQ, 60), off(PPQ, 62), off(PPQ, 64), off(PPQ, 65)]
        fl2 = expand(messages, ppq=PPQ)
        apply_bank(fl2, self.many_slot_bank())
        bank = self.many_slot_bank()
        roots, _ = bank_slots(bank)
        slots = [fl2.slots[i] for i, kind in enumerate(fl2.kinds) if kind == KIND_ON]
        self.assertEqual(len(set(slots)), 4, f"a four-note chord needs four slots, got {slots}")
        # And the pitch effect still lands the chosen sample on the right note.
        for i, kind in enumerate(fl2.kinds):
            if kind == KIND_ON:
                self.assertEqual(fl2.a[i] - fl2.semis[i], roots[(0, fl2.slots[i])])

    def test_a_restrike_may_keep_the_slot_its_own_note_is_using(self):
        """Repeating a pitch on the same slot is a restrike, not a lost voice.

        A piano damps its string when the key is struck again, so the allocator
        must not spend a different sample on the repeat: that would both change
        the timbre and stop the note from behaving like a restrike.
        """
        messages = [on(0, 60), off(PPQ // 2, 60), on(PPQ // 4, 60), off(PPQ, 60)]
        fl2 = expand(messages, ppq=PPQ)
        apply_bank(fl2, self.many_slot_bank())
        slots = [fl2.slots[i] for i, kind in enumerate(fl2.kinds) if kind == KIND_ON]
        self.assertEqual(slots[0], slots[1], f"the repeat moved to another sample: {slots}")
        self.assertEqual(fl2.meta["restrikes"], 1)
        self.assertEqual(fl2.meta["stolen_slots"], 0)

    def test_a_chord_past_the_sample_count_is_counted_not_hidden(self):
        """A program with fewer samples than voices has to steal; say so."""
        messages = [on(0, note) for note in (60, 62, 64)]
        messages += [off(PPQ, note) for note in (60, 62, 64)]
        fl2 = expand(messages, ppq=PPQ)
        apply_bank(fl2, self.many_slot_bank(slots=2))
        self.assertEqual(fl2.meta["stolen_slots"], 1, fl2.meta)
        self.assertEqual(len({fl2.slots[i] for i, kind in enumerate(fl2.kinds) if kind == KIND_ON}), 2)

    def test_channel_ten_uses_the_drum_bank(self):
        fl2 = expand([on(0, 38, chan=9), off(PPQ, 38, chan=9)], ppq=PPQ)
        apply_bank(fl2, self.bank())
        index = fl2.kinds.index(KIND_ON)
        self.assertEqual(fl2.sprites[index], 1, "synth.gm.h is sprite index 1")
        self.assertEqual(fl2.slots[index], 3)


def _have_mido() -> bool:
    try:
        import mido  # noqa: F401
    except ImportError:
        return False
    return True


@unittest.skipUnless(_have_mido(), "mido is not installed")
class MidiReaderTests(unittest.TestCase):
    """``read_midi`` is the one place a real file is parsed, so it gets a file."""

    def setUp(self):
        import mido

        self.mido = mido
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "song.mid"

    def write(self, *messages) -> Path:
        mid = self.mido.MidiFile(ticks_per_beat=PPQ)
        track = self.mido.MidiTrack()
        mid.tracks.append(track)
        for message in messages:
            track.append(message)
        mid.save(str(self.path))
        return self.path

    def test_a_pitch_wheel_becomes_a_bend(self):
        from tools.midi_to_fl2 import KIND_BEND, read_midi

        M = self.mido.Message
        path = self.write(M("pitchwheel", pitch=-2048, time=0), M("note_on", note=60, time=0))
        messages, ppq = read_midi(path)
        self.assertEqual(ppq, PPQ)
        bends = [m for m in messages if m.kind == "bend"]
        self.assertEqual(len(bends), 1)
        self.assertEqual(bends[0].value, -2048)

    def test_a_tempo_event_becomes_bpm(self):
        from tools.midi_to_fl2 import read_midi

        path = self.write(self.mido.MetaMessage("set_tempo", tempo=500000, time=0))  # 120 BPM
        messages, _ = read_midi(path)
        tempo = [m for m in messages if m.kind == "tempo"]
        self.assertEqual(len(tempo), 1)
        self.assertAlmostEqual(tempo[0].value, 120.0, places=2)


@unittest.skipUnless(_have_mido(), "mido is not installed")
class SongMetaTests(unittest.TestCase):
    """What the debug block shows about a file: its name, its size, its lyrics.

    None of it is needed to *play* the song, and none of it can be recovered from
    the note lists afterwards -- the project keeps a full copy of the MIDI as
    hex, but that only helps a project that can parse it. So the converter reads
    it out of the file and bakes it in beside the tempo.
    """

    def setUp(self):
        import mido

        self.mido = mido
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def file(self, *tracks, name: str = "song.mid") -> Path:
        mid = self.mido.MidiFile(ticks_per_beat=PPQ, type=1)
        for messages in tracks:
            track = self.mido.MidiTrack()
            mid.tracks.append(track)
            for message in messages:
                track.append(message)
        path = Path(self.tmp.name) / name
        mid.save(str(path))
        return path

    def test_the_sequence_name_is_the_first_track_name_at_tick_zero(self):
        from tools.midi_to_fl2 import read_song_meta

        M = self.mido.Message
        Meta = self.mido.MetaMessage
        path = self.file(
            [Meta("track_name", name="The Real Title", time=0), Meta("set_tempo", time=0)],
            # a second track names itself too, and must not win
            [Meta("track_name", name="Piano", time=0), M("note_on", note=60, time=0)],
        )
        meta = read_song_meta(path, path)
        self.assertEqual(meta["title"], "The Real Title")
        self.assertEqual(meta["kilobytes"], max(1, round(path.stat().st_size / 1024)))

    def test_a_file_with_no_name_is_titled_by_its_own_file_name(self):
        from tools.midi_to_fl2 import read_song_meta

        path = self.file([self.mido.MetaMessage("set_tempo", time=0)],
                         name="Counting Stars.mid")
        self.assertEqual(read_song_meta(path, path)["title"], "Counting Stars")

    def test_lyrics_keep_their_words_and_land_on_player_ticks(self):
        from tools.midi_to_fl2 import TICKS_PER_BEAT, lyric_lists, read_song_meta

        Meta = self.mido.MetaMessage
        # `time` is a *delta*: one beat, then two more, so the third line starts
        # three beats in.
        path = self.file([
            Meta("lyrics", text="first", time=0),
            Meta("lyrics", text="  second  ", time=PPQ),
            Meta("lyrics", text="third", time=PPQ * 2),
        ])
        meta = read_song_meta(path, path)
        # A beat is TICKS_PER_BEAT player ticks, whatever the file's own PPQ is,
        # because the player counts 1/16 beats and not source ticks.
        self.assertEqual(meta["lyrics"], [[0, "first"], [TICKS_PER_BEAT, "second"],
                                         [TICKS_PER_BEAT * 3, "third"]])
        lists = lyric_lists(meta)
        self.assertEqual(lists["Song.LyricTick"], [0, TICKS_PER_BEAT, TICKS_PER_BEAT * 3])
        self.assertEqual(lists["Song.LyricText"], ["first", "second", "third"])

    def test_a_hex_copy_of_the_file_reads_the_same_way(self):
        """A baked song can be brought up to date from the hex it carries.

        `make np5-hex` and `Song.hex` are the only surviving copy of some songs,
        so `midi_to_fl2.py meta --hex` has to get the same facts out of the text
        that it would get out of the file.
        """
        from tools.midi_to_fl2 import read_song_meta

        Meta = self.mido.MetaMessage
        path = self.file([Meta("track_name", name="From Hex", time=0),
                          Meta("lyrics", text="la", time=0)])
        as_file = read_song_meta(path, path)
        import io

        raw = path.read_bytes()
        as_hex = read_song_meta(io.BytesIO(raw))
        self.assertEqual(as_hex["title"], "From Hex")
        self.assertEqual(as_hex["lyrics"], as_file["lyrics"])
        # A paste has no file name to fall back on and no directory to measure,
        # so its size is the bytes it decoded to -- the same file, the same size.
        self.assertEqual(as_hex["kilobytes"], as_file["kilobytes"])


class FilesTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def test_write_then_read_keeps_every_note(self):
        fl2 = expand([on(0, 60), on(PPQ // 2, 64), off(PPQ, 60), off(PPQ * 2, 64)], ppq=PPQ)
        out = Path(self.tmp.name) / "song"
        meta = write(fl2, out)
        self.assertEqual(meta["events"], len(fl2.ticks))
        self.assertTrue((out / "Player.EvTick.jsa").exists())
        self.assertTrue((out / "Player.EvIndex.jsa").exists())
        again = read(out)
        self.assertEqual(notes_of(again), notes_of(fl2))

    def test_the_note_table_round_trips(self):
        fl2 = expand([on(0, 60), off(PPQ, 60), on(PPQ * 2, 72), off(PPQ * 3, 72)], ppq=PPQ)
        out = Path(self.tmp.name) / "song"
        meta = write(fl2, out)
        self.assertIn("Note.tick", meta["names"])
        again = read(out)
        self.assertEqual(again.n_lens, fl2.n_lens)
        self.assertEqual(again.n_keys, fl2.n_keys)

    def test_the_index_points_into_the_tick_list(self):
        fl2 = expand([on(i * 10, 60) for i in range(600)] + [off(10 * 600, 60)], ppq=PPQ)
        out = Path(self.tmp.name) / "song"
        write(fl2, out)
        index = json.loads((out / "Player.EvIndex.jsa").read_text(encoding="utf-8"))
        ticks = json.loads((out / "Player.EvTick.jsa").read_text(encoding="utf-8"))
        self.assertEqual(index, [ticks[0], ticks[512], ticks[1024]])


if __name__ == "__main__":
    unittest.main()
