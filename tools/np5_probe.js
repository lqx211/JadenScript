#!/usr/bin/env node
// Health probe for the NotePlayer V5 project: tick rate, live voices, clones.
//
//   node tools/np5_probe.js build/np5.sb3 5
//
// Audio is absent -- this only says whether the script side keeps time and
// whether every clone retires. Two things it deliberately does not do:
//
//   * it does not fake the clock. `Sequencer.WORK_TIME` and `WARP_TIME` are
//     `Date.now()` based, so a frozen clock turns any spin into a permanent
//     hang and the probe measures nothing. Real time in, real time out.
//   * it does not fetch extensions. `lmsTempVars2` (the thread variables the
//     voice uses) is stubbed with the same three opcodes, registered as a
//     builtin before the project loads.
//
// What to look for: `ticks/s` matches the song's tempo (a tick is 1/16 beat, so
// 100 BPM is 26.7), and `voices` equals `clones` and comes back down -- a
// monotonic climb means notes are never released.
//
//   node tools/np5_probe.js build/np5.sb3 5 30      # as scratch.mit.edu runs it
//   node tools/np5_probe.js build/np5.sb3 5 30 --json
//   node tools/np5_probe.js build/np5.sb3 5 30 --answer song.hex
//
// The project asks for a song on every green flag, so a headless run has to
// answer: `--answer` is a file of hex, and without it the box is answered empty,
// which is the "play the built-in one" path.
//
// The state is read through the Stage's *names*, so this needs a build without
// `doObfuscation`: an obfuscated project has no `Tick.tick` to find, and the
// probe says so rather than printing `undefined`.

process.env.LOG_LEVEL = "error";

const fs = require("fs");
const path = require("path");

const ROOT = path.resolve(__dirname, "..");

// `Runtime._step` asks the page whether the tab is hidden before it draws, so a
// headless run needs that much of a DOM.
global.document = { hidden: false };
const VirtualMachine = require(path.join(ROOT, "scratch-vm-develop", "src", "index.js"));

class TempVariablesStub {
    getInfo () {
        return {
            id: "lmsTempVars2",
            name: "Temp",
            blocks: [
                { opcode: "setThreadVariable", blockType: "command", arguments: { VAR: {}, STRING: {} } },
                { opcode: "getThreadVariable", blockType: "reporter", arguments: { VAR: {} } },
                { opcode: "changeThreadVariable", blockType: "command", arguments: { VAR: {}, NUM: {} } }
            ]
        };
    }

    setThreadVariable (args, util) {
        if (util.thread.tempVars === undefined) util.thread.tempVars = {};
        util.thread.tempVars[args.VAR] = args.STRING;
    }

    getThreadVariable (args, util) {
        return util.thread.tempVars === undefined ? "" : util.thread.tempVars[args.VAR];
    }

    changeThreadVariable (args, util) {
        if (util.thread.tempVars === undefined) util.thread.tempVars = {};
        const current = Number(util.thread.tempVars[args.VAR] || 0);
        util.thread.tempVars[args.VAR] = current + Number(args.NUM || 0);
    }
}

function main () {
    const project = process.argv[2] || path.join(ROOT, "build", "np5.sb3");
    const seconds = Number(process.argv[3] || 5);
    // 30 is what scratch.mit.edu runs at; 60 what TurboWarp does by default.
    const fps = Number(process.argv[4] || 60);
    const json = process.argv.includes("--json");
    // Two green flags in a row: run, press stop, run again. A project whose
    // repaint gate keeps a value across runs draws nothing the second time.
    const twice = process.argv.includes("--twice");
    // Every pitch, pan and volume a voice is given, per sounding note. A bend
    // or a fader has to reach the note that is already playing, and there is no
    // audio to hear that with, so the numbers the voice sends to the engine are
    // the evidence:
    //
    //   set effect PITCH / PAN -> sound_seteffectto, by EFFECT name
    //   set volume to          -> sound_setvolumeto
    //
    // All three go through `soundBank.setEffects(target)` and the engine
    // re-syncs live players for each -- AudioEngine's chain is
    // [PanEffect, PitchEffect, VolumeEffect] and EffectChain.setEffectsFromTarget
    // falls back to `target.volume` when `soundEffects` has no entry for it --
    // so a value appearing here twice with two different numbers is a note that
    // really was re-tuned, not a note that was restarted.
    const tracePitch = process.argv.includes("--pitch");
    const traceMix = process.argv.includes("--mix");
    // `--plays`: how many times each voice started its sound. A note longer
    // than its sample has to replay it -- Scratch cannot loop a sound and
    // `playSound` restarts from the first frame -- so this is the count that
    // says the retrigger in `voice.inc.jsw` really happens.
    const tracePlays = process.argv.includes("--plays");
    // Every key the keyboard draws lit, per frame: the lights come from the
    // score (`Piano.end`), so a test can check them against the song it wrote --
    // a key that stays lit after its note is over is a seek that skipped the
    // note-offs, and there is nothing to hear that with.
    const traceLights = process.argv.includes("--lights");
    const stallAt = process.argv.indexOf("--stall");
    const stall = stallAt > 0 ? Number(process.argv[stallAt + 1] || 0) : 0;
    // `--keys s,ArrowDown,Space`: keys tapped in that order, a quarter second
    // apart, so a page driven from the keyboard can be tested without a browser.
    const keysAt = process.argv.indexOf("--keys");
    // `Space` is spelled out here but is a single space to the VM: its
    // `_keyStringToScratchKey` maps `' '` and nothing else, so a literal
    // "Space" would be pressed and never be seen as the space bar.
    const keys = keysAt > 0 && process.argv[keysAt + 1]
        ? process.argv[keysAt + 1].split(",").map((key) => key.trim()).filter(Boolean)
            .map((key) => (key === "Space" ? " " : key))
        : [];
    const answerAt = process.argv.indexOf("--answer");
    const answer = answerAt > 0 && process.argv[answerAt + 1]
        ? fs.readFileSync(process.argv[answerAt + 1], "utf8").trim()
        : "";

    const vm = new VirtualMachine();
    vm.runtime.currentStepTime = 1000 / fps;
    vm.extensionManager.addBuiltinExtension("lmsTempVars2", TempVariablesStub);

    const stageValue = (name) => {
        const stage = vm.runtime.getTargetForStage();
        for (const value of Object.values(stage.variables)) {
            if (value.name === name) return value.value;
        }
        return undefined;
    };
    const clones = () => vm.runtime.targets.filter((target) => !target.isOriginal).length;

    // Pen is the screen here: with no renderer at all the pen blocks throw, so
    // the probe needs one anyway, and counting the strokes is how "did the
    // keyboard draw" is answered without a screen.
    // `--art` reconstructs the screen from the pen calls instead of counting
    // them: the project's whole picture is horizontal runs, so replaying the
    // strokes into a bitmap and printing the top of it is the only way to see
    // what the text block actually says without a browser. A stroke's diameter
    // matters -- `clearHud` and `clearStage` erase with a thick line -- so the
    // replay paints an area, not a hairline.
    const art = process.argv.includes("--art");
    // `--text 180,168,156,144,132`: read those lines of the screen back as
    // characters, which is how a test checks what the debug block *says* rather
    // than which variables it was built from.
    const textAt = process.argv.indexOf("--text");
    const hudLines = textAt > 0 && process.argv[textAt + 1]
        ? process.argv[textAt + 1].split(",").map(Number)
        : [];
    // Where the first character of the scanned lines starts. The HUD begins at
    // the left margin (-232), a terminal page begins at column 5 of the grid
    // (-200), and the cell boundaries have to line up with the text or every
    // glyph reads as a question mark.
    const originAt = process.argv.indexOf("--text-origin");
    const textOrigin = originAt > 0 && process.argv[originAt + 1]
        ? Number(process.argv[originAt + 1])
        : -232;
    // A page is wider than the 60 cells a HUD line uses.
    const cellsAt = process.argv.indexOf("--text-cells");
    const textCells = cellsAt > 0 && process.argv[cellsAt + 1]
        ? Number(process.argv[cellsAt + 1])
        : 60;
    const W = 480, H = 360;
    const pixels = art ? new Uint8Array(W * H) : null;
    // The pen's colour, because the project clears by *painting* the paper
    // colour over the stage rather than by `erase all` -- `Pen.eraseAll()`
    // would take the HUD with it. A replay that ignores colour paints the erase
    // bands as ink and the whole stage comes out black.
    const PAPER = "101418";
    const colorOf = (state) => {
        // Same shape as `diameterOf`: this fork hands the attributes object
        // itself, so `color4f` is on `state` and not on `state.penAttributes`.
        const c = !state ? null
            : state.color4f ? state.color4f
                : state.penAttributes ? state.penAttributes.color4f : null;
        if (!c) return "ffffff";
        const hex = [c[0], c[1], c[2]]
            .map((v) => Math.max(0, Math.min(255, Math.round(Number(v) * 255))))
            .map((v) => v.toString(16).padStart(2, "0"))
            .join("");
        return hex;
    };
    const thinRuns = art ? [] : null;
    // this fork hands `penLine` the attributes object itself, not the pen state
    // around it (`scratch-vm-develop/src/extensions/scratch3_pen/index.js`
    // passes `penState.penAttributes`), so the diameter is `state.diameter`.
    // Reading `state.penAttributes.diameter` silently gave every stroke a width
    // of one, which turned the erase bands into hairlines and made every `--art`
    // capture show yesterday's text underneath today's.
    const diameterOf = (state) => {
        const value = !state ? 0
            : state.diameter !== undefined ? state.diameter
                : state.penAttributes ? state.penAttributes.diameter : 0;
        return Number.isFinite(Number(value)) && Number(value) > 0 ? Number(value) : 1;
    };
    const paint = (x0, y0, x1, y1, diameter, color) => {
        if (!art) return;
        const value = color === PAPER ? 0 : 1;
        // pen coordinates are stage coordinates: -240..240, -180..180, y up
        const px = (x) => Math.round(x + W / 2);
        const py = (y) => Math.round(H / 2 - y);
        const r = Math.max(1, Math.round(diameter)) / 2;
        const ax = px(x0), ay = py(y0), bx = px(x1), by = py(y1);
        const steps = Math.max(Math.abs(bx - ax), Math.abs(by - ay), 1);
        for (let i = 0; i <= steps; i += 1) {
            const cx = ax + ((bx - ax) * i) / steps;
            const cy = ay + ((by - ay) * i) / steps;
            for (let dy = -r; dy <= r; dy += 1) {
                for (let dx = -r; dx <= r; dx += 1) {
                    const x = Math.round(cx + dx), y = Math.round(cy + dy);
                    if (x >= 0 && x < W && y >= 0 && y < H) pixels[y * W + x] = value;
                }
            }
        }
    };
    // The font, to turn the strokes back into characters. A glyph is a list of
    // horizontal runs, so a cell of the screen is recognisable by which runs
    // land in it -- which is the only way to *read* the text block from here.
    const fontOf = () => {
        const read = (name) => JSON.parse(fs.readFileSync(
            path.join(ROOT, "userspace", "np5", "assets", "lists", `Font.${name}.jsa`), "utf8"));
        const chars = read("chars"), start = read("start"), runs = read("runs");
        const table = [];
        for (let i = 0; i < chars.length; i += 1) {
            const glyph = [];
            // `Font.start` is 1-based into `Font.runs`, the same way the
            // project's `text` block reads it (`Font.runs.get(a)` is `runs[a-1]`).
            for (let a = start[i] - 1; a < start[i + 1] - 1; a += 3) {
                // rows are drawn downwards from the line's y, so a run's screen
                // y is `lineY - row`
                glyph.push([runs[a], runs[a + 1], runs[a + 2]]);
            }
            table.push([chars[i], glyph]);
        }
        return table;
    };
    // Every run thinner than the width of a glyph, by `y|x0|x1`: the erase
    // bands and the note bodies are thick, and including them would make every
    // cell match nothing.
    const runSet = () => {
        const seen = new Set();
        for (const [y, x0, x1, d] of thinRuns) {
            seen.add(`${y}|${x0}|${x1}`);
        }
        return seen;
    };
    // Pixels, not runs: the pen also stamps a point where it goes down, and a
    // point plus a line is the same ink as the line. Expanding the runs of one
    // cell into the pixels they cover makes the comparison exact both ways, so
    // a cell only reads as a character when nothing else is in it.
    const textOf = (lineY) => {
        const font = fontOf();
        let out = "";
        for (let col = 0; col < textCells; col += 1) {
            const cellX = textOrigin + col * 8;
            const ink = new Set();
            for (const [y, x0, x1] of thinRuns) {
                // The whole glyph box, rows 0..14. It fits because a line is
                // `Term.pitch` (18) from the next one: the row above's lowest
                // descender lands four rows *above* this baseline, outside the
                // box. A narrower window was how punctuation went missing --
                // `"` and `(` reach up to rows 1 and 2, above every letter.
                if (y >= lineY - 14 && y <= lineY && x0 >= cellX && x1 <= cellX + 7) {
                    for (let x = x0; x <= x1; x += 1) ink.add(`${lineY - y}|${x - cellX}`);
                }
            }
            if (ink.size === 0) {
                out += " ";
                continue;
            }
            let found = "?";
            for (const [ch, glyph] of font) {
                const want = new Set();
                for (const [x, row, w] of glyph) {
                    for (let i = 0; i < w; i += 1) want.add(`${row}|${x + i}`);
                }
                if (want.size !== ink.size) continue;
                let same = true;
                for (const key of want) {
                    if (!ink.has(key)) {
                        same = false;
                        break;
                    }
                }
                if (same) {
                    found = ch;
                    break;
                }
            }
            out += found;
        }
        return out.replace(/\s+$/, "");
    };

    const artText = () => {
        // One character per 8x15 glyph cell, which is the font's own grid.
        const rows = [];
        for (let row = 0; row * 15 < H; row += 1) {
            let line = "";
            for (let col = 0; col * 8 < W; col += 1) {
                let ink = 0;
                for (let y = row * 15; y < (row + 1) * 15 && y < H; y += 1) {
                    for (let x = col * 8; x < (col + 1) * 8 && x < W; x += 1) {
                        ink += pixels[y * W + x];
                    }
                }
                line += ink > 0 ? "#" : " ";
            }
            rows.push(line.replace(/\s+$/, ""));
        }
        while (rows.length && rows[rows.length - 1] === "") rows.pop();
        return rows.join("\n");
    };

    const pen = { strokes: 0, clears: 0 };
    const renderer = {
        _skin: 1, createPenSkin () { return 1; }, createDrawable () { return 1; },
        markDrawableAsNoninteractive () {}, updateDrawableSkinId () {},
        penClear () {
            pen.clears += 1;
            if (art) pixels.fill(0);
        },
        // This fork of scratch-vm's pen extension passes only the position; the
        // colour and the diameter live in `penState.penAttributes`, which is
        // also what a real sketch of the call would need for the thickness.
        penPoint (skin, state, x, y) {
            pen.strokes += 1;
            const d = diameterOf(state);
            if (!state || state === undefined) throw new Error("pen state");
            paint(x, y, x, y, d, colorOf(state));
            if (art && d <= 2 && colorOf(state) !== PAPER) {
                thinRuns.push([Math.round(y), Math.round(x), Math.round(x), d]);
            }
        },
        penLine (skin, state, x0, y0, x1, y1) {
            pen.strokes += 1;
            const d = diameterOf(state);
            paint(x0, y0, x1, y1, d, colorOf(state));
            // A thick line across the HUD strip is `clearHud` wiping the block
            // before it is repainted, so everything before it is the *previous*
            // frame's text and would read as a smear over this one.
            if (art && colorOf(state) === PAPER && d >= 50 && y0 > 100) thinRuns.length = 0;
            if (art && d <= 2 && colorOf(state) !== PAPER) {
                thinRuns.push([Math.round(y0), Math.round(x0), Math.round(x1), d]);
            }
        },
        penStamp () {},
        createDrawableGroup () { return 0; }, destroyDrawable () {},
        updateDrawableVisible () {}, updateDrawablePosition () {},
        updateDrawableDirectionScale () {}, updateDrawableEffect () {},
        updateDrawableRotationCenter () {}, updateDrawableScale () {},
        setDrawableOrder () {}, draw () {}
    };

    // note index -> the distinct pitches that voice was given, in order
    const pitchTrace = new Map();
    const lights = [];
    // What SCRATCH sound mode asked the music extension for. The block takes a
    // one-based GM number and folds it through the extension's own table, so the
    // number itself is the only thing that can be checked without hearing it.
    const instruments = [];
    // note index -> the distinct values of one parameter that voice was given
    const mixTrace = new Map();
    const recordMix = (util, name, value) => {
        const variable = util.target.lookupVariableByNameAndType("_i", "");
        const key = variable ? Number(variable.value) : 0;
        if (key <= 0) return;
        let perVoice = mixTrace.get(name);
        if (!perVoice) mixTrace.set(name, (perVoice = new Map()));
        const seen = perVoice.get(key) || [];
        if (seen[seen.length - 1] !== value) seen.push(value);
        perVoice.set(key, seen);
    };
    // voice -> how many times it started its sound
    const playTrace = new Map();
    if (tracePlays) {
        const playSound = vm.runtime._primitives.sound_play;
        vm.runtime._primitives.sound_play = (args, util) => {
            const variable = util.target.lookupVariableByNameAndType("_i", "");
            const key = variable ? Number(variable.value) : 0;
            if (key > 0) playTrace.set(key, (playTrace.get(key) || 0) + 1);
            return playSound(args, util);
        };
    }
    if (tracePitch || traceMix) {
        const setEffect = vm.runtime._primitives.sound_seteffectto;
        vm.runtime._primitives.sound_seteffectto = (args, util) => {
            const effect = String(args.EFFECT).toUpperCase();
            if (tracePitch && effect === "PITCH") {
                const variable = util.target.lookupVariableByNameAndType("_i", "");
                const key = variable ? Number(variable.value) : 0;
                const value = Number(args.VALUE);
                const seen = pitchTrace.get(key) || [];
                if (seen[seen.length - 1] !== value) seen.push(value);
                pitchTrace.set(key, seen);
            }
            if (traceMix && effect === "PAN") recordMix(util, "pan", Number(args.VALUE));
            return setEffect(args, util);
        };
        const setVolume = vm.runtime._primitives.sound_setvolumeto;
        vm.runtime._primitives.sound_setvolumeto = (args, util) => {
            // The block's input is named VOLUME here and VALUE on `set effect`,
            // and an argument read from a report goes through `util` -- hence
            // the cast to a number rather than trusting the field.
            if (traceMix) recordMix(util, "volume", Number(args.VOLUME ?? args.VALUE));
            return setVolume(args, util);
        };
    }

    // The ask block is a promise: answer it as soon as it is put up, or the
    // green-flag script never gets past the input box.
    vm.runtime.on("QUESTION", () => vm.runtime.emit("ANSWER", answer));

    return vm.loadProject(fs.readFileSync(project)).then(async () => {
        // A project's `_twconfig_` raises the clone cap for TurboWarp, and it is
        // applied while the project loads -- so this has to come after, or the
        // config reads it back. scratch.mit.edu has no such field and stops at
        // 300, which is the run FPS=30 stands for.
        if (fps <= 30) vm.runtime.runtimeOptions.maxClones = 300;
        // The project ships `"turbo":true` in its `_twconfig_`: without it the
        // sequencer ends a pass as soon as the pen asks for a redraw, and the
        // clock thread -- which is queued behind 250 pool voices -- ran 5 times
        // in 7 seconds instead of 210. The probe measures the player as it runs
        // in TurboWarp, so it turns the same thing on.
        vm.runtime.turboMode = true;
        if (stageValue("Tick.tick") === undefined) {
            console.error(`${path.basename(project)}: no Tick.tick on the Stage -- ` +
                          "an obfuscated build has no names to read; probe a plain one");
            process.exit(2);
        }
        vm.runtime.renderer = renderer;
        // After the load, not before: the music extension is registered while the
        // project loads, so its primitives do not exist until then.
        const setInstrument = vm.runtime._primitives.music_midiSetInstrument;
        if (setInstrument) {
            vm.runtime._primitives.music_midiSetInstrument = (args, util) => {
                instruments.push(Math.round(Number(args.INSTRUMENT)));
                return setInstrument(args, util);
            };
        }
        const settingsState = () => ({
            open: Number(stageValue("Set.open") || 0),
            cursor: Number(stageValue("Set.cursor") || 0),
            sound: Number(stageValue("Set.sound") || 0),
            glow: Number(stageValue("Set.glow") || 0),
            keys: Number(stageValue("Set.keys") || 0),
            hud: Number(stageValue("Set.hud") || 0),
            horizon: Number(stageValue("Piano.horizon") || 0),
            depth: Number(stageValue("Piano.depth") || 0),
            fov: Number(stageValue("Piano.fov") || 0),
            // The player's own three knobs, read back so a test can see that the
            // page stored what the key asked for before looking for the number
            // at the engine.
            master: Number(stageValue("Mix.master") || 0),
            pan: Number(stageValue("Mix.masterPan") || 0),
            masterPitch: Number(stageValue("Mix.masterPitch") || 0)
        });
        // The debug block's own state: the text costs pen strokes, so what a
        // test can read back is the values it is built from -- which file is
        // loaded, how big it is, and which lyric line the playhead is inside.
        const hudState = () => ({
            title: String(stageValue("Song.title") ?? ""),
            kb: Number(stageValue("Song.kb") || 0),
            lyricCount: Number(stageValue("Song.lyricCount") || 0),
            lyric: Number(stageValue("Piano.lyric") || 0),
            lyricText: String((stageValue("Song.LyricText") || [])[Number(stageValue("Piano.lyric") || 0) - 1] ?? ""),
            nps: Number(stageValue("Player.nps") || 0)
        });
        const runOnce = async () => {
            pen.strokes = 0;
            pen.clears = 0;
            pitchTrace.clear();
            mixTrace.clear();
            playTrace.clear();
            lights.length = 0;
            instruments.length = 0;
            vm.greenFlag();
            const started = Date.now();
            const rows = [];
            let steps = 0;
            let peakVoices = 0;
            let peakClones = 0;
            // A key is pressed for 120 ms and released, 250 ms apart: long
            // enough for two frames, short enough that nothing auto-repeats.
            let nextKey = 0;
            let keyDownAt = -1;
            const keyboard = vm.runtime.ioDevices.keyboard;
            // A stall is a hidden tab or a hitch: no frames at all for a while,
            // while the wall clock keeps running. The deadline moves with it so
            // the same amount of *playing* happens either way.
            const stallMs = stall * 1000;
            let deadline = started + seconds * 1000;
            let stalled = stallMs === 0;
            while (Date.now() < deadline) {
                if (!stalled && Date.now() - started >= (seconds * 1000) / 2) {
                    stalled = true;
                    await new Promise((done) => setTimeout(done, stallMs));
                    deadline += stallMs;
                }
                const elapsed = Date.now() - started;
                if (nextKey < keys.length && elapsed >= 400 + nextKey * 250) {
                    keyboard.postData({ key: keys[nextKey], keyCode: 0, isDown: true });
                    keyDownAt = elapsed;
                    nextKey += 1;
                } else if (keyDownAt >= 0 && elapsed - keyDownAt >= 120) {
                    keyboard.postData({ key: keys[nextKey - 1], keyCode: 0, isDown: false });
                    keyDownAt = -1;
                }
                vm.runtime._step();
                steps += 1;
                // The ask block resolves a promise, and a promise only settles on a
                // microtask -- a tight synchronous loop would never let it.
                await new Promise((resolve) => setImmediate(resolve));
                if (traceLights) {
                    const ends = stageValue("Piano.end") || [];
                    const now = Number(stageValue("Tick.tick") || 0);
                    const running = Number(stageValue("Tick.active") || 0) === 1;
                    const on = [];
                    for (let key = 0; key < ends.length; key++) {
                        const end = Number(ends[key]);
                        if (running && end > 0 && end >= now) on.push(key);
                    }
                    lights.push([now, on]);
                }
                peakVoices = Math.max(peakVoices, Number(stageValue("Player.voices") || 0));
                peakClones = Math.max(peakClones, clones());
                if (steps % fps === 0) {
                    rows.push(
                        `  ${((Date.now() - started) / 1000).toFixed(1)}s  ` +
                        `tick=${stageValue("Tick.tick")}  ` +
                        `voices=${stageValue("Player.voices")}  clones=${clones()}`
                    );
                }
            }
            const wall = (Date.now() - started) / 1000;
            const tick = Number(stageValue("Tick.tick") || 0);
            return {
                seconds: wall,
                steps,
                ticksPerSecond: Number((tick / wall).toFixed(2)),
                peakVoices,
                peakClones,
                finalVoices: Number(stageValue("Player.voices") || 0),
                finalClones: clones(),
                notesPlayed: Number(stageValue("Player.notesPlayed") || 0),
                skipped: Number(stageValue("Player.skipped") || 0),
                stillPlaying: Number(stageValue("Tick.active") || 0),
                strokes: pen.strokes,
                clears: pen.clears,
                settings: settingsState(),
                hud: hudState(),
                pitch: [...pitchTrace.entries()].sort((a, b) => a[0] - b[0]),
                // parameter -> [[voice, [distinct values in order]], ...]
                plays: [...playTrace.entries()].sort((a, b) => a[0] - b[0]),
                mix: [...mixTrace.entries()].map(([name, perVoice]) => [
                    name,
                    [...perVoice.entries()].sort((a, b) => a[0] - b[0])
                ]),
                lights,
                instruments,
                rows
            };
        };

        const runs = [await runOnce()];
        if (twice) {
            // The red button: every thread gone, the pen layer left as it was.
            vm.runtime.stopAll();
            for (let i = 0; i < 3; i++) {
                vm.runtime._step();
                await new Promise((resolve) => setImmediate(resolve));
            }
            runs.push(await runOnce());
        }
        const first = runs[0];
        const { rows } = first;
        const wall = first.seconds;
        const steps = first.steps;
        const peakVoices = Math.max(...runs.map((run) => run.peakVoices));
        const peakClones = Math.max(...runs.map((run) => run.peakClones));
        const tick = first.ticksPerSecond * wall;
        const result = {
            project: path.basename(project),
            fps,
            seconds: wall,
            steps,
            ticks: tick,
            ticksPerSecond: first.ticksPerSecond,
            peakVoices,
            peakClones,
            finalVoices: first.finalVoices,
            finalClones: first.finalClones,
            notesPlayed: first.notesPlayed,
            skipped: first.skipped,
            stillPlaying: first.stillPlaying,
            strokes: runs.map((run) => run.strokes),
            clears: runs.map((run) => run.clears),
            settings: runs.map((run) => run.settings),
            hud: runs.map((run) => run.hud)
        };
        if (tracePitch) result.pitch = runs.map((run) => run.pitch);
        if (traceMix) result.mix = runs.map((run) => run.mix);
        if (tracePlays) result.plays = runs.map((run) => run.plays);
        if (traceLights) result.lights = runs.map((run) => run.lights);
        result.instruments = [...new Set(runs[0].instruments)].sort((a, b) => a - b);
        if (hudLines.length) result.screen = hudLines.map((lineY) => textOf(lineY));
        if (json) {
            console.log(JSON.stringify(result));
            process.exit(0);
        }
        if (art) console.log(artText());
        // The lines read back as text, both for a human and for a test: this is
        // the only assertion that can be made about *what the block says*, as
        // opposed to which variables it was built from.
        const readLines = hudLines.map((lineY) => textOf(lineY));
        if (hudLines.length) {
            hudLines.forEach((lineY, i) => console.log(`y=${lineY}: ${readLines[i]}`));
        }
        console.log(`${path.basename(project)}  ${fps} fps  ${seconds} s  (${steps} steps)`);
        console.log(rows.join("\n"));
        console.log(`  ticks/s ${result.ticksPerSecond}   cursor ${stageValue("Player.cursor")}   active ${result.stillPlaying}`);
        console.log(`  voices peak ${peakVoices}, now ${result.finalVoices}   ` +
                    `clones peak ${peakClones}, now ${result.finalClones}   ` +
                    `notes ${result.notesPlayed}`);
        process.exit(0);
    });
}

main().catch((error) => {
    console.error(error && error.stack);
    process.exit(1);
});
