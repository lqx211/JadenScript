; ---------------------------------------------------------------------------
; A boot sector that sets CGA mode 4 and paints the bitmap at 0xB8000.
;
; Mode 4 is the one everybody wrote for in 1984: 320 x 200, four colours, two
; bits a pixel, and the scanlines interleaved -- even rows at the bottom of the
; 16 KiB page and odd rows 8 KiB further on. Nothing about it is at 0xA0000,
; which is what makes it a different code path from mode 13h: the framebuffer
; is the *text* plane the console uses.
;
; The sector proves three things a test can read back: `int 10h` accepts the
; mode, `AH=0Fh` reports it, and memory at 0xB8000 takes the writes. It also
; writes the colour select (0x3D9) and polls the status register (0x3DA), so a
; program that waits for a retrace is not a program that hangs here.
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

    mov     ax, 0x0004              ; CGA mode 4: 320 x 200 x 4 colours
    int     0x10
    mov     ah, 0x0F                ; and what does it say it is in now?
    int     0x10
    mov     [0x3002], ax            ; ds is 0 here, so this is 0x3002

    mov     dx, 0x3D9               ; colour select: set 1 (bit 5), no bright
    mov     al, 0x20
    out     dx, al
    mov     dx, 0x3DA               ; status 1, the way a program waits for
    in      al, dx                  ; the retrace: two reads, so a guest that
    mov     [0x3004], al            ; tests bit 3 sees it change
    in      al, dx
    mov     [0x3005], al

    mov     ax, 0xB800              ; the bitmap, which is the text plane
    mov     es, ax
    xor     di, di
    mov     cx, 0x4000              ; 16 KiB: the whole CGA page
    mov     al, 0x1B                ; 00 01 10 11: colours 0, 1, 2, 3
    cld
    rep     stosb

    mov     ax, 0x0300              ; 0x3000 linear, so a test can read it back
    mov     ds, ax
    mov     byte [0x0000], 0xA5
    mov     byte [0x0001], 0x5A

    ; Deliberately *not* `mov ax,3 / int 10h` on the way out: int 10h clears
    ; the screen when it sets a mode, and on a CGA the screen is the bitmap we
    ; just wrote. mode13.asm can restore text because its framebuffer is a
    ; different 64 KiB; here the two modes share one page of memory.
    cli                             ; nothing left to do: a timer tick would only
    hlt                             ; wake the CPU into the padding after this

times 510 - ($ - $$) db 0
dw 0xAA55
