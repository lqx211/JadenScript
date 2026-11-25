"""Where the blocks go: TurboWarp's own clean-up layout.

The compiler used to place each script at ``y += 400``, which is a guess: a
script taller than 400 px lands on top of the next one, and short ones leave
huge gaps. "Clean up blocks" gets this right because it reads the height off the
rendered workspace, which a compiler does not have -- so the heights have to be
modelled.

**Every constant here was measured, not guessed.** They come from reading
``block.height`` off a live TurboWarp editor for the shapes a compiled project
contains (923 blocks of NotePlayer V5), then solving for the constants that
reproduce all of them. scratch-blocks lays a block out in rows of
``GRID_UNIT = 4``: a statement row is 56 px, a reporter pill 48, a literal 32,
and an input taller than its slot grows the row with it::

    statement   max(56, tallest input + 16)   64 for pen_*, which draws a
                                              taller row to fit its icon
    reporter    max(48, tallest input + 8)    or 40 with no slots at all
    if/repeat   row + 24 + max(32, then)
    forever     row + 16 + max(32, then)      no bottom arm below the loop
    if/else     row + 24 + max(32, then) + 24 + else

The placement is TurboWarp's "Clean up Plus" (the DevTools addon that replaces
``Blockly``'s ``cleanUp``): columns start at ``x = 48``, blocks at ``y = 64``,
72 px between blocks, and each column starts one widest-block plus 96 px to the
right of the previous one.

One deliberate difference: TurboWarp derives its columns from the positions the
blocks already have, and a freshly compiled project has every script at x = 0,
so that rule would produce a single column of several thousand pixels. The
scripts are packed into columns by height instead (``COLUMN_HEIGHT``), in
document order, which is what a person gets by cleaning up a project that was
already spread out.
"""

from __future__ import annotations

from .ir import Block, Target

# -- measured geometry ------------------------------------------------------
#: A statement row: 48 px of block plus the 8 px bottom notch.
ROW = 56
#: Rows from the extensions that draw an icon next to their text are one notch
#: taller: measured flat 64 for pen_* and for music_setTempo, where a plain row
#: is 56. An extension block not listed here is assumed to be a plain row.
TALL_ROW = 64
TALL_ROW_PREFIXES = ("pen_", "music_")
#: A block that ends a script (it has no next connection) has no bottom notch
#: to join with, so it is a notch shorter than a row.
CAP_ROW = 48
CAP_OPCODES = frozenset({"control_stop", "control_delete_this_clone"})
#: A reporter drawn flat, with nothing plugged in.
BARE_REPORTER = 40
#: A literal or a dropdown.
SHADOW = 32
PROCEDURE_DEFINITION = 72
PROCEDURE_PROTOTYPE = 56
#: Stacked rows share a notch, so every joint takes 8 px back.
JOINT = 8
#: How much an input taller than its slot grows the row around it.
STATEMENT_GROW = 16
REPORTER_GROW = 8
#: C-blocks: the arm below the substack, and the slot an empty substack leaves.
BOTTOM_ARM = 24
FOREVER_ARM = 16
EMPTY_SUBSTACK = 32
#: An arm cannot tuck under a block that has no bottom notch to share, so a
#: substack ending in a cap block pushes it down one notch.
CAP_ARM_EXTRA = 8
#: The "{else}" divider inside if/else.
ELSE_ARM = 24

#: Primitives that deserialize to a taller shadow: a variable reads as a
#: reporter pill, every other primitive as a literal.
VARIABLE_PRIMITIVE = 12
VARIABLE_ROW = 40
VARIABLE_SHADOWS = frozenset({"data_variable", "argument_reporter_string_number",
                             "argument_reporter_boolean"})

C_BLOCKS = frozenset(
    {"control_if", "control_repeat", "control_repeat_until", "control_while"}
)
SUBSTACKS = ("SUBSTACK", "SUBSTACK2")

# -- TurboWarp's clean-up placement ----------------------------------------
COLUMN_X = 48
COLUMN_Y = 64
ROW_GAP = 72
COLUMN_GAP = 96

#: How tall a column may get before a new one starts. Two screens of scrolling;
#: TurboWarp leaves the splitting to the author, a compiler has to pick.
COLUMN_HEIGHT = 4000

#: Width estimate: a block's own text plus its inputs, in pixels. Only used to
#: keep columns apart, so it is allowed to be generous.
BASE_WIDTH = 160
CHAR_WIDTH = 7
MAX_WIDTH = 900


# ---------------------------------------------------------------------------
# input slots
# ---------------------------------------------------------------------------
def _primitive_row(payload: list) -> int:
    return VARIABLE_ROW if payload and payload[0] == VARIABLE_PRIMITIVE else SHADOW


def _slot_child(blocks: dict[str, Block], value: object) -> Block | None:
    """The block plugged into a slot, if there is one."""
    if isinstance(value, str):
        return blocks.get(value)
    if isinstance(value, list) and len(value) >= 2 and isinstance(value[1], str):
        return blocks.get(value[1])
    return None


def _slot_height(blocks: dict[str, Block], value: object, reporters: set[str]) -> int:
    """How tall whatever sits in one input slot is."""
    child = _slot_child(blocks, value)
    if child is not None:
        return block_height(blocks, child, reporters)
    if isinstance(value, list) and len(value) >= 2 and isinstance(value[1], list):
        # ``[1, [4, "1"]]`` is a literal, ``[3, [12, "name", "id"], …]`` a bare
        # variable: neither is a block, but both are drawn in the slot.
        return _primitive_row(value[1])
    return 0


def _row_height(blocks: dict[str, Block], block: Block, reporters: set[str]) -> int:
    tallest = 0
    for name, value in block.inputs.items():
        if name in SUBSTACKS:
            continue
        tallest = max(tallest, _slot_height(blocks, value, reporters))
    if block.id in reporters:
        return max(BARE_REPORTER, tallest + REPORTER_GROW)
    if block.opcode in CAP_OPCODES:
        return max(CAP_ROW, tallest + STATEMENT_GROW)
    floor = TALL_ROW if block.opcode.startswith(TALL_ROW_PREFIXES) else ROW
    return max(floor, tallest + STATEMENT_GROW)


# ---------------------------------------------------------------------------
# stacks
# ---------------------------------------------------------------------------
def _stack(blocks: dict[str, Block], first: str | None) -> list[Block]:
    out: list[Block] = []
    seen: set[str] = set()
    current = first
    while current is not None and current in blocks and current not in seen:
        seen.add(current)
        block = blocks[current]
        if block.shadow:
            break
        out.append(block)
        current = block.next
    return out


def _substack(blocks: dict[str, Block], block: Block, name: str) -> list[Block]:
    value = block.inputs.get(name)
    if not isinstance(value, list) or len(value) < 2 or isinstance(value[1], list):
        return []
    return _stack(blocks, value[1])


def _first(stack: list[Block]) -> str | None:
    return stack[0].id if stack else None


def reporter_ids(target: Target) -> set[str]:
    """Blocks plugged into a slot: those are drawn as reporters, not statements."""
    out: set[str] = set()
    for block in target.blocks.values():
        for name, value in block.inputs.items():
            if name in SUBSTACKS:
                continue
            child = _slot_child(target.blocks, value)
            if child is not None and not child.shadow:
                out.add(child.id)
    return out


def _arm(chain: list[Block], width: int) -> int:
    """The height of the arm that closes a substack."""
    if chain and chain[-1].opcode in CAP_OPCODES:
        return width + CAP_ARM_EXTRA
    return width


def _deps(blocks: dict[str, Block], block: Block) -> list[str]:
    """Blocks whose height this one's height is computed from.

    Every non-substack input slot, plus every row of every substack: a
    C-block's height adds up the rows it contains.
    """
    out: list[str] = []
    for name, value in block.inputs.items():
        if name in SUBSTACKS:
            for row in _substack(blocks, block, name):
                out.append(row.id)
            continue
        child = _slot_child(blocks, value)
        if child is not None:
            out.append(child.id)
    return out


def _heights(
    blocks: dict[str, Block], roots: list[str], reporters: set[str]
) -> dict[str, int]:
    """The drawn height of every block reachable from ``roots``.

    Iterative on purpose: ``vmProtect``'s ``h0`` backend turns a switch into
    one long ``if``/``elif`` chain, so a protected project can nest thousands of
    C-blocks deep and the recursive form blew CPython's stack measuring it.
    """
    deps: dict[str, list[str]] = {}
    stack = [bid for bid in roots if bid in blocks]
    while stack:
        bid = stack.pop()
        if bid in deps:
            continue
        block = blocks[bid]
        found = [child for child in _deps(blocks, block) if child in blocks]
        deps[bid] = found
        stack.extend(found)

    waiting = {bid: len(set(found)) for bid, found in deps.items()}
    parents: dict[str, list[str]] = {bid: [] for bid in deps}
    for bid, found in deps.items():
        for child in set(found):
            parents[child].append(bid)

    heights: dict[str, int] = {}
    ready = [bid for bid, count in waiting.items() if count == 0]
    while ready:
        bid = ready.pop()
        block = blocks[bid]
        heights[bid] = _height_of(blocks, block, reporters, heights)
        for parent in parents[bid]:
            if parent in heights:
                continue
            waiting[parent] -= 1
            if waiting[parent] == 0:
                ready.append(parent)
    for bid in deps:                     # a cycle cannot be drawn: give it 0
        heights.setdefault(bid, 0)
    return heights


def _height_of(
    blocks: dict[str, Block], block: Block, reporters: set[str],
    heights: dict[str, int],
) -> int:
    """One block's own box height, reading its children out of ``heights``."""
    if block.opcode == "procedures_definition":
        return PROCEDURE_DEFINITION
    if block.opcode == "procedures_prototype":
        return PROCEDURE_PROTOTYPE
    if block.shadow:
        return VARIABLE_ROW if block.opcode in VARIABLE_SHADOWS else SHADOW

    tallest = 0
    for name, value in block.inputs.items():
        if name in SUBSTACKS:
            continue
        child = _slot_child(blocks, value)
        if child is not None:
            tallest = max(tallest, heights.get(child.id, 0))
        elif isinstance(value, list) and len(value) >= 2 and isinstance(value[1], list):
            tallest = max(tallest, _primitive_row(value[1]))
    if block.id in reporters:
        row = max(BARE_REPORTER, tallest + REPORTER_GROW)
    elif block.opcode in CAP_OPCODES:
        row = max(CAP_ROW, tallest + STATEMENT_GROW)
    else:
        floor = TALL_ROW if block.opcode.startswith(TALL_ROW_PREFIXES) else ROW
        row = max(floor, tallest + STATEMENT_GROW)

    def rows_height(chain: list[Block]) -> int:
        if not chain:
            return 0
        total = sum(heights.get(item.id, 0) for item in chain)
        return total - JOINT * (len(chain) - 1)

    if block.opcode == "control_forever":
        then = _substack(blocks, block, "SUBSTACK")
        inner = max(EMPTY_SUBSTACK, rows_height(then))
        return row + _arm(then, FOREVER_ARM) + inner
    if block.opcode in C_BLOCKS or block.opcode == "control_if_else":
        then = _substack(blocks, block, "SUBSTACK")
        height = row + _arm(then, BOTTOM_ARM)
        height += max(EMPTY_SUBSTACK, rows_height(then))
        if block.opcode == "control_if_else":
            otherwise = _substack(blocks, block, "SUBSTACK2")
            if otherwise:
                height += _arm(otherwise, ELSE_ARM)
                height += rows_height(otherwise)
        return height
    return row


def block_height(blocks: dict[str, Block], block: Block,
                 reporters: set[str] = frozenset()) -> int:
    """The height scratch-blocks would draw this block's own box at."""
    return _heights(blocks, [block.id], set(reporters)).get(block.id, 0)


def stack_height(blocks: dict[str, Block], first: str | None,
                 reporters: set[str] | None = None) -> int:
    """The height a script is drawn at: its rows, sharing their joints."""
    if reporters is None:
        reporters = _reporters_of(blocks)
    rows = _stack(blocks, first)
    if not rows:
        return 0
    heights = _heights(blocks, [row.id for row in rows], reporters)
    total = sum(heights.get(row.id, 0) for row in rows)
    return total - JOINT * (len(rows) - 1)


def _reporters_of(blocks: dict[str, Block]) -> set[str]:
    out: set[str] = set()
    for block in blocks.values():
        for name, value in block.inputs.items():
            if name in SUBSTACKS:
                continue
            child = _slot_child(blocks, value)
            if child is not None and not child.shadow:
                out.add(child.id)
    return out


def _literal_chars(value: object) -> int:
    """The text a serialized input would print, roughly."""
    if isinstance(value, str):
        return 0
    if isinstance(value, (list, tuple)):
        if len(value) == 2 and isinstance(value[0], int) and isinstance(value[1], str):
            # [4, "text"] / [10, "text"] -- a shadow's literal.
            return len(value[1])
        return sum(_literal_chars(item) for item in value)
    return 0


def stack_width(blocks: dict[str, Block], first: str | None) -> int:
    """A width estimate for a stack: its widest row plus its text.

    Iterative like :func:`_heights`: the ``h0`` switch turns into a deep chain
    of nested C-blocks, and walking it recursively used to overflow.
    """
    widest = BASE_WIDTH
    seen: set[str] = set()
    pending: list[tuple[str | None, int]] = [(first, 0)]
    while pending:
        bid, depth = pending.pop()
        if bid is None or bid not in blocks or bid in seen:
            continue
        seen.add(bid)
        block = blocks[bid]
        chars = sum(len(str(field[0])) for field in block.fields.values())
        chars += sum(_literal_chars(value) for value in block.inputs.values())
        widest = max(widest, BASE_WIDTH + chars * CHAR_WIDTH + depth * 16)
        if block.next is not None:
            pending.append((block.next, depth))
        for name in SUBSTACKS:
            inner = _substack(blocks, block, name)
            if inner:
                pending.append((_first(inner), depth + 1))
    return min(widest, MAX_WIDTH)


# ---------------------------------------------------------------------------
# placement
# ---------------------------------------------------------------------------
def layout_target(target: Target, *, fixed: set[str] = frozenset(),
                  column_height: int = COLUMN_HEIGHT) -> None:
    """Place every top-level block that has no explicit position.

    ``fixed`` holds the ids whose x/y came from ``metadata … pos`` or ``// at``:
    those are the author's own positions and are left exactly where they are.
    """
    movable = [
        block
        for block in target.blocks.values()
        if block.top_level and block.id not in fixed and not block.shadow
    ]
    if not movable:
        return
    reporters = reporter_ids(target)
    rows_by_script = {block.id: _stack(target.blocks, block.id) for block in movable}
    row_heights = _heights(
        target.blocks,
        [row.id for rows in rows_by_script.values() for row in rows],
        reporters,
    )

    def script_height(block: Block) -> int:
        rows = rows_by_script[block.id]
        if not rows:
            return 0
        total = sum(row_heights.get(row.id, 0) for row in rows)
        return total - JOINT * (len(rows) - 1)

    heights = {block.id: script_height(block) for block in movable}
    columns: list[list[Block]] = [[]]
    used = 0
    for block in movable:
        height = heights[block.id]
        if used and used + height + ROW_GAP > column_height:
            columns.append([])
            used = 0
        columns[-1].append(block)
        used += height + ROW_GAP

    cursor_x = COLUMN_X
    for column in columns:
        cursor_y = COLUMN_Y
        widest = BASE_WIDTH
        for block in column:
            block.x = cursor_x
            block.y = cursor_y
            cursor_y += heights[block.id] + ROW_GAP
            widest = max(widest, stack_width(target.blocks, block.id))
        cursor_x += widest + COLUMN_GAP


def layout_project(targets, *, fixed: set[str] = frozenset(),
                   column_height: int = COLUMN_HEIGHT) -> None:
    for target in targets:
        layout_target(target, fixed=fixed, column_height=column_height)
