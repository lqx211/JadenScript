#!/usr/bin/env node
// Tokenises the committed TextMate grammar with the real vscode-textmate +
// Oniguruma engine, the way VS Code does. The Python test suite has to stand in
// for TextMate with a regex approximation; this is the ground truth for rule
// ordering and for scopes TextMate only assigns by capture group.
//
//   npm install            # once, at the repo root
//   node tools/check_grammar_real.js [file.jsw ...]
//
// Exits non-zero when an expectation fails, so it can gate a release.

const fs = require('fs');
const path = require('path');

const ROOT = path.resolve(__dirname, '..');
const GRAMMAR = path.join(ROOT, 'vscode-jsw/syntaxes/jsw.tmLanguage.json');

let vsctm, onig;
try {
  vsctm = require(path.join(ROOT, 'node_modules/vscode-textmate'));
  onig = require(path.join(ROOT, 'node_modules/vscode-oniguruma'));
} catch {
  console.error('skip: npm install vscode-textmate vscode-oniguruma (see README)');
  process.exit(0);
}

// token -> the innermost scope it must end up with.
const EXPECT = [
  ['#enable_feature<canvas>;', '#enable_feature', 'keyword.other.directive.jsw'],
  ['#enable_feature<canvas>;', 'canvas', 'entity.name.tag.jsw'],
  ['import "assets/x.svg" as "img loader";', '"assets/x.svg"', 'string.quoted.jsw'],
  ['import "assets/x.svg" as "img loader";', 'as', 'keyword.control.import.jsw'],
  ['import "assets/x.svg" as "img loader";', '"img loader"', 'entity.name.namespace.jsw'],
  ['import "assets/x.svg" as notes;', 'notes', 'entity.name.namespace.jsw'],
  ['Empty: name="Empty", x=36, y=28;', 'Empty', 'entity.name.type.class.jsw'],
  ['stage: costume=0, volume=100;', 'stage', 'keyword.declaration.stage.jsw'],
  ['class Empty {', 'Empty', 'entity.name.type.class.jsw'],
  ['    public void Stage.tick() {', 'Stage.tick', 'entity.name.function.jsw'],
  ['    metadata Stage.tick pos 539,64;', 'metadata', 'keyword.declaration.jsw'],
  ['    metadata Stage.tick pos 539,64;', '539', 'constant.numeric.jsw'],
  ['any value = 0x1d40;', 'any', 'constant.language.jsw'],
  ['any value = 0x1d40;', '0x1d40', 'constant.numeric.hex.jsw'],
  ['Global.frameIndex += 1;', 'Global', 'support.class.jsw'],
  ['Global.frameIndex += 1;', 'frameIndex', 'variable.other.property.jsw'],
  ['Sensing.keyPressed(128);', 'Sensing', 'support.class.builtin.jsw'],
  ['Sensing.keyPressed(128);', 'keyPressed', 'entity.name.function.call.jsw'],
  ['if (a && !b) { return; }', '&&', 'keyword.operator.logical.jsw'],
  ['x = f"hi {a}";', 'hi ', 'string.interpolated.jsw'],
  ['x = f"hi {a}";', '{', 'punctuation.section.embedded.begin.jsw'],
  ['stage: costume=0, volume=100;', 'stage', 'keyword.declaration.stage.jsw'],
  ['stage: costume=0, volume=100;', 'costume', 'keyword.other.target-key.jsw'],
  ['stage: costume=0, volume=100;', 'volume', 'keyword.other.target-key.jsw'],
  ['Empty: name="Empty", x=36;', 'name', 'keyword.other.target-key.jsw'],
  ['metadata list Stage.x at 0,0 0x0 hidden;', 'list', 'constant.language.monitor-mode.jsw'],
  ['metadata list Stage.x at 0,0 0x0 hidden;', 'at', 'keyword.other.metadata.jsw'],
  ['metadata list Stage.x at 0,0 0x0 hidden;', 'hidden', 'keyword.other.metadata.jsw'],
  ['metadata list Stage.Lyrics.zh at 5,5 0x0 hidden;', 'Stage.Lyrics.zh', 'variable.other.member.jsw'],
  // calls: a custom block, an extension method, and a declaration
  ['        playNote(60);', 'playNote', 'entity.name.function.call.jsw'],
  ['        LogStatic("x");', 'LogStatic', 'entity.name.function.call.jsw'],
  ['        _q5(320, 75);', '_q5', 'entity.name.function.call.jsw'],
  ['        Pen.setPenSize(2);', 'setPenSize', 'entity.name.function.call.jsw'],
  ['        Looks.say(join("a", "b"));', 'join', 'support.function.builtin.jsw'],
  ['        n = lengthOf(x);', 'lengthOf', 'support.function.builtin.jsw'],
  ['#enable_feature<doObfuscation>;', 'doObfuscation', 'constant.language.feature.jsw'],
  ['            case _ {', '_', 'variable.other.jsw'],
  ['public void hello() {', 'hello', 'entity.name.function.jsw'],
  ['        switch op_1 {', 'switch', 'keyword.control.jsw'],
  ['            case 0 {', 'case', 'keyword.control.jsw'],
  ['            case _ {', 'case', 'keyword.control.jsw'],
  ['    import', 'import', 'keyword.control.import.jsw'],
  ['    import "a.jsa" as b;', 'import', 'keyword.control.import.jsw'],
  ['    import "a.jsa" as b;', 'as', 'keyword.control.import.jsw'],
  ['        x = items[2];', 'items', 'variable.other.jsw'],
  // micro:bit: the namespace, and a menu value written as a constant
  ['        microbit.displayText("hi");', 'microbit', 'support.class.builtin.jsw'],
  ['        microbit.displayText("hi");', 'displayText', 'entity.name.function.call.jsw'],
  ['        microbit.isButtonPressed(microbit.buttonA);', 'buttonA', 'constant.language.microbit.jsw'],
  ['        Looks.say(microbit.tiltAngle(microbit.front));', 'front', 'constant.language.microbit.jsw'],
  ['    public void playNote(n) {', 'playNote', 'entity.name.function.jsw'],
  ['        if (x) { y = 1; }', 'if', 'keyword.control.jsw'],
  ['    public c_en = "hi";', 'c_en', 'variable.other.readwrite.jsw'],
  ['    public void start() {', 'void', 'storage.type.jsw'],
  ['    public Player.tick() {', 'Player', 'support.class.jsw'],
  ['    private _i;', '_i', 'variable.other.temp.jsw'],
  ['    import "a.svg" as a; // costume centerx=0 res=1', 'costume', 'keyword.other.annotation.jsw'],
  ['    import "a.svg" as a; // costume centerx=0 res=1', 'centerx', 'variable.parameter.annotation.jsw'],
  ['    import "a.wav" as b; // sound rate=48000 samples=8225', 'sound', 'keyword.other.annotation.jsw'],
  ['    import "a.wav" as b; // sound rate=48000 samples=8225', 'samples', 'variable.parameter.annotation.jsw'],
  ['for i in 0..3 {', 'i', 'variable.other.loop.jsw'],
  ['for i in 0..3 {', 'in', 'keyword.control.jsw'],
  ['for i in 0..3 {', '..', 'keyword.operator.range.jsw'],
  ['for Synth.counter in 1..n {', 'Synth.counter', 'variable.other.loop.jsw'],
  ['// proccode move camera %s', 'proccode', 'keyword.control.directive.jsw'],
];

// A scope that must not appear anywhere on the line.
const REJECT = [
  // A ``case`` label must not be mistaken for a target declaration.
  ['case foo:', 'entity.name.type.class.jsw'],
  // ``in`` is real syntax now, so it must not be flagged as a reserved word.
  ['for i in 0..3 {', 'invalid.illegal.reserved.jsw'],
];

(async () => {
  await onig.loadWASM(
    fs.readFileSync(path.join(ROOT, 'node_modules/vscode-oniguruma/release/onig.wasm')).buffer,
  );
  const registry = new vsctm.Registry({
    onigLib: Promise.resolve({
      createOnigScanner: (sources) => new onig.OnigScanner(sources),
      createOnigString: (s) => new onig.OnigString(s),
    }),
    loadGrammar: async () => JSON.parse(fs.readFileSync(GRAMMAR, 'utf8')),
  });
  const grammar = await registry.loadGrammar('source.jsw');

  let failed = 0;
  for (const [line, token, scope] of EXPECT) {
    const row = grammar.tokenizeLine(line, vsctm.INITIAL);
    const hits = row.tokens
      .filter((t) => line.slice(t.startIndex, t.endIndex) === token)
      .flatMap((t) => t.scopes);
    if (!hits.includes(scope)) {
      failed++;
      console.error(`FAIL ${JSON.stringify(token)} in ${JSON.stringify(line)}`);
      console.error(`     want ${scope}`);
      console.error(`     got  ${[...new Set(hits)].join(', ') || '(unscoped)'}`);
    }
  }

  for (const [line, forbidden] of REJECT) {
    const row = grammar.tokenizeLine(line, vsctm.INITIAL);
    const seen = row.tokens.flatMap((t) => t.scopes);
    if (seen.includes(forbidden)) {
      failed++;
      console.error(`FAIL ${JSON.stringify(line)} must not contain ${forbidden}`);
    }
  }

  for (const file of process.argv.slice(2)) {
    const lines = fs.readFileSync(file, 'utf8').split('\n');
    lines.forEach((line, i) => {
      const row = grammar.tokenizeLine(line, vsctm.INITIAL);
      const parts = row.tokens.map(
        (t) => `${JSON.stringify(line.slice(t.startIndex, t.endIndex))}=${t.scopes.at(-1)}`,
      );
      console.log(`${String(i + 1).padStart(4)} ${parts.join(' ')}`);
    });
  }

  if (failed) {
    console.error(`\n${failed} of ${EXPECT.length + REJECT.length} expectations failed`);
    process.exit(1);
  }
  console.log(`ok: ${EXPECT.length + REJECT.length} scopes match real TextMate tokenisation`);
})();
