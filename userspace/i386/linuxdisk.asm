; linuxdisk -- a boot sector that boots Linux from the disk it lives on.
;
; 512 bytes is not enough loader to do this properly, so the sector is only a
; trampoline: it reads the rest of the loader (the next eight sectors) to 0x0600
; through the BIOS, and jumps there. Everything after that happens in
; linuxdisk2.asm.
;
; The disk is laid out by tools/mklinuxdisk.py:
;
;   LBA 0            this sector
;   LBA 1 .. 8       the second stage, at 0x0600
;   LBA 9 ..         an uncompressed vmlinux: an ELF32 with PT_LOAD segments

        org     0x7C00
        bits    16

STAGE2   equ 0x0600
DAP      equ 0x0500

start:
        cli
        xor     ax, ax
        mov     ds, ax
        mov     es, ax
        mov     ss, ax
        mov     sp, 0x7C00
        sti

        ; ---- the next eight sectors, through the BIOS: on a disk this small
        ;      there is no point writing a driver twice, and sectors 2..9 of
        ;      track 0 are addressable the way an 8086 always could
        mov     ax, STAGE2 >> 4         ; a segment, not an address
        mov     es, ax
        xor     bx, bx                  ; es:bx = 0x0600
        mov     ah, 0x02                ; read
        mov     al, 8                   ; eight sectors
        xor     ch, ch                  ; cylinder 0
        mov     cl, 2                   ; from sector 2
        xor     dh, dh                  ; head 0
        mov     dl, 0x80                ; the first hard disk
        int     0x13
        jc      .failed

        jmp     0x0000:STAGE2

.failed:
        mov     si, msg_nodisk
        call    puts
.halt:  hlt
        jmp     .halt

puts:
        push    ax
        push    dx
        push    si
.next:  mov     al, [si]
        test    al, al
        jz      .out
        call    putc
        inc     si
        jmp     .next
.out:   pop     si
        pop     dx
        pop     ax
        ret

putc:
        push    ax
        push    dx
        mov     [ssave], al
.wait:  mov     dx, 0x3FD
        in      al, dx
        test    al, 0x20
        jz      .wait
        mov     al, [ssave]
        mov     dx, 0x3F8
        out     dx, al
        pop     dx
        pop     ax
        ret

ssave:  db      0
msg_nodisk: db "no stage 2", 13, 10, 0

        times   510 - ($ - $$) db 0
        dw      0xAA55
