"""``doObfuscation``: replace every name the project exposes.

Publishing a compiled project shows its sprite names, variable names, list
names, custom block labels and broadcast names in the editor. This pass rewrites
all of them, and then fills the workspace with things that are not real: decoy
variables and lists, decoy custom blocks nobody calls (with arguments, like a
real one), string pools with junk shuffled into them, and -- optionally -- a
state machine where a straight script used to be.

The hidden names are numbers, from three counters:

===============  ===========================================================
object           name
===============  ===========================================================
variable, list   ``v0``, ``v1``, … — **one** counter for every variable and
                 every list, in every sprite and on the Stage
private block    ``p0``, ``p1``, … — one per custom block label
broadcast        ``c0``, ``c1``, … — one per message
===============  ===========================================================

Why numbers: a *dotted* name (``Player.score``) still says which class it
belongs to, and a word tells you what to look for. ``v17`` says nothing -- the
counter is shared by every scope, so a Stage global sits right next to a
sprite-local and neither the spelling nor the position says where a name came
from. The numbers are handed out in shuffled order as well, so the decoy objects
are not simply "the ones at the end of the list".

What is *not* renamed, because Scratch matches it by text from the asset list or
from the source file:

* costume and sound names (menus and the ``costumes``/``sounds`` arrays point
  at them -- see ``--obf-scrub`` for the metadata that *is* dropped),
* the names inside ``// monitor`` / ``// watch`` comments (those are source
  directives; the monitors they produce *are* renamed).

Broadcast names are renamed, so a project that builds a message at runtime
(``Control.broadcast(join(module, ".prop"))``) cannot find it any more.
``obfuscate_project`` returns the mapping and ``Codegen`` reports the computed
lookups that this breaks.

The output is deterministic: the same source compiled twice produces the same
hidden names, so diffs and golden tests keep working.
"""

from __future__ import annotations

import hashlib
import json
import random
import re

from . import opcodes as O
from .ir import Block, Lit, Project, Ref, Target

_SEED = "jswc-hide-names"
_LETTERS = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"
_BODY = _LETTERS + "0123456789"


# ---------------------------------------------------------------------------
# Names that are still words: targets, block arguments, thread variables
# ---------------------------------------------------------------------------
def _word(digest: bytes, length: int) -> str:
    """``length`` characters, starting with a letter so it stays an identifier."""
    head = _LETTERS[digest[0] % len(_LETTERS)]
    body = "".join(_BODY[b % len(_BODY)] for b in digest[1:length])
    return head + body


class _WordNamer:
    """Deterministic word names for the few labels that cannot be numbers."""

    def __init__(self, seed: str):
        self.seed = seed
        self.used: set[str] = set()

    def __call__(self, key: str, length: int = 6) -> str:
        attempt = 0
        while True:
            digest = hashlib.sha256(f"{self.seed}:{key}:{attempt}".encode()).digest()
            name = _word(digest[:length], length)
            if name not in self.used:
                self.used.add(name)
                return name
            attempt += 1


_PLACEHOLDER = re.compile(r"%[sbn]")


def _scatter(count: int, rng: random.Random) -> list[int]:
    """``0 .. count-1`` in a shuffled order.

    The number *is* the name, so shuffling the numbers is what mixes decoys in
    among the real objects: a sprite's third custom block can be ``p17`` while
    an unused decoy takes ``p16``, and nothing about either number says which
    one runs.
    """
    order = list(range(count))
    rng.shuffle(order)
    return order


class _Plan:
    """The name maps, filled in by the numbering passes."""

    def __init__(self, seed: str):
        self.words = _WordNamer(seed)
        self.rng = random.Random(f"{seed}:order")
        self.var_names: dict[str, str] = {}
        self.list_names: dict[str, str] = {}
        self.broadcast_names: dict[str, str] = {}
        self.by_scope: dict[tuple[str, str, str], str] = {}
        self.mapping: dict[str, str] = {}


# ---------------------------------------------------------------------------
# Junk text: real-looking strings that no script ever asks for
# ---------------------------------------------------------------------------
_JUNK_WORDS = (
    "engine", "flag", "costume", "sprite", "scene", "tempo", "sample", "voice",
    "track", "frame", "buffer", "sector", "kernel", "clone", "tick", "channel",
    "patch", "bank", "loop", "reset", "stage", "pen", "note", "mixer",
)


def _junk_string(rng: random.Random) -> str:
    kind = rng.randrange(7)
    if kind == 0:
        return "".join(rng.choice(_LETTERS) for _ in range(rng.randint(4, 9)))
    if kind == 1:
        return f"{rng.choice(_JUNK_WORDS)}{rng.randrange(1000)}"
    if kind == 2:
        return "".join(rng.choice("0123456789abcdef") for _ in range(rng.choice((8, 12, 16, 24))))
    if kind == 3:
        return f"{rng.uniform(-180.0, 180.0):.3f}"
    if kind == 4:
        return f"{rng.choice(_JUNK_WORDS)}_{rng.choice(_JUNK_WORDS)}"
    if kind == 5:
        return str(rng.randrange(1, 10 ** rng.randint(2, 7)))
    return f"{rng.choice(_JUNK_WORDS)} {rng.choice(_JUNK_WORDS)}"


def _junk_strings(rng: random.Random, count: int, exclude=()) -> list[str]:
    taken = set(exclude)
    out: list[str] = []
    while len(out) < count:
        text = _junk_string(rng)
        if text and text not in taken:
            taken.add(text)
            out.append(text)
    return out


# ---------------------------------------------------------------------------
# Trash code: state and blocks that nothing reads
# ---------------------------------------------------------------------------
_HAT_OPCODES = frozenset(
    {
        "event_whenflagclicked",
        "event_whenkeypressed",
        "event_whenthisspriteclicked",
        "event_whenstageclicked",
        "event_whenbackdropswitchesto",
        "event_whengreaterthan",
        "event_whenbroadcastreceived",
        "control_start_as_clone",
    }
)


def _set_var(target: Target, name: str, ident: str, value: object) -> Block:
    block = target.new_block("data_setvariableto")
    block.set_field("VARIABLE", name, ident)
    block.set_input("VALUE", Lit(O.TEXT, str(value)))
    return block


def _set_var_from(target: Target, name: str, ident: str, reporter: Block) -> Block:
    """``set var to <reporter>`` -- the decoy side of using an argument."""
    block = target.new_block("data_setvariableto")
    block.set_field("VARIABLE", name, ident)
    block.set_input("VALUE", Ref(reporter))
    return block


def _change_var(target: Target, name: str, ident: str, value: object) -> Block:
    block = target.new_block("data_changevariableby")
    block.set_field("VARIABLE", name, ident)
    block.set_input("VALUE", Lit(O.MATH_NUM, str(value)))
    return block


def _junk_statement(target: Target, rng: random.Random, variables, lists) -> Block:
    """One plausible statement that touches state nothing else reads."""
    name, ident = rng.choice(variables)
    kind = rng.randrange(5)
    if kind == 0:
        return _set_var(target, name, ident, rng.choice([0, 1, 2, rng.randrange(1, 99)]))
    if kind == 1:
        return _change_var(target, name, ident, rng.choice([1, -1, rng.randrange(1, 9)]))
    if kind == 2:
        repeat = target.new_block("control_repeat")
        repeat.set_input("TIMES", Lit(O.MATH_NUM, str(rng.randint(2, 9))))
        repeat.set_input("SUBSTACK", Ref(_change_var(target, name, ident, 1)))
        return repeat
    if kind == 3:
        compare = target.new_block("operator_gt")
        compare.set_input("OPERAND1", Lit(O.TEXT, str(rng.randint(1, 50))))
        compare.set_input("OPERAND2", Lit(O.TEXT, str(rng.randint(1, 50))))
        branch = target.new_block("control_if_else")
        branch.set_input("CONDITION", Ref(compare))
        branch.set_input("SUBSTACK", Ref(_set_var(target, name, ident, 0)))
        branch.set_input("SUBSTACK2", Ref(_change_var(target, name, ident, 2)))
        return branch
    if lists:
        list_name, list_id = rng.choice(lists)
        join = target.new_block("operator_join")
        join.set_input("STRING1", Lit(O.TEXT, str(rng.randint(0, 99))))
        join.set_input("STRING2", Lit(O.TEXT, str(rng.randint(0, 99))))
        add = target.new_block("data_addtolist")
        add.set_field("LIST", list_name, list_id)
        add.set_input("ITEM", Ref(join))
        return add
    return _change_var(target, name, ident, rng.choice([1, 3]))


#: Words a decoy block's label is built from. The label is renamed to ``p%d``
#: before the project is written, so these are not what hides it -- the *shape*
#: is: a decoy that takes arguments and reads them is not distinguishable from a
#: real helper by looking at the palette or at its definition.
_DECOY_VERBS = (
    "update", "refresh", "advance", "flush", "scan", "apply", "commit", "draw",
    "measure", "collect", "resolve", "settle", "step", "publish",
)
_DECOY_NOUNS = (
    "frame", "buffer", "sector", "channel", "table", "window", "batch", "layer",
    "queue", "tile", "sample", "clock", "block", "region",
)


def _arg_reporter(target: Target, name: str, boolean: bool = False) -> Block:
    block = target.new_block(
        "argument_reporter_boolean" if boolean else "argument_reporter_string_number"
    )
    block.set_field("VALUE", name)
    return block


def _decoy_procedure(
    target: Target,
    project: Project,
    rng: random.Random,
    index: int,
    namer: _WordNamer,
    corrupt: bool = False,
) -> None:
    """A custom block that looks used and is called from nowhere.

    Real blocks in a compiled project take two or three arguments; a workspace
    where the one zero-argument block is also the one nobody calls answers
    "which block is the decoy?" by itself. So a decoy declares arguments of its
    own -- names from the same ``_WordNamer`` a real parameter gets, ``%s`` and
    ``%b`` mixed -- and its body reads them. ``_number_proccodes`` renames the
    label, the parameter names and every reporter that reads them in one pass,
    so nothing is left that says "this one was invented".
    """
    variables = [(str(entry[0]), ident) for ident, entry in target.variables.items()]
    lists = [(str(entry[0]), ident) for ident, entry in target.lists.items()]
    if target is not project.stage:
        variables += [(str(entry[0]), ident) for ident, entry in project.stage.variables.items()]
        lists += [(str(entry[0]), ident) for ident, entry in project.stage.lists.items()]
    if not variables:
        return
    # Declaration order is not stable across processes (see `_number_state`);
    # the ids are.
    variables.sort(key=lambda pair: pair[1])
    lists.sort(key=lambda pair: pair[1])

    arity = rng.choice((1, 1, 2, 2, 3))
    kinds = [rng.choice(("%s", "%b")) for _ in range(arity)]
    param_names = [namer(f"decoy-arg:{index}:{slot}") for slot in range(arity)]
    param_ids = [target.idgen.next(f"decoy-arg:{index}:{slot}") for slot in range(arity)]
    taken = {
        str(block.mutation.get("proccode"))
        for block in target.blocks.values()
        if isinstance(block.mutation, dict)
    }
    while True:
        words = [rng.choice(_DECOY_VERBS), rng.choice(_DECOY_NOUNS)]
        rng.shuffle(words)
        label = " ".join(words + kinds)
        if label not in taken:
            taken.add(label)
            break

    definition = target.new_block("procedures_definition")
    definition.top_level = True
    definition.x = 0
    definition.y = _free_y(target) + 400 * index
    prototype = target.new_block("procedures_prototype")
    prototype.shadow = True
    prototype.mutation = {
        "tagName": "mutation",
        "children": [],
        "proccode": label,
        "argumentids": json.dumps(param_ids),
        "argumentnames": json.dumps(param_names),
        "argumentdefaults": json.dumps(
            ["false" if kind == "%b" else "" for kind in kinds]
        ),
        "warp": "false",
    }
    # Each parameter's own default-value slot: a reporter, as the editor writes
    # it, so the definition block is not a stub with empty sockets. A ``%b``
    # parameter needs a *boolean* reporter: scratch-blocks checks the shape
    # when it loads the workspace and refuses the project otherwise. The
    # corrupt form keeps the string reporter on purpose (see
    # ``corruptBlockPos``).
    for param_id, name, kind in zip(param_ids, param_names, kinds):
        reporter = _arg_reporter(target, name, kind == "%b" and not corrupt)
        reporter.shadow = True
        prototype.inputs[param_id] = [1, reporter.id]
    definition.inputs["custom_block"] = [1, prototype.id]

    string_params = [n for n, kind in zip(param_names, kinds) if kind == "%s"]
    bool_params = [n for n, kind in zip(param_names, kinds) if kind == "%b"]
    head: list[Block] = []
    for _ in range(rng.randint(2, 4)):
        head.append(_junk_statement(target, rng, variables, lists))
        if rng.random() < 0.5 and string_params:
            # Read a parameter, so the arguments are used and not decoration.
            name, ident = rng.choice(variables)
            head.append(_set_var_from(
                target, name, ident,
                _arg_reporter(target, rng.choice(string_params)),
            ))
        elif rng.random() < 0.5 and bool_params:
            # A boolean parameter can only sit in a boolean slot; putting it
            # in ``set var to`` is the same shape error in the other direction.
            test = target.new_block("operator_not")
            test.set_input(
                "OPERAND",
                Ref(_arg_reporter(target, rng.choice(bool_params), True)),
            )
            branch = target.new_block("control_if")
            branch.set_input("CONDITION", Ref(test))
            branch.set_input("SUBSTACK", Ref(_change_var(target, *rng.choice(variables), 1)))
            head.append(branch)
    for first, second in zip(head, head[1:]):
        first.next = second.id
    definition.next = head[0].id


def _free_y(target: Target) -> int:
    """Below every script already in the workspace, so decoys do not cover them."""
    return max((block.y for block in target.blocks.values() if block.top_level), default=-400)


def _add_decoys(
    project: Project, rng: random.Random, namer: _WordNamer, corrupt: bool = False
) -> list[str]:
    """Decoy state and decoy blocks, none of which any script reads.

    Returns the ids of the decoy lists, so they can be grown to the size of the
    string pool once it is known: a pool of two hundred items standing alone
    next to two four-item lists would still be the obvious place to look.

    The names written here are placeholders -- ``_number_state`` turns every
    variable and list into ``v%d`` -- but they are still words an author could
    have typed, so nothing depends on the rename having run for the decoys to
    look like the real thing.
    """
    stage = project.stage
    padded: list[str] = []
    for index in range(2):
        name = f"{rng.choice(_JUNK_WORDS).capitalize()}{index}"
        padded.append(stage.declare_list(name, _junk_strings(rng, rng.randint(4, 9))))
    for index in range(2):
        stage.declare_broadcast(f"{rng.choice(_JUNK_WORDS).capitalize()}{index}")
    for index in range(max(3, len(project.targets))):
        target = project.targets[index % len(project.targets)]
        name = f"{rng.choice(_JUNK_WORDS).capitalize()}{index}"
        target.declare_variable(name, rng.choice([0, 1, ""]))
    for index in range(min(12, len(project.targets) + 3)):
        target = project.targets[index % len(project.targets)]
        _decoy_procedure(target, project, rng, index, namer, corrupt)
    return padded


# ---------------------------------------------------------------------------
# Control flattening: a hat body becomes a random state machine
# ---------------------------------------------------------------------------
#: Placeholder name of the state variable a flattened script dispatches on, and
#: of the variable its dead guards touch. Both are renamed to ``v%d`` like every
#: other variable; the names are here so the pass is readable, and so a build
#: with renaming disabled is still not full of ``_state0``.
_STATE_LABEL = "Phase"
_DECOY_TAIL = "Hold"


def _chain(blocks, start: str | None) -> list[Block]:
    out: list[Block] = []
    seen: set[str] = set()
    current = start
    while current and current not in seen:
        seen.add(current)
        block = blocks.get(current)
        if block is None:
            break
        out.append(block)
        current = block.next
    return out


def _flatten_script(
    target: Target,
    hat: Block,
    chain: list[Block],
    rng: random.Random,
    label: str,
    state_counter: "list[int]",
) -> None:
    """``hat -> s0 -> s1 -> s2`` becomes a chain of guarded states in place.

    ::

        set v9 to 17
        if <17 = v9> { s0; set v9 to 4 }
        if <v9 = 4>  { s1; set v9 to 31 }
        if <v9 = 31> { s2 }

    The guards are evaluated in one pass and each one is true by the time it is
    reached, so the statements run in the same order, in the same frame, as the
    straight script did. That is the whole reason it is written this way: a
    ``repeat until`` loop costs a *frame* per state (Scratch yields at the end
    of every loop iteration) and a self-calling block costs a *step* per call
    (the compiled runtime cannot inline a recursive procedure), and either one
    changes the project's timing -- measured on the i386 machine, where the
    display thread fell a frame behind on the first step.

    It is still a state machine: the state lives in a variable, the numbers are
    scattered, dead guards carrying junk sit among the live ones (one to three
    of them, drawn per script), and either operand of a guard may be the
    variable, so a reader has to track the state to follow the order instead of
    recognising ``if state = 1``.
    """
    state_counter[0] += 1
    state_name = f"{label}{state_counter[0]}"
    state_id = target.declare_variable(state_name, 0)
    decoy_name = f"{label}{state_counter[0]}{_DECOY_TAIL}"
    decoy_id = target.declare_variable(decoy_name, 0)
    # Distinct, unordered numbers: nothing about the chain reads as a sequence.
    numbers = rng.sample(range(0, 6 * len(chain)), len(chain))
    unused = [n for n in range(6 * len(chain), 8 * len(chain)) if n not in numbers]

    def state_reporter() -> Block:
        block = target.new_block("data_variable")
        block.set_field("VARIABLE", state_name, state_id)
        return block

    def set_state(number: int) -> Block:
        block = target.new_block("data_setvariableto")
        block.set_field("VARIABLE", state_name, state_id)
        block.set_input("VALUE", Lit(O.TEXT, str(number)))
        return block

    def guard(number: int, body: Block) -> Block:
        """``if <state = number>`` -- operands swapped half the time."""
        compare = target.new_block("operator_equals")
        first, second = Ref(state_reporter()), Lit(O.TEXT, str(number))
        if rng.random() < 0.5:
            first, second = second, first
        compare.set_input("OPERAND1", first)
        compare.set_input("OPERAND2", second)
        block = target.new_block("control_if")
        block.set_input("CONDITION", Ref(compare))
        block.set_input("SUBSTACK", Ref(body))
        return block

    entry = set_state(numbers[0])
    guards: list[Block] = []
    for position, statement in enumerate(chain):
        statement.next = None
        if position + 1 < len(chain):
            statement.next = set_state(numbers[position + 1]).id
        guards.append(guard(numbers[position], statement))
    # Guards whose number the state can never hold: the junk behind them is
    # never reached, and a reader cannot tell them from the ones that are. They
    # only ever write the decoy variable, so a mistake here cannot steer the
    # machine that does run.
    variables = [(decoy_name, decoy_id)]
    for number in unused[: rng.randint(1, 3)]:
        body = _junk_statement(target, rng, variables, [])
        guards.insert(rng.randrange(len(guards) + 1), guard(number, body))

    body = [entry, *guards]
    for first, second in zip(body, body[1:]):
        first.next = second.id
    hat.next = entry.id


def _flatten_scripts(project: Project, rng: random.Random, cap: int) -> int:
    """Flatten every hat body up to ``cap`` blocks long; returns how many.

    The state variables are numbered from one counter across the whole project,
    so two scripts on the same sprite do not both call their state ``_state0``
    -- that reused name was itself a signature: it said "these two scripts were
    flattened by the same pass".
    """
    if cap <= 0:
        return 0
    flattened = 0
    state_counter = [0]
    for target in project.targets:
        blocks = target.blocks
        candidates = [
            block for block in list(blocks.values())
            if block.top_level and block.opcode in _HAT_OPCODES
        ]
        for hat in candidates:
            chain = _chain(blocks, hat.next)
            if not 3 <= len(chain) <= cap:
                continue
            _flatten_script(target, hat, chain, rng, _STATE_LABEL, state_counter)
            flattened += 1
    return flattened


# ---------------------------------------------------------------------------
# Numbering passes
# ---------------------------------------------------------------------------
def _number_state(project: Project, plan: _Plan) -> None:
    """Every variable and every list, from one counter, in shuffled order."""
    slots: list[tuple[Target, str, str, list]] = []
    for target in project.targets:
        for ident, entry in target.variables.items():
            slots.append((target, "var", ident, entry))
        for ident, entry in target.lists.items():
            slots.append((target, "list", ident, entry))
    # Sorted, because the declaration order of an imported list comes out of a
    # set in codegen: it is the same set every run, but not the same order in
    # every process. The ids are stable, so this is the order to shuffle.
    slots.sort(key=lambda slot: (slot[1], slot[2]))
    for (target, kind, ident, entry), number in zip(slots, _scatter(len(slots), plan.rng)):
        original = str(entry[0])
        hidden = f"v{number}"
        entry[0] = hidden
        plan.mapping[original] = hidden
        plan.by_scope[(kind, target.name, original)] = hidden
        if kind == "var":
            plan.var_names[ident] = hidden
        else:
            plan.list_names[ident] = hidden
    _shuffle_declarations(project, plan.rng)


def _shuffle_declarations(project: Project, rng: random.Random) -> None:
    """Shuffle the order the variables and lists are declared in.

    The numbers are already scattered, but the *order* was still the order the
    source declared them in, so the fifth variable in the file was the fifth one
    the author wrote. Nothing reads a dict position -- the VM addresses state by
    id -- so a shuffled order is free, and it separates the objects that belong
    together."""

    def shuffled(mapping: dict) -> dict:
        items = list(mapping.items())
        rng.shuffle(items)
        return dict(items)

    for target in project.targets:
        target.variables = shuffled(target.variables)
        target.lists = shuffled(target.lists)


def _number_proccodes(project: Project, plan: _Plan) -> None:
    """``Sprite.move %s`` -> ``p3 %s``.

    Every custom block label is a name the editor shows in the palette and in
    "My Blocks"; leaving it readable spells out the call graph of the published
    project. The ``%s``/``%b``/``%n`` placeholders are kept verbatim and in
    order, because Scratch pairs them with ``argumentids`` — only the words
    around them change.
    """
    labels: list[str] = []
    seen: set[str] = set()
    for target in project.targets:
        for block in target.blocks.values():
            mutation = getattr(block, "mutation", None)
            if not isinstance(mutation, dict):
                continue
            proccode = mutation.get("proccode")
            if proccode and proccode not in seen:
                seen.add(proccode)
                labels.append(proccode)
    labels.sort()
    hidden = dict(zip(labels, (f"p{number}" for number in _scatter(len(labels), plan.rng))))

    for target in project.targets:
        for block_id, block in list(target.blocks.items()):
            mutation = getattr(block, "mutation", None)
            if not isinstance(mutation, dict):
                continue
            proccode = mutation.get("proccode")
            if not proccode:
                continue
            body = " ".join(_PLACEHOLDER.findall(proccode))
            full = f"{hidden[proccode]} {body}".strip()
            mutation["proccode"] = full
            names = mutation.get("argumentnames")
            if isinstance(names, str) and names:
                try:
                    original = json.loads(names)
                except ValueError:
                    continue
                if isinstance(original, list) and all(isinstance(n, str) for n in original):
                    renamed = [
                        plan.words(f"argument:{full}:{index}")
                        for index, _name in enumerate(original, 1)
                    ]
                    mutation["argumentnames"] = json.dumps(renamed)
                    if block.opcode == "procedures_prototype":
                        # An argument reporter looks its parameter up *by name*,
                        # so the body has to follow the rename or every
                        # parameter reads as empty.
                        _rename_reporters(target, block_id, dict(zip(original, renamed)))


def _number_broadcasts(project: Project, plan: _Plan) -> None:
    # Sorted like the variable and proccode passes: the ids are stable across
    # processes, the declaration order is a dict's, so this is the order to
    # shuffle. Without it two builds of one source could hand out different
    # numbers for the same message.
    entries = sorted(project.stage.broadcasts.items())
    for (bid, name), number in zip(entries, _scatter(len(entries), plan.rng)):
        hidden = f"c{number}"
        plan.broadcast_names[str(name)] = hidden
        plan.mapping[str(name)] = hidden
        project.stage.broadcasts[bid] = hidden


def _rename_vm_broadcasts(project: Project, names: dict[str, str]) -> None:
    """Follow the rename into a ``vmProtect`` broadcast queue.

    The interpreter queues a message as a text argument to
    ``__vm_queue_broadcast``. That argument is not an ``event_broadcast``
    block, so the normal broadcast rename never sees it; without this the
    receiver's renamed hat and the sender's old literal stop matching.
    """
    for target in project.targets:
        for block in target.blocks.values():
            mutation = getattr(block, "mutation", None)
            proccode = mutation.get("proccode", "") if isinstance(mutation, dict) else ""
            if "__vm_queue_broadcast" not in proccode:
                continue
            for raw in (block.inputs or {}).values():
                if (
                    isinstance(raw, list)
                    and len(raw) > 1
                    and isinstance(raw[1], list)
                    and len(raw[1]) > 1
                ):
                    hidden = names.get(str(raw[1][1]))
                    if hidden is not None:
                        raw[1][1] = hidden


# ---------------------------------------------------------------------------
# The pool: text lives in one list, shuffled, with junk around it
# ---------------------------------------------------------------------------
def _grow_decoy_lists(project: Project, ids: list[str], rng: random.Random, size: int) -> None:
    """Give every decoy list a length drawn from around the pool's."""
    low = max(8, size // 2)
    for ident in ids:
        entry = project.stage.lists.get(ident)
        if entry is None:
            continue
        wanted = rng.randint(low, max(low, size + size // 3))
        have = {str(item) for item in entry[1]}
        room = wanted - len(entry[1])
        if room > 0:
            entry[1].extend(_junk_strings(rng, room, have))
        rng.shuffle(entry[1])


def _fill_pool(
    project: Project,
    list_id: str,
    lookups: list[tuple[Block, str]],
    rng: random.Random,
    junk: int | None,
) -> int:
    """Shuffle the pool, bury it in junk, and rewrite every index that reads it."""
    entry = project.stage.lists.get(list_id)
    if entry is None:
        return 0
    real = [str(item) for item in entry[1]]
    count = junk if junk is not None else max(24, len(real))
    items = real + _junk_strings(rng, count, real)
    rng.shuffle(items)
    position: dict[str, int] = {}
    for index, text in enumerate(items, 1):
        position.setdefault(text, index)
    for block, text in lookups:
        block.set_input("INDEX", Lit(O.MATH_NUM, str(position[text])))
    entry[1] = items
    return len(items) - len(real)


def _scrub(project: Project, rng: random.Random) -> int:
    """Take the editor residue out of a release build.

    ``x``/``y`` on a top-level block are where the author dropped it on the
    canvas: a column of blocks at ``x = 200`` growing downwards is a script
    somebody wrote by hand, and two scripts at the same ``x`` were written
    together. Workspace comments are worse -- they are free text about the
    source, and every ``// comment`` line in it survived into the file.
    Directives are not affected: ``// monitor`` and ``// watch`` become
    ``project.monitors``, not comments.
    """
    scrubbed = 0
    for target in project.targets:
        for block in target.blocks.values():
            if block.top_level:
                block.x = rng.randrange(-1800, 1800)
                block.y = rng.randrange(-1200, 1200)
        scrubbed += sum(len(group) for group in project.comments.values())
    project.comments = {}
    return scrubbed


#: Ids ``corruptBlockPos`` plants. Readable enough that whoever opens the
#: project.json knows the reference was left dangling on purpose, and a prefix
#: the validator can recognise without guessing.
_CORRUPT_PREFIX = "missing_"


def corrupt_block_positions(project: Project, seed: str = _SEED) -> None:
    """``corruptBlockPos``: leave the block graph broken for the editor.

    Anti-tamper, not obfuscation. Every target gets a few *unreachable* blocks
    whose ``next``, ``parent`` and input ids name blocks that do not exist,
    plus a prototype whose ``%b`` parameter holds a string reporter, and every
    top-level block is dropped somewhere random on the canvas.

    The VM only follows references from a hat, so none of the planted blocks
    ever executes: the project still runs. scratch-blocks resolves the whole
    blocks map when it opens the workspace, so the editor reports the missing
    ids, fails to connect the prototype and refuses to load the project. That
    asymmetry is the feature; ``doObfuscation`` on its own writes a project
    both the VM and the editor accept.
    """
    rng = random.Random(f"{seed}:corruptBlockPos")
    for target in project.targets:
        for block in target.blocks.values():
            if block.top_level:
                block.x = rng.randrange(-1800, 1800)
                block.y = rng.randrange(-1200, 1200)
        slug = re.sub(r"\W+", "_", target.name)
        for index in range(3):
            dead = Block(
                id=f"{_CORRUPT_PREFIX}{slug}_{index}",
                opcode="looks_say",
            )
            target.add(dead)
            dead.inputs["MESSAGE"] = [2, f"{_CORRUPT_PREFIX}{slug}_{index}_input"]
            dead.next = f"{_CORRUPT_PREFIX}{slug}_{index}_next"
            dead.parent = f"{_CORRUPT_PREFIX}{slug}_{index}_parent"
        prototype = Block(
            id=f"{_CORRUPT_PREFIX}{slug}_prototype",
            opcode="procedures_prototype",
        )
        target.add(prototype)
        prototype.shadow = True
        argument_id = f"{_CORRUPT_PREFIX}{slug}_argument"
        prototype.mutation = {
            "tagName": "mutation",
            "children": [],
            "proccode": "!missing %b",
            "argumentids": json.dumps([argument_id]),
            "argumentnames": json.dumps(["missing"]),
            "argumentdefaults": json.dumps([""]),
            "warp": "false",
        }
        reporter = Block(
            id=f"{_CORRUPT_PREFIX}{slug}_reporter",
            opcode="argument_reporter_string_number",
        )
        target.add(reporter)
        reporter.set_field("VALUE", "missing")
        reporter.shadow = True
        prototype.inputs[argument_id] = [1, reporter.id]


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def obfuscate_project(
    project: Project,
    seed: str = _SEED,
    *,
    pool: tuple[str, list[tuple[Block, str]]] | list[tuple[str, list[tuple[Block, str]]]] | None = None,
    junk: int | None = None,
    flatten: int = 0,
    scrub: bool = False,
    corrupt: bool = False,
) -> dict[str, str]:
    """Rename every name in place and add the decoys.

    ``pool`` is the string list (or lists) codegen built -- its id, and every
    ``item of list`` reporter that reads it -- so the items can be shuffled with
    junk mixed in and the indices rewritten. ``junk`` is how many junk items to
    add per pool (``None`` matches the number of real ones), ``flatten`` is the
    longest hat body to turn into a state machine (``0`` turns that off),
    ``scrub`` drops editor coordinates and non-directive comments, and
    ``corrupt`` leaves the decoy ``%b`` slots holding string reporters (see
    ``corruptBlockPos``).

    Returns ``original -> hidden`` so callers can report what changed (the
    mapping is many-to-one for names shared by different sprites).
    """
    plan = _Plan(seed)

    # The Stage is the one target whose name is not free text: the sb3 schema
    # pins ``targets[0].name`` to the literal "Stage", and scratch.mit.edu
    # rejects the whole project ("should be equal to one of the allowed values")
    # if it is anything else. It has nothing to hide anyway -- it is the stage.
    # A sprite is ``s%d`` from its own counter, handed out in shuffled order like
    # every other number, so the target list does not say which sprite came
    # first.
    sprites = [target for target in project.targets if not target.is_stage]
    sprite_names = dict(
        zip(
            (target.name for target in sprites),
            (f"s{number}" for number in _scatter(len(sprites), plan.rng)),
        )
    )
    target_names = {
        target.name: target.name if target.is_stage else sprite_names[target.name]
        for target in project.targets
    }

    _flatten_scripts(project, plan.rng, flatten)
    # ``--obf-junk 0`` is the small build: no decoy state, no decoy blocks and
    # no junk strings, which is what someone tuning a project's speed wants.
    decoy_lists = (
        [] if junk == 0 else _add_decoys(project, plan.rng, plan.words, corrupt)
    )
    _number_state(project, plan)
    _number_broadcasts(project, plan)
    _rename_vm_broadcasts(project, plan.broadcast_names)
    _number_proccodes(project, plan)

    # Extensions can reference state *by name* in a dropdown or in a plain text
    # input (the JSON list picker stores a name, thread variables are strings).
    by_name: dict[str, str] = {}
    for (kind, _sprite, original), hidden in plan.by_scope.items():
        by_name.setdefault(original, hidden)
    thread_names = _ThreadNamer(plan.words)

    for target in project.targets:
        for block in target.blocks.values():
            _rename_block(block, plan.var_names, plan.list_names, plan.broadcast_names, target_names)
            _rename_state_picker(block, by_name)
            _rename_thread_variables(block, thread_names)
    for monitor in project.monitors:
        _rename_monitor(monitor, plan.by_scope, target_names)

    # Comments are keyed by target name; keep that association.
    project.comments = {
        target_names.get(name, name): value for name, value in project.comments.items()
    }
    for target in project.targets:
        target.name = target_names[target.name]

    size = 24
    pools = pool if isinstance(pool, list) else [pool] if pool is not None else []
    for list_id, lookups in pools:
        _fill_pool(project, list_id, lookups, plan.rng, junk)
        entry = project.stage.lists.get(list_id)
        if entry is not None:
            size = max(size, len(entry[1]))
    _grow_decoy_lists(project, decoy_lists, plan.rng, size)
    if scrub:
        _scrub(project, plan.rng)

    return plan.mapping


#: Menu fields whose value is a sprite name. They are literals in the source,
#: so they have to follow the rename like a broadcast does -- a clone target
#: left at the old name resolves to nothing and the clone never runs.
_SPRITE_MENU_FIELDS = frozenset(
    {"CLONE_OPTION", "TO", "TOUCHINGOBJECTMENU", "DISTANCETOMENU", "OBJECT"}
)


def _rename_block(block, var_names, list_names, broadcast_names, target_names) -> None:
    fields = block.fields or {}
    for name, value in fields.items():
        if name in _SPRITE_MENU_FIELDS and value and value[0] in target_names:
            value[0] = target_names[value[0]]
    if "VARIABLE" in fields and fields["VARIABLE"][1]:
        hidden = var_names.get(fields["VARIABLE"][1])
        if hidden is not None:
            fields["VARIABLE"][0] = hidden
    if "LIST" in fields and fields["LIST"][1]:
        hidden = list_names.get(fields["LIST"][1])
        if hidden is not None:
            fields["LIST"][0] = hidden
    if "BROADCAST_OPTION" in fields:
        hidden = broadcast_names.get(fields["BROADCAST_OPTION"][0])
        if hidden is not None:
            fields["BROADCAST_OPTION"][0] = hidden
    for raw in (block.inputs or {}).values():
        if not isinstance(raw, list):
            continue
        for payload in raw:
            if not isinstance(payload, list) or len(payload) < 3:
                continue
            kind, name, ident = payload[0], payload[1], payload[2]
            if kind == O.VAR:
                hidden = var_names.get(ident)
            elif kind == O.LIST:
                hidden = list_names.get(ident)
            elif kind == O.BROADCAST:
                hidden = broadcast_names.get(name)
            else:
                continue
            if hidden is not None:
                payload[1] = hidden


def _rename_reporters(target, prototype_id: str, argmap: dict[str, str]) -> None:
    # The prototype carries the parameter's own default-value slot, which holds
    # a reporter block just like the body does.
    _walk_reporters(prototype_id, target.blocks, argmap)
    for block in target.blocks.values():
        if getattr(block, "opcode", "") != "procedures_definition":
            continue
        raw = (block.inputs or {}).get("custom_block")
        if isinstance(raw, list) and len(raw) > 1 and raw[1] == prototype_id:
            _walk_reporters(block.next, target.blocks, argmap)
            return


def _walk_reporters(block_id: str | None, blocks, argmap: dict[str, str]) -> None:
    # Walk iteratively: vmProtect's runtime has a dispatch chain long enough
    # to exceed Python's recursion limit.
    seen: set[str] = set()
    pending = [block_id] if block_id else []
    while pending:
        current = pending.pop()
        if not current or current in seen:
            continue
        seen.add(current)
        block = blocks.get(current)
        if block is None:
            continue
        if getattr(block, "opcode", "").startswith("argument_reporter_"):
            field = (block.fields or {}).get("VALUE")
            if field and field[0] in argmap:
                field[0] = argmap[field[0]]
        if getattr(block, "next", None):
            pending.append(block.next)
        for raw in (block.inputs or {}).values():
            # ``[3, block, obscured-shadow]`` puts a second payload after the
            # block, and a slot can hold a primitive instead of a block id, so
            # every payload is looked at rather than just ``raw[1]``.
            if not isinstance(raw, list):
                continue
            for payload in raw:
                if isinstance(payload, str):
                    pending.append(payload)
                    continue
                if isinstance(payload, list) and len(payload) > 1:
                    nested = payload[1]
                    if isinstance(nested, str):
                        pending.append(nested)


class _ThreadNamer:
    """``lmsTempVars2`` keeps thread-variable names as text, not as state ids."""

    def __init__(self, namer: _WordNamer):
        self.namer = namer
        self.names: dict[str, str] = {}

    def __call__(self, original: str) -> str:
        if original not in self.names:
            hidden = self.namer(f"thread:{original}")
            # The extension's loop blocks expect the underscore convention.
            self.names[original] = "_" + hidden if original.startswith("_") else hidden
        return self.names[original]


#: Inputs of a ``lmsTempVars2`` block that hold a *name* rather than a value.
#: Every block in the extension names its variable in ``VAR``; ``STRING`` and
#: ``NUM`` are the value being stored and are none of this pass's business, so
#: the list is explicit instead of "every input called VAR, STRING or VALUE".
_THREAD_VAR_SLOTS = ("VAR",)


def _rename_thread_variables(block, namer: "_ThreadNamer") -> None:
    if not block.opcode.startswith("lmsTempVars2_"):
        return
    for slot in _THREAD_VAR_SLOTS:
        raw = (block.inputs or {}).get(slot)
        if not isinstance(raw, list):
            continue
        for payload in raw:
            # ``[1, [11, "name"]]`` is the usual shape; ``[3, block, [11,
            # "name"]]`` keeps the name in the obscured shadow, and a name left
            # behind there reads an empty variable at run time.
            if not isinstance(payload, list) or len(payload) < 2:
                continue
            if payload[0] == O.TEXT and isinstance(payload[1], str) and payload[1]:
                payload[1] = namer(payload[1])


def _rename_state_picker(block, by_name: dict[str, str]) -> None:
    """A dropdown that names a variable/list (``…_menu_get_list``)."""
    if not block.opcode.endswith(("_get_list", "_get_var", "_menu_list", "_menu_var")):
        return
    for field, entry in (block.fields or {}).items():
        if entry and isinstance(entry[0], str) and entry[0] in by_name:
            entry[0] = by_name[entry[0]]


def _rename_monitor(monitor: dict, by_scope, target_names) -> None:
    params = monitor.get("params") or {}
    sprite = monitor.get("spriteName") or "Stage"
    if "VARIABLE" in params:
        kind, name = "var", params["VARIABLE"]
    elif "LIST" in params:
        kind, name = "list", params["LIST"]
    else:
        kind = name = None
    if name is not None:
        hidden = by_scope.get((kind, sprite, name))
        if hidden is None:
            # The monitor outlived its sprite: take the name from any scope.
            hidden = next(
                (v for (k, _s, n), v in by_scope.items() if k == kind and n == name),
                None,
            )
        if hidden is not None:
            params["VARIABLE" if kind == "var" else "LIST"] = hidden
    if monitor.get("spriteName"):
        monitor["spriteName"] = target_names.get(
            monitor["spriteName"], monitor["spriteName"]
        )
