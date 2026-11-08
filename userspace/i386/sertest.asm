; sertest -- prove the serial port answers, the way a loader would use it.
;
; Two ways in, because a guest has two: `int 14h` (the BIOS service, which works
; before any driver exists) and the 16550's own registers at 0x3F8. Both have to
; end up in the same place -- the host's console -- and the poll on the line
; status is the part that matters: a card that never says "transmitter empty"
; is a console that prints nothing at all.

        org     0x7C00
        bits    16

COM1    equ 0x3F8
SSAVE   equ 0x4EF

start:
        cli
        xor     ax, ax
        mov     ds, ax
        mov     es, ax
        mov     ss, ax
        mov     sp, 0x7C00
        sti

        ; ---- through the BIOS service
        mov     ah, 0x01
        mov     al, 'A'
        int     0x14
        mov     ah, 0x01
        mov     al, '1'
        int     0x14

        ; ---- through the registers, polling the line status like a driver
        mov     al, 'B'
        call    putc
        mov     al, '2'
        call    putc
        mov     al, 13
        call    putc
        mov     al, 10
        call    putc

        ; ---- and the divisor latch has to be writable, or a driver stalls
        mov     dx, COM1 + 3
        mov     al, 0x80
        out     dx, al
        mov     dx, COM1
        mov     al, 1
        out     dx, al                  ; divisor low: 115200
        mov     dx, COM1 + 1
        xor     al, al
        out     dx, al
        mov     dx, COM1 + 3
        mov     al, 0x03                ; back to 8N1, DLAB off
        out     dx, al
        mov     al, 'C'
        call    putc

        mov     al, 'D'
        call    putc
        mov     al, 13
        call    putc
        mov     al, 10
        call    putc

        mov     byte [0x4F0], 1         ; "I got this far", for the test
        jmp     $

; putc -- al out of COM1, waiting for the transmitter to have room
putc:
        push    ax
        push    dx
.wait:
        mov     dx, COM1 + 5            ; LSR
        in      al, dx
        test    al, 0x20                ; THRE
        jz      .wait
        pop     dx
        pop     ax
        mov     dx, COM1
        out     dx, al
        ret

        times   510 - ($ - $$) db 0
        dw      0xAA55
