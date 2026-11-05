; ---------------------------------------------------------------------------
; A boot sector, the way a real one starts: CS:IP = 0000:7C00, DS = ES = 0, no
; relocations, and the last two bytes 0xAA55 -- which is what int 19h checks
; before it hands over.
;
; It prints, reads sector 2 through int 13h, prints what it read, and stops.
; That is the whole contract a boot sector has: the firmware loaded it, it can
; call the firmware back, and it decides when the machine stops being the
; firmware's problem.
; ---------------------------------------------------------------------------

[bits 16]
org 0x7C00

STAGE2_AT equ 0x7E00                ; right above us, inside the 64 KiB

start:
    cli
    xor     ax, ax
    mov     ds, ax
    mov     es, ax
    mov     ss, ax
    mov     sp, 0x7C00
    cld
    sti

    mov     si, msg_hello
    call    puts

    xor     ax, ax
    mov     es, ax
    mov     bx, STAGE2_AT
    mov     ah, 0x02                ; read
    mov     al, 1                   ; one sector
    xor     ch, ch                  ; cylinder 0
    mov     cl, 2                   ; sector 2
    xor     dh, dh                  ; head 0
    mov     dl, 0x80                ; first hard disk
    int     0x13
    jc      disk_failed

    mov     si, STAGE2_AT
    call    puts
    mov     si, msg_done
    call    puts
.halt:
    cli
    hlt
    jmp     .halt

disk_failed:
    mov     si, msg_err
    call    puts
.halt2:
    cli
    hlt
    jmp     .halt2

; puts: si = zero-terminated string, ds = 0
puts:
    push    ax
    push    bx
    push    si
.next:
    lodsb
    test    al, al
    jz      .done
    mov     ah, 0x0E
    mov     bx, 0x0007
    int     0x10
    jmp     .next
.done:
    pop     si
    pop     bx
    pop     ax
    ret

msg_hello:  db "MBR: hello from a boot sector", 13, 10, 0
msg_done:   db "MBR: handing nothing on, halting", 13, 10, 0
msg_err:    db "MBR: int 13h read failed", 13, 10, 0

    times 510 - ($ - $$) db 0
    dw 0xAA55
