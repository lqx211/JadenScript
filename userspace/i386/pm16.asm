; pm16 -- the smallest program that proves the machine is a 386 and not an 8086.
;
; It builds a GDT, sets CR0.PE, far-jumps into the descriptor and then writes
; through a segment whose *base* is not its selector shifted left: the data
; descriptor at 0x10 says base 0x2000, and the three bytes land there. A CPU
; that only stores selectors would write to 0x0100 instead, which is the IVT.
;
; Sixteen-bit code on purpose: the operand path here is sixteen bits wide, so a
; 32-bit code segment is the next slice, not this one.
;
;   nasm -f bin pm16.asm -o pm16.bin
;   python3 tools/i386_jsw_check.py --code pm16.bin --steps 40
bits 16
org 0x1000
start:
    cli
    xor     ax, ax
    mov     ds, ax
    lgdt    [gdt_desc]
    mov     eax, cr0
    or      al, 1
    mov     cr0, eax
    jmp     0x08:pm_entry
gdt:
    dq      0
    db      0xFF, 0xFF, 0x00, 0x00, 0x00, 0x9A, 0x00, 0x00   ; 08 code, 64K, 16-bit
    db      0xFF, 0xFF, 0x00, 0x20, 0x00, 0x92, 0x00, 0x00   ; 10 data, base 0x2000
gdt_end:
gdt_desc:
    dw      gdt_end - gdt - 1
    dd      gdt
pm_entry:
    mov     ax, 0x10
    mov     ds, ax
    mov     byte [0], 'P'
    mov     byte [1], 'M'
    mov     byte [2], '!'
    hlt
