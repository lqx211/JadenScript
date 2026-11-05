# How a TurboWarp / Scratch 3 project actually works

Everything below was verified against the reference project in this repo
(`sample_np4.26_sb3/project.json`, a TurboWarp export of NotePlayer 4), not
copied from memory. Numbers and excerpts come from that file.

## 1. The container

A `.sb3` file is a **zip archive**:

```
project.json          ← the entire program, one JSON document
<md5>.svg / <md5>.png ← costumes, named by the MD5 of their bytes
<md5>.wav             ← sounds, named the same way
```

Asset names are content-addressed, so `md5ext` in `project.json` must equal
`assetId + "." + dataFormat`, and the file with that name must exist in the
zip, otherwise the project fails to load.

The reference export has 347 files: 1 `project.json`, 16 SVGs and 330 WAVs.

## 2. Top level of `project.json`

```jsonc
{
  "targets":   [ /* stage first, then one per sprite */ ],
  "monitors":  [ /* 108 in the reference: variable/list watchers */ ],
  "extensions": ["pen", "music", "lmsTempVars2", "utilities", …],
  "extensionURLs": { /* TurboWarp only: id → url for custom extensions */ },
  "meta": {
    "semver": "3.0.0",
    "vm": "0.2.0",
    "agent": "",
    "platform": { "name": "TurboWarp", "url": "https://turbowarp.org/" }
  }
}
```

`targets[0]` is always the Stage (`"isStage": true`); every later target is a
sprite. In the reference there are 11 targets: `Stage`, `core`, `builder`,
`graphical`, `console`, `graph`, `pseudocode`, and four `synth.*` sprites.

## 3. A target

Stage targets carry the globals; sprites carry their own copies of the same
keys plus position/appearance:

```jsonc
// sprite
{
  "isStage": false, "name": "core",
  "variables": {}, "lists": {}, "broadcasts": {},
  "blocks": { … }, "comments": {},
  "currentCostume": 0, "costumes": [ … ], "sounds": [],
  "volume": 100, "layerOrder": 1,
  "visible": true, "x": -196, "y": 121, "size": 100, "direction": 90,
  "draggable": false, "rotationStyle": "all around"
}
```

```jsonc
// stage extras
{ "tempo": 60, "videoTransparency": 50, "videoState": "on",
  "textToSpeechLanguage": null }
```

`variables` / `lists` / `broadcasts` are **dictionaries keyed by id**, not
arrays:

```jsonc
"variables": {  "jEk@4|i[#Fk?(8x)AV.-my variable": ["Player.triggers", 289] }
"lists":     {  "6dBmR)D[s0:!p;TK!,s4": ["Player.Notes", []] }
"broadcasts":{  "td:I9*aQ5*?{ulPSHP8O": "Piano.draw" }
```

Two observations that shape the whole compiler design:

1. **Variable names are namespaced strings.** The reference project has
   `Player.triggers`, `Tick.tick`, `Piano.counter0`, … — the dot is part of the
   name. JadenScript's `Player.triggers` therefore maps 1:1 to a Stage
   variable, no symbol mangling required.
2. **Broadcast names are qualified method names.** `Piano.draw`,
   `Player.timerCheck`, `Player.main` — every public `Namespace.method()` in
   the source becomes a broadcast of the same name.

## 4. Blocks: one flat dictionary

`blocks` is a flat map `id → block`. Control flow is expressed with `next`
(statement ordering) and `parent` (ownership), never with nesting.

```jsonc
"Pf": {
  "opcode": "event_whenbroadcastreceived",
  "next": "Pg", "parent": null,
  "inputs": {}, "fields": { "BROADCAST_OPTION": ["Piano.draw", "td:I9*aQ5*?{ulPSHP8O"] },
  "shadow": false, "topLevel": true, "x": 140, "y": -419
}
```

Rules the VM relies on:

* A script head has `topLevel: true`, `parent: null` and an `x`/`y` position.
* Every other block has a `parent` (the block that owns it) and a `next`
  (or `null`).
* Reporter blocks live in the `inputs` of their consumer — their `parent` is
  the consumer, and their `next` is `null`.
* `shadow: true` marks dropdowns and procedure prototypes.

## 5. Inputs: the `[shape, payload]` encoding

Each entry of `inputs` is a 2-element array whose first element is a shape code:

| shape | meaning | example |
| --- | --- | --- |
| `1` | literal only | `"X": [1, [4, "-239"]]` |
| `1` with a string | shadow block id | `"custom_block": [1, "aS"]` |
| `2` | block, no shadow (booleans, substacks) | `"SUBSTACK": [2, "wo"]` |
| `3` | block + obscured shadow | `"NUM1": [3, "PI", [10, ""]]` |

The second element of a literal is `[primitiveId, value]`:

| id | primitive | id | primitive |
| ---: | --- | ---: | --- |
| 4 | number | 9 | colour |
| 5 | positive number | 10 | text |
| 6 | whole number | 11 | broadcast |
| 7 | integer | 12 | variable |
| 8 | angle | 13 | list |

So `Pen.setPenColor(expr)` compiles to
`"COLOR": [3, "wq", [9, "#a2a2a2"]]` — the real block plus the default colour
shadow that Scratch shows when the reporter is removed.

`fields` is different: it is always `[value, id]` where `id` is only meaningful
for variables, lists and broadcasts:

```jsonc
"fields": { "VARIABLE": ["Tick.wait", ":@+L{K=WaD+EBn.EUG6f"] }
"fields": { "LIST":     ["Piano.wks", "*0/n}:u89;8D-vjk.dkA"] }
"fields": { "KEY_OPTION": ["space", null] }
```

## 6. Mutations

Blocks that are not fixed-shape carry a `mutation` object:

```jsonc
// custom block definition + its prototype (shadow)
"procedures_definition": { "inputs": { "custom_block": [1, "Py"] } }
"procedures_prototype": {
  "shadow": true,
  "mutation": {
    "tagName": "mutation", "children": [],
    "proccode": "RGB %s %s %s",
    "argumentids": "[\"!aTE-…\",\"6)oW…\",\"4:-n…\"]",
    "argumentnames": "[\"r\",\"g\",\"b\"]",
    "argumentdefaults": "[\"\",\"\",\"\"]",
    "warp": "false"
  }
}
```

`warp` is the "run without screen refresh" flag and is a **string**. The call
site repeats `proccode`, `argumentids` and `warp` (but not the names), and its
argument inputs are keyed by argument id.

`control_stop` also needs a mutation: `{ "hasnext": "false" }` when the option
is `this script`.

## 7. Hats

| source construct | opcode | payload |
| --- | --- | --- |
| `void greenFlag()` | `event_whenflagclicked` | — |
| `void whenCloned()` | `control_start_as_clone` | — |
| `// hook: when [space] key pressed` | `event_whenkeypressed` | field `KEY_OPTION` |
| `public void Ns.Name()` | `event_whenbroadcastreceived` | field `BROADCAST_OPTION` |

## 8. The reference project's calling convention

This is the single most important thing to copy:

* `public void Piano.draw()` produces **two** scripts:
  1. a `procedures_definition` whose proccode is `"Draw"` (namespace stripped,
     because custom blocks are per-sprite), and
  2. a `event_whenbroadcastreceived` hat named `"Piano.draw"` whose body is a
     single `procedures_call` back into that definition.
* `call Piano.draw();` (fire and forget) compiles to `event_broadcast`.
* `Piano.draw();` (blocking) compiles to `procedures_call`, and only works
  inside the same sprite; cross-sprite blocking calls go through
  `event_broadcastandwait`.

That is why `call` is documented in the source as "do not wait for the called
function to finish" — Scratch has no async call primitive, so the compiler
uses the broadcast scheduler.

## 9. Extensions

`extensions` is a list of extension ids; their opcodes are prefixed with the id
(`pen_clear`, `music_playNoteForBeats`, `lmsTempVars2_setThreadVariable`, …).
TurboWarp additionally stores `extensionURLs` for extensions loaded from a URL.

This is not optional for community extensions. Loading a project that lists
`utilities` without an `extensionURLs` entry aborts with:

```
Error: Unknown extension: utilities
```

Verified in the TurboWarp editor. The reference project maps nine ids:

```jsonc
"extensionURLs": {
  "lmsTempVars2":  "https://extensions.turbowarp.org/Lily/TempVariables2.js",
  "utilities":     "https://extensions.turbowarp.org/utilities.js",
  "skyhigh173JSON":"https://extensions.turbowarp.org/Skyhigh173/json.js",
  "lmsTimers":     "https://extensions.turbowarp.org/Lily/MoreTimers.js",
  "lmscomments":   "https://extensions.turbowarp.org/Lily/CommentBlocks.js",
  "sipcconsole":   "https://extensions.turbowarp.org/-SIPC-/consoles.js",
  "runtimeoptions":"https://extensions.turbowarp.org/runtime-options.js",
  "gsaWebsocket":  "https://extensions.turbowarp.org/godslayerakp/ws.js",
  "lmsMoreEvents": "https://extensions.turbowarp.org/Lily/MoreEvents.js"
}
```

Built-in extensions (`pen`, `music`, …) ship with the editor and must *not* be
listed there.

The reference project uses 11 extensions:

```
lmsTempVars2, utilities, skyhigh173JSON, lmsTimers, music, lmscomments,
sipcconsole, runtimeoptions, gsaWebsocket, lmsMoreEvents, pen
```

`lmsTempVars2` is worth calling out: bare local variables ("thread variables")
compile to `lmsTempVars2_setThreadVariable` / `getThreadVariable` instead of
scratch variables. jswc supports both modes — see
[`JADENSCRIPT-SPEC.md`](./JADENSCRIPT-SPEC.md#locals).

## 10. Opcodes used in the reference project

See [`reference/opcode-shapes.md`](./reference/opcode-shapes.md) for the full
table of opcode → inputs/fields, generated by
`tools/opcode_report.py`. Regenerate it whenever a new project is analysed.

## 11. Validating a generated project without opening Scratch

`jswc build` runs a structural validator that checks:

* every `next` / `parent` / input reference points at an existing block,
* every non-top-level block has a parent and every top-level block has none,
* `VARIABLE` / `LIST` / `BROADCAST_OPTION` ids exist on the target or stage,
* every block is reachable from a top-level script.

`tests/test_sb3.py` additionally compares the generated target/block/costume
key sets against the reference project, so the shape can't silently drift.
