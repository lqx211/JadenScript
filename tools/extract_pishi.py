#!/usr/bin/env python3
"""Extract the pishi.ai extension schemas from a project that uses them.

pishi's extensions are built into their own Scratch player (pishi.ai/play), so
there is no `getInfo()` source to parse and no `extensionURLs` entry in a
project that uses them -- the only authoritative description of a block is the
block itself, as the player saved it.

So this reads an `.sb3` and writes ``jswc/pishi_blocks.py``:

* **arguments** (name, type, menu) come from the blocks in the project: the
  input names are the block's own, a menu argument is one whose payload is a
  ``<ext>_menu_<name>`` shadow, and a numeric primitive means the slot takes a
  number. This is exact, and it is what the compiler needs to turn
  ``Facemesh.getX(...)`` into a block with the right inputs.
* **kind** (COMMAND / REPORTER / BOOLEAN / HAT) is not in the project file --
  Scratch derives it from the extension at run time. It comes from two places:
  the player's own bundle when a block is defined with a literal
  ``blockType``, and otherwise from the naming convention every pishi extension
  follows (``get*``/``is*``/``when*``/``*Count``), which the bundle confirms
  for the majority of blocks -- ``--report`` prints the agreement rate and
  every block the two sources disagree about, so a bad guess is visible.

The facts taken from the player's 33 MB bundle are kept in a small sidecar
(next to the project) so a refresh needs neither the download nor the network:
``--write`` reads the sidecar by default, and ``--from-bundle`` rewrites it.

Usage::

    python3 tools/extract_pishi.py examples/PishiExtensions.sb3 --report
    python3 tools/extract_pishi.py examples/PishiExtensions.sb3 --write
    python3 tools/extract_pishi.py examples/PishiExtensions.sb3 \
        --from-bundle /tmp/pishi-probe/gui.js --write-sidecar
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import zipfile
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "jswc" / "pishi_blocks.py"
#: What the player's bundle added to the extraction: authoritative block kinds
#: and argument order. Small enough to commit, which is what makes `--write`
#: reproducible on a machine that has never seen gui.js.
SIDECAR = ROOT / "examples" / "pishi_kinds.json"

#: The namespaces `pishiSupport` turns on, in the order the project lists them.
NAMESPACES = {
    "facemesh": "Facemesh",
    "handpose": "Handpose",
    "posenet": "Posenet",
    "objectdetection": "ObjectDetection",
    "googleteachablemachine": "GoogleTeachableMachine",
    "speechrecognition": "SpeechRecognition",
    "aichatbot": "AIChatBot",
    "arduino": "Arduino",
}

#: A slot filled with one of these primitives takes a number / a string.
MATH_NUM = 4
TEXT = 10

#: Block kinds come from the block's name when the bundle does not say. Every
#: pishi extension names a reporter `get*`/`list*`/`last*`/`active*`, a boolean
#: `is*`/`has*`, a hat `when*`; the rest are commands. `--report` prints where
#: the names disagree with the bundle, which is how the rule was tuned: a suffix
#: rule (`*Image`, `*Interval`) was wrong, because `selectInputImage` and
#: `setClassificationInterval` are commands.
REPORTER_PREFIXES = ("get", "list", "last", "active", "read")
BOOLEAN_PREFIXES = ("is", "has")
REPORTER_SUFFIXES = ("Count", "Status")
HAT_PREFIXES = ("when",)


def kind_from_name(opcode: str) -> str:
    if opcode.startswith(HAT_PREFIXES):
        return "HAT"
    if opcode.startswith(BOOLEAN_PREFIXES):
        return "BOOLEAN"
    if opcode.startswith(REPORTER_PREFIXES) or opcode.endswith(REPORTER_SUFFIXES):
        return "REPORTER"
    return "COMMAND"


def read_project(path: Path) -> dict:
    with zipfile.ZipFile(path) as archive:
        return json.loads(archive.read("project.json"))


def blocks_of(project: dict) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for target in project["targets"]:
        for ident, block in target["blocks"].items():
            if isinstance(block, dict):
                out[ident] = block
    return out


#: Blocks the two sources disagree about, decided by hand from what the block
#: does. `--report` prints a disagreement list; these are the rows where the
#: bundle's global-by-name lookup picked up another extension's block.
KIND_OVERRIDES = {
    "lastAnswer": ("REPORTER", "the last answer is a value, not an action"),
    "i2cWrite": ("COMMAND", "writing to the bus does not return anything"),
    "isAnalogInRange": ("BOOLEAN", "`is*` is a condition in every pishi extension"),
    "readPinSmart": ("REPORTER", "reading a pin returns the pin's value"),
}


def _literal_keys(body: str) -> list[str]:
    """Top-level keys of an object literal, in the order they are written."""
    keys, depth = [], 0
    i = 0
    while i < len(body):
        ch = body[i]
        if ch in "{[(":
            depth += 1
        elif ch in "}])":
            depth -= 1
        elif ch == "'" and depth == 0:
            end = body.find("'", i + 1)
            if end == -1:
                break
            word = body[i + 1:end]
            if body[end + 1:end + 2] == ":":
                keys.append(word)
            i = end
        i += 1
    return keys


def bundle_kinds(source: str) -> tuple[dict[str, str], dict[str, list[str]]]:
    """``opcode -> kind`` and ``opcode -> argument order`` from the bundle.

    The bundle is minified, but a block definition is still one object literal:

    ``{'opcode':'getX', 'text':…, 'blockType':…['REPORTER'], 'arguments':{…}}``.
    So the search stops at the next ``'opcode':`` -- a marker found beyond that
    belongs to a different block, which is how ``getSoundLabel`` first came back
    as a hat. An opcode defined twice with different kinds is left out rather
    than guessed: the name rule has a better chance than a coin toss.
    """
    marker = re.compile(r"\['(COMMAND|REPORTER|BOOLEAN|HAT)'\]")
    found: dict[str, set[str]] = defaultdict(set)
    orders: dict[str, set[tuple[str, ...]]] = defaultdict(set)
    starts = [m for m in re.finditer(r"'opcode':'([A-Za-z_][A-Za-z0-9_]*)'", source)]
    for index, match in enumerate(starts):
        end = starts[index + 1].start() if index + 1 < len(starts) else len(source)
        window = source[match.end():min(end, match.end() + 1200)]
        kind = marker.search(window)
        if kind:
            found[match.group(1)].add(kind.group(1))
        arguments = re.search(r"'arguments':\{", window)
        if arguments:
            depth, start = 0, arguments.end() - 1
            for offset in range(start, min(len(window), start + 800)):
                if window[offset] == "{":
                    depth += 1
                elif window[offset] == "}":
                    depth -= 1
                    if depth == 0:
                        keys = tuple(_literal_keys(window[start + 1:offset]))
                        if keys:
                            orders[match.group(1)].add(keys)
                        break
    kinds = {name: kinds.pop() for name, kinds in found.items() if len(kinds) == 1}
    order = {name: list(next(iter(seen))) for name, seen in orders.items() if len(seen) == 1}
    return kinds, order


def signature(block: dict, blocks: dict[str, dict]) -> tuple[tuple[str, str, str], ...]:
    """``(name, type, menu)`` for each input, in the order the block declares."""
    args = []
    for name, raw in (block.get("inputs") or {}).items():
        kind, menu, prim = "STRING", "", None
        payload = raw[1] if len(raw) > 1 else None
        if isinstance(payload, str):
            inner = blocks.get(payload)
            if inner is not None and "_menu_" in inner["opcode"]:
                menu = inner["opcode"].split("_menu_", 1)[1]
            elif inner is not None:
                prim = "block"
        elif isinstance(payload, list) and payload:
            prim = payload[0]
        if menu:
            kind = "STRING"
        elif prim == MATH_NUM:
            kind = "NUMBER"
        args.append([name, kind, menu])
    return tuple(tuple(arg) for arg in args)


def collect(path: Path, kinds: dict[str, str],
            order: dict[str, list[str]]) -> tuple[dict, list[str]]:
    project = read_project(path)
    blocks = blocks_of(project)
    per_opcode: dict[str, list] = defaultdict(list)
    for block in blocks.values():
        extension = block["opcode"].split("_", 1)[0]
        if extension not in NAMESPACES:
            continue
        if "_menu_" in block["opcode"]:
            continue                     # a dropdown, described by its argument
        per_opcode[block["opcode"]].append(block)

    catalogue: dict[str, dict] = {}
    disagreements: list[str] = []
    for opcode, uses in sorted(per_opcode.items()):
        extension, short = opcode.split("_", 1)
        signature_of = signature(uses[0], blocks)
        for other in uses[1:]:
            if signature(other, blocks) != signature_of:
                # Two shapes for one opcode: keep the first and say so, because
                # a wrong arity is worse than a warning at generation time.
                disagreements.append(
                    f"{opcode}: {[a[0] for a in signature_of]} vs "
                    f"{[a[0] for a in signature(other, blocks)]}"
                )
        # The project cannot tell a hat from a loose stack of commands: both are
        # top-level with a `next`. Only the name and the bundle can, so a block
        # is a hat only when it is called `when*` or the bundle says so.
        guessed = kind_from_name(short)
        known = kinds.get(short)
        if known and known != guessed:
            disagreements.append(f"{opcode}: bundle says {known}, name says {guessed}")
        override = KIND_OVERRIDES.get(short)
        if override:
            if not known or known == override[0]:
                disagreements.append(f"{opcode}: override {override[0]} is stale")
            known = override[0]
        # The bundle writes the arguments in the block's own order; when the
        # names agree, that order is better than the project's input order.
        wanted = order.get(short)
        if wanted and sorted(wanted) == sorted(arg[0] for arg in signature_of):
            rank = {name: index for index, name in enumerate(wanted)}
            signature_of = tuple(sorted(signature_of, key=lambda arg: rank[arg[0]]))
        entry = catalogue.setdefault(extension, {"blocks": [], "menus": set()})
        entry["blocks"].append({
            "opcode": short,
            "type": known or guessed,
            "args": [list(arg) for arg in signature_of],
        })
        for _, _, menu in signature_of:
            if menu:
                entry["menus"].add(menu)
    return catalogue, disagreements


def render(catalogue: dict, source: Path) -> str:
    namespaces = {NAMESPACES[ext]: ext for ext in catalogue}
    lines = [
        '"""Generated by tools/extract_pishi.py -- do not edit by hand.',
        "",
        f"Block metadata for the pishi.ai extensions, read from `{source.name}`.",
        "Regenerate with: `python3 tools/extract_pishi.py",
        f"{source} --write`.",
        '"""',
        "",
        "from __future__ import annotations",
        "",
        "# extension id -> {namespace, blocks: {opcode: {type, args}}}",
        "PISHI_BLOCKS: dict[str, dict] = {",
    ]
    for extension in sorted(catalogue, key=lambda e: NAMESPACES[e]):
        entry = catalogue[extension]
        lines.append(f"    # {NAMESPACES[extension]}.* -- {len(entry['blocks'])} blocks")
        lines.append(f'    "{extension}": {{')
        lines.append(f'        "namespace": "{NAMESPACES[extension]}",')
        lines.append('        "blocks": {')
        for block in sorted(entry["blocks"], key=lambda b: b["opcode"]):
            args = ", ".join(
                f'("{name}", "{kind}", "{menu}")' for name, kind, menu in block["args"]
            )
            lines.append(
                f'            "{block["opcode"]}": {{"type": "{block["type"]}", '
                f'"args": [{args}]}},'
            )
        lines.append("        },")
        if entry["menus"]:
            lines.append('        "menus": {')
            for menu in sorted(entry["menus"]):
                lines.append(f'            "{menu}": (),')
            lines.append("        },")
        lines.append("    },")
    lines.append("}")
    lines.append("")
    return "\n".join(lines)


def load_sidecar(path: Path) -> tuple[dict[str, str], dict[str, list[str]]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return data.get("kinds", {}), data.get("orders", {})


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("project", help="an .sb3 that uses the pishi extensions")
    parser.add_argument("--from-bundle", metavar="GUI_JS",
                        help="re-read the kinds from the player's bundle instead of the sidecar")
    parser.add_argument("--write-sidecar", action="store_true",
                        help=f"write {SIDECAR.relative_to(ROOT)}")
    parser.add_argument("--write", action="store_true", help=f"write {OUT.name}")
    parser.add_argument("--report", action="store_true", help="print what was found")
    args = parser.parse_args(argv)

    source = Path(args.project).expanduser()
    kinds: dict[str, str] = {}
    order: dict[str, list[str]] = {}
    if args.from_bundle:
        bundle = Path(args.from_bundle).expanduser()
        kinds, order = bundle_kinds(bundle.read_text(encoding="utf-8", errors="ignore"))
    elif SIDECAR.exists():
        kinds, order = load_sidecar(SIDECAR)
    if args.write_sidecar:
        SIDECAR.write_text(
            json.dumps({"kinds": kinds, "orders": order}, indent=1, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        print(f"wrote {SIDECAR.relative_to(ROOT)}: {len(kinds)} kinds, {len(order)} orders")
    catalogue, disagreements = collect(source, kinds, order)
    total = sum(len(entry["blocks"]) for entry in catalogue.values())
    menus = sum(len(entry["menus"]) for entry in catalogue.values())
    print(f"{source.name}: {len(catalogue)} extensions, {total} blocks, {menus} menus, "
          f"{len(kinds)} block kinds read from the bundle, "
          f"{len(order)} argument orders")
    if args.report:
        for extension in sorted(catalogue, key=lambda e: NAMESPACES[e]):
            entry = catalogue[extension]
            print(f"  {NAMESPACES[extension]:22} {len(entry['blocks']):3} blocks, "
                  f"{len(entry['menus'])} menus")
        for line in disagreements:
            print(f"  ! {line}")
    if args.write:
        OUT.write_text(render(catalogue, source), encoding="utf-8")
        print(f"wrote {OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
