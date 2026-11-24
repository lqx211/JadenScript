#!/bin/sh
# Install `jswc` as a command you can run from anywhere.
#
#   ./install.sh                 # user install: private venv + launcher on PATH
#   ./install.sh --venv          # use the checkout's own .venv instead
#   ./install.sh --prefix DIR    # put the launcher somewhere else
#   ./install.sh --check         # diagnose only; install nothing
#   ./install.sh --uninstall     # remove the launcher and the private venv
#
# Why a private venv instead of `pip install` into the system Python: Homebrew,
# Debian and most current distributions mark their interpreter as externally
# managed (PEP 668) and refuse the install. A venv is the supported answer, it
# never needs sudo, and `-e` keeps the install pointing at this checkout -- so
# editing jswc/ takes effect on the next run instead of needing a reinstall.
#
# Nothing here writes outside $XDG_DATA_HOME (default ~/.local/share) and
# $PREFIX (default ~/.local/bin).
set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
MIN_PYTHON=3.11
MIN_PYTHON_CODE='import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)'

PREFIX=${PREFIX:-${XDG_BIN_HOME:-$HOME/.local/bin}}
DATA_HOME=${XDG_DATA_HOME:-$HOME/.local/share}
VENV=${JSWC_VENV:-$DATA_HOME/jswc/venv}
MODE=install
USE_REPO_VENV=0

usage() {
    cat <<'EOF'
usage: ./install.sh [--check] [--venv] [--prefix DIR] [--uninstall]

  (no flags)     install into a private venv, launcher in ~/.local/bin
  --venv         install into this checkout's .venv instead
  --prefix DIR   where the `jswc` launcher goes (default ~/.local/bin)
  --check        print what would happen, change nothing
  --uninstall    remove the launcher and the private venv

Environment: PYTHON, PREFIX, XDG_DATA_HOME, JSWC_VENV
EOF
}

while [ $# -gt 0 ]; do
    case $1 in
        --check|--dry-run|-n) MODE=check ;;
        --uninstall|--remove) MODE=uninstall ;;
        --venv) USE_REPO_VENV=1 ;;
        --prefix)
            [ $# -ge 2 ] || { printf '%s\n' "install.sh: --prefix needs a directory" >&2; exit 2; }
            PREFIX=$2
            shift
            ;;
        --prefix=*) PREFIX=${1#--prefix=} ;;
        -h|--help) MODE=help ;;
        *) printf '%s\n' "install.sh: unknown option '$1' (try --help)" >&2; exit 2 ;;
    esac
    shift
done

LAUNCHER=$PREFIX/jswc
REPO_VENV=$ROOT/.venv
[ "$USE_REPO_VENV" = 1 ] && VENV=$REPO_VENV

say() { printf '%s\n' "$*"; }
die() { printf 'install.sh: %s\n' "$*" >&2; exit 1; }

find_python() {
    for candidate in ${PYTHON:-} python3 python3.13 python3.12 python3.11; do
        command -v "$candidate" >/dev/null 2>&1 || continue
        if "$candidate" -c "$MIN_PYTHON_CODE" 2>/dev/null; then
            command -v "$candidate"
            return 0
        fi
    done
    return 1
}

on_path() {
    case ":${PATH:-}:" in
        *":$1:"*) return 0 ;;
        *) return 1 ;;
    esac
}

[ "$MODE" = help ] && { usage; exit 0; }

PY=$(find_python) || die "no Python $MIN_PYTHON+ found; set PYTHON=/path/to/python3"
PY_VERSION=$("$PY" -c 'import sys; print("%d.%d.%d" % sys.version_info[:3])')

if [ "$MODE" = check ]; then
    say "checkout   $ROOT"
    say "python     $PY ($PY_VERSION)"
    if command -v pipx >/dev/null 2>&1 && [ "$USE_REPO_VENV" = 0 ]; then
        say "method     pipx ($(command -v pipx))"
    else
        say "method     venv + pip"
    fi
    say "venv       $VENV"
    say "launcher   $LAUNCHER"
    if on_path "$PREFIX"; then
        say "path       $PREFIX is on PATH"
    else
        say "path       $PREFIX is NOT on PATH -- add: export PATH=\"$PREFIX:\$PATH\""
    fi
    if [ -x "$LAUNCHER" ]; then
        say "state      installed: $("$LAUNCHER" --version 2>/dev/null || echo '(stale launcher)')"
    else
        say "state      not installed"
    fi
    say "check ok; nothing was changed"
    exit 0
fi

if [ "$MODE" = uninstall ]; then
    removed=0
    if [ -e "$LAUNCHER" ] || [ -L "$LAUNCHER" ]; then
        rm -f "$LAUNCHER"
        removed=1
        say "removed    $LAUNCHER"
    fi
    if [ "$USE_REPO_VENV" = 0 ] && [ -d "$VENV" ]; then
        rm -rf "$VENV"
        removed=1
        say "removed    $VENV"
    fi
    if command -v pipx >/dev/null 2>&1 && pipx list 2>/dev/null | grep -q jswc; then
        pipx uninstall jswc >/dev/null 2>&1 || true
        removed=1
        say "removed    pipx package jswc"
    fi
    [ "$removed" = 1 ] || say "nothing to remove"
    exit 0
fi

# -- install ---------------------------------------------------------------
# The launcher's directory must be writable *now*: a tool installed with sudo
# is a tool the user cannot upgrade.
mkdir -p "$PREFIX" 2>/dev/null || die "cannot create $PREFIX (pick another with --prefix)"
[ -w "$PREFIX" ] || die "$PREFIX is not writable (pick another with --prefix)"

install_with_pipx() {
    say "installing with pipx (editable, so edits to jswc/ apply immediately)"
    pipx install --force --editable "$ROOT" >/dev/null || die "pipx install failed"
    pipx_bin=$(pipx environment --value PIPX_BIN_DIR 2>/dev/null || true)
    [ -n "$pipx_bin" ] || pipx_bin=$(dirname -- "$(command -v jswc || true)")
    if [ -n "$pipx_bin" ] && [ -x "$pipx_bin/jswc" ]; then
        ln -sf "$pipx_bin/jswc" "$LAUNCHER"
        say "linked     $LAUNCHER -> $pipx_bin/jswc"
    fi
}

install_with_venv() {
    if [ ! -x "$VENV/bin/python" ]; then
        say "creating   $VENV"
        mkdir -p "$(dirname -- "$VENV")"
        "$PY" -m venv "$VENV" || die "could not create a virtualenv at $VENV"
    fi
    say "installing into $VENV"
    # --no-build-isolation first: the only build requirement is setuptools>=68,
    # which most interpreters already have, so an offline machine still works.
    # Falling back to pip's isolation costs a download but always works.
    if ! "$VENV/bin/python" -m pip install --quiet --disable-pip-version-check \
            --no-build-isolation -e "$ROOT" 2>/dev/null; then
        "$VENV/bin/python" -m pip install --quiet --disable-pip-version-check \
            -e "$ROOT" || die "pip install failed"
    fi
    [ -x "$VENV/bin/jswc" ] || die "$VENV/bin/jswc is missing; the console script did not install"
    ln -sf "$VENV/bin/jswc" "$LAUNCHER"
    say "linked     $LAUNCHER -> $VENV/bin/jswc"
}

if command -v pipx >/dev/null 2>&1 && [ "$USE_REPO_VENV" = 0 ]; then
    install_with_pipx
else
    install_with_venv
fi

VERSION=$("$LAUNCHER" --version 2>/dev/null || true)
[ -n "$VERSION" ] || die "installed, but \`jswc --version\` did not answer; check $LAUNCHER"
say "installed  $VERSION"

if ! on_path "$PREFIX"; then
    say ""
    say "$PREFIX is not on PATH yet. Add it to your shell profile:"
    say "  export PATH=\"$PREFIX:\$PATH\""
    say "then open a new terminal (or run that line now)."
fi

say ""
say "next:"
say "  jswc --help        # the whole CLI"
say "  jswc codes         # every diagnostic, with the fix"
say "  npm install        # once, for \`jswc build --html\`"
say "  make serve         # project server for TurboWarp, port 8000"
