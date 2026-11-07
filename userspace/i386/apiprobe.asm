; ---------------------------------------------------------------------------
; A boot sector that asks the firmware the two questions a kernel's loader
; asks: "what memory is there" (int 15h, E820) and "can you read a sector by
; its number" (int 13h, the EDD packet functions).
;
; Everything it learns is written to fixed addresses so a test can read it back
; out of the machine instead of parsing the screen. That is the whole point of
; this file: the answers are a contract, and a contract is worth testing.
; ---------------------------------------------------------------------------

[bits 16]
org 0x7C00

BUF     equ 0x800                   ; room for three E820 entries
COUNT   equ 0x7F0                   ; how many the firmware handed back
EDDBX   equ 0x7F2
EDDAH   equ 0x7F4
EDDCX   equ 0x7F6
PARAMS  equ 0x960                   ; the EDD drive parameters
SECTOR2 equ 0x900                   ; where a packet read lands

start:
    cli
    xor     ax, ax
    mov     ds, ax
    mov     es, ax
    mov     ss, ax
    mov     sp, 0x7C00
    sti

    ; ---- E820 ------------------------------------------------------------
    xor     bx, bx                  ; the first entry
    xor     bp, bp                  ; and how many we have taken
.e820:
    mov     ax, bp                  ; 20 bytes an entry: (n * 5) * 4
    mov     cx, 5
    mul     cx
    shl     ax, 2
    add     ax, BUF
    mov     di, ax
    mov     eax, 0xE820
    mov     edx, 0x534D4150
    mov     ecx, 20
    int     0x15
    jc      .e820_done
    inc     bp
    or      ebx, ebx
    jnz     .e820
.e820_done:
    mov     [COUNT], bp
    xor     ax, ax
    mov     ds, ax
    mov     es, ax

    ; ---- does the drive speak EDD? ---------------------------------------
    mov     ah, 0x41
    mov     bx, 0x55AA
    mov     dl, 0x80
    int     0x13
    mov     [EDDBX], bx
    mov     [EDDAH], ax
    mov     [EDDCX], cx

    ; ---- read sector 2 by its number -------------------------------------
    xor     ax, ax
    mov     ds, ax
    mov     si, dap
    mov     ah, 0x42
    mov     dl, 0x80
    int     0x13

    ; ---- the parameters, the EDD way -------------------------------------
    xor     ax, ax
    mov     ds, ax
    mov     si, PARAMS
    mov     ah, 0x48
    mov     dl, 0x80
    int     0x13

    hlt

dap:
    db      0x10, 0                 ; size, reserved
    dw      1                       ; one sector
    dw      SECTOR2                 ; offset
    dw      0                       ; segment
    dq      1                       ; lba 1, which is the second sector

times 510 - ($ - $$) db 0
dw 0xAA55
