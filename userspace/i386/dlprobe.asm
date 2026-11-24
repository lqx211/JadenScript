; ---------------------------------------------------------------------------
; dlprobe -- what a boot sector gets in DL, and what happens without it.
;
; MEMZ is the guest this exists for: its first instruction after the hand-off
; is `int 13h ah=02` to read its own stage 2, and it never sets DL, because
; every real BIOS leaves the drive it booted from there. A firmware that hands
; over whatever POST happened to leave in DL gets "no such drive" and a boot
; sector that decompresses a screenful of zeros -- a black screen that looks
; like the machine failed to boot at all.
;
; It also proves the destination segment: the read lands in ES:DI, and a
; sector read through DS:DI instead would land on the interrupt table.
;
; Writes AH (the int 13h status: 0 worked, 4 no such drive) to 0x07E0:0x0500,
; which is absolute 0x8300 with the segment the read used.
; ---------------------------------------------------------------------------

[bits 16]
org 0x7C00

STATUS equ 0x0500                   ; DS = 0x07E0, so this is absolute 0x8300
STAGE2 equ 0x0800                   ; ES = 0x07E0, DI = 0x0800 -> absolute 0x8600

start:
    mov     bx, 0x07E0
    mov     es, bx
    mov     ds, bx                  ; the segment MEMZ reads its frames in
    mov     ax, 0x0204              ; read, four sectors
    mov     cx, 0x0002              ; cylinder 0, sector 2: lba 1
    mov     dh, 0x00                ; head 0
    mov     bx, STAGE2              ; es:bx = 0x07E0:0x0800
    int     0x13                    ; DL is whatever the firmware left there
    mov     [STATUS], ah
    hlt

times 510-($-$$) db 0
dw      0xAA55
