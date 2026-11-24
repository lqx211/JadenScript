#!/usr/bin/env python3
"""Turn a SoundFont into a NotePlayer V5 sample bank.

Usage::

    python3 tools/sf2_bank.py inspect ~/Documents/MuseScore3/SoundFonts/gm.sf2
    python3 tools/sf2_bank.py build   ~/Documents/MuseScore3/SoundFonts/gm.sf2

``inspect`` prints what the file actually contains: presets, zones, sample and
loop lengths, and the size a baking policy would produce. ``build`` writes the
bank a NotePlayer V5 project imports.

Why a bake step at all: Scratch sounds have pitch/pan/volume effects and
nothing else -- there are no loop points. The SoundFonts here are tiny-loop
fonts (``gm.sf2``: samples are 91 ms long on average, loops 4 ms), so playing
one as-is gives a click, not a note. A looped sample is therefore rendered as
``attack + loop repeated to N seconds + fade``, which is what a hardware
sampler does with the same data; one-shots keep their natural length.

The file is still finite, and a MIDI is not: in a real one a few percent of the
notes outlast any fixed bake (a 13 s seashore, a 35 s string pad, a 6 s sung
note), and those are the notes a listener notices. So the voice gets
``VoiceDur`` -- how long each slot's audio lasts -- and replays the file when it
runs out, which `Sound.playSound` does by restarting it on the same target.
That is why ``--sustain`` is worth more than a second or two: every replay is a
re-attack (the beginning of the file), so the bake length is how often the
instrument is re-articulated inside one held note.

The table this writes is what the compiler bakes into the event stream, so the
runtime never searches for a zone::

    preset, note -> (sprite, sound slot, semitones)

``semitones`` is Scratch's pitch effect unit, so a voice clone only needs
``Sound.setEffect("PITCH", semitones + bend)``.
"""

from __future__ import annotations

import argparse
import json
import shutil
import struct
import sys
import wave
from array import array
from dataclasses import dataclass, field
from pathlib import Path

# ---------------------------------------------------------------- SF2 parsing

GEN_PAN = 17
GEN_INSTRUMENT = 41
GEN_KEY_RANGE = 43
GEN_VEL_RANGE = 44
GEN_ATTENUATION = 48
GEN_COARSE_TUNE = 51
GEN_FINE_TUNE = 52
GEN_SAMPLE_ID = 53
GEN_SAMPLE_MODES = 54
GEN_SCALE_TUNING = 56
GEN_OVERRIDING_ROOT_KEY = 58

_SIGNED_GENS = {GEN_PAN, GEN_COARSE_TUNE, GEN_FINE_TUNE}


def _chunks(buf: bytes, off: int, end: int):
    while off + 8 <= end:
        cid = buf[off : off + 4].decode("latin-1")
        size = struct.unpack_from("<I", buf, off + 4)[0]
        yield cid, off + 8, size
        off += 8 + size + (size & 1)


def _find_list(buf: bytes, off: int, end: int, name: str):
    for cid, body, size in _chunks(buf, off, end):
        if cid != "LIST":
            continue
        if buf[body : body + 4].decode("latin-1") == name:
            return body + 4, body + size
        found = _find_list(buf, body + 4, body + size, name)
        if found:
            return found
    return None


def _cstr(buf: bytes, off: int, length: int) -> str:
    return buf[off : off + length].split(b"\0")[0].decode("latin-1", "replace").strip()


@dataclass
class Sample:
    index: int
    name: str
    start: int
    end: int
    loop_start: int
    loop_end: int
    rate: int
    original_pitch: int
    pitch_correction: int
    sample_type: int

    @property
    def frames(self) -> int:
        return max(0, self.end - self.start)

    @property
    def seconds(self) -> float:
        return self.frames / self.rate if self.rate else 0.0

    @property
    def has_loop(self) -> bool:
        return 0 < self.loop_start < self.loop_end <= self.end


@dataclass
class Zone:
    key_lo: int = 0
    key_hi: int = 127
    vel_lo: int = 0
    vel_hi: int = 127
    sample: int = -1
    coarse: int = 0
    fine: int = 0
    scale: int = 100
    root_override: int | None = None
    attenuation: float = 0.0
    pan: float = 0.0
    loop_mode: int = 0

    def under(self, outer: "Zone") -> "Zone":
        """``outer`` (preset level) on top of ``self`` (instrument level)."""
        return Zone(
            key_lo=max(self.key_lo, outer.key_lo),
            key_hi=min(self.key_hi, outer.key_hi),
            vel_lo=max(self.vel_lo, outer.vel_lo),
            vel_hi=min(self.vel_hi, outer.vel_hi),
            sample=self.sample,
            coarse=outer.coarse or self.coarse,
            fine=outer.fine or self.fine,
            scale=outer.scale if outer.scale != 100 else self.scale,
            root_override=outer.root_override if outer.root_override is not None else self.root_override,
            attenuation=outer.attenuation or self.attenuation,
            pan=outer.pan or self.pan,
            loop_mode=outer.loop_mode or self.loop_mode,
        )


@dataclass
class Preset:
    index: int
    name: str
    bank: int
    program: int
    zones: list[Zone] = field(default_factory=list)


class Sf2:
    def __init__(self, path: Path):
        self.path = path
        self.buf = path.read_bytes()
        if self.buf[:4] not in (b"RIFF", b"RIFX"):
            raise ValueError(f"{path}: not a RIFF file")
        pdta = _find_list(self.buf, 12, len(self.buf), "pdta")
        if not pdta:
            raise ValueError(f"{path}: no pdta chunk (not a SoundFont)")
        self._sub = {cid: (body, size) for cid, body, size in _chunks(self.buf, *pdta)}
        sdta = _find_list(self.buf, 12, len(self.buf), "sdta")
        if sdta:
            self._sub.update({cid: (body, size) for cid, body, size in _chunks(self.buf, *sdta)})
        if "smpl" not in self._sub:
            raise ValueError(f"{path}: no smpl chunk (compressed or empty font)")
        self.samples = self._read_samples()
        self.presets = self._read_presets()

    def _count(self, cid: str, stride: int) -> int:
        return self._sub[cid][1] // stride

    def _gens(self, table: str, first: int, count: int) -> dict[int, int]:
        body, _ = self._sub[table]
        gens: dict[int, int] = {}
        for i in range(first, first + count):
            oper, amount = struct.unpack_from("<HH", self.buf, body + i * 4)
            if oper in _SIGNED_GENS:
                amount = struct.unpack("<h", struct.pack("<H", amount))[0]
            gens[oper] = amount
        return gens

    def _read_samples(self) -> list[Sample]:
        body, size = self._sub["shdr"]
        out = []
        for i in range(max(0, size // 46 - 1)):
            off = body + i * 46
            start, end, ls, le, rate = struct.unpack_from("<IIIII", self.buf, off + 20)
            out.append(
                Sample(
                    index=i,
                    name=_cstr(self.buf, off, 20),
                    start=start,
                    end=end,
                    loop_start=ls,
                    loop_end=le,
                    rate=rate or 44100,
                    original_pitch=self.buf[off + 40],
                    pitch_correction=struct.unpack_from("<b", self.buf, off + 41)[0],
                    sample_type=struct.unpack_from("<H", self.buf, off + 44)[0],
                )
            )
        return out

    def _read_presets(self) -> list[Preset]:
        phdr, phdr_size = self._sub["phdr"]
        ninst = self._count("inst", 22) - 1
        npresets = phdr_size // 38 - 1
        presets = []
        for i in range(max(0, npresets)):
            off = phdr + i * 38
            first = struct.unpack_from("<H", self.buf, off + 24)[0]
            last = struct.unpack_from("<H", self.buf, off + 62)[0] if i + 1 < npresets else self._count("pbag", 4)
            preset = Preset(
                index=i,
                name=_cstr(self.buf, off, 20),
                bank=struct.unpack_from("<H", self.buf, off + 22)[0],
                program=struct.unpack_from("<H", self.buf, off + 20)[0],
            )
            preset.zones = self._preset_zones(first, last, ninst)
            presets.append(preset)
        return presets

    def _bag_gens(self, table: str, gen_table: str, bag: int) -> dict[int, int]:
        body, _ = self._sub[table]
        gen_index = struct.unpack_from("<H", self.buf, body + bag * 4)[0]
        # The last bag of a preset/instrument still ends where the *next* bag in
        # the global table starts, so the range cannot stop at ``last``.
        if bag + 1 < self._count(table, 4):
            next_gen = struct.unpack_from("<H", self.buf, body + (bag + 1) * 4)[0]
        else:
            next_gen = self._count(gen_table, 4)
        return self._gens(gen_table, gen_index, next_gen - gen_index)

    def _preset_zones(self, first: int, last: int, ninst: int) -> list[Zone]:
        inst, _ = self._sub["inst"]
        zones: list[Zone] = []
        outer = Zone()
        for bag in range(first, last):
            gens = self._bag_gens("pbag", "pgen", bag)
            if GEN_INSTRUMENT not in gens:
                outer = _zone_from_gens(gens, outer)
                continue
            inst_index = gens[GEN_INSTRUMENT]
            if not 0 <= inst_index < ninst:
                continue
            zones.extend(self._instrument_zones(inst_index, inst, _zone_from_gens(gens, Zone())))
        return zones

    def _instrument_zones(self, inst_index: int, inst: int, outer: Zone) -> list[Zone]:
        first = struct.unpack_from("<H", self.buf, inst + inst_index * 22 + 20)[0]
        last = struct.unpack_from("<H", self.buf, inst + (inst_index + 1) * 22 + 20)[0]
        global_zone = Zone()
        out = []
        for bag in range(first, last):
            gens = self._bag_gens("ibag", "igen", bag)
            if GEN_SAMPLE_ID not in gens:
                global_zone = _zone_from_gens(gens, global_zone)
                continue
            zone = _zone_from_gens(gens, global_zone)
            zone.sample = gens[GEN_SAMPLE_ID]
            out.append(zone.under(outer))
        return out

    def semitones(self, zone: Zone, note: int) -> float:
        """How far ``note`` is from the pitch this sample sounds at, in semitones.

        ``chPitchCorrection`` is *subtracted* from the key number. That sign is
        not guesswork: rendering the same program and note with FluidSynth and
        measuring both puts the error at exactly ``2 * correction`` cents when it
        is added, so a font that stores 48 adds up to a semitone of error --
        Voice Oohs, Bassoon and Ocarina were all audibly out of tune.
        """
        sample = self.samples[zone.sample]
        root = zone.root_override if zone.root_override is not None else sample.original_pitch
        if not 0 <= root <= 127:
            root = 60
        root_pitch = root - sample.pitch_correction / 100.0
        semis = (note - root_pitch) * zone.scale / 100.0 + zone.coarse + zone.fine / 100.0
        return round(semis, 3)

    def lut(self, preset: Preset, velocity: int) -> list[tuple[int, float, int]]:
        """128 entries of (sample, semitones, loop mode); sample -1 is silence."""
        out: list[tuple[int, float, int]] = []
        for note in range(128):
            match = None
            for zone in preset.zones:
                if not zone.key_lo <= note <= zone.key_hi:
                    continue
                if not zone.vel_lo <= velocity <= zone.vel_hi:
                    continue
                if match is None or (zone.vel_lo, -(zone.key_hi - zone.key_lo)) > (match.vel_lo, -(match.key_hi - match.key_lo)):
                    match = zone
            out.append((-1, 0.0, 0) if match is None else (match.sample, self.semitones(match, note), match.loop_mode))
        return out


def _zone_from_gens(gens: dict[int, int], base: Zone) -> Zone:
    zone = Zone(**base.__dict__)
    if GEN_KEY_RANGE in gens:
        zone.key_lo, zone.key_hi = gens[GEN_KEY_RANGE] & 0xFF, (gens[GEN_KEY_RANGE] >> 8) & 0xFF
    if GEN_VEL_RANGE in gens:
        zone.vel_lo, zone.vel_hi = gens[GEN_VEL_RANGE] & 0xFF, (gens[GEN_VEL_RANGE] >> 8) & 0xFF
    if GEN_COARSE_TUNE in gens:
        zone.coarse = gens[GEN_COARSE_TUNE]
    if GEN_FINE_TUNE in gens:
        zone.fine = gens[GEN_FINE_TUNE]
    if GEN_SCALE_TUNING in gens:
        zone.scale = gens[GEN_SCALE_TUNING]
    if GEN_OVERRIDING_ROOT_KEY in gens:
        zone.root_override = gens[GEN_OVERRIDING_ROOT_KEY] & 0x7F
    if GEN_ATTENUATION in gens:
        zone.attenuation = gens[GEN_ATTENUATION] / 10.0
    if GEN_PAN in gens:
        zone.pan = gens[GEN_PAN] / 10.0
    if GEN_SAMPLE_MODES in gens:
        zone.loop_mode = gens[GEN_SAMPLE_MODES]
    return zone


# ------------------------------------------------------------------- baking


def pcm_of(sf2: Sf2, sample: Sample) -> array:
    body, size = sf2._sub["smpl"]
    data = sf2.buf[body : body + size]
    out = array("h")
    out.frombytes(data[sample.start * 2 : sample.end * 2])
    if sys.byteorder == "big":
        out.byteswap()
    return out


#: The GM programs that are a *continuous texture* rather than a note: seashore,
#: helicopter, applause. They are the ones the sound font gives a long looped
#: noise sample with no transient -- and, measured on ``gm.sf2``, the ones that
#: arrive at full level in the first few milliseconds, where a flute takes 60 ms
#: to get there and a string section 30. A wall of noise that starts instantly
#: reads as "too loud", and in a mix it is what masks everything else, so these
#: are the one family shaped at bake time: a slow onset and a few dB of headroom.
#: Nothing else is touched -- a gunshot or a fret noise is a percussive effect
#: whose whole point is the transient.
AMBIENCE = {122, 124, 126}


def shape_of(preset: Preset, fade: float, gain: float) -> tuple[float, float]:
    """The ``(fade-in seconds, linear gain)`` a preset's samples are baked with."""
    if preset.bank == 0 and preset.program in AMBIENCE:
        return fade, gain
    return 0.002, 1.0


def bake(sf2: Sf2, sample: Sample, loop_mode: int, seconds: float,
         fade_in: float = 0.002, gain: float = 1.0) -> tuple[array, bool]:
    """``attack + loop repeated`` for a looped zone, the sample as-is otherwise."""
    pcm = pcm_of(sf2, sample)
    extended = False
    if loop_mode in (1, 3) and sample.has_loop:
        ls = sample.loop_start - sample.start
        le = sample.loop_end - sample.start
        loop = pcm[ls:le]
        target = max(len(pcm), int(seconds * sample.rate))
        attack = pcm[:ls]
        need = target - len(attack)
        if loop and need > 0:
            body = array("h")
            for _ in range(need // len(loop) + 1):
                body.extend(loop)
            pcm = attack + body[:need]
            extended = True
    _fade(pcm, sample.rate, fade_in, gain)
    return pcm, extended


def _fade(pcm: array, rate: int, fade_in: float = 0.002, gain: float = 1.0) -> None:
    """Ramp the first ``fade_in`` seconds up from silence, then trim the level.

    The 2 ms default is an anti-click ramp, not an envelope: a piano or a flute
    brings its own attack and lengthening this would round it off. ``fade_in`` is
    how an *ambience* sample -- which has no attack of its own, only a step from
    silence to a wall of noise -- is given one. ``gain`` is the level trim that
    keeps that wall from masking the instruments.
    """
    start = min(len(pcm), int(max(fade_in, 0.002) * rate))
    for i in range(start):
        pcm[i] = int(pcm[i] * (i / start))
    fade_out = min(len(pcm), int(0.020 * rate))
    if fade_out:
        for i in range(fade_out):
            idx = len(pcm) - 1 - i
            pcm[idx] = int(pcm[idx] * (i / fade_out))
    if gain != 1.0:
        for i in range(len(pcm)):
            pcm[i] = int(pcm[i] * gain)


def write_wav(path: Path, pcm: array, rate: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = array("h", pcm)
    if sys.byteorder == "big":
        data.byteswap()
    with wave.open(str(path), "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(rate)
        out.writeframes(data.tobytes())


# -------------------------------------------------------------- scratch piano

#: Where Scratch's own instrument samples live. The music extension plays them
#: through its own engine, which caps a project at 30 simultaneous notes on
#: scratch.mit.edu (`CONCURRENCY_LIMIT`, enforced when `miscLimits` is on) --
#: far below what a MIDI needs. Baking the same eight multi-samples into this
#: bank instead keeps the sound and drops the limit: they go through the same
#: voice system as every other preset.
MUSIC_ASSETS = Path("scratch-vm-develop/src/extensions/scratch3_music/assets/instruments/1-piano")

#: The multi-samples the music extension uses for its piano, and the note each
#: one is recorded at.
PIANO_SAMPLES = (24, 36, 48, 60, 72, 84, 96, 108)

#: GM programs 0..7 are the piano family: three acoustics, two electrics, a
#: harpsichord and a clavinet. They are the ones `MIDI_INSTRUMENTS` maps onto
#: Scratch's piano as well.
PIANO_PROGRAMS = range(8)


class DecoderMissing(RuntimeError):
    pass


def decode_audio(path: Path, out: Path) -> tuple[int, array]:
    """An MP3 to mono 16-bit PCM, with whatever the machine has.

    Scratch ships its instrument samples as MP3. There is no MP3 decoder in the
    standard library, so this shells out to `afconvert` (macOS) or `ffmpeg`; a
    machine with neither keeps the sound font's piano and says so.
    """
    import subprocess

    out.parent.mkdir(parents=True, exist_ok=True)
    if shutil.which("afconvert"):
        subprocess.run(["afconvert", "-f", "WAVE", "-d", "LEI16@44100", "-c", "1",
                        str(path), str(out)], check=True, capture_output=True)
    elif shutil.which("ffmpeg"):
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(path),
                        "-ac", "1", "-ar", "44100", "-sample_fmt", "s16", str(out)],
                       check=True, capture_output=True)
    else:
        raise DecoderMissing(
            "no MP3 decoder found: install ffmpeg, or leave --piano off to keep "
            "the sound font's piano")
    with wave.open(str(out), "rb") as handle:
        rate = handle.getframerate()
        frames = handle.readframes(handle.getnframes())
    pcm = array("h")
    pcm.frombytes(frames)
    if sys.byteorder == "big":
        pcm.byteswap()
    return rate, pcm


PIANO_SPRITE = "synth.piano"


def _encode_mp3(wav: Path, mp3: Path, bitrate: int) -> None:
    """A WAV to mono MP3, with ffmpeg. macOS's `afconvert` cannot encode MP3."""
    import subprocess

    if not shutil.which("ffmpeg"):
        raise DecoderMissing(
            "no MP3 encoder found: install ffmpeg, or pass --mp3 0 to keep WAV")
    mp3.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(wav),
                    "-ac", "1", "-c:a", "libmp3lame", "-b:a", f"{bitrate}k", str(mp3)],
                   check=True, capture_output=True)
    wav.unlink()


def _trim(pcm: array, rate: int, seconds: float) -> array:
    """`pcm` cut to `seconds`, with a 15 ms fade so the cut cannot click.

    A piano sample has a long tail and the release envelope in the voice script
    finishes what is left, but a cut in the middle of a cycle is a step in the
    waveform -- a click on *every* note. The fade is at the end and short enough
    to be inaudible on its own.
    """
    keep = min(len(pcm), int(rate * seconds))
    if keep < len(pcm):
        fade = min(int(rate * 0.015), keep)
        for i in range(fade):
            pcm[keep - fade + i] = int(pcm[keep - fade + i] * (fade - i) / fade)
        pcm = pcm[:keep]
    return pcm


def substitute_keys(report: dict, sound_files: dict, out: Path,
                    samples: list[tuple[int, Path]], trim: float = 2.5,
                    mp3_bitrate: int = 96, label: str = "keys") -> int:
    """Point the piano programs at a per-note or multi-sample piano.

    ``samples`` is ``(midi note, file)``, one entry per *slot*: the slot order is
    the file order, and a note the set does not cover is played by the nearest
    slot with the difference as a pitch correction -- so eight multi-samples and
    128 per-note samples take the same path here.

    Sounds are named by *slot*, not by the note each sample was recorded at: the
    voice script plays ``"s" + <slot>`` and the note -> slot table stores the
    ordinal. Every other preset in the bank is named that way too, so a
    per-note name here is a sprite whose sounds all fail to resolve -- silent
    piano, no error anywhere.
    """
    sprite = PIANO_SPRITE
    folder = out / "sounds" / sprite
    if folder.exists():
        for stale in folder.iterdir():
            stale.unlink()               # a re-bake with a different layout
    notes = [note for note, _ in samples]
    files = []
    for slot, (recorded, path) in enumerate(samples):
        if not path.exists():
            raise SystemExit(f"{path} is missing; point --piano/--keys at the piano samples")
        wav = folder / f"s{slot}.wav"
        rate, pcm = decode_audio(path, wav)
        pcm = _trim(pcm, rate, trim)
        write_wav(wav, pcm, rate)
        target = wav
        if mp3_bitrate > 0:
            target = folder / f"s{slot}.mp3"
            _encode_mp3(wav, target, mp3_bitrate)
        files.append({"name": f"s{slot}", "file": (Path("sounds") / sprite / target.name).as_posix(),
                      "rate": rate, "frames": len(pcm), "source": f"{label}/{path.name}",
                      "extended": False})
    report["sprites"][sprite] = {
        "sounds": files,
        "count": len(files),
        "bytes": sum((out / f["file"]).stat().st_size for f in files),
    }
    # `render_classes` walks this, so the sprite's imports and its copy of the
    # voice script are written from here like any other
    sound_files[sprite] = files
    replaced = 0
    for preset in report["presets"]:
        if preset["bank"] != 0 or preset["program"] not in PIANO_PROGRAMS:
            continue
        preset["sprite"] = sprite
        preset["sustaining"] = True
        preset["notes"] = []
        for note in range(128):
            nearest = min(range(len(notes)), key=lambda i: abs(notes[i] - note))
            preset["notes"].append([nearest, round(note - notes[nearest], 2)])
        replaced += 1
    return replaced


def substitute_piano(report: dict, sound_files: dict, out: Path, source: Path,
                     trim: float = 2.5) -> int:
    """Scratch's own eight piano multi-samples, as the music extension has them."""
    samples = [(note, source / f"{note}.mp3") for note in PIANO_SAMPLES]
    return substitute_keys(report, sound_files, out, samples, trim, mp3_bitrate=0,
                           label="music/1-piano")


def read_key_samples(source: Path, base: int = 0) -> list[tuple[int, Path, str]]:
    """``(note, extracted file, original name)`` for a per-key sample set.

    Two layouts, both of them something a real project ships: a directory of
    ``1.wav`` .. ``128.wav``, and an ``.sb3`` whose sprite has one sound per key
    named that way (the 3D keyboard example does exactly this -- its ``発音機構``
    sprite is 128 samples, one per MIDI note, and ``1.wav`` is note 0).
    """
    import zipfile

    found: list[tuple[int, Path, str]] = []
    if source.is_dir():
        for path in sorted(source.iterdir()):
            if path.suffix.lower() not in (".wav", ".mp3", ".ogg"):
                continue
            if not path.stem.isdigit():
                raise SystemExit(f"{path.name}: a per-key set needs the samples named by number")
            found.append((base + int(path.stem) - 1, path, path.name))
    else:
        import json
        import tempfile

        temp = Path(tempfile.mkdtemp(prefix="piano-keys-"))
        with zipfile.ZipFile(source) as archive:
            project = json.loads(archive.read("project.json"))
            best = None
            for target in project["targets"]:
                sounds = target.get("sounds") or []
                named = [x for x in sounds if str(x.get("name", "")).isdigit()]
                if named and (best is None or len(named) > len(best[0])):
                    best = (named, target["name"])
            if best is None:
                raise SystemExit(f"{source.name}: no sprite with sounds named by number")
            named, sprite_name = best
            for sound in named:
                member = sound.get("md5ext") or sound.get("assetId")
                path = temp / f"{sound['name']}{Path(member).suffix}"
                path.write_bytes(archive.read(member))
                found.append((base + int(sound["name"]) - 1, path, f"{sprite_name}/{sound['name']}"))
    found.sort(key=lambda item: item[0])
    return found


# ----------------------------------------------------------------- grouping

FAMILIES = [
    (0, 15, "gm.a"),  # piano, chromatic percussion
    (16, 31, "gm.b"),  # organ, guitar
    (32, 47, "gm.c"),  # bass, solo strings
    (48, 63, "gm.d"),  # ensemble, brass
    (64, 79, "gm.e"),  # reed, pipe
    (80, 103, "gm.f"),  # synth lead/pad/fx
    (104, 127, "gm.g"),  # ethnic, percussive, sfx
]


def family_of(preset: Preset) -> str:
    if preset.bank == 128:
        return "gm.h"
    for lo, hi, name in FAMILIES:
        if lo <= preset.program <= hi:
            return name
    return "gm.g"


def is_sustaining(preset: Preset) -> bool:
    if preset.bank == 128:
        return False
    program = preset.program
    if program < 16 or 24 <= program < 40 or 104 <= program < 120:
        return False
    return True


def sound_name(slot: int) -> str:
    return f"s{slot}"


# -------------------------------------------------------------------- build


def build(args) -> int:
    sf2 = Sf2(Path(args.sf2).expanduser())
    wanted = _select_presets(sf2, args.banks)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    sound_id: dict[tuple[str, int, int, float], int] = {}
    sound_files: dict[str, list[dict]] = {}
    rows = []
    for preset in wanted:
        table = sf2.lut(preset, args.velocity)
        if all(sample < 0 for sample, _, _ in table):
            continue
        sprite = "synth." + family_of(preset)
        length = args.sustain if is_sustaining(preset) else args.decay
        slots = []
        fade_in, gain = shape_of(preset, args.fx_fade, args.fx_gain)
        for sample_index, semis, loop_mode in table:
            if sample_index < 0:
                slots.append([-1, 0])
                continue
            # The shape is part of the key: the same sample baked as an ambience
            # and as a note is two different files, and a slot is a file.
            key = (sprite, sample_index, loop_mode, float(length), fade_in, gain)
            if key not in sound_id:
                sample = sf2.samples[sample_index]
                pcm, extended = bake(sf2, sample, loop_mode, float(length), fade_in, gain)
                slot = len(sound_files.setdefault(sprite, []))
                sound_id[key] = slot
                rel = Path("sounds") / sprite / f"{sound_name(slot)}.wav"
                write_wav(out / rel, pcm, sample.rate)
                sound_files[sprite].append(
                    {
                        "name": sound_name(slot),
                        "file": rel.as_posix(),
                        "rate": sample.rate,
                        "frames": len(pcm),
                        "source": sample.name,
                        "extended": extended,
                    }
                )
            slots.append([sound_id[key], semis])
        rows.append(
            {
                "bank": preset.bank,
                "program": preset.program,
                "name": preset.name,
                "sprite": sprite,
                "sustaining": is_sustaining(preset),
                "notes": slots,
            }
        )

    # Whether each slot may be replayed to continue a note. A slot is a
    # fabricated sustain only if the sample was looped out *and* the preset that
    # chooses it is a sustaining instrument; the banks' families mix the two (an
    # organ and a guitar are both programs 16..31), so this cannot be decided per
    # sprite. A slot any non-sustaining preset uses is 0 -- a stopped note is a
    # smaller lie than a bell that re-strikes itself.
    sustain_of = {}
    for row in rows:
        flags = sustain_of.setdefault(row["sprite"], {})
        for slot, _semis in row["notes"]:
            if slot < 0:
                continue
            flags[slot] = flags.get(slot, True) and row["sustaining"]
    for sprite, flags in sustain_of.items():
        for slot, sound in enumerate(sound_files.get(sprite, [])):
            sound["retrigger"] = bool(sound.get("extended")) and flags.get(slot, False)

    report = {
        "source": str(Path(args.sf2).expanduser()),
        "velocity": args.velocity,
        "seconds": {"sustain": args.sustain, "decay": args.decay},
        "sprites": {
            sprite: {
                "sounds": sounds,
                "count": len(sounds),
                "bytes": sum((out / s["file"]).stat().st_size for s in sounds),
            }
            for sprite, sounds in sorted(sound_files.items())
        },
        "presets": rows,
    }
    if args.piano:
        try:
            replaced = substitute_piano(report, sound_files, out, Path(args.piano).expanduser())
            print(f"  piano: Scratch's own samples on {replaced} presets -> out/sounds/synth.piano")
        except DecoderMissing as exc:
            print(f"  piano: keeping the sound font's ({exc})")
    (out / "bank.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    map_source, map_info = render_map(report, out, args.prefix)
    (out / "map.jsw").write_text(map_source, encoding="utf-8")
    print(f"  lookup table {map_info['entries']:,} entries -> {out / 'map.jsw'}")
    (out / "synth.gm.jsw").write_text(
        render_classes(sound_files, args.voice, args.prefix, voice_piano(args)),
        encoding="utf-8")
    print(summary(report, out))
    return 0


def voice_piano(args) -> str | None:
    return getattr(args, "voice_piano", None)


def piano(args) -> int:
    """Re-bake *only* the piano of a bank that has already been built.

    The rest of the bank is 449 samples that took a SoundFont to produce; the
    piano is the one family whose source is a project, not a font. Doing it in
    place means a new piano does not need the font at all -- which matters,
    because the font the bank was made from is not always still on the machine.
    """
    out = Path(args.bank).expanduser()
    manifest = out / "bank.json"
    if not manifest.exists():
        raise SystemExit(f"{manifest} is missing; bake the bank first (make np5-bank)")
    report = json.loads(manifest.read_text(encoding="utf-8"))
    sound_files = {sprite: info["sounds"] for sprite, info in report["sprites"].items()}
    source = Path(args.keys).expanduser()
    found = read_key_samples(source, args.base)
    if not found:
        raise SystemExit(f"{source}: no samples")
    samples = [(note, path) for note, path, _ in found]
    replaced = substitute_keys(report, sound_files, out, samples, args.trim,
                               args.mp3, label=source.name)
    report["source"] = report.get("source", "") if args.keep_source else f"{source} (+ {report.get('source', '')})"
    manifest.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    map_source, map_info = render_map(report, out, args.prefix)
    (out / "map.jsw").write_text(map_source, encoding="utf-8")
    (out / "synth.gm.jsw").write_text(
        render_classes(sound_files, args.voice, args.prefix, voice_piano(args)), encoding="utf-8")
    info = report["sprites"][PIANO_SPRITE]
    print(f"  piano: {len(samples)} samples on {replaced} presets, "
          f"{info['bytes'] / 1e6:.2f} MB, slots {found[0][0]}..{found[-1][0]}")
    print(summary(report, out))
    return 0


def _select_presets(sf2: Sf2, banks: str) -> list[Preset]:
    if banks == "all":
        return sf2.presets
    wanted = {int(b) for b in banks.split(",") if b.strip()}
    return [p for p in sf2.presets if p.bank in wanted]


#: The two banks a runtime loader can reach: the melodic one and the drum kit on
#: channel 10. A GM preset is (bank, program); the loader knows the channel, not
#: the file, so the table is indexed by exactly that.
MAP_BANKS = (0, 128)


def render_map(report: dict, out: Path, prefix: str = "bank/") -> tuple[str, dict]:
    """The lookup a *runtime* loader needs: (bank, program, note) -> sample.

    A build-time conversion resolves this per note and can forget the table. A
    project that parses a MIDI after it has loaded cannot, so the same answer is
    written out as two lists: ``Bank.voice`` packs ``sprite * 100000 + slot`` --
    both are small integers -- and ``Bank.semis`` is the pitch correction. The
    index is ``(bank_slot * 128 + program) * 128 + note``, one-based, where
    ``bank_slot`` is 0 for the melodic bank and 1 for the drum kit.

    A program the sound font does not have falls back the way the converter
    does: the first preset of the same bank. A slot past the end is a note the
    preset cannot reach at all, which is a piano's lowest octave, and it points
    at slot 0 with the correction that covers it.

    A note the preset has *no* sample for at all -- a drum kit's notes outside
    its range -- is written as ``-1`` rather than as an index. Slot 0 is a real
    sample, so 0 cannot say "nothing"; the loader reads the -1 and drops the
    note instead of asking for a sample that is not there.
    """
    by_bank: dict[int, dict[int, dict]] = {}
    for preset in report["presets"]:
        by_bank.setdefault(preset["bank"], {}).setdefault(preset["program"], preset)
    sprites = sorted(report["sprites"])
    voice: list[int] = []
    semis: list[float] = []
    for bank in MAP_BANKS:
        presets = by_bank.get(bank) or {}
        default = presets.get(0) or next(iter(presets.values()), None)
        for program in range(128):
            preset = presets.get(program) or default
            for note in range(128):
                if preset is None:
                    voice.append(-1)
                    semis.append(0.0)
                    continue
                slot, semis_ = preset["notes"][note]
                if int(slot) < 0:
                    voice.append(-1)
                    semis.append(0.0)
                    continue
                sprite = sprites.index(preset["sprite"])
                voice.append(sprite * 100000 + int(slot))
                semis.append(round(float(semis_), 2))
    lines = ["// Generated by tools/sf2_bank.py -- do not edit.", ""]
    out.mkdir(parents=True, exist_ok=True)
    for name, values in (("Bank.voice", voice), ("Bank.semis", semis)):
        (out / f"{name}.jsa").write_text(
            json.dumps(values, separators=(",", ":")), encoding="utf-8")
        lines.append(f'import "{prefix}{name}.jsa";')
    lines.append("")
    lines.append(f"public Song.bankBanks = {len(MAP_BANKS)};")
    # The index of the piano sprite, which the dispatcher uses as the `else` of
    # its clone chain and which songs are checked against.
    piano = sorted(report["sprites"]).index(PIANO_SPRITE) if PIANO_SPRITE in report["sprites"] else -1
    lines.append(f"public Song.pianoSprite = {piano};")
    return "\n".join(lines), {"entries": len(voice), "sprites": sprites}


def render_classes(sound_files: dict[str, list[dict]], voice: str | None = None,
                   prefix: str = "", voice_piano: str | None = None,
                   pool: str | None = None, piano_pool: str | None = None) -> str:
    """One class per sample sprite, each carrying the note's own script.

    `pool` / `piano_pool` are accepted and ignored: the voice pool they named is
    gone. It reused clones instead of making one per note, which is faster but
    audibly wrong -- Scratch's volume and pitch effects belong to the target, so
    a reused voice un-mutes and re-tunes the tail of the note before it. See
    userspace/np5/voice.inc.jsw.
    """
    del pool, piano_pool
    lines = ["// Generated by tools/sf2_bank.py -- do not edit.", ""]
    for sprite, sounds in sorted(sound_files.items()):
        cls = sprite.replace(".", "_")
        lines.append(f"class {cls} {{")
        for sound in sounds:
            lines.append(f'    import "{prefix}{sound["file"]}" as "{sound["name"]}";')
        lines.append(render_durations(sounds))
        which = voice_piano if (sprite == PIANO_SPRITE and voice_piano) else voice
        if which:
            lines.append(f'    include "{which}"')
        lines.append("}")
        lines.append(f'{cls}: name="{sprite}", x=0, y=0, size=100, direction=90, show=0;')
        lines.append("")
    return "\n".join(lines)


def sound_seconds(sound: dict) -> float:
    rate = float(sound.get("rate") or 0)
    return round(sound.get("frames", 0) / rate, 3) if rate else 0.0


def render_durations(sounds: list[dict]) -> str:
    """``private VoiceDur = [...]`` -- how long each slot's audio actually lasts.

    A voice has to know this, because a note can be longer than its sample and
    Scratch cannot loop one: `Sound.playSound` on a target that is already
    playing restarts the file from the beginning (`SoundPlayer.play` builds a
    fresh source node), so replaying it is the only way to keep a long note
    sounding. A MIDI file is full of long notes -- in a real one 4-5% of the
    notes outlast 1.5 s, and the held ones are the ones a listener notices: a
    13 s seashore, a 35 s string pad, a 6 s sung note. Without this they simply
    stop, and the expression curve that shapes them is never heard at all.

    **0 is "never retrigger".** `retrigger` marks a slot that was *looped out*
    from a few milliseconds of attack and a 4 ms loop -- a fabricated sustain,
    where replaying the file continues the tone -- and that belongs to a
    sustaining instrument. A slot that is a real recording (a piano, a plucked
    string, a bell, a drum) is 0: replaying one of those is a re-strike, which is
    a different note, not a longer one.
    """
    entries = []
    for sound in sounds:
        entries.append(sound_seconds(sound) if sound.get("retrigger") else 0)
    return "    private VoiceDur = [" + ", ".join(str(x) for x in entries) + "];"


def summary(report: dict, out: Path) -> str:
    lines = [f"bank: {report['source']}", f"  presets: {len(report['presets'])}"]
    total = 0
    for sprite, info in report["sprites"].items():
        total += info["bytes"]
        lines.append(f"  {sprite:14s} {info['count']:4d} sounds  {info['bytes'] / 1e6:7.2f} MB")
    lines.append(f"  total {total / 1e6:.2f} MB in {out}")
    return "\n".join(lines)


# ------------------------------------------------------------------ inspect


def inspect(args) -> int:
    sf2 = Sf2(Path(args.sf2).expanduser())
    wanted = _select_presets(sf2, args.banks)
    used = {z.sample for p in wanted for z in p.zones if z.sample >= 0}
    looped = [sf2.samples[i] for i in used if sf2.samples[i].has_loop]
    tonal = [p for p in wanted if is_sustaining(p)]
    print(sf2.path)
    print(f"  presets {len(sf2.presets)}  samples {len(sf2.samples)}  selected {len(wanted)} ({len(tonal)} sustaining)")
    print(f"  samples reachable {len(used)}  with a loop {len(looped)}")
    if looped:
        secs = sorted(s.seconds for s in looped)
        loops = sorted((s.loop_end - s.loop_start) / s.rate for s in looped)
        print(f"  sample length  med {secs[len(secs) // 2]:.3f}s  max {secs[-1]:.3f}s")
        print(f"  loop length    med {loops[len(loops) // 2]:.4f}s  max {loops[-1]:.4f}s")
        per_second = 2 * looped[0].rate
        print(f"  baked at {args.sustain}s mono16 -> {len(looped) * int(args.sustain * per_second) / 1e6:.2f} MB over {len(looped)} sounds")
    banks: dict[int, int] = {}
    for preset in sf2.presets:
        banks[preset.bank] = banks.get(preset.bank, 0) + 1
    print(f"  banks {banks}")
    for preset in wanted[:6]:
        print(f"   {preset.bank:3d}:{preset.program:3d} {preset.name[:24]:24s} zones {len(preset.zones)}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    ins = sub.add_parser("inspect", help="report what the SoundFont holds")
    ins.add_argument("sf2")
    ins.add_argument("--banks", default="0,128", help="comma separated bank numbers, or 'all'")
    ins.add_argument("--sustain", type=float, default=1.5)
    ins.set_defaults(func=inspect)

    bld = sub.add_parser("build", help="bake samples and write bank.json + synth.gm.jsw")
    bld.add_argument("sf2")
    bld.add_argument("-o", "--out", default="build/np5-bank")
    bld.add_argument("--banks", default="0,128")
    bld.add_argument("--velocity", type=int, default=100, help="velocity used to pick a layer")
    bld.add_argument("--sustain", type=float, default=3.0, help="seconds for sustained instruments")
    bld.add_argument("--decay", type=float, default=1.0, help="seconds for plucked/percussive ones")
    bld.add_argument("--fx-fade", type=float, default=0.4,
                     help="fade-in seconds for the ambience programs (seashore, helicopter, applause)")
    bld.add_argument("--fx-gain", type=float, default=0.7,
                     help="linear level for those, as a fraction of full scale")
    bld.add_argument(
        "--prefix",
        default="",
        help="path prefix for sound imports, from the project file that includes the bank",
    )
    bld.add_argument(
        "--voice",
        default=None,
        help="file included inside every sample sprite (the note voice), relative to the bank",
    )
    bld.add_argument(
        "--voice-piano",
        default=None,
        help="file included inside the piano sprite instead of --voice",
    )

    bld.add_argument(
        "--piano", default=None, metavar="DIR",
        help="use Scratch's own piano multi-samples (the music extension's 1-piano folder)",
    )
    bld.set_defaults(func=build)

    pia = sub.add_parser("piano", help="re-bake only the piano of an existing bank")
    pia.add_argument("--bank", default="userspace/np5/bank", help="the baked bank directory")
    pia.add_argument("--keys", required=True,
                     help="a directory of 1.wav..128.wav, or an .sb3 with a per-key sprite")
    pia.add_argument("--base", type=int, default=0,
                     help="the MIDI note the sample named 1 is (default 0)")
    # 3.5 s, not 2: a piano sample is still -28 dB at 2.5 s, which is the
    # difference between a note that rings and one that stops. Measured on the
    # example's C4: -16 dB at 1 s, -28 at 2.5, -38 at 4.
    pia.add_argument("--trim", type=float, default=3.5, help="seconds kept from each sample")
    pia.add_argument("--mp3", type=int, default=96,
                     help="MP3 bitrate in kbit/s, 0 to keep WAV (default 96)")
    pia.add_argument("--prefix", default="", help="path prefix for sound imports")
    pia.add_argument("--voice", default=None, help="the note voice, relative to the bank")
    pia.add_argument("--voice-piano", default=None,
                     help="the clone body for the piano sprite, relative to the bank")

    pia.add_argument("--keep-source", action="store_true",
                     help="leave bank.json's source field alone")
    pia.set_defaults(func=piano)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
