; fputest -- the x87, one instruction at a time, for the two machines to agree on.
;
; `tools/i386_jsw_check.py --code` loads this at 0x1000, runs it from 0100:0000 on
; the 80386 model and on the Scratch machine, and compares every register, the
; memory it wrote and the FPU's own state after each instruction. What it is
; here to prove is that the encode and the decode of the three memory formats
; are the *same arithmetic* in both machines -- the registers hold doubles on
; either side, so a disagreement can only come from a power of two applied in
; the wrong order.
;
; Nothing here needs the BIOS: it is a flat binary with a stack of its own, and
; it ends in `hlt`.

        org     0x1000
        bits    16

        mov     sp, 0x0F00
        finit

        ; ---- the stack, and one round trip per format ------------------
        fld1                            ; 1
        fld1
        fadd    st0, st1                ; 2
        fst     qword [0x2000]          ; 2, and the stack still holds it
        fstp    dword [0x2008]          ; m32fp, then pop
        fstp    qword [0x2010]          ; m64fp, then pop

        fld     dword [0x2008]          ; the single, read back and widened
        fstp    qword [0x2018]

        fldpi
        fstp    tword [0x2020]          ; m80fp: ten bytes, 64-bit mantissa
        fld     tword [0x2020]
        fstp    qword [0x202A]

        ; ---- integers, both directions ---------------------------------
        fild    word [0x2100]           ; -7
        fistp   word [0x2030]
        fild    dword [0x2102]          ; 123456
        fist    dword [0x2032]          ; stored, and still on the stack
        fistp   dword [0x2036]
        fild    qword [0x2106]          ; a sixty-four-bit integer
        fistp   qword [0x203A]

        ; ---- the arithmetic, and the two swapped digits ----------------
        fld1
        fld1
        fsub    st0, st1                ; 1 - 1 = 0
        fadd    dword [0x2008]          ; + 2 = 2
        fstp    qword [0x2042]

        fld1
        fld1
        fdivr   st0, st1                ; 1 / 1 = 1, the swapped digit
        fstp    qword [0x204A]

        fld1
        fld1
        fsubr   st0, st1                ; 1 - 1 = 0
        fstp    qword [0x2052]

        fld1
        fld1
        fdiv    st0, st0                ; 1 / 1 = 1
        fstp    qword [0x205A]

        fld1
        fld1
        faddp                           ; the P forms pop
        fstp    qword [0x2062]

        ; ---- compare, and the status word ------------------------------
        fld1
        fldz
        fcompp                          ; st0 < st1: C0
        fnstsw  ax
        mov     [0x206A], ax

        fldz
        fldz
        fcompp                          ; equal: C3
        fnstsw  ax
        mov     [0x206C], ax

        xor     ax, ax
        fld1
        fld1
        fcomip  st0, st1                ; equal, and the flags say so
        fstp    st0
        lahf
        mov     [0x206E], ax

        ; ---- the control word ------------------------------------------
        fldcw   [0x2100]                ; -7: not a round mode anyone means
        fnstcw  [0x2070]
        fldcw   [0x2108]                ; 0x0F7F: chop, single precision
        fnstcw  [0x2072]
        finit

        ; ---- the one-argument operations -------------------------------
        fld1
        fsqrt
        fstp    qword [0x2074]

        fld1
        fchs
        fabs
        fstp    qword [0x207C]

        fld     dword [0x2102]          ; 123456
        frndint
        fstp    qword [0x2084]

        fld1
        fldz
        fxch    st1                     ; the top two swap places
        fstp    qword [0x208C]
        fstp    qword [0x2094]

        ; ---- the stack pointer a program can see ----------------------
        fld1
        fld1
        fld1
        fdecstp
        fincstp
        fstp    qword [0x209C]
        fstp    qword [0x20A4]
        fstp    qword [0x20AC]
        fnstsw  ax
        mov     [0x20B4], ax

        ; ---- fnsave / frstor: the whole state through memory ----------
        fld1
        fldpi
        fadd    st0, st1
        fnsave  [0x2200]
        fld1
        frstor  [0x2200]                ; and both registers are back
        fstp    qword [0x20B6]
        fstp    qword [0x20BE]
        fnstsw  ax
        mov     [0x20C6], ax

        ; ---- the packed decimal forms ----------------------------------
        fldpi
        fbstp   [0x20D0]                ; eighteen digits and a sign
        fbld    [0x20D0]
        fstp    qword [0x20DA]

        fld1
        fchs
        fbstp   [0x20E2]                ; a negative, so the sign nibble
        fbld    [0x20E2]
        fstp    qword [0x20EC]

        hlt

        times 0x2100 - ($ - $$) db 0
        dw      -7                      ; 0x2100: a word, and a control word
        dd      123456                  ; 0x2102
        dq      0x0000000000010000      ; 0x2106
        dw      0x0F7F                  ; 0x210E: chop, single precision
