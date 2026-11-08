; linuxboot -- hand a 32-bit Linux the machine, the way the boot protocol says.
;
; A kernel is not a boot sector: it does not read itself, and it does not look
; for the BIOS. It is loaded at 1 MB, given a `boot_params` page that says what
; the machine is, and entered in protected mode with the page's address in ESI
; (the 32-bit boot protocol). Everything it needs to know about memory comes
; from E820, which is why that table is the longest part of this file.
;
; This loader is deliberately separate from the BIOS and the boot menu: it is
; the piece that will be handed a real `vmlinux`, and until then a stub stands
; where the kernel goes, so the handover can be tested without one.

        org     0x7C00
        bits    16

ZP_SEG   equ 0x1000               ; 0x10000: boot_params, the "zero page"
CM_SEG   equ 0x1200               ; 0x12000: the command line
GD_SEG   equ 0x1300               ; 0x13000: the descriptors
ZEROPAGE equ 0x10000              ; ... and the three as linear addresses,
CMDLINE  equ 0x12000              ;     because that is what the kernel and
GDTBASE  equ 0x13000              ;     the protocol are given
KERNEL   equ 0x100000             ; where a kernel is loaded (1 MB)
COM1     equ 0x3F8

start:
        cli
        xor     ax, ax
        mov     ds, ax
        mov     es, ax
        mov     ss, ax
        mov     sp, 0x7C00
        sti

        ; the A20 gate, which is what makes memory above 1 MB the second
        ; megabyte rather than a mirror of the first
        in      al, 0x92
        or      al, 2
        and     al, 0xFE               ; do not press the reset button
        out     0x92, al

        mov     si, msg_hello
        call    puts

        ; ---- the zero page is zeroed first: every field nobody sets is a
        ;      field the kernel reads, and a leftover is a lie.
        ;      Every address below 0x10000 fits in a 16-bit offset and nothing
        ;      above it does, so the zero page is addressed through a segment.
        mov     ax, ZP_SEG
        mov     es, ax
        xor     di, di
        xor     eax, eax
        mov     cx, 4096 / 2
        rep     stosw

        ; ---- E820, straight from the BIOS, into the table the kernel reads
        mov     ax, ZP_SEG
        mov     es, ax
        mov     di, 0x2D0              ; e820_table
        xor     ebx, ebx               ; the continuation index
        xor     ebp, ebp               ; how many entries so far
.e820:
        mov     eax, 0xE820
        mov     edx, 0x534D4150        ; 'SMAP'
        mov     ecx, 20
        int     0x15
        jc      .e820_done
        cmp     eax, 0x534D4150
        jne     .e820_done
        add     di, 20
        inc     ebp
        test    ebx, ebx
        jnz     .e820
.e820_done:
        mov     ax, ZP_SEG
        mov     es, ax
        mov     [es:0x1E8], bp         ; e820_entries, one byte

        ; ---- the command line, into low memory and pointed at
        mov     ax, CM_SEG
        mov     es, ax
        mov     di, 0
        mov     si, cmdline
.copy:
        mov     al, [si]
        mov     [es:di], al
        inc     si
        inc     di
        test    al, al
        jnz     .copy

        ; ---- setup_header, the part of boot_params the kernel reads first
        mov     ax, ZP_SEG
        mov     es, ax
        mov     word  [es:0x1FE], 0xAA55      ; boot_flag
        mov     dword [es:0x202], 0x53726448  ; hdr: 'HdrS'
        mov     word  [es:0x206], 0x020F      ; protocol 2.15
        mov     byte  [es:0x210], 0xFF        ; type_of_loader: undefined
        mov     byte  [es:0x211], 0x01        ; loadflags: LOADED_HIGH
        mov     dword [es:0x214], KERNEL      ; code32_start
        mov     dword [es:0x218], 0           ; no initramfs yet
        mov     dword [es:0x21C], 0
        mov     word  [es:0x224], 0x8000      ; heap_end_ptr: real mode only
        mov     dword [es:0x228], CMDLINE     ; cmd_line_ptr
        mov     dword [es:0x22C], 0x37FFFFFF  ; initrd_addr_max
        mov     dword [es:0x230], 0x100000    ; kernel_alignment
        mov     byte  [es:0x234], 1           ; relocatable_kernel
        mov     dword [es:0x238], 2048        ; cmdline_size
        mov     byte  [es:0x1EF], 0xFF        ; sentinel

        ; ---- the descriptors: one flat pair, 4 GB either way
        mov     ax, GD_SEG
        mov     es, ax
        xor     di, di
        mov     si, gdt
        mov     cx, gdt_end - gdt
        rep     movsb
        mov     word [gdtr], gdt_end - gdt - 1
        mov     dword [gdtr + 2], GDTBASE
        lgdt    [gdtr]

        mov     si, msg_pm
        call    puts

        ; ---- protected mode, paging off: the kernel builds its own tables
        mov     eax, cr0
        or      eax, 1
        mov     cr0, eax
        db      0x66, 0xEA             ; far jump, 32-bit offset
        dd      pm32
        dw      0x08

        bits    32
pm32:
        mov     ax, 0x10
        mov     ds, ax
        mov     es, ax
        mov     fs, ax
        mov     gs, ax
        mov     ss, ax
        mov     esp, 0x90000
        mov     esi, ZEROPAGE          ; the protocol's one argument
        mov     eax, 0x2BADB002        ; ... and its one magic number
        jmp     KERNEL

        bits    16
; puts -- si = a string, out of COM1
puts:
        push    ax
        push    dx
        push    si
.next:
        mov     al, [si]
        test    al, al
        jz      .out
        call    putc
        inc     si
        jmp     .next
.out:
        pop     si
        pop     dx
        pop     ax
        ret

putc:
        push    ax
        push    dx
        mov     [ssave], al
.wait:
        mov     dx, COM1 + 5
        in      al, dx
        test    al, 0x20
        jz      .wait
        mov     al, [ssave]
        mov     dx, COM1
        out     dx, al
        pop     dx
        pop     ax
        ret

ssave:  db      0

msg_hello: db "linuxboot: ", 0
msg_pm:    db "protected mode", 13, 10, 0
cmdline:   db "console=ttyS0,115200 earlyprintk=serial", 0

gdt:
        dq      0
        ; code: base 0, limit 4 GB, 32-bit, readable
        dw      0xFFFF, 0x0000
        db      0x00, 0x9A, 0xCF, 0x00
        ; data: base 0, limit 4 GB, writable
        dw      0xFFFF, 0x0000
        db      0x00, 0x92, 0xCF, 0x00
gdt_end:

gdtr:   dw      0
        dd      0

        times   510 - ($ - $$) db 0
        dw      0xAA55
