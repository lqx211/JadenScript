; ---------------------------------------------------------------------------
; A cat that marches across a mode 13h screen, dragging a rainbow, singing on
; PIT channel 2.  An original sector -- the animation is rectangles, not a
; bitmap, because a boot sector is 512 bytes and a 320x200 picture is not.
;
; What it asks of the machine, and nothing else:
;
;   * `int 10h`, ax = 0x0013: the card's 320 x 200 aperture at 0xA0000, a byte
;     a pixel.  A pixel is a palette index; the display maps index > 15 through
;     a 4-bit-per-channel cube, so the colours below are indices into that cube
;     and no palette is loaded.
;   * PIT channel 2 through ports 0x43/0x42 and the gate on port 0x61 -- the
;     three writes `meow.asm` uses.  A note is a divisor, so the tune is a
;     table of sixteen-bit numbers and there is no sample to play.
;   * the BIOS tick counter at 0x46C: `hlt` once a frame so the CPU sleeps and
;     the display thread gets the machine to itself.
;
; A frame is: wipe the band, twelve rainbow bars (the colour rotates one place
; a frame so the trail crawls), the cat as eleven rectangles, and a tail whose
; height follows the frame.  The walk is an x that advances and wraps.
; ---------------------------------------------------------------------------

[bits 16]
org 0x7C00

TICKS   equ 0x046C                  ; the BIOS tick counter, 18.2 Hz
STRIDE  equ 320
VGA     equ 0xA000

; palette indices for the display's 4-bit cube: r*32 + g*4 + b
C_BLACK equ 0
C_GREY  equ 170                     ; r4 g4 b2
C_WHITE equ 255
C_PINK  equ 238
C_RED   equ 224
C_ORNG  equ 236
C_YEL   equ 252
C_GRN   equ 28
C_CYN   equ 31
C_BLUE  equ 3
C_VIO   equ 131

BAND_Y  equ 88                      ; the rows the animation owns
BAND_H  equ 84

start:
    cli
    xor     ax, ax
    mov     ds, ax
    mov     ss, ax
    mov     sp, 0x7C00
    cld
    sti

    mov     ax, 0x0013              ; mode 13h
    int     0x10
    mov     ax, VGA
    mov     es, ax
    xor     di, di
    xor     ax, ax
    mov     cx, 32000               ; 64000 bytes = 32000 words of black
    rep     stosw

main:
    call    wipe
    call    rainbow
    call    cat
    call    tail
    call    sing

    mov     cx, 1                   ; one tick a frame, ~55 ms
    call    wait_ticks

    inc     word [phase]
    add     word [catx], 3
    cmp     word [catx], STRIDE - 44
    jb      main
    mov     word [catx], 8          ; off the right edge: back to the left
    jmp     main

; ---- the scene -----------------------------------------------------------

; Blank the band so nothing of the last frame is left behind.
wipe:
    mov     word [fx], 0
    mov     word [fy], BAND_Y
    mov     word [fw], STRIDE
    mov     word [fh], BAND_H
    mov     byte [fc], C_BLACK
    jmp     fill

; Twelve bars, six pixels tall, their colours rotating with the frame.
rainbow:
    push    ax
    push    bx
    push    cx
    xor     cx, cx                  ; bar number
.bar:
    mov     ax, cx
    add     al, byte [phase]
    xor     ah, ah
    and     al, 7                   ; al = colour slot
    mov     bx, palette
    add     bx, ax
    mov     al, [bx]
    mov     [fc], al
    mov     ax, cx
    mov     bx, 9
    mul     bx                      ; ax = bar * 9
    add     ax, BAND_Y + 8
    mov     [fy], ax
    mov     word [fx], 0
    mov     word [fw], STRIDE
    mov     word [fh], 9
    push    cx
    call    fill
    pop     cx
    inc     cx
    cmp     cl, 8
    jb      .bar
    pop     cx
    pop     bx
    pop     ax
    ret

; The cat: a table of rectangles relative to (catx, caty).
cat:
    push    ax
    push    cx
    push    si
    mov     si, catrects
    mov     cx, catn
.next:
    push    cx
    mov     al, [si]                ; dx
    xor     ah, ah
    add     ax, [catx]
    mov     [fx], ax
    mov     al, [si + 1]            ; dy
    xor     ah, ah
    add     ax, [caty]
    mov     [fy], ax
    mov     al, [si + 2]            ; w
    xor     ah, ah
    mov     [fw], ax
    mov     al, [si + 3]            ; h
    xor     ah, ah
    mov     [fh], ax
    mov     al, [si + 4]            ; colour
    mov     [fc], al
    call    fill
    add     si, 5
    pop     cx
    loop    .next
    pop     si
    pop     cx
    pop     ax
    ret

; The tail: one rectangle whose height follows the frame.
tail:
    push    ax
    push    si
    mov     ax, [catx]
    add     ax, 30
    mov     [fx], ax
    mov     ax, [phase]
    and     ax, 3
    mov     si, taildy
    add     si, ax
    mov     al, [si]
    xor     ah, ah
    add     ax, [caty]
    mov     [fy], ax
    mov     word [fw], 3
    mov     word [fh], 10
    mov     byte [fc], C_GREY
    call    fill
    pop     si
    pop     ax
    ret

; ---- one rectangle -------------------------------------------------------
; The rectangle is at [fx], [fy], [fw] wide and [fh] tall, colour [fc].
fill:
    push    ax
    push    cx
    push    di
    push    es
    mov     ax, VGA
    mov     es, ax
    mov     di, [fy]
    shl     di, 6                   ; y * 64
    mov     ax, [fy]
    shl     ax, 8                   ; y * 256
    add     di, ax                  ; y * 320
    add     di, [fx]
    mov     al, [fc]
    mov     cx, [fh]
.row:
    push    cx
    mov     cx, [fw]
    rep     stosb
    pop     cx
    add     di, STRIDE
    sub     di, [fw]
    dec     cx
    jnz     .row
    pop     es
    pop     di
    pop     cx
    pop     ax
    ret

; ---- sound ---------------------------------------------------------------
; One note every other frame, from an eight-note phrase of our own.
sing:
    push    ax
    push    si
    mov     ax, [phase]
    shr     ax, 1
    and     ax, 7
    add     ax, ax
    mov     si, tune
    add     si, ax
    mov     ax, [si]
    call    play
    pop     si
    pop     ax
    ret

; ax = PIT divisor for channel 2, then close the speaker gate
play:
    push    ax
    mov     al, 0xB6                ; ch2, lo then hi, mode 3 square
    out     0x43, al
    pop     ax
    out     0x42, al
    mov     al, ah
    out     0x42, al
    in      al, 0x61
    or      al, 3
    out     0x61, al
    ret

; ---- the clock -----------------------------------------------------------
; cx = how many ticks to idle for; IRQ0 advances the counter.
wait_ticks:
    push    ax
    push    bx
    mov     bx, [TICKS]
.wait:
    hlt
    mov     ax, [TICKS]
    sub     ax, bx
    cmp     ax, cx
    jb      .wait
    pop     bx
    pop     ax
    ret

; ---- data ----------------------------------------------------------------

palette:    db C_RED, C_ORNG, C_YEL, C_GRN, C_CYN, C_BLUE, C_VIO, C_RED
taildy:     db 0, 2, 5, 2

; dx, dy, w, h, colour -- the cat, from the top-left of its box
catrects:
    db 2, 0, 4, 4, C_GREY          ; left ear
    db 11, 0, 4, 4, C_GREY         ; right ear
    db 1, 4, 15, 12, C_GREY        ; head
    db 4, 8, 2, 2, C_BLACK         ; eyes
    db 11, 8, 2, 2, C_BLACK
    db 12, 14, 3, 1, C_WHITE       ; whisker
    db 14, 10, 17, 10, C_GREY      ; body
    db 17, 20, 3, 3, C_GREY        ; front leg
catn        equ ($ - catrects) / 5

; an original phrase: A4 C5 E5 C5, D5 F5 A5 F5
tune:       dw 2712, 2281, 1811, 2281, 2414, 2032, 1708, 2032

catx:       dw 8
caty:       dw 92
phase:      dw 0
fx:         dw 0
fy:         dw 0
fw:         dw 0
fh:         dw 0
fc:         db 0

times 510 - ($ - $$) db 0
dw 0xAA55
