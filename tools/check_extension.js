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

const registered = { completion: [], hover: [], signature: [], definition: [], symbols: [] };

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

class MarkdownString {
  constructor(value) {
    this.value = value;
  }
}
class Location {
  constructor(uri, range) {
    this.uri = uri;
    this.range = range;
  }
}
class SignatureInformation {
  constructor(label, documentation) {
    this.label = label;
    this.documentation = documentation;
    this.parameters = [];
  }
}
class ParameterInformation {
  constructor(label) {
    this.label = label;
  }
}
class SignatureHelp {
  constructor() {
    this.signatures = [];
    this.activeSignature = 0;
    this.activeParameter = 0;
  }
}
class SymbolInformation {
  constructor(name, kind, containerName, location) {
    this.name = name;
    this.kind = kind;
    this.containerName = containerName;
    this.location = location;
  }
}

const vscode = {
  Position,
  Range,
  CompletionItem,
  SnippetString,
  Hover,
  MarkdownString,
  Location,
  SignatureInformation,
  ParameterInformation,
  SignatureHelp,
  SymbolInformation,
  SymbolKind: { Class: 4, Method: 5, Function: 11, Variable: 12 },
  languages: {
    registerCompletionItemProvider(selector, provider) {
      registered.completion.push(provider);
      return { dispose() {} };
    },
    registerHoverProvider(selector, provider) {
      registered.hover.push(provider);
      return { dispose() {} };
    },
    registerSignatureHelpProvider(selector, provider, ...triggers) {
      registered.signature.push({ provider, triggers });
      return { dispose() {} };
    },
    registerDefinitionProvider(selector, provider) {
      registered.definition.push(provider);
      return { dispose() {} };
    },
    registerDocumentSymbolProvider(selector, provider) {
      registered.symbols.push(provider);
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
check("registers a signature help provider", registered.signature.length === 1);
check("the signature provider triggers on ( and ,",
  registered.signature[0] && registered.signature[0].triggers.join("") === "(,", 
  JSON.stringify(registered.signature[0] && registered.signature[0].triggers));
check("registers a definition provider", registered.definition.length === 1);
check("registers a document symbol provider", registered.symbols.length === 1);
check("disposables tracked", context.subscriptions.length === 5, String(context.subscriptions.length));

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

// The function hints: a My Block declared in the same file.
const typed = [
  "class main {",
  "    private void slide(start, steps) {",
  "        Motion.move(steps);",
  "    }",
  "    public void greenFlag() {",
  "        slide(1, 2);",
  "    }",
  "}",
].join("\n");
const typedDoc = documentFor(typed);
typedDoc.uri = "file:///typed.jsw";
const callAt = typed.indexOf("slide(1, ") + "slide(1, ".length;
const help = registered.signature[0].provider.provideSignatureHelp(
  typedDoc, typedDoc.positionAt(callAt)
);
check("signature help answers", help instanceof SignatureHelp, String(help));
check("the hint is the declaration's signature",
  help && help.signatures[0].label === "slide(start, steps)",
  help && help.signatures[0].label);
check("the second argument is active", help && help.activeParameter === 1,
  String(help && help.activeParameter));
check("the hint has one parameter per argument",
  help && help.signatures[0].parameters.length === 2,
  help && String(help.signatures[0].parameters.length));

const definition = registered.definition[0].provideDefinition(
  typedDoc, typedDoc.positionAt(typed.indexOf("slide(1") + 2)
);
check("definition resolves to a Location", definition instanceof Location, String(definition));
check("definition points at the declaration",
  definition && definition.range.start.line === 1,
  definition && String(definition.range.start.line));

const symbols = registered.symbols[0].provideDocumentSymbols(typedDoc);
check("symbols come back as SymbolInformation",
  Array.isArray(symbols) && symbols.every((s) => s instanceof SymbolInformation), String(symbols));
check("the class is a symbol", symbols.some((s) => s.name === "main"));
check("a block is a symbol with its signature",
  symbols.some((s) => s.name === "slide" && s.containerName.includes("slide(start, steps)")),
  symbols.map((s) => `${s.name}:${s.containerName}`).join(","));

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
