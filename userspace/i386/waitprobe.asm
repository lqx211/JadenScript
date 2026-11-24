org     0x7c00
        bits    16
        ; ask for a long wait (50 ms = 59,650 counts, over one counter) and for
        ; one that goes through in pieces, and record the clock either side of
        ; each: the point of the call is that the clock moves by the counts the
        ; request asked for, and that the CPU is what moves it.
        xor     ax, ax
        mov     ds, ax
        mov     es, ax
        call    clockread
        mov     [0x0702], ax           ; before the first wait
        mov     ah, 0x86
        xor     cx, cx
        mov     dx, 50000              ; 50 ms
        int     0x15
        mov     al, 0
        adc     al, 0
        mov     [0x0700], al           ; CF of the first wait
        call    clockread
        mov     [0x0704], ax           ; after it
        mov     ah, 0x86
        mov     cx, 1                  ; 1 << 16 us + 0 = 65.5 ms more
        mov     dx, 0
        int     0x15
        mov     al, 0
        adc     al, 0
        mov     [0x0701], al
        call    clockread
        mov     [0x0706], ax           ; after both
        mov     byte [0x0708], 0xA5    ; the waits are done
        cli
        hlt
        jmp     $

; clockread -- the chipset's count register, as one sixteen-bit number. The low
; byte latches the high one, so the pair cannot straddle a carry.
clockread:
    push    dx
    mov     dx, 0x5bd
    in      al, dx
    mov     ah, al
    inc     dx
    in      al, dx
    dec     dx
    xchg    al, ah
    pop     dx
    ret

        times   510 - ($ - $$) db 0
        dw      0xAA55
