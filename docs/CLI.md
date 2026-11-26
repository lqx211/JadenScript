# `jswc` — command line tour

Everything you can do with JadenScript goes through one entry point:

```bash
python3 -m jswc <command> [options]     # from a checkout, no install
jswc <command> [options]                # after ./install.sh (or pip install -e .)
```

`./install.sh` (or `make install`) is the supported way to get the command:
a private venv plus a launcher in `~/.local/bin`, no sudo, no writes into the
system Python. `./install.sh --check` describes what it would do without
changing anything, and `./install.sh --uninstall` removes it.

Run `jswc` with no arguments to print the help screen, `jswc --version` for the
build number, and `jswc <command> -h` for the options of a single command.

```
usage: jswc [-h] [--version] [--no-color] [--plain] <command> ...

commands:
  build      compile a .jsw file into a .sb3 project
  decompile  turn an .sb3 back into .jsw
  check      parse a file and print diagnostics
  ast        dump the parsed AST
  tokens     dump the token stream
  codes      list diagnostic codes (Exxx / Wxxx)
  features   list language feature flags
```

---

## 1. Compile

```bash
jswc build examples/hello.jsw -o build/hello.sb3
```

```
◎ Compiling hello.jsw
✔ Compiled build/hello.sb3
  blocks 2 · sprites 1 · classes 1 · time 4 ms
```

A project that needs extensions lists them comma-separated on the next line:

```
  extensions pen, sipcconsole
```

The summary always reports **blocks**, **sprites**, **classes** and wall time,
the next line lists the **extensions** the project needs, and any diagnostic is
printed before the ✔ line (warnings can be fixed without stopping the build).

Useful flags:

| flag | effect |
| --- | --- |
| `-o/--output PATH` | where to write (default: next to the source, `.sb3`) |
| `--project-json` | write an unzipped `project.json` instead of a zip |
| `--html` | also write a standalone `.html` (TurboWarp Packager; needs `npm install`) |
| `--html-title TEXT` | page title for the `--html` file (default: the file name) |
| `--locals sprite\|thread` | where bare variables live (`thread` needs `lmsTempVars2`) |
| `--warp` | every custom block runs without screen refresh |
| `--enable FEATURE` | turn on a language feature (repeatable, see `jswc features`; `pishiSupport` adds the pishi.ai extensions) |
| `--target scratch\|turbowarp` | which build of one source: defines `SCRATCH` or `TURBOWARP` for `#ifdef` |
| `--enable FEATURE` | turn on a language feature (repeatable) |
| `--obf-junk N` | with `doObfuscation`: junk strings to shuffle into each pool (default: one per real item, `0` adds no decoys) |
| `--obf-flatten N` | with `doObfuscation`: turn hat bodies of up to `N` blocks into a state machine (default 16, `0` disables) |
| `--obf-pools N` | with `doObfuscation`: spread the literals over `N` lists (default 3, `1` puts them all in one) |
| `--obf-scrub` | with `doObfuscation`: drop workspace comments and randomise block positions |
| `--obf-ints auto\|list\|none` | with `doObfuscation`: how integer literals are hidden (default `auto`: one variable per constant, ~1.4×; `list`: `item of list` like text, ~3.3×; `none`: inline, free) |
| `--vm-budget N` | with `vmProtect`: interpreter steps per loop iteration (default 1, one per frame; `0` is treated as `1`) |
| `--vm-style list\|h0` | with `vmProtect`: `h0` (default) gives every instruction an explicit `switch` case; `list` dispatches through a shared `__vm_step` |
| `--vm-seed N` | with `vmProtect`: seed the randomized VM layout; omit it for a fresh layout on every build |
| `--vm-crypt 0..3` | with `vmProtect`: `0` fixed layout, `1` randomized layout (default), `2` add checksum guards, `3` add decoy cases |
| `--vm-split` / `--no-vm-split` | with `vmProtect`: put each h0 instruction body in its own helper method (default: split; `--no-vm-split` keeps it in `__vm_switch`) |
| `--layout auto\|always\|never` | block placement: `auto` (default) lays out scripts that have no recorded position, `always` ignores recorded positions and cleans up everything, `never` leaves them where they are |
| `--layout-height PX` | how tall one column of scripts may get before a new one starts (default: 4000) |
| `--only-sprite NAME` | emit the Stage and that one sprite, for a quick single-sprite edit loop (a kept sprite that still calls a dropped one gets `W0029`) |
| `-D/--define NAME` | define `NAME` for `#ifdef` (repeatable) |
| `--allow-undefined` | undefined globals become warnings instead of errors |
| `--strict` | exit 1 as soon as a diagnostic is produced |
| `-v/--verbose` | per-phase timings: parse · generate · validate · write |

Features are opt-in because they change the shape of the generated project:

```bash
jswc build project.jsw --enable useTempVariables --enable commentMetadata
jswc build project.jsw --enable namespacedNames --enable doObfuscation
jswc build project.jsw --enable doObfuscation --enable corruptBlockPos
jswc build project.jsw --enable vmProtect --vm-budget 4
jswc build project.jsw --enable vmProtect --vm-style list
```

`vmProtect` replaces supported hat bodies and custom-block bodies with calls to
a per-sprite bytecode interpreter (`-l info` prints how many programs it took
and how many scripts stayed native; a program is a hat or a custom block). It is
deliberately slow: the interpreter runs one instruction per frame by default,
and `--vm-budget N` trades that slowdown for speed. A script or custom block
using a block outside the supported set stays native and reports `W0031`;
`vanillaScratch` skips `vmProtect` entirely with `W0030`. The generated project
uses the same built-in blocks and extensions the source did, but it is meant for
TurboWarp-sized projects, where the slowdown can be tuned.

The default `h0` backend uses the hand-written state-machine shape from
`examples/h0.jsw`: one numbered case per instruction, dispatched by
`__vm_run`. The dispatch bisects on the label rather than emitting one long
if/elif chain (JSW's `switch`), so a project with thousands of instructions
keeps a logarithmic block depth and a logarithmic cost per step. `--vm-style list` instead stores the
instruction stream in Stage lists and dispatches it through a shared
`__vm_step` if/elif chain. Both styles use the same bytecode lowering,
native-call handling, frame allocator and warp rules; only the dispatch
differs. The `h0` style produces many more Scratch blocks and its case chain is
generally slower at runtime; use `--vm-style list` for smaller, faster builds.
Either style changes the generated control flow, not the JSW source or its
class names.

The VM layout is randomized per build: opcode ids, h0 case labels and their
order, and the generated helper names are not stable between builds. This keeps
two builds of the same source from sharing a readable dispatch table. Pass
`--vm-seed N` to reproduce a build exactly. `--vm-crypt N` selects the layout:
`0` is a fixed layout for debugging, `1` is the default randomized layout,
`2` also adds a checksum guard to every VM entry, and `3` adds unreachable
decoy cases. In h0 style, each unique instruction body is emitted as a helper
method by default (`--vm-split`), so no single function contains the whole
dispatch; `--no-vm-split` puts the bodies back inside `__vm_switch`.

A guarded green-flag reset clears the shared VM arena/free list when no frame
is active; completed runs also reset the allocator by themselves. Generic
community-extension commands and reporters are virtualized when their metadata
and fixed menus are known; extension hats keep their native trigger and
virtualize the body. A computed extension menu, some extension loops, or an unsupported extension
conditional still falls back to native blocks with `W0031`.

Custom-block parameters are part of the protected frame, and procedure calls
from either the interpreter or an ordinary native script go through the
generated stub. Nested calls allocate VM frames and return them to the free
list when the callee halts, so recursion works. The arena starts with up to 320
frames but at most ~12,288 slots, so a project with very deep expressions starts
smaller rather than carrying an enormous list; it grows by one frame whenever
nested calls or concurrent clones need more, so live frames are never
overwritten. `--warp` only speeds up the native stubs the
interpreter calls: `__vm_run` itself stays a non-warp loop so it always yields.
For the same reason, `// warp` custom blocks and every procedure they call stay
native. A virtualised caller can still call them -- warp mode belongs to the
callee's stack frame -- so a procedure that merely calls a warp helper keeps its
protection.

`corruptBlockPos` is the anti-tamper build: every target gets a few blocks
nothing can reach whose `next`/`parent`/input ids name blocks that do not
exist, a prototype whose `%b` parameter holds a string reporter, and block
positions scattered at random. The VM only follows references from a hat, so
the project still runs; scratch-blocks resolves the whole blocks map, so the
TurboWarp editor reports the missing ids and refuses to open the workspace.
It is independent of `doObfuscation` (combine them for a release), and off by
default because a corrupt project cannot be opened for editing.

`--target` and `-D` are the preprocessor's, not a feature flag: they are
resolved before lexing, so the lines they hide never reach the parser. One
source, two artifacts — a Scratch build that may only use built-in blocks, and
a TurboWarp build that may load an extension:

```bash
jswc build machine.jsw -o build/machine.sb3        --target turbowarp
jswc build machine.jsw -o build/machine-scratch.sb3 --target scratch
jswc build machine.jsw -o build/debug.sb3 -D TRACE
```

`check` accepts both flags too, so the editor's fast pass answers a question
about the same text the build will compile — see §3.

`jswc features` prints the current list:

```
language features
  • commentMetadata
  • corruptBlockPos
  • doObfuscation
  • hideObjectNames
  • namespacedNames
  • pishiSupport
  • useTempVariables
  • vanillaScratch
  • vmProtect
  enable with --enable FEATURE (repeatable)
```

### One file that runs anywhere: `--html`

```bash
jswc build userspace/school/gtm_demo.jsw -o build/gtm_demo.sb3 --html
```

```
✔ Compiled build/gtm_demo.sb3
  blocks 5 · sprites 1 · classes 1 · time 272 ms
  extensions gtm
  html      build/gtm_demo.html (1.9 MB, self-contained)
```

The `.sb3` is a *source* for whoever has the project server; the `.html` is the
deliverable. It embeds the whole TurboWarp player, every asset, and every custom
extension — `extensionURLs` entries are fetched at build time and rewritten to
`data:text/javascript,` URLs by the packager, so a project that uses `gtm` runs
from a folder with nothing else in it, offline.

This is TurboWarp's own `@turbowarp/packager` (pinned to an exact version in
`package.json`, because its Node API is unstable across minor releases), not a
re-implementation. Two consequences:

* **`npm install` once** in the checkout. Without it the build stops with a
  message saying so.
* **The extension URLs have to answer at build time.** A `localhost:8000` entry
  means `make serve` has to be running for that one command. If a URL does not
  answer, the build fails and names it — the packager on its own only *warns*
  and leaves the URL in the file, which produces a page that looks packaged and
  then works on exactly one machine.

The project model is the same one the `.sb3` gets: `--enable`, `--obf-*`,
`--target` and so on all apply, and `--project-json --html` works too (the
archive is built in memory for the packager).

| flag | effect |
| --- | --- |
| `--html` | also write `<output>.html` next to the `.sb3` |
| `--html-title TEXT` | `document.title` / window title (default: derived from the file name) |

Autoplay and turbo mode are on in the packaged page, since it is meant to be
handed to someone and double-clicked. For a title screen or a different
appearance, use <https://packager.turbowarp.org/> with the same `.sb3`.

#### Hosting the packaged file: a page-wide Content-Security-Policy breaks it

The file is self-contained, but it is *not* a single script: each extension is a
`data:text/javascript` URL, and an extension that loads a library does so from a
CDN. A host that sends a `Content-Security-Policy` has to allow both, or the page
opens and does nothing — the console says

```
Loading the script 'data:text/javascript,…' violates the following Content
Security Policy directive: "script-src 'self' 'unsafe-inline' …"
Fetch API cannot load https://storage.googleapis.com/tm-model/<id>/metadata.json.
Refused to connect because it violates the document's Content Security Policy.
```

| needs | why |
| --- | --- |
| `script-src … data: blob:` | every inlined extension |
| `script-src … https://cdn.jsdelivr.net` | TensorFlow.js, which `gtm` injects as a `<script>` |
| `connect-src … https://teachablemachine.withgoogle.com` | the share link's `model.json` |
| `connect-src … https://storage.googleapis.com` | where that link redirects (`tm-model/…`) |
| `connect-src … https://cdn.jsdelivr.net` | tfjs fetches its own weights/backends |
| `Permissions-Policy: camera=(self)` | `getUserMedia`; the default is already `self` |

#### When the header cannot be changed: `--self-hosted`

```bash
jswc build userspace/school/main.jsw -o tmp/serve.sb3 --html --self-hosted
```

```
✔ Compiled tmp/serve.sb3
  html      tmp/serve.html (1.8 MB, self-contained)
```

The page is written as before, plus one folder next to it that it loads
everything from:

| path | what |
| --- | --- |
| `serve_files/ext/<id>.js` | each extension, fetched at build time and referenced as `serve_files/ext/<id>.js` instead of a `data:` URL |
| `serve_files/lib/@tensorflow/tfjs@…/dist/tf.min.js` | TensorFlow.js, at the path `gtm` builds from its base |
| `serve_files/lib/@teachablemachine/pose@…/…` | the pose runtime, when the model needs it |
| `serve_files/lib/tfjs-models/savedmodel/posenet/…` | posenet's pretrained weights, which the pose runtime fetches by absolute URL |
| `serve_files/model/<id>/` | the model the project names: `metadata.json`, `model.json` and every weight shard |

Every URL in the page is then same-origin, so a host whose policy is
`script-src 'self'; connect-src 'self'` runs it unchanged. Three things make
that work:

* the extensions are named relative to the page and resolved at load time
  (`new URL(extension, document.baseURI)`), so the folder can be moved or served
  from any path — the VM itself rejects a relative URL outright;
* `gtm` reads `window.GTM_LIB_BASE` and `window.GTM_MODEL_MIRROR`, which the
  build sets in an inline script, so the library and the model come from the
  folder while the *source* still says the Teachable Machine share link;
* posenet's weights are fetched by the pose runtime with a hard-coded Google
  URL, so `fetch` is wrapped for the `https://storage.googleapis.com/tfjs-models/`
  prefix only (`window.GTM_MIRROR_MAP`).

Upload the page **and** its folder together. The `.sb3` is unchanged — it still
names the original URLs, so it keeps working in the editor.

The mirror is what a build can see: models named by a literal share link. A URL
computed at run time cannot be copied, and the page will fail to load that model
under a strict policy.

Do not replace the policy with one of these — add the sources to the one being
sent, so the rest of the site keeps its protection:

| directive | add |
| --- | --- |
| `script-src` | `data: blob: https://cdn.jsdelivr.net` |
| `connect-src` | `https://teachablemachine.withgoogle.com https://storage.googleapis.com https://cdn.jsdelivr.net` |

nginx appends the directive inline:

```nginx
add_header Content-Security-Policy "default-src 'self'; script-src 'self' 'unsafe-inline' 'unsafe-eval' data: blob: https://static.cloudflareinsights.com https://www.google.com https://www.gstatic.com https://cdn.jsdelivr.net; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob: https:; media-src 'self' data: blob: https:; font-src 'self' data:; connect-src 'self' https://cloudflareinsights.com https://teachablemachine.withgoogle.com https://storage.googleapis.com https://cdn.jsdelivr.net ws: wss:; frame-src 'self' blob:; worker-src 'self' blob:; object-src 'none'; base-uri 'self'; form-action 'self'; frame-ancestors 'self'" always;
```

Behind Cloudflare the header may come from a **Transform Rule → Modify response
header** rather than the origin, in which case editing nginx changes nothing: set
the same single-line value there (Rules → Transform Rules → Modify response
header → *Set static* on `Content-Security-Policy`).

nginx's `add_header` does not append across levels: a nested block that sets any
`add_header` drops the outer ones, so edit the policy that is actually being
sent rather than adding a second one. Check with
`curl -sSI https://host/page.html | grep -i content-security` — `data:` has to
appear in `script-src`, and the three hosts in `connect-src`.

`tools/check_csp.py` reads the live response and names the missing sources, so
this is a one-command answer rather than a console hunt:

```console
$ python3 tools/check_csp.py https://chat.lqx211.com/temp/serve.html
page      https://chat.lqx211.com/temp/serve.html
          2,013,929 bytes, packaged (extensions inlined)
policy    header
script-src
          MISSING  data:                                    the inlined extensions are data: URLs
          ok       'unsafe-eval'                            scratch-vm's compiler uses Function()
          MISSING  https://cdn.jsdelivr.net                 TensorFlow.js is injected as a <script>
connect-src
          MISSING  https://storage.googleapis.com           where that link redirects (tm-model/…)
```

It exits non-zero while anything is missing, so it can gate a deploy.

To check the header *before* touching a server, `tools/serve.py` can send it:

```bash
python3 tools/serve.py --root tmp --csp packaged   # http://localhost:8010/
```

`--csp packaged` adds that `Content-Security-Policy` and
`Permissions-Policy: camera=(self)`, so the page is tested against exactly what a
real host will send. Without the flag the server sends no policy, which is how a
page that fails in production passes locally.

### Where the blocks land: `--layout`

```bash
jswc build userspace/np5/main.jsw -o build/np5.sb3 --layout always
```

A project that opens in TurboWarp already arranged is a project you can read,
and the placement is the one TurboWarp's own **Clean up blocks** produces: the
first script at `(48, 64)`, each following script 72 px below the last, and a
new column starting one widest-script plus 96 px to the right. That is
TurboWarp's DevTools "Clean up Plus" addon, which replaces `Blockly`'s
`cleanUp`; the compiler reproduces its placement, not its column-splitting (see
below).

Clean-up gets its spacing right because it asks the rendered workspace how tall
each script is. A compiler has no workspace, so `jswc/layout.py` models it.
Every constant there was measured by reading `block.height` off a live editor —
4,476 blocks across NotePlayer V5 and a shape probe, all reproduced exactly:

| shape | height |
| --- | --- |
| a statement row | 56, `pen_*` / `music_*` rows 64 (they draw an icon) |
| a block that ends a script (`stop all`, `delete this clone`) | 48, no bottom notch to share |
| a reporter, flat | 40 |
| a literal or dropdown | 32 |
| `if` / `repeat` | row + 24 + the substack (32 even when empty) |
| `forever` | row + 16 + the substack (no bottom arm) |
| `if`/`else` | row + 24 + then + 24 + else |
| an input taller than its slot | grows the row above it, 8 px per 8 px (16 for statements) |
| stacked rows | share a notch: 8 px per joint |

Which scripts move is the point of `--layout`:

| mode | behaviour |
| --- | --- |
| `auto` (default) | scripts the source placed with `// at x,y` or `metadata … pos` stay put; everything else is arranged |
| `always` | ignore the recorded positions and arrange the whole project — TurboWarp's button |
| `never` | place nothing; every script keeps the coordinates it was given |

`auto` is the default because a decompiled project carries the original
author's layout in its `metadata pos` lines, and rebuilding it should not
scatter a hand-arranged workspace.

Two differences from the button in the editor:

* **Columns are packed by height, not inferred from where the blocks already
  are.** Clean-up splits columns by looking for x positions more than 256 px
  apart, which a freshly compiled project has none of. `--layout-height PX`
  says how tall one column may get before the next starts (default 4000, about
  two screens).
* **Workspace comments are left where they are.** They are free-floating
  `blockId: null` comments with coordinates from the source, so they do not
  move with a script.

---

## 2. Decompile

```bash
jswc decompile sample_np4.26_sb3.zip -o build/final/zip.jsw
```

```
✔ Decompiled build/final/zip.jsw
  sprites 10 · assets 346 · lines 2,734 · time 650 ms
  state files 19 (.jsa)
  63 warnings
```

- costumes and sounds land in `assets/` next to the output (`--assets-prefix`
  renames the folder, `--skip-assets` does not write them at all),
- huge variables and lists are written to `.jsv` / `.jsa` / `.jsl` files instead
  of bloating the source — `--max-inline N` sets the threshold (default 50),
- monitors, costume metadata and sprite properties are preserved as
  `// monitor`, `// costume` and `// Sprite:` comments; add
  `#enable_feature<commentMetadata>;` at the top of the decompiled file to make
  the compiler read them back,
- `--strict-roundtrip` exits 1 when something would not survive the trip back:
  a name that had to be renamed (`I0001`) or a block kept as text (`W0008`).
  The differences are listed under the failure, which is what makes the flag
  usable in CI.

---

## 3. Check

```bash
jswc check project.jsw
```

```
checking project.jsw
✔ parsed 10 class(es), 83 function(s)
  • core        24 function(s)
  • Player      31 function(s)
  …
  ⚠ W002  project.jsw:405:37  eval: the 'looks_costume' dropdown is computed at runtime…
  1 warning
```

`check` never generates a project, so it is the fast feedback loop while
editing. Parsing is lenient (an unclosed `(` is auto-closed and reported);
`--strict` makes any diagnostic exit with code 1.

It takes the same `-D` / `--target` as `build`, because a file full of `#ifdef`
has more than one correct reading and the one that matters is the build you are
about to make:

```bash
jswc check project.jsw --target turbowarp     # the branches that build defines
jswc check project.jsw                        # neither: both branches dropped
```

Without the flags the preprocessor runs with nothing defined, so every guarded
branch is invisible and `check` answers the "neither build" question. If a line
only exists under `#ifdef TURBOWARP`, ask about it with `--target turbowarp` —
the diagnostic keeps the line number the author wrote, dropped lines above it or
not.

### Machine-readable output

`--format=efm` prints one line per diagnostic in `errorformat` shape, which is
what Vim, `efm-langserver` and `nvim-lint` read:

```bash
jswc check project.jsw --format=efm
```
```
project.jsw:12:9: E: undefined variable 'scoer' (E0035)
project.jsw:405:37: W: eval: the 'looks_costume' dropdown is computed at runtime (W0004)
```

`--format=json` gives the same rows as objects with `file`, `line`, `col`,
`code`, `severity` and `message`. Both formats print nothing at all for a clean
file and exit 0, so a linter never keeps stale messages. See
[`docs/NVIM.md`](./NVIM.md) for a ready-made configuration.

---

## 4. Inspect

```bash
jswc ast project.jsw          # class / method tree
jswc ast project.jsw --json   # full AST as JSON (for tooling)
jswc tokens project.jsw       # line:col, kind, value for every token
```

---

## 5. Diagnostic codes

Every message carries a stable code, a title and (with `--explain`) a hint, so
you can look it up, grep the docs, or hand it to someone else:

```
  ✖ E0011  bad.jsw:1:12  expected '{', found 'extends'
    │ class main extends Sprite {
    │            ^
```

Codes are a flat list — `E0001`, `E0002`, … for errors and `W0001`, `W0002`, …
for warnings. The number carries no meaning: it is an identifier to quote, not
a category. New problems take the next free number, so a code never changes
once it has shipped, and nothing else is renumbered when one is added.

`jswc codes` prints the whole registry straight from
[`jswc/diagnostics.py`](../jswc/diagnostics.py):

```bash
jswc codes                      # every code, in numeric order
jswc codes --severity warning   # only warnings
jswc codes -s metadata          # search codes, titles and hints
jswc codes --explain            # add the hint line under each code
```

### Log levels

`--loglevel` (`-l`) decides which severities print. It defaults to `warn`, so a
normal build stays quiet:

| level | prints |
| --- | --- |
| `error` / `quiet` | errors only |
| `warn` (default) / `warning` | errors and warnings |
| `info` / `all` | everything, including `Ixxx` |

```bash
jswc -l info decompile project.sb3 -o project.jsw   # include the renames
jswc check src.jsw -l error                          # errors only
```

`Ixxx` codes are **information**: true, worth knowing, not a problem. The
decompiler's renames live there — `I0001` appears 59 times on a real project,
which is exactly why it is not a warning. Nothing is ever lost: `-l info`
restores the full report, and the tally always shows how many were hidden.

### Hints

The hint under a diagnostic is **off by default** — a compile of a working
project stays quiet. Pass `--explain` (or `-e`) to `jswc`, before or after the
command, to print it:

```bash
jswc --explain build src/main.jsw -o build/main.sb3
jswc build src/main.jsw -o build/main.sb3 --explain
```

```
  ✖ E0002  a.jsw:3:13  string literal runs to the end of the line without a closing quote
    │         x = "abc;
    │             ^
    help: Add the closing quote, or escape the one inside as \".
```

### The registry

| `E0001` | error | unexpected character |
| `E0002` | error | unterminated string literal |
| `E0003` | error | unterminated f-string |
| `E0004` | error | unterminated block comment |
| `E0005` | error | invalid \x escape |
| `E0006` | error | invalid \u escape |
| `E0007` | error | malformed numeric literal |
| `E0008` | error | expected a token |
| `E0009` | error | expected a name |
| `E0010` | error | expected ';' |
| `E0011` | error | expected '{' |
| `E0012` | error | expected '}' |
| `E0013` | error | expected '(' |
| `E0014` | error | expected ')' |
| `E0015` | error | expected '[' |
| `E0016` | error | expected ']' |
| `E0017` | error | unexpected token |
| `E0018` | error | unterminated block |
| `E0019` | error | unterminated class body |
| `E0020` | error | unterminated stage body |
| `E0021` | error | unterminated f-string hole |
| `E0022` | error | malformed 'for' header |
| `E0023` | error | malformed 'switch' |
| `E0024` | error | missing asset path |
| `E0025` | error | 'call' without a call |
| `E0026` | error | invalid assignment target |
| `E0027` | error | invalid increment target |
| `E0028` | error | unexpected token in an f-string hole |
| `E0029` | error | no class declarations |
| `E0030` | error | bad directive |
| `E0031` | error | metadata needs a target |
| `E0032` | error | malformed metadata |
| `E0033` | error | expected 'init' |
| `E0034` | error | unexpected top-level statement |
| `E0035` | error | undefined variable |
| `E0036` | error | unknown function |
| `E0037` | error | unknown reporter |
| `E0038` | error | unknown command |
| `E0039` | error | statement used as a value |
| `E0040` | error | not a block of that extension |
| `E0041` | error | private to another sprite |
| `E0042` | error | cannot assign to a parameter |
| `E0043` | error | cannot assign to a list |
| `E0044` | error | wrong number of arguments |
| `E0045` | error | expected a literal value |
| `E0046` | error | expected a variable name |
| `E0047` | error | expected a list name |
| `E0048` | error | expected a message name |
| `E0049` | error | expected a block opcode |
| `E0050` | error | does not take a block body |
| `E0051` | error | custom block returns a value |
| `E0052` | error | initial value is not a literal |
| `E0053` | error | unsupported construct |
| `E0054` | error | unsupported syntax |
| `E0055` | error | call target could not be lowered |
| `E0056` | error | proccode mismatch |
| `E0057` | error | expected a field name |
| `E0058` | error | asset not found |
| `E0059` | error | invalid JSON |
| `E0060` | error | expected a JSON array |
| `E0061` | error | unsupported asset type |
| `E0062` | error | dangling block reference |
| `E0063` | error | malformed block structure |
| `E0064` | error | malformed input |
| `E0065` | error | unknown variable id |
| `E0066` | error | unknown list id |
| `E0067` | error | unknown broadcast id |
| `E0068` | error | unknown opcode |
| `E0069` | error | unknown feature |
| `E0070` | error | unreadable monitor comment |
| `E0071` | error | no such sprite or class |
| `E0072` | error | unknown monitor state |
| `E0073` | error | metadata needs a literal value |
| `E0074` | error | temp loop variable already live |
| `E0076` | error | wrong number of arguments |
| `W0001` | warning | ignored an extra token |
| `W0002` | warning | auto-closed bracket |
| `W0004` | warning | computed at runtime |
| `W0005` | warning | undeclared argument |
| `W0006` | warning | keyword outside an argument |
| `W0007` | warning | key the VM cannot report |
| `W0008` | warning | dropped or degraded |
| `W0009` | warning | 'continue' ignored |
| `W0010` | warning | class-level statement skipped |
| `W0011` | warning | duplicate visibility |
| `W0012` | warning | initial value not literal |
| `W0013` | warning | expression statement has no effect |
| `W0014` | warning | duplicate import |
| `W0015` | warning | comment directive ignored |
| `W0016` | warning | comment for an unknown target |
| `W0017` | warning | comment text is not a string literal |
| `W0018` | warning | unreadable note comment |
| `W0019` | warning | monitor annotation ignored |
| `W0020` | warning | duplicate metadata |
| `W0021` | warning | loop counter reassigned |
| `W0022` | warning | display matrix |
| `W0023` | warning | broadcast name built at runtime |
| `W0024` | warning | clone abandoned by stopThisScript |
| `W0025` | warning | 'break' inside a loop |

### Information

| code | meaning |
| --- | --- |
| `I0001` | a name was renamed to stay legal in Scratch (hidden unless `-l info`) |

`E9999` is the last resort: a message that matched no entry at all. Seeing one
means a diagnostic was raised without a code — please report the text verbatim.

### Migrating from `E001`–`E008` / `E1xxx`

The pre-1.1 scheme grouped everything into eight codes, then into five families.
Both are gone. Every old code maps to exactly one new one; the message text is
unchanged, so `grep` for the wording when you need the translation:

| old | new |
| --- | --- |
| `E001` lexical (`E1xxx`) | `E0001`–`E0007` |
| `E001` syntax (`E2xxx`) | `E0008`–`E0034` |
| `E002` unknown name (`E3xxx`) | `E0035`–`E0039` |
| `E005` declaration / scope | `E0040`–`E0052` |
| `E003` unknown block | `E0040`, `E0069` |
| `E007` cannot assign | `E0042`, `E0043` |
| `E004` bad asset (`E4xxx`) | `E0058`–`E0061` |
| `E008` invalid project (`E4xxx`) | `E0062`–`E0068` |
| `E006` bad directive (`E5xxx`) | `E0030`–`E0032`, `E0070`–`E0072` |
| `W001` renamed | `I0001` |
| `W002` eval | `W0004` |
| `W004` undeclared argument | `W0005` |
| `W006` shadowed declaration | `W0011` |
| `W007` argument shape | `E0044`–`E0049` |
| `W008` keyword outside an argument | `W0006` |
| `W009` key the VM cannot report | `W0007` |
| `W003` dropped / degraded | `W0008`–`W0013` |
| `W005` ignored directive | `W0015`, `W0019` |

Messages that still arrive as plain text (from a plugin, or an older call site)
are classified by shape as a fallback, so nothing renders without a code.

### About `W0004` (eval)

Scratch matches broadcast names, costume names and menu entries **by text at
run time**. Writing

```jsw
Looks.switchCostume(join("cos", "tume1"));
```

works, but a typo or a rename fails silently inside the VM. The compiler cannot
prove the result, so it warns — this is the one class of message worth reading
twice before shipping a project.

---

## 6. Exit codes

| code | meaning |
| --- | --- |
| `0` | success |
| `1` | build failed: invalid project, or `--strict` with diagnostics |
| `2` | the source could not be read, parsed or compiled |
| `130` | interrupted (`Ctrl-C`) |

Scripts can therefore use `jswc build x.jsw --strict || exit 1`.

---

## 7. Colour and terminals

Output uses 256-colour ANSI plus a small `◎ → ◉` spinner while a project is
being generated. It degrades automatically:

| situation | result |
| --- | --- |
| piped / redirected / CI | no colour, no spinner, one line per step |
| `NO_COLOR=1` | no colour |
| `TERM=dumb` | no colour |
| `--no-color` | no colour (beats `FORCE_COLOR`) |
| `--plain` | ASCII symbols: `OK`, `XX`, `├─`, `->` |
| `FORCE_COLOR=1` | colour even when redirected (screenshots, demos) |

The spinner writes to **stderr**, results to **stdout**, so
`jswc decompile big.sb3 -o big.jsw > log.txt` still gives you a clean log.

---

## 8. Recipes

```bash
# round-trip a project and compare the two builds
jswc decompile project.sb3 -o roundtrip/project.jsw
jswc build roundtrip/project.jsw -o roundtrip/project_rt.sb3

# ... and fail when the trip cannot be exact
jswc decompile project.sb3 -o roundtrip/project.jsw --strict-roundtrip

# one sprite, while it is being edited
jswc build machine.jsw -o build/one.sb3 --only-sprite Cpu

# fail CI on any new warning
jswc build src/main.jsw -o build/main.sb3 --strict

# ship a build with names hidden, decoys in place and state machines on
jswc build src/main.jsw -o build/hidden.sb3 --enable doObfuscation

# the same, but small: no junk strings and straight scripts
jswc build src/main.jsw -o build/hidden.sb3 --enable doObfuscation \
    --obf-junk 0 --obf-flatten 0

# the same, for a CPU that has to keep every last frame: numbers stay inline
jswc build src/main.jsw -o build/hidden.sb3 --enable doObfuscation --obf-ints none

# bytecode-protected scripts, slowed to one instruction per frame
jswc build src/main.jsw -o build/protected.sb3 --enable vmProtect

# a release build: one string list, no comments, no canvas positions
jswc build src/main.jsw -o build/release.sb3 --enable doObfuscation \
    --obf-pools 1 --obf-scrub

# find where a reporter is used
jswc tokens src/main.jsw | grep -i "setPenColorToColor"
```
