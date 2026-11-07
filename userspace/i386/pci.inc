; ---------------------------------------------------------------------------
; pciScan -- walk the bus the way a driver's first pass does.
;
; PCI is a bus of *indirections*: there is no fixed address to read a device
; from. You write a dword to 0xCF8 naming bus/slot/function/register, then read
; the dword back from 0xCFC, and a slot that never answered its vendor id reads
; as 0xFFFF. That is how a machine with an unknown board in it still boots: the
; firmware enumerates, and the driver is written against what it found.
;
; Up to 32 slots, and functions 1..7 only for a slot whose function 0 says it is
; multi-function -- which is what a real scan does, because probing functions of
; a single-function slot is eight times the work for nothing.
;
; Leaves [BDA_PCI_NDEV] = how many functions answered, and a record per function
; at PCI_TAB: slot, function, vendor (two bytes, little-endian), device (two).
; ---------------------------------------------------------------------------
PCI_ADDR    equ 0xCF8
PCI_DATA    equ 0xCFC
PCI_TAB     equ 0x700               ; eight bytes a record
PCI_NMAX    equ 16

pciScan:
    push    ax
    push    bx
    push    cx
    push    dx
    push    di
    mov     byte [es:BDA_PCI_NDEV], 0
    mov     di, PCI_TAB
    xor     bh, bh                  ; bh = the slot, 0..31
.slot:
    xor     bl, bl                  ; bl = the function, 0..7
.fn:
    call    pciSelectSlot           ; address this function's register 0
    mov     dx, PCI_DATA
    in      eax, dx
    ; a vendor id of 0xFFFF is "no device"; one of 0 is a bus stuck low, and
    ; neither is something to record
    test    eax, 0xFFFF
    jz      .absent
    cmp     eax, 0xFFFFFFFF
    je      .absent
    mov     ch, al                  ; vendor, low byte first
    mov     cl, ah
    shr     eax, 16
    cmp     byte [es:BDA_PCI_NDEV], PCI_NMAX
    jae     .norecord
    mov     [es:di], bh             ; slot
    mov     [es:di + 1], bl         ; function
    mov     [es:di + 2], ch
    mov     [es:di + 3], cl
    mov     [es:di + 4], al         ; device
    mov     [es:di + 5], ah
    mov     byte [es:di + 6], 0
    mov     byte [es:di + 7], 0
    add     di, 8
    inc     byte [es:BDA_PCI_NDEV]
.norecord:
    ; functions 1..7 exist only if function 0 is multi-function
    test    bl, bl
    jnz     .nextfn
    call    pciMulti
    test    al, al
    jz      .nextslot
.nextfn:
    inc     bl
    cmp     bl, 8
    jb      .fn
.nextslot:
    inc     bh
    cmp     bh, 32
    jb      .slot
    pop     di
    pop     dx
    pop     cx
    pop     bx
    pop     ax
    ret
.absent:
    ; a missing function 0 means the whole slot is empty
    test    bl, bl
    jz      .nextslot
    jmp     .nextfn

; ---------------------------------------------------------------------------
; pciMulti -- does the function just read have siblings? al = 1 if so.
; The header type lives in register 3, bits 16..23, and its bit 7 is the flag.
; ---------------------------------------------------------------------------
pciMulti:
    ; the answer is in al, so ax is *not* saved: restoring it here was the bug
    ; that made a multi-function slot look single, and the scan walked straight
    ; past the IDE controller's second function
    push    dx
    call    pciSelectSlot
    mov     dx, PCI_ADDR
    in      eax, dx                 ; what was just selected
    or      eax, 0x0C               ; register 3: byte offset 12
    out     dx, eax
    mov     dx, PCI_DATA
    in      eax, dx
    shr     eax, 16
    and     al, 0x80
    cmp     al, 0x80
    mov     al, 0
    jne     .no
    mov     al, 1
.no:
    pop     dx
    ret

; ---------------------------------------------------------------------------
; pciSelectSlot -- address the function in bh:bl, register 0
;
; The address dword is: bit 31 enable, bits 15..11 slot, bits 10..8 function,
; bits 7..2 the register, and the low two bits zero because it names a dword.
; ---------------------------------------------------------------------------
pciSelectSlot:
    push    eax
    push    ecx
    xor     eax, eax
    mov     al, bh
    shl     eax, 11
    xor     ecx, ecx
    mov     cl, bl
    shl     ecx, 8
    or      eax, ecx
    or      eax, 0x80000000
    call    pciSelect
    pop     ecx
    pop     eax
    ret

; ---------------------------------------------------------------------------
; pciSelect -- address the dword in eax on the config bus
; ---------------------------------------------------------------------------
pciSelect:
    push    edx
    mov     dx, PCI_ADDR
    out     dx, eax
    pop     edx
    ret
