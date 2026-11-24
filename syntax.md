Syntax specs for JadenScript
(Still writing.)

# User defined rules (DO NOT EDIT THIS PART)

## Additional features switch
In JadenScript, we can enable some additional features by using the following code:

```jsw
#enable_feature<feature_name>;
```

The names are `useTempVariables`, `commentMetadata`, `namespacedNames`,
`doObfuscation`, `corruptBlockPos`, `vanillaScratch`, `vmProtect` and
`pishiSupport`.

`vanillaScratch` is the one that changes what blocks are emitted rather than how
names are spelled: it compiles to blocks scratch.mit.edu can run, because Scratch
rejects a whole project for containing a single block its VM does not have. What
it lowers, and to what:

| written | vanilla Scratch |
| --- | --- |
| `while (c) { … }` | `repeat until <not (c)>` |
| `for i in 1..n { … }` | `repeat (n)` with the counter declared |
| `Comments.commentC("note") { … }` | the body, without the label |
| `Comments.commentCommand("note");` | nothing |
| `Comments.commentReporter("note", x)` | `x` |
| `console.log/info/warn/error(x)` | `add (x) to (Console)`, a Stage list |
| `console.clear()` | `delete all of (Console)` |
| `Timer.startTimer(n)` | add-or-replace `n` in `TimerNames` with the `timer` in `TimerStarts` |
| `Timer.getTimer(n)` | `(timer) - (item (item # of (n) in (TimerNames)) of (TimerStarts))` |
| `a ** b` | `10 ^ (b * log a)` |
| `Util.stringToBoolean(x)` | `x = "true" or x = "1"` |
| `Runtime.stageWidth()` and friends | 480, 360, 30 fps, 300 clones |
| `WebSocket.sendMessage(x)`, `WebSocket.isConnected()` | nothing, and `0` |
| `Temp.forEachThreadVariable("_i", n) { … }` | `set 0; repeat n { change by 1; … }` |
| a `// hook: before project save` function | nothing — Scratch has no save event |
| a `loose { … }` stack | nothing — it is on the canvas, not in the program |
| `a if c else b` in an argument | `set _t = b; if c { set _t = a }` before the statement, then `_t` |

Blocks with no equivalent are **not** silently kept: the build still contains
them, and `tools/scratch_audit.py` reports every one, so the gap is a number
rather than a surprise at upload time.

## Green flag
In TurboWarp, a green flag event means the Scratch VM has been resetted, and all sprites immediately stops, then VM start calling all 'When green flag clicked' blocks in sprites. 

We usually define those when green flag clicked events with the situation below:

```jsw
class test {
    public void greenFlag() {
        // do something
    }
}
```

## Lists
In TurboWarp, it provided those APIs for you to manage lists:
Append things to list, delete n of list, delete all of list, insert things at n of list, replace item n of list with n, (item n of list), (item # of n in list), (length of list), (list contains n), show list, hide list.

In JadenScript, we may use the code above to do the same thing:
```jsw
class something {
    private void start(){
        
    }
}
```


class something {
    private void start(){
        
    }
}
```

## Public, private and temp object definition

### Functions

There are exactly two kinds, and the keyword picks which Scratch block you get:

| Declaration | Scratch | Reachable from | Arguments |
| --- | --- | --- | --- |
| `public void f()` | a `when I receive "f"` hat + a My Block | its own class **and any other class** | none — `E0086` |
| `void f(a, b)` (no `public`) | a My Block only | its own class only | as many as it likes |

```jsw
class helloworld {
    public void broadcast_1() { // Broadcast + My Block "broadcast_1"
        // do something
    }

    void custom_block_1(x) { // My Block "custom_block_1 %s", private to this class
        // do something with x
    }
}
```

`public` is a broadcast, so it follows the broadcast rules: no arguments
(`E0086`) and no return value. A `return` inside any function is still
unsupported and reports a warning. `void f(a, b)` inherits the parameters as
My Block arguments, which is what a helper normally wants.

#### Calling a function that lives in another class

A Scratch custom block belongs to one sprite, so `object.mu()` from another
class cannot be a custom-block call: it is a broadcast, and the target sprite
needs a hat that listens for it. Write the call the obvious way and both sides
are generated — `object` gets `when I receive "object.mu"` in front of its
`mu` block, and the caller gets `broadcast "object.mu" and wait`:

```jsw
class model { // the Stage
    public void greenFlag() {
        object.mu();   // broadcast "object.mu" and wait
        call object.ml(); // broadcast "object.ml", no waiting
    }
}

class object {
    public void mu() { // receives "object.mu", then moves
        Motion.y += 10;
    }
}
```

Rules that fall out of the broadcast:

* It has to be **public** — a private function is `E0041`, because no hat
  exists for it.
* It has to be **parameterless** — a broadcast carries no arguments, so
  `object.mu(2)` is `E0085`. Send values through a global variable or a list
  the other sprite reads.
* Which class is declared first does not matter; the lookup is by
  `(sprite, function)`, not by position in the file.
* A dotted call from *inside* the same class (`object.mu()` while writing
  `class object`) is still a plain custom-block call — there is no sprite
  boundary to cross.
* `#enable_feature<namespacedNames>` is irrelevant here. It renames global
  variables and broadcasts, but a function's qualified name is only dotted
  when it is *declared* dotted (`public void object.mu()`), which stays
  supported for decompiled projects.

### Variables
In JadenScript, variables can also be defined as public,
private and temp (which functions can't define as temp).

To enable temp variable allocation, put this code into your code header:

```jsw
#enable_feature<useTempVariables>;
```

With useTempVariables enabled, all private variables names starts with '_' inside.
function in a class will be allocated as Temp variables,
they could be reused globally after they are freed (function
return).

```jsw
class something {
    private void custom_block_1() {
        private _temp = 0; // Temp variable, will be freed after function return
        private temp = 0; // This is a private variable which kept in this class
    }
}
```

## Cloud variables

A cloud variable is a stage variable the player shares between everybody
running the project. In JadenScript it has its own declaration keyword:

```jsw
class lobby {
    cloud score = 0;     // a stage variable, [name, value, true]

    public void greenFlag() {   // hook: green flag
        score += 1;
    }
}
```

`cloud` replaces the visibility word, because a cloud variable is always
public: `scratch-vm` loads it as `[name, value, true]` and only for the Stage
(`sb3.js` checks `object.isStage`), so the compiler puts it there whatever class
it is written in. Reading it from another class works the same way a global
does:

```jsw
class hud {
    public void init() {   // hook: green flag
        Looks.say(score);
    }
}
```

With `#enable_feature<commentMetadata>;` the trailing-comment spelling works
too, as long as the comment is exactly the word:

```jsw
#enable_feature<commentMetadata>;
class lobby {
    public score = 0; // cloud
}
```

Three things the compiler refuses or warns about, because the player is silent
about them:

| code | when |
| --- | --- |
| `E0081` | `cloud items = [1, 2];` — Scratch has no cloud lists; the flag is only read for variables |
| `E0082` | `cloud x = 1;` inside a function — a declaration belongs to the class, so move it out and assign here |
| `W0032` | more than ten cloud variables — `cloudOptions.limit` is 10, and the extras load as ordinary stage variables |

Cloud variables also cost the project its offline behaviour: with no cloud
server configured the value is read-only in the editor and nothing syncs, which
is what a `[name, value, true]` variable does in the Scratch player and in
TurboWarp alike.

## Initial state definition

### Variables
In TurboWarp, the environment is already set up by creators,
like variables, lists, default costume. But instead in
JadenScript, all public, private variables **will raise
Exception in compile time** if not defined.

We usually define initial variables outside everything:
```jsw
public abc = 'Hello!';
class helloworld {
    public init {
        Looks.say(abc); // abc <- 'Hello!' (global), valid.
    }
}

Compiler detect the code initial state by locating the first
variable allocation in the code (scanning from top to bottom)
```jsw
class helloworld {
    public c = 1145; // Define initial variable state for this sprite outside calls
    private d = 1919; // Define initial private variable state for this sprite (class) only
    public void broadcast_1(){
        public a = 15; // Define global variable a = 0
        private b = 23; // Define local variable for this sprite (Class) called helloworld.
        public c = 69; // Set c=69, its initial state is 1145.
    }
    private void broadcast_2(){
        private _temp = 0; //
    }
}
```
In this situation, the initial state of variables of this class will be a=15, b=23, c=1145, d=1919.

Also, you can define variable initial state by importing variables from a file named `*.jsv` (VARIABLE), `*.jsa` (LIST, stored as JSON Array), `*.jsl` (LIST, stored values per line like TurboWarp export tools does):
```jsw
import 'a.jsv' as a;
```
If `as` is not provided, then the filename will become the variable name by default (without extension name).

### Sprite (Classes) position and other metadatas
In TurboWarp, each sprite has its own position, size, direction and visibility. In JadenScript, we can define these metadatas by using the following codes:

Method 1:
```jsw
class helloworld(x=0, y=0, size=100, direction=90, show=1) {
    // This sprite will be created at (0,0), size 100%, direction 90 degree and visible
}
```

Method 2:
```jsw
helloworld: x=0, y=0, size=100, direction=90, show=1; // This overwrites the metadata of this class to tell compiler to create this sprite at that point. If more than one definitions used this way, the last one will be used, and compiler will raise warning for both conflicts.
```

Method 3 (Requires feature commentMetadata enabled)
```jsw
class code { // Sprite: spritename (0,0) size=100 direction=90 show=1
    ...
}
```
By using this method you can tell compiler to create this sprite not only with those metadatas, but also rename the sprite to the provided `spritename`. Note that if comment does not match the pattern of 'Sprite: %s (%d,%d) ...(more metadata assignment)',
compiler will raise a warning if your class starts with 'Sprites:' but with invalid arguments after the 'Sprites:'.
This method is allowed only if feature commentMetadata is enabled, or comments in class definition will be ignored.

### Exceptions during variable import
If file not found, compiler will raise exception and refuse to compile.
If file is invalid (Invalid JSON), compiler will raise exception and refuse to compile.

## Handle attachments
In TurboWarp, we can import attachments (photos, audios, etc.) within the UI provided to user,
Therefore in JadenScript, we import attachments by using the following code:

```jsw
import '' (as attachment_name);

```

When object name will become the file name of the attachment
if no name is provided with `as`.






---

# Assistant written rules

## The user rules, restated (my reading)

The section above is the contract; this is the same contract in my own words,
so this half of the document stands on its own. Where the two disagree, the
user section wins.

**Feature switches.** A file may start with `#enable_feature<name>;` lines.
They are opt-in gates: a compiler that does not know a feature must refuse to
apply it rather than silently do something else. Today the switch turns on
`useTempVariables`, `commentMetadata` and `namespacedNames`; unknown names are
reported as warnings so a typo cannot pass unnoticed.

**Green flag.** `public void greenFlag()` is the "when green flag clicked" hat.
`public init { … }` inside a class means the same thing, and is what a sprite
uses for startup work. Both produce exactly one flag script per class.

**Lists.** Every Scratch list operation is a method on the list name:
`add`, `delete`, `clear`, `insert`, `set`, `get`, `indexOf`, `length`,
`contains`, `show`, `hide`. `get`/`indexOf` accept `"last"`, `"random"` and
`"any"` like Scratch does.

**Private, public and temp.**
A `private void` function is a custom block; a `public void` function is a
broadcast (and, without parameters, also a callable custom block). A
parameterless public function whose body is only a call to a custom block is
decompiled back into that custom block instead of a wrapper.
Variables follow the same idea: `public` lives on the Stage, `private` lives
on the sprite, and a name starting with `_` is a thread variable (the
`useTempVariables` feature), which is freed when the script that owns it
finishes. Variables cannot be declared `temp` inside a function signature,
only through the `_` prefix.

**Initial state.** Every variable and list is defined by its **first**
allocation in document order — later assignments with `=` change the value,
they do not re-define it. Reading a name that was never allocated is a compile
error unless `--allow-undefined` is passed (used only for legacy sources).
State can also be imported from a side file: `.jsv` (one variable),
`.jsa` (a JSON array for a list) and `.jsl` (one list item per line). A missing
file or invalid JSON stops the build.

**Sprite metadata, three ways.** (1) `class X(x=0, y=0, size=100,
direction=90, show=1)`, (2) a standalone `X: x=0, …;` line, (3) the
`// Sprite: name (x,y) …` comment, which needs `commentMetadata` and is the
only form that can also rename the sprite. Precedence is
**defaults → `// Sprite:` → class parameters → standalone lines**, so the most
explicit form wins; a second standalone line for the same class is a conflict
and warns.

**Attachments.** `import "path" as name;` embeds a file into the built `.sb3`.
Images become costumes and audio becomes sounds; the same statement is reused
for state side files, so one syntax covers every asset a project needs.

## My additions

The rest of this file documents what I added on top of the user rules. They
exist because the decompiler has to be able to express everything a real
project contains, and because the round trip must not lose anything.

| version | addition | why |
| --- | --- | --- |
| v0.1 | lexer, parser, AST, opcode registry, IR, sb3 writer, CLI, VS Code grammar | the compiler itself |
| v0.3 | `repeat (n)`, list literals, `join`, `Control.broadcast(…)`, `Control.deleteThisClone()`, `// hook:` hats, `// warp`, `// proccode` | every block the reference project uses must be spellable |
| v0.4 | community-extension registry (`JSON`, `Timer`, `console`, `Runtime`, `Util`, `Temp`, `Comments`, `MoreEvents`, `WebSocket`), `stage { }`, `#enable_feature<namespacedNames>`, huge state in side files, asset imports | 11 extensions and 3871 blocks in the reference project |
| v0.5 | `_name` thread variables always on, `private _x;`, `isTurboWarp()`, `isCompiled()`, f-strings that compile to `join` chains | readable source instead of `lmsTempVars2_*` noise |
| v0.6 | `// at x,y`, `loose { }`, `// monitor` / `// watch`, `// comment`, costume/sound metadata, `// undefined`, `call(…) { … }`, thread-variable spelling, monitors keyed by variable id | the canvas, the monitors and the pen-drawn UI must survive a rebuild |
| v0.7 | `log(x)` / `warn(x)` / `error(x)` / `startTimer(x)` / `stopTimer(x)` / `breakpoint;` | the console add-on calls were decompiled as `__log__` stubs; the project's logging has to reach the editor's Logs tab |
| v1.0 | `#enable_feature<doObfuscation>`, eval warnings | a published build shows sprite/variable/list names in the editor, and runtime-resolved names fail silently |
| v1.1 | `cloud a = 0;` (and `public a = 0; // cloud`), `gtm.*`, `E0081`/`E0082`/`W0032` | a cloud variable is `[name, value, true]` on the Stage and nothing else in the language could produce one; the VM drops the flag silently past ten of them |
| v0.9 | number literals (`0x`/`0b`/`0o`, bare numbers), `block(…)`, sprite-local `_x` by default | two real projects exposed them: hex kernel constants, dropdowns that are values, and `_x` locals that must not silently become thread variables |
| v0.8 | `menu("opcode", "FIELD", value)`, `while (c)` -> `control_while`, `for` de-lowering | dropdown blocks that are not an input slot (`round(<note 66>)`, a menu dragged loose) and loops must come back with the same block, not an equivalent one |

Details of the v0.6 work, because each line fixed a real regression:

* **`// at x,y`** — block positions were thrown away, so every script piled up
  in one column. The comment sits right after the opening brace so trailing
  `hook:` comments keep working.
* **`loose { }`** — blocks dragged onto the canvas with no hat are real blocks;
  bundling them into a fake `looseBlocks()` procedure deleted their chains.
* **`// monitor` / `// watch`** — all 108 monitors (slider range, list size,
  show mode, position) round-trip byte for byte, including the
  `sliderMin`/`sliderMax`/`isDiscrete` fields Scratch writes on every variable
  monitor and its refusal to write them on list monitors. A monitor is keyed by
  the **variable id**, not by a fresh id, otherwise the editor creates a new
  monitor at a default position the first time a script shows the variable.
* **`// comment`** — workspace comments (the pseudocode notes in the reference
  project) are part of what a user sees, so they are preserved verbatim.
* **costume/sound metadata** — `centerx`, `centery`, `res`, `rate`, `samples`.
  Recomputing the anchor from the image size shifted every sprite and was the
  real cause of the "positions are wrong" report.
* **`// undefined`** — the reference calls custom blocks that the project never
  defines. The marker emits the dangling calls without inventing empty My
  Blocks.
* **`call(…) { … }`** — extension C-blocks (`Comments.commentC`,
  `Temp.forEachThreadVariable`) own their body. Hoisting it out made the graph
  and the note tables unreadable, and the loop body ran once instead of N
  times.
* **thread variable spelling** — `Temp.forEachThreadVariable("g", …)` and the
  body's `_g` must be the same variable; the decompiler renamed one side only,
  which silently broke every `forEach` loop (the graph drew nothing at all).
* **monitors keyed by variable id** — see above; without it "show variable"
  produced monitors at the wrong place and size.

## Implementation status of the user rules (v0.2)

| User rule | Status | Where |
| --- | --- | --- |
| `#enable_feature<name>;` switches | ✅ implemented, unknown names warn | `jswc/parser.py` → `Program.features` |
| `#enable_feature<useTempVariables>;` | ✅ `private _x` inside a function becomes a thread variable | `jswc/codegen.py` `_store_temp` |
| `#enable_feature<commentMetadata>;` | ✅ `// Sprite:` is ignored (with a warning) unless enabled | `jswc/codegen.py` `_sprite_metadata` |
| `void greenFlag()` | ✅ `event_whenflagclicked` | `jswc/codegen.py` |
| `public init { … }` | ✅ green-flag script | `jswc/parser.py` `parse_init_block` |
| Lists API | ✅ add/append/clear/get/set/insert/delete/length/contains/show/hide | `jswc/opcodes.py` |
| public function = broadcast, private = custom block | ✅ | `jswc/codegen.py` |
| public variable = Stage global, private = sprite local | ✅ | `jswc/vars.py` |
| first allocation defines the initial state | ✅ | `jswc/vars.py` `VarPlanner` |
| undefined variables are compile errors | ✅ default; `--allow-undefined` for legacy files | `jswc/vars.py` `_check_name` |
| variable import `.jsv` / `.jsa` / `.jsl` | ✅ missing file and bad JSON refuse to compile | `jswc/vars.py` `_import_global` |
| sprite metadata method 1 `class X(x=…, …)` | ✅ | `jswc/parser.py` + `_sprite_metadata` |
| sprite metadata method 2 `X: x=…, …;` | ✅ last one wins, conflicts warn | `jswc/parser.py` + `_sprite_metadata` |
| sprite metadata method 3 `// Sprite:` | ✅ gated by `commentMetadata`, can rename the sprite | `_sprite_metadata` |
| private functions returning a value | ⬜ needs the Custom Reporters extension; `return expr` currently stops the script | roadmap 11.1 |

Notes on the implemented semantics:

* Metadata precedence is **defaults → `// Sprite:` → `class` parameters →
  standalone `X: …;` lines**, so the most explicit form wins.
* A literal initial value is stored in `project.json`; a non-literal one is
  declared with `0` and assigned by a generated green-flag script (with a
  warning), because Scratch variables cannot hold expressions.
* `useTempVariables` uses `lmsTempVars2` thread variables. Scratch threads end
  when the script ends, not when a custom block returns, so a temp variable is
  released when the calling script finishes — see roadmap 11.2.
* `--locals=thread` still exists as a blunt "make every local a thread
  variable" switch for comparing output with the reference project.


## Canvas, monitor and layer metadata (v0.6)

Everything Scratch keeps next to the blocks - where a script sits, which
monitors are on the stage, the target's volume/tempo/layer - is written as a
comment, so the source stays readable and a rebuild restores it.

```jsw
class core { // Sprite: core (-199,121) size=100 direction=90 show=1 costume=2 volume=100 layer=7 rotation=all draggable=0
    import "assets/1b63d215.svg" as "背景1"; // costume centerx=336.5 centery=255 res=1
    import "assets/848e5181.wav" as "Warn";  // sound rate=48000 samples=15360

    public void greenFlag() { // at 3979,-499
        Control.broadcast("Player.start");
    }

    // a stack that is not attached to any hat, kept where it was
    loose { // at 10340,-779
        Player.detached = 0;
        Control.wait(1);
    }
}

stage { // Stage: costume=0 volume=100 layer=0 tempo=275.3784 video=on videoTransparency=50
}

// monitor Stage.Player.playMode slider at 5,5 100x24 shown range 0..100 discrete
// monitor Stage.Player.Notes list at 378,0 100x74 hidden
// watch Stage.sensing_timer at 94,73 0x0 shown
```

| directive | meaning |
| --- | --- |
| `// at x,y` | top-level position of the script that follows (without one the script is arranged with the rest, see `--layout` in `docs/CLI.md`) |
| `loose { … }` | blocks with no hat; one chain per `loose` block |
| `// monitor <target>.<name> <mode> at x,y WxH shown\|hidden [range a..b [discrete]]` | a variable or list monitor |
| `// watch <target>.<opcode> at x,y WxH` | a monitor that shows a plain reporter |
| `costume=` `volume=` `layer=` `rotation=` `draggable=` | sprite/stage fields |
| `// costume centerx= centery= res=` | costume anchor and bitmap resolution |
| `// sound rate= samples=` | sound playback metadata |

`rotation` is `all`, `leftright` or `none`; `layer` is the `layerOrder`
(0 = back). Names in `import … as "…"` are quoted whenever the Scratch name is
not a valid identifier, because costumes and sounds are matched by name
(`switch costume to [false]`).

A callee the project calls but never defines is marked so the calls stay
dangling, exactly like the original:

```jsw
private void __log__(arg1) { // undefined
}
```

## Community extensions (v0.4)

Extension blocks are callable through their namespace. The mapping is generated
from the real extension sources (see `tools/extract_extension.py`), so every
block of every known extension is reachable:

```jsw
JSON.isArray(x);                 // skyhigh173JSON_json_is with types=Array
JSON.json_array_get(a, i);       // generic form: any extension block
Timer.startTimer("Player.timer");
console.warn("no song loaded");
Runtime.turboMode();
Util.ruleReplace(text, "\s", ",");
Temp.getRuntimeVariable("CreateNoteGroup.2");
Comments.commentC("why this exists");
WebSocket.isConnected();
```

Known extensions: `JSON` (skyhigh173JSON), `Timer` (lmsTimers),
`console` (sipcconsole), `Runtime` (runtimeoptions), `Util` (utilities),
`Temp` (lmsTempVars2), `Comments` (lmscomments), `MoreEvents` (lmsMoreEvents),
`WebSocket` (gsaWebsocket), `Pointerlock` (pointerlock), `gtm` (the Teachable
Machine extension in `userspace/school/gtm.js`) and `keySim` (Key Simulation).

The extension id (and its `extensionURLs` entry) is added to the project
automatically the first time one of its blocks is emitted.

### `gtm` — a Teachable Machine model

Every block the extension in `userspace/school/gtm.js` defines is callable
(`gtm.load`, `classify` — *top label from the camera*, `classifyImage`,
`confidence`, `isLabel`, `predictions`, `status`, `lastError`, `modelName`,
`labels`, `labelAt`, `startCamera`, `stopCamera`, `cameraActive`, `threshold`,
`setThreshold`; see [`docs/GTM.md`](./docs/GTM.md)). `classify` is the "top
label from this camera" block, and it is highlighted like the rest.

Classification runs **in the background**, so `confidence` is never a stale
value from an old click: `gtm.updateInterval(0.05)` is 20 updates a second,
`0` is as fast as the model finishes, and `gtm.updateInterval()` reads it back
(one name, two blocks, chosen by the argument count).

```jsw
public void tick() {
    Looks.say(gtm.confidence("up"));   // no classify call needed
}
```

### `keySim` — pressing keys and clicking for the player

Key Simulation (`cubesterKeySimulation`, from
<https://extensions.turbowarp.org/CubesterYT/KeySimulation>) puts input *into*
the player: the pressed key is one the project did not receive from anyone, and
`key pressed?`, `when key pressed`, `mouse down?`, `mouse x`/`mouse y` and
`when this sprite clicked` all see it.

```jsw
keySim.press("space", 0.2);        // press it for 0.2 s, then carry on
keySim.pressAndWait("enter", 1);   // … and wait the second out
keySim.hold("shift");              // down until released
keySim.release("shift");
keySim.clickLeft(-100, 20);        // 0.1 s by default
keySim.clickMiddle(0, 0, 0.5);     // … or as long as you say
keySim.clickRight(40, 40);
keySim.holdRight(1, 2);            // at x=1, y=2
keySim.releaseRight();             // the release block takes no coordinates
keySim.moveTo(0, 0);               // just the pointer, no click
```

The buttons fold the extension's `BUTTON` dropdown into the name; the general
forms take it as an argument for a computed one:

```jsw
keySim.click(0, -100, 20, 0.1, "without waiting");
keySim.holdButton(2, 1, 2);
keySim.releaseButton(1);
```

**Key names.** The dropdown stores what `KeyboardEvent.key` reports, so a
literal is spelled the way the dropdown spells it and translated on the way
out: `"space"` becomes `" "`, `"up arrow"` becomes `"ArrowUp"`, and a single
character stays itself. A literal the extension does not know is `W0034` — it
would compile to a block that can never fire. A computed key is passed through
untouched (the dropdown accepts a reporter).

```jsw
keySim.press("spce", 0.2);   // W0034: not a key Key Simulation knows
```

Every block here waits out its own duration in real time, so `--warp` does not
make `press … for 2 seconds` return early. A `--target scratch` build is
refused (`E0084`): Scratch cannot load a custom extension at all.

### Reporters with no arguments are properties

A reporter that takes nothing is a *property* of the namespace, spelled without
parentheses — the same way `Sensing.answer`, `Sensing.timer`, `Runtime.stageWidth`
and `Util.pi` already read:

```jsw
_x = WebSocket.closingCode;         // ✓ a value, exactly like Sensing.answer
_y = Pointerlock.isPointerLocked;   // ✓ 0/1
_z = WebSocket.closingCode();       // ✓ also accepted: same block
```

Both spellings compile to the same reporter, so writing `()` is harmless, but
the **decompiler always writes the property form** — one canonical spelling per
block keeps a round trip byte-identical. A project that declares a variable with
the same dotted name (`public WebSocket.closingCode = 0;`) keeps its variable:
a declared name shadows the extension.

### Commands with several forms dispatch on the argument count

Some extension commands are several blocks in disguise. Closing a WebSocket is
the clearest case — the extension ships three blocks, and the language gives them
one name:

| written | compiles to |
| --- | --- |
| `WebSocket.closeConnection()` | `closeWithoutReason` — "CLOSE CONNECTION" |
| `WebSocket.closeConnection(code)` | `closeWithCode` — "Close connection with code %s" |
| `WebSocket.closeConnection(code, reason)` | `closeWithReason` — "…with reason %s and code %s" |
| anything else | `E0076`, with the allowed counts in the message |

### `WebSocket`

| JadenScript | block |
| --- | --- |
| `WebSocket.connectTo(url)` | `newInstance` |
| `WebSocket.sendMessage(payload)` | `sendMessage` |
| `WebSocket.receivedMessageData` | `messageData` |
| `WebSocket.isConnected` | `isConnected` |
| `WebSocket.connectionErrored` | `hasErrored` |
| `WebSocket.isConnectionClosed` | `isClosed` |
| `WebSocket.closingCode` / `.closingMessage` | `closeCode` / `closeMessage` |
| `WebSocket.closeConnection(…)` | the three closing blocks |
| `// hook: WebSocket.onMessage` (also `onOpen`, `onError`, `onClose`) | the event hats |

The extension's own names (`newInstance`, `messageData`, `hasErrored`,
`isClosed`, `closeCode`, `closeMessage`, `closeWithoutReason`, `closeWithCode`,
`closeWithReason`) are still accepted, so older sources and decompiled projects
keep compiling; the decompiler just never writes them.

### `Pointerlock`

```jsw
Pointerlock.setPointerLock(1);         // "enabled" in the dropdown
Pointerlock.setPointerLock(0);         // "disabled"
Pointerlock.setPointerLock(pressed);   // a runtime 0/1, passed through
if (Pointerlock.isPointerLocked) { … } // reads like a boolean
```

`setPointerLock(n)` takes 0/1 (or any expression). A literal is pinned to the
menu's own value (`true`/`false`) so the block shows the dropdown in the editor;
anything else is handed to the `acceptReporters` input, with `W0004` to say the
picker is resolved at run time.

### `TurboWarp` — keyboard and mouse

The `tw` extension is built into TurboWarp, so no URL is needed:

```jsw
_last = TurboWarp.lastKeyPressed;      // "a", "A", "space", "left arrow", "any"…
if (TurboWarp.primaryButtonDown) { … }      // 0 = primary (usually left)
if (TurboWarp.secondaryButtonDown) { … }    // 2 = secondary (usually right)
if (TurboWarp.isMouseButtonDown(n)) { … }   // 0 / 1 / 2 at run time
```

`lastKeyPressed` is the *cased* key the keyboard device stores: a single letter
keeps the case it was typed with, everything else is the name Scratch uses
(`space`, `enter`, `up arrow`, …). It is a property, like every other
no-argument reporter.

## Stage scripts (v0.4)

```jsw
stage {
    public void whenStageClicked() { // hook: stage click
        Player.toggleDisp();
    }

    public void whenBackdropChanges() { // hook: backdrop night
        Looks.say("good evening");
    }
}
```

Hooks: `stage click`, `backdrop <name>`, `click`, `before project save`,
`after project save`, `broadcast <name>`, `green flag`, `clone`,
`when [key] key pressed`.

## Temp variables (v0.5, revised v0.9)

A bare `_name` is the project's local variable. Which *kind* of local it is
depends on one switch:

```jsw
class synth {
    private _next = 0;             // sprite-local variable ("for this sprite only")
    ...
}
```

```jsw
#enable_feature<useTempVariables>;

class synth {
    private _next;                 // declaration without an initial value
    private _id = 0;               // still fine

    public void tick(){
        _next = score + 1;         // lmsTempVars2_setThreadVariable
        score = _next;             // lmsTempVars2_getThreadVariable
        _next += 1;                // lmsTempVars2_changeThreadVariable
    }
}
```

**Why sprite-local is the default (measured, not assumed).** A sprite-local
variable is *copied* when a sprite clones itself, so every clone owns its
value. Measured in TurboWarp with a probe project: the original sets
`local = 1` and creates a clone; the clone sets `local = 42`; after both write
their value to stage variables the original still reads `1` and the clone reads
`42`. Clones do not share sprite-local variables.

So `_x` only needs the temp extension when the *same* clone runs the same
procedure in two threads at once (recursion, or two hats interleaving) — that is
what a thread variable buys. The decompiler turns the extension on only for
projects that already use `lmsTempVars2` (it writes the feature line itself),
and leaves every other project on plain sprite variables. `--locals thread`
forces thread variables for a build.

Decompiling the extension blocks produces exactly these declarations, so a
project's temp variables come back as `private _name;`.

## TurboWarp predicates (v0.5)

```jsw
if (!isTurboWarp()) { logError("open this in turbowarp"); }
if (!isCompiled())  { logWarn("running uncompiled"); }
```

Both are direct calls (no namespace) and evaluate to `1`: a project that uses
TurboWarp extensions cannot load in vanilla Scratch, so the guard is satisfied
by construction. Declaring a variable with the same name takes over:
`public isCompiled = 0;` makes `isCompiled()` read that variable.

## pishi.ai extensions (v1.0)

```jsw
#enable_feature<pishiSupport>;
```

pishi.ai's player ships eight extensions of its own — computer vision, a chat
bot and an Arduino bridge — and they exist **only** in that player, so they are
behind a feature: a source that turns it on is saying "this build is for
pishi.ai", and a source that does not cannot depend on them by accident.

| namespace | what it is | blocks |
| --- | --- | --- |
| `Facemesh` | face landmarks | 21 |
| `Handpose` | hand landmarks | 19 |
| `Posenet` | body pose, named joints | 54 |
| `ObjectDetection` | boxes, labels, confidence | 36 |
| `GoogleTeachableMachine` | a trained image/sound model | 26 |
| `SpeechRecognition` | speech to text | 9 |
| `AIChatBot` | chat completion, models, roles | 12 |
| `Arduino` | pins, PWM, servos, I²C, LEDs, tone | 45 |

```jsw
#enable_feature<pishiSupport>;

public lastNose = 0;

class main {
    public void greenFlag() {
        Facemesh.turnClassification("on");
        Posenet.setMinimumConfidence(0.5);
        lastNose = Posenet.getNoseX();
        if (Arduino.isAnalogInRange(3, 2, 9)) {
            Arduino.digitalWrite(13, "high");
        }
        Arduino.setPinMode(9, "servo");
    }
}
```

Arguments are positional, the dropdowns are literals, and the extension is
declared in the project (`extensions: ["facemesh", …]`) with **no
`extensionURLs`** — exactly what the player writes. Menus become shadow blocks
the way every other extension's do (`facemesh_menu_keypointMenu`).

The table is generated from a project the player saved
(`examples/PishiExtensions.sb3`): argument names, order and menus are read from
the blocks themselves, and the block *kind* (command / reporter / boolean / hat)
from the player's bundle, snapshotted into `examples/pishi_kinds.json` so a
refresh needs no download:

```
python3 tools/extract_pishi.py examples/PishiExtensions.sb3 --write
```

Two name collisions are reported, because a namespace is how a block is
*spelled*:

| collision | severity | why |
| --- | --- | --- |
| another extension claims the same namespace | **error** `E0077` | the compiler would resolve the call to one extension and the player would run the other |
| a variable, list or sprite has the namespace's name | **warning** `W0028` | the declaration still compiles, but `Arduino.x` always reaches the extension, so the two cannot both be spelled that way |

Decompiling a project that lists any pishi extension writes
`#enable_feature<pishiSupport>;` at the top and turns every block back into a
call, so a pishi project round-trips like any other.

## Obscured names (v1.0)

```jsw
#enable_feature<doObfuscation>;
```

Compiling with this switch rewrites **every** name the project exposes, fills
the workspace with things that are not real, and can turn a straight script into
a state machine -- so publishing a build publishes neither your naming nor your
shape:

| object | before | after |
| --- | --- | --- |
| variable, list | `Player.score`, `Player.notes` | `v0`, `v1`, … |
| custom block label | `Player.move %s` | `p4 %s` — the `%s`/`%b` slots stay |
| broadcast | `Ping` | `c0` |
| class / sprite | `main` | `s0`, `s1`, … |
| thread variable (`lmsTempVars2`) | `_i` | `_kQm2xW` (keeps the underscore) |
| custom block argument | `n` | `Vh2kQa` |
| text and integer literal | `say "Hello"`, `Pen.setSize(3)` | `item 7 of (v12)`, `item 41 of (v40)` |

**Why numbers.** A dotted name (`Player.score`) still says which class a
variable belongs to, and a word says what to look for. A number says nothing:
one counter runs across the *whole* project, so a Stage global and a
sprite-local sit next to each other in the numbering, and neither the spelling
nor the position says which sprite a name came from. The numbers are handed out
in shuffled order, and the decoys below take numbers from the same counter, so
`v17` is no more likely to be real than `v16`. Sprites are `s%d` from their own
counter, shuffled the same way, so the target list does not say which sprite
came first either.

**What is added.** All of it is inert -- nothing calls it, nothing reads it, and
a script that never runs costs nothing at run time:

* decoy variables and decoy lists, on the Stage and in the sprites, sized to
  match the real ones so the string lists are not the big obvious ones;
* decoy custom blocks with plausible bodies (`repeat`, `if/else`, list adds)
  that nothing calls, so "My Blocks" has more entries than the project has
  functions. A decoy **declares arguments too** -- names from the same word
  generator a real parameter gets, `%s` and `%b` mixed, and a body that reads
  them: a workspace whose one zero-argument block is also the one nobody calls
  answers "which block is the decoy?" by itself;
* decoy broadcast messages, so the message list has unused names in it.

Nothing the pass invents keeps a working name. Decoys are called what an author
would have called them (`Phase3`, `Buffer1`, `flush sector %s`) until the
renaming runs, and then they are `v%d`/`p%d` like everything else -- a build
with a name that spells `__trash0` would hand a reader the whole trick.

**The string lists.** Every *text* literal moves into Stage lists, and the
script reads it back as `item i of list`. The literals are spread over **three**
lists (`--obf-pools N`; `1` puts them all in one), because one list holding
every string in the project is the one thing in the workspace that reads as
"this is the string table". Which list a literal lands in is decided by a hash
of the text, not by fill order, so the same source always produces the same
split.

Each list is shuffled and filled with junk strings (numbers, hex blobs, words,
coordinates) -- `--obf-junk N` sets how much per list, `--obf-junk 0` turns the
decoys off. The decoy lists are grown to a similar length, so list size says
nothing either. Floats stay inline: a float is a number with no words in it, and
there are far fewer of them than integers.

**Integers become variables** (`--obf-ints auto`, the default). A hidden
integer still has to be *read*, and the shape of that read is the whole cost:

| form | what a use becomes | i386 emulated work |
| --- | --- | --- |
| `--obf-ints none` | the literal, inline | 1.00× |
| `--obf-ints auto` | one variable per constant, read as `(v123)` | **1.44×** |
| `--obf-ints list` | `item i of v456` in a string list | **3.30×** |

The list form is the expensive one because the compiled runtime has to convert
and bound the index on every single use, and an inner loop runs those thousands
of times -- no threshold helps, because the constants that matter at run time
are not the ones that appear often in the source (a constant written once inside
a `while` body runs forever). A variable read compiles to a property load, and a
variable's initial value is state exactly like a list item, so `auto` hides just
as much for less than half the cost. The value is stored as a number; a string
`"5"` in a variable costs another 2×, and an integer that is used as *text*
(`say("007")`) keeps its spelling and stays in the pool instead.

Speed, measured on the two projects in this repository (a fixed slice of real
time in the real VM, count of emulated frames / player ticks):

| project | plain | hidden (`auto`) | hidden (`list`) |
| --- | --- | --- | --- |
| i386 (`make i386-obf` uses `none`) | 9,840 | 6,715 (1.44×) | 2,727 (3.3×) |
| NotePlayer (`build/np5-release.sb3`) | 349,086 ticks | 310,959 (0.89×) | 277,883 (0.80×) |

`--obf-ints none` stays the switch for a build that has to keep every last
frame — a CPU is literal-heavy and this machine's whole point is speed, which is
why `make i386-obf` uses it. `--obf-junk`, `--obf-pools` and `--obf-flatten` do
not measurably change the cost.

**State machines.** With `--obf-flatten N` (default 16) every hat whose body is
at most `N` blocks gets a state variable and a chain of guards:

```
when green flag clicked            when green flag clicked
  set x to 0                         set v8 to 17
  change x by 1                      if <17 = (v8)> { set x to 0;     set v8 to 4 }
  change x by 2                      if <(v8) = 4>  { change x by 1;  set v8 to 31 }
                                     if <(v8) = 31> { change x by 2 }
                                     set v12 to 3
                                     if <(v8) = 93> { set v12 to 7 }   <- never true
```

The state lives in a variable and the numbers are scattered, so a reader has to
follow `v8` to recover the order. The chain is *straight line* on purpose: a
`repeat until` dispatch costs a frame per state (Scratch yields at the end of
every loop iteration) and a self-calling block costs a step per call (the
compiled runtime cannot inline a recursive procedure). Either one changes the
project's timing; measured on the i386 machine, both did — the display thread
fell behind on the very first step. A guard that is already true costs one
comparison, so the whole thing measures at about 10% of the emulated work in a
3-second run.

The variety is deliberate: every script gets its own state variable from one
counter across the whole project (two scripts on one sprite are not both
`Phase1`), one to three dead guards are mixed in among the live ones at random
positions, a guard writes `<(v8) = 4>` or `<4 = (v8)>` depending on the draw,
and a dead guard carries a different body each time. A uniform shape -- always
one decoy guard, always the state on the left, always the last state -- is a
signature the shape itself gives away.

Scattered numbers also mean the *order* is not readable from the branch order,
and a script that `stop this script` flattens like any other: nothing was pushed
on the stack, so the stop stops exactly what it stopped before. The dead guards
only ever write their own variable, so a mistake there cannot steer the machine
that does run. `--obf-flatten 0` turns the whole thing off.

**The editor residue.** `--obf-scrub` takes out what is left after the names:
block `x`/`y` (where the author dropped each script on the canvas -- a column at
`x = 200` growing downwards is a script somebody wrote by hand) and workspace
comments (`// comment` notes are free text about the source). It is off by
default because a note is sometimes the point of the build; monitors and lists
are not affected, since those are state, not metadata.

Everything stays wired: variable/list references, monitor mode/position/range,
workspace comments, extension dropdowns that name a list or variable
(`JSON`'s list pickers) and thread-variable names follow the rename, and
variable/list/block **ids do not change**, so the block structure is the same
project with different labels. The *declaration order* of variables and lists is
shuffled as well -- nothing addresses state by position, so the order was free
information about what the source declared first.
variable/list/block **ids do not change**, so the block structure is the same
project with different labels.

Deliberately *not* renamed, with the reason:

| kept | why |
| --- | --- |
| costume / sound names | menus and the project's own name lookups point at them |
| `// monitor` / `// watch` comments | source directives naming the *original* objects |
| float literals | a number with a decimal point has no words in it |
| workspace comments, block `x`/`y` | metadata, not names — `--obf-scrub` drops these |

Two consequences:

* A project that asks for a **sprite by name at run time** (`Sensing.of`, a
  "sprite exists" extension block) sees the obscured names.
* A project that **builds a broadcast name** (`Control.broadcast(join(module,
  ".prop"))`) cannot find its receiver once the messages are renamed. The
  compiler knows this and reports `W0023` at that call.

The same source always produces the same obscured names, so diffs and golden
tests still work. `--enable doObfuscation` does the same from the command line.

`hideObjectNames` is the old spelling of this switch: it is still accepted and
reported as `I0002`, but `doObfuscation` is the name to write.

## VM protection (v1.1, `vmProtect`)

```jsw
#enable_feature<vmProtect>;
```

`vmProtect` replaces the body of every script and custom block it understands
with a call to a bytecode interpreter. A hat stays where it was, so Scratch
still decides when the script starts; its body becomes
`__vm_start(program, frame)`. A custom block keeps its definition and signature,
but its body becomes a call to `__vm_proc_start(program, temps, argc, ...)`.
The interpreter lives in the same sprite because Scratch custom blocks are
per-target. The default `h0` backend emits the shape of `examples/h0.jsw`:
`__vm_switch` has one numbered `case` per instruction, and `__vm_run` calls
that switch in a loop. `--vm-style list` keeps the immutable instruction
stream and its arguments in Stage lists (`__vm_ops`, `__vm_args`), with
constants in `__vm_k`, then dispatches each instruction through `__vm_step`.
The h0 style trades a larger project (one Scratch case block group per
instruction) and a potentially slower case scan for explicit dispatch; it is
still a block backend, not a source-to-source class rewriter.

The generated layout is randomized on every build: opcode ids, h0 case labels
and their order, and helper names all depend on a fresh seed. `--vm-seed N`
reproduces a build exactly. `--vm-crypt 0..3` selects the hardening level:
0 fixed layout for debugging, 1 default randomized layout, 2 adds a checksum
guard to every VM entry, and 3 adds unreachable decoy cases. The h0 backend
puts each unique instruction body in its own random-named helper method by
default; `--no-vm-split` keeps the bodies inside `__vm_switch`.

Generic community-extension commands and reporters are lowered through the
same native-call vocabulary as the core blocks. Their fixed menu values are
written as literals; a computed menu, an extension loop/conditional, or an
unknown extension opcode leaves the program native with `W0031`. Extension
hats keep their native trigger block and virtualize the body.

### Milestones

This is the original vmProtect plan, kept so the stage names do not drift.
The `M2` label in early checkpoints meant procedure virtualization; in this
plan that work belongs to **M3**.

| stage | work | status |
| --- | --- | --- |
| M0 | prototype the dispatcher by hand; prove wait/warp, thread variables across yields, and clone list copying | ✅ |
| M1 | bytecode compiler for hats, variables, literals, arithmetic, `if`/`repeat`/`forever`, `wait`, `broadcast`; stubs and runtime skeleton | ✅ |
| M2 | all built-in and extension blocks, reporter handlers, menus, boolean truthiness, list operations | 🟡 partial: generic extension commands/reporters/menus, comment blocks and `forEachThreadVariable` are virtualized; some extension loops, conditionals and dynamic menu values still fall back to native `W0031` |
| M3 | custom blocks: frames, parameters, recursion, warp, receiver hats, cross-sprite broadcasts | ✅ |
| M4 | clones, stop variants, ask, glide, `broadcastAndWait`, `wait until`, edge/extension hats | ✅ wait-until is bytecode now; extension hats keep their native trigger and virtualize their bodies |
| M5 | thread arena, lazy growth, overflow handling, green-flag reset | ✅ dynamic growth/reuse, active-frame cleanup, and a guarded green-flag reset |
| M6 | encoding/crypt levels, dispatch shuffling, checksums, decoys, slow knobs | ✅ `--vm-crypt 0..3`: fixed/random layout, checksum guards and decoy cases; `--vm-seed`, `--vm-budget` and split helpers |
| M7 | `doObfuscation` / `corruptBlockPos` composition, validator, diagnostics | ✅ |
| M8 | differential harness, CI fixtures, docs/CLI/editor data | 🟡 partial: tests and docs are in place; extension-heavy fixtures are not fully covered |

Mutable frame state always lives in one `__vm_stack` list, and
`__vm_next`/`__vm_free` track frame allocation. Each VM frame's `base` is a
custom-block parameter, which the interpreter keeps per invocation across
yields and broadcasts, so clones and concurrent scripts never share a state
block. Stage hats are left native in this slice: the Stage owns the programs and
the arena, so it is already where the state lives.

The instruction set is deliberately small at this stage:

| source | bytecode |
| --- | --- |
| variable read/write, `+=`, literals | load/store/change, constant pool |
| `+ - * / %`, `< > =`, `and or not` | arithmetic and comparison cases |
| `join` | `join` case |
| `if` / `if/else`, `repeat`, `forever`, `repeat until` / `while`, `wait` | jumps, a repeat counter, a wait case |
| `broadcast`, `broadcast and wait` | queued and delivered at a yield point |
| simple looks/motion/sound/pen/music/sensing commands | native call, with each input read from the bytecode |
| `length of`, `item of`, `contains`, `item # of` and list add/set/insert/delete/clear | native list method; a list name that is not an identifier stays native |
| `Motion.x`, `Motion.direction`, `Sound.volume`, `Looks.size` writes | native property setter/change |
| `say`, `say for secs`, `stop this script`, `stop all`, `stop other scripts` | native looks/stop calls |
| a custom block defined in the same sprite | its body is virtualised; calls go through its native stub |
| a call to a custom block | a native call into the stub, with arguments read from the VM frame |
| `green flag`, broadcast, key, click, backdrop, clone hats | the hat is kept, its body is virtualised |
| custom-block parameters | `argument_reporter` blocks load from the frame's parameter slots |

A script or custom block that uses anything else is **left as ordinary blocks**
and reported as `W0031`; the project always builds. A script that calls a
native custom block whose body contains `while (1)` is left native too: the
native call would never return to the interpreter, and a clone started from it
could take the scheduler's only slot. `vanillaScratch` wins over `vmProtect`: a
build for scratch.mit.edu keeps its native blocks and reports `W0030`.

A virtualised `procedures_definition` is still a real Scratch custom block, so
native scripts (including a hat that `W0031` left native) can call it and get
the interpreter. Its parameters occupy frame slots immediately after the eight
interpreter state slots; temporary values come after the parameters, and the
operand stack comes after both. A call starts a nested `__vm_run`; the frame is
allocated from `__vm_next` and returned to `__vm_free` when that run halts, so
recursive and concurrent calls reuse frames instead of walking off the arena.
There is no separate bytecode call stack: the native custom-block call is the
call stack. `stop this script` inside a virtualised custom block halts the
frame and returns to its caller, matching Scratch.

A `// warp` custom block is left native, and so is every procedure reachable
from one. The interpreter's `__vm_run` is a `while (1)` loop and must yield at
the end of each iteration; Scratch's warp mode skips that yield, so putting the
interpreter inside a warp call chain would freeze the tab. A virtualised
caller can still call a native warp custom block, so only the warp chain loses
protection. The same rule explains the conservative counts below: i386's CPU
is almost entirely warp procedures.

The field values of a block (`effect`, `mathop`, `rotation style`) are literals
in Scratch, so they are written straight into the generated call; the input
values are the only thing read from the VM stack. A field is not an input
socket, and handing it a reporter would leave that reporter orphaned in the
project JSON.

On this repository's large projects the split (counted in programs: scripts
plus custom-block bodies) is: i386 3 protected and 228 native, rv64 2 and 102,
and the Scratch 3 emulator 89 and 210. Most of the native programs are `// warp`
custom blocks or helpers called by one. The i386 CPU boot script is also native
because it calls `mainLoop`, an infinite native block; the three protected
programs are the service hats that were protected in M1, and the machine still
boots. Leaving the warp chain native is what keeps that boot from turning into
a frozen tab.

`wait until` is left native: it takes a **reporter**, and passing one evaluated
boolean would freeze the script on the first false. `--vm-budget` is the speed
knob: 1 is the deliberate crawl, 10 is usable for a small project, and a CPU
that has to keep its frame rate needs a much higher number. The frame arena
starts with 320 slots and grows by one frame when nested custom-block calls or
simultaneous clones need more; a running frame is never overwritten. The free
list reuses frames as calls return, so a long-running project does not pay for
the arena's high-water mark on every call.

The point is the slowdown: `__vm_run` is a `while (1)` loop, and Scratch yields
at the end of every iteration. The interpreter is always non-warp, even in a
`--warp` build; a warp loop would never yield back to Scratch, so `--warp` only
reaches the native custom blocks the interpreter calls. `--vm-budget N` runs N interpreter steps per
iteration. The default is 1 (one instruction per frame), which is intentionally
extreme; raise it for a playable build. `0` is treated as `1`.

Broadcasts are queued rather than started in the middle of a bytecode step, and
delivered before a wait, a loop back edge, `broadcast and wait`, or the end of
the script. That keeps the native ordering where a receiver sees the sender's
state at its next yield, instead of seeing a half-updated interpreter frame.

With `doObfuscation`, text literals are already pooled before this pass: those
pool reads are resolved to constants when the bytecode is built, and a queued
broadcast follows the message rename so sender and receiver stay matched.
The VM's own state lists and helper blocks go through the same rename as
everything else, so a protected release does not advertise itself with a row of
`__vm_` names.

This is anti-tamper, not encryption: the program is bytecode in a Stage list, so
somebody who is determined can read and change it. What it removes is the
straightforward "open the project, read the scripts" path, and the interpreter
makes the control flow much harder to follow in the editor.

## Eval warnings (v1.0)

Scratch resolves some names **at runtime**: a broadcast whose message is a
variable, a clone target, a costume/sound menu, an extension list picker. That
is an eval — a typo or a rename fails silently instead of at compile time — so
the compiler warns:

```
warning: 5:27: eval: the broadcast name is computed at runtime; the VM looks
         the name up when the block runs, so a typo or a rename fails silently
```

Caught today:

| where | example |
| --- | --- |
| broadcast message | `Control.broadcast(name)` / `Control.broadcastAndWait(name)` |
| clone target | `Control.createClone(name)` |
| costume / backdrop | `Looks.switchCostume(name)` |
| sound | `Sound.play(name)` |
| sprite of `go to` | `Motion.goto(name)` |
| extension pickers | `JSON.…` list/variable menus fed with a variable |
| `menu()` / `block()` | a field value that is not a literal |

Literals are silent, so `Control.broadcast("Ping")` compiles without noise.
Numeric dropdowns (`note`, instrument, drum) are exempt: a computed *number* is
not a name lookup.

## Raw blocks (v0.9)

Two projects in a row contained a reporter the language has no name for — a
call to a parameter the procedure never declares (`<is compiled?>`,
`<value>`). Scratch reads those as empty, but dropping them loses a block:

```jsw
if (block("argument_reporter_boolean", "VALUE", "is compiled?")) { … }
x = block("argument_reporter_string_number", "VALUE", "value");
```

`block(opcode, field, value)` emits exactly that block (not a shadow), which
keeps the round trip faithful without inventing syntax for every block Scratch
can hold. `menu(opcode, field, value)` is the dropdown version.

## Dropdown blocks as values (v0.8)

A dropdown is normally an argument: `Sound.play("6LA108L")` puts the sound
dropdown in the sound slot. Sometimes a project uses the dropdown block itself
as a value, or drags one onto the canvas on its own. `menu()` spells that:

```jsw
create_note(Operators.round(menu("note", "NOTE", 66)), 16, 0.25, 0, 1);

loose { // at 238,-200
    menu("music_menu_INSTRUMENT", "INSTRUMENT", 1);
}
```

Three arguments: the block opcode, the field name and the field value. The
compiler creates the block with `shadow` set, so the editor shows the dropdown
it showed before. Without `menu()` those blocks collapsed to a plain `"66"`, a
text box where a note picker belongs.

`menu()` is also how the decompiler reports a dropdown it cannot express as an
argument. Two things it still cannot carry, because they are invisible in the
rendered project but not in the file:

* the fallback shadow of a *dynamic* slot (`sound_play <variable>` keeps a
  sound dropdown under the reporter; the rebuild has an empty text slot), and
* a compressed variable read (`[12, name, id]`) becomes a `data_variable`
  block again on rebuild — the editor compresses it back when it saves.

## Console statements (v0.7)

These six statements write to the **TurboWarp Consoles extension**
(`sipcconsole`, the *Logs* tab of the editor). They are not the browser
developer console: nothing here reaches DevTools.

```jsw
log("tick " + Tick.tick);   // sipcconsole_Journal   — plain log line
warn("deprecated path");    // sipcconsole_Warning   — yellow line
error("no MIDI device");    // sipcconsole_Error     — red line
startTimer("Player");       // sipcconsole_Timeron   — start a named log timer
stopTimer("Player");        // sipcconsole_Timeroff  — stop it, logs the time
breakpoint;                 // sipcconsole_debug("@breakpoint") — a marker line
```

`log`/`warn`/`error`/`startTimer`/`stopTimer` take one expression and are
statements, so the argument is not required to be a string. `breakpoint` is a
keyword statement, not a call and not a variable, and takes no arguments.

The extension has no breakpoint block, so `breakpoint;` compiles to
`sipcconsole_debug("@breakpoint")`: the marker shows up in the Logs tab at the
right moment, which is what the statement is for. `console.debug("…")` stays
available for an arbitrary debug line.

Decompiling goes the other way:

| block in the project | source |
| --- | --- |
| `sipcconsole_Journal` | `log(x);` |
| `sipcconsole_Warning` | `warn(x);` |
| `sipcconsole_Error` | `error(x);` |
| `sipcconsole_Timeron` | `startTimer(x);` |
| `sipcconsole_Timeroff` | `stopTimer(x);` |
| `sipcconsole_debug` with `@breakpoint` | `breakpoint;` |
| `procedures_call` whose proccode is a zero-width-space name (`\u200blog\u200b\u200b %s`, `\u200bbreakpoint\u200b\u200b`) | the same six statements |

The zero-width names are the console add-on's hidden calls. The reference
project has 17 of them, with no definitions, so they are worth keeping as real
console statements instead of becoming `__log__` stubs. Three of those scripts
log the same value twice (once through the hidden call, once through the real
block); the decompiler marks the second line with
`// logged twice in the original project` so the duplicate is explained rather
than silently merged. Delete either line if a single entry is wanted.

## f-strings (v0.5)

```jsw
Player.sbar = f"T:{Tick.tick}/{Player.total} NPS:{nps}";
```

compiles to nested `join` blocks, left to right, and `{{` / `}}` are literal
braces. The decompiler folds join chains of five or more pieces back into an
f-string (`--max-inline`-independent; `Decompiler(fstring_threshold=…)`).

## Number literals (v0.9)

Numbers are written the way the block shows them:

```jsw
x = 42;              // 42
y = 1.5;             // 1.5
mask = 0x100000000;  // 4294967296 — integer, not float
flags = 0b1010;      // 10
mode = 0o17;         // 15
label = "007";       // stays text: leading zeros are meaningful
```

The decompiler drops the quotes when a value *is* a number (`"1"` → `1`), so
blocks read like the canvas. Strings that only look numeric keep their quotes:
`"007"`, `"2."`, `""`, `"1 2"`.

Hexadecimal, binary and octal text is written in that spelling (`0x100000000`)
and converted to decimal on the way into the project, because that is what the
VM stores. A round trip therefore *normalises* `0x…` to plain decimal — the
value is identical, the spelling is not.

## Conditions are true/false slots, and `jswc` fills them

`if`, `while`, `repeat until`, `and`, `or` and `not` take a **Boolean** in
Scratch, not a value. `scratch-blocks` checks that on load: a variable, a list
item or `a + b` in a condition is refused ("Input CONDITION expected Boolean,
found String") and the whole script is dropped from the editor's workspace —
the project still runs, but that stack is invisible and uneditable.

A condition in JadenScript is an expression, so anything that is not already a
Boolean gets wrapped in the VM's own truthiness test:

```jsw
while (active) { }            // while <not <(active) = 0>>
if (count + 1) { }            // if <not <((count) + 1) = 0>>
if (a > b) { }                // if <(a) > (b)>  — already Boolean
```

That reads a number the way Scratch's `Cast.compare` does: `true`/`false` are
1/0, empty text is 0, and any other text is not 0. The one divergence from
`Cast.toBoolean` is the literal text `"false"`, which the VM reads as false and
`<… = 0>` reads as true — write `(flag = "false")` if that spelling matters.

## Comparing text (a VM trap, not a language rule)

Scratch's `=` and its list search compare two values by **first trying numbers**
and only then falling back to a case-folded string compare. Two consequences
that a program has to be written around:

```jsw
"0" == " ";      // true!  both cast to the number 0
"a" == "A";      // true:  the fallback lowercases
item # of "a" in ["A","b"];   // 1, not 0
```

So `if (ch == " ")` does **not** test for a space when `ch` might be `"0"`, and
a Base64 alphabet cannot be looked up in a list, because its upper and lower
halves would collide. Text that has to survive this is either hex (where `A` and
`a` mean the same digit) or matched against a known alphabet rather than against
whitespace. `userspace/np5/loader.jsw` does both, and says why.

## `+` is arithmetic, and `join` is text (the other VM trap)

`+` is `operator_add` and nothing else: both operands are cast to numbers
before the addition, so text on either side is worth zero.

```jsw
"abc" + "def";     // 0, not "abcdef"
"0x" + n;          // 0, not "0x2f"
join("abc", "def");        // "abcdef"
join("0x", n);             // "0x2f"
```

There is no operator that concatenates. A chain of `+` therefore *compiles* --
it is a read-only block, not an error -- and then quietly produces `0` at
runtime, which is how a decoder that built every line out of `+` came to
publish the number zero while every test that ran it agreed it worked.

The decompiler writes `join` blocks back as `join(…)` or as an f-string
(§ *f-strings*), and `operator_add` back as `+`, so a round trip is exact. The
simulator in `tools/jsw_sim.py` follows the same rule and reports `0`, so a
test that runs the decompiled project cannot hide this.

## Naming style (v0.4)

`#enable_feature<namespacedNames>;` plus `ns=<Prefix>` in the sprite comment
makes bare global names and public procedures adopt the project's dotted style:

```jsw
#enable_feature<namespacedNames>;

class graph { // Sprite: graph (165,59) size=100 direction=90 show=1 ns=Graph
    public dpoint_x = 0;          // → variable Graph.dpoint_x
    public void doGraph(){ … }    // → broadcast + custom block Graph.doGraph
}
```

Sprite-local (`private`) names are never rewritten, so clones keep their own
variables. Without the feature (or without `ns=`) nothing is prefixed.

The prefix is only added for what the class itself declares `public`. A
file-scope `public i = 0;` is a plain Stage variable, so a read of it inside
`class graph` stays `i` — prefixing it would fork the value into a second,
empty `Graph.i`. Converted runtime variables (see above) are globals too and
are never prefixed.

A custom block whose label cannot be a function name keeps its exact text:

```jsw
private void create_note(t, n) { // proccode create note %s in tick %s
```

The placeholders are `%s`, `%n` and `%b`; the language itself has one untyped
parameter, so the **count** is what has to match the parameter list. `%b` is
kept because a decompiled Scratch project may declare a boolean parameter: the
prototype then gets an `argument_reporter_boolean` shadow with the default
`false`, which is what scratch-blocks expects for that slot.

## Huge state (v0.4)

A list or string bigger than `--max-inline` (default 50 items / 1000
characters) is written to a side file and imported, so the source stays
readable:

```jsw
import "assets/lists/Player.NotesData.jsa";          // public
import "assets/lists/privateBig.jsa" as privateBig;  // sprite-local
import "assets/vars/longText.jsv" as longText;
```

## Constructs added for exact decompilation (v0.3)

The decompiler must be able to express every block it meets, so the language
gained a few constructs. All of them are also useful by hand.

```jsw
repeat (10) { … }                    // control_repeat (count loop, no variable)
Comments.commentC("why") { … }       // a extension C-block that owns its body
Temp.forEachThreadVariable("i", 3) { … }
public scores = [10, 20, 30];        // list with initial items
x = join("a", b);                    // operator_join
Control.broadcast("My Message");     // broadcast by exact name
Control.broadcastAndWait("Ping");    // broadcast and wait by exact name
Control.deleteThisClone();           // control_delete_this_clone

public void onPing() { // hook: broadcast Ping
    // a receiver whose body is not just a procedure call
}

onPing();                            // broadcast "Ping" and wait for it
call onPing();                       // broadcast "Ping", do not wait
Control.broadcast("Ping");           // same thing, spelled with the block name

public void whenClicked() { // hook: click
    // event_whenthisspriteclicked
}
```

A `// hook: broadcast` method is a *hat*, not a custom block, so a call to it
cannot be a `procedures_call`: it always broadcasts. `X();` waits for the
receivers, `call X();` runs them in the background. The decompiler writes these
two spellings whenever the message has a receiver; a message that is computed
(`Control.broadcastAndWait(join(module, ".prop"))`) keeps the explicit form,
because no name can be written for it. A real custom block of the same name
wins over the receiver, so existing procedure calls are unaffected.

`join` takes any number of operands and can be written flat. The compiler
builds a *balanced* tree of binary `operator_join` blocks, so the nesting depth
is ⌈log₂ n⌉ rather than one level per operand:

```jsw
join(1, 2, 3, 4, 5, 6, 7, 8);
// join(join(join(1,2), join(3,4)), join(join(5,6), join(7,8)))
join(1, 2, 3, 4, 5, 6, 7, 8, 9);
// join(<the eight above>, 9) — an odd operand rides along one level
```

The decompiler folds a join chain back into the flat call (or an f-string once
it has enough pieces), so the shape survives a round trip as a value even when
the original project nested it the other way.

Reserved words are accepted as plain identifiers (`static = 1;`,
`Player.delete(1)`), because real projects contain such names. Parameters and
procedure names that collide with a keyword are suffixed by the decompiler
(`in` → `in_`) and reported as warnings.

`jswc decompile project.sb3 -o out.jsw` writes the source plus an `assets/`
folder; see [`docs/DECOMPILER.md`](./docs/DECOMPILER.md) for the exactness
guarantees and the remaining gaps.

## Handle attachments
In TurboWarp, we can import attachments (photos, audios, etc.) within the UI




### Asset imports (v0.1 implementation)

`import` is implemented and embeds real assets into the generated `.sb3`:

| form | meaning |
| --- | --- |
| `import "art/piano.png";` | costume named after the file stem |
| `import "art/hit.wav" as Hit;` | sound named `Hit` |
| `import "logo.svg" (as Logo);` | parenthesised form from the user rules |

* Paths resolve relative to the `.jsw` file.
* *Where* the line sits picks the target: inside a class it is that sprite's
  costume or sound, in `stage { … }` or at the top level it is the Stage's --
  which is how a project gets a backdrop of its own.
* Images (`.svg .png .jpg .jpeg .bmp .gif .webp`) become costumes; PNG and SVG
  dimensions are read so the rotation centre is correct.
* Sounds (`.wav .mp3 .ogg`) become sounds; WAV headers are parsed for
  `rate` and `sampleCount`.
* Bytes are stored in the zip as `<md5>.<ext>` and referenced by
  `assetId` / `md5ext`, exactly like the reference project.
* A missing file produces a `line:col` diagnostic and the build continues.
* `jswc build file.jsw --project-json` cannot embed binaries; use `.sb3`.

In TurboWarp, some additional tools require imports, currently, importing those methods has following options:
1. From extensions.turbowarp.org
2. The extension JavaScript file


How jswc handles each:

- **Built-in extensions** (`pen`, `music`) — the extension id is added to
  `project.extensions` automatically as soon as a mapped opcode is emitted, so
  `Pen.eraseAll()` or `Music.playNote(60, 0.5)` need no import statement.
- **TurboWarp extensions** (`utilities`, `lmsTempVars2`, `skyhigh173JSON`,
  `lmsTimers`, `lmscomments`, `sipcconsole`, `runtimeoptions`) — added
  automatically when a mapped call needs them; the resulting `.sb3` then
  requires TurboWarp rather than vanilla Scratch.
- **Unmapped namespaces** (`JSON.*`, `Timer.*`, `console.*`, `Util.*`,
  `Runtime.*`, …) — the compiler records a diagnostic instead of guessing an
  opcode. Add an entry to `jswc/opcodes.py` to map them; see
  [Extending the compiler](#extending-the-compiler).
- **User sprites** — a `public void Ns.Name()` in another class is reachable
  from anywhere through its broadcast, so "importing" a cross-sprite method is
  just calling it.

---

# JadenScript language reference (v0.1)

JadenScript (`.jsw`) is a small, JavaScript-looking language whose only target
is a Scratch 3 / TurboWarp project. It is deliberately *not* JavaScript: every
construct has a direct Scratch block lowering, and anything without one is a
compile error rather than a silent change of meaning.

## 1. Source files

| property | value |
| --- | --- |
| extension | `.jsw` |
| encoding | UTF-8 |
| comments | `// line`, `/* block */` |
| statement terminator | `;` **or** newline (automatic) |
| line continuation | trailing `\` |
| case sensitivity | yes |

Newlines are ignored inside `(` … `)` and `[` … `]`, which makes wrapped call
arguments legal:

```jsw
Pen.setPenColor(
    (Piano.activergb.get(1) % 16777216)
    if (Player.NotesActive.get(3) > 0) else
    (4210752)
);
```

## 2. Vocabulary

Defined once in [`jswc/keywords.py`](./jswc/keywords.py) and imported by both
the compiler and the TextMate grammar generator, so the editor can never
highlight a different set from the one the parser accepts.

**Keywords**
`class` `stage` `loose` `metadata` `void` `public` `private` `if` `elif` `else`
`for` `while` `repeat` `switch` `case` `default` `break` `continue` `return`
`call` `import` `include` `as` `true` `false` `null` `any` `and` `or` `not`

`import` names an asset, `include` splices a source file in (§"Modules"), and
`as` is the alias word both of them use.

**Reserved for later** (parsed as keywords, rejected where unsupported)
`extends` `static` `final` `const` `let` `var` `new` `this` `typeof`
`instanceof` `delete` `try` `catch` `finally` `throw` `yield` `await` `async`
`do` `in` `of` `enum` `struct` `typedef` `export`

**Literal forms**

| form | example | note |
| --- | --- | --- |
| decimal | `1`, `1.035`, `1e3` | kept as text; Scratch numbers are strings |
| hex | `0xFF` | kept as written |
| string | `"a\x20b"` | `\n \r \t \0 \\ \" \' \xNN \uNNNN` |
| key literal | `'space'`, `'c'` | same as a string; used by `case` |
| boolean / null | `true`, `false`, `null` | lowered to `"1"`, `"0"`, `""` |
| key menu | `any` | the "any key" dropdown choice; always the text `"any"` |

**`any`** is the *any key* menu option, so it is a keyword rather than an
identifier. It compiles to the text `"any"` everywhere, and gives `W008` when
used outside a call's argument list — `Sensing.keyPressed(any)` is what it is
for, `int = any;` is almost certainly a typo:

```
if (Sensing.keyPressed(any)) { … }   // fine, no warning
marker = any;                        // compiles to "any", warns (W008)
message = "any";                     // say it with quotes when you mean text
```

## 2.1 Target metadata, without comments

A Scratch target carries properties that no block can set: where its scripts
sit on the canvas, which variables have monitors open, and where its costume
origin is. Those used to be *comments* (`// Sprite:`, `// at`, `// monitor`).
They are real syntax now; the comments are still accepted, so older sources
keep working.

```jsw
main: name="main", x=0, y=0, size=100, direction=90, show=1, costume=0,
      volume=100, layer=1, rotation="all", draggable=0;

stage: costume=0, volume=100, layer=0, tempo=60, video="on", videoTransparency=50;

class main { … }

stage { … }
```

`name=` is what the sprite is actually called in the project, so a sprite called
`obj loader` is `obj_loader: name="obj loader", …;` — the class name stays a
valid identifier.

A function written **outside** any class belongs to the Stage (the backdrop
target), so a one-file utility does not need a wrapper:

```jsw
public void hello() { // hook: green flag
    Looks.say("hi");
}   // lives on the Stage, exactly as if it were inside ``stage { … }``
```

### Modules: `include "file.jsw"`

A `class` is a sprite, so a project with three sprites needs three classes. An
include keeps each of them in its own file: the line is replaced by the contents
of the named file, resolved relative to the file that mentioned it.

```jsw
// rv64.jsw -- the board: shared state and nothing else
import "assets/lists/X.image.jsa";

include "display.jsw"
include "net.jsw"
include "core.jsw"
```

```jsw
// display.jsw -- one module, one class, one sprite
class Display {
    public void displayStart() { // hook: green flag
        …
    }
}
```

| rule | why |
| --- | --- |
| `include` is a keyword, not a comment | a disabled include and a live one have to look different; the line is text substitution, so nothing else may share it |
| one quoted path, then optionally a `//` comment | the path is inside the quotes, so a comment can never be part of it |
| paths are relative to the *including* file | a module can live beside the one that pulls it in |
| nesting is allowed, cycles are an error | `a.jsw -> b.jsw -> a.jsw` is reported with the chain, not a hang |
| a diagnostic names the module and its own line | the compiler keeps a line map, so an error in `core.jsw:120` never points at the joined text |

Included text is spliced in order, so declarations used by a later module should
be included before it — or, as the machine here does, put the shared state in the
including file, above the includes.

### `metadata` statements

```jsw
metadata <script> pos <x>,<y>;                        // where the script sits
metadata <variable> at <x>,<y> <W>x<H> <shown|hidden>;          // a read-out
metadata <variable> <mode> at <x>,<y> <W>x<H> <shown|hidden> [range a..b [discrete]];
metadata <mode> <variable> at <x>,<y> <W>x<H> <shown|hidden>;   // same thing
metadata watch <target>.<opcode> id="…" mode=… at …;
metadata note <target> at <x>,<y> <W>x<H> [minimized]: "text";
```

| piece | meaning |
| --- | --- |
| `<script>` | the method name as written: `greenFlag`, `Stage.startFrameCounter`, `Events.keyPressed` |
| `<variable>` | `Owner.name`: `Stage.Score`, `main.lives`, `"obj loader".score` |
| `<mode>` | `default` · `large` · `slider` · `list` — see below |
| `<W>x<H>` | the monitor box, e.g. `100x24` |

**Monitor modes.** Omitting the mode is the same as writing `default`: an
ordinary read-out. The other modes are Scratch's own, with the spellings a
reader is likely to try:

| written | Scratch `mode` | notes |
| --- | --- | --- |
| *(nothing)* · `default` · `normal` | `default` | a plain read-out box |
| `large` · `largeReadout` | `large` | the big number, for a value someone is watching |
| `slider` | `slider` | `0..100`, stepping by 1 — Scratch's default range |
| `slider(a,b)` | `slider` | the range in parentheses, in either position |
| `list` | `list` | the list's contents |

`slider(a,b)` also accepts whole-number bounds as steps: `slider(0,2)` is a
three-state switch, `slider(0.2,4)` is a rate. The explicit `range a..b
[discrete]` spelling still works and is what the decompiler writes, so a
`metadata slider(0,2) …` line comes back as `metadata slider … range 0..2
discrete` after a round trip.

`metadata` may appear at the top level or inside a `class` / `stage` body; the
target is named explicitly, so its position in the file does not matter.
A `stage: …;` line only carries the Stage's own properties — it does not open a
block.

## 3. Names and scope\n## 3. Names and scope

| written as | meaning | lowered to |
| --- | --- | --- |
| `Player.notes` | Stage variable named `"Player.notes"` | `data_variable` / `data_setvariableto` |
| `Piano.notes` used with list methods | Stage list named `"Piano.notes"` | `data_*oflist` |
| `x` (bare) | local | sprite-local variable, or `lmsTempVars2` thread variable with `--locals=thread` |
| parameter name | custom block argument | `argument_reporter_string_number` |
| `Motion.x`, `Sound.volume`, … | Scratch property | `motion_setx`, `sound_setvolumeto`, … |

**Aliases.** A few blocks have a second, friendlier spelling. Both compile to
the same opcode, so a project may use either:

| alias | same block | opcode |
| --- | --- | --- |
| `Music.stopAll()` / `Music.stopAllSounds()` | `Sound.stopAllSounds()` | `sound_stopallsounds` |
| `Control.clone(x)` | `Control.createClone(x)` | `control_create_clone_of` |
| `Sensing.isKeyPressed(x)` | `Sensing.keyPressed(x)` | `sensing_keypressed` |

The decompiler always writes the canonical name, so round-tripped source uses
one spelling throughout.

### Asking: `Sensing.ask(q)` is a value too

Scratch's ask block is a *statement* that waits, and the answer is a separate
reporter. In JadenScript the statement still works, and the same call used as a
value asks first and answers with what was typed:

```jsw
Sensing.ask("Name?");           // the statement: the answer is read later
name = Sensing.answer;

age = Sensing.ask("Age?");      // the value form: asks, then assigns
if (age == "42") { … }
```

The value form compiles to `ask and wait` followed by a `set` of a temporary
from `answer`, and the expression becomes a read of that temporary — so two
questions in one statement do not read each other's answer:

```jsw
Looks.say(join(Sensing.ask("one"), Sensing.ask("two")));
```

The temporary is called `_ask1`, `_ask2`, … and lives in the sprite, like the
`_t1` temporaries a conditional in an argument needs. A bare
`Sensing.ask("…");` line emits the block on its own, with no temporary.

The question is asked **once per statement**, before that statement runs — so a
`Sensing.ask` in a `while` condition asks before the loop starts and reads the
same answer every time round. Ask inside the loop body instead.

### Keys: `Sensing.keyPressed(x)` / `Sensing.isKeyPressed(x)`

A **string** is the key itself, exactly like the Scratch menu:

```jsw
if (Sensing.isKeyPressed("a"))      { … }
if (Sensing.isKeyPressed("space"))  { … }
if (Sensing.isKeyPressed("left arrow")) { … }
if (Sensing.keyPressed(any))        { … }   // any key at all
```

A **number** is the character's code point (decimal, `0x`, `0b` or `0o`). The
compiler resolves it to a key name while compiling, so the project still works
in plain Scratch:

| number | key | note |
| --- | --- | --- |
| `65`, `0x41`, `0b1000001` | `A` | letters are upper-cased, like the menu |
| `97` | `A` | lower case means the same key |
| `32` | `space` | |
| `13` / `10` | `enter` | |
| `8` / `127` | `backspace` / `delete` | |
| `27` | `escape` | |
| `33`–`126` | that character | `;` `=` `,` `-` `.` `/` `[` `\` `]` `` ` `` `'` |
| **`128`–`140`** | the keys with no character | see below |
| `160`+ | that Unicode character | `233` → `É`, `0x0416` → `Ж` |

Codes 128–159 are the C1 control range, which holds no printable character, so
the compiler uses it for **TurboWarp's extra keys**:

| 128 | 129 | 130 | 131 | 132 | 133 | 134 |
| --- | --- | --- | --- | --- | --- | --- |
| left arrow | up arrow | right arrow | down arrow | shift | control | caps lock |

| 135 | 136 | 137 | 138 | 139 | 140 |
| --- | --- | --- | --- | --- | --- |
| scroll lock | insert | home | end | page up | page down |

Every one of those is a name TurboWarp's VM reports (`KEY_NAME` in
`scratch-vm/src/io/keyboard.js`); they are reachable by name too, e.g.
`Sensing.isKeyPressed("page up")`.

A key that the VM cannot report warns with `W009` instead of compiling to a
test that never fires: `"f1"` (function keys are not tracked and `"f1"` would
match the letter F), `"tab"`, `"alt"`, `"num lock"`, `"pause"`, an unused C1
code, or a code point that is not printable.

The same numbers work in the hat comment: `// hook: when [65] key pressed`.

A dotted name is a global, a bare name is a local. That single rule is what
makes the language interoperable with existing projects: variable names such as
`Player.maxnotes` survive a round trip unchanged.

Parameters are immutable inside the body (Scratch has no "set argument" block).

### Locals

`--locals=sprite` (default) and `--locals=thread` decide where bare names live.

| mode | read | write | trade-off |
| --- | --- | --- | --- |
| `sprite` | `data_variable` | `data_setvariableto` | works in vanilla Scratch, but clones share the slot |
| `thread` | `lmsTempVars2_getThreadVariable` | `lmsTempVars2_setThreadVariable` | matches the reference project and is re-entrant; needs TurboWarp |

Loop counters are always real variables, because `control_for_each` cannot bind
a thread-local.

## 4. Declarations

```jsw
class player { // Sprite: player (-196,121) size=100 direction=90 show=1
    public void Player.tick(){ … }
    private void helper(value){ … }
}
```

* One `class` → one sprite. The Stage is generated automatically.
* The trailing `// Sprite:` comment supplies the sprite's position, size,
  direction and visibility (`show=0` → hidden).
* Function names may be dotted. The part before the last dot is the *namespace*
  and only matters for cross-sprite calls and broadcast names.
* `procedures_definition` uses only the last segment as its `proccode`, because
  custom blocks are scoped to a sprite in Scratch.

## 5. Statements

```jsw
public Player.score = 0;      // declare/assign a global
Player.score += 1;            // change by
private tmp = 2;              // local
tmp *= 3;                     // read-modify-write
Player.score++;               // increment
if (c) { … } elif (c2) { … } else { … }
while (1) { … }
while (c) { … }
for i in 1..n { … }            // inclusive counter (see below)
for (i = 1, i <= n, i++) { … } // the same thing, three-part spelling
for (i = 0; i < n; i++) { … }  // exclusive: `..` cannot express this
switch (k) { case 1: … case _: … }  // or: switch k { case 1 { … } case _ { … } }
break; continue; return;
call Player.timerCheck();     // asynchronous (broadcast)
Player.timerCheck();          // synchronous (procedures_call)
0                             // explicit no-op
log(x); warn(x); error(x);    // TurboWarp console (Logs tab)
startTimer(x); stopTimer(x);
breakpoint;                   // console marker line
```

A `switch` lowers to a chain of `if / else if / else`, so it costs one
comparison per case. Semantics differ from C in one place: **cases never fall
through**, so no `break` is required — write each body once. `case _` (or
`default`) is the `else` branch.

```jsw
switch op_1 {           // the parentheses are optional
    case 0 { Looks.say(0); }
    case 1 { Looks.say(1); }
    case _ { Looks.say(3); }
}
switch (op_1) {         // the same switch, colon spelling
    case 0: Looks.say(0);
    case 1: Looks.say(1);
    default: Looks.say(3);
}
```

`continue` has no Scratch equivalent and is reported as a diagnostic.

`return;` lowers to `stop this script`. Inside a custom block that is a real
return: `Thread.stopThisScript` pops the stack back to the `procedures_call`
and resumes after it, so the caller keeps running. It is the cheap way to leave
a procedure early — used by `anim3d`'s `frame()` to skip a redraw.

```jsw
private void frame() { // warp
    if (Tick.tick == lastTick) { return; }   // costs one `stop this script`
    …
}
```

`break;` is the same block, so it is the same return — **not** a loop break,
which Scratch does not have. Inside a loop it leaves the whole function, which
is almost never what a loop body means, so it is reported as `W0025`. Keep a
flag in the loop condition instead:

```jsw
_i = 1;
_go = 1;
while (_i <= n && _go == 1) {   // `&&` and the flag are the only loop exit
    if (done(_i)) { _go = 0; } else { work(_i); }
    _i += 1;
}
```

### The range counter: `for i in a..b`

`..` is an **inclusive ascending** range, so `for i in 1..10` counts 1, 2, …, 10.
Both bounds may be any expression, and the range may be bracketed freely:

```jsw
for i in 1..10 { … }                 // both bounds literal
for i in (1..10) { … }               // the same
for (i in 1..10) { … }               // the same
for i in a..b { … }                  // variables
for i in (a+1)..(b+1) { … }          // expressions
for i in ((a+1)..(b+1)) { … }        // the same
for Synth.counter in 1..n { … }      // a public stage variable
```

**Lowering.** Starting at a literal `1` with a real (non-temp) counter becomes
Scratch's own counter block, `repeat n` with the index bound to the variable —
fewer blocks and exactly what a person would build by hand. Anything else
becomes an explicit while loop:

```jsw
for i in 0..3 { body }            // ⇒
i = 0;                            //    set i to 0
while (i <= 3) { body; i += 1; }  //    repeat until (i > 3) { body; i += 1 }
```

Note this is a `control_repeat_until` whose condition is the *exit* test, so it
prints as `while (i <= 3)` when decompiled. `..` is never emitted for an
exclusive loop: `for (i=0; i<5; i++)` keeps its three-part spelling, because
`0..4` would mean something different for a non-integer counter.

**Which variable is the counter.** The name decides, using the usual rules:

| counter | meaning |
| --- | --- |
| `i` | an existing `public i` (a stage variable, reset to the start value) or a sprite-local |
| `Synth.counter` | always the stage variable `Synth.counter` |
| `_i` | a temp (thread) variable when `#enable_feature<useTempVariables>`; it never becomes sprite state and disappears with the script |

Re-using a temp counter while its loop is still running is a **compile
error** — the inner loop would overwrite the outer counter:

```jsw
for _i in 0..3 { for _i in 0..2 { … } }   // E0074: temp loop variable already live
```

Two loops one after the other may share `_i`; the first has finished by then.

**Writing the counter inside the body** is almost always a mistake, so it is a
warning (`W0021`):

```jsw
for i in 0..3 {
    i = 99;          // W0021: the loop counter 'i' is assigned inside its own loop
}
```

**Decompiling.** A `set` immediately followed by a `while` whose condition
compares that variable and whose last body block is `change … by 1` comes back
as the range form, with the start value taken from the `set`:

```jsw
i = 2;                     // ⇒ for i in 2..5 { … }
while (i <= 5) { …; i += 1; }
```

The shape must match exactly: the `set` directly before the loop, the compare on
the counter itself, and the `change by 1` as the last block. A temp counter is
recognised the same way through `lmsTempVars2_setThreadVariable` /
`getThreadVariable` / `changeThreadVariable`.

Expression bounds are parenthesised on the way out so the result re-parses:
`for i in 0..(n + 1)`.

### Walking a list: `for v in <list>` and `for l in a, b`

Two forms, and the thing after `in` decides which:

```jsw
for note in notes { total += note; }        // the loop variable takes each ITEM
for l in lu, ll, lr { l.clear(); }          // the body runs once per LIST
```

**Items.** `for v in <list>` reads `item (i) of <list>` for every index, so `v`
is a value. There is no for-each block in Scratch, so it lowers to an index:

```jsw
for v in notes { body }      // ⇒
_forItem1 = 1;               //    set _forItem1 to 1
repeat (length of notes) {   //    repeat (length of notes)
    v = notes.get(_forItem1); //      set v to (item _forItem1 of notes)
    body                     //      body
    _forItem1 += 1;          //      change _forItem1 by 1
}
```

The count is read once, before the loop, so a body that grows or shrinks the
list cannot make the loop run forever. It *can* still make it skip items —
deleting inside the body shifts the rest of the list under the index — so that
is `W0033`:

```jsw
for v in notes { notes.delete(1); }   // W0033: the index keeps going, the items move
```

`_forItem1` is generated, one per loop (so nested loops are fine), and follows
the usual `_`-prefix rule: a temp (thread) variable with `useTempVariables`, a
sprite variable otherwise.

**Lists.** `for l in a, b, c` binds the loop variable to each **list** in turn,
which is what makes `l.length()`, `l.delete(1)` and `l.append(x)` act on the
list itself. Scratch cannot hold a list in a variable, so there is nothing to
loop over: the body is **unrolled**, one copy per name with the loop variable
replaced by that name.

```jsw
for l in lu, ll { l.clear(); }   // ⇒  delete all of lu
                                  //     delete all of ll
```

Consequences worth knowing, because they are the cost of unrolling: the body is
duplicated in the project (three lists, three copies), and reading the loop
variable *after* the loop has nothing behind it — there is no variable, only
the copies.

**Which names count as lists.** A name is a list when the project declared it
(`private notes = [];`, `public notes = [];`) or already used it with a list
method. Anything else is `E0083`:

```jsw
for v in 3 { … }             // E0083: '3' is not a declared list …
for v in missing { … }       // E0083: … write `for i in 1..n` to count
```

**Decompiling.** These lower to ordinary `set`/`repeat`/`item of` blocks, so
they come back as the block shapes they are, not as `for … in <list>`. The
counting form above is the one the decompiler recognises back.

## 6. Expressions

Precedence, lowest to highest:

```
a if c else b        ternary (postfix, Python style)
||  or
&&  and
==  !=
<   <=   >   >=
+   -
*   /   %
**                   right associative
!  -  +  not         unary
.  ()  []            postfix
```

Postfix chains are general: `Player.Notes.get(i).get(3)` parses as a call on the
result of a call. Lowering of the second `.get` (a JSON array access) is not
implemented yet and produces a diagnostic.

### Built-in namespaces

`Pen`, `Music`, `Sound`, `Motion`, `Looks`, `Control`, `Sensing`,
`Variables`, `Lists`, `Operators`, `Events`, `JSON`, `console`, `Timer`,
`Util`, `Runtime`, `Clone`.

Only the mappings listed in [`jswc/opcodes.py`](./jswc/opcodes.py) are
implemented; the table is verified against `scratch-vm/src/blocks` and the pen
and music extensions.

There is no `c ? a : b`. `?` is not a JadenScript operator: write the ternary
the postfix way (`x = (1) if (c) else (2);`, above). A `?` in an expression is
`E0087` with that hint, and the grammar flags it as illegal rather than
pretending a C-shaped ternary exists.

### State by name: `Variables`, `Lists`, `Clone`, `Stage`

A variable that is only known at run time can still be reached, because these
four namespaces lower to the ordinary blocks. The target is named **first** and
may be bare or quoted:

```jsw
Lists.add(items, 1);              // items.add(1)
items[1] = 9;                     // Lists.set(items, 1, 9)
x = items[2];                     // Lists.get(items, 2), counting from 1
items[2] += 5;                    // read, add, replace
Lists.insert(items, 1, 0);        // insert at index 1
Lists.set(items, 2, 9);           // replace index 2
Lists.delete(items, 3);
Lists.clear(items);
x = Lists.get(items, 1);
n = Lists.length(items);
b = Lists.contains(items, 7);
i = Lists.indexOf(items, 7);
Lists.showList(items); Lists.hideList(items);

Clone.create();                   // clone of this sprite
Clone.create("piano");            // clone of another sprite
Clone.delete();                   // delete this clone

Variables.showVariable(score);    // show the monitor
Variables.hideVariable(score);
```

`Variables` is only the monitor pair: a variable is written `score = 5` in
JadenScript, so a `Variables.set(...)` spelling would just be a second way to
say the same thing. Those still compile for older sources —
`Variables.set/get/change/show/hide` and the list spellings `show`, `hide`,
`append`, `push`, `replace`, `remove` are accepted as aliases — but the popup
offers only the canonical ones, so a variable is never mistaken for a list.

`items[i]` is the same block as `items.get(i)` and counts from **1**, like the
`item of list` block. It works for reading (`x = items[2]`) and for writing
(`items[2] = 9`, `items[2] += 5`); the decompiler keeps writing `.get(...)`, so
a rebuild never changes a shape it does not have to.

`Stage.` is a **prefix**, not a namespace object: `Stage.Lyrics.zh` reads the
stage variable `Lyrics.zh`, which is the same spelling the monitor annotations
use. The prefix is only dropped when the shorter name really exists, so a
project with a variable literally called `Stage.x` round-trips unchanged.
`Stage.get(...)` / `Stage.set(...)` / `Stage.change(...)` are the call spellings.

### micro:bit

TurboWarp's micro:bit bridge, with the namespace spelled `microbit`. Every menu
value can be written as a constant, so a call reads as code:

```jsw
microbit.display("0101010101100010101000100");   // 5x5 LEDs, 25 cells
microbit.display(0b111);                          // the same picture as a number
microbit.displayText("hi");
microbit.clearDisplay();

if (microbit.isButtonPressed(microbit.buttonA)) { … }
if (microbit.isButtonPressed("A")) { … }          // the same
if (microbit.isButtonPressed(any)) { … }          // any button
if (microbit.isTilted(microbit.front)) { … }
Looks.say(microbit.tiltAngle(microbit.right));
```

| constant | value | where |
| --- | --- | --- |
| `microbit.buttonA` / `microbit.buttonB` | `"A"` / `"B"` | buttons |
| `microbit.moved` / `shaken` / `jumped` | the gesture | gestures |
| `microbit.front` / `back` / `left` / `right` | the tilt direction | tilt |
| `microbit.pin0` / `pin1` / `pin2` | `"0"` / `"1"` / `"2"` | pin hats |

`any` is the language keyword (or `"any"`), not `microbit.any`: only `A` and
`B` have constants, and `any` is not a direction the tilt sensor reports.

**Display matrix.** The 5x5 display takes 25 characters of `0` and `1` — row by
row, top left first. A number is the same picture: `0b111` is
`0000000000000000000000111`. A string that is not 25 binary cells still
compiles but warns (`W0022`), because the board would silently show something
else.

**Hats.** Each micro:bit trigger is a `public void` whose `// hook:` comment
carries the menu value:

```jsw
public void microbit.buttonPressed() { // hook: when [A] button pressed
    …
}

public void microbit.gesture() { // hook: when [shaken]
    …
}

public void microbit.tilted() { // hook: when tilted [front]
    …
}

public void microbit.pinConnected() { // hook: when pin [0] connected
    …
}
```

The method name is a label; the value lives in the comment, exactly like
`Events.keyPressed(k)`.

### Runtime variables become real variables

`Temp.setRuntimeVariable` and friends store their state in the *extension*, out
of reach of monitors, the variable list and the green flag. The compiler lowers
them to ordinary Stage variables instead:

| written | compiled to |
| --- | --- |
| `Temp.setRuntimeVariable("_tps", v)` | `_tps = v` (a Stage variable, initial `0`) |
| `Temp.changeRuntimeVariable("_tps", 1)` | `_tps += 1` |
| `Temp.getRuntimeVariable("_tps")` | reading `_tps` |

Every converted name is declared with the initial value **0**, and one Stage
script resets them all:

```jsw
// generated — not written by hand
public void greenFlag() {   // when green flag clicked
    _CreateNoteGroup_0 = 0;
    _Player_atps = 0;
    …
}
```

Two limits, both deliberate:

- A **computed** name (`Temp.setRuntimeVariable(name, 1)`) cannot become a
  variable, so that call keeps using the extension.
- A project that also calls `Temp.deleteRuntimeVariable`,
  `runtimeVariableExists` or `listRuntimeVariables` keeps **all** of its
  `Temp.*RuntimeVariable` calls on the extension — deletion and existence only
  make sense against the extension's own store, so a half-converted project
  would be wrong in a way that is hard to see.

Thread variables (`Temp.setThreadVariable`, `forEachThreadVariable`, …) are
never converted: they are per-script state, not globals.

A converted name is a plain **global**, so it is declared outside every class
and always referenced bare — `_CreateNoteGroup_0`, never
`NpBuild._CreateNoteGroup_0`. A `namespace=` on the sprite does not apply to
it, which keeps the variable table and every block field spelled the same way.

Global helper functions: `string(x)`, `sleep(milliseconds)`, `lengthOf(x)`,
`len(x)`, `length(x)`, `join(a, b, …)`, `float(x)`.

`lengthOf` is the `length of []` block: the value is turned into text first, so
`lengthOf(123)` is 3 and `lengthOf(myList[1])` counts the characters of that
item. `len` and `length` are the same block under older names.

## 7. Directives

Directives are ordinary comments with a known prefix, so the files stay valid
JadenScript if the compiler ignores them.

| directive | effect |
| --- | --- |
| `// Sprite: name (x,y) size=… direction=… show=… costume=… volume=… layer=… rotation=… draggable=…` | sprite metadata for the class |
| `// Stage: costume=… volume=… layer=… tempo=… video=… videoTransparency=…` | stage metadata for the `stage { }` block |
| `// hook: when [key] key pressed` | turns the function into an `event_whenkeypressed` hat |
| `// warp` or `// main thread` | sets `warp: "true"` on the custom block |
| `// no-warp` | keeps that one block's frames even under `--warp` |
| `// green flag` | turns the function into `event_whenflagclicked` |
| `// proccode <label>` | the exact custom-block label, used when it cannot be spelled as a function name |
| `// at x,y` | canvas position of the script / loose stack |
| `// monitor …`, `// watch …` | a stage monitor (see "Canvas, monitor and layer metadata") |
| `// comment <target> at x,y WxH: "text"` | a workspace comment (the notes authors leave around) |
| `// undefined` | the project calls this custom block but never defines it |
| `// costume centerx=… centery=… res=…` | costume anchor and bitmap resolution |
| `// sound rate=… samples=…` | sound playback metadata |

`void whenCloned()` is recognised by name (`control_start_as_clone`), and
`void greenFlag()` needs no directive.

## 8. Lowering table

| JadenScript | Scratch |
| --- | --- |
| `class X` | target `X` |
| `void greenFlag()` | `event_whenflagclicked` |
| `void whenCloned()` | `control_start_as_clone` |
| `public void Ns.F()` | `procedures_definition` + `event_whenbroadcastreceived "Ns.F"` |
| `Ns.F()` same sprite | `procedures_call` |
| `Ns.F()` other sprite | `event_broadcastandwait "Ns.F"` |
| `call Ns.F()` | `event_broadcast "Ns.F"` |
| `x = e` (bare) | `data_setvariableto` / `lmsTempVars2_setThreadVariable` |
| `x += e` | `data_changevariableby` |
| `x -= e` | `data_changevariableby (0 - e)` |
| `x *= e` | `data_setvariableto (x * e)` |
| `if / elif / else` | nested `control_if_else` |
| `while (1)` | `control_forever` |
| `while (c)` | `control_while (c)` |
| `while (!c)` | `control_repeat_until (c)` |
| `for (i=1, i<=n, i++)` | `control_for_each` |
| other `for` | `control_repeat_until (not c)` + explicit increment |
| `menu("note", "NOTE", 66)` | a bare dropdown (shadow) block |
| `switch` | `control_if_else` chain |
| `return` / `break` | `control_stop "this script"` |
| `a if c else b` | `utilities_ternaryOperator` |
| `**` | `utilities_exponent` |
| `string(x)` | `operator_join (x, "")` |
| `!a` | `operator_not` |
| `a != b` | `operator_not (a == b)` |
| `a <= b` | `operator_not (a > b)` |
| `Sensing.distanceTo("mouse-pointer")` | `sensing_distanceto` |
| `Sensing.touchingColor("#ff0000")` | `sensing_touchingcolor` |
| `Sensing.of("x position", "Stage")` | `sensing_of` |
| `Looks.setEffect("ghost", 50)` | `looks_seteffectto` |
| `Looks.clearGraphicEffects()` | `looks_cleargraphiceffects` |
| `Looks.goToFront()` / `Looks.goToBack()` | `looks_gotofrontback` |
| `Looks.switchBackdrop("name")` | `looks_switchbackdropto` |
| `Control.stopOtherScripts()` | `control_stop "other scripts in sprite"` |

## 9. Known limits

* Custom blocks cannot return values; a function used as a value is a
  diagnostic.
* `continue`, breaking out of a nested loop, and exceptions have no Scratch
  equivalent.
* Arrays/JSON values (`x.get(1).get(2)`) need extension mappings that are not
  written yet.
* Statements at class level (outside a function) are rejected.
* Recursion works only as far as Scratch's stack allows; sprite-local variables
  are shared between clones in `--locals=sprite` mode.

## 10. Extending the compiler

1. Add the opcode to `COMMANDS` / `REPORTERS` / `PROPERTIES` in
   `jswc/opcodes.py`, including input names and primitive ids.
2. Confirm the shape with
   `python3 tools/opcode_report.py <path>/project.json` on a project that uses
   the block, or by reading `scratch-vm/src/blocks|extensions`.
3. Add a unit test in `tests/test_codegen.py` that asserts the opcode appears
   and that the project still validates.

## 11. Conditional compilation (v1.1)

One source, more than one build. `#ifdef` / `#ifndef` / `#else` / `#endif` are
resolved *before* lexing, so the compiler never sees the hidden lines and the
two builds are ordinary programs:

```jsw
#ifdef TURBOWARP
    Looks.switchCostume("Frame");        // measure the stage the hard way
#else
    _w = 480;                            // Scratch's stage is always this
    _h = 360;
#endif
```

The rules are the C preprocessor's, cut down to the part that pays for itself:

| directive | meaning |
| --- | --- |
| `#define NAME` | NAME counts as defined from here on |
| `#ifdef NAME` … `#endif` | keep the block only when NAME is defined |
| `#ifndef NAME` … `#endif` | keep the block only when NAME is not defined |
| `#else` | the other half of the nearest open block |

Nesting is unlimited, `#define` inside a block that was dropped has no effect,
and an `#ifdef` without `#endif` is an error rather than a silently truncated
program. A dropped line occupies no line number, because the origin of every
surviving line is carried with it: diagnostics still point at the file and line
the author wrote.

Two names are defined for you:

```bash
jswc build machine.jsw -o machine.sb3 --target turbowarp   # TURBOWARP
jswc build machine.jsw -o machine.sb3 --target scratch     # SCRATCH
jswc build machine.jsw -o out.sb3 -D DEBUG -D TRACE        # anything else
```

`--target turbowarp` is the build that may use extensions; `--target scratch`
is the one that has to run on scratch.mit.edu, where only built-in blocks
exist. Building with no `--target` defines neither name, which is the "neither
build in particular" case the tests and the offline simulators want. Use
`#ifdef SCRATCH` when the *Scratch* side is the one that has to differ — as
above, where 480 x 360 is a constant and measuring it is wasted frames.

`--target` is shorthand and nothing more: it is exactly `-D TURBOWARP` or
`-D SCRATCH` and has no other effect, so a build is reproducible from the
defines alone and `check` can be asked the same question:

```bash
jswc check machine.jsw --target turbowarp   # what the TurboWarp build sees
jswc check machine.jsw                      # neither: every guarded line gone
```

Without the flags `check` hides every guarded branch, which is why a line that
only exists under `#ifdef TURBOWARP` looks fine until you build the build it
belongs to.

### A define flows into what it includes, and no further

`#define` is not scoped to a file: a define in the including file is visible to
every module it includes, which is what makes one switch enough.

```jsw
// machine.jsw -- the switch and the modules it governs
#define WANT_SPEAKER
include "speaker.jsw"          // the whole class is inside #ifdef WANT_SPEAKER
include "core.jsw"
```

```jsw
// speaker.jsw
#ifdef WANT_SPEAKER
class Speaker { public void speakerInit() { … } }
#endif
```

That is also why a dropped block's `#define` does nothing: the preprocessor
walks the text once, top to bottom, and a line it never keeps never runs. When a
flag needs to be shared but not in the including file, put it on the command
line — `-D` is the same set, so `#define` in a source and `-D` on the command
line are interchangeable.

Nest freely; the branches read top to bottom and only the enclosing `live`
matters:

```jsw
#ifdef TURBOWARP
    #ifdef TRACE
        console.log("frame");            // TurboWarp build, tracing on
    #else
        _trace = 0;                      // TurboWarp build, tracing off
    #endif
#else
    _trace = 0;                          // Scratch build: no console at all
#endif
```

`-D TRACE --target turbowarp` takes the first branch, `--target turbowarp` the
second, `--target scratch` the third. A stray `#else` or `#endif` is reported
against its own line, and an unbalanced `#ifdef` is `#ifdef without #endif`, not
a silently truncated program.

## 12. Command line

`jswc` is the only entry point; the full tour lives in
[`docs/CLI.md`](./docs/CLI.md). The short version:

```bash
jswc build   project.jsw -o build/project.sb3     # ← the usual command
jswc build   project.jsw --target scratch         # or turbowarp, see §11
jswc decompile project.sb3 -o project.jsw         # recover source (+ assets/)
jswc check   project.jsw --strict                 # parse only, fail on diagnostics
jswc ast     project.jsw [--json]                 # class / method tree
jswc tokens  project.jsw                          # line:col, kind, value
jswc codes   [--severity warning]                 # Exxx / Wxxx registry
jswc features                                     # --enable flags
```

Output is coloured on a TTY and plain when redirected; `--no-color`, `--plain`,
`NO_COLOR=1` and `TERM=dumb` force plain text, `FORCE_COLOR=1` forces colour.
Every diagnostic carries a stable code and, with `--explain`, a hint that
names the fix:

```
  ✖ E0011  project.jsw:1:12  expected '{', found 'extends'
    │ class main extends Sprite {
    │            ^
```

Codes are a flat list: `E0001`, `E0002`, … for errors, `W0001`, `W0002`, … for
warnings. The number means nothing on its own — it is an identifier to quote in
a bug report, and new problems take the next free one so an existing code never
changes. `jswc codes` lists all of them, `jswc codes -s metadata` searches
titles and hints, and [`docs/CLI.md`](./docs/CLI.md) has the full table.
Exit codes: `0` ok, `1` build failed, `2` source error,
`130` interrupted.
