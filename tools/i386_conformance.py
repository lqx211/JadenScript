#!/usr/bin/env python3
"""Run a battery of 32-bit instruction sequences through both machines.

    python3 tools/i386_conformance.py              # every sequence
    python3 tools/i386_conformance.py --list
    python3 tools/i386_conformance.py -k push     # names containing "push"

`tools/i386_jsw_check.py --code` already compares one program instruction for
instruction; this is that harness with a table of programs and a verdict a CI
job and a person can both read. Each sequence is assembled with nasm, loaded at
0x1000, and run from 0100:0000 on the model and on the Scratch machine until it
halts or disagrees.

The sequences are written `[bits 16]` with explicit `o32`/`a32` prefixes,
because a real-mode code segment defaults to sixteen bits and the 32-bit forms
are what a kernel reaches for. Each one starts by pointing the stack somewhere
of its own: the power-on SP is POST's, and a sequence that depends on it would
be comparing two different stacks.

This is the tool the kernel divergence asks for. Booting a kernel costs ten
minutes an attempt and reports the *symptom*; a sequence costs a second and
reports the instruction.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CHECK = ROOT / "tools" / "i386_jsw_check.py"

#: name -> program. `[bits 16]` everywhere: see the docstring.
SEQUENCES: dict[str, str] = {
    "stack-esp": """
        o32 mov esp, 0x0F00
        o32 push esp                ; pushes the value *before* the decrement
        o32 pop ebx
        o32 push 0x12345678
        o32 push dword [esp+0]
        o32 pop ecx
        o32 pop edx
        hlt
    """,
    "stack-frame": """
        o32 mov esp, 0x0F00
        o32 mov ebp, 0x0F00
        o32 enter 16, 0             ; push ebp; ebp = esp; esp -= 16
        o32 leave                   ; esp = ebp; pop ebp
        o32 push 0x01020304
        o32 pop eax
        hlt
    """,
    "pushad": """
        o32 mov esp, 0x0F00
        mov ax, 0x1122
        mov bx, 0x3344
        o32 pushad
        o32 xor eax, eax
        o32 xor ebx, ebx
        o32 popad
        o32 popad                   ; and off the stack again
        hlt
    """,
    "shift-count": """
        o32 mov eax, 0x80000001
        mov cl, 1
        o32 shl eax, cl             ; 0x00000002, CF = the bit that fell out
        o32 sar eax, cl             ; arithmetic: the sign stays
        mov cl, 33                  ; masked to five bits, so this is a shift by one
        o32 shr eax, cl
        hlt
    """,
    "shld-shrd": """
        o32 mov eax, 0x11223344
        o32 mov ebx, 0xAABBCCDD
        mov cl, 8
        o32 shld eax, ebx, cl       ; the top of ebx enters at the bottom of eax
        o32 shrd ebx, eax, cl
        hlt
    """,
    "mul-div": """
        o32 mov eax, 0x00010001
        o32 mov ecx, 0x00000003
        o32 mul ecx                 ; edx:eax = eax * ecx
        o32 mov ebx, edx
        o32 div ecx                 ; eax = quotient, edx = remainder
        o32 mov esi, edx
        hlt
    """,
    "imul-forms": """
        o32 mov eax, 0xFFFFFFF0     ; -16
        o32 mov ecx, 0x00000010
        o32 imul eax, ecx           ; two-operand: eax = eax * ecx
        o32 imul ebx, eax, 3        ; three-operand with an immediate
        o32 imul edx, eax, -1
        hlt
    """,
    "movsx-movzx": """
        mov  byte [0x0200], 0xF0
        mov  byte [0x0201], 0x7F
        o32 movsx eax, byte [0x0200]    ; 0xFFFFFFF0
        o32 movzx ebx, byte [0x0200]    ; 0x000000F0
        o32 movsx ecx, word [0x0200]    ; 0xF0FF sign-extended... the two bytes
        o32 movzx edx, word [0x0200]
        hlt
    """,
    "bit-scan": """
        o32 mov eax, 0x00F00000
        o32 bsf ebx, eax            ; lowest set bit: 20
        o32 bsr ecx, eax            ; highest: 23
        o32 bt  eax, 20             ; CF = that bit
        o32 bts eax, 0
        o32 btr eax, 20
        hlt
    """,
    "setcc-cmov": """
        o32 mov eax, 5
        o32 mov ebx, 5
        o32 cmp eax, ebx            ; ZF set
        o32 sete cl
        o32 setne dl
        o32 mov esi, 0x11111111
        o32 mov edi, 0x22222222
        o32 cmove esi, edi          ; moves only on equality
        o32 cmovne edi, esi
        hlt
    """,
    "rep-movsd": """
        mov  ax, ds
        mov  es, ax
        o32 mov esi, 0x0300
        o32 mov edi, 0x0400
        o32 mov ecx, 4
        cld
        o32 rep movsd
        std                         ; and once backwards
        o32 mov esi, 0x0310
        o32 mov edi, 0x0410
        o32 mov ecx, 2
        o32 rep movsd
        cld
        hlt
    """,
    "ret-imm": """
        o32 mov esp, 0x0F00
        o32 push 0xCAFE0000         ; a return address nothing jumps to
        o32 push 0x01020304         ; filler the callee must drop
        o32 pop eax                 ; take the filler back off
        o32 push 0xCAFE0000
        o32 ret 4                   ; pops the address and 4 more bytes
        hlt
    """,
    "loop-ecxz": """
        o32 mov ecx, 3
        o32 mov eax, 0
    again:
        o32 add eax, 1
        o32 loop again              ; address size follows the prefix
        o32 jecxz done
        o32 mov ebx, 1
    done:
        hlt
    """,
    "bswap-xchg": """
        o32 mov eax, 0x11223344
        o32 bswap eax               ; 0x44332211
        o32 mov ebx, 0xDEADBEEF
        o32 xchg eax, ebx
        o32 xadd eax, ebx           ; exchange, then add
        hlt
    """,
    "cmpxchg": """
        o32 mov eax, 0x11           ; what we expect to find
        o32 mov ebx, 0x11
        o32 mov [0x0500], ebx
        o32 mov ebx, 0x22           ; what to write if it matches
        o32 cmpxchg [0x0500], ebx   ; ZF set, memory becomes 0x22
        o32 mov ebx, 0x33
        o32 cmpxchg [0x0500], ebx   ; ZF clear, eax becomes 0x22
        hlt
    """,
    "sib-forms": """
        mov  ax, ds
        mov  es, ax
        o32 mov eax, 0x0700
        o32 mov ebx, 0x0004
        a32 mov dword [eax+ebx*4+8], 0x11223344
        a32 mov edx, dword [eax+ebx*4+8]
        o32 mov ebp, 0x0700
        a32 mov edi, [ebp+16]
        a32 mov esi, [eax+0]
        hlt
    """,
    "lea-width": """
        o32 lea eax, [si+4]
        o32 mov ebx, 0x1000
        a32 lea ecx, [ebx+ebx*2+8]
        o32 lea edx, [bx]
        hlt
    """,
    "moffs": """
        mov  word [0x0200], 0x1234
        o32 mov eax, [0x0200]
        o32 mov [0x0204], eax
        a32 o32 mov ebx, [0x00000200]
        hlt
    """,
    "rel-jumps": """
        o32 jmp near target
        o32 mov eax, 0xDEAD
    target:
        o32 mov ebx, 1
        o32 call near sub
        o32 add ebx, 10
        hlt
    sub:
        o32 add ebx, 100
        o32 ret
    """,
    "string-prefix-32": """
        mov  ax, ds
        mov  es, ax
        o32 mov esi, 0x0300
        o32 mov edi, 0x0400
        a32 o32 mov ecx, 2
        cld
        a32 o32 rep movsd
        hlt
    """,
    "addr-size-32": """
        mov  ax, ds
        mov  es, ax
        o32 mov eax, 0x0600         ; a32: the destination is a 32-bit register
        a32 mov word [eax], 0x7788
        a32 mov dx, word [eax]
        o32 mov ecx, 0x0604
        o32 mov dword [ecx], 0x99AABBCC
        o32 mov edi, ecx
        hlt
    """,
}


def programs(names: list[str]) -> list[tuple[str, str]]:
    if not names:
        return list(SEQUENCES.items())
    chosen = [(name, body) for name, body in SEQUENCES.items()
              if any(part in name for part in names)]
    return chosen


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("names", nargs="*", help="only sequences whose name contains one of these")
    parser.add_argument("--list", action="store_true", help="print the sequence names")
    parser.add_argument("--steps", type=int, default=4000)
    parser.add_argument("--no-build", action="store_true",
                        help="use the project the last build left behind")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    if args.list:
        for name in SEQUENCES:
            print(name)
        return 0

    chosen = programs(args.names)
    if not chosen:
        print("no sequence matched", file=sys.stderr)
        return 2

    failed = 0
    with tempfile.TemporaryDirectory() as tmp:
        blob = Path(tmp) / "seq.bin"
        for index, (name, body) in enumerate(chosen):
            source = Path(tmp) / "seq.asm"
            source.write_text("[bits 16]\norg 0\n" + body, encoding="utf-8")
            built = subprocess.run(
                ["nasm", "-f", "bin", str(source), "-o", str(blob)],
                capture_output=True, text=True,
            )
            if built.returncode != 0:
                print(f"{name:14s} assemble failed\n{built.stderr}", file=sys.stderr)
                failed += 1
                continue
            command = [sys.executable, str(CHECK), "--code", str(blob),
                       "--steps", str(args.steps)]
            if index or args.no_build:
                # the first sequence builds the project; the rest reuse it
                command.append("--no-build")
            run = subprocess.run(
                command,
                capture_output=True, text=True, cwd=ROOT,
            )
            if run.returncode == 0 and not args.verbose:
                line = next((l for l in run.stdout.splitlines() if l.startswith("agree")), "")
                print(f"{name:14s} ok    {line}")
                continue
            print(f"{name:14s} FAIL")
            print(run.stdout.rstrip())
            if run.stderr.strip():
                print(run.stderr.rstrip())
            failed += 1

    print()
    print(f"{len(chosen) - failed}/{len(chosen)} sequences agree")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
