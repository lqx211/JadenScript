# Roadmap — small, independently shippable pieces

The rule for every ticket: **one module or one behaviour, one acceptance test,
no ticket depends on more than one other ticket.** A ticket is done when its
test passes and `make test` stays green.

Legend: ✅ done · 🚧 in progress · ⬜ todo

---

## Phase 0 — Learn the target (research)

| # | Piece | Deliverable | Acceptance | Status |
| --- | --- | --- | --- | --- |
| 0.1 | sb3 container | `docs/SCRATCH-SB3-FORMAT.md` §1–2 | explains zip layout, assets, meta from the real export | ✅ |
| 0.2 | Block graph | same doc §3–5 | `next`/`parent`/`topLevel`/inputs/fields/mutations documented with real excerpts | ✅ |
| 0.3 | Calling convention | same doc §8 | explains procedure + broadcast receiver + `call` semantics | ✅ |
| 0.4 | Opcode shape extractor | `tools/opcode_report.py` | running it on `sample_np4.26_sb3/project.json` reproduces `docs/reference/opcode-shapes.md` | ✅ |
| 0.5 | VM opcode catalog | `tools/opcode_report.py --vm scratch-vm-develop --opcodes` | lists the 147 core/pen/music opcodes from source | ✅ |
| 0.6 | Language survey | `syntax.md` §1–8 | every construct in `sample.jsw` is named and classified | ✅ |

## Phase 1 — Front end (text → AST)

| # | Piece | Module | Acceptance | Status |
| --- | --- | --- | --- | --- |
| 1.1 | Keyword registry (single source of truth) | `jswc/keywords.py` | grammar generator and lexer both import it | ✅ |
| 1.2 | Token model | `jswc/tokens.py` | `Kind`, `Token`, positioned errors | ✅ |
| 1.3 | Lexer | `jswc/lexer.py` | strings/escapes, char literals, comments, ASI-aware newlines, `\` continuation | ✅ |
| 1.4 | AST | `jswc/ast_nodes.py` | data-only dataclasses, `iter_nodes` walker lives in codegen | ✅ |
| 1.5 | Parser: declarations | `jswc/parser.py` | `class`, `void`, params, dotted names, `// Sprite:` meta | ✅ |
| 1.6 | Parser: statements | `jswc/parser.py` | if/elif/else, while, for (`;` and `,`), switch, jump, `call`, visibility assignments | ✅ |
| 1.7 | Parser: expressions | `jswc/parser.py` | precedence table, postfix chains, postfix ternary, `**` right-assoc | ✅ |
| 1.8 | Diagnostics + lenient recovery | `jswc/parser.py` | unbalanced `(` / extra `)` in the reference file become warnings, not failures | ✅ |
| 1.9 | Comments → annotations | `jswc/parser.py` | `// hook:` and `// Sprite:` reach the AST node on the same line | ✅ |

## Phase 2 — Middle end (AST → meaning)

| # | Piece | Module | Acceptance | Status |
| --- | --- | --- | --- | --- |
| 2.1 | Scope model | `jswc/codegen.py` `Scope` | bare name → local, dotted → global, param → argument reporter | ✅ |
| 2.2 | Opcode registry | `jswc/opcodes.py` | `COMMANDS`/`REPORTERS`/`PROPERTIES` with input names + primitive ids | ✅ |
| 2.3 | List inference | `jswc/codegen.py` `_collect` | `X.add/get/length/...` anywhere marks `X` as a list before emission | ✅ |
| 2.4 | Control-flow lowering | `jswc/codegen.py` | `for` → for-each/repeat-until, `while` → forever/repeat-until, `switch` → if-chain with fall-through | ✅ |
| 2.5 | Call lowering | `jswc/codegen.py` | same-sprite → `procedures_call`, cross-sprite → `broadcastandwait`, `call` → `broadcast` | ✅ |
| 2.6 | Expression lowering | `jswc/codegen.py` | arithmetic/compare/logic/ternary/exponent/string helpers | ✅ |
| 2.7 | Unary/constant folding | `jswc/codegen.py` | `-5` emits `[4, "-5"]`, not `subtract(0,5)` | ✅ |

## Phase 3 — Back end (IR → sb3)

| # | Piece | Module | Acceptance | Status |
| --- | --- | --- | --- | --- |
| 3.1 | Block IR | `jswc/ir.py` | `Block`, `Lit`/`Ref`/`Inline`/`ShadowRef`, `serialize_input` implement shapes 1/2/3 | ✅ |
| 3.2 | Deterministic ids | `jswc/ir.py` `IdGen` | two builds of the same source are byte-identical | ✅ |
| 3.3 | Parent/next normalisation | `jswc/ir.py` `Project.normalize` | every non-top-level block has a parent, derived from inputs/next | ✅ |
| 3.4 | Project assembly | `jswc/sb3.py` | stage + sprites + variables/lists/broadcasts + costume asset | ✅ |
| 3.5 | Target shape parity | `tests/test_sb3.py` | target/costume key sets equal the reference project's | ✅ |
| 3.6 | Embeddable + Zip writer | `jswc/sb3.py` | `project.json` + `<md5>.svg` inside the zip | ✅ |
| 3.7 | Structural validator | `jswc/validator.py` | dangling links, phantom inputs, unknown var/list/broadcast ids | ✅ |
| 3.8 | Reachability checker | `tests/test_sb3.py` | every block reachable from a top-level script | ✅ |
| 3.9 | `extensionURLs` for community extensions | `jswc/opcodes.py` `EXTENSION_URLS` | project with `utilities` loads instead of "Unknown extension" | ✅ |

## Phase 4 — Tooling

| # | Piece | Module | Acceptance | Status |
| --- | --- | --- | --- | --- |
| 4.1 | CLI (`build`/`check`/`ast`/`tokens`) | `jswc/cli.py` | `python -m jswc build sample.jsw` writes a valid `.sb3` | ✅ |
| 4.2 | TextMate grammar generator | `tools/gen_tm_grammar.py` | every keyword in `keywords.py` appears in the generated JSON | ✅ |
| 4.3 | VS Code extension manifest | `vscode-jsw/` | language id `jsw`, `.jsw` files, grammar + snippets wired | ✅ |
| 4.4 | Snippets | `vscode-jsw/snippets/jsw.json` | class/hat/loop/switch/hook/call templates | ✅ |
| 4.5 | Completion data | `tools/gen_vscode_data.py` → `vscode-jsw/data/jsw.json` | keywords, 18 namespaces / 179 members, list methods, features, hats, annotations, all generated from the compiler tables | ✅ |
| 4.6 | Completion provider | `vscode-jsw/extension.js`, `completion.js` | `Pen.` member lists, list methods, hat snippets, document symbols, hover | ✅ |
| 4.7 | Makefile | `Makefile` | `make test`, `make demo`, `make sample`, `make grammar`, `make vscode-data` | ✅ |
| 4.8 | Installable package | `pyproject.toml` | `pip install -e .` exposes the `jswc` command | ✅ |
| 4.9 | Key arguments | `jswc/keycodes.py` | `Sensing.isKeyPressed(65)` / `(128)` resolve to VM key names; `W009` for keys the VM cannot report; names in completion | ✅ |
| 4.10 | Canvas metadata as syntax | `jswc/parser.py`, `codegen.py`, `decompile.py` | `metadata <name> pos x,y;`, `metadata <mode> <name> at …;`, `metadata note …;`, `stage: …;` and `<sprite>: name="…", …;` replace the `// at`, `// monitor`, `// comment`, `// Sprite:` and `// Stage:` comments; the comments still parse | ✅ |

## Phase 5 — Fidelity to the reference project

| # | Piece | Acceptance | Status |
| --- | --- | --- | --- |
| 5.1 | Compile the whole 828-line `sample.jsw` | valid project, 0 structural issues | ✅ |
| 5.2 | Performance budget | lex+parse+codegen+serialise < 100 ms (currently ~37 ms) | ✅ |
| 5.3 | Hats: green flag, clone, key hook, broadcast receiver | all four emitted | ✅ |
| 5.4 | Thread-local variables (`--locals=thread`) | `lmsTempVars2_*` blocks + extension | ✅ |
| 5.5 | Diagnostics instead of wrong code | every unmapped extension call is reported with line:col | ✅ |

---

## Phase 11 — User rules from `syntax.md` (v0.2)

| # | Piece | Acceptance | Status |
| --- | --- | --- | --- |
| 11.0 | `#enable_feature<…>;` switches | unknown feature warns, known features recorded on `Program` | ✅ |
| 11.1 | Variable planning: public/private, first allocation wins, definition checks | `tests/test_language_rules.py::GlobalAndInitialStateTests` | ✅ |
| 11.2 | Variable/list imports `.jsv` `.jsa` `.jsl` | missing file / bad JSON refuse to compile | ✅ |
| 11.3 | Sprite metadata methods 1, 2, 3 | precedence + conflict warnings + rename | ✅ |
| 11.4 | `public init { … }` | green-flag script | ✅ |
| 11.5 | Temp variables (`useTempVariables`) | `_x` → `lmsTempVars2` only when the feature is on | ✅ |
| 11.6 | Custom Reporters for `return expr` from private functions | needs `lmsCustomReporters` opcodes | ⬜ |
| 11.7 | Temp variables freed on **custom block return** | TurboWarp threads end with the script, not the block; needs a generated save/restore frame | ⬜ |
| 11.8 | Type-checked metadata (``class X(show=1)``) and unknown keys | currently unknown keys are ignored | ⬜ |

## Phase 12 — Decompiler (sb3 → jsw → sb3)

| # | Piece | Module | Acceptance | Status |
| --- | --- | --- | --- | --- |
| 12.1 | sb3 reader (zip, folder, project.json) | `jswc/sb3_read.py` | loads all three inputs, keeps assets | ✅ |
| 12.2 | Block graph → statements | `jswc/decompile.py` `_statement` | chains, substacks, if/elif/else, loops | ✅ |
| 12.3 | Expression printer with precedence | `_expr` / `_raw` | no redundant parentheses, no precedence bugs | ✅ |
| 12.4 | Procedure + broadcast recovery | `_prepass`, `_emit_procedure` | names come from the receiver broadcast | ✅ |
| 12.5 | Initial state + metadata emission | `_state_declarations`, `_emit_class` | variables/lists/sprite metadata preserved | ✅ |
| 12.6 | Asset extraction | `jswc/cli.py` `cmd_decompile` | costumes/sounds written next to the source | ✅ |
| 12.7 | Canonical round-trip harness | `tests/roundtrip.py` | ignores ids/coords, compares scripts + state | ✅ |
| 12.8 | Language constructs needed for exact inverses | `repeat`, list literals, `join`, `Control.broadcast`, hooks | every emitted statement recompiles | ✅ |
| 12.9 | Rename safety (invalid names, temp vars, params) | `_flat_identifier`, `thread_renames` | renames reported, state preserved | ✅ |
| 12.10 | Extension packs for 100 % of the reference | `jswc/extensions.py` + generated `extension_blocks.py` | **0 unsupported blocks** | ✅ |
| 12.11 | Stage scripts (`event_whenstageclicked`) | `stage { }` block + hooks | stage scripts round-trip | ✅ |
| 12.11a | Extension metadata extractor | `tools/extract_extension.py` | 132 blocks from 9 real extension sources | ✅ |
| 12.11b | Inert workspace blocks preserved | `looseBlocks()` | loose chains keep their subtrees | ✅ |
| 12.14 | `join(x, "")` printed as `x` | `decompile.py` | no `string()` noise | ✅ |
| 12.15 | Redundant `Class.` prefixes dropped and restored | `ns=` + `namespacedNames` | `Graph.doGraph` ↔ `doGraph` | ✅ |
| 12.16 | Custom-block labels preserved | `// proccode …` | multi-word proccodes round-trip | ✅ |
| 12.12 | `--strict-roundtrip` (fail on any rename/unsupported) | CLI flag | CI can enforce fidelity | ⬜ |
| 12.13 | Source maps: emit `// line N` comments for block ids | decompiler option | debuggable output | ⬜ |

Reference-project coverage today: **0/3871 unsupported blocks**, 71 variables,
40 lists, 10 sprites, stage scripts and 11 extensions preserved; the recompiled
project loads in TurboWarp. See [`DECOMPILER.md`](./DECOMPILER.md).

## Phase 13 — Ergonomics (v0.5)

| # | Piece | Acceptance | Status |
| --- | --- | --- | --- |
| 13.1 | `_name` is a thread variable when the feature asks for it | `#enable_feature<useTempVariables>` (or `--locals thread`); otherwise it is a sprite-local variable, because clones already copy those | ✅ (revised v0.9) |
| 13.2 | `private _x;` declaration without a value | parses, declares nothing in project state | ✅ |
| 13.3 | Decompile temp blocks into `private _x;` | round-trips | ✅ |
| 13.4 | `isTurboWarp()` / `isCompiled()` direct calls | constant `1`, overridable by a variable | ✅ |
| 13.5 | f-strings | compile to nested joins, `{{`/`}}` escapes | ✅ |
| 13.6 | Decompiler folds long join chains into f-strings | ≥5 pieces, canonical form treats join as associative | ✅ |
| 13.7 | `string()` removal | `join(x,"")` prints as `x` | ✅ |

## Phase 14 — Publishing (`doObfuscation`) and safety

| # | Piece | Module | Acceptance | Status |
| --- | --- | --- | --- | --- |
| 14.1 | `#enable_feature<hideObjectNames>;` | `jswc/obfuscate.py` | every class / variable / list name becomes a deterministic random identifier (thread variables too), Stage names keep the `Ns.name` shape | ✅ |
| 14.2 | Names stay wired | `obfuscate.py` `_rename_block` / `_rename_monitor` / `_rename_state_picker` | variable/list references, monitors, comment keys, JSON list pickers follow the rename; ids and block structure unchanged | ✅ |
| 14.2a | Broadcasts, costume/sound names, proccodes and text data stay | `obfuscate.py` | measured on the NotePlayer: renaming broadcasts broke `join(module, ".prop")` dispatch, so they are excluded | ✅ |
| 14.3 | Eval warnings | `jswc/codegen.py` `_check_eval` | a runtime-computed name in a broadcast, clone target, costume/sound menu, extension picker or `menu()` field raises `warning: eval: …` | ✅ |
| 14.4 | **User-friendly variable parameters** | `syntax.md` + parser | see below | ⬜ **INCOMPLETE — design only** |

Measured: the NotePlayer project (4,116 blocks, 108 monitors, 11 sprites)
compiled with `--enable hideObjectNames` keeps its opcode multiset, block ids,
monitor count and monitor→state wiring, and loads in TurboWarp; a small probe
project was green-flagged in the editor and its broadcast receiver still ran.

### 14.4 User-friendly variable parameters — **not implemented**

Today the decompiler writes **every** metadata knob it can see, as comments
around the declaration:

```jsw
public Player.notes = [];                                   // initial value
// monitor Stage.Player.notes list at 378,0 100x74 hidden     // monitor mode/pos/size
// costume centerx=48 centery=50 res=1                       // per asset
class main { // Sprite: main (0,0) size=100 direction=90 show=1 costume=0
             //        volume=100 layer=1 rotation=all draggable=0
```

That is exactly right for a *round trip* (nothing is lost) but it is not what a
human writes. The planned feature is a friendlier, parameterised declaration
that the compiler expands into the same directives, for example:

```jsw
public Player.notes = [] { monitor: list at 378,0 size 100x74 hidden };
public Tick.wait = 4     { monitor: slider 1..8 discrete at 156,177 };
class main               { sprite: size 100, costume 0, visible };
```

Open questions to settle before writing code:

* which knobs deserve inline parameters (monitor mode/range/position/size are
  the frequent ones; layer/rotation/draggable are rare),
* whether the friendly form should *replace* the comment directives in
  decompiled output or only be accepted on input (round-trip fidelity argues
  for "accepted on input, comments stay canonical"),
* how a parameterised declaration and a `// monitor` line for the same state
  should merge (last wins? error?),
* keeping the two forms in sync when the decompiler re-emits a source that used
  the friendly form.

Until this lands, the decompiler output stays "highest fidelity, least
friendly", and hand-written sources use the comment directives documented in
`syntax.md` ("Canvas, monitor and layer metadata").

## Phase 15 — CLI experience (v1.1)

| # | Piece | Module | Acceptance | Status |
| --- | --- | --- | --- | --- |
| 15.1 | ANSI palette + capability detection | `jswc/ui.py` `Theme` | 256-colour output on a TTY; plain text when piped, `NO_COLOR`, `TERM=dumb` or `--no-color` | ✅ |
| 15.2 | ASCII fallback | `ui.Theme.symbol` | `--plain` yields `OK`/`XX`/`|`/`-`, every glyph has a plain twin | ✅ |
| 15.3 | Breathing `◎ → ◉ → ◎` spinner | `ui.Spinner` | thread-driven, clears its own line, writes to stderr, disabled without a TTY | ✅ |
| 15.4 | Diagnostic registry `Exxxx`/`Wxxxx` | `diagnostics.py` | flat `E0001…`/`W0001…` list, one code per problem, every raise site stamps its own | ✅ |
| 15.5 | Caret snippets | `ui.format_diagnostic` | errors print the source line and a `^` under the column; EOF errors point past the last real line | ✅ |
| 15.6 | Build/decompile summary | `cli.cmd_build` / `cmd_decompile` | ✔ line + blocks · sprites · classes · time, extension list, warning tally, `-v` phase timings | ✅ |
| 15.7 | `jswc features` / `jswc codes` / no-arg help | `cli.py` | discoverable from the terminal, no docs needed | ✅ |
| 15.8 | `docs/CLI.md` | docs | tour of every command, exit codes, colour rules, recipes | ✅ |
| 15.9 | CLI tests | `tests/test_cli.py` | 21 tests: exit codes, codes, `--no-color`, `--plain`, classification | ✅ |
| 15.10 | Syntax codes | `lexer.py`, `parser.py` | missing token, unterminated string/comment/block, bad `for` header, bad metadata tail; the message names the token that was expected | ✅ |
| 15.11 | Semantic codes | `codegen.py`, `vars.py`, `validator.py` | undefined name, private access, arity, unsupported syntax, asset errors, dangling sb3 links | ✅ |
| 15.12 | Hints | `diagnostics.Message.hint` | every code carries a one-line fix, shown only with `--explain`, as a `help:` line under the caret | ✅ |
| 15.13 | Fallback classification | `diagnostics.LEGACY` | uncoded text still renders with a code and a hint; `E9999` is the last resort | ✅ |
| 15.14 | Information level `Ixxx` | `diagnostics.LEVELS` | renames are information, not warnings; `--loglevel error\|warn\|info`, default `warn` | ✅ |
| 15.15 | `for i in a..b` | `parser.parse_range_for`, `codegen._emit_range_for` | inclusive range, both bounds expressions, bracketed or not, dotted counter; temp `_i` never becomes state; nested reuse is `E0074`; reassignment is `W0021` | ✅ |
| 15.16 | Range round trip | `decompile._counted_for` | `repeat n` and `set`+`while`+`change by 1` both come back as `for … in a..b`; exclusive loops keep the three-part form | ✅ |

## Phase 16 — Pen 3D (`anim3d/`, see [`ANIM3D.md`](./ANIM3D.md))

| # | Piece | Where | Acceptance | Status |
| --- | --- | --- | --- | --- |
| 16.1 | `Tick.tick` scene clock | `anim3d/core.jsw` | pose is a pure function of `Tick.tick`, never of a frame counter; the show is relative to `showOrigin` so it runs the same inside a player already at tick 2159 | ✅ |
| 16.2 | Two-gate redraw budget | `frame()` | returns unless the tick moved **and** ≥ 1/60 s of wall clock passed; `drawn` climbs ~60/s inside a 180 FPS Turbo Mode VM, 1:1 at the player's ~40 Hz | ✅ |
| 16.3 | Projection buffer | `D3.proj` + `prepare()` | one pass over `D3.pos`, edges only read `D3.proj`; `prepare()` grows every buffer with `add` because `set` on an empty list is a no-op | ✅ |
| 16.4 | Near-plane flag | `D3.proj` slot 3 | vertices closer than `D3.nearZ` are flagged `0` and their edges skipped, so nothing smears across the stage | ✅ |
| 16.5 | Easing as a procedure | `easeBy(id, p)` | twelve curves (linear … outElastic) writing the shared `D3.ease` slot; unknown ids degrade to linear | ✅ |
| 16.6 | Frame verification without screenshots | `docs/ANIM3D.md` | `renderer.extractDrawableScreenSpace()` reads the pen skin in the same frame it draws; headless screenshots of a WebGL stage are unreliable | ✅ |
| 16.7 | Shot list + collapse transition | `D3.shots`, `shotAt`, `D3.wScale` | one scene drawn per frame; the outgoing shot shrinks to a point and the incoming one grows out of it | ✅ |
| 16.8 | Keyframed camera | `D3.keys`, `kfAt`, `applyKeys` | rows of `tick, yaw, pitch, dist, easeId`; the ease id belongs to the destination key; yaw closes the loop at +360° | ✅ |
| 16.9 | Orbit vs fly camera | `D3.orbit`, `basePitch`, `nearZ` | long geometry along `+Z` stays in front of the camera; floors recede towards the horizon (the pitch sign) | ✅ |
| 16.10 | Scene library | `buildPoly/Grid/Tunnel/Wave/Cube` | five wireframe scenes, 12–48 vertices each, sequenced by the shot list | ✅ |
| 16.11 | Embedding tool | `tools/embed_anim3d.py` | removes the demo clock, fences the copy between markers, idempotent, `--check` fails when stale | ✅ |
| 16.12 | Embed into NotePlayer | `userspace/wem_np4_26b5` | 5,464 blocks, 11 sprites, no new warnings; the player runs 15 s with zero note timeouts, `Player.skips = 0` | ✅ |
| 16.13 | Pen-layer arbitration | `D3.active`, `D3.onFrame` | the renderer clears once per frame and broadcasts `D3.afterFrame`; `Piano.draw()` skips its own erase while the show runs. Verified: 3,252 piano pixels with the hook, 162 without | ✅ |
| 16.14 | Sparse floor grid | `buildGrid` | five rows by seven lines plus four posts; reads as a plane from any sway angle instead of collapsing into a band | ✅ |
| 16.15 | Filled faces | `anim3d/` | painter's algorithm over the same `D3.proj` buffer, back-face culling from the winding order | ⬜ |
| 16.16 | Per-depth pen size / colour | `render()` | near edges brighter and thicker; one `setPenSize` per edge is the whole cost | ⬜ |
| 16.17 | Running order from the film | `tools/gen_anim3d_show.py` | 15 chapters / 274 shots / 7,346 ticks resolved off the measured downbeat grid; the block is fenced, idempotent and checked by the test suite | ✅ |
| 16.18 | Motif library | `drawScene` | 16 wireframe motifs, chosen from the shot name, varied by the shot's `param` | ✅ |
| 16.19 | Yaw damping for flat motifs | `view(…, damp)` | a flat motif that keeps the keyframe sway goes edge-on and draws nothing: glyphs averaged 427 px before, 3,623 after | ✅ |
| 16.20 | Per-shot camera recipes | generator | hand-authored yaw/pitch/dist per shot instead of one rule per motif | ⬜ |
| 16.21 | Real landmark timing | generator | resolve `keys(T).l5.start` and friends against the lyric table instead of interpolating between bar landmarks | ⬜ |

## Next small pieces (unblocked, pick any)

### 6. Extension mapping packs

Each pack is one dictionary in `jswc/opcodes.py` + one test.

| # | Namespace | Opcodes to map | Notes |
| --- | --- | --- | --- |
| 6.1 | `console.*` | `sipcconsole_Journal/Information/Warning/Error/debug` | extension `sipcconsole` |
| 6.2 | `Timer.*` | `lmsTimers_valueOfTimer`, `lmsTimers_startResetTimer` | extension `lmsTimers` |
| 6.3 | `JSON.*` | `skyhigh173JSON_json_*` | needs value-typed calls, see 6.6 |
| 6.4 | `Runtime.*` | `runtimeoptions_getEnabled/getFramerate/...` | booleans + menu fields |
| 6.5 | `Util.*` | `utilities_regexReplace`, `utilities_stringToBoolean` | extension `utilities` |
| 6.6 | Chained calls | `a.get(1).get(2)` → `shadow`/`JSON` reporter | needs call-result typing |

### 7. Output quality

| # | Piece | Why |
| --- | --- | --- |
| 7.1 | `Motion.x = …; Motion.y = …` → `motion_gotoxy` | matches the reference (`gotoxy -239 -45`) |
| 7.2 | `x = x + 1` → `data_changevariableby` | smaller scripts |
| 7.3 | Boolean custom-block parameters (`%b` + `argument_reporter_boolean`) | needed by `Player.checkIfDisp`‑style code |
| 7.4 | Warp policy per function (`// warp`, `// no-warp`) | avoid one global flag |
| 7.5 | Monitors for `Variables.showVariable(x)` | `monitors[]` entries, not just the show block |
| 7.6 | Source maps: block id → `line:col` stored as project comments | debug the generated project |

### 8. Project integration

| # | Piece | Why |
| --- | --- | --- |
| 8.1 | `--base-project existing.sb3` | inject compiled sprites into a real project with its costumes/sounds |
| 8.2 | Asset passthrough | reuse costumes/sounds referenced by name |
| 8.3 | `--only-sprite NAME` | incremental rebuilds |
| 8.4 | `jswc fmt` | one canonical formatting, so diffs stay small |
| 8.5 | `import "asset.png" as name;` | ✅ costumes/sounds embedded with real md5 assets (`tests/test_assets.py`) |

### 9. Round-trip / confidence

| # | Piece | Why |
| --- | --- | --- |
| 9.1 | `jswc decompile project.json` → `.jsw` | proves the lowering table both ways; also produces more fixtures |
| 9.2 | Opcode coverage test | compile the reference and diff its opcode multiset against the real project |
| 9.3 | Load-in-VM test (Node + `scratch-vm`) | **verified manually**: both builds load in the TurboWarp editor; automating it needs `npm i`, which was blocked | 🚧 |
| 9.4 | Golden project fixtures | store a small `.sb3` per language feature |

### 10. Editor

| # | Piece | Why |
| --- | --- | --- |
| 10.1 | Language server with push diagnostics | reuse `Parser.diagnostics` + `Codegen.diagnostics` |
| 10.2 | Hover: show the Scratch opcode a namespace call maps to | teaching aid |
| 10.3 | Go-to-definition for `Namespace.member` | navigate 800-line files |
| 10.4 | Completion for `jswc/opcodes.py` namespaces | discoverability |

---

## Suggested order

```
6.1 → 6.2 → 7.1 → 7.2 → 8.1 → 9.1 → 9.2 → 10.1
```

Rationale: extension packs unblock real code (`console`, `Timer` are the two
most frequent diagnostics in `sample.jsw`); 7.1/7.2 shrink output; 8.1 makes
the compiler usable on existing projects; 9.1 turns the reference project into
an endless source of fixtures; 10.1 is the point where editing `.jsw` stops
being blind.

## How to add a language feature

1. Write the failing test first (`tests/test_parser.py` or `tests/test_codegen.py`).
2. Add the AST node in `jswc/ast_nodes.py`.
3. Parse it in `jswc/parser.py`.
4. Lower it in `jswc/codegen.py`; if it needs a new block, add the opcode to
   `jswc/opcodes.py` and verify the shape with `tools/opcode_report.py`.
5. Document it in `syntax.md` §5/§6 and in the lowering table §8.
6. If it is a keyword, run `make grammar` so the editor highlights it.
