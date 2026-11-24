#!/usr/bin/env python3
"""MIDI -> NotePlayer V5 event stream (FLDataV2).

Usage::

    python3 tools/midi_to_fl2.py convert song.mid -o build/fl2/song
    python3 tools/midi_to_fl2.py dump    build/fl2/song

The V4 pipeline (PyTayo ``0.py`` -> ``1.py``) wrote one line per note and then
loaded it into Scratch at run time. V5 writes parallel lists that jswc imports
with ``.jsa``, so a built-in song costs nothing to load, and every field the
player needs is already resolved:

    EvTick   absolute tick, non-decreasing      (16 ticks per beat)
    EvKind   0 on  1 off  2 program  3 bend  4 cc7  5 cc10  6 tempo  7 cc64
             8 cc1  9 cc11
    EvChan   0..15 (-1 for tempo)
    EvA      note | program | bend-8192 | cc value | bpm
    EvB      velocity
    EvVoice  voice id, shared by a note-on and its note-off

``EvVoice`` is what V4 could not express: a note has an identity, so the
runtime can stop exactly that voice. The ids are coloured at build time --
overlapping notes never share one -- so there is no runtime lookup and the pool
size *is* the song's peak polyphony.

With ``--bank`` the converter also resolves the sample, so the runtime never
searches a zone:

    EvSprite sprite index in bank.json
    EvSlot   sound slot inside that sprite, *allocated* so two notes that sound
             together never share one (see ``apply_bank``)
    EvSemis  pitch effect for the note, before bend
    EvLen    length of the note this row starts; 0 on every other kind

``EvLen`` is what a note-on needs to light its key for exactly as long as the
note sounds. Everything above is an *event*: it says when something happens.
A falling-note renderer needs the other axis, so the converter also writes one
row per note, in start order, as a second table:

    Note.tick  start tick
    Note.len   length in ticks, pedal already included
    Note.key   MIDI key, 0..127 (the keyboard draws index key % 12)
    Note.trk   the hue the example colours by: the MIDI track, or the channel
               when the file has a single track

The renderer walks that table with a single cursor, so a frame costs the notes
it can see instead of the fifteen thousand a song contains.
"""

from __future__ import annotations

import argparse
import heapq
import io
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

KIND_ON = 0
KIND_OFF = 1
KIND_PROGRAM = 2
KIND_BEND = 3
KIND_CC7 = 4
KIND_CC10 = 5
KIND_TEMPO = 6
KIND_CC64 = 7
KIND_CC1 = 8
#: CC11 (expression) is the channel's *second* volume, and an old MIDI leans on
#: it for every swell -- CC7 sets the level once at the top of a track and CC11
#: rides it. Without it those files play flat.
KIND_CC11 = 9

CC_KINDS = {7: KIND_CC7, 10: KIND_CC10, 1: KIND_CC1, 64: KIND_CC64, 11: KIND_CC11}
CC_NAMES = {v: k for k, v in CC_KINDS.items()}

#: One tick is 1/16 of a beat, which is what ``Tick.wait = 4`` means to V4.
TICKS_PER_BEAT = 16


@dataclass
class Message:
    """One MIDI event, already at an absolute tick and channel-scoped."""

    tick: int
    kind: str  # on / off / program / cc / bend / tempo
    chan: int = 0
    note: int = 0
    velocity: int = 0
    value: int = 0
    program: int = 0
    control: int = 0
    track: int = 0


@dataclass
class Fl2:
    ticks: list[int] = field(default_factory=list)
    kinds: list[int] = field(default_factory=list)
    chans: list[int] = field(default_factory=list)
    a: list[float] = field(default_factory=list)
    b: list[int] = field(default_factory=list)
    voice: list[int] = field(default_factory=list)
    # Length of the note a row starts; 0 on every other kind. The player needs
    # it at note-on time to light the key and to size the note body.
    evlen: list[int] = field(default_factory=list)
    trks: list[int] = field(default_factory=list)
    sprites: list[int] | None = None
    slots: list[int] | None = None
    semis: list[float] | None = None
    meta: dict = field(default_factory=dict)
    # One row per note, in start order, for the falling-note renderer: an event
    # stream says *when* something happens, a renderer needs a note's whole
    # span to draw it. Kept out of ``sort`` because it is a separate table.
    n_ticks: list[int] = field(default_factory=list)
    n_lens: list[int] = field(default_factory=list)
    n_keys: list[int] = field(default_factory=list)
    n_trks: list[int] = field(default_factory=list)

    def sort(self) -> None:
        order = sorted(range(len(self.ticks)), key=lambda i: (self.ticks[i], -self.kinds[i]))
        for name in ("ticks", "kinds", "chans", "a", "b", "voice", "evlen", "trks", "sprites", "slots", "semis"):
            values = getattr(self, name)
            if values is not None:
                setattr(self, name, [values[i] for i in order])

    def add(self, tick: int, kind: int, chan: int, a: float, b: int = 0, voice: int = -1,
            length: int = 0, track: int = 0) -> int:
        index = len(self.ticks)
        self.ticks.append(int(tick))
        self.kinds.append(kind)
        self.chans.append(chan)
        self.a.append(a)
        self.b.append(int(b))
        self.voice.append(int(voice))
        self.evlen.append(int(length))
        self.trks.append(int(track))
        return index


# ------------------------------------------------------------------ pairing


def expand(messages: list[Message], ticks_per_beat: int = TICKS_PER_BEAT, ppq: int = 480,
           hue: str = "track", per_tick: int = 0, voices: int = 0,
           sustain: int = 4) -> Fl2:
    """MIDI messages -> note-on/note-off pairs, pedal expanded, ids coloured."""
    scale = ticks_per_beat / ppq
    pedal = [False] * 16
    program = [0] * 16
    pending: dict[tuple[int, int], list[dict]] = {}
    held: dict[tuple[int, int], list[dict]] = {}
    notes: list[dict] = []
    fl2 = Fl2()

    def tick(msg: Message) -> int:
        return int(round(msg.tick * scale))

    def close(note: dict, at: int) -> None:
        note["off"] = max(at, note["on"] + 1)
        notes.append(note)

    for msg in sorted(messages, key=lambda m: (m.tick, m.kind != "off")):
        at = tick(msg)
        if msg.kind == "on":
            key = (msg.chan, msg.note)
            pending.setdefault(key, []).append(
                {"on": at, "off": None, "chan": msg.chan, "note": msg.note, "vel": msg.velocity,
                 "program": program[msg.chan], "track": msg.track}
            )
        elif msg.kind == "off":
            key = (msg.chan, msg.note)
            stack = pending.get(key)
            if not stack:
                continue
            note = stack.pop(0)
            if pedal[msg.chan]:
                held.setdefault(key, []).append(note)
            else:
                close(note, at)
        elif msg.kind == "program":
            program[msg.chan] = msg.program
            fl2.add(at, KIND_PROGRAM, msg.chan, msg.program, track=msg.track)
        elif msg.kind == "cc":
            if msg.control == 64:
                was = pedal[msg.chan]
                pedal[msg.chan] = msg.value >= 64
                if was and not pedal[msg.chan]:
                    for key, notes_ in list(held.items()):
                        if key[0] != msg.chan:
                            continue
                        while notes_:
                            close(notes_.pop(0), at)
                        held.pop(key, None)
                continue
            kind = CC_KINDS.get(msg.control)
            if kind is not None:
                fl2.add(at, kind, msg.chan, msg.value, track=msg.track)
        elif msg.kind == "bend":
            fl2.add(at, KIND_BEND, msg.chan, msg.value, track=msg.track)
        elif msg.kind == "tempo":
            fl2.add(at, KIND_TEMPO, -1, msg.value, track=msg.track)

    end = max([m.tick for m in messages], default=0)
    end = int(round(end * scale))
    for stack in pending.values():
        for note in stack:
            close(note, end)
    for held_ in held.values():
        for note in held_:
            close(note, end)

    if per_tick or voices:
        notes = thin(notes, per_tick, voices, sustain)
    assign_voices(notes)
    table = sorted(notes, key=lambda n: (n["on"], n["voice"]))
    fl2.n_ticks = [n["on"] for n in table]
    fl2.n_lens = [n["off"] - n["on"] for n in table]
    colour = colour_of(hue, table)
    fl2.n_keys = [n["note"] for n in table]
    fl2.n_trks = [colour(n) for n in table]
    for note in table:
        fl2.add(note["on"], KIND_ON, note["chan"], note["note"], note["vel"], note["voice"],
                note["off"] - note["on"], colour(note))
        fl2.add(note["off"], KIND_OFF, note["chan"], note["note"], 0, note["voice"],
                track=colour(note))
    fl2.sort()
    colour_count = len(set(fl2.n_trks)) or 1
    fl2.meta = {
        "hue_step": round(100 / colour_count, 4),
        "thin": per_tick,
        "ticks_per_beat": ticks_per_beat,
        "ppq": ppq,
        "notes": len(notes),
        "ticks": max([n["off"] for n in notes], default=0),
        "polyphony": max((n["voice"] for n in notes), default=-1) + 1,
        "tempo": next((fl2.a[i] for i, k in enumerate(fl2.kinds) if k == KIND_TEMPO), 120),
    }
    return fl2


def thin(notes: list[dict], per_tick: int = 0, voices: int = 0,
         sustain: int = 4) -> list[dict]:
    """Fit a song into the player's limits without losing its sustain.

    A black MIDI asks for more than a Scratch project can sound: Pi.mid peaks at
    7,980 simultaneous voices, and a project may hold 300 clones. Thinning is
    what makes such a file playable at all, and *which* notes it drops is the
    whole question.

    Taking the first *n* of each tick biases every chord towards its lowest
    pitches. Taking the loudest does nothing -- in a black MIDI everything sits
    at the same velocity. And sampling evenly by pitch, which is what this used
    to do, throws away the sustained notes: Pi.mid's 3.1 million notes are 99.8%
    one tick long, so a held bass note competing with two hundred sparkles has an
    8% chance of surviving, and what is left is all clicks. Measured on the thinned
    file: the long notes' share of the sounding time fell from 17.6% to 3%.

    So the order is by importance, not by tick:

    1. **the sustained notes**, longest first, as many as the voice budget allows.
       These are what a listener hears as the sound of the piece.
    2. **the rest**, an even sample of each tick's pitches up to ``per_tick``,
       and only where the voice budget still has room.

    ``voices`` is the ceiling on simultaneous notes (a project has 300 clones;
    leave headroom for the release tails). ``per_tick`` is the ceiling on
    note-ons in one tick, which is what keeps the lists a size a project can
    carry. ``sustain`` is the length, in this player's 1/16-beat ticks, at which
    a note counts as sustained.
    """
    if not per_tick and not voices:
        return notes
    last = max((n["off"] for n in notes), default=0) + 2
    use = [0] * last

    def room(note: dict) -> bool:
        if voices <= 0:
            return True
        return max(use[note["on"]:note["off"] + 1], default=0) < voices

    def claim(note: dict) -> None:
        for tick in range(note["on"], note["off"] + 1):
            use[tick] += 1

    kept: list[dict] = []
    taken: set[int] = set()

    if sustain > 0:
        for note in sorted((n for n in notes if n["off"] - n["on"] >= sustain),
                           key=lambda n: (-(n["off"] - n["on"]), n["on"])):
            if room(note):
                claim(note)
                kept.append(note)
                taken.add(id(note))

    by_tick: dict[int, list[dict]] = {}
    for note in notes:
        if id(note) not in taken:
            by_tick.setdefault(note["on"], []).append(note)
    for on in sorted(by_tick):
        group = sorted(by_tick[on], key=lambda n: (n["note"], n["off"]))
        chosen = group
        if per_tick and len(group) > per_tick:
            step = len(group) / per_tick
            chosen = [group[int(i * step)] for i in range(per_tick)]
        for note in chosen:
            if room(note):
                claim(note)
                kept.append(note)

    kept.sort(key=lambda n: (n["on"], n["note"]))
    return kept


def assign_voices(notes: list[dict]) -> None:
    """Interval colouring: overlapping notes never share an id."""
    free: list[int] = [0]
    next_id = 1
    active: list[tuple[int, int]] = []
    for note in sorted(notes, key=lambda n: n["on"]):
        while active and active[0][0] <= note["on"]:
            _end, vid = heapq.heappop(active)
            heapq.heappush(free, vid)
        if not free:
            free.append(next_id)
            next_id += 1
        note["voice"] = heapq.heappop(free)
        heapq.heappush(active, (note["off"], note["voice"]))


# --------------------------------------------------------------------- bank


def colour_of(hue: str, notes: list[dict]):
    """Which number the renderer turns into a hue.

    ``track`` is what the example does. ``channel`` is the useful fallback: a
    format-0 MIDI has exactly one track, where every note would come out the
    same colour, and the channel is what separates the instruments there.
    ``auto`` takes the track unless it would separate nothing.
    """
    if hue == "channel":
        return lambda n: n["chan"]
    if hue == "track":
        return lambda n: n["track"]
    tracks = {n["track"] for n in notes}
    return (lambda n: n["track"]) if len(tracks) > 1 else (lambda n: n["chan"])


def note_table(fl2: Fl2) -> dict[str, list]:
    """The renderer's four lists, named as the Stage holds them."""
    if not fl2.n_ticks:
        return {}
    return {
        "Note.tick": fl2.n_ticks,
        "Note.len": fl2.n_lens,
        "Note.key": fl2.n_keys,
        "Note.trk": fl2.n_trks,
    }


def load_bank(path: Path) -> dict:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    sprites = list(data["sprites"])
    sprite_index = {name: i for i, name in enumerate(sprites)}
    presets: dict[tuple[int, int], dict] = {}
    for preset in data["presets"]:
        presets[(preset["bank"], preset["program"])] = {
            "sprite": sprite_index[preset["sprite"]],
            "notes": preset["notes"],
        }
    return {"sprites": sprites, "presets": presets, "sprite_index": sprite_index}


def bank_slots(bank: dict) -> tuple[dict, dict]:
    """The two tables a voice needs: each slot's root note, and each program's slots.

    A slot's *root* is the MIDI note it plays with no pitch effect at all. The
    bank stores a preset as ``notes[note] = [slot, semitones]``, so the root
    falls straight out of any row that uses that slot; that the rows of one
    slot agree is what makes the runtime's ``semitones = note - root`` work.
    """
    roots: dict[tuple[int, int], float] = {}
    slots_of: dict[tuple[int, int], tuple[int, list[int]]] = {}
    for key, preset in bank["presets"].items():
        sprite = preset["sprite"]
        slots: list[int] = []
        for note, (slot, semis) in enumerate(preset["notes"]):
            roots.setdefault((sprite, slot), note - semis)
            if slot not in slots:
                slots.append(slot)
        slots_of[key] = (sprite, slots)
    return roots, slots_of


# Scratch clamps the pitch effect to +/-360 tenths of a semitone, and TurboWarp
# does not. A slot further than this from the note would sound wrong on
# scratch.mit.edu, so allocation prefers slots inside it.
PITCH_LIMIT = 36.0


def apply_bank(fl2: Fl2, bank: dict) -> None:
    """Resolve every note-on to (sprite, slot, semitones) at build time.

    The slot is *allocated*, not looked up, which is scratch PFA's idea with the
    reason it exists: scratch-audio keeps exactly one player per sound asset per
    sprite -- `SoundBank.playSound` calls `player.stop()` when a different target
    asks for a sound that is already playing -- so two notes on one slot are not
    polyphony, the second one silences the first. Looking a note up in the bank
    gives each *pitch* its nearest sample and nothing else, so any program whose
    sample count is smaller than its polyphony loses voices: measured on the
    built-in song, 22% of note-ons stopped a note of a different pitch that was
    still sounding.

    Each note takes the best of its program's slots:

    1. a free one;
    2. or one whose occupant is the *same* note -- a restrike, and a piano
       damping its own string when the key is struck again is correct;
    3. nearest in pitch, so the sample is resampled as little as the program's
       own samples allow;
    4. and when every slot is playing something else, the one that frees soonest.

    Losing a voice is what is heard as "notes missing"; a sample resampled a few
    semitones is what a sound font does everywhere anyway. So the order is not
    negotiable.
    """
    program = [0] * 16
    roots, slots_of = bank_slots(bank)
    fl2.sprites = [0] * len(fl2.ticks)
    fl2.slots = [0] * len(fl2.ticks)
    fl2.semis = [0.0] * len(fl2.ticks)
    # (sprite, slot) -> (the tick it frees at, the note it is playing)
    occupied: dict[tuple[int, int], tuple[int, int]] = {}
    missing = 0
    stolen = 0
    restruck = 0
    for i, kind in enumerate(fl2.kinds):
        if kind == KIND_PROGRAM:
            program[fl2.chans[i]] = int(fl2.a[i])
        elif kind == KIND_ON:
            chan = fl2.chans[i]
            key = (128 if chan == 9 else 0, program[chan])
            if key not in slots_of:
                key = (0, program[chan]) if (0, program[chan]) in slots_of else (0, 0)
            if key not in slots_of:
                missing += 1
                continue
            sprite, slots = slots_of[key]
            note = int(fl2.a[i]) % 128
            tick = fl2.ticks[i]
            # The bank's own answer for this note, which the allocator only moves
            # away from when that slot is busy. Using it verbatim keeps a bank
            # whose rows do not fit `note - root` (a coarse/fine-tuned sample)
            # playing exactly what it asked for.
            ideal, ideal_semis = bank["presets"][key]["notes"][note]

            def rank(slot: int) -> tuple:
                holder = occupied.get((sprite, slot))
                busy = holder is not None and holder[0] > tick
                shift = abs(roots[(sprite, slot)] - note)
                return (
                    busy and holder[1] != note,      # cutting another note is worst
                    shift > PITCH_LIMIT,             # Scratch would clamp this pitch
                    shift,
                    # A tie means two slots are the same distance from the note,
                    # which for a drum kit is two *different* drums at the same
                    # pitch: the bank's own choice has to win or a snare becomes
                    # a kick.
                    slot != ideal,
                    (holder[0] - tick) if busy else 0,
                )

            # A drum kit is the one case where a slot is not a substitute for
            # another: the slots *are* the instruments (kick, snare, hat), all of
            # them rooted at their own key, so moving a hit to a free slot would
            # play a different drum. Cutting a decaying hit is what a drum does.
            slot = ideal if chan == 9 else min(slots, key=rank)
            holder = occupied.get((sprite, slot))
            if holder is not None and holder[0] > tick:
                if holder[1] == note:
                    restruck += 1
                else:
                    stolen += 1
            occupied[(sprite, slot)] = (tick + max(1, fl2.evlen[i]), note)
            fl2.sprites[i] = sprite
            fl2.slots[i] = slot
            fl2.semis[i] = ideal_semis if slot == ideal else note - roots[(sprite, slot)]
    fl2.meta["missing_presets"] = missing
    fl2.meta["stolen_slots"] = stolen
    fl2.meta["restrikes"] = restruck


# --------------------------------------------------------------------- I/O


def read_midi(path: Path) -> tuple[list[Message], int]:
    try:
        import mido
    except ImportError:  # pragma: no cover - environment problem, not a bug
        raise SystemExit("mido is required to read MIDI: pip install mido")

    mid = mido.MidiFile(str(path))
    messages: list[Message] = []
    for track_index, track in enumerate(mid.tracks):
        at = 0
        for msg in track:
            at += msg.time
            if msg.type == "note_on" and msg.velocity > 0:
                messages.append(Message(at, "on", msg.channel, msg.note, msg.velocity, track=track_index))
            elif msg.type == "note_off" or (msg.type == "note_on" and msg.velocity == 0):
                messages.append(Message(at, "off", msg.channel, msg.note, track=track_index))
            elif msg.type == "program_change":
                messages.append(Message(at, "program", msg.channel, program=msg.program, track=track_index))
            elif msg.type == "control_change":
                messages.append(Message(at, "cc", msg.channel, control=msg.control, value=msg.value, track=track_index))
            elif msg.type in ("pitchwheel", "pitch_bend"):
                # ``getattr(msg, "pitch", msg.value)`` would evaluate the default
                # eagerly and raise: a pitchwheel message has no ``value``.
                bend = msg.pitch if msg.type == "pitchwheel" else msg.value
                messages.append(Message(at, "bend", msg.channel, value=bend, track=track_index))
            elif msg.type == "set_tempo":
                messages.append(Message(at, "tempo", -1, value=round(60000000 / msg.tempo, 3), track=track_index))
    return messages, mid.ticks_per_beat


# --------------------------------------------------------------------- meta


def read_song_meta(source, path: Path | None = None) -> dict:
    """What the debug block shows about the file itself.

    Three things a MIDI carries that the player does not need to *play* it, and
    that the project cannot work out for itself once the notes are lists:

      * the sequence name -- the first ``track_name`` at tick 0 of the first
        track, which is where every sequencer writes the song's title. A file
        without one falls back to its own file name;
      * its size on disk, which is the only honest way to say "this is a big
        file" next to the loaded note count;
      * the lyrics: ``FF 05`` meta events, as (player tick, text) pairs. They
        sit at a tick, so the player shows the one the playhead is inside
        rather than the whole text at once.
    """
    try:
        import mido
    except ImportError:  # pragma: no cover - environment problem, not a bug
        raise SystemExit("mido is required to read MIDI: pip install mido")

    mid = mido.MidiFile(file=source) if hasattr(source, "read") else mido.MidiFile(str(source))
    title = ""
    lyrics: list[tuple[int, str]] = []
    for index, track in enumerate(mid.tracks):
        at = 0
        for msg in track:
            at += msg.time
            if msg.type == "track_name" and index == 0 and at == 0 and not title:
                title = str(msg.name).strip()
            elif msg.type == "lyrics":
                words = str(msg.text).strip()
                if words:
                    lyrics.append((at, words))
    if not title:
        title = Path(getattr(mid, "filename", "") or path or "song.mid").stem
    if path is not None:
        size = path.stat().st_size
    else:  # a paste: the bytes it decoded to are the file
        source.seek(0)
        size = len(source.read())
    lyrics.sort(key=lambda pair: pair[0])
    scale = TICKS_PER_BEAT / mid.ticks_per_beat
    return {
        "title": title,
        "kilobytes": max(1, round(size / 1024)),
        "lyrics": [[round(tick * scale), words] for tick, words in lyrics],
    }


def lyric_lists(meta: dict) -> dict:
    """The two lists the HUD walks: a tick and the words that start at it."""
    lyrics = meta.get("lyrics") or []
    return {
        "Song.LyricTick": [pair[0] for pair in lyrics],
        "Song.LyricText": [pair[1] for pair in lyrics],
    }


def write(fl2: Fl2, out: Path, prefix: str = "") -> dict:
    """Write the lists under dotted names, which is how a Stage list is spelled.

    ``Player.EvTick`` rather than ``EvTick``: a bare name used with list methods
    is a *sprite* list, and the bank sprites are the ones reading these.
    """
    out.mkdir(parents=True, exist_ok=True)
    lists = {
        "Player.EvTick": fl2.ticks,
        "Player.EvKind": fl2.kinds,
        "Player.EvChan": fl2.chans,
        "Player.EvA": fl2.a,
        "Player.EvB": fl2.b,
        "Player.EvVoice": fl2.voice,
    }
    if fl2.evlen:
        lists["Player.EvLen"] = fl2.evlen
    if fl2.trks:
        lists["Player.EvTrk"] = fl2.trks
    if fl2.sprites is not None:
        lists["Player.EvSprite"] = fl2.sprites
        lists["Player.EvSlot"] = fl2.slots
        lists["Player.EvSemis"] = fl2.semis
    lists.update(note_table(fl2))
    lists.update(lyric_lists(fl2.meta))
    for name, values in lists.items():
        (out / f"{name}.jsa").write_text(json.dumps(values, separators=(",", ":")), encoding="utf-8")
    index = [fl2.ticks[i] for i in range(0, len(fl2.ticks), 512)]
    (out / "Player.EvIndex.jsa").write_text(json.dumps(index, separators=(",", ":")), encoding="utf-8")
    meta = dict(fl2.meta, events=len(fl2.ticks), names=sorted(lists))
    (out / "fl2.json").write_text(json.dumps(meta, indent=1), encoding="utf-8")
    (out / "song.jsw").write_text(render_song(meta, prefix), encoding="utf-8")
    return meta


def render_song(meta: dict, prefix: str = "") -> str:
    """The fragment a project includes: the lists, and what the player needs."""
    names = meta["names"] + ["Player.EvIndex"]
    lines = ["// Generated by tools/midi_to_fl2.py -- do not edit.", ""]
    lines += [f'import "{prefix}{name}.jsa";' for name in names]
    lines.append("")
    lines.append(f"public Song.tempo = {meta.get('tempo', 120)};")
    lines.append(f"public Song.pool = {max(1, meta.get('polyphony', 1))};")
    lines.append(f"public Song.ticks = {meta.get('ticks', 0)};")
    lines.append(f"public Song.notes = {meta.get('notes', 0)};")
    lines.append(f"public Song.hueStep = {meta.get('hue_step', 2.3)};")
    # The debug block's last three facts: what the file was called, how big it
    # was, and how many lyric lines came with it. `Song.title` is also what a
    # runtime paste overwrites -- see `loader.jsw` -- and `Song.baked` is the
    # copy the built-in is put back to, because the hex in the project carries
    # no file name to recover.
    title = json.dumps(str(meta.get("title", "")))
    lines.append(f"public Song.title = {title};")
    lines.append(f"public Song.baked = {title};")
    lines.append(f"public Song.kb = {int(meta.get('kilobytes', 0))};")
    lines.append(f"public Song.lyricCount = {len(meta.get('lyrics') or [])};")
    lines.append("")
    return "\n".join(lines)


def read(out: Path) -> Fl2:
    def load(name: str):
        path = out / f"{name}.jsa"
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None

    fl2 = Fl2(
        ticks=load("Player.EvTick") or [],
        kinds=load("Player.EvKind") or [],
        chans=load("Player.EvChan") or [],
        a=load("Player.EvA") or [],
        b=load("Player.EvB") or [],
        voice=load("Player.EvVoice") or [],
        evlen=load("Player.EvLen") or [],
        trks=load("Player.EvTrk") or [],
        sprites=load("Player.EvSprite"),
        slots=load("Player.EvSlot"),
        semis=load("Player.EvSemis"),
        n_ticks=load("Note.tick") or [],
        n_lens=load("Note.len") or [],
        n_keys=load("Note.key") or [],
        n_trks=load("Note.trk") or [],
    )
    meta_path = out / "fl2.json"
    fl2.meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}
    return fl2


def notes_of(fl2: Fl2) -> list[dict]:
    """Re-pair the stream, so ``dump`` checks what the player will actually see."""
    live: dict[int, dict] = {}
    out = []
    for i, kind in enumerate(fl2.kinds):
        if kind == KIND_ON:
            live[fl2.voice[i]] = {
                "tick": fl2.ticks[i],
                "note": int(fl2.a[i]),
                "channel": fl2.chans[i],
                "velocity": fl2.b[i],
                "voice": fl2.voice[i],
                "track": fl2.trks[i] if fl2.trks else 0,
            }
            if fl2.sprites is not None:
                live[fl2.voice[i]]["sprite"] = fl2.sprites[i]
                live[fl2.voice[i]]["slot"] = fl2.slots[i]
                live[fl2.voice[i]]["semis"] = fl2.semis[i]
        elif kind == KIND_OFF and fl2.voice[i] in live:
            note = live.pop(fl2.voice[i])
            note["length"] = fl2.ticks[i] - note["tick"]
            out.append(note)
    out.extend(live.values())
    out.sort(key=lambda n: (n["tick"], n["voice"]))
    return out


# --------------------------------------------------------------------- CLI


def cmd_convert(args) -> int:
    midi = Path(args.midi).expanduser()
    messages, ppq = read_midi(midi)
    fl2 = expand(messages, args.ticks_per_beat, ppq, args.hue, args.thin,
                 args.voices, args.sustain)
    fl2.meta.update(read_song_meta(midi, midi))
    if args.bank:
        apply_bank(fl2, load_bank(Path(args.bank).expanduser()))
    out = Path(args.out)
    meta = write(fl2, out, args.prefix)
    # scratch.mit.edu clamps the pitch effect to +/-360 units = +/-36 semitones,
    # so a note that needs more than that lands wrong on Scratch even though it
    # is fine in TurboWarp (miscLimits off). Report, do not silently clamp.
    far = [s for s in (fl2.semis or []) if abs(s) > 36]
    if far:
        print(f"  {len(far)} note-ons need more than +/-36 semitones "
              f"(worst {min(far):+.1f}); Scratch will clamp those")
    print(
        f"{Path(args.midi).name}: {meta['notes']} notes, {meta['events']} events, "
        f"{meta['ticks']} ticks, polyphony {meta['polyphony']}"
    )
    if args.thin or args.voices:
        print(f"  thinned: {args.thin or '-'} note-ons/tick, {args.voices or '-'} voices, "
              f"sustained >= {args.sustain} ticks")
    if args.bank:
        print(f"  baked against {args.bank}; missing presets {meta.get('missing_presets', 0)}, "
              f"{meta.get('stolen_slots', 0)} notes stole a busy slot, "
              f"{meta.get('restrikes', 0)} restruck their own")
    print(f"  -> {out}")
    return 0


def cmd_meta(args) -> int:
    """Refresh only what the debug block shows, from the MIDI or from its hex.

    A song is baked once and its lists never change, but the title, the size and
    the lyrics live in the *file*, and a project's own `Song.hex` is a full copy
    of it. So a bake that predates the debug block can be brought up to date
    without the original file -- which is the usual case, because the hex in the
    project is the only surviving copy of a song that was never committed.
    """
    directory = Path(args.out)
    meta_path = directory / "fl2.json"
    if not meta_path.exists():
        print(f"{directory}: not a baked song (no fl2.json)", file=sys.stderr)
        return 1
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    if args.hex:
        # Either bare hex or the `Song.hex` list `midi_to_hex.py` writes -- in
        # which case the chunks are the quoted strings and the rest is JSW.
        text = Path(args.hex).read_text(encoding="utf-8")
        chunks = re.findall(r'"([0-9A-Fa-f]+)"', text) or [text]
        raw = bytes.fromhex("".join(chunks))
        found = read_song_meta(io.BytesIO(raw))
    elif args.midi:
        midi = Path(args.midi).expanduser()
        found = read_song_meta(midi, midi)
    else:
        print("one of --hex or --midi is required", file=sys.stderr)
        return 2
    meta.update(found)
    meta["names"] = sorted(set(meta.get("names") or []) | set(lyric_lists(meta)))
    for name, values in lyric_lists(meta).items():
        (directory / f"{name}.jsa").write_text(
            json.dumps(values, separators=(",", ":")), encoding="utf-8")
    meta_path.write_text(json.dumps(meta, indent=1), encoding="utf-8")
    (directory / "song.jsw").write_text(render_song(meta, args.prefix), encoding="utf-8")
    print(f"{directory}: {found['title']!r}, {found['kilobytes']} kB, "
          f"{len(found['lyrics'])} lyric lines")
    return 0


def cmd_reallocate(args) -> int:
    """Re-voice an already baked song: new slots, same notes.

    The allocator runs at bake time, but a song is baked once and the bank can
    outlive it -- and a song baked before the allocator existed has one voice per
    pitch instead of one per program. This walks the event stream again with the
    current bank and rewrites only `EvSprite`/`EvSlot`/`EvSemis`.
    """
    directory = Path(args.directory)
    fl2 = read(directory)
    if fl2.sprites is None:
        print(f"{directory}: no sample data, nothing to allocate (bake with --bank first)")
        return 1
    bank = load_bank(Path(args.bank).expanduser())
    apply_bank(fl2, bank)
    write(fl2, directory, args.prefix)
    far = [s for s in fl2.semis if abs(s) > PITCH_LIMIT]
    print(f"{directory}: re-voiced, {fl2.meta.get('stolen_slots', 0)} notes stole a busy slot, "
          f"{len(far)} need more than +/-{PITCH_LIMIT:.0f} semitones")
    return 0


def cmd_dump(args) -> int:
    fl2 = read(Path(args.directory))
    notes = notes_of(fl2)
    if args.json:
        print(json.dumps(notes, indent=1))
        return 0
    print(f"{len(notes)} notes")
    for note in notes[:20]:
        print(
            f"  t{note['tick']:6d} +{note['length']:5d} n{note['note']:3d} ch{note['channel']:2d} "
            f"v{note['velocity']:3d} id{note['voice']:3d}"
        )
    return 0


def cmd_compare(args) -> int:
    """Dump must agree with the MIDI it came from, note for note."""
    messages, ppq = read_midi(Path(args.midi).expanduser())
    expected = expand(messages, args.ticks_per_beat, ppq)
    actual = read(Path(args.directory))
    want, got = notes_of(expected), notes_of(actual)
    problems = []
    if len(want) != len(got):
        problems.append(f"note count {len(want)} != {len(got)}")
    for a, b in zip(want, got):
        for key in ("tick", "length", "note", "channel", "velocity"):
            if a[key] != b[key]:
                problems.append(f"t{a['tick']}: {key} {a[key]} != {b[key]}")
                break
    for problem in problems[:10]:
        print("  " + problem)
    print("ok" if not problems else f"{len(problems)} differences")
    return 0 if not problems else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    conv = sub.add_parser("convert", help="MIDI -> Ev*.jsa")
    conv.add_argument("midi")
    conv.add_argument("-o", "--out", required=True)
    conv.add_argument(
        "--hue", choices=("channel", "track", "auto"), default="channel",
        help="note colour source: MIDI channel (default), track, or auto",
    )
    conv.add_argument(
        "--thin", type=int, default=0, metavar="N",
        help="keep at most N note-ons per tick (a black MIDI needs this)",
    )
    conv.add_argument(
        "--voices", type=int, default=0, metavar="N",
        help="ceiling on simultaneous notes; the longest ones are kept first",
    )
    conv.add_argument(
        "--sustain", type=int, default=4, metavar="TICKS",
        help="length at which a note counts as sustained and is kept first",
    )
    conv.add_argument("--bank", default=None, help="bank.json, to resolve samples now")
    conv.add_argument("--ticks-per-beat", type=int, default=TICKS_PER_BEAT)
    conv.add_argument("--prefix", default="", help="path prefix for the imports, from the including file")
    conv.set_defaults(func=cmd_convert)

    real = sub.add_parser("reallocate", help="re-voice a baked song against a bank")
    real.add_argument("directory")
    real.add_argument("--bank", required=True, help="bank.json")
    real.add_argument("--prefix", default="song/", help="path prefix for the imports")
    real.set_defaults(func=cmd_reallocate)

    meta = sub.add_parser("meta", help="refresh the title/size/lyrics of a baked song")
    meta.add_argument("-o", "--out", required=True, help="the baked song's directory")
    meta.add_argument("--hex", default=None, help="a MIDI as hex text (midi_to_hex.py)")
    meta.add_argument("--midi", default=None, help="or the MIDI file itself")
    meta.add_argument("--prefix", default="song/", help="path prefix for the imports")
    meta.set_defaults(func=cmd_meta)

    dump = sub.add_parser("dump", help="read Ev*.jsa back as notes")
    dump.add_argument("directory")
    dump.add_argument("--json", action="store_true")
    dump.set_defaults(func=cmd_dump)

    comp = sub.add_parser("compare", help="check a dump against the MIDI")
    comp.add_argument("midi")
    comp.add_argument("directory")
    comp.add_argument("--ticks-per-beat", type=int, default=TICKS_PER_BEAT)
    comp.set_defaults(func=cmd_compare)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
