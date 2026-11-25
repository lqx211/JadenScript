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

function toLocation(document, nameStart, name) {
  const start = document.positionAt(nameStart);
  const end = document.positionAt(nameStart + name.length);
  return new vscode.Location(document.uri, new vscode.Range(start, end));
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

  // The argument hint for a call. `(` is the character that opens one and `,`
  // the one that moves to the next parameter, so both retrigger it.
  context.subscriptions.push(
    vscode.languages.registerSignatureHelpProvider(
      { language: "jsw" },
      {
        provideSignatureHelp(document, position) {
          try {
            const help = engine.provideSignatureHelp(
              document.getText(),
              document.offsetAt(position),
              data
            );
            if (!help) return undefined;
            const signature = new vscode.SignatureInformation(
              help.label,
              help.doc ? new vscode.MarkdownString(help.doc) : undefined
            );
            signature.parameters = help.parameters.map(
              (param) => new vscode.ParameterInformation(param.label)
            );
            const result = new vscode.SignatureHelp();
            result.signatures = [signature];
            result.activeSignature = 0;
            result.activeParameter = help.activeParameter;
            return result;
          } catch (err) {
            console.error("[jaden-script] signature help failed:", err);
            return undefined;
          }
        },
      },
      "(",
      ","
    )
  );

  // Go to definition: a class, a My Block, a variable, a list.
  context.subscriptions.push(
    vscode.languages.registerDefinitionProvider(
      { language: "jsw" },
      {
        provideDefinition(document, position) {
          try {
            const hit = engine.provideDefinition(
              document.getText(),
              document.offsetAt(position),
              data
            );
            if (!hit) return undefined;
            return toLocation(document, hit.nameStart, hit.name);
          } catch (err) {
            console.error("[jaden-script] definition failed:", err);
            return undefined;
          }
        },
      }
    )
  );

  // The outline: classes, their blocks and their state.
  context.subscriptions.push(
    vscode.languages.registerDocumentSymbolProvider(
      { language: "jsw" },
      {
        provideDocumentSymbols(document) {
          try {
            const symbols = engine.documentSymbols(document.getText(), data);
            const k = vscode.SymbolKind;
            const kindOf = {
              class: k.Class,
              method: k.Method,
              function: k.Function,
              variable: k.Variable,
            };
            return [...symbols.values()]
              .filter((symbol) => symbol.nameStart)
              .map((symbol) => new vscode.SymbolInformation(
                symbol.name,
                kindOf[symbol.kind] || k.Variable,
                symbol.detail,
                toLocation(document, symbol.nameStart, symbol.name)
              ));
          } catch (err) {
            console.error("[jaden-script] symbols failed:", err);
            return [];
          }
        },
      }
    )
  );
}

function deactivate() {}

module.exports = { activate, deactivate };
