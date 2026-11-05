"""Command line interface: ``python -m jswc <command>``."""

from __future__ import annotations

import argparse
import dataclasses
import json
import sys
import time
from pathlib import Path

from . import __version__, include, lints, ui
from .codegen import Codegen
from .lexer import tokenize
from .parser import Parser
from .sb3 import write_project_json, write_sb3
from .include import Source
from .tokens import JswError
from .validator import validate

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_ERROR = 2
EXIT_INTERRUPTED = 130

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _read(path: str | Path) -> str:
    return Path(path).read_text(encoding="utf-8")


def _load(path: str | Path) -> Source:
    """Read a source file with its ``// include`` lines expanded."""
    return include.expand(path)


def _parse(path: str | Path, lenient: bool = True) -> tuple[Parser, object]:
    parser = Parser(tokenize(_read(path)), lenient=lenient)
    return parser, parser.parse()


def _parse_source(source: Source, lenient: bool = True) -> tuple[Parser, object]:
    parser = Parser(tokenize(source.text), lenient=lenient)
    return parser, parser.parse()


def _err(message: str) -> None:
    sys.stdout.flush()
    print(message, file=sys.stderr)


def _report_diagnostics(notes: list[str], source: str | Source | None, name: str,
                        suffix: str = "") -> tuple[int, int, int]:
    """Print the diagnostics ``--loglevel`` allows; returns ``(errors, warnings, info)``."""
    errors = warnings = info = 0
    for note in notes:
        entry = ui.classify(note)
        if not ui.shows(entry.severity, ui.THEME.loglevel):
            continue
        text = ui.format_diagnostic(*_locate(note, source, name))
        if suffix:
            text += ui.muted(f"  {suffix}")
        _err(text)
        if entry.severity == "error":
            errors += 1
        elif entry.severity == "warning":
            warnings += 1
        else:
            info += 1
    return errors, warnings, info


def _locate(note: str, source: str | Source | None,
            name: str) -> tuple[str, str | None, str]:
    """Point a diagnostic at the module it came from, not at the joined text."""
    if not isinstance(source, Source):
        return note, source, name
    line, col, body = ui.split_position(note)
    if line is None:
        return note, source.text, name
    file, real = source.origin(line)
    # `format_diagnostic` indexes the *whole* text by line number, so it needs
    # the module's text and the module's own line, not the joined pair.
    return f"{real}:{col}: {body}", _file_text(file), Path(file).name


def _file_text(path: str) -> str:
    text = _FILE_CACHE.get(path)
    if text is None:
        try:
            text = Path(path).read_text(encoding="utf-8")
        except OSError:
            text = ""
        _FILE_CACHE[path] = text
    return text


_FILE_CACHE: dict[str, str] = {}


def _tally(errors: int, warnings: int, info: int = 0) -> str:
    theme = ui.THEME
    parts = []
    if info:
        parts.append(theme.paint(f"{info} info", theme.blue))
    if warnings:
        parts.append(theme.paint(f"{warnings} warning{'s' if warnings != 1 else ''}", theme.yellow))
    if errors:
        parts.append(theme.paint(f"{errors} error{'s' if errors != 1 else ''}", theme.red))
    if not parts:
        parts.append(theme.paint("no diagnostics", theme.green))
    return "  " + ui.separator().join(parts)


def _stats(parts: list[tuple[str, str]]) -> str:
    """``blocks 1,336 · extensions 6`` with dim labels and cyan values."""
    theme = ui.THEME
    chunks = [f"{ui.muted(label + ' ')}{theme.paint(str(value), theme.cyan)}"
              for label, value in parts]
    return "  " + ui.separator().join(chunks)


def _project_stats(project) -> dict[str, int]:
    blocks = sum(len(target.blocks) for target in project.targets)
    sprites = sum(1 for target in project.targets if not target.is_stage)
    return {"blocks": blocks, "sprites": sprites}


# ---------------------------------------------------------------------------
# commands
# ---------------------------------------------------------------------------


def cmd_check(args: argparse.Namespace) -> int:
    started = time.perf_counter()
    try:
        loaded = _load(args.source)
    except include.IncludeError as exc:
        _err(ui.failure(str(exc)))
        return EXIT_FAILED
    source = loaded.text
    name = Path(args.source).name
    if args.format != "text":
        # Machine-readable: the editor reads these, so print nothing else.
        _emit_machine_diagnostics(args.source, args.format)
        return EXIT_OK
    print(ui.heading(f"checking {args.source}"))
    parser, program = _parse_source(loaded, lenient=True)

    functions = sum(len(cls.funcs) for cls in program.classes)
    print(ui.success(f"parsed {ui.accent(str(len(program.classes)))} class(es), "
                     f"{ui.accent(str(functions))} function(s)"))
    for cls in program.classes:
        print(f"  {ui.muted(ui.THEME.symbol('bullet'))} "
              f"{ui.THEME.paint(cls.name, ui.THEME.bold, ui.THEME.blue)}  "
              f"{ui.muted(f'{len(cls.funcs)} function(s)')}")

    notes = list(parser.diagnostics) + lints.run(program)
    errors, warnings, info = _report_diagnostics(notes, loaded, name)
    print(_tally(errors, warnings, info))
    print(ui.muted(f"  {name}{ui.separator()}{ui.elapsed(time.perf_counter() - started)}"))
    if args.strict and warnings:
        _err(ui.failure("strict mode: warnings are treated as errors"))
        return EXIT_FAILED
    return EXIT_OK


SEVERITY_LETTER = {"error": "E", "warning": "W", "info": "I"}


def _emit_machine_diagnostics(path: str, style: str) -> None:
    """``--format=efm`` / ``--format=json``: exactly what an editor can parse.

    ``efm`` is ``file:line:col: T: message (CODE)``, which is a plain
    ``errorformat`` (``%f:%l:%c: %t: %m``) for Vim, efm-langserver and
    nvim-lint alike.
    """
    parser = Parser(tokenize(_read(path)), lenient=True)
    notes = list(parser.diagnostics)
    try:
        program = parser.parse()
        notes.extend(lints.run(program))
    except JswError as exc:
        # A hard parse error stops the parser before it can collect anything,
        # so the exception itself is the diagnostic.
        notes.append(f"{exc.line}:{exc.col}: {exc.message}")
    rows = []
    for note in notes:
        entry = ui.classify(note)
        if not ui.shows(entry.severity, ui.THEME.loglevel):
            continue
        line, col, body = ui.split_position(note)
        code, message = ui.split_code(body)
        rows.append({
            "file": path,
            "line": line,
            "col": col,
            "code": code,
            "severity": entry.severity,
            "message": message.strip(),
        })
    if style == "json":
        print(json.dumps(rows, indent=1, ensure_ascii=False))
        return
    for row in rows:
        where = f"{row['file']}:{row['line']}:{row['col']}"
        letter = SEVERITY_LETTER.get(row["severity"], "E")
        suffix = f" ({row['code']})" if row["code"] else ""
        print(f"{where}: {letter}: {row['message']}{suffix}")


def cmd_ast(args: argparse.Namespace) -> int:
    parser, program = _parse(args.source)
    if args.json:
        print(json.dumps(_to_json(program), indent=2, ensure_ascii=False))
    else:
        _print_tree(program)
    _report_diagnostics(list(parser.diagnostics), _read(args.source), Path(args.source).name)
    return EXIT_OK


def cmd_tokens(args: argparse.Namespace) -> int:
    result = tokenize(_read(args.source))
    for tok in result.tokens:
        print(f"{tok.line:>4}:{tok.col:<4} {tok.kind.value:<8} {tok.value!r}")
    _err(ui.muted(f"  {ui.count(len(result.tokens))} tokens"
                  f"{ui.separator()}{ui.count(len(result.comments))} comments"))
    return EXIT_OK


def cmd_decompile(args: argparse.Namespace) -> int:
    from .decompile import decompile
    from .sb3_read import load_sb3

    source_path = Path(args.source)
    with ui.Spinner(f"Decompiling {ui.accent(source_path.name)}") as spinner:
        loaded = load_sb3(args.source)
        t_load = time.perf_counter()
        source, diagnostics, extra_files = decompile(
            loaded,
            asset_prefix=args.assets_prefix,
            max_inline=args.max_inline,
            include_assets=not args.skip_assets,
        )
        t_lowered = time.perf_counter()
        output = Path(args.output or source_path.with_suffix(".jsw"))
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(source, encoding="utf-8")

        asset_dir = output.parent / args.assets_prefix.strip("/")
        written = 0
        if loaded.assets and not args.skip_assets:
            asset_dir.mkdir(parents=True, exist_ok=True)
            for asset_name, payload in loaded.assets.items():
                (asset_dir / asset_name).write_bytes(payload)
                written += 1
        for asset_name, payload in extra_files.items():
            target = asset_dir / asset_name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(payload)
        t_done = time.perf_counter()

    took = spinner.stop()

    errors, warnings, info = _report_diagnostics(diagnostics, None, source_path.name)
    print(ui.success(f"Decompiled {ui.THEME.paint(str(output), ui.THEME.bold)}"))
    print(_stats([
        ("sprites", ui.count(len(loaded.sprites))),
        ("assets", ui.count(written)),
        ("lines", ui.count(source.count("\n") + 1)),
        ("time", ui.elapsed(took)),
    ]))
    if extra_files:
        kinds = ", ".join(sorted({Path(n).suffix for n in extra_files}))
        print(_stats([("state files", f"{ui.count(len(extra_files))} ({kinds})")]))
    if errors or warnings or info:
        print(_tally(errors, warnings, info))
    if args.verbose:
        _err("  " + ui.separator().join(ui.muted(f"{k} {v}") for k, v in (
            ("read", ui.elapsed(t_load - spinner.started)),
            ("lower", ui.elapsed(t_lowered - t_load)),
            ("write", ui.elapsed(t_done - t_lowered)),
        )))
    return EXIT_OK


def cmd_build(args: argparse.Namespace) -> int:
    source_path = Path(args.source)
    try:
        loaded = _load(args.source)
    except include.IncludeError as exc:
        _err(ui.failure(str(exc)))
        return EXIT_FAILED
    source_text = loaded.text
    name = source_path.name

    with ui.Spinner(f"Compiling {ui.accent(name)}") as spinner:
        parser, program = _parse_source(loaded, lenient=not args.strict)
        t_parse = time.perf_counter()
        codegen = Codegen(
            program,
            seed=source_path.stem,
            locals_mode=args.locals,
            warp=args.warp,
            strict=args.strict,
            base_dir=source_path.resolve().parent,
            allow_undefined=args.allow_undefined,
            features=set(args.enable),
        )
        project = codegen.compile()
        t_codegen = time.perf_counter()
        issues = validate(project)
        t_validate = time.perf_counter()

    took = spinner.stop()

    errors, warnings, info = _report_diagnostics(list(parser.diagnostics), loaded, name)
    more_errors, more_warnings, more_info = _report_diagnostics(
        list(codegen.diagnostics), loaded, name)
    errors += more_errors
    warnings += more_warnings
    info += more_info
    if codegen.plan is not None and args.allow_undefined and codegen.plan.errors:
        _err("")
        _report_diagnostics(list(codegen.plan.errors), loaded, name,
                            suffix="allowed by --allow-undefined")
    for issue in issues:
        _err(ui.format_diagnostic(issue, None, name))
        errors += 1

    failed = bool(issues)
    if codegen.diagnostics and args.strict:
        failed = True
        _err(ui.failure("strict mode: diagnostics are treated as errors"))
    elif issues:
        _err(ui.failure("the generated project is structurally invalid"))

    if failed:
        _err(_tally(errors, warnings, info))
        _err(ui.muted("  run `jswc codes` to look up a code"))
        return EXIT_FAILED

    output = Path(args.output or source_path.with_suffix(".sb3"))
    if args.project_json:
        path = write_project_json(project, output.with_suffix(".json"))
    else:
        path, _data = write_sb3(project, output)
    took_total = time.perf_counter() - spinner.started

    stats = _project_stats(project)
    print(ui.success(f"Compiled {ui.THEME.paint(str(path), ui.THEME.bold)}"))
    print(_stats([
        ("blocks", ui.count(stats["blocks"])),
        ("sprites", ui.count(stats["sprites"])),
        ("classes", ui.count(len(program.classes))),
        ("time", ui.elapsed(took_total)),
    ]))
    if project.extensions:
        print(f"  {ui.muted('extensions ')}"
              f"{ui.THEME.paint(', '.join(project.extensions), ui.THEME.magenta)}")
    if errors or warnings or info:
        print(_tally(errors, warnings, info))
        if errors:
            print(ui.muted("  the project was written anyway; pass --strict to fail "
                           "on diagnostics"))
    if args.verbose:
        phases = [
            ("parse", ui.elapsed(t_parse - spinner.started)),
            ("generate", ui.elapsed(t_codegen - t_parse)),
            ("validate", ui.elapsed(t_validate - t_codegen)),
            ("write", ui.elapsed(took)),
        ]
        _err("  " + ui.separator().join(ui.muted(f"{k} {v}") for k, v in phases))
    return EXIT_OK


def cmd_codes(args: argparse.Namespace) -> int:
    """Print the diagnostic catalogue: one line per code, in numeric order."""
    theme = ui.THEME
    print(ui.heading("diagnostic codes"))
    needle = (args.search or "").lower()
    shown = [
        m for m in ui.MESSAGES
        if m is not ui.FALLBACK
        and (not args.severity or m.severity == args.severity)
        and (not needle or needle in m.code.lower() or needle in m.title.lower()
             or needle in m.hint.lower())
    ]
    if not shown:
        print(ui.muted("  no code matches"))
        return EXIT_OK
    print()
    for message in sorted(shown, key=lambda m: m.code):
        colour = theme.red if message.severity == "error" else theme.yellow
        print(f"  {theme.paint(message.code, colour, theme.bold)}  {message.title}")
        if args.explain and message.hint:
            print(ui.muted(f"        {message.hint.replace('``', '')}"))
    print()
    print(ui.muted(f"  {len(shown)} code(s)"))
    return EXIT_OK


def cmd_features(_args: argparse.Namespace) -> int:
    from .keywords import FEATURES

    print(ui.heading("language features"))
    for feature in sorted(FEATURES):
        print(f"  {ui.THEME.symbol('bullet')} "
              f"{ui.THEME.paint(feature, ui.THEME.bold, ui.THEME.blue)}")
    print(ui.muted("  enable with --enable FEATURE (repeatable)"))
    return EXIT_OK


def _to_json(node):
    if isinstance(node, (tuple, list)):
        return [_to_json(item) for item in node]
    if hasattr(node, "__dataclass_fields__"):
        out = {"kind": type(node).__name__}
        for field in dataclasses.fields(node):
            out[field.name] = _to_json(getattr(node, field.name))
        return out
    return node


def _print_tree(program) -> None:
    for cls in program.classes:
        print(f"{ui.THEME.paint('class', ui.THEME.bold, ui.THEME.magenta)} "
              f"{ui.THEME.paint(cls.name, ui.THEME.bold, ui.THEME.blue)}")
        for fn in cls.funcs:
            params = ", ".join(fn.params)
            print(f"  {ui.muted(fn.visibility)} "
                  f"{ui.THEME.paint(fn.qualified, ui.THEME.cyan)}({params})  "
                  f"{ui.muted(f'[{len(fn.body)} stmt(s)]')}")


# ---------------------------------------------------------------------------
# argument parsing
# ---------------------------------------------------------------------------

EPILOG = """\
examples:
  jswc build examples/hello.jsw -o build/hello.sb3
  jswc decompile project.sb3 -o project.jsw
  jswc check project.jsw --strict
  jswc build project.jsw --enable useTempVariables --enable commentMetadata
  jswc codes --severity warning
  jswc features

exit codes:
  0  success
  1  build failed (structural problems or --strict)
  2  the source could not be read, parsed or compiled
"""


def _add_visual_flags(parser: argparse.ArgumentParser) -> None:
    """``--no-color`` / ``--plain`` / ``--explain`` also after the command name."""
    parser.add_argument("--no-color", action="store_true", default=argparse.SUPPRESS,
                        help="disable ANSI colour")
    parser.add_argument("--plain", action="store_true", default=argparse.SUPPRESS,
                        help="ASCII-only symbols")
    parser.add_argument("--explain", action="store_true", default=argparse.SUPPRESS,
                        help="show the hint line under each diagnostic")
    parser.add_argument("-l", "--loglevel", default=argparse.SUPPRESS, metavar="LEVEL",
                        choices=("error", "warn", "info"),
                        help="diagnostics to print: error, warn (default) or info")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="jswc",
        description=f"{ui.THEME.paint('jswc', ui.THEME.bold, ui.THEME.cyan)} "
                    f"{ui.muted(f'v{__version__}')} — JadenScript ⇄ TurboWarp compiler",
        epilog=EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--version", action="version", version=f"jswc {__version__}")
    parser.add_argument("--no-color", action="store_true",
                        help="disable ANSI colour (NO_COLOR is honoured too)")
    parser.add_argument("--plain", action="store_true",
                        help="ASCII-only symbols, for terminals without Unicode")
    parser.add_argument("-e", "--explain", action="store_true",
                        help="show the hint that explains each diagnostic")
    parser.add_argument("-l", "--loglevel", default="warn", metavar="LEVEL",
                        choices=("error", "warn", "info"),
                        help="diagnostics to print: error, warn (default) or info")
    sub = parser.add_subparsers(dest="command", required=True, metavar="<command>",
                                title="commands")

    build = sub.add_parser("build", help="compile a .jsw file into a .sb3 project",
                           description="Compile JadenScript to a Scratch 3 / TurboWarp project.")
    build.add_argument("source")
    build.add_argument("-o", "--output")
    build.add_argument("--locals", choices=("sprite", "thread"), default="sprite",
                       help="where bare variable names live (default: sprite-local)")
    build.add_argument("--warp", action="store_true",
                       help="run every custom block without screen refresh")
    build.add_argument("--project-json", action="store_true",
                       help="write project.json instead of a zipped .sb3")
    build.add_argument("--enable", action="append", default=[], metavar="FEATURE",
                       help="enable a language feature (useTempVariables, commentMetadata, "
                            "namespacedNames, doObfuscation)")
    build.add_argument("--allow-undefined", action="store_true",
                       help="downgrade undefined-variable errors (needed for legacy sources)")
    build.add_argument("--strict", action="store_true",
                       help="fail when any diagnostic is reported")
    build.add_argument("-v", "--verbose", action="store_true",
                       help="show per-phase timings")
    _add_visual_flags(build)
    build.set_defaults(func=cmd_build)

    decompile = sub.add_parser("decompile", help="turn an .sb3 back into .jsw",
                               description="Recover readable JadenScript from a Scratch project.")
    decompile.add_argument("source")
    decompile.add_argument("-o", "--output")
    decompile.add_argument("--skip-assets", action="store_true",
                           help="do not extract costumes/sounds (keeps .jsa/.jsv state files)")
    decompile.add_argument("--max-inline", type=int, default=50,
                           help="lists/variables longer than this are written to .jsa/.jsv files")
    decompile.add_argument("--assets-prefix", default="assets/",
                           help="folder (next to the output) that receives costumes and sounds")
    decompile.add_argument("-v", "--verbose", action="store_true")
    _add_visual_flags(decompile)
    decompile.set_defaults(func=cmd_decompile)

    check = sub.add_parser("check", help="parse a file and print diagnostics",
                           description="Parse a file without generating a project.")
    check.add_argument("source")
    check.add_argument("--strict", action="store_true",
                       help="exit 1 when any diagnostic is reported")
    check.add_argument("--format", choices=("text", "efm", "json"), default="text",
                       help="output style; 'efm' is one 'file:line:col: T: msg' line each")
    _add_visual_flags(check)
    check.set_defaults(func=cmd_check)

    ast_cmd = sub.add_parser("ast", help="dump the parsed AST")
    ast_cmd.add_argument("source")
    ast_cmd.add_argument("--json", action="store_true")
    _add_visual_flags(ast_cmd)
    ast_cmd.set_defaults(func=cmd_ast)

    tokens = sub.add_parser("tokens", help="dump the token stream")
    tokens.add_argument("source")
    _add_visual_flags(tokens)
    tokens.set_defaults(func=cmd_tokens)

    codes = sub.add_parser("codes", help="list diagnostic codes (Exxxx / Wxxxx)")
    codes.add_argument("--severity", choices=("error", "warning", "info"))
    codes.add_argument("-s", "--search", help="match a code, a title or a hint")
    _add_visual_flags(codes)
    codes.set_defaults(func=cmd_codes)

    features = sub.add_parser("features", help="list language feature flags")
    features.set_defaults(func=cmd_features)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    if argv is None:
        argv = sys.argv[1:]
    if not argv:
        parser.print_help()
        return EXIT_OK
    args = parser.parse_args(argv)
    ui.configure(color=False if args.no_color else None,
                 unicode=False if args.plain else None,
                 explain=bool(getattr(args, "explain", False)),
                 loglevel=getattr(args, "loglevel", "warn"))
    try:
        return args.func(args)
    except JswError as exc:
        text = f"{exc.line}:{exc.col}: {exc.message}" if exc.line else exc.message
        source: str | Source | None = None
        if exc.line:
            try:
                source = _load(args.source)
            except include.IncludeError:
                source = ui.read_source(args.source)
        _err(ui.format_diagnostic(*_locate(text, source, Path(args.source).name)))
        return EXIT_ERROR
    except FileNotFoundError as exc:
        _err(ui.failure(f"{ui.THEME.paint('file not found', ui.THEME.red, ui.THEME.bold)}  "
                        f"{exc.filename or exc}"))
        return EXIT_ERROR
    except KeyboardInterrupt:
        _err(ui.failure("interrupted"))
        return EXIT_INTERRUPTED


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
