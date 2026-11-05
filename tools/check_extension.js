#!/usr/bin/env node
// Loads vscode-jsw/extension.js against a stub `vscode` module and calls the
// providers, so a runtime error in activation is caught without opening an
// editor.
//
//   node tools/check_extension.js

"use strict";

const fs = require("fs");
const path = require("path");
const Module = require("module");

const ROOT = path.resolve(__dirname, "..");
const EXT = path.join(ROOT, "vscode-jsw");

const registered = { completion: [], hover: [] };

class Position {
  constructor(line, character) {
    this.line = line;
    this.character = character;
  }
}
class Range {
  constructor(a, b) {
    this.start = a;
    this.end = b;
  }
}
class CompletionItem {
  constructor(label, kind) {
    this.label = label;
    this.kind = kind;
  }
}
class SnippetString {
  constructor(value) {
    this.value = value;
  }
}
class Hover {
  constructor(contents, range) {
    this.contents = contents;
    this.range = range;
  }
}

const vscode = {
  Position,
  Range,
  CompletionItem,
  SnippetString,
  Hover,
  languages: {
    registerCompletionItemProvider(selector, provider) {
      registered.completion.push(provider);
      return { dispose() {} };
    },
    registerHoverProvider(selector, provider) {
      registered.hover.push(provider);
      return { dispose() {} };
    },
  },
};

// Intercept `require("vscode")`.
const load = Module._load;
Module._load = function (request, parent, isMain) {
  if (request === "vscode") return vscode;
  return load.apply(this, arguments);
};

const extension = require(path.join(EXT, "extension.js"));

let failures = 0;
function check(name, ok, detail) {
  if (ok) return;
  failures++;
  console.error(`FAIL ${name}${detail ? " — " + detail : ""}`);
}

const context = { subscriptions: [] };
extension.activate(context);
check("registers a completion provider", registered.completion.length === 1);
check("registers a hover provider", registered.hover.length === 1);
check("disposables tracked", context.subscriptions.length === 2);

// A fake TextDocument over a real file.
function documentFor(text) {
  return {
    getText: () => text,
    offsetAt: (position) => {
      const lines = text.split("\n");
      let offset = 0;
      for (let i = 0; i < position.line; i++) offset += lines[i].length + 1;
      return offset + position.character;
    },
    positionAt: (offset) => {
      const before = text.slice(0, offset);
      const line = before.split("\n").length - 1;
      return new Position(line, offset - (before.lastIndexOf("\n") + 1));
    },
  };
}

const source = [
  "class main { // Sprite: main (0,0) size=100",
  "    public score = 0;",
  "    public void greenFlag() {",
  "        Pen.setPenSize(1);",
  "        Pen.",
  "    }",
  "}",
].join("\n");

const document = documentFor(source);
const provider = registered.completion[0];
const items = provider.provideCompletionItems(document, new Position(4, 12));
check("completion returns items", Array.isArray(items) && items.length > 0, String(items));
check(
  "items are CompletionItems",
  items.every((i) => i instanceof CompletionItem),
  "wrong class"
);
check(
  "detail is filled in",
  items.every((i) => typeof i.detail === "string")
);
check(
  "namespace members offered",
  items.some((i) => i.label === "setPenSize"),
  items.map((i) => i.label).slice(0, 5).join(",")
);

// Hover over `setPenSize`.
const hover = registered.hover[0].provideHover(document, new Position(3, 14));
check("hover works", hover instanceof Hover, String(hover));
check("hover range", hover && hover.range instanceof Range);

// A file that does not exist must not throw: the provider is called a lot.
const bad = provider.provideCompletionItems(
  { getText: () => { throw new Error("boom"); }, offsetAt: () => 0 },
  new Position(0, 0)
);
check("provider survives a throwing document", Array.isArray(bad) && bad.length === 0);

extension.deactivate();
check("deactivate exists", typeof extension.deactivate === "function");

if (failures) {
  console.error(`${failures} failure(s)`);
  process.exit(1);
}
console.log("extension OK");
