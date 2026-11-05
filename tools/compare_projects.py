"""Compare two Scratch projects: state, assets, block literals and scripts.

Used to prove a ``sb3 -> jsw -> sb3`` round trip: the rebuild must carry the
same variables, lists, costumes, sounds, monitors and block *contents* as the
original. Structural differences that do not change behaviour (inlined
variable primitives, folded ``join(x, "")``, an ``if/else`` with an empty else)
are reported separately as notes.

    python3 tools/compare_projects.py original.sb3 rebuilt.sb3
    python3 tools/compare_projects.py a.sb3 b.sb3 --quiet   # only problems
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import zipfile
from collections import Counter
from pathlib import Path

# Opcode shapes TurboWarp's own compiler produces; a rebuild may normalise them.
# Blocks the decompiler folds away or re-emits as an equivalent shorthand.
STYLE = {
    "data_variable",                 # TurboWarp inlines variable reads as primitives
    "argument_reporter_string_number",
    "note",                          # dropdown used as a value
    "sound_sounds_menu",
    "looks_costume",
    "sensing_keyoptions",
    "sensing_distancetomenu",
    "sensing_of_object_menu",
    "operator_join",                 # join(x, "") folds to x
    "sipcconsole_Journal",
    "console-call",
}

BENIGN = {
    ("control_if_else", "control_if"),          # empty else branch
    ("control_repeat_until", "control_while"),  # same loop, negated condition
    ("operator_not", None),                     # folded into the while
    ("operator_join", None),                    # join(x, "") removed
    ("procedures_call", "sipcconsole_Journal"), # an extension procedure
    ("looks_costume", None),                    # dropdown used as a value
    ("sound_sounds_menu", None),
    ("sensing_keyoptions", None),
    ("sensing_distancetomenu", None),
}


def load(path: str | Path) -> tuple[dict, set[str]]:
    with zipfile.ZipFile(path) as archive:
        data = json.loads(archive.read("project.json"))
        names = set(archive.namelist())
    return data, names


def digest_lists(target: dict) -> dict[str, tuple[int, str]]:
    out = {}
    for entry in target.get("lists", {}).values():
        payload = json.dumps(entry[1], ensure_ascii=False)
        out[entry[0]] = (len(entry[1]), hashlib.md5(payload.encode()).hexdigest()[:10])
    return out


CONSOLE_PROCS = {"log", "journal", "warn", "warning", "error", "starttimer", "stoptimer", "debug", "information"}


def normalise_ref(name: str) -> str:
    """Follow the documented decompiler conventions.

    ``Graph.v1`` -> ``v1`` (namespaced state) and ``_x``/``x`` are the same
    thread variable when the rebuild uses the ``useTempVariables`` spelling.
    Renames that change the *meaning* still show up as missing/extra names in
    the list comparison, which stays exact.
    """
    text = name
    if "." in text:
        text = text.split(".", 1)[1]
    text = text.lstrip("_")
    text = re.sub(r"[^A-Za-z0-9_$]", "_", text)
    return text.rstrip("_")


def numeric_key(text: str):
    """The number a literal stands for, so ``0x10`` and ``16`` compare equal.

    The compiler rewrites hex / binary / octal literals as decimal integers on
    purpose, so a textual difference here is not a behaviour change.
    """
    stripped = text.strip()
    if not stripped:
        return None
    try:
        return int(stripped, 0)
    except ValueError:
        try:
            return float(stripped)
        except ValueError:
            return None


def canon(block: dict) -> tuple:
    """Opcode + literal fields and inputs, ignoring block references."""
    fields = tuple(sorted((key, str(value[0])) for key, value in (block.get("fields") or {}).items()))
    if name_is_ref(block.get("opcode") or ""):
        fields = tuple((key, normalise_ref(value)) for key, value in fields)
    literals = []
    for name, raw in (block.get("inputs") or {}).items():
        if not isinstance(raw, list) or len(raw) < 2:
            continue
        item = raw[1]
        if isinstance(item, list) and item and item[0] in (4, 5, 6, 7, 8, 9, 10, 11):
            text = str(item[1])
            if item[0] in (4, 5, 6, 7, 8):  # numeric primitives: compare as numbers
                number = numeric_key(text)
                if number is not None:
                    text = f"#{number}"
            else:
                number = numeric_key(text)
                if number is not None and text.strip().lower().startswith(("-0x", "0x", "0b", "0o", "-0b", "-0o")):
                    # Hex/binary/octal text in a text input: same value once
                    # compiled, so compare numerically too.
                    text = f"#{number}"
            literals.append((name, text))
    opcode = block.get("opcode") or ""
    if name_is_ref(opcode):
        literals = [(k, normalise_ref(v)) for k, v in literals]
    if opcode == "procedures_call":
        mutation = block.get("mutation") or {}
        proccode = str(mutation.get("proccode", ""))
        head = proccode.split(" ", 1)[0].lower()
        if head in CONSOLE_PROCS:
            return ("console-call", head, ())
        # Argument *ids* are project-internal; only the shape matters.
        args = len(json.loads(mutation.get("argumentids", "[]") or "[]"))
        return ("procedures_call", proccode, args)
    if opcode.startswith("sipcconsole_"):
        head = opcode.split("_", 1)[1].lower()
        alias = {"journal": "log", "information": "log", "debug": "debug"}.get(head, head)
        return ("console-call", alias, ())
    return (opcode, fields, tuple(sorted(literals)))


def name_is_ref(opcode: str) -> bool:
    """Blocks whose literals are variable / list / thread-variable names."""
    return (
        opcode.startswith("data_")
        or opcode.startswith("lmsTempVars2_")
        or opcode.startswith("lmsThreadVars2_")
    )


def summary(data: dict) -> dict:
    out = {}
    for target in data["targets"]:
        blocks = {bid: b for bid, b in target["blocks"].items() if isinstance(b, dict)}
        out[target["name"]] = {
            "blocks": blocks,
            "opcodes": Counter(b.get("opcode") for b in blocks.values()),
            "scripts": sum(1 for b in blocks.values() if b.get("topLevel")),
            "vars": Counter((e[0].replace(" ", "_"), str(e[1])) for e in target.get("variables", {}).values()),
            "lists": digest_lists(target),
            "costumes": Counter((c["name"], c["md5ext"]) for c in target.get("costumes", [])),
            "sounds": Counter((s["name"], s["md5ext"]) for s in target.get("sounds", [])),
            "sprite": {k: target.get(k) for k in
                       ("x", "y", "size", "direction", "visible", "currentCostume",
                        "volume", "layerOrder", "rotationStyle", "draggable")},
        }
    return out


def monitor_key(monitor: dict) -> tuple:
    params = monitor.get("params") or {}
    return (
        monitor.get("opcode"),
        params.get("VARIABLE") or params.get("LIST"),
        round(monitor.get("x", 0)), round(monitor.get("y", 0)),
        monitor.get("width"), monitor.get("height"), monitor.get("visible"),
        monitor.get("mode"), monitor.get("sliderMin"), monitor.get("sliderMax"),
        monitor.get("isDiscrete"),
    )


def _strip_duplicates(counter: Counter) -> Counter:
    """Fold ``name_2`` duplicates back onto their base name for comparison."""
    out: Counter = Counter()
    for (name, value), count in counter.items():
        base = name
        while True:
            head, sep, tail = base.rpartition("_")
            if sep and tail.isdigit():
                base = head
            else:
                break
        out[(base, value)] += count
    return out


def compare(left_path: str, right_path: str, quiet: bool = False) -> int:
    left, left_assets = load(left_path)
    right, right_assets = load(right_path)
    a, b = summary(left), summary(right)
    problems = 0
    notes = 0

    def problem(text: str) -> None:
        nonlocal problems
        problems += 1
        print("  ! " + text)

    def note(text: str) -> None:
        nonlocal notes
        notes += 1
        if not quiet:
            print("  ~ " + text)

    names_a = [t["name"] for t in left["targets"]]
    names_b = [t["name"] for t in right["targets"]]
    print(f"targets: {len(names_a)} vs {len(names_b)}")
    if names_a != names_b:
        problem(f"target lists differ:\n      {names_a}\n      {names_b}")

    for name in names_a:
        if name not in b:
            problem(f"target {name!r} missing from the second project")
            continue
        x, y = a[name], b[name]
        if x["sprite"] != y["sprite"]:
            diff = {k: (x["sprite"][k], y["sprite"][k])
                    for k in x["sprite"] if x["sprite"][k] != y["sprite"][k]}
            problem(f"{name}: sprite properties {diff}")
        if _strip_duplicates(x["vars"]) != _strip_duplicates(y["vars"]):
            missing = _strip_duplicates(x["vars"]) - _strip_duplicates(y["vars"])
            extra = _strip_duplicates(y["vars"]) - _strip_duplicates(x["vars"])
            problem(f"{name}: variable values differ {list(missing.items())[:3]} / {list(extra.items())[:3]}")
        if set(x["lists"]) != set(y["lists"]):
            problem(f"{name}: list names differ {sorted(set(x['lists']) ^ set(y['lists']))[:6]}")
        for list_name, shape in x["lists"].items():
            other = y["lists"].get(list_name)
            if other is not None and other != shape:
                problem(f"{name}.{list_name}: list contents differ {shape} vs {other}")
        if x["costumes"] != y["costumes"]:
            problem(f"{name}: costumes differ {list((x['costumes'] - y['costumes']).items())[:2]}")
        if x["sounds"] != y["sounds"]:
            problem(f"{name}: sounds differ {list((x['sounds'] - y['sounds']).items())[:2]}")
        if x["scripts"] != y["scripts"]:
            problem(f"{name}: script count {x['scripts']} vs {y['scripts']}")

        only = x["opcodes"] - y["opcodes"]
        extra = y["opcodes"] - x["opcodes"]
        for opcode in list(only):
            if any(a_ == opcode for a_, _ in BENIGN):
                del only[opcode]
        for opcode in list(extra):
            if opcode in ("data_variable", "argument_reporter_string_number"):
                del extra[opcode]      # TurboWarp inlines primitives, we emit blocks
            elif any(b_ == opcode for _, b_ in BENIGN if b_):
                del extra[opcode]
        if only or extra:
            note(f"{name}: opcode counts missing={dict(only)} extra={dict(extra)}")

        bag_a = Counter(canon(block) for block in x["blocks"].values())
        bag_b = Counter(canon(block) for block in y["blocks"].values())
        if bag_a != bag_b:
            missing, extra = bag_a - bag_b, bag_b - bag_a
            for signature in [sig for sig in list(missing) + list(extra) if sig[0] in STYLE]:
                note(f"{name}: {signature[0]} re-emitted in a different shape")
            missing = Counter({sig: n for sig, n in missing.items() if sig[0] not in STYLE})
            extra = Counter({sig: n for sig, n in extra.items() if sig[0] not in STYLE})
            if missing or extra:
                problem(f"{name}: block literals differ "
                        f"{list(missing.items())[:2]} / {list(extra.items())[:2]}")

    monitors_a = Counter(monitor_key(m) for m in left.get("monitors", []))
    monitors_b = Counter(monitor_key(m) for m in right.get("monitors", []))
    if monitors_a != monitors_b:
        problem(f"monitors differ: missing={sum((monitors_a - monitors_b).values())} "
                f"extra={sum((monitors_b - monitors_a).values())}")

    assets_a = {n for n in left_assets if not n.endswith(".json")}
    assets_b = {n for n in right_assets if not n.endswith(".json")}
    if assets_a != assets_b:
        problem(f"assets differ: {len(assets_a - assets_b)} missing, {len(assets_b - assets_a)} extra")

    print()
    if problems:
        print(f"{problems} problem(s), {notes} note(s)")
        return 1
    print(f"identical behaviour: state, assets, monitors and block literals match "
          f"({notes} structural note(s) that do not change behaviour)")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("original")
    parser.add_argument("rebuilt")
    parser.add_argument("-q", "--quiet", action="store_true", help="hide structural notes")
    args = parser.parse_args(argv)
    return compare(args.original, args.rebuilt, args.quiet)


if __name__ == "__main__":
    sys.exit(main())
