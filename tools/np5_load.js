#!/usr/bin/env node
// Answer the project's song box with a hex MIDI and report what the parse made
// of it. The oracle is `tools/midi_to_fl2.py`, which does the same job offline.
//
//   node tools/np5_load.js build/np5-plain.sb3 song.hex --json
//
// A build with `doObfuscation` has no names to read, so this needs a plain one
// and says so if it is handed a hidden project.
process.env.LOG_LEVEL = "error";

const fs = require("fs");
const path = require("path");

const ROOT = path.resolve(__dirname, "..");
const VirtualMachine = require(path.join(ROOT, "scratch-vm-develop", "src", "index.js"));

const HEADLESS_RENDERER = {
    _skin: 1,
    createPenSkin () { return this._skin; },
    createDrawable () { return 1; },
    markDrawableAsNoninteractive () {},
    updateDrawableSkinId () {},
    penClear () {}, penPoint () {}, penLine () {}, penStamp () {},
    createDrawableGroup () { return 0; },
    destroyDrawable () {},
    updateDrawableVisible () {}, updateDrawablePosition () {},
    updateDrawableDirectionScale () {}, updateDrawableEffect () {},
    updateDrawableRotationCenter () {}, updateDrawableScale () {},
    setDrawableOrder () {}, draw () {},
};

function main () {
    const args = process.argv.slice(2);
    const project = args[0];
    const hexFile = args[1];
    const wantJson = args.includes("--json");
    const at = args.indexOf("--seconds");
    const seconds = at > 0 ? Number(args[at + 1]) : 3;
    const answer = hexFile ? fs.readFileSync(hexFile, "utf8").trim() : "";

    const vm = new VirtualMachine();
    vm.runtime.currentStepTime = 1000 / 30;
    global.document = { hidden: false };

    const stage = () => vm.runtime.getTargetForStage();
    const value = (name) => {
        for (const item of Object.values(stage().variables)) {
            if (item.name === name) return item.value;
        }
        return undefined;
    };
    const list = (name) => {
        for (const item of Object.values(stage().variables)) {
            if (item.name === name && item.type === "list") return item.value;
        }
        return [];
    };

    // The ask block is a promise, so the answer has to be given as soon as the
    // box appears and the loop has to let microtasks run.
    vm.runtime.on("QUESTION", () => vm.runtime.emit("ANSWER", answer));

    return vm.loadProject(fs.readFileSync(project)).then(async () => {
        vm.runtime.renderer = HEADLESS_RENDERER;
        if (value("Load.mode") === undefined) {
            console.error(`${path.basename(project)}: no Load.mode on the Stage -- ` +
                          "an obfuscated build has no names to read; load a plain one");
            process.exit(2);
        }
        vm.greenFlag();
        const started = Date.now();
        let steps = 0;
        let peakVoices = 0;
        while (Date.now() - started < seconds * 1000) {
            vm.runtime._step();
            steps += 1;
            await new Promise((resolve) => setImmediate(resolve));
            peakVoices = Math.max(peakVoices, Number(value("Player.voices") || 0));
            if (Number(value("Tick.active") || 0) === 0 && Number(value("Load.mode") || 0) !== undefined &&
                Number(value("Load.error") || 0) === 0 && steps > 4) {
                break;
            }
        }
        const result = {
            mode: Number(value("Load.mode") || 0),
            error: Number(value("Load.error") || 0),
            bytes: Number(value("Load.bytes") || 0),
            ppq: Number(value("Load.ppq") || 0),
            ticks: Number(value("Load.ticks") || 0),
            tempo: Number(value("Load.tempo") || 0),
            songTicks: Number(value("Song.ticks") || 0),
            songNotes: Number(value("Song.notes") || 0),
            played: Number(value("Player.notesPlayed") || 0),
            // What the debug block shows about the file: its name, its size, and
            // the lyrics the parse found. A paste has no size on disk, so `kb`
            // is the decoded length -- which is the same number for the same
            // file, whichever way it arrived.
            title: String(value("Song.title") ?? ""),
            kb: Number(value("Song.kb") || 0),
            lyricCount: Number(value("Song.lyricCount") || 0),
            lyricTick: Number(value("Load.mode") || 0) === 1 ? list("Song.LyricTick") : [],
            lyricText: Number(value("Load.mode") || 0) === 1 ? list("Song.LyricText") : [],
            steps,
            peakVoices,
            // Only the parsed song is compared; the built-in one is a hundred
            // thousand events and dumping it would be pure noise.
            ev: Number(value("Load.mode") || 0) === 1 ? {
                tick: list("Player.EvTick"), kind: list("Player.EvKind"),
                chan: list("Player.EvChan"), a: list("Player.EvA"),
                b: list("Player.EvB"), voice: list("Player.EvVoice"),
                len: list("Player.EvLen"), trk: list("Player.EvTrk"),
                sprite: list("Player.EvSprite"), slot: list("Player.EvSlot"),
                semis: list("Player.EvSemis"),
            } : null,
            note: Number(value("Load.mode") || 0) === 1 ? {
                tick: list("Note.tick"), len: list("Note.len"),
                key: list("Note.key"), trk: list("Note.trk"),
            } : null,
        };
        if (wantJson) {
            console.log(JSON.stringify(result));
        } else {
            const events = result.ev ? result.ev.kind.length : 0;
            const notes = result.note ? result.note.tick.length : 0;
            console.log(`${path.basename(project)}: mode ${result.mode} error ${result.error} ` +
                        `${result.bytes} bytes, ${events} events, ${notes} notes, ` +
                        `${result.played} played, ${result.ticks} ticks at ${result.tempo} BPM`);
        }
        // `process.exit` would drop a pending write on a pipe, and the parsed
        // song is bigger than the buffer.
        process.exitCode = 0;
        return;
    });
}

main().catch((error) => {
    process.stderr.write(String(error && error.stack) + "\n");
    process.exit(1);
});
