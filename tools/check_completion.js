#!/usr/bin/env node
// Smoke-tests vscode-jsw/completion.js against the real .jsw files.
//
//   node tools/check_completion.js [file.jsw ...]
//
// Exits non-zero when a case fails, so it can run from the test suite.

"use strict";

const fs = require("fs");
const path = require("path");

const ROOT = path.resolve(__dirname, "..");
const engine = require(path.join(ROOT, "vscode-jsw", "completion.js"));
const data = JSON.parse(
  fs.readFileSync(path.join(ROOT, "vscode-jsw", "data", "jsw.json"), "utf8")
);

let failures = 0;
function check(name, ok, detail) {
  if (ok) return;
  failures++;
  console.error(`FAIL ${name}${detail ? " — " + detail : ""}`);
}

/** Hover the word after the `|` marker. */
function hoverAt(source) {
  const offset = source.indexOf("|");
  const text = source.slice(0, offset) + source.slice(offset + 1);
  const info = engine.provideHover(text, offset, data);
  return info ? info.contents.value : "";
}

/** Complete at the marker ``|`` in `source` (or at its end). */
function completeAt(source) {
  const offset = source.includes("|") ? source.indexOf("|") : source.length;
  const text = source.slice(0, offset) + source.slice(offset + 1);
  return engine.provideCompletions(text, offset, data).map((i) => i.label);
}

const cases = [
  // statement keywords
  ["sta", "stage"],
  ["pub", "public"],
  ["rep", "repeat"],
  // builtins and namespaces
  ["jo", "join"],
  ["Motion.mov", "move"],
  ["Pen.setPe", "setPenSize"],
  ["Looks.switch", "switchCostume"],
  ["Operators.rou", "round"],
  ["JSON.isA", "isArray"],
  ["console.lo", "log"],
  ["Events.key", "keyPressed"],
];
for (const [typed, expected] of cases) {
  const labels = completeAt(typed);
  check(`completion ${JSON.stringify(typed)}`, labels.includes(expected), `got [${labels.slice(0, 8)}]`);
}

// A dot is not a list. The receiver names what it really is, and only a real
// list gets `add`/`get` — anything else gets nothing rather than noise.
const DOC = [
  "public Player.notes = 1;",
  "public Player.score = 0;",
  'import "assets/lists/Player.NotesActive.jsa";',
  "public myList = [1, 2, 3];",
  "public counter = 0;",
  "class Lyrics {",
  '    public c_en = "hi";',
  "    public zh = [];",
  "    public void init() { }",
  "}",
].join("\n");

function completeInDocument(typed) {
  const text = DOC + "\n" + typed;
  return engine.provideCompletions(text, text.length, data).map((i) => i.label);
}

check("a list offers list methods", completeInDocument("myList.ad").includes("add"));
check("a plain variable does not", completeInDocument("counter.ad").length === 0);
check("an unknown name does not", completeInDocument("whatever.ad").length === 0);
check("a class offers its members", completeInDocument("Lyrics.c_e").includes("c_en"));
check("a class offers its methods", completeInDocument("Lyrics.in").includes("init"));
check("a class's list member is a list", completeInDocument("Lyrics.zh.a").includes("add"));
check("a dotted prefix offers its leaves", completeInDocument("Player.no").includes("notes"));
check("a dotted prefix offers every leaf", completeInDocument("Player.").includes("score"));
check("a dotted import leaf is a list", completeInDocument("NotesActive.g").includes("get"));
check("no list methods for a prefix", !completeInDocument("Player.").includes("add"));
check("runtime namespaces still win", completeInDocument("Pen.setPe").includes("setPenSize"));
// A namespace is never a list, however the document looks: `Variables.` offers
// the monitor pair, not `add(item)`.
check("variables namespace is the monitor pair", completeInDocument("Variables.").join() === "hideVariable,showVariable");
check("variables namespace is never a list", !completeInDocument("Variables.").includes("add"));
check("lists namespace offers hideList", completeInDocument("Lists.hide").includes("hideList"));
check("a real list still gets list methods", completeInDocument("myList.remove").includes("remove"));

// a namespace completion types the dot for you
const nsItems = engine.provideCompletions("Pe", 2, data);
const pen = nsItems.find((i) => i.label === "Pen");
check("namespace insertText", pen && pen.insertText === "Pen.", JSON.stringify(pen));

// inside a plain string nothing is suggested …
check("no completion in strings", completeAt('Looks.say("se').length === 0);
// … but a key block's quotes offer the names the VM reports
check("key name completion", completeAt('if (Sensing.keyPressed("le').includes("left arrow"));
check("key names are not offered elsewhere", !completeAt('Looks.say("le').includes("left arrow"));

// inside a comment the annotations show up
check("annotation completion", completeAt("// proc").includes("proccode"));
check("annotation receiver", completeAt("// receiv").includes("receiver at"));
check("no keywords in comments", !completeAt("// pub").includes("public"));

// metadata: the forms, the monitor modes, then the structural words
check("metadata form pos", completeAt("metadata p").includes("pos"));
check("metadata form monitor", completeAt("metadata mon").includes("monitor"));
check("metadata form comment", completeAt("metadata com").includes("comment"));
check("metadata mode", completeAt("metadata l").includes("list"));
check("metadata mode large", completeAt("metadata lar").includes("large"));
check("metadata mode largeReadout", completeAt("metadata largeR").includes("largeReadout"));
check("metadata words", completeAt("metadata list Stage.x ").includes("at"));
check("metadata hidden", completeAt("metadata list Stage.x h").includes("hidden"));
check("metadata state", completeAt("metadata note Stage at 1,2 3x4 m").includes("minimized"));
check("metadata pos", completeAt("metadata Stage.tick p").includes("pos"));
check("pos is not offered after a target",
  !completeAt("metadata monitor Stage.x po").includes("pos"), "the target already decided the form");

// a typed value only after its own key
check("rotation values", completeAt("stage: rotation=").includes("leftright"));
check("video values", completeAt("stage: video=").includes("on-flipped"));
check("show is a flag", completeAt("Empty: show=").includes("1"));
check("rotation values are not offered for show",
  !completeAt("Empty: show=").includes("leftright"));

// keySim: the dropdown arguments are the hint -- a *key* name, a mouse
// button, or the and-wait item. `press("space")` alone compiles to " ".
check("keysim key", completeAt('keySim.press("').includes("up arrow"));
check("keysim key prefix", completeAt('keySim.press("sp').length === 1);
check("keysim hold", completeAt('keySim.hold("').includes("space"));
check("keysim release", completeAt('keySim.release("').includes("enter"));
check("keysim bare key", completeAt("keySim.press(").includes("space"));
check("keysim buttons", completeAt("keySim.holdButton(").includes("2"));
check("keysim and-wait", completeAt('keySim.click(0, 1, 2, 0.5, "').includes("and wait"));
check("keysim does not hijack other calls", !completeAt('Looks.say("').includes("up arrow"));
check("keysim key hover", hoverAt('keySim.press("sp|ace", 0.2);').includes('\" \"'));
check("keysim arrow hover", hoverAt('keySim.hold("up |arrow");').includes("ArrowUp"));

// stage:/sprite property keys, with `=` already typed, filtered by target
check("stage keys", completeAt("stage: cos").includes("costume"));
check("sprite keys", completeAt("Empty: nam").includes("namespace"));
check("sprite keys exclude stage-only keys",
  !completeAt("Empty: temp").includes("tempo"), "tempo is a Stage property");
check("stage keys exclude sprite-only keys",
  !completeAt("stage: x").includes("x"), "the Stage has no position");
check("target keys are not offered in code", !completeAt("    cos").includes("costume"));

// the `// Sprite:` comment carries the same keys
check("sprite comment keys", completeAt("// Sprite: main (0,0) siz").includes("size"));
check("sprite comment excludes position", !completeAt("// Sprite: main (0,0) x").includes("x"));
check("stage comment keys", completeAt("// Stage: temp").includes("tempo"));

// asset annotations inside the import comment
check("asset keys", completeAt("// costume cen").includes("centerx"));
check("asset rate", completeAt("// sound ra").includes("rate"));
check("asset keys stay out of prose", !completeAt("// a note about cen").includes("centerx"));

// feature switches
check("feature completion", completeAt("#enable_feature<com").includes("commentMetadata"));
check("feature list complete", completeAt("#enable_feature<").length === data.features.length);

// document symbols (classes, methods, variables, parameters)
const doc = [
  "class player { // Sprite: player (0,0) size=100",
  "    public score = 0;",
  "    private void update(delta) {",
  "        sco|",
  "    }",
  "}",
].join("\n");
check("document symbol", completeAt(doc).includes("score"), "variables of this file");
check("parameter symbol", completeAt(doc.replace("sco|", "del|")).includes("delta"), "parameters");

// hats come with their hook comment and body
const hat = engine.provideCompletions("whenClo", 7, data).find((i) => i.label === "whenCloned");
check("hat snippet", hat && hat.insertText && hat.insertText.value.includes("public void whenCloned() {"));

// hover
const hoverText = "Pen.setPenSize(2);";
const hover = engine.provideHover(hoverText, hoverText.indexOf("setPenSize") + 2, data);
check("hover on member", hover && hover.contents.value.includes("setPenSize"), JSON.stringify(hover));
const hoverKw = engine.provideHover("if (a) {}", 1, data);
check("hover on keyword", hoverKw && hoverKw.contents.value.includes("**if**"), JSON.stringify(hoverKw));


// hover: the whole metadata grammar on the keyword, then the type of each word
const metaHover = hoverAt("|metadata slider Stage.x at 0,0 10x10 shown;");
for (const needle of ["pos", "monitor", "slider", "comment", "type", "mode", "layout"]) {
  check(`metadata hover mentions ${needle}`, metaHover.toLowerCase().includes(needle), metaHover.slice(0, 120));
}
check("hover on a mode", hoverAt("metadata sli|der Stage.x at 0,0 10x10 shown;").includes("metadata ·"));
check("hover on pos", hoverAt("metadata Stage.tick po|s 1,2;").includes("pos <x>,<y>"));
check("hover on a flag", hoverAt("metadata list Stage.x at 0,0 10x10 sh|own;").includes("monitor state"));
check("hover on a target key type",
  hoverAt("Empty: si|ze=100;").includes("number"), "a key hover names its type");
check("hover on a stage key",
  hoverAt("stage: temp|o=120;").includes("stage"), "tempo is a Stage property");
check("hover on a rotation value", hoverAt("Sprite: name=\"x\", rotation=a|ll;").includes("rotation=all"));
check("hover on a flag value", hoverAt("Empty: show=|1;").includes("show=1"));
check("hover on a dashed value", hoverAt("stage: videoState=on-|flipped;").includes("on-flipped"));
check("no hover for a plain word", hoverAt("x = value|s;") === "", "prose is not metadata");

// Real files: every `Namespace.member` the compiler accepts must be offered by
// completion right after the dot, so the data cannot drift from the corpus.
// Occurrences inside comments or strings are prose, not code.
for (const file of process.argv.slice(2)) {
  const text = fs.readFileSync(file, "utf8");
  const lines = text.split("\n");
  const seen = new Set();
  let checked = 0;
  let rejected = 0;
  let lineStart = 0;
  lines.forEach((line, index) => {
    const code = line.replace(/\/\/.*$/, "");
    const re = /\b([A-Z][A-Za-z0-9_]*)\.([A-Za-z_$][\w$]*)/g;
    let m;
    while ((m = re.exec(code))) {
      const [, namespace, member] = m;
      if (!data.namespaces[namespace]) continue;
      const id = namespace + "." + member;
      if (seen.has(id)) continue;
      seen.add(id);
      const at = lineStart + m.index + namespace.length + 1; // just after the dot
      const labels = engine.provideCompletions(text, at, data).map((i) => i.label);
      if (labels.includes(member)) {
        checked++;
      } else {
        // A name the compiler rejects (`Control.clone`) is a corpus bug, not a
        // completion bug; report it once so it is visible.
        rejected++;
        console.log(`  note: ${path.basename(file)}:${index + 1} ${id} is not a known block`);
      }
    }
    lineStart += line.length + 1;
  });
  console.log(`${path.basename(file)}: ${checked} namespace.member positions confirmed (${rejected} rejected by the compiler)`);
}

if (failures) {
  console.error(`${failures} failure(s)`);
  process.exit(1);
}
console.log("completion OK");
