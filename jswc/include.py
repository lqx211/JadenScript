"""``// include "file.jsw"`` — real source modules.

The language maps a ``class`` to a sprite, so a project with three sprites needs
three classes; keeping them in one file makes the file the size of the machine.
An include is therefore a *line* directive: the line is replaced by the contents
of the named file, resolved relative to the file that mentioned it.

    // include "display.jsw"

Everything after the directive is ordinary source, so the split is purely about
where the text lives. Diagnostics have to keep pointing at the module the reader
wrote, not at the assembled text, so :class:`Source` carries one origin per line
and :meth:`Source.origin` maps an assembled line back to ``(file, line)``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

_INCLUDE = re.compile(r'^\s*//\s*include\s+"([^"]+)"\s*$')


class IncludeError(Exception):
    """A module could not be read, or the include graph has a cycle."""


@dataclass
class Source:
    """Assembled text plus the file and line each line came from."""

    text: str
    name: str
    origins: list[tuple[str, int]] = field(default_factory=list)
    #: every file that contributed, in the order they were first included
    files: list[str] = field(default_factory=list)

    def origin(self, line: int) -> tuple[str, int]:
        """``(file, line)`` for a 1-based line of the assembled text."""
        if 1 <= line <= len(self.origins):
            return self.origins[line - 1]
        return (self.name, line)

    def line_text(self, line: int) -> str:
        lines = self.text.splitlines()
        if 1 <= line <= len(lines):
            return lines[line - 1]
        return ""


def expand(path: str | Path, *, _seen: tuple[Path, ...] = ()) -> Source:
    """Read ``path``, splicing in every ``// include`` it mentions."""
    path = Path(path)
    resolved = path.resolve()
    if resolved in _seen:
        chain = " -> ".join(p.name for p in (*_seen, resolved))
        raise IncludeError(f"include cycle: {chain}")
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise IncludeError(f"cannot read {path}: {exc.strerror}") from exc

    lines: list[str] = []
    origins: list[tuple[str, int]] = []
    files: list[str] = [str(path)]
    for number, line in enumerate(text.splitlines(), start=1):
        match = _INCLUDE.match(line)
        if match is None:
            lines.append(line)
            origins.append((str(path), number))
            continue
        child = expand(path.parent / match.group(1), _seen=(*_seen, resolved))
        child_lines = child.text.splitlines()
        for offset, child_line in enumerate(child_lines):
            lines.append(child_line)
            origins.append(child.origins[offset] if offset < len(child.origins)
                           else (child.name, offset + 1))
        for name in child.files:
            if name not in files:
                files.append(name)

    return Source(text="\n".join(lines) + "\n", name=str(path), origins=origins, files=files)
