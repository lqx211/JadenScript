; kstub32 -- a kernel-shaped stub, for proving the handover without a kernel.
;
; It is loaded at 1 MB and entered exactly the way Linux is: protected mode,
; paging off, ESI pointing at boot_params, EAX holding 0x2BADB002. What it does
; is report, on the serial port, the three things the loader promised -- that it
; arrived at all, that boot_params says 'HdrS', and that the E820 table has
; entries in it -- and then stop.

        org     0x100000
        bits    32

COM1    equ 0x3F8

start:
        ; the magic number is in EAX, so nothing may touch AL until it has been
        ; read: `mov al, 'K'` and then `cmp eax, magic` is how this stub first
        ; reported a failure it did not have
        mov     dx, COM1
        mov     bl, 'K'                 ; "a kernel is running at 1 MB"
        call    putb

        cmp     eax, 0x2BADB002         ; the protocol's magic number
        jne     .bad

        mov     eax, [esi + 0x202]      ; hdr
        cmp     eax, 0x53726448         ; 'HdrS'
        jne     .bad
        mov     bl, 'H'
        call    putb

        mov     eax, [esi + 0x214]      ; code32_start
        cmp     eax, 0x100000
        jne     .bad
        mov     bl, 'C'
        call    putb

        movzx   ecx, byte [esi + 0x1E8] ; e820_entries
        cmp     ecx, 2
        jb      .bad
        mov     bl, 'E'
        call    putb
        add     cl, '0'                 ; how many, as a digit
        mov     bl, cl
        call    putb

        mov     bl, 13
        call    putb
        mov     bl, 10
        call    putb
        jmp     .stop

.bad:
        mov     bl, '!'
        call    putb
.stop:
        cli
        hlt
        jmp     .stop

; putb -- bl out of COM1, with eax left exactly as it was found: the protocol's
; magic number lives in it, and `mov al` is enough to lose it
putb:
        push    eax
        mov     al, bl
        out     dx, al
        pop     eax
        ret
