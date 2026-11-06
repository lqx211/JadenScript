; kernel -- the smallest thing that deserves the name: it turns the machine
; into a 386 with paging on, and then says so on the screen.
;
; This is the shape every kernel starts in. Load a GDT, switch to protected
; mode, build page tables, set CR3, set CR0.PG -- and from that moment every
; address the CPU produces goes through the tables. The text plane is
; identity-mapped by those tables, so the write at the end is also the proof:
; the store went through the pager like everything else.
;
; Sixteen-bit code on purpose. The operand path here is still sixteen bits
; wide, so a page-table entry is written as two word stores; that is not how a
; kernel is written, but it is the same hardware underneath.
;
;   nasm -f bin kernel.asm -o kernel.bin
;   python3 tools/i386_jsw_check.py --code kernel.bin --steps 4000
;
; memory it uses:
;   0x1000   the code
;   0x2000   the page directory
;   0x3000   the page table, mapping the first 1 MiB to itself
;   0x7C00   the stack
;   0xB8000  the text plane -- inside the mapped megabyte, which is why the
;            store at the end is evidence rather than decoration
bits 16
org 0x1000

PD      equ 0x2000
PT      equ 0x3000
TEXT    equ 0xB8000
PAGES   equ 256                 ; one megabyte, identity-mapped

start:
    cli
    xor     ax, ax
    mov     ds, ax
    mov     es, ax
    lgdt    [gdt_desc]
    mov     eax, cr0
    or      al, 1               ; PE
    mov     cr0, eax
    jmp     0x08:pm_entry

; ---- the GDT: flat code and data, sixteen-bit so the operand path fits ----
gdt:
    dq      0
    db      0xFF, 0xFF, 0x00, 0x00, 0x00, 0x9A, 0x00, 0x00   ; 0x08 code
    db      0xFF, 0xFF, 0x00, 0x00, 0x00, 0x92, 0x00, 0x00   ; 0x10 data, base 0
    db      0xFF, 0xFF, 0x00, 0x80, 0x0B, 0x92, 0x00, 0x00   ; 0x18 data, base 0xB8000
gdt_end:
gdt_desc:
    dw      gdt_end - gdt - 1
    dd      gdt

pm_entry:
    mov     ax, 0x10
    mov     ds, ax
    mov     es, ax
    mov     ss, ax
    mov     sp, 0x7C00

    ; ---- the page directory: one entry, pointing at the table ----
    mov     di, PD
    mov     ax, PT | 0x07       ; present, writable, user
    mov     [es:di], ax
    xor     ax, ax
    mov     [es:di + 2], ax     ; the base fits in sixteen bits

    ; ---- the page table: PAGES entries, each page mapped to itself ----
    ; The base of entry n is n * 0x1000, which needs twenty bits by the end,
    ; so it is carried in dx:ax and written as two words.
    mov     di, PT
    mov     cx, PAGES
    xor     ax, ax              ; low half of the base
    xor     dx, dx              ; high half
.map:
    push    ax
    push    dx
    or      ax, 0x07            ; present, writable, user
    mov     [es:di], ax
    mov     [es:di + 2], dx
    pop     dx
    pop     ax
    add     ax, 0x1000          ; next page
    adc     dx, 0
    add     di, 4
    loop    .map

    ; ---- paging on ----
    ; `mov eax, imm32` is the one thirty-two-bit form this operand path has,
    ; and it is enough: CR0.PG is bit 31 and the page directory fits in a word.
    mov     eax, PD
    mov     cr3, eax
    mov     eax, 0x80000001     ; PG and PE together
    mov     cr0, eax

    ; ---- and the proof: a store that had to go through the tables ----
    ; The text plane is at 0xB8000, which no sixteen-bit offset reaches from a
    ; base of zero, so it gets a descriptor whose base is exactly that.
    mov     ax, 0x18
    mov     es, ax
    xor     di, di
    mov     ax, 0x0F4B          ; 'K'
    mov     [es:di], ax
    mov     ax, 0x0F45          ; 'E'
    mov     [es:di + 2], ax
    mov     ax, 0x0F52          ; 'R'
    mov     [es:di + 4], ax
    mov     ax, 0x0F4E          ; 'N'
    mov     [es:di + 6], ax
    mov     ax, 0x0F45          ; 'E'
    mov     [es:di + 8], ax
    mov     ax, 0x0F4C          ; 'L'
    mov     [es:di + 10], ax
.halt:
    hlt
    jmp     .halt
