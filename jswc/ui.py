"""Terminal UI: colours, symbols, a spinner and coded diagnostics.

Everything here degrades to plain ASCII when the output is redirected, when
``NO_COLOR`` is set or when the terminal is dumb, so pipes and CI logs stay
readable.  ``FORCE_COLOR=1`` overrides the detection for demos and screenshots.
"""

from __future__ import annotations

import os
import re
import shutil
import sys
import textwrap
import threading
import time
from pathlib import Path

from .diagnostics import (  # noqa: F401  (re-exported for older callers)
    FALLBACK,
    LEVELS,
    MESSAGES,
    Message,
    classify,
    normalise_level,
    shows,
    split_code,
)

# ---------------------------------------------------------------------------
# Capability detection
# ---------------------------------------------------------------------------

def _stream() -> object:
    return sys.stdout


def supports_color(stream=None) -> bool:
    stream = stream or _stream()
    if os.environ.get("NO_COLOR"):
        return False
    force = os.environ.get("FORCE_COLOR")
    if force:
        return force not in ("0", "false", "no")
    if os.environ.get("TERM") == "dumb":
        return False
    return bool(getattr(stream, "isatty", lambda: False)())


def supports_unicode(stream=None) -> bool:
    stream = stream or _stream()
    encoding = (getattr(stream, "encoding", "") or "").lower()
    if "utf" not in encoding:
        return False
    return os.environ.get("JSWS_ASCII") not in ("1", "true")


class Theme:
    """ANSI palette; every attribute is empty when colour is disabled."""

    def __init__(self, color: bool = True, unicode: bool = True, explain: bool = False,
                 loglevel: str = "warn"):
        self.color = color
        self.unicode = unicode
        #: ``--explain``: print the catalogue hint under each diagnostic.
        self.explain = explain
        #: ``--loglevel``: ``error`` < ``warn`` (default) < ``info``.
        self.loglevel = normalise_level(loglevel)

    # -- primitives --------------------------------------------------------
    def _wrap(self, *codes: str) -> str:
        return f"\033[{';'.join(codes)}m" if self.color else ""

    @property
    def reset(self) -> str:
        return self._wrap("0")

    @property
    def bold(self) -> str:
        return self._wrap("1")

    @property
    def dim(self) -> str:
        return self._wrap("2")

    @property
    def italic(self) -> str:
        return self._wrap("3")

    @property
    def red(self) -> str:
        return self._wrap("38;5;203")

    @property
    def green(self) -> str:
        return self._wrap("38;5;114")

    @property
    def yellow(self) -> str:
        return self._wrap("38;5;221")

    @property
    def blue(self) -> str:
        return self._wrap("38;5;111")

    @property
    def magenta(self) -> str:
        return self._wrap("38;5;176")

    @property
    def cyan(self) -> str:
        return self._wrap("38;5;117")

    @property
    def grey(self) -> str:
        return self._wrap("38;5;245")

    def paint(self, text: str, *styles: str) -> str:
        if not self.color or not styles:
            return text
        return "".join(styles) + text + self.reset

    # -- symbols -----------------------------------------------------------
    def symbol(self, name: str) -> str:
        fancy = {
            "ok": "✔",
            "fail": "✖",
            "warn": "⚠",
            "info": "›",
            "dot": "·",
            "arrow": "→",
            "bullet": "•",
            "branch": "├",
            "last": "└",
            "bar": "│",
            "pipe": "│",
            "circle": "◎",
            "ellipsis": "…",
        }
        plain = {
            "ok": "OK",
            "fail": "XX",
            "warn": "!!",
            "info": ">",
            "dot": ".",
            "arrow": "->",
            "bullet": "-",
            "branch": "|-",
            "last": "`-",
            "bar": "|",
            "pipe": "|",
            "circle": "o",
            "ellipsis": "...",
        }
        return fancy[name] if self.unicode else plain[name]


THEME = Theme(supports_color(), supports_unicode())


def configure(*, color: bool | None = None, unicode: bool | None = None,
              explain: bool = False, loglevel: str = "warn") -> Theme:
    """Re-detect (or force) the capabilities; used by the global CLI flags."""
    global THEME
    THEME = Theme(
        supports_color() if color is None else color,
        supports_unicode() if unicode is None else unicode,
        explain,
        loglevel,
    )
    return THEME


def visible(text: str) -> bool:
    """Whether a diagnostic passes the active ``--loglevel``."""
    return shows(classify(text).severity, THEME.loglevel)


# ---------------------------------------------------------------------------
# Diagnostic catalogue (the codes live in diagnostics.py)
# ---------------------------------------------------------------------------
ANSI = re.compile(r"\033\[[0-9;]*m")


def strip_ansi(text: str) -> str:
    return ANSI.sub("", text)


_POSITION = re.compile(r"^(\d+):(\d+):\s*(.*)$", re.DOTALL)


def split_position(text: str) -> tuple[int | None, int | None, str]:
    """``12:9: message`` -> ``(12, 9, "message")``."""
    match = _POSITION.match(text)
    if not match:
        return None, None, text
    return int(match.group(1)), int(match.group(2)), match.group(3)


def format_diagnostic(text: str, source: str | None = None,
                      source_name: str = "", *, show_source: bool = True) -> str:
    """One rendered diagnostic: code, location, message, caret and a ``help``."""
    theme = THEME
    entry = classify(text)
    line, col, body = split_position(text)
    # Messages are stamped with their code at the raise site; the code is
    # rendered in its own column, so drop it from the prose and keep only the
    # part it does not repeat — the title.
    stamped, rest = split_code(body)
    if stamped:
        body = rest
    is_error = entry.severity == "error"
    glyph = {
        "error": theme.symbol("fail"),
        "warning": theme.symbol("warn"),
    }.get(entry.severity, theme.symbol("info"))
    colour = {"error": theme.red, "warning": theme.yellow}.get(entry.severity, theme.blue)

    code = theme.paint(entry.code, colour, theme.bold)
    if line is not None:
        location = theme.paint(f"{source_name}:{line}:{col}" if source_name else f"{line}:{col}",
                               theme.grey)
    else:
        location = theme.paint(source_name, theme.grey) if source_name else ""

    head = f"  {theme.paint(glyph, colour)} {code}"
    if location:
        head += f"  {location}"
    prefix = head + "  "
    width = max(shutil.get_terminal_size((100, 24)).columns, 40)
    room = max(width - len(strip_ansi(prefix)), 24)
    wrapped = textwrap.wrap(body, room) or [""]
    head = prefix + wrapped[0]
    for extra in wrapped[1:]:
        head += "\n" + " " * len(strip_ansi(prefix)) + extra

    pipe = theme.symbol("pipe")
    out = [head]
    if is_error and show_source and source and line:
        lines = source.splitlines()
        index = line - 1
        cursor = lines[index] if 0 <= index < len(lines) else ""
        caret_at = col or 1
        if not cursor.strip() and index > 0:
            # EOF errors point past the last real line; show it instead of a blank.
            back = index - 1
            while back >= 0 and not lines[back].strip():
                back -= 1
            if back >= 0:
                cursor = lines[back]
                caret_at = len(cursor.rstrip()) + 1
        if cursor.strip():
            gutter = " " * len(str(line))
            out.append(f"  {theme.paint(gutter + ' ', theme.grey)}"
                       f"{theme.paint(pipe, theme.grey)} {theme.paint(cursor.rstrip(), theme.grey)}")
            caret = " " * max(caret_at - 1, 0) + "^"
            out.append(f"  {theme.paint(gutter + ' ', theme.grey)}"
                       f"{theme.paint(pipe, theme.grey)} {theme.paint(caret, colour, theme.bold)}")

    if entry.hint and theme.explain:
        # Backticks are for the generated docs; a terminal wants plain prose.
        hint = entry.hint.replace("``", "")
        gutter = " " * len(str(line or ""))
        indent = f"  {gutter} "
        hint_room = max(width - len(indent) - len("help: "), 24)
        wrapped_hint = textwrap.wrap(hint, hint_room) or [""]
        lead = f"{indent}{theme.paint('help:', theme.cyan, theme.bold)} "
        out.append(lead + theme.paint(wrapped_hint[0], theme.grey))
        for extra in wrapped_hint[1:]:
            out.append(" " * len(strip_ansi(lead)) + theme.paint(extra, theme.grey))
    return "\n".join(out)


# ---------------------------------------------------------------------------
# Spinner
# ---------------------------------------------------------------------------
SPINNER_FRAMES = ("·", "○", "◎", "◉", "◎", "○")
SPINNER_ASCII = (".", "o", "O", "0", "O", "o")


class Spinner:
    """``◎ Compiling x.jsw`` with a breathing circle, only on a TTY."""

    def __init__(self, text: str, stream=None, enabled: bool | None = None):
        self.text = text
        self.stream = stream or sys.stderr
        self.theme = THEME
        self.enabled = (
            bool(getattr(self.stream, "isatty", lambda: False)())
            if enabled is None
            else enabled
        )
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.started = time.perf_counter()

    def __enter__(self) -> "Spinner":
        if self.enabled:
            self._thread = threading.Thread(target=self._spin, daemon=True)
            self._thread.start()
        else:
            self._write(f"{self.theme.symbol('circle')} {self.text}"
                        f"{self.theme.symbol('ellipsis')}")
        return self

    def __exit__(self, *_exc) -> None:
        self.stop()

    def _write(self, line: str) -> None:
        self.stream.write("\r\033[K" + line if self.enabled else line + "\n")
        self.stream.flush()

    def _spin(self) -> None:
        frames = SPINNER_FRAMES if self.theme.unicode else SPINNER_ASCII
        index = 0
        while not self._stop.is_set():
            frame = frames[index % len(frames)]
            big = index % len(frames) in (3,)
            circle = self.theme.paint(frame, self.theme.cyan, *( (self.theme.bold,) if big else () ))
            self._write(f"{circle} {self.theme.paint(self.text, self.theme.bold)}")
            index += 1
            self._stop.wait(0.09)

    def stop(self, final: str | None = None) -> float:
        elapsed = time.perf_counter() - self.started
        if self.enabled and self._thread is not None:
            self._stop.set()
            self._thread.join(timeout=0.5)
            self.stream.write("\r\033[K")
        if final:
            self.stream.write(final + "\n")
        self.stream.flush()
        return elapsed


# ---------------------------------------------------------------------------
# Small formatting helpers
# ---------------------------------------------------------------------------
def elapsed(seconds: float) -> str:
    if seconds < 1:
        return f"{seconds * 1000:.0f} ms"
    return f"{seconds:.1f} s"


def count(value: int) -> str:
    return f"{value:,}"


def success(text: str) -> str:
    theme = THEME
    return f"{theme.paint(theme.symbol('ok'), theme.green, theme.bold)} {text}"


def failure(text: str) -> str:
    theme = THEME
    return f"{theme.paint(theme.symbol('fail'), theme.red, theme.bold)} {text}"


def heading(text: str) -> str:
    theme = THEME
    return theme.paint(text, theme.bold, theme.cyan)


def separator() -> str:
    """The ``·`` used between stats, ``-`` when Unicode is unavailable."""
    return muted(f" {THEME.symbol('dot')} ")


def muted(text: str) -> str:
    return THEME.paint(text, THEME.grey)


def accent(text: str) -> str:
    return THEME.paint(text, THEME.magenta)


def read_source(path: str | Path) -> str | None:
    """Source text for caret rendering; ``None`` when it cannot be read."""
    try:
        return Path(path).read_text(encoding="utf-8")
    except OSError:
        return None
