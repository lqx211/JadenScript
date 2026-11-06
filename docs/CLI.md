# `jswc` — command line tour

Everything you can do with JadenScript goes through one entry point:

```bash
python3 -m jswc <command> [options]     # from a checkout
jswc <command> [options]                # after `pip install -e .`
```

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
| `--locals sprite\|thread` | where bare variables live (`thread` needs `lmsTempVars2`) |
| `--warp` | every custom block runs without screen refresh |
| `--enable FEATURE` | turn on a language feature (repeatable, see `jswc features`) |
| `--target scratch\|turbowarp` | which build of one source: defines `SCRATCH` or `TURBOWARP` for `#ifdef` |
| `-D/--define NAME` | define `NAME` for `#ifdef` (repeatable) |
| `--allow-undefined` | undefined globals become warnings instead of errors |
| `--strict` | exit 1 as soon as a diagnostic is produced |
| `-v/--verbose` | per-phase timings: parse · generate · validate · write |

Features are opt-in because they change the shape of the generated project:

```bash
jswc build project.jsw --enable useTempVariables --enable commentMetadata
jswc build project.jsw --enable namespacedNames --enable doObfuscation
```

`--target` and `-D` are the preprocessor's, not a feature flag: they are
resolved before lexing, so the lines they hide never reach the parser. One
source, two artifacts — a Scratch build that may only use built-in blocks, and
a TurboWarp build that may load an extension:

```bash
jswc build machine.jsw -o build/machine.sb3        --target turbowarp
jswc build machine.jsw -o build/machine-scratch.sb3 --target scratch
jswc build machine.jsw -o build/debug.sb3 -D TRACE
```

`jswc features` prints the current list:

```
language features
  • commentMetadata
  • doObfuscation
  • namespacedNames
  • useTempVariables
  enable with --enable FEATURE (repeatable)
```

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
  the compiler read them back.

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

# fail CI on any new warning
jswc build src/main.jsw -o build/main.sb3 --strict

# ship a build with names hidden
jswc build src/main.jsw -o build/hidden.sb3 --enable doObfuscation

# find where a reporter is used
jswc tokens src/main.jsw | grep -i "setPenColorToColor"
```
