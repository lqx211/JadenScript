; biosprobe: the calls a Linux setup asks before it does anything else, each
; with its answer left at 0x700 for the test to read back.
;
;   [0700] carry of int 15h AX=2401h (A20 on)       [0701] AX=2402h read-back
;   [0702] AX=E801h, kilobytes below 16 MiB          [0704] its BX
;   [0706] carry of int 15h AH=C0h                   [0707] int 13h AH=01
;   [0708] int 16h AH=02                             [0709] int 17h status
;   [070A] int 10h AH=12h                            [070C] int 1Ah AH=02 CX
;   [070E] its DX
        org     0x7c00
        bits    16
        xor     ax, ax
        mov     ds, ax
        mov     es, ax
        mov     di, 0x0700

        mov     ax, 0x2401              ; A20 on
        int     0x15
        mov     al, 0
        adc     al, 0
        stosb

        mov     ax, 0x2402              ; and read it back
        int     0x15
        stosb

        mov     ax, 0xE801              ; memory, the way a 1995 setup asks
        int     0x15
        mov     [0x0702], ax
        mov     [0x0704], bx

        mov     ax, 0xC000              ; system configuration: none
        int     0x15
        mov     al, 0
        adc     al, 0
        mov     [0x0706], al

        mov     ah, 0x01                ; the last disk status
        mov     dl, 0x80
        int     0x13
        mov     [0x0707], ah

        mov     ah, 0x02                ; keyboard shift flags
        int     0x16
        mov     [0x0708], al

        mov     ah, 0x01                ; is there a printer?
        xor     dx, dx
        int     0x17
        mov     [0x0709], ah

        mov     ax, 0x1210              ; is there a VGA?
        int     0x10
        mov     [0x070A], al

        mov     ah, 0x02                ; the real-time clock
        int     0x1A
        mov     [0x070C], cx
        mov     [0x070E], dx

        cli
        hlt
        jmp     $

        times   510 - ($ - $$) db 0
        dw      0xAA55
