#!/usr/bin/env node
// Run two builds of one project and compare everything they hold.
//
//   node tools/obf_compare.js build/plain.sb3 build/hidden.sb3 120
//
// The obfuscator renames labels and adds blocks, so a diff of the two
// project.json files says nothing about whether the project still *behaves*.
// This runs both in the real VM for the same number of frames and compares the
// state they end in: every variable and every list, keyed by id -- ids are not
// renamed, so the plain build's ids name the same objects in the hidden one.
//
// Numbers and strings are compared as they are, lists item by item. The pooled
// string list is not in the plain build at all (nothing is pooled without the
// switch), so it never takes part in the comparison; the decoy objects are the
// same story. Broadcast *names* are skipped for a different reason: the VM
// keeps every message as a Stage variable, and those are renamed on purpose.
//
// A project that reads the clock or the frame rate cannot be compared this way:
// two runs of the *same* file already differ. Run the harness with one file
// twice to see that noise floor before blaming the obfuscator for it.
//
// Exit status: 0 same, 1 different, 2 could not run.

process.env.LOG_LEVEL = "error";

const fs = require("fs");
const path = require("path");

const ROOT = path.resolve(__dirname, "..");

// `Runtime._step` asks the page whether the tab is hidden before it draws.
global.document = { hidden: false };
const VirtualMachine = require(path.join(ROOT, "scratch-vm-develop", "src", "index.js"));

const perFrame = process.argv.includes("--per-frame");

// `--ext tools/i386mem.js` registers a project extension as if TurboWarp had
// fetched it: the file calls `Scratch.extensions.register(...)` against the
// globals below, and the instance goes into the VM's builtin list so loading
// does not stop at "permission denied". `--stub gsaWebsocket` is the other
// half: an extension a build names but the comparison does not need.
const extensionFiles = [];
const stubIds = [];
for (let i = 0; i < process.argv.length; i += 1) {
    if (process.argv[i] === "--ext") extensionFiles.push(process.argv[i + 1]);
    if (process.argv[i] === "--stub") stubIds.push(process.argv[i + 1]);
}

function loadExtensions () {
    const registered = [];
    global.Scratch = {
        ArgumentType: {
            NUMBER: "number", STRING: "string", BOOLEAN: "Boolean", COLOR: "color",
            ANGLE: "angle", MATRIX: "matrix", IMAGE: "image", NOTE: "note", LIST: "list"
        },
        BlockType: {
            COMMAND: "command", REPORTER: "reporter", BOOLEAN: "Boolean", HAT: "hat",
            EVENT: "event", CONDITIONAL: "conditional", LOOP: "loop", BUTTON: "button",
            LABEL: "label"
        },
        extensions: { register: (extension) => registered.push(extension) },
        translate: (message) => message,
        canFetch: async () => false,
        fetch: async () => { throw new Error("no network in the harness"); }
    };
    for (const file of extensionFiles) {
        delete require.cache[require.resolve(path.resolve(file))];
        require(path.resolve(file));
    }
    return registered;
}

const extensions = loadExtensions();

//: What a stub has to answer for a build that names it. The i386 machine links
//: the network sprite whether or not anything connects, so `isConnected` has to
//: exist and say no -- an opcode the extension never declared throws when the
//: block runs, which kills the thread and the comparison with it.
const STUB_SPECS = {
    // The thread-variable extension: a real store on the thread, because the
    // rv64 build keeps its registers in thread locals and a stub that returned
    // nothing would run every instruction from zero.
    lmsTempVars2: {
        setThreadVariable: ["command", function (args, util) {
            if (util.thread.tempVars === undefined) util.thread.tempVars = {};
            util.thread.tempVars[args.VAR] = args.STRING;
        }],
        getThreadVariable: ["reporter", function (args, util) {
            return util.thread.tempVars === undefined ? "" : util.thread.tempVars[args.VAR];
        }],
        changeThreadVariable: ["command", function (args, util) {
            if (util.thread.tempVars === undefined) util.thread.tempVars = {};
            const now = Number(util.thread.tempVars[args.VAR] || 0);
            util.thread.tempVars[args.VAR] = now + Number(args.NUM || 0);
        }]
    },
    tw: {
        getButtonIsDown: ["Boolean", () => false],
        getLastKeyPressed: ["reporter", () => ""],
        menu_mouseButton: ["reporter", () => ""]
    },
    pointerlock: {
        isLocked: ["Boolean", () => false]
    },
    gsaWebsocket: {
        newInstance: ["command", () => {}],
        isConnected: ["Boolean", () => false],
        sendMessage: ["command", () => {}],
        messageData: ["reporter", () => ""],
        onMessage: ["hat", () => {}],
        onOpen: ["hat", () => {}],
        onClose: ["hat", () => {}],
        onError: ["hat", () => {}]
    }
};

class StubExtension {
    constructor (id) {
        this.id = id;
        this.spec = STUB_SPECS[id] || {};
    }
    getInfo () {
        return {
            id: this.id,
            name: this.id,
            blocks: Object.entries(this.spec).map(([opcode, [blockType]]) => ({
                opcode,
                blockType,
                arguments: { A: { type: "string", defaultValue: "" } }
            }))
        };
    }
}

// Every stub opcode answers through this one name, so a build that passes an
// argument the spec did not describe still gets a value instead of a throw.
for (const spec of Object.values(STUB_SPECS)) {
    for (const [opcode, [, answer]] of Object.entries(spec)) {
        StubExtension.prototype[opcode] = answer;
    }
}

const PEN = {
    _skin: 1, createPenSkin () { return 1; }, createDrawable () { return 1; },
    // A `say` bubble is a text skin: without these the block throws, and a
    // project that says anything would stop the comparison at frame one.
    createTextSkin () { return 1; }, updateTextSkin () {}, destroySkin () {},
    createSVGSkin () { return 1; }, updateSVGSkin () {},
    destroyDrawableSkinId () {}, getSkinSize () { return [0, 0]; },
    getCurrentSkinSize () { return [0, 0]; }, getNativeSize () { return [480, 360]; },
    getBounds () { return { left: 0, right: 0, top: 0, bottom: 0 }; },
    markDrawableAsNoninteractive () {}, updateDrawableSkinId () {},
    penClear () {}, penPoint () {}, penLine () {}, penStamp () {},
    createDrawableGroup () { return 0; }, destroyDrawable () {},
    updateDrawableVisible () {}, updateDrawablePosition () {},
    updateDrawableDirectionScale () {}, updateDrawableEffect () {},
    updateDrawableRotationCenter () {}, updateDrawableScale () {},
    setDrawableOrder () {}, draw () {}
};

//: Lists whose order is not part of the contract: the pooled strings are
//: deliberately shuffled, so only their contents can be compared.
function snapshot (vm) {
    const originals = vm.runtime.targets.filter((target) => target.isOriginal);
    return originals.map((target) => {
        const state = {};
        for (const [id, variable] of Object.entries(target.variables)) {
            // A broadcast message is stored as a Stage variable whose *value*
            // is its name, and those names are renamed on purpose.
            if (variable.type === "broadcast_msg") continue;
            state[id] = Array.isArray(variable.value)
                ? { list: variable.value.map(String) }
                : { value: String(variable.value) };
        }
        return state;
    });
}

//: `--until <variableId>=<value>`: stop when that Stage variable reaches the
//: value, instead of after a fixed number of frames. Work-per-frame is real
//: time, so a clock-driven project (the i386 machine) never does the same
//: amount of work in the same number of frames -- but it does reach the same
//: state after the same amount of work.
// `--step-ms 0.001` makes one step cost one loop iteration instead of a
// budget of wall-clock work: the sequencer runs one pass over the threads and
// stops. A machine whose main loop yields once per emulated frame then advances
// exactly one frame per step, in both builds, which is what turns "same state
// after the same work" into "same state at every frame".
const stepArg = process.argv.find((arg) => arg.startsWith("--step-ms="));
const stepMs = stepArg ? Number(stepArg.slice("--step-ms=".length)) : 1000 / 30;

const untilArg = process.argv.find((arg) => arg.startsWith("--until="));
const until = untilArg ? untilArg.slice("--until=".length).split("=") : null;

async function run (project, frames) {
    const vm = new VirtualMachine();
    for (const extension of extensions) {
        const id = extension.getInfo().id;
        vm.extensionManager.addBuiltinExtension(id, function () { return extension; });
    }
    for (const id of stubIds) {
        vm.extensionManager.addBuiltinExtension(id, function () { return new StubExtension(id); });
    }
    vm.runtime.currentStepTime = stepMs;
    vm.runtime.turboMode = true;
    await vm.loadProject(fs.readFileSync(project));
    vm.runtime.renderer = PEN;
    // A project that asks something would wait forever for an answer, and the
    // state it would be compared in would be the state before it started.
    vm.runtime.on("QUESTION", () => vm.runtime.emit("ANSWER", ""));
    vm.greenFlag();
    // `--per-frame` keeps every frame, so a change in *when* something happens
    // shows up as well as a change in what happened. That is how a state
    // machine that spent an extra frame per statement would be caught.
    const trail = perFrame ? [snapshot(vm)] : [];
    const limit = until ? frames * 400 : frames;
    for (let i = 0; i < limit; i += 1) {
        if (until) {
            const value = Number(vm.runtime.getTargetForStage().variables[until[0]].value);
            if (value >= Number(until[1])) break;
        }
        vm.runtime._step();
        // A microtask between steps: a promise settled by a block (ask, or a
        // broadcast) only resumes on one, and a tight loop would never let it.
        await new Promise((resolve) => setImmediate(resolve));
        if (perFrame) trail.push(snapshot(vm));
    }
    const state = snapshot(vm);
    const clones = vm.runtime.targets.filter((target) => !target.isOriginal).length;
    if (process.argv.includes("--dump")) console.log(path.basename(project), JSON.stringify(state));
    vm.runtime.stopAll();
    return { state, clones, trail };
}

function compareFrames (plain, hidden) {
    const frames = Math.min(plain.trail.length, hidden.trail.length);
    for (let frame = 0; frame < frames; frame += 1) {
        const one = { state: plain.trail[frame], clones: 0 };
        const two = { state: hidden.trail[frame], clones: 0 };
        const diffs = compare(one, two);
        if (diffs.length) return diffs.map((line) => `frame ${frame}: ${line}`);
    }
    return [];
}

function compare (plain, hidden) {
    const diffs = [];
    if (plain.clones !== hidden.clones) {
        diffs.push(`clones: ${plain.clones} -> ${hidden.clones}`);
    }
    plain.state.forEach((target, index) => {
        const other = hidden.state[index];
        if (other === undefined) {
            diffs.push(`target ${index}: missing`);
            return;
        }
        for (const [id, value] of Object.entries(target)) {
            const there = other[id];
            if (there === undefined) {
                diffs.push(`target ${index} ${id}: missing after obfuscation`);
            } else if (JSON.stringify(value) !== JSON.stringify(there)) {
                const before = JSON.stringify(value).slice(0, 120);
                const after = JSON.stringify(there).slice(0, 120);
                diffs.push(`target ${index} ${id}: ${before} -> ${after}`);
            }
        }
    });
    return diffs;
}

async function main () {
    const [plainFile, hiddenFile, frameArg] = process.argv.slice(2);
    if (!plainFile || !hiddenFile) {
        console.error("usage: obf_compare.js plain.sb3 hidden.sb3 [frames]");
        process.exit(2);
    }
    const frames = Number(frameArg || 120);
    const plain = await run(plainFile, frames);
    const hidden = await run(hiddenFile, frames);
    const diffs = perFrame ? compareFrames(plain, hidden) : compare(plain, hidden);
    if (process.argv.includes("--json")) {
        console.log(JSON.stringify({ frames, same: diffs.length === 0, diffs }));
    } else if (diffs.length === 0) {
        console.log(`same after ${frames} frames${perFrame ? " (every frame)" : ""}: ` +
                    `${Object.keys(plain.state[0]).length} Stage names, ${plain.clones} clones`);
    } else {
        console.log(`${diffs.length} difference(s) after ${frames} frames:`);
        for (const line of diffs.slice(0, 40)) console.log(`  ${line}`);
    }
    process.exit(diffs.length === 0 ? 0 : 1);
}

main().catch((error) => {
    console.error(error);
    process.exit(2);
});
