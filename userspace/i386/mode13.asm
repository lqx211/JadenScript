; ---------------------------------------------------------------------------
; A boot sector that sets mode 13h and paints memory, to prove the card answers
; at 0xA0000 the way it says it does.
;
; Nothing here is clever: `int 10h` sets the mode, ES points at the aperture,
; and a `rep stosb` fills it. That is all a DOS program does, and all this
; machine has to support for a graphics program to draw anything at all. It
; writes a marker at 0x3000 so a test can tell the sector ran at all, and
; leaves 64 KiB of 'A' behind it.
; ---------------------------------------------------------------------------

[bits 16]
org 0x7C00

start:
    cli
    xor     ax, ax
    mov     ds, ax
    mov     ss, ax
    mov     sp, 0x7C00
    sti

    mov     ax, 0x0013              ; mode 13h: 320 x 200 x 256 colours
    int     0x10
    mov     ah, 0x0F                ; and what does it say it is in now?
    int     0x10
    mov     [0x3002], ax            ; ds is 0 here, so this is 0x3002

    mov     ax, 0xA000              ; and the memory behind it
    mov     es, ax
    xor     di, di
    mov     cx, 0x1000              ; 64 KiB: the whole aperture
    mov     al, 0x41                ; 'A'
    cld
    rep     stosb
    ; and a marker the tests can read: this sector's own code ran
    mov     ax, 0x0300              ; 0x3000 linear, so a test can read it back
    mov     ds, ax
    mov     byte [0x0000], 0xA5
    mov     byte [0x0001], 0x5A
    mov     ax, 0x0003
    int     0x10                   ; text back, so the halt leaves a clean screen

    cli                             ; nothing left to do: a timer tick would only
    hlt                             ; wake the CPU into the padding after this

times 510 - ($ - $$) db 0
dw 0xAA55
