# JS2Twrp — JadenScript → TurboWarp compiler

`jswc` compiles **JadenScript** (`.jsw`), a small JavaScript-flavoured language,
into a Scratch 3 / TurboWarp project (`.sb3`).

The language is modelled on the pseudo-code that [`sample.jsw`](./sample.jsw)
(NotePlayer 4 by lqx211) is written in, and the reference export in
[`sample_np4.26_sb3/`](./sample_np4.26_sb3) is used as ground truth for every
block shape the compiler emits.

```jsw
class player { // Sprite: player (0,0) size=100 direction=90 show=1
    public void greenFlag() {
        public Player.score = 0;
        Pen.eraseAll();
        for i in 1..10 {              // inclusive counter → repeat-with-counter
            Piano.notes.add(i*10);
        }
    }

    public void Player.tick(){
        Player.score++;
        Pen.setPenColor(Player.score % 16777216);
        Pen.penDown();
        Motion.x += 4;
        Pen.penUp();
    }
}
```

## Quick start

```bash
make test                       # 908 unit tests, about fourteen minutes
make hello                      # examples/hello.jsw → build/hello.sb3 (the smallest project)
make demo                       # examples/demo.jsw → build/demo.sb3
make assets                     # examples/assets_demo.jsw (imports a costume) → build/assets_demo.sb3
make rules                      # examples/rules_demo.jsw (features, state, imports) → build/rules_demo.sb3
make roundtrip                  # decompile + recompile the demos and the reference project
make sample                     # the full 828-line reference → build/sample.sb3
python3 -m jswc check sample.jsw
python3 -m jswc ast examples/demo.jsw
```

Open `build/demo.sb3` in [TurboWarp](https://turbowarp.org/) to inspect the
generated blocks.

Optional install:

```bash
pip install -e .      # provides the `jswc` command
```

## CLI

```bash
jswc build examples/hello.jsw -o build/hello.sb3
```

```
◎ Compiling hello.jsw
✔ Compiled build/hello.sb3
  blocks 2 · sprites 1 · classes 1 · time 4 ms
```

| command | purpose |
| --- | --- |
| `jswc build file.jsw -o out.sb3` | compile to a zipped project |
| `jswc build file.jsw --project-json` | emit an unzipped `project.json` instead |
| `jswc build file.jsw --locals thread` | bare variables become `lmsTempVars2` thread variables |
| `jswc build file.jsw --warp` | mark every custom block "run without screen refresh" |
| `jswc build file.jsw --enable F` | turn on a language feature (`jswc features` lists them) |
| `jswc build file.jsw --strict` | exit 1 when any diagnostic is produced |
| `jswc check file.jsw [--strict]` | parse only, print diagnostics |
| `jswc decompile project.sb3 -o out.jsw` | turn a project back into source (+ extracted assets) |
| `jswc decompile project.sb3 --skip-assets` | source only; big lists still go to `.jsa` files |
| `jswc decompile project.sb3 --max-inline 200` | keep lists up to 200 items inline |
| `jswc ast file.jsw [--json]` | dump the AST |
| `jswc tokens file.jsw` | dump the token stream |
| `jswc codes [--severity warning] [-s text]` | browse the `Exxxx`/`Wxxxx`/`Ixxxx` diagnostics |
| `jswc -l info check file.jsw` | include information (`Ixxx`) in the report |
| `jswc features` | list `--enable` flags |
| `python3 tools/compare_projects.py a.sb3 b.sb3` | prove a round trip: state, assets, monitors and block literals must match |
| `make i386` | assemble the 80386 BIOS and a boot sector, boot them in the model, and build the Scratch machine |
| `make i386-check` | boot the Scratch machine and the model side by side, diffing every instruction |
| `make i386-setup-check` | the same, with Delete pressed: the whole BIOS setup screen, text and attributes |

With `--locals thread` (and always for a name that starts with `_`), a bare
variable is a **thread** temporary, not a procedure local — two procedures on the
same call chain share it. A helper that reuses a caller's `_name` silently
overwrites it, which is why the RISC-V core's page-table walker prefixes its
scratch variables `_tg`.

Diagnostics carry a stable code and point at the exact column; `--explain` adds
the fix:

```
  ✖ E0011  bad.jsw:1:12  expected '{', found 'extends'
    │ class main extends Sprite {
    │            ^
    help: Bodies open with { and close with }.
```

Codes are a flat `E0001`, `E0002`, … / `W0001`, `W0002`, … / `I0001`, … list
with no hidden meaning — `jswc codes` prints the whole registry from
[`jswc/diagnostics.py`](./jswc/diagnostics.py). Hints stay hidden until
`--explain`, and `Ixxx` information until `--loglevel info` (the default is
`warn`).

Colour and the `◎` spinner turn themselves off when the output is redirected;
`--no-color`, `--plain` (ASCII), `NO_COLOR=1` and `TERM=dumb` force plain text,
`FORCE_COLOR=1` forces colour. Exit codes: `0` ok, `1` build failed, `2` source
error. Full tour: [`docs/CLI.md`](./docs/CLI.md).

## Extensions

Blocks are reachable through their namespace (`Pen.move`, `JSON.arrayGet`,
`microbit.displayText`), and the micro:bit module has the spellings the
hardware deserves: `microbit.buttonA` is `"A"`, `microbit.display(0b111)` is
the 5x5 picture as a number, and each trigger is a hat
(`// hook: when [A] button pressed`). Opcodes and menu items are taken from
`scratch-vm/src/extensions/scratch3_microbit/index.js`, so a rebuilt project
loads in Scratch and TurboWarp unchanged.

## State you can see

`Temp.setRuntimeVariable` normally hides a variable inside an extension, where
no monitor, round-trip or green-flag reset can reach it. The compiler lowers it
to an ordinary Stage variable initialised to `0` and generates one Stage script
that zeroes them all on the green flag; a project that deletes or enumerates
runtime variables keeps the extension calls instead, so semantics never drift.
`Variables.*`, `Lists.*` and `Clone.*` lower to the ordinary blocks, and
`Stage.x` reaches stage state with the same spelling the monitor annotations use.

## Pipeline

```
source.jsw
   │  jswc/lexer.py      tokenise (newline-sensitive, ASI, `\` continuation)
   ▼
tokens
   │  jswc/parser.py     recursive descent → AST, lenient paren recovery
   ▼
ast_nodes.py
   │  jswc/codegen.py    scoping, opcode registry, control-flow lowering
   ▼
ir.py (blocks, inputs, fields, mutations)
   │  jswc/sb3.py        targets, assets, zip
   ▼
out.sb3 ── jswc/validator.py  structural checks before writing
```

Everything is deterministic: two builds of the same source produce identical
`project.json`, which keeps diffs and golden tests meaningful.

## Repository layout

```
jswc/            compiler package
  keywords.py    single source of truth for keywords (compiler + editor)
  lexer.py       scanner
  parser.py      recursive-descent parser
  ast_nodes.py   AST
  opcodes.py     JadenScript namespace → Scratch opcode table
  codegen.py     AST → block IR
  ir.py          block model + deterministic ids
  sb3.py         project.json / zip writer
  sb3_read.py    project reader (zip / folder / project.json)
  decompile.py   sb3 → JadenScript
  extensions.py  community-extension call resolution (aliases + generic)
  extension_blocks.py  generated block metadata (132 blocks, 9 extensions)
  validator.py   structural validation
  cli.py         command line
tests/           unittest suite (no third-party dependencies)
tools/           opcode_report.py, gen_tm_grammar.py
vscode-jsw/      VS Code extension: TextMate grammar + completion
nvim/            Neovim/Vim plugin: syntax, ftdetect, ftplugin
anim3d/          pen-only 3D renderer: shots, keyframes, five scenes (docs/ANIM3D.md)
userspace/i386/  an 80386 PC that runs inside TurboWarp: BIOS, setup utility,
                 boots a disk, a PXE client, an RTL8139 and a wire to the host
                 (docs/I386.md)
userspace/rv64/  the same idea for RISC-V: bios.S, a console, a disk, a shell
                 (docs/RV64.md)
docs/            format notes, roadmap, generated opcode reference
examples/        hello.jsw, minimal.jsw, demo.jsw, assets_demo.jsw, rules_demo.jsw
                 + assets/, hello/, rules_data/
projects/        whole programs in JadenScript: Linux.jsw (a Linux boot), TheMast3D.jsw
                 (a 13k-block 3D renderer), NotePlayer-4.jsw (GPL-3.0, see NOTICE.md)
```

## Version control

The sources are in git; `build/`, `userspace/*/*.bin|.lst|.elf|.img` and every
`.sb3` are outputs and stay out of it. The large example projects under
`userspace/` (`linux/`, `wem_np4_26b4/`, `wem_np4_26b5/`, `i386_example/`) and
the sample media in `assets/` are ignored too -- they are hundreds of megabytes
of somebody else's project, not this one's source.

```
git log --oneline          # what changed
make i386                  # nasm + image + sb3
python3 -m unittest tests.test_i386
```

## Documentation

* [`syntax.md`](./syntax.md) — the language reference and lowering table.
* [`docs/SCRATCH-SB3-FORMAT.md`](./docs/SCRATCH-SB3-FORMAT.md) — how a
  TurboWarp project is actually structured, verified against the reference.
* [`docs/reference/opcode-shapes.md`](./docs/reference/opcode-shapes.md) —
  every opcode in the reference project with its inputs and fields.
* [`docs/DECOMPILER.md`](./docs/DECOMPILER.md) — the reverse direction and its
  exactness guarantees.
* [`docs/ANIM3D.md`](./docs/ANIM3D.md) — pen-only 3D: `Tick.tick` as the scene
  clock, a shot list with collapse transitions, keyframed camera moves, the
  60 Hz redraw budget inside a 180 FPS VM, how the renderer shares the single
  project-wide pen layer with a host's own overlay, and how to verify a frame
  without trusting a screenshot.
* [`docs/I386.md`](./docs/I386.md) — the 80386 machine: the CPU, the chipset,
  POST, the setup utility, the boot menu, the network card and its host wire,
  and the long list of bugs each of those found.
* [`docs/RV64.md`](./docs/RV64.md) — the RISC-V machine, and the parts of the
  language it had to grow first (bit fields, unsigned compares, packed memory).
* [`docs/ROADMAP.md`](./docs/ROADMAP.md) — the work cut into small,
  independently shippable pieces.

## Editor support

`vscode-jsw/` is a ready-to-use extension (language id `jsw`, `.jsw` files):

**Colouring** — keywords, modifiers, reserved words (flagged as
`invalid.illegal`), `#enable_feature<…>;` directives with the feature name, the
import path and alias, class names, `void` names, namespace detection
(`support.class` for `Pen`, `Music`, `Player`, …), method/property highlighting
after `.`, hex / binary / octal / decimal / leading-dot numbers, and every
comment the decompiler writes (`// Sprite:`, `// hook:`, `// proccode`,
`// receiver at`, `// monitor`, `// watch`, `// at`, `// warp`) including the
quoted names inside them.

The syntax the decompiler emits is coloured as syntax, not as text:

| written | scopes |
| --- | --- |
| `for i in 1..10` | `for`/`in` keywords, the counter as a loop variable, `..` as a range operator |
| `stage: costume=0, volume=100;` | `stage` keyword, every property key as a keyword, the `=` and values separately |
| `Empty: name="Empty", x=36;` | the sprite name as a type, the keys as keywords |
| `metadata list Stage.Lyrics.zh at 5,5 hidden;` | `metadata` declaration, `list`/`large`/`slider` as monitor modes, `at`/`pos`/`hidden`/`shown` as metadata words, the target as a member |
| `// costume centerx=0 res=1` | `costume`/`sound` as annotation words, `centerx`/`res`/`rate`/`samples` as annotation parameters |
| `public c_en = …` vs `private _i;` | both state names share one colour; a `_name` temp variable gets a related but dimmer one |
| `playNote(60)` / `LogStatic("x")` / `_q5(1,2)` / `Pen.setPenSize(2)` | every call is a function: a custom block, an extension method, a compiler builtin (`join`, `log`, `block`) — and `_q5(...)` is a *call*, never a temp variable |

After a `.` the completion engine asks the document what the receiver *is*
before offering anything: a runtime namespace (`Pen`, `JSON`, `Temp`) wins, then
a class in this file, then a prefix of dotted stage state (`Player.`), and only
a name the file declares (or proves, via `x.y.get(…)`) to be a list gets the
list methods. An unknown name gets nothing rather than a menu of guesses.

Those colours ship as `editor.tokenColorCustomizations` defaults scoped to
`[jsw]` ([`vscode-jsw/package.json`](./vscode-jsw/package.json)), so any theme
still wins if you override them. A test walks the grammar and fails if it emits
a token scope with no colour rule, so a new construct cannot arrive uncoloured. f-strings are tokenised with real nested
holes, so `f"{a} {block(\"x\")}"` keeps its inner strings.

**Completion** (`extension.js` + `completion.js`) —

| you type | you get |
| --- | --- |
| `pub`, `rep`, `whi` | keywords and modifiers |
| `jo`, `blo` | the builtins that become blocks (`join`, `block`, `log`, …) |
| `Pe` | `Pen` and, on accept, `Pen.` ready to type into |
| `Pen.` / `Pen.setPe` | every pen block, with its signature and opcode |
| `myList.` | the list methods (`add`, `get`, `length`, …) — **only** when the document really declares `myList` as a list |
| `Player.` | the leaves of dotted stage state (`Player.notes`, `Player.score`) instead of list methods |
| `Lyrics.` | that class's own state and methods |
| `JSON.` / `console.` / `Timer.` | community-extension members (friendly aliases) |
| `microbit.` | the micro:bit blocks, its menu constants (`buttonA`, `front`) and both hat snippets |
| `whenClo` | a hat snippet: `public void whenCloned() { … }` |
| `// proc` | annotations (`proccode`, `receiver at`, `monitor`, …) |
| `metadata ` | the monitor modes (`list`, `large`, `slider`, `normal`, `default`) |
| `metadata list Stage.x ` | `at`, `hidden`, `shown`, `range`, `pos`, … |
| `metadata list Stage.` | the state this project can monitor (`Stage.Graph.dpoint_x`, …) |
| `stage: ` / `Empty: ` | the target's properties, with the `=` already typed |
| `// costume ` / `// sound ` | `centerx=`, `centery=`, `res=`, `rate=`, `samples=` |
| `#enable_feature<` | the four feature switches |
| a variable/method/class name | symbols already used in the file |

Completion is served from `data/jsw.json`, a generated snapshot of the
compiler's own tables, so a block that compiles is a block that autocompletes.
Hover shows the same information. `editor.wordBasedSuggestions` is switched off
for `.jsw` so only the real blocks appear.

**Snippets** — 20 templates for classes, hats, loops, switches and hooks.

The grammar is generated, never edited by hand:

```bash
make grammar       # python3 tools/gen_tm_grammar.py    (VS Code colouring)
make vscode-data   # python3 tools/gen_vscode_data.py   (VS Code completion)
make nvim-syntax   # python3 tools/gen_vim_syntax.py    (Neovim colouring)
make nvim-check    # highlight a fixture with a real nvim
```

`tests/test_grammar.py` and `tests/test_extension.py` keep this honest: the
committed JSON must match the generators, every regex must compile, every
`#include` must resolve, every keyword/namespace/annotation/literal must reach a
scope, every namespace member used in `sample.jsw` / `TheMast3D.jsw` must be
offered by completion, and `extension.js` must activate against a stub `vscode`
API (`tools/check_extension.js`). Those tests stand in for TextMate with regular
expressions; for the real thing, run the grammar through the same engine VS Code
uses (needs `npm install` once at the repo root):

```bash
node tools/check_grammar_real.js [file.jsw ...]   # vscode-textmate + Oniguruma
```

To use the extension, link it into your editor once and reload the window:

```bash
make vscode-ext   # ~/.vscode/extensions/jaden-script -> ./vscode-jsw
```

(or open `vscode-jsw/` in VS Code and press <kbd>F5</kbd> for an Extension
Development Host).

### Neovim

[`nvim/`](./nvim/) is the same language for Vim/Neovim: `syntax/jsw.vim`
(generated by `make nvim-syntax` from the same keyword tables), filetype
detection and a `//` commentstring. `make nvim-check` opens a fixture in a real
`nvim` and asserts the syntax group of 36 tokens.

```sh
cp -r nvim/syntax nvim/ftdetect nvim/ftplugin ~/.config/nvim/
```

Diagnostics come from `jswc check --format=efm`, which every `errorformat`
consumer understands — see [`docs/NVIM.md`](./docs/NVIM.md) for a ready-made
`efm-langserver` / `nvim-lint` configuration.

### Preview a project in TurboWarp

TurboWarp can fetch a project from a URL, but the response needs
`Access-Control-Allow-Origin: *` (plain `python3 -m http.server` does not send
it), and a local `http://localhost` is only reachable because browsers treat it
as a trustworthy origin:

```sh
python3 tools/serve_cors.py 8765      # serves the repo root with CORS
open 'https://turbowarp.org/?project_url=http://localhost:8765/userspace/custom1/main.sb3&autoplay'
```

`&autoplay` hits the green flag on load, so a "does it still run?" check is one
click away. Drop `autoplay` to start paused. If the browser refuses the
cross-origin fetch (Firefox/Safari are stricter than Chrome about mixed
content), build the project and open the `.sb3` directly instead.

## Language rules

`syntax.md` is the specification. The compiler implements the user-written
rules: feature switches (`#enable_feature<useTempVariables>;`,
`#enable_feature<commentMetadata>;`, `#enable_feature<namespacedNames>;`,
`#enable_feature<hideObjectNames>;`), public/private/temp variables with
first-allocation-wins initial state, undefined-variable errors
(`--allow-undefined` for legacy files), `.jsv`/`.jsa`/`.jsl` variable imports,
three sprite-metadata syntaxes, and `public init { … }`.

Two publishing helpers ship with the compiler:

* `#enable_feature<hideObjectNames>;` (or `--enable hideObjectNames`) rewrites
  every sprite, variable, list (and thread-variable) name to a deterministic
  random identifier — same blocks, same ids, no author naming in the editor.
  Broadcast names, costume/sound names and custom-block labels are kept: they
  are matched by text, sometimes built by concatenation at runtime.
* runtime-resolved names (a broadcast or clone target that is a *variable*, a
  costume/sound menu fed with an expression, an extension list picker) raise
  `warning: eval: …`, because Scratch looks those names up while the project
  runs and a typo fails silently.

## Status

`sample.jsw` (828 lines, 3 classes, 44 functions) parses end-to-end and
compiles to a 1336-block project that passes structural validation in ~37 ms.

The 167 MB reference archive decompiles to a **125 KB** source file (large
lists become `.jsa` imports) that rebuilds with **0 unsupported blocks**,
71/71 variables, 40/40 lists, 10/10 sprites, the Stage scripts and all 11
extensions preserved — with **zero compiler warnings**, 0 unsupported blocks,
and the project's own `Graph.doGraph` naming style stripped to `doGraph` in
source and restored on rebuild.

`sample.jsw` is also proven reversible: `sample.jsw → .sb3 → .jsw → .sb3`
produces a canonically identical project (tested on every run), and the
regenerated project loads in the TurboWarp editor. Both `build/demo.sb3` and
`build/sample.sb3` were loaded too and render as normal blocks — the `extensionURLs`
entry for community extensions (e.g. `utilities`) is what makes that work.
Calls into extensions that are not mapped yet (`JSON.*`, `Timer.*`,
`console.*`, `Runtime.*`) are reported as `line:col` diagnostics instead of
being silently mistranslated. See
[`docs/ROADMAP.md`](./docs/ROADMAP.md#next-small-pieces-unblocked-pick-any) for
what comes next.

## Licence

The compiler, the tools, the two machines and the documentation are MIT; see
[`LICENSE`](./LICENSE).

Two things in the tree are somebody else's and are **not** MIT:

| Path | What it is |
|---|---|
| `sample.jsw` | a decompilation of **NotePlayer 4** (GPL-3.0), kept as the compatibility fixture every block shape was verified against |
| `wem_example/` | **FrostNova's `world.execute (me)` music video** (AGPL-3.0), kept as the example `anim3d`'s shot list is generated from — it carries its own [`NOTICE.md`](./wem_example/NOTICE.md), including material that is not licensed at all (the song, its lyrics, the trademarks it shows) |

Neither is linked into `jswc`; they are aggregated in the same repository, and
each keeps its own licence. `sample_np4.26_sb3/` — the 167 MB reference archive
those fixtures come from — is not in the repository.
