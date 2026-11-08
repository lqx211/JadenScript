; linuxdisk2 -- the half of the loader that does not fit in a boot sector.
;
; linuxdisk.asm loads this to 0x0600 and jumps here. linuxboot.asm proves the
; handover; this one does the part that makes it a *disk*: read the kernel in,
; put every PT_LOAD segment where the ELF says it goes, and only then jump.
;
; The kernel's first LBA and its length are patched into the bottom of this
; file by tools/mklinuxdisk.py, which finds them by the 'KSDL' marker.
; Real mode cannot address 16 MB, and a kernel is linked there, so the reading
; happens in protected mode with a flat segment and our own ATA driver: five
; port writes and 256 `insw` a sector, no bounce buffer, no 64 KiB window. The
; firmware is used for exactly the two things it is good at -- the memory map
; and the first sector of the kernel -- and then left behind.

        org     0x0600
        bits    16

ZP_SEG   equ 0x1000               ; 0x10000: boot_params, the "zero page"
CM_SEG   equ 0x1200               ; 0x12000: the command line
GD_SEG   equ 0x1300               ; 0x13000: the descriptors
ZEROPAGE equ 0x10000              ; ... and the same three as linear addresses,
CMDLINE  equ 0x12000              ;     because that is what the kernel and
GDTBASE  equ 0x13000              ;     the protocol are given
ELFSEC   equ 0x8000               ; the kernel's first sector, read by the BIOS
DAP      equ 0x04F0               ; the disk address packet int 13h wants
KLBA     equ 0x0520               ; the kernel's first LBA, in sectors
KSEC     equ 0x0524               ; how long it is
ENTRY    equ 0x0528               ; where the kernel said to jump
COM1     equ 0x3F8
ATA_CMD  equ 0x1F0

start:
        cli
        xor     ax, ax
        mov     ds, ax
        mov     es, ax
        mov     ss, ax
        mov     sp, 0x7C00
        sti

        ; the A20 gate: without it nothing above the first megabyte is itself
        in      al, 0x92
        or      al, 2
        and     al, 0xFE               ; do not press the reset button
        out     0x92, al

        mov     si, msg_hello
        call    puts

        ; ---- the zero page is zeroed first: every field nobody sets is a
        ;      field the kernel reads, and a leftover is a lie. Nothing above
        ;      0x10000 fits in a 16-bit offset, so it goes through a segment.
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
        xor     di, di
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
        mov     dword [es:0x214], 0           ; code32_start, patched below
        mov     dword [es:0x218], 0           ; no initramfs yet
        mov     dword [es:0x21C], 0
        mov     word  [es:0x224], 0x8000      ; heap_end_ptr: real mode only
        mov     dword [es:0x228], CMDLINE     ; cmd_line_ptr
        mov     dword [es:0x22C], 0x37FFFFFF  ; initrd_addr_max
        mov     dword [es:0x230], 0x100000    ; kernel_alignment
        mov     byte  [es:0x234], 1           ; relocatable_kernel
        mov     dword [es:0x238], 2047        ; cmdline_size
        mov     byte  [es:0x1EF], 0xFF        ; sentinel

        ; ---- where the kernel is: patched in by the image builder, so the
        ;      loader never has to know how big a kernel is
        mov     eax, [patch + 4]
        mov     [KLBA], eax
        mov     eax, [patch + 8]
        mov     [KSEC], eax

        ; ---- the first sector of the kernel: its ELF header and program
        ;      headers, which is all the loader needs to know to place it
        mov     word  [DAP + 0], 16           ; size of this packet
        mov     word  [DAP + 2], 1            ; one sector
        mov     word  [DAP + 4], ELFSEC       ; offset in the segment
        mov     word  [DAP + 6], 0            ; segment
        mov     eax, [KLBA]
        mov     [DAP + 8], eax                ; lba, low half
        mov     dword [DAP + 12], 0           ; ... and high half
        mov     si, DAP
        mov     dl, 0x80                      ; the first hard disk
        mov     ah, 0x42                      ; read, the EDD way
        int     0x13
        jc      disk_failed

        ; ---- a flat pair of descriptors, then protected mode with paging off
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

        mov     edx, [0x8018]          ; e_entry
        mov     [ENTRY], edx
        mov     [ZEROPAGE + 0x214], edx ; code32_start is the entry, not a guess

        ; every program header in the first sector: a kernel has three, and
        ; anything with more than fits in 512 bytes says so by being wrong
        mov     esi, [0x801C]          ; e_phoff
        movzx   ecx, word [0x802C]     ; e_phnum
        add     esi, ELFSEC
        add     ecx, 0
        jz      kernel_ready
.phdr:
        cmp     dword [esi], 1         ; PT_LOAD
        jne     .next
        ; keep the header, because the ATA loop below needs its registers
        push    ecx
        push    esi
        mov     eax, [esi + 4]         ; p_offset
        mov     edi, [esi + 12]        ; p_paddr: where it must live
        mov     ecx, [esi + 16]        ; p_filesz: bytes on the disk
        mov     ebx, [esi + 20]        ; p_memsz: bytes once placed
        shr     eax, 9                 ; sectors into the image
        add     eax, [KLBA]
        call    load_sectors
        pop     esi
        mov     eax, [esi + 20]        ; p_memsz
        sub     eax, [esi + 16]        ; ... minus what was on the disk
        jbe     .done
        mov     ecx, eax
        xor     eax, eax
        rep     stosb                  ; the BSS part: zero, not whatever
.done:
        pop     ecx
.next:
        add     esi, 32                ; the size of an ELF32 program header
        dec     ecx
        jnz     .phdr

kernel_ready:
        mov     esp, 0x90000
        mov     esi, ZEROPAGE          ; the protocol's one argument
        mov     eax, 0x2BADB002        ; ... and its one magic number
        mov     ebx, 0
        mov     ebp, 0
        mov     edi, 0
        jmp     [ENTRY]

; load_sectors -- eax = first lba, edi = where it goes, ecx = how many bytes.
; ATA PIO, one sector at a time: 0x1F3..0x1F6 name the sector, 0x1F2 says how
; many, and 0x1F7 takes the command and then reports whether the drive is ready.
load_sectors:
        mov     ebp, ecx               ; bytes still to move
        mov     [0x540], eax           ; lba
        mov     [0x544], edi           ; destination
.sector:
        test    ebp, ebp
        jz      .out
        mov     eax, [0x540]
        mov     edi, [0x544]
        ; the sector number, high byte first: 0x1F6 carries 0xE0 | lba[27:24]
        mov     ecx, eax
        shr     ecx, 24
        or      cl, 0xE0
        mov     dx, 0x1F6
        mov     al, cl
        out     dx, al
        mov     eax, [0x540]
        mov     dx, 0x1F3
        out     dx, al
        mov     eax, [0x540]
        shr     eax, 8
        mov     dx, 0x1F4
        out     dx, al
        mov     eax, [0x540]
        shr     eax, 16
        mov     dx, 0x1F5
        out     dx, al
        mov     dx, 0x1F2
        mov     al, 1                  ; one sector a command
        out     dx, al
        mov     dx, 0x1F7
        mov     al, 0x20               ; READ SECTORS
        out     dx, al
.wait:
        in      al, dx
        test    al, 0x08               ; DRQ: the drive has the sector
        jnz     .ready
        test    al, 0x01               ; ERR: it never will
        jnz     disk_failed32
        jmp     .wait
.ready:
        mov     dx, ATA_CMD
        mov     ecx, 256               ; words in a sector
        rep     insw                   ; es:edi, and ES is flat
        add     dword [0x544], 512
        inc     dword [0x540]
        sub     ebp, 512
        jmp     .sector
.out:
        ret

disk_failed32:
        mov     eax, 0x0010            ; the machine is not going anywhere
        jmp     $

        bits    16
disk_failed:
        mov     si, msg_nodisk
        call    puts
.halt:
        hlt
        jmp     .halt

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

msg_hello:  db "linuxdisk: ", 0
msg_pm:     db "protected mode", 13, 10, 0
msg_nodisk: db "no kernel sector", 13, 10, 0
cmdline:    db "console=ttyS0,115200 earlyprintk=serial,ttyS0,115200", 0

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

        ; the image builder looks for this marker and writes the next two
        ; dwords: nothing loads a kernel from a fixed address any more
patch:  dd      'KSDL'
        dd      9                      ; the kernel's first lba
        dd      0                      ; how many sectors it is
