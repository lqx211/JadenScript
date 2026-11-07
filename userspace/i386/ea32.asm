; ---------------------------------------------------------------------------
; Thirty-two-bit effective addresses, which is the one thing a kernel does
; before it does anything else.
;
; Sixteen-bit code spells an address as a pair from a fixed list -- BX+SI,
; BP+DI -- and that is what this machine could decode. Everything compiled for
; a 386 spells it as base + index*scale + displacement, with a second byte when
; there is an index, and offsets that are thirty-two bits wide. A kernel's
; first hundred instructions index a table that way, so this file is the test
; that says whether the decoder is there.
;
; It runs from 0x1000 (the checker loads it there with CS base 0x1000), enters
; protected mode with a flat D=1 code segment, and keeps every address under a
; megabyte on purpose: paging is off, so both machines truncate above that, and
; the point here is the arithmetic of the address, not the pager.
; ---------------------------------------------------------------------------

[bits 16]
; the checker loads this at 0x1000 with CS base 0x1000; the flat descriptor is
; base 0, so every label has to carry that offset from the start
org 0x1000

start:
    cli
    lgdt    [gdtr]
    mov     eax, cr0
    or      eax, 1
    mov     cr0, eax
    jmp     0x08:pm32

[bits 32]
pm32:
    mov     ax, 0x10                ; flat data, all four of them
    mov     ds, ax
    mov     es, ax
    mov     ss, ax
    mov     fs, ax
    mov     gs, ax

    mov     esp, 0x70000            ; the checker starts with SP at zero, and
    mov     ebp, 0x70000            ; 0xFFFFC is inside the ROM window
    mov     ebx, 0x400              ; base
    mov     ecx, 3                  ; index
    mov     eax, 0x11223344
    mov     [ebx + ecx * 4 + 0x20], eax      ; SIB, scale 4, disp8
    mov     edx, [ebx + ecx * 4 + 0x20]      ; and back out again

    mov     [0x800], edx            ; mod 00 rm 101: a bare disp32
    mov     [ecx * 4 + 0x900], edx  ; SIB with no base at all
    mov     ebp, 0xA00
    mov     [ebp + 0x10], edx       ; EBP-relative, which reads via SS
    mov     edi, [ebp + 0x10]

    lea     esi, [ebx + ecx * 8 + 4]         ; 0x400 + 24 + 4 = 0x41C
    mov     [0xB00], esi

    mov     eax, 0xC0DE
    mov     [ebx + 0x4], ax         ; a word store through a 32-bit address
    mov     [ebx + 0x6], dx

    ; ---- the instructions that were fixed to follow the operand size -----
    mov     dword [ebx + 0x100], 0x55667788   ; C7 /0, a full-width immediate
    mov     eax, [ebx + 0x100]                ; 8B, a full-width load
    mov     [0xC00], eax
    mov     ecx, 0x0102
    mov     [ebx + 0x104], ecx
    mov     edx, 0x55667788
    test    [ebx + 0x100], edx                ; 85, a full-width test
    mov     eax, 0
    adc     eax, 0                            ; ZF was set: carry in, CF out
    mov     [0xC04], eax
    mov     esi, 0x11112222
    xchg    [ebx + 0x108], esi                ; 87
    mov     [0xC08], esi                      ; the old memory value
    mov     eax, 0x00010000                   ; 65536
    mov     ecx, 3
    xor     edx, edx
    div     ecx                               ; F7 /6, 32-bit divide
    mov     [0xC0C], eax                      ; 21845
    mov     [0xC10], edx                      ; 1
    mov     dword [ebx + 0x10C], 0xAABBCCDD
    push    dword [ebx + 0x10C]               ; FF /6, a wide push
    pop     eax
    mov     [0xC14], eax

    ; ---- the instruction pointer, which was still sixteen bits -----------
    call    sub32                             ; the return address is a dword
    mov     [0xD08], eax
    mov     eax, [esp]                        ; what `call` pushed
    mov     [0xD0C], eax

    ; a far return to code above the first 64 KiB: in a flat segment EIP is
    ; thirty-two bits, so 0x11000 is an ordinary place to be
    push    dword 0x08
    push    dword HIGH
    retf
    hlt

sub32:
    mov     eax, 0xFEEDFACE
    ret

; ---- the flat descriptor table -------------------------------------------
align 8
gdt:
    dq      0                       ; the null descriptor
    dq      0x00CF9A000000FFFF      ; 0x08: base 0, limit 4 GiB, code, D=1
    dq      0x00CF92000000FFFF      ; 0x10: base 0, limit 4 GiB, data
gdt_end:

gdtr:
    dw      gdt_end - gdt - 1
    dd      gdt

; ---- and code that no sixteen-bit instruction pointer could reach ---------
HIGH equ 0x11000                        ; 0x1000 where the blob is + 0x10000

times 0x10000 - ($ - $$) db 0
high:
    mov     eax, 0x1234BEEF
    mov     [0xD00], eax
    jmp     .landed                     ; a rel32 that has to stay up here
    mov     eax, 0
.landed:
    mov     [0xD04], eax
    hlt
