"use strict";

// JadenScript (.jsw) language support: completion, hover and the directives
// that rebuild Scratch metadata. Syntax colouring comes from the TextMate
// grammar in syntaxes/.

const path = require("path");
const fs = require("fs");
const vscode = require("vscode");
const engine = require("./completion.js");

const DATA_FILE = path.join(__dirname, "data", "jsw.json");

function loadData() {
  try {
    return JSON.parse(fs.readFileSync(DATA_FILE, "utf8"));
  } catch (err) {
    console.error("[jaden-script] cannot read completion data:", err.message);
    return { keywords: [], builtins: [], features: [], namespaces: {}, listMethods: [], annotations: [] };
  }
}

function toRange(document, start, end) {
  return new vscode.Range(document.positionAt(start), document.positionAt(end));
}

function toCompletionItem(document, plain) {
  const completion = new vscode.CompletionItem(plain.label, plain.kind);
  completion.detail = plain.detail;
  if (plain.documentation) completion.documentation = plain.documentation;
  if (plain.sortText) completion.sortText = plain.sortText;
  if (plain.insertText) {
    if (typeof plain.insertText === "string") {
      completion.insertText = plain.insertText;
    } else {
      completion.insertText = new vscode.SnippetString(plain.insertText.value);
    }
  }
  return completion;
}

function activate(context) {
  const data = loadData();

  context.subscriptions.push(
    vscode.languages.registerCompletionItemProvider(
      { language: "jsw" },
      {
        provideCompletionItems(document, position) {
          try {
            const text = document.getText();
            const items = engine.provideCompletions(text, document.offsetAt(position), data);
            return items.map((plain) => toCompletionItem(document, plain));
          } catch (err) {
            console.error("[jaden-script] completion failed:", err);
            return [];
          }
        },
      },
      // These characters open a namespace (`Pen.`), a directive
      // (`#enable_feature<`) or an annotation (`// `).
      ".",
      "<",
      " ",
      "/"
    )
  );

  context.subscriptions.push(
    vscode.languages.registerHoverProvider(
      { language: "jsw" },
      {
        provideHover(document, position) {
          try {
            const info = engine.provideHover(
              document.getText(),
              document.offsetAt(position),
              data
            );
            if (!info) return undefined;
            return new vscode.Hover(
              info.contents,
              toRange(document, info.start, info.end)
            );
          } catch (err) {
            console.error("[jaden-script] hover failed:", err);
            return undefined;
          }
        },
      }
    )
  );
}

function deactivate() {}

module.exports = { activate, deactivate };
