#!/usr/bin/env node
// Prints every token of the given lines with its innermost scope, which is how
// a missing scope is found: run it on a real project and look for a word that
// only carries `meta.*` or `source.jsw`.
//
//   node tools/dump_jsw_tokens.js file.jsw 23 24 30
const fs = require('fs');
const path = require('path');
const ROOT = path.resolve(__dirname, '..');
const GRAMMAR = path.join(ROOT, 'vscode-jsw/syntaxes/jsw.tmLanguage.json');
const vsctm = require(path.join(ROOT, 'node_modules/vscode-textmate'));
const onig = require(path.join(ROOT, 'node_modules/vscode-oniguruma'));

(async () => {
  await onig.loadWASM(
    fs.readFileSync(path.join(ROOT, 'node_modules/vscode-oniguruma/release/onig.wasm')).buffer,
  );
  const registry = new vsctm.Registry({
    onigLib: Promise.resolve({
      createOnigScanner: (s) => new onig.OnigScanner(s),
      createOnigString: (s) => new onig.OnigString(s),
    }),
    loadGrammar: async () => JSON.parse(fs.readFileSync(GRAMMAR, 'utf8')),
  });
  const grammar = await registry.loadGrammar('source.jsw');
  const file = process.argv[2];
  const lines = fs.readFileSync(file, 'utf8').split('\n');
  const want = new Set(process.argv.slice(3).map(Number));
  let state = vsctm.INITIAL;
  lines.forEach((line, i) => {
    const row = grammar.tokenizeLine(line, state);
    state = row.ruleStack;
    if (!want.has(i + 1)) return;
    console.log(`--- ${i + 1}: ${line}`);
    for (const t of row.tokens) {
      const text = line.slice(t.startIndex, t.endIndex);
      if (!text.trim()) continue;
      console.log(`    ${JSON.stringify(text).padEnd(22)} ${t.scopes[t.scopes.length - 1]}`);
    }
  });
})();
