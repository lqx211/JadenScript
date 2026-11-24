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
; The scratch lives inside this image, not in the free-looking bytes below
; 0x7C00: the BIOS uses some of those itself, and an int 13h that zeroes the
; packet it was just handed is a boot that reads the wrong sector for ever.
DAP      equ dap
KLBA     equ klba
KSEC     equ ksec
KENTER   equ kenter
KOFF     equ koff
ILBA     equ ilba
ISEC     equ isec
IADDR    equ iaddr
ATALBA   equ atalba
ATADST   equ atadst
ATABAT   equ atabat
BAT      equ 32                 ; sectors one ATA command covers
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

        mov     ax, ZP_SEG
        mov     es, ax

        ; ---- screen_info: the console asks the card what it was, even with a
        ;      serial console on the command line
        mov     word  [es:0x04], 16384        ; ext_mem_k
        mov     word  [es:0x08], 3            ; orig_video_mode: 80x25 text
        mov     word  [es:0x0A], 80           ; columns
        mov     word  [es:0x0C], 3            ; orig_video_ega_bx
        mov     word  [es:0x0E], 25           ; lines
        mov     byte  [es:0x0F], 1            ; orig_video_isVGA
        mov     word  [es:0x10], 16           ; points

        ; ---- where the kernel is: patched in by the image builder, so the
        ;      loader never has to know how big a kernel is
        mov     eax, [patch + 4]
        mov     [KLBA], eax
        mov     eax, [patch + 8]
        mov     [KSEC], eax
        mov     eax, [patch + 16]
        mov     [KOFF], eax             ; the payload starts this far in
        mov     eax, [patch + 12]
        mov     [KENTER], eax
        mov     eax, [patch + 20]
        mov     [ILBA], eax
        mov     eax, [patch + 24]
        mov     [ISEC], eax
        mov     eax, [patch + 28]
        mov     [IADDR], eax

        ; ---- the first sector of the image: the setup header the kernel
        ;      describes itself with
        mov     word  [DAP + 0], 16           ; size of this packet
        ; two sectors, not one: the setup header runs from 0x1F1 to 0x270 --
        ; `HdrS`, `code32_start`, `initrd_addr_max` and `init_size` are all past
        ; 0x200 -- so half of it is in the sector after the boot sector. Copying
        ; one sector's worth put zeros in `init_size`, which is a decompressor
        ; told it has no memory to work in.
        mov     word  [DAP + 2], 2            ; two sectors
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

        ; ---- the setup header, copied out of the kernel's own boot sector --
        ;      after that sector has been read. It was copied *before* the read
        ;      for a while, which looks like a working loader: the bytes land in
        ;      boot_params and a stub that only checks `code32_start` (written
        ;      later, by us) says everything is fine. What it actually copied
        ;      was whatever the BIOS had left at 0x8000, so `init_size` -- how
        ;      much memory the decompressor is allowed to use -- was a number
        ;      the loader invented.
        mov     ax, ZP_SEG
        mov     es, ax
        xor     ax, ax
        mov     ds, ax
        mov     si, ELFSEC + 0x1F1
        mov     di, 0x1F1
        mov     cx, 0x80                ; through init_size, at 0x264
        rep     movsb                   ; DS:SI -> ES:DI, four bytes a step

        ; ---- and now the fields that are *ours*, because the copy above just
        ;      overwrote them with the kernel's own (zero) placeholders:
        ;      `cmd_line_ptr` was written before the copy for a while, which is
        ;      a command line the kernel never sees -- no console, no serial.
        mov     ax, ZP_SEG
        mov     es, ax
        mov     byte  [es:0x210], 0xFF        ; type_of_loader: undefined
        or      byte  [es:0x211], 0x81        ; LOADED_HIGH | CAN_USE_HEAP
        mov     word  [es:0x224], 0x8000      ; heap_end_ptr: real mode only
        mov     dword [es:0x228], CMDLINE     ; cmd_line_ptr
        mov     dword [es:0x218], 0           ; no initramfs yet
        mov     dword [es:0x21C], 0
        mov     byte  [es:0x1EF], 0xFF        ; sentinel
        mov     word  [es:0x20A], 0xFFFF      ; vid_mode: leave the card alone

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

        ; Interrupts off across the switch. Between `mov cr0` and the far jump
        ; the machine is in protected mode with no IDT, and a timer tick that
        ; lands there reads a descriptor table that does not exist yet.
        cli
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

        ; ---- the kernel, in one piece, where its own decompressor expects to
        ;      be. What the image holds is the protected-mode part of a
        ;      bzImage: the stub that unpacks the payload, with the compressed
        ;      stream behind it at the offset that stub was built against. It
        ;      is loaded contiguously and entered at the address the header
        ;      names, exactly as a BIOS loader would, because the stub sets up
        ;      its own page tables and needs nothing from us but the zero page.
        ;
        ;      Loading the *decompressed* vmlinux instead -- the obvious thing,
        ;      and what this did first -- is a machine that runs a kernel at
        ;      its link address with no page tables: the first %fs: access
        ;      reads a linear address there is no memory at, and the kernel
        ;      triple faults before it prints a character.
        mov     eax, [KENTER]
        mov     [ZEROPAGE + 0x214], eax ; code32_start, as the header says
        mov     edi, eax
        mov     ecx, [KSEC]            ; the payload's own sector count
        shl     ecx, 9                 ; sectors to bytes
        mov     eax, [KLBA]
        add     eax, [KOFF]            ; past the setup stub
        call    load_sectors

        ; ---- the initramfs, and the two numbers boot_params needs for it.
        ;      Without one the kernel has no /init to run and stops with
        ;      "VFS: Unable to mount root fs"; with one it unpacks this and
        ;      executes the program inside it.
        mov     ecx, [ISEC]
        test    ecx, ecx
        jz      .no_initrd
        shl     ecx, 9
        mov     edi, [IADDR]
        mov     eax, [ILBA]
        call    load_sectors
        mov     eax, [IADDR]
        mov     [ZEROPAGE + 0x218], eax ; initrd_addr
        mov     eax, [ISEC]
        shl     eax, 9
        mov     [ZEROPAGE + 0x21C], eax ; initrd_size
.no_initrd:

kernel_ready:
        push    0                      ; a defined EFLAGS, not the loader's
        popfd
        mov     esp, 0x90000
        mov     esi, ZEROPAGE          ; boot_params, the protocol's one
        mov     eax, 0x2BADB002        ; argument, and its magic number
        mov     ebx, 0
        mov     ebp, 0
        mov     edi, 0
        jmp     [KENTER]

; load_sectors -- eax = first lba, edi = where it goes, ecx = how many bytes.
; ATA PIO, one sector at a time: 0x1F3..0x1F6 name the sector, 0x1F2 says how
; many, and 0x1F7 takes the command and then reports whether the drive is ready.
load_sectors:
        mov     ebp, ecx               ; bytes still to move
        mov     [ATALBA], eax          ; lba
        mov     [ATADST], edi          ; destination
.sector:
        test    ebp, ebp
        jle     .out                   ; the last sector makes the count negative
        mov     eax, [ATALBA]
        mov     edi, [ATADST]
        ; the sector number, high byte first: 0x1F6 carries 0xE0 | lba[27:24]
        mov     ecx, eax
        shr     ecx, 24
        or      cl, 0xE0
        mov     dx, 0x1F6
        mov     al, cl
        out     dx, al
        mov     eax, [ATALBA]
        mov     dx, 0x1F3
        out     dx, al
        mov     eax, [ATALBA]
        shr     eax, 8
        mov     dx, 0x1F4
        out     dx, al
        mov     eax, [ATALBA]
        shr     eax, 16
        mov     dx, 0x1F5
        out     dx, al
        ; How many sectors this command covers: up to BAT sectors, and never
        ; more than are left. The drive steps the LBA itself between them, so
        ; the five port writes and the poll are paid once a batch instead of
        ; once a sector -- which is most of what a Scratch ATA read costs.
        mov     ebx, ebp
        add     ebx, 511
        shr     ebx, 9                 ; sectors still to read
        cmp     ebx, BAT
        jbe     .batch
        mov     ebx, BAT
.batch:
        mov     [ATABAT], ebx
        mov     dx, 0x1F2
        mov     al, bl
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
        mov     ebx, [ATABAT]
        shl     ebx, 8                 ; words in a batch: 256 a sector
        mov     ecx, ebx
        ; `rep insw`: the element is a word (the 66 prefix) and the address is
        ; still EDI, because a string instruction's address size comes from the
        ; 67 prefix and not from the operand size. It is also the difference
        ; between a step per sector and a step per word -- thirty thousand
        ; sectors is thirty thousand instructions, not thirty million.
        rep     insw
        mov     ebx, [ATABAT]
        add     [ATALBA], ebx          ; the lba counts sectors
        shl     ebx, 9                 ; the destination counts bytes
        add     [ATADST], ebx
        sub     ebp, ebx
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
; Two consoles on purpose: tty0 is the card this machine paints, so whatever
; the kernel says is on the stage where someone can see it, and ttyS0 is the
; same text in `X.serialText` for anything reading the machine from outside.
cmdline:    db "console=tty0 console=ttyS0,115200 earlyprintk=serial,ttyS0,115200", 0

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

        ; the image builder looks for this marker and writes the next three
        ; dwords: nothing loads a kernel from a fixed address any more
klba:   dd      9                      ; patched by the image builder
ksec:   dd      0
kenter: dd      0x100000               ; ... and the address it is entered at
koff:   dd      0                      ; the payload's first sector in the image
ilba:   dd      0                      ; the initramfs' first lba
isec:   dd      0                      ; ... and how many sectors
iaddr:  dd      0x4000000              ; ... and where it goes
atalba: dd      0
atadst: dd      0
atabat: dd      0
dap:    times   16 db 0

patch:  dd      'KSDL'
        dd      9                      ; the kernel image's first lba
        dd      0                      ; how many sectors the payload is
        dd      0x100000               ; ... and where it is entered
        dd      0                      ; ... and how many setup sectors to skip
        dd      0                      ; the initramfs' first lba
        dd      0                      ; ... how many sectors it is
        dd      0x4000000              ; ... and where it goes
