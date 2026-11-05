# Decompiler — sb3 → JadenScript → sb3

`jswc decompile` turns a compiled project back into source. The contract is
**round-trip fidelity**: compiling the emitted `.jsw` must reproduce the same
scripts and the same initial state.

```
project.sb3 ──► sb3_read.py ──► block graph ──► decompile.py ──► project.jsw
                                                                     │
                                            compile (lexer/parser/codegen)
                                                                     ▼
                                                               project.sb3
```

## What is preserved exactly

Verified by `tests/test_roundtrip.py` on `build/demo.sb3`,
`build/assets_demo.sb3`, `build/rules_demo.sb3` **and on `sample.jsw` itself**
(`sample.jsw → step1.sb3 → step2.jsw → step3.sb3`, canonical forms identical).
The canonical forms of the input and output projects are **identical**:

* every script, statement order, nesting and block opcode,
* every variable and list with its initial value / items,
* sprite names, positions, size, direction, visibility,
* procedure proccodes, argument names and the `warp` flag,
* broadcast names,
* extension list.

The canonical form (`tests/roundtrip.py`) erases only what legitimately
changes: block ids, `x`/`y`, `parent`/`next` ids, generated argument ids and
asset hashes.

## The reference project

`sample_np4.26_sb3` (3871 blocks, 11 targets, 9 community extensions) gives a
realistic stress test:

| metric | value |
| --- | --- |
| source lines produced | 2686 |
| unsupported blocks | **0 / 3871** |
| stage variables preserved | 71 / 71 |
| stage lists preserved | 40 / 40, including three with 166 112 items |
| sprites | 10 / 10 |
| costumes and sounds | 346 / 346 with names, anchors and rates |
| monitors | 108 / 108 |
| workspace comments | 18 / 18 |
| recompiled project | 4141 blocks, loads in the TurboWarp editor |

One encoding-level difference explains most of the block-count delta and is
*not* a fidelity loss: the reference stores global variable reads as inline
primitives (`[3, [12, "Tick.freeze", "id"], …]`), while jswc always emits a
`data_variable` reporter block — both are valid sb3 and evaluate identically.

## A full `.sb3` with assets

```bash
jswc decompile sample_np4.26_sb3.zip -o build/decompiled/zip_sample.jsw
# wrote zip_sample.jsw (10 sprite(s), 346 asset(s))          ~3 s
jswc build build/decompiled/zip_sample.jsw --allow-undefined -o out.sb3
# wrote out.sb3 (2476 blocks, 4 extension(s))                ~8 s, 0 warnings
```

The archive is 167 MB; the generated **source is 93 KB**, because bulky state
does not belong in a source file:

* lists longer than `--max-inline` (default 50) **or** whose inline text would
  exceed 4 000 characters are written to `assets/lists/<name>.jsa` and imported
  with the language's existing `import` syntax — the reference has three lists
  with 166 112 items each;
* strings longer than 1 000 characters go to `assets/vars/<name>.jsv`;
* costumes and sounds are written to `assets/<md5>.<ext>` and imported by hash;
  `--skip-assets` keeps only the state files.

Measured on the reference archive:

| preserved | result |
| --- | --- |
| variables | 71 / 71 **including the JSON type** (`0` vs `"0"`) |
| lists | 40 / 40, names, lengths and item types |
| sprites | 10 / 10 with x, y (floats kept), size, direction, visibility |
| stage scripts | 2 / 2 blocks |
| extensions | 11 / 11 |
| assets | 346 / 346 |
| blocks without a mapping | **0 / 3871** |
| compiler warnings on rebuild | **0** |
| blocks after rebuild | 4136 (see below) |

The block count differs because of encoding choices that Scratch treats
identically:

| change | blocks |
| --- | ---: |
| inline `[12, …]` variable reads become real `data_variable` blocks | +374 |
| `join(x, "")` → `x`, and `note` dropdown shadows become literals | −147 |
| `a != b` rebuilds as `not(a == b)`, `while` as `repeat until (not …)` | +64 |

All 10 sprites, 71 variables, 40 lists, the Stage scripts, 108 monitors and 11
extensions are preserved, every top-level script keeps its canvas position, the
rebuild produces **zero compiler warnings**, and both builds load in the
TurboWarp editor with the same pre-existing extension console messages.

## Community extensions

Every TurboWarp extension the reference projects use is mapped by **fetching the
real extension source** and reading its `getInfo().blocks` metadata — opcodes,
block type (command/reporter/boolean/hat), argument names, argument types and
`menu:` declarations. Nothing is guessed.

```bash
for u in Skyhigh173/json.js Lily/MoreTimers.js -SIPC-/consoles.js \
         runtime-options.js utilities.js Lily/TempVariables2.js \
         Lily/CommentBlocks.js Lily/MoreEvents.js godslayerakp/ws.js; do
  curl -sS -o "/tmp/ext_$(echo $u | tr '/' '_')" "https://extensions.turbowarp.org/$u"
done
python3 tools/extract_extension.py --write     # refresh jswc/extension_blocks.py
```

That produces `jswc/extension_blocks.py` (132 blocks over 9 extensions) which
the compiler and the decompiler share:

| extension | namespace | example |
| --- | --- | --- |
| `skyhigh173JSON` | `JSON` | `JSON.isArray(x)` → `skyhigh173JSON_json_is` + `types="Array"` |
| `lmsTimers` | `Timer` | `Timer.getTimer("t")` → `lmsTimers_valueOfTimer` |
| `sipcconsole` | `console` | `console.warn("x")` → `sipcconsole_Warning` |
| `runtimeoptions` | `Runtime` | `Runtime.turboMode()` → `runtimeoptions_getEnabled` + `thing="turbo mode"` |
| `utilities` | `Util` | `Util.ruleReplace(s, r, n)` → `utilities_regexReplace` |
| `lmsTempVars2` | `Temp` | `Temp.getRuntimeVariable("x")` |
| `lmscomments` | `Comments` | `Comments.commentC("why")` |
| `lmsMoreEvents` | `MoreEvents` | save hooks |
| `gsaWebsocket` | `WebSocket` | `WebSocket.isConnected()` |

Two lookup layers:

* **aliases** (`jswc/extensions.py` `ALIASES`) give the friendly names people
  actually type. An alias may pin arguments to literals (`JSON.isArray` pins
  `types="Array"`) — those are re-applied on decompile, so the call round-trips;
* **generic fallback**: `JSON.json_array_get(a, i)` resolves to
  `skyhigh173JSON_json_array_get` with the declared argument names, so *every*
  block of a known extension is expressible even without an alias.

Argument types map to sb3 primitives (`STRING→10`, `NUMBER→4`, `BOOLEAN→2`);
`menu:` arguments get the real shadow dropdown block
(`<extid>_menu_<menu>`) with the value in its field.

Extension C-blocks (`lmscomments_commentC`, for-each helpers) **execute their
substack**, so the decompiler emits the call followed by the body.

## Stage scripts

A project's Stage can carry scripts (the reference has
`event_whenstageclicked`). They are emitted in a dedicated top-level block:

```jsw
stage {
    public void whenStageClicked() { // hook: stage click
        Player.toggleDisp();
    }
}
```

Also supported: `// hook: backdrop <name>` (`event_whenbackdropswitchesto`),
`// hook: before project save` / `// hook: after project save`
(`lmsMoreEvents_beforeSave` / `afterSave`), `// hook: click`
(`event_whenthisspriteclicked`).

## Inert blocks

Blocks dragged onto the workspace with no hat never run. They are preserved in
one generated procedure per sprite so their code and blocks survive a round
trip:

```jsw
// Blocks that were not attached to any script.
private void looseBlocks() {
    // procedures_call
    ...
}
```

Loose *reporters* (and dropdown shadows such as the MIDI `note` block) cannot
be statements, so they are kept as `// loose block: <opcode>` comments.

## Naming style

Two things keep the output readable without losing information.

**No `string()` noise.** `join(x, "")` is just `x` (Scratch values are text
already), so the decompiler prints the operand:

```jsw
_c = "#ff00a5";              // not string("#ff00a5")
d  = join("x", Player.name); // only real joins keep join()
```

**Redundant class prefixes are dropped, and restored.** When every custom block
of a sprite also uses the sprite's namespace (`Graph.doGraph`, `Graph.drawNumber`),
the decompiler writes the short name and records the prefix:

```jsw
class graph { // Sprite: graph (165,59) size=100 direction=90 show=1 ns=Graph
    public dpoint_x = 0;               // project: Graph.dpoint_x
    private void doGraph() { … }       // project: Graph.doGraph
    public void draw() { … }           // project: broadcast Graph.draw
}
```

* detection is **case-insensitive** (`graph` ↔ `Graph`), the original casing is
  kept in `ns=`;
* compiling a class with `ns=` under `#enable_feature<namespacedNames>` turns
  bare global names, list names and public procedure names back into
  `Graph.…` — variables, lists, broadcasts and proccodes all round-trip;
* sprite-local names are never rewritten, and a class whose custom blocks use
  short names (e.g. `player` with `reset`) is left exactly as written, so mixed
  styles never get mangled.

Custom-block labels that cannot be a function name are preserved verbatim:

```jsw
private void create_note(t, n) { // proccode create note %s in tick %s
```

### A renamed temp must be renamed *everywhere*

Body reads go through `_thread_variable()`, but the loop matcher used to read
the counter's raw project name. For a temp actually called `i` the decompiler
wrote:

```jsw
for (i=1, i<130, i++) {     // counter: a Stage variable
    c_en = en.get(_i);      // body: the temp
}
```

`i` and `_i` are different storage. The three-part `for` lowers to
`data_setvariableto` / `data_changevariableby` (a *Stage* variable), while the
body read `lmsTempVars2_getThreadVariable`. The rebuilt project then incremented
a variable nothing ever read and the body never advanced — `Lyrics.start` in
NotePlayer sat frozen on line 1.

Two rules follow:

* `_counter_set`, `_counter_step` and `_is_not_variable` all map the name
  through `thread_renames`, so the recovered counter is the same spelling the
  body uses.
* because that spelling starts with `_`, `_counted_for` declines the three-part
  form on purpose (a temp counter cannot be bound by it) and the loop stays an
  explicit `while` with `_i = 1` … `_i += 1`, which lowers back to thread
  variables. NotePlayer's original is a TurboWarp `control_while` anyway, so
  this is also the more faithful shape.

`tests/test_roundtrip_regressions.ThreadVariableLoopTests` builds a project
whose temp counter really is named `i` and asserts one name for the whole loop.

## Renames (and why they are safe)

| situation | action |
| --- | --- |
| variable/list name is not a valid JadenScript name (`v%`) | sanitised (`v_`), initial state preserved under the new name |
| thread variable without a leading `_` | renamed `_name` so `useTempVariables` allocates it; **every** reference is renamed, including a loop counter |
| parameter collides with a variable of the same name | parameter renamed `name_arg`, argument reporters follow |
| parameter or procedure named like a reserved word (`in`, `delete`) | suffixed (`in_`); proccodes are unaffected |
| sprite name is not an identifier (`synth.builtin`) | class name flattened (`synth_builtin`); the `// Sprite:` comment renames it back |
| sprite name contains a space (`obj loader`) | class name flattened (`obj_loader`); the `// Sprite: "obj loader"` comment renames it back |
| state name is a keyword (`return`) | suffixed (`return_`) |
| two variables/lists share a name in one target | the second one is suffixed (`t_2`); both keep their own value and id |
| two procedures flatten to the same name (`rotate … around x axis` / `… z axis`) | the second is suffixed (`rotate_2`) and so are its calls |
| a list is read as a value (`f`) while a variable called `f` is visible | the **list** is suffixed (`f_`); a bare name always means the variable |
| `<[my variable] of [sprite]>` property | rewritten with the renamed variable, because the block resolves it by text |

Renames are recorded as `warning:` lines so nothing changes silently.

### Procedure names must be unique

Scratch identifies a custom block by its whole label, JadenScript by its name.
`TheMast3D`'s `processor` declares three blocks that read

```
rotate %s %s %s around x axis %s d
rotate %s %s %s around y axis %s d
rotate %s %s %s around z axis %s d
```

and all three flatten to `rotate`. Only one definition survives a rebuild, so
every call — whichever axis it asked for — ran the same body. The decompiler now
suffixes the duplicates (`rotate`, `rotate_2`, `rotate_3`, in project order) and
renames the call sites to match; the `// proccode` comment keeps the original
label, so the rebuilt project still has all three blocks.

### Broadcast receivers keep their message

A `when I receive` hat is rebuilt from the `// receiver at x,y "message"`
annotation, and the message is the text the hat listens for. The method name is
a *sanitised* version of it (`update dynamic geometry` → `update_dynamic_geometry`),
so the message has to be written out whenever the two differ. It was compared
against the proccode instead, which for these hats is the message itself, so the
text was dropped — the hat then listened for `update_dynamic_geometry`, a
broadcast nothing sends.

That silently disabled the whole animation pipeline in `TheMast3D`: no door, no
lift and no train moved, because `update dynamic geometry` never ran (and
`handle bounding boxes` stopped culling, so the cull flags stayed stale). Two
receiver hats out of 88 were affected.

Verified by driving the game and opening a door: the state `stasis door open`
set to 1 lerps `stasis door position` from `0` to `1.05` in **both** builds, and
the frame that follows is identical — 19,384 `penPoint` + 19,414 `penLine`
calls, the same coordinates in the same order, and the same 831 projected
vertices.

### Canvas metadata is code now

The decompiler used to write target properties, script positions and monitors
as comments, which meant nothing worked unless `#enable_feature<commentMetadata>`
was on and the reader knew the syntax. They are declarations:

```jsw
metadata slider Stage.Player.score at 5,6 100x24 shown range 0..250 discrete;

stage: costume=0, volume=100, layer=0, tempo=60, video="on", videoTransparency=50;
stage {
    metadata Stage.startFrameCounter pos 539,64;
    public void Stage.startFrameCounter() { // hook: broadcast Stage.startFrameCounter
        …
    }
}

main: name="main", x=0, y=0, size=100, direction=90, show=1, costume=0, volume=100,
      layer=1, rotation="all", draggable=0;

class main { … }
```

| was | now |
| --- | --- |
| `// Sprite: main (0,0) size=100 …` | `main: name="main", x=0, y=0, size=100, …;` |
| `// Stage: costume=0 volume=100 …` | `stage: costume=0, volume=100, …;` |
| `public void f() { // at 539,64` | `metadata f pos 539,64;` before the method |
| `// monitor Stage.score slider at …` | `metadata slider Stage.score at …;` |
| `// watch Stage.sensing_timer …` | `metadata watch Stage.sensing_timer …;` |
| `// comment Stage at x,y WxH: "…"` | `metadata note Stage at x,y WxH: "…";` |

`#enable_feature<commentMetadata>;` is therefore no longer emitted: nothing in
the output needs it. The comments are still *parsed*, so a source written
before the change compiles unchanged — that is what makes the migration
gradual.

Two details the round trip exposed:

* monitor **ids** are arbitrary text (`)_9Lty?!ER]Z^|g5fzL^_xposition`), so they
  are quoted: `id="…"`;
* an owner with a space is quoted in both spellings — `"img list loader".score`
  — and the metadata parser accepts a quoted owner in either argument order.

Loose stacks are the one thing that keeps a comment (`loose { // at 300,400`),
because a stack of blocks has no name to refer to.

### Numbers, hex and quoted text

TurboWarp stores numeric block inputs as *text*, including hex:
`[4, "0xFFFFFFFF"]`. The decompiler writes that without quotes, so the source
reads like the block and the compiler turns it back into the number:

```jsw
_sign_return = (0x100000000 - (0 - num)) * (num < 0) + num * (num > -1);
if (__csrno == 0x340) { … }
```

| stored | written | compiled back to |
| --- | --- | --- |
| `[4, "0xFFFFFFFF"]` | `0xFFFFFFFF` | `4294967295` (decimal integer) |
| `[4, "0b1010"]` | `0b1010` | `10` |
| `[4, "0o17"]` | `0o17` | `15` |
| `[10, "1.5"]` | `1.5` | `"1.5"` (same text) |
| `[10, "007"]` | `"007"` | unchanged — leading zeros are not a number |
| `[10, "1df0"]` | `"1df0"` | unchanged — not a number at all |

Hex is rewritten as decimal on purpose (that is what the VM stores); the value
never changes, and `tools/compare_projects.py` compares numeric literals as
numbers so the textual rewrite is not reported as a difference.

**Variable and list values keep their quotes when the stored JSON type is a
string** (`_literal_text`). A variable whose value is the text `"0x6"` must not
become `0x6`: Scratch does not parse hex, so `"0x6" = 6` is false and the value
would change meaning. Only block *inputs* are unquoted.

### A bare name is a variable, not a list

Scratch keeps variables and lists in separate namespaces, so `TheMast3D` has a
global variable `f` (the focal length) *and* a sprite-local list also called `f`
(a scratch buffer). JadenScript spells both as a bare name: `f` is the variable,
`f.get(1)` is the list's first item, and `f` on its own would be the list's
contents. The compiler resolves that in favour of the **variable**, because a
value position almost always means the variable, and the decompiler renames a
list that is actually *read* as a value (`data_listcontents` or a list
primitive) when its name is taken.

Getting this backwards is silent and catastrophic: `vert x = f * vert x / vert z`
read the empty list, so every projected x and y became `0` while z stayed
correct — the whole scene collapsed onto `(0, 0)` and the points mode drew rays
from the centre. `tests/test_roundtrip_regressions.py::VariableBeatsListTests`
pins the rule down.

## Syntax the decompiler relies on

These constructs were added to the language so that every emitted statement has
an exact inverse:

| construct | compiles to |
| --- | --- |
| `repeat (n) { … }` | `control_repeat` |
| `public x = [1, 2, 3];` | a list with initial items |
| `join(a, b)` | `operator_join` |
| `Control.broadcast("name")` / `Control.broadcastAndWait("name")` | broadcast by exact name (used when the name is not a valid function identifier) |
| `Control.deleteThisClone()` | `control_delete_this_clone` |
| `Control.stopThisScript()` / `Control.stopOtherScripts()` | `control_stop` with option `this script` / `other scripts in sprite` |
| `Sensing.distanceTo(menu)` | `sensing_distanceto` |
| `Sensing.touchingColor("#ff0000")` | `sensing_touchingcolor` |
| `Sensing.of("x position", "Stage")` | `sensing_of` (property stays a field) |
| `Looks.setEffect("ghost", 50)` | `looks_seteffectto` |
| `Looks.clearGraphicEffects()` | `looks_cleargraphiceffects` |
| `Looks.goToFront()` / `Looks.goToBack()` | `looks_gotofrontback` |
| `Looks.switchBackdrop(name)` / `Looks.switchBackdropAndWait(name)` | `looks_switchbackdropto` / `…andwait` |
| `import "lists/x.jsa" as x;` inside a class | a sprite-local list with those items (`.jsv` → variable) |
| `// receiver at 10,20 "message"` | the hat is recreated there, listening for that exact text |
| `// hook: broadcast <name>` | `event_whenbroadcastreceived` with that exact name |
| `// hook: click` | `event_whenthisspriteclicked` |
| `// warp` | `warp: "true"` on the custom block |
| `log(x)` / `warn(x)` / `error(x)` / `startTimer(x)` / `stopTimer(x)` | `sipcconsole_Journal` / `Warning` / `Error` / `Timeron` / `Timeroff` |
| `breakpoint;` | `sipcconsole_debug("@breakpoint")` |
| `menu("note", "NOTE", 66)` | a bare dropdown (shadow) block, also as a root block in `loose { }` |
| `while (c)` / `while (!c)` | `control_while (c)` / `control_repeat_until (c)` |
| `Temp.setRuntimeVariable("x", v)` | a Stage variable `x`, initial `0`, plus a green-flag reset script |
| `Variables.set(x, v)` / `Lists.add(x, v)` | the ordinary `data_setvariableto` / `data_addtolist` blocks |
| `block("opcode", "FIELD", value)` | that block, kept verbatim (no shadow flag) |
| `#enable_feature<doObfuscation>;` | the whole project with random names (see `syntax.md`) |
| `0x10` / `0b101` / `0o17` | the decimal integer Scratch stores |

Reserved words are also accepted as plain identifiers (`static = …`), because
real projects contain variables with those names.

## Choosing between `call X()` and `Control.broadcast("X")`

The decompiler emits `call X()` when a broadcast has a receiver script that
invokes a procedure (the pattern jswc itself generates), and
`Control.broadcast("X")` otherwise. That keeps generated source readable while
remaining correct for arbitrary broadcast names, including invisible ones.

## Adding a new bidirectional mapping

1. Add the command/reporter to `jswc/opcodes.py` (forward direction).
2. The decompiler picks it up automatically from `_COMMAND_BY_OPCODE` /
   `_REPORTER_BY_OPCODE`; structured blocks (`control_*`, `data_*`) need a case
   in `Decompiler._statement` / `_expr`.
3. Add a round-trip test: build a project that uses the block, decompile it,
   compile and compare canonical forms.

## Known gaps

Measured against the reference project (4111 blocks rebuilt from 3871), with
`tools/`-style opcode histograms. Nothing below changes what the project does
or how it looks when it runs; each is a serialisation detail:

* **compressed variable reads** — the project stores `[12, name, id]` inline;
  a rebuild makes them `data_variable` blocks (384 of them). The editor
  compresses them again the next time it saves, so this shows up as a block
  count difference, not as a difference on screen.
* **`join(x, "")`** — the `string()`-noise rule folds these away (83 in the
  reference project). Values are unchanged; only the empty-concat blocks go.
* **constant joins** — `join("a", "b")` folds to `"ab"` for the same reason.
* **fallback shadows of dynamic slots** — `sound_play <variable>` keeps a sound
  dropdown (and `looks_switchcostumeto`, `control_create_clone_of`, a note
  menu) *under* the reporter. The rebuild keeps the reporter and an empty text
  slot; the fallback only matters if the reporter is empty.
* **`if/else` with an empty else** becomes `if` (one block).
* **undeclared argument reporters** — the project has leftover
  `<is TurboWarp?>` / `<is compiled?>` blocks in scripts that do not declare
  those parameters. Scratch evaluates them as empty, so they decompile to `""`.

### Learned from a 13k-block community project

`TheMast3D.sb3` (20 sprites, 13,949 blocks, 144 assets, TurboWarp-compiled)
round-trips cleanly. It found these rules, all now enforced:

* **`[1, null]` inputs** — TurboWarp's compiler writes an empty value that way;
  it decompiles to `""` instead of crashing.
* **quoted sprite names** — `// Sprite: "obj loader"` keeps the spaces, so the
  rebuilt target has the original name (monitors and `Sensing.of` depend on it).
* **keyword and duplicate state names** — `return` → `return_`, the second `t`
  → `t_2`; both copies keep their own value, and every by-id reference follows.
* **class-level state files** — `import "lists/WORLD_obj.jsa" as WORLD_obj;`
  inside a class declares sprite-local state that the body can read.
* **a list read as a value** — `Operators.length(myList)` compiles to
  `operator_length(data_listcontents)`, never a (new, empty) variable.
* **state stays where it lives** — `Variables.showVariable(x)` and `x += 1` used
  to also declare `x` on the Stage; sprite-local state never forks.
* **receiver hats** — `// receiver at x,y "message"` brings back
  `when I receive "message"` at the original position, with the original text
  even when the procedure label differs (`project verts` vs `project vertices`).
* **extension menus** — `pen_setPenColorParamTo` reads `COLOR_PARAM` (a shadow
  block), not `PARAM`; the VM otherwise warns about an unknown colour parameter.
* **loose blocks that fold to a literal** — a `join("", "")` left on the canvas
  becomes `""`, which compiles to nothing, so such a block is written as
  `block("operator_join");` and kept.

### Proving a round trip

`tools/compare_projects.py original.sb3 rebuilt.sb3` compares what actually
matters: every variable value, every list (length + content hash), costumes and
sounds by md5, monitors, sprite properties, script counts and each block's
opcode plus literal field/input values. Structural rewrites TurboWarp makes
itself (inlined variable primitives, folded `join(x, "")`, `if` with an empty
else, a console extension procedure) are reported as notes, not problems.

Measured: `TheMast3D.sb3` → jsw → sb3 has no *behavioural* difference — every
reachable script, every variable value after the same frame, and the full pen
call stream match (see [Pen-level comparison](#pen-level-comparison-toolsprobe_penjs)).
What the tool still reports are **notes and naming problems**: state names that
JadenScript cannot spell (`render mode` → `render_mode`), primitives re-emitted
as blocks, orphaned blocks rewritten as `loose` scripts, folded constants and the
author's zero-width-space `log`/`warn`/`error` calls, which become console
blocks. Run it on the project you are about to ship and read the `!` lines; they
are the list of things a human could still notice.

Resolved while writing this section: `note` shadows under `operator_round`
(24 blocks), the console add-on's 17 zero-width calls, one dropdown dragged
loose, `control_while` loops (8), the double-negated `repeat until` conditions
(56 `operator_not` blocks).

## Canvas metadata

Scratch keeps a lot of information *next to* the blocks. Losing it does not
change a single opcode, yet the project looks - and sometimes behaves -
different, so every piece is written as a comment and restored on build:

| metadata | directive |
| --- | --- |
| script position | `// at x,y` after the opening brace of every top-level script |
| unattached blocks | `loose { // at x,y … }`, one chain per block stack |
| monitors (slider range, list size, show mode) | `// monitor <target>.<name> <mode> at x,y WxH shown\|hidden [range a..b [discrete]]` |
| reporter monitors | `// watch <target>.<opcode> id=<id> at x,y WxH` |
| workspace comments | `// comment <target> at x,y WxH: "text"` (JSON-escaped, newlines included) |
| backdrop / costume index | `costume=` in `// Sprite:` / `// Stage:` |
| volume, tempo, layer order, video state | `volume=` `tempo=` `layer=` `video=` `videoTransparency=` |
| rotation style, draggable | `rotation=all\|leftright\|none`, `draggable=0\|1` |
| costume anchor and bitmap resolution | `// costume centerx= centery= res=` |
| sound rate and length | `// sound rate= samples=` |

Targets also keep their extension order and the exact asset hashes, so the
rebuilt `.sb3` renders the same picture before any script runs.

> **Planned, not implemented:** a friendlier, parameterised way to declare the
> same metadata on the declaration itself (`public Player.notes = [] { monitor:
> list at 378,0 size 100x74 hidden };`) is tracked as **14.4 — INCOMPLETE** in
> [`ROADMAP.md`](./ROADMAP.md). Until then these comment directives are the only
> form, and the decompiler keeps emitting every one of them.

Monitor details that are easy to get wrong (all of them cost a rebuild round):

* a variable/list monitor's `id` **is the variable/list id** — a fresh id makes
  the editor add a second monitor at its default position the first time a
  script runs `show variable`;
* Scratch writes `sliderMin` / `sliderMax` / `isDiscrete` on **every variable
  monitor** (not just sliders) but never on list monitors — the builder now
  does the same, so all 108 monitors in the reference project compare equal;
* the three slider fields are only printed in the source when they differ from
  the Scratch defaults (`0..100`, discrete), which keeps the comment block
  readable;
* reporter monitors (`sensing_timer`, `sensing_online`, `sound_volume`) have no
  state behind them, so they carry their `id` explicitly.

## Ids that leak into text

Some extension menus store a variable or list *id* as text (the JSON
extension's list picker, for example). A rebuild allocates fresh ids, so a raw
id would silently point at nothing - the reference project's whole note table
went empty this way. The decompiler looks every menu value up and writes the
**name** instead; the extensions resolve ids first and fall back to names.

## A second project: Linux 6.1.14 on RISC-V

The round trip was re-run on someone else's project —
<https://scratch.mit.edu/projects/1201938491> ("Linux 6.1.14-rv32ima On
Scratch", 3,349 blocks, 5 targets, 21 lists with 71.8 M items, pen only):

| | original | rebuilt |
| --- | --- | --- |
| blocks | 3,349 | 4,172 (828 are variable reads the editor compresses again) |
| scripts | 169 | 169, same positions, same hats |
| monitors | 83 | 83, same sprite/mode/position/size/visibility |
| list items | 71,846,347 | 71,846,347, same per-sprite scoping |
| extensions | `pen` | `pen` + `sipcconsole` (the project has 11 hidden console calls) |

Then it was loaded in TurboWarp and green-flagged: the kernel boots and prints
its log (`Linux version 6.1.14`, `SLUB: HWalign=64`, `printk: console [tty0]
enabled`, …), `Global.frameIndex` advances at the same rate as the original.

What that project exposed, all fixed:

* **`_x` locals were silently becoming thread variables.** The project names
  its sprite variables `_unicode`, `_imm`, `@divide.return`. Sanitising those to
  `_x` made the compiler pull in `lmsTempVars2` and emit 881 thread-variable
  blocks that were never there. Thread variables are now opt-in
  (`#enable_feature<useTempVariables>`), because Scratch clones already *copy*
  sprite-local variables (measured: clone writes 42, original still reads 1).
* **dotted sprite-local state.** `buffer.glyphs` is a sprite list whose name has
  a dot; a dot means "Stage variable" in this language, so every read created an
  empty Stage list. Sprite-local names are flattened (`buffer_glyphs`).
* **big state imported inside a class** was emitted at the top level, which
  again turned sprite lists into Stage lists (and left 3 monitors unresolved).
* **`"menu" in opcode`** matched `looks_costu**me**` + `**nu**mbername`; the
  costume-name reporters were flattened to the text `"name"`. Menu opcodes are
  now matched by shape (`_menu_`, `_menu`, `_options`).
* **two sprites can declare the same qualified procedure** (`Global.prepareSave`
  in both RISCV and Terminal); the map kept only the last one, so a local call
  turned into a cross-sprite broadcast-and-wait. Calls resolve inside their own
  class first.
* **a broadcast whose receiver calls an undefined procedure** must not rename
  that procedure: the call is inert in the project, and naming it after the
  broadcast made the rebuild actually run it.
* `0.Run Frame`-style labels were read as `0` (an object) `.Run_Frame`; labels
  that are not clean dotted names are flattened.

## Runtime checks

`sb3 -> jsw -> sb3` is also validated by running both projects in the
TurboWarp editor and sampling the same variables:

| after 5 s | original | rebuild |
| --- | --- | --- |
| `Tick.tick` | 241 | 270 |
| `Player.notes` | 297 | 337 |
| `Player.instances` (polyphony) | 9 | 8 |
| `Player.skips` | 0 | 0 |
| `Music.tempo()` | 275.38 | 275.38 |

The rebuild is stable for minutes (no clone leak, no drift in `Tick.wait`),
which is what the earlier `lmsTempVars2_changeThreadVariable` bug destroyed: the
decompiler read the amount from the wrong input (`STRING` instead of `NUM`), so
every synth clone ramped its volume *up* forever instead of fading out.

### Third project: TheMast3D (3D engine, 13,957 blocks)

The same loop, on a project TurboWarp itself compiled (20 sprites, 144 assets,
187 monitors, 8 multi-megabyte state files):

| check | original | rebuild |
| --- | --- | --- |
| targets / target order | 21 | 21, same order |
| scripts (top-level) | 328 | 328 |
| blocks (all / reachable) | 11,358 / 11,203 | 13,957 (see below) |
| Stage variables / lists | 60 / 28 | 60 / 28 |
| assets | 144 | 144, byte-identical |
| monitors | 187 | 187 (names follow the renames) |
| `_3D__This_Frame` after 2.5 s | advances | advances |
| VM warnings after green flag | stale-monitor notices only | identical |

The rebuild has ~2,600 *more* block objects without changing behaviour, because
the decompiler turns inline primitive inputs (`[12, "f", id]`, `[13, …]`) and
procedure-prototype argument shadows into real blocks. Going the other way, 155
blocks that no script can reach in the original (orphaned chains that Scratch
keeps in the workspace) are re-emitted as `loose { … }` scripts, and constant
folds (`join("shift", "")` → `"shift"`), empty `else` branches and
`sound`/`costume` menu shadows are rewritten — all value-preserving. The
remaining opcode differences are listed by `tools/compare_projects.py`.

### Verifying a render (the slow way, when a project "looks wrong")

A faithful round trip can still *look* wrong if the two runs are not compared
at the same point, so the check used for `TheMast3D` was:

1. free-run both builds in the same player (same viewport), hit the green flag,
   wait the same number of seconds;
2. screenshot **the stage canvas only** and pixel-diff them
   (`PIL.ImageChops.difference`);
3. additionally run both with a *frozen* clock and manual `runtime._step()` to
   be frame-exact;
4. compare the full geometry lists and the pen draw-call stream
   (`renderer.penLine/penPoint/penStamp`, coordinates to 3 decimals) — see
   [Pen-level comparison](#pen-level-comparison-toolsprobe_penjs).

One trap worth remembering: **freezing the clock stops this project's clock
logic.** `main` computes `dt = 86400 * (daysSince2000() - last_time)` and shows
`FPS = round(1 / dt)`. With a frozen clock `dt` is 0, `FPS` prints `Infinity`
and `dt` is *not* clamped (`0 > 20 * target_dt` is false), so anything that
integrates over time simply stops while the rest of the frame still runs. That
is fine for frame-exact comparisons — both builds freeze identically — but the
in-game `FPS` counter reading `Infinity` or `0` means you are looking at a
frozen clock, not at a broken translation. Don't screenshot that state.


### Pen-level comparison (`tools/probe_pen.js`)

Variables alone do not prove that a project *draws* the same thing. The probe
freezes the clock, steps the VM by hand, records every `penClear / penPoint /
penLine / penStamp` call of a single frame and returns them as strings:

```js
__q5(320, 75)   // camera rotation (z, x); returns { total, calls: [...] }
```

Results for `TheMast3D`, three camera rotations (original vs rebuild):

| rotation (z, x) | pen calls | calls at (0,0) |
| --- | --- | --- |
| 287, 70 | 462 / 462 | 0 / 0 |
| 320, 75 | 489 / 489 | 0 / 0 |
| 30, 80 | 220 / 220 | 0 / 0 |

The call lists are **identical item by item** (same opcodes, same colour+size
attributes, same coordinates, same order). The geometry lists behind them are
identical too (`verts`, `verts cam space`, `verts projected`, `tris`,
`bounding boxes`, `verts extra data`, all 657/4704 entries).

#### The comparison is only as good as the baseline

An earlier run of this page reported "identical, and the points at `(0,0)` are
the project's own behaviour" — 110 to 157 points sitting on the origin in
*both* builds. That conclusion was wrong, because the file used as the
"original" had itself been produced by the compiler. Two copies of the same
bug agree with each other perfectly.

What finally caught it was a **per-frame digest of every variable**, not of the
pen calls: frame 0 already differed, and `verts projected` read
`0,0,z,0,0,z,…` in the rebuild against `x,y,z,x,y,z,…` in the original. Tracing
writes to `vert x` showed the second assignment (`vert x = f * vert x / vert z`)
producing `0` while every input (`f`, `vert x`, `vert z`) matched. The cause was
`f` compiling to the *list* called `f`, not the global variable — see
[A bare name is a variable, not a list](#a-bare-name-is-a-variable-not-a-list).

Lessons for the next investigation:

* diff **state**, not just pixels or draw calls; the first diverging variable
  names the faulty expression;
* confirm the baseline is the untouched project (hash it, or decompile it in
  the same session) before believing "both builds agree";
* a value that is correct in one component and zero in another (`z` right,
  `x`/`y` zero) points at one factor of the expression, not at the loop.

