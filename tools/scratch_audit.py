#!/usr/bin/env python3
"""Can scratch.mit.edu accept this project? Say what would stop it.

    python3 tools/scratch_audit.py build/np.sb3
    python3 tools/scratch_audit.py build/np.sb3 --json
    python3 tools/scratch_audit.py build/np.sb3 --vm scratch-vm-develop

scratch.mit.edu rejects a project for three reasons, and they are all
mechanical, so none of them needs a guess:

* **an extension it does not have** — `extensions` must be built-ins only, and
  `extensionURLs` (a TurboWarp field) must not be there at all;
* **a block its VM cannot run** — the block may come from an extension, or it
  may be a TurboWarp addition to a built-in one (`control_while`,
  `control_for_each`, the counter blocks);
* **size** — the project file has 50 MB, and a single asset 10 MB.

The opcode list is read out of the VM's own source when one is available, so a
block that a newer Scratch added is not reported as foreign. The count per
opcode is what matters: one `control_while` is a curiosity, seventy-seven is
a rewrite with a shape.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import zipfile
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

#: TurboWarp's additions to the built-in extensions. They are spelled with a
#: built-in prefix, so nothing but a list keeps them out.
TURBOWARP_ONLY = frozenset({
    "control_while",
    "control_for_each",
    "control_all_at_once",
    "control_clear_counter",
    "control_get_counter",
    "control_incr_counter",
})

#: Opcodes that are vanilla but are not named in a block file: the VM builds
#: them as the *shadow* of an argument type (a note, a costume, a sound, a
#: colour) or as the prototype half of a custom block. Reading only
#: ``opcode:`` lines reports all of these as foreign, which makes the audit
#: look ten times worse than the project is.
VANILLA_SHADOWS = frozenset({
    "math_number", "math_positive_number", "math_whole_number", "math_integer",
    "math_angle", "text", "colour_picker", "matrix", "note",
    "looks_costume", "looks_backdrops", "sound_sounds_menu",
    "sensing_keyoptions", "sensing_of_object_menu", "sensing_touchingobjectmenu",
    "sensing_distancetomenu", "control_create_clone_of_menu",
    "event_broadcast_menu", "music_menu_DRUM", "music_menu_INSTRUMENT",
    "procedures_prototype", "procedures_definition", "procedures_call",
    "argument_reporter_string_number", "argument_reporter_boolean",
})

#: Extensions scratch.mit.edu ships. Anything else has to go.
SCRATCH_EXTENSIONS = frozenset({
    "pen", "music", "video", "text2speech", "translate",
    "makeymakey", "microbit", "ev3", "boost", "wedo2", "gdxfor",
})

PROJECT_LIMIT = 50 * 1024 * 1024
ASSET_LIMIT = 10 * 1024 * 1024

#: A core block file declares `operator_add: this.add`. An extension declares
#: `opcode: 'clear'`, so the two are read with two patterns rather than one
#: loose one that matches neither.
_OPCODE_RE = re.compile(r"^\s+([a-z][A-Za-z0-9]*_[A-Za-z0-9_]+)\s*:", re.MULTILINE)
_EXT_OPCODE_RE = re.compile(r"\bopcode:\s*'([^']+)'")
_MENUS_RE = re.compile(r"\bmenus:\s*\{")


def extension_menus(text: str) -> set[str]:
    """Menu names an extension declares under ``menus: { … }``.

    A dropdown is not an ``opcode:`` entry: the VM builds `<id>_menu_<name>`
    for each key at load time (``runtime._makeExtensionMenuId``), so a block
    like the pen's ``pen_menu_colorParam`` is real even though no source line
    spells it out. Missing these made the audit call a working pen project
    unportable.
    """
    names: set[str] = set()
    for match in _MENUS_RE.finditer(text):
        depth = 1
        index = match.end()
        start = index
        while index < len(text) and depth:
            if text[index] == "{":
                depth += 1
            elif text[index] == "}":
                depth -= 1
            index += 1
        body = text[start : index - 1]
        level = 0
        for line in body.splitlines():
            stripped = line.strip()
            if level == 0:
                key = re.match(r"([A-Za-z_][A-Za-z0-9_]*)\s*:\s*\{", stripped)
                if key:
                    names.add(key.group(1))
            level += line.count("{") - line.count("}")
    return names


def vm_opcodes(vm: Path) -> set[str]:
    """Every opcode the VM implements, read out of its block sources.

    A core block file names its opcodes in full (`operator_add`). An extension
    names only the tail (`clear`) and the VM builds `<id>_<tail>` at load time,
    so the prefix has to be put back or every pen and music block looks like
    something Scratch cannot run.
    """
    found: set[str] = set()
    src = vm / "src"
    for path in src.glob("blocks/scratch3_*.js"):
        found |= set(_OPCODE_RE.findall(path.read_text(errors="ignore")))
    for path in src.glob("extensions/*/index.js"):
        text = path.read_text(errors="ignore")
        # the extension's *own* id is the one right after `getInfo () {`, not
        # the first `id:` in the file -- a colour menu declares `id: 'pen.colorMenu.color'`
        info = text[text.find("getInfo"):] if "getInfo" in text else text
        category = re.search(r"id:\s*'([^']+)'", info)
        prefix = category.group(1) if category else path.parent.name.split("_")[-1]
        for tail in set(_EXT_OPCODE_RE.findall(text)):
            found.add(f"{prefix}_{tail}")
        for menu in extension_menus(text):
            found.add(f"{prefix}_menu_{menu}")
    return found - TURBOWARP_ONLY


def load(path: Path) -> tuple[dict, dict[str, int]]:
    """The project, and the size of every asset the zip really holds."""
    with zipfile.ZipFile(path) as archive:
        data = json.loads(archive.read("project.json"))
        sizes = {i.filename: i.file_size for i in archive.infolist()}
    return data, sizes


def audit(path: Path, vm: Path | None) -> dict:
    data, sizes = load(path)
    vanilla = vm_opcodes(vm) if vm is not None else set()

    declared = list(data.get("extensions") or [])
    urls = dict(data.get("extensionURLs") or {})
    foreign_ext = [e for e in declared if e not in SCRATCH_EXTENSIONS]

    per_opcode: Counter[str] = Counter()
    where: defaultdict[str, set[str]] = defaultdict(set)
    total_blocks = 0
    for target in data["targets"]:
        for block in target["blocks"].values():
            if not isinstance(block, dict):
                continue
            total_blocks += 1
            opcode = block["opcode"]
            per_opcode[opcode] += 1
            where[opcode].add(target["name"])

    # An opcode is foreign when the VM does not know it. With no VM to ask,
    # fall back on the namespace: an extension's blocks carry its id.
    def foreign(opcode: str) -> bool:
        if opcode in TURBOWARP_ONLY:
            return True
        if opcode in VANILLA_SHADOWS:
            return False
        if vanilla:
            return opcode not in vanilla
        prefix = opcode.split("_", 1)[0]
        return prefix not in {
            "operator", "data", "event", "control", "sensing", "looks",
            "motion", "sound", "pen", "procedures", "argument", "music",
        }

    blockers = {o: c for o, c in per_opcode.items() if foreign(o)}

    # The schema pins the first target to the Stage and its name to the literal
    # "Stage" (`sb3_definitions.json: stage.properties.name.enum`). Anything
    # else and scratch.mit.edu answers "Could not parse as a valid SB2 or SB3
    # project" without saying which project it means -- obfuscation used to
    # rename the Stage, which is how this check came about.
    targets = data.get("targets") or []
    stage_problems: list[str] = []
    if not targets:
        stage_problems.append("no targets at all")
    else:
        first = targets[0]
        if not first.get("isStage"):
            stage_problems.append(f"targets[0] is {first.get('name')!r}, not the Stage")
        if first.get("name") != "Stage":
            stage_problems.append(
                f"targets[0].name is {first.get('name')!r}; the sb3 schema allows only 'Stage'"
            )

    biggest: list[tuple[int, str, str]] = []
    total_size = 0
    for target in data["targets"]:
        for kind in ("costumes", "sounds"):
            for asset in target.get(kind, []):
                key = asset.get("md5ext")
                size = sizes.get(key or "", 0)
                total_size += size
                if size > ASSET_LIMIT:
                    biggest.append((size, f"{target['name']}/{asset.get('name')}", key or ""))

    return {
        "path": str(path),
        "bytes": path.stat().st_size,
        # every asset the project references, once: a costume shared by two
        # sprites is one asset, and a set of *sizes* counted two empty ones
        # as one
        "uncompressed": sum({name: size for name, size in sizes.items()
                             if name != "project.json"}.values()),
        "blocks": total_blocks,
        "extensions": declared,
        "foreign_extensions": foreign_ext,
        "extension_urls": urls,
        "blockers": blockers,
        "stage_problems": stage_problems,
        "blocker_total": sum(blockers.values()),
        "blocker_where": {o: sorted(where[o]) for o in blockers},
        "oversized_assets": biggest,
        "vm": str(vm) if vm else None,
    }


def report(result: dict) -> str:
    lines: list[str] = []
    ok = True
    mb = result["uncompressed"] / 1e6

    lines.append(f"{result['path']}")
    lines.append(f"  {result['blocks']:,} blocks, {mb:.1f} MB of assets, "
                 f"{result['bytes'] / 1e6:.1f} MB on disk")

    if result["stage_problems"]:
        ok = False
        for problem in result["stage_problems"]:
            lines.append(f"  ✖ {problem}")
    if result["extension_urls"]:
        ok = False
        lines.append(f"  ✖ {len(result['extension_urls'])} extension URL(s) — "
                     "Scratch has no such field:")
        for name, url in sorted(result["extension_urls"].items()):
            lines.append(f"      {name}: {url}")
    if result["foreign_extensions"]:
        ok = False
        lines.append(f"  ✖ {len(result['foreign_extensions'])} extension(s) Scratch "
                     "does not ship: " + ", ".join(sorted(result["foreign_extensions"])))

    if result["blocker_total"]:
        ok = False
        lines.append(f"  ✖ {result['blocker_total']:,} blocks a vanilla VM cannot run "
                     f"({len(result['blockers'])} kinds):")
        for opcode, count in sorted(result["blockers"].items(), key=lambda kv: -kv[1]):
            targets = ", ".join(result["blocker_where"][opcode])
            lines.append(f"      {count:6,}  {opcode:36s} in {targets}")
    else:
        lines.append("  ✔ every block is one a vanilla Scratch VM implements")

    if result["uncompressed"] > PROJECT_LIMIT:
        ok = False
        lines.append(f"  ✖ {mb:.1f} MB of assets; the project limit is 50 MB")
    if result["oversized_assets"]:
        ok = False
        for size, name, _ in result["oversized_assets"][:10]:
            lines.append(f"  ✖ {size / 1e6:.1f} MB asset, limit 10 MB: {name}")

    lines.append("")
    lines.append("  ✔ ready to upload" if ok else "  ✖ Scratch would reject this")
    return "\n".join(lines)


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("project", type=Path, help="an .sb3 to inspect")
    ap.add_argument("--vm", type=Path, default=None,
                    help="a scratch-vm checkout, for its real opcode list")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    args = ap.parse_args(argv)

    if args.vm is not None and not (args.vm / "src").is_dir():
        ap.error(f"{args.vm} is not a scratch-vm checkout")

    result = audit(args.project, args.vm)
    if args.json:
        print(json.dumps(result, indent=1))
    else:
        print(report(result))
    ready = (not result["foreign_extensions"] and not result["blockers"]
             and not result["extension_urls"]
             and result["uncompressed"] <= PROJECT_LIMIT
             and not result["oversized_assets"])
    return 0 if ready else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
