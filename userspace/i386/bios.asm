; ---------------------------------------------------------------------------
; i386 bios: power-on self test, a text console, and a boot.
;
; Real mode, because that is where a PC starts: CS = 0xF000, IP = 0xFFF0, and
; the ROM assembled at zero so every fits in sixteen bits.
; nasm -f bin gives us exactly the 64 KiB window a ROM occupies; the reset
; vector is the last sixteen bytes of it.
;
; The services here are the ones a boot sector actually calls:
;
;   int 0x10   ah=0x0E teletype output, ah=0x00 set mode, ah=0x0F get mode,
;              ah=0x03 get cursor
;   int 0x11   equipment word
;   int 0x12   memory size in KiB
;   int 0x13   ah=0x02 read sectors (CHS, 63 sectors, 16 heads, disk 0x80)
;   int 0x16   ah=0x00 wait for a key, ah=0x01 poll
;   int 0x19   boot: load sector 0 to 0x7C00 and jump
;   int 0x1A   ah=0x00 read the tick count
;
; The battery-backed CMOS is a real device on ports 0x70/0x71, and this firmware
; uses it the way a PC does: memory size and equipment come *out* of it (so the
; same ROM describes a different machine), the clock is read from it, and the
; boot counter at 0x30 is incremented and written back, which is the one thing
; that proves the battery is still connected across a power cycle.
;
; The handlers keep ds pointing at the ROM segment (so `mov si, msg` works),
; es at zero for the BIOS data area, and fs at the text plane. Saving bp first
; and moving sp into it puts the flags the CPU pushed at [bp+6], which is how a
; handler returns a carry flag: iret pops the flags, so setting them after the
; fact would be pointless.
; ---------------------------------------------------------------------------

[bits 16]
org 0

TEXT_SEG        equ 0xB800
DISK_SEG        equ 0xC000          ; the staged disk image, 128 sectors
COLS            equ 80                  ; the default card, and the splash's art
ROWS            equ 30
MAXCOLS         equ 160                 ; the widest plane the card can present,
MAXROWS         equ 60                  ; matching X.dispMaxCols/Rows

BDA_CURSOR_COL  equ 0x450
BDA_CURSOR_ROW  equ 0x451
BDA_TICKS       equ 0x46C
BDA_KB_HEAD     equ 0x41A
BDA_KB_TAIL     equ 0x41C
BDA_KB_BUFFER   equ 0x41E
KB_RING         equ 32                ; the buffer's own bytes: sixteen words
BDA_KB_PEND     equ 0x4F7             ; 1 between an E0 and the code it names
BDA_EQUIP       equ 0x410
BDA_MEMK        equ 0x413
BDA_VIDEO_MODE  equ 0x449             ; the mode int 10h last set
BDA_ARG_AL      equ 0x4E4             ; int 10h's own copy of its arguments
BDA_LAST_STATUS equ 0x4EE             ; int 13h's last status byte
BDA_READ_ERRORS equ 0x603             ; ... and how many reads have failed
BDA_RESET       equ 0x4F5             ; the firmware asks the chipset to restart
BDA_ARG_AH      equ 0x4E5
; scratch the console keeps between calls: free space in the data area, at an
; address that is RAM on every PC. It must *not* live in the ROM, which is
; read-only on a real board and on this machine
ARG_AX          equ 0x4F0
ARG_AH          equ 0x4F1
ARG_BL          equ 0x4F2
BDA_PCI_NDEV     equ 0x6FF         ; how many PCI functions answered
BDA_CORES       equ 0x4F3
PIC_CMD         equ 0x20
PIC_MSK         equ 0x21
PIT_CH0         equ 0x40
PIT_CMD         equ 0x43
PIT_RATE        equ 11932           ; 1.193182 MHz / 100 Hz, the divisor a PC uses
CON_ATTR        equ 0x4F4          ; the attribute the console prints with
BDA_BOOTDEV     equ 0x4FE          ; the device int 19h hands over to
BDA_BOOTFORCE   equ 0x4FF          ; 1: a menu chose BDA_BOOTDEV, try only it
BDA_BOOTPTR     equ 0x4F8          ; offset:segment the boot sector is at
CDDRIVE         equ 0x9F           ; the number every BIOS gives the CD-ROM
CDBUF           equ 0x5000         ; one 2048-byte CD sector of scratch
CON_T           equ 0x52E          ; the first row of the console's window
CON_B           equ 0x52F          ; ... and the last
CON_W           equ 0x530          ; how wide it is, from column 0
COM1            equ 0x3F8          ; the first serial port
SSAVE           equ 0x4EF          ; int 14h's one byte of scratch
BDA_VIDCOLS     equ 0x531          ; how many of those columns the card can show
BDA_VIDROWS     equ 0x532          ; ... and how many rows
BDA_SPLASH      equ 0x533          ; 1 while the power-on picture is on screen
BDA_CLIP        equ 0x534          ; 1 while the console clips instead of wrapping
SU_ROWSAVE      equ 0x520

; ---------------------------------------------------------------------------
; post
; ---------------------------------------------------------------------------
start:
    cli
    xor     ax, ax
    mov     ds, ax
    mov     es, ax
    mov     ss, ax
    mov     sp, 0x7C00
    cld
    ; Interrupts stay off until the vectors are installed and the two chips
    ; that raise them are programmed. A cold board cannot tell the difference
    ; -- its 8259 is uninitialised and its 8254 is not counting -- but a warm
    ; restart inherits both from the firmware that asked for it, so an IRQ0
    ; lands on whatever vector happens to be there and the CPU walks off into
    ; the ROM. `sti` is below, once every vector and both chips are set up.

    ; the text plane: 80 x 30 cells of (char, attribute)
    mov     ax, TEXT_SEG
    mov     es, ax
    xor     di, di
    mov     cx, MAXCOLS * MAXROWS
    mov     ax, 0x0720              ; space, light grey on black
    rep     stosw

    ; the BIOS data area
    xor     ax, ax
    mov     es, ax
    mov     byte [es:BDA_CURSOR_COL], 0
    mov     byte [es:BDA_CURSOR_ROW], 0
    mov     byte [es:CON_ATTR], 0x07
    ; the console's window starts as the whole screen; the splash narrows it
    mov     byte [es:CON_T], 0
    mov     byte [es:CON_B], ROWS - 1
    mov     byte [es:CON_W], COLS
    mov     byte [es:BDA_VIDCOLS], COLS
    mov     byte [es:BDA_VIDROWS], ROWS
    mov     byte [es:BDA_SPLASH], 0
    mov     byte [es:BDA_CLIP], 0
    ; until the menu says otherwise the machine boots the disk
    mov     byte [es:BDA_BOOTDEV], 0x80
    ; the keyboard ring the interrupt fills and int 16h drains starts empty,
    ; at the buffer itself rather than at zero -- zero is not inside the ring,
    ; and a head there would have the first store land in the data area's own
    ; bytes
    mov     word [es:BDA_KB_TAIL], BDA_KB_BUFFER
    mov     word [es:BDA_KB_HEAD], BDA_KB_BUFFER
    mov     byte [es:BDA_KB_PEND], 0

    ; what this machine has comes out of the CMOS, not out of this listing:
    ; 0x14 is the equipment word, 0x15/0x16 the base memory in KiB
    mov     al, 0x14
    call    cmos_read
    mov     ah, al
    mov     al, 0x15
    call    cmos_read
    mov     bl, al
    mov     al, 0x16
    call    cmos_read
    mov     bh, al
    mov     [es:BDA_EQUIP], ax
    mov     [es:BDA_MEMK], bx
    ; two word stores, not one dword store: this firmware keeps every operand
    ; sixteen bits wide, which is what "16-bit bus" on the banner means
    mov     word [es:BDA_TICKS], 0
    mov     word [es:BDA_TICKS + 2], 0

    ; a POST that found memory is a POST that passed
    mov     al, 0x31
    mov     cl, 0xA5
    call    cmos_write

    ; ---- the two chips the tick depends on ------------------------------
    ; The 8259: four initialization words, all of them to the *command* port --
    ; only the mask goes to the port next door, and getting that wrong is how a
    ; controller ends up waiting for an ICW2 that never comes, acknowledging
    ; one interrupt and then never another. ICW1 initialize, ICW2 vectors start
    ; at 0x08, ICW3 a slave on IRQ2, ICW4 8086 mode.
    mov     al, 0x11
    out     PIC_CMD, al
    mov     al, 0x08
    out     PIC_CMD, al
    mov     al, 0x04
    out     PIC_CMD, al
    mov     al, 0x01
    out     PIC_CMD, al
    mov     al, 0xFE                ; OCW1: only IRQ0 gets through
    out     PIC_MSK, al
    ; The 8254: channel 0, both bytes, mode 2 (rate generator), binary. The
    ; divisor is the one a PC uses for 100 Hz -- and on this board, whose clock
    ; is the instruction counter, it comes out at about that.
    mov     al, 0x34
    out     PIT_CMD, al
    mov     al, PIT_RATE % 256
    out     PIT_CH0, al
    mov     al, PIT_RATE / 256
    out     PIT_CH0, al
    mov     al, 0xB6                ; channel 2, both bytes, mode 3: the speaker
    out     PIT_CMD, al
    mov     al, 0x9C                ; 533 Hz, which nothing listens to yet
    out     PIT_CH0 + 2, al
    mov     al, 0x2E
    out     PIT_CH0 + 2, al

    ; the interrupt vector table
    ;
    ; It is filled with one that answers before any of the real handlers go in,
    ; so no vector is ever 0000:0000. That address is not "nothing happens": it
    ; is the table itself, and a call to a missing vector runs the table as
    ; code, stores through whatever registers the guest had, and then halts on
    ; the first 0xF4 it meets. That is what a failed GRUB boot looked like.
    mov     ax, cs
    push    es
    xor     di, di
    xor     dx, dx
    mov     es, dx
    mov     bx, intDefault
.fill_vectors:
    mov     [es:di], bx
    mov     [es:di + 2], ax
    add     di, 4
    cmp     di, 0x400
    jb      .fill_vectors
    pop     es

    mov     bx, 0x10 * 4
    mov     dx, int10
    call    set_vector
    mov     bx, 0x11 * 4
    mov     dx, int11
    call    set_vector
    mov     bx, 0x12 * 4
    mov     dx, int12
    call    set_vector
    mov     bx, 0x13 * 4
    mov     dx, int13
    call    set_vector
    mov     bx, 0x14 * 4
    mov     dx, int14
    call    set_vector
    mov     bx, 0x16 * 4
    mov     dx, int16
    call    set_vector
    mov     bx, 0x17 * 4
    mov     dx, int17
    call    set_vector
    mov     bx, 0x18 * 4
    mov     dx, int18
    call    set_vector
    mov     bx, 0x19 * 4
    mov     dx, int19
    call    set_vector
    mov     bx, 0x1A * 4
    mov     dx, int1a
    call    set_vector
    mov     bx, 0x08 * 4
    mov     dx, int08
    call    set_vector
    mov     bx, 0x09 * 4
    mov     dx, int09
    call    set_vector
    mov     bx, 0x15 * 4
    mov     dx, int15
    call    set_vector
    mov     bx, 0x1C * 4
    mov     dx, int1c
    call    set_vector

    ; from here on the ROM is the data segment
    mov     ax, cs
    mov     ds, ax

    ; the emulation settings in the CMOS go to the chipset before anything
    ; reads them back
    call    hwApply

    ; everything that can interrupt now has somewhere to go
    sti

    ; how much of the plane the card can actually show. On a 640 x 480 stage
    ; that is all of it; on Scratch's own 480 x 360 it is 60 x 22, and a BIOS
    ; that keeps printing at column 70 would be printing into the void.
    call    cardGeometry
    ; A card narrower than the plane is a window on it, not a narrower plane:
    ; a line that does not fit loses its tail rather than folding onto the next
    ; row and writing over the row below. That fold is what turned the log into
    ; a heap of fragments on Scratch's own 480 x 360 stage.
    mov     byte [es:BDA_CLIP], 1

    ; the splash goes up first. A card smaller than the artwork is a window on
    ; it -- the picture is anchored at its bottom, where the logo is -- so the
    ; only stage that gets no splash is one too small to hold the logo at all.
    cmp     byte [es:BDA_VIDCOLS], 20
    jb      .nosplash
    cmp     byte [es:BDA_VIDROWS], 8
    jb      .nosplash
    call    gxSplashShow
.nosplash:
    mov     si, msg_step_post
    call    splashStep

    mov     si, msg_banner
    call    puts

    ; the boot counter at CMOS 0x30: read, add one, write back, then show it.
    ; This is the battery, stated as a fact the guest can see.
    mov     al, 0x30
    call    cmos_read
    inc     al
    mov     cl, al
    mov     al, 0x30
    call    cmos_write
    mov     si, msg_bootno
    call    puts
    mov     al, cl
    call    puthex
    mov     si, msg_crlf
    call    puts

    ; and the clock, straight out of the RTC registers
    mov     al, 0x04
    call    cmos_read
    call    puthex
    mov     al, ':'
    call    putc
    mov     al, 0x02
    call    cmos_read
    call    puthex
    mov     al, ':'
    call    putc
    mov     al, 0x00
    call    cmos_read
    call    puthex
    mov     si, msg_crlf
    call    puts
    mov     si, msg_cpu
    call    puts
    ; the size comes out of the CMOS, so it is printed rather than baked in
    mov     si, msg_mem
    call    puts
    mov     ax, [es:BDA_MEMK]
    call    put3
    mov     si, msg_memk
    call    puts
    ; and the memory above the megabyte, out of the same CMOS the setup wrote:
    ; a board that only ever says "640K" looks like a board with no sockets,
    ; which is exactly what the memory page exists to change
    mov     al, 0x17
    call    cmos_read
    mov     bl, al
    mov     al, 0x18
    call    cmos_read
    mov     bh, al
    mov     si, msg_memx
    call    puts
    mov     ax, bx
    or      ax, ax
    jz      .noext
    call    putn
    mov     si, msg_memxk
    call    puts
    jmp     .afterext
.noext:
    mov     si, msg_memx0
    call    puts
.afterext:
    ; the card said how big it is, so the banner says so too
    mov     si, msg_video
    call    puts
    mov     al, [es:BDA_VIDCOLS]
    xor     ah, ah
    call    putn
    mov     al, 'x'
    call    putc
    mov     al, [es:BDA_VIDROWS]
    xor     ah, ah
    call    putn
    mov     si, msg_video_tail
    call    puts
    ; how many cores the board has, and the SMP bring-up: copy the AP
    ; trampoline into low RAM, wake every other core, wait for them to check in
    mov     dx, 0x5A0
    in      al, dx
    xor     ah, ah
    mov     [es:BDA_CORES], al
    mov     si, msg_cores
    call    puts
    mov     al, [es:BDA_CORES]
    xor     ah, ah
    call    putn
    mov     si, msg_crlf
    call    puts

    call    smpBoot

    ; the PCI bus: how many functions answered, which is the number a driver's
    ; first pass over the bus would count for itself
    call    pciScan
    mov     si, msg_pci
    call    puts
    mov     al, [es:BDA_PCI_NDEV]
    xor     ah, ah
    call    putn
    mov     si, msg_pci_tail
    call    puts

    mov     ah, 0x00                ; COM1 up, 115200 8N1: anything that wants
    int     0x14                    ; to report on the way in can just write
    mov     si, msg_step_boot
    call    splashStep
    call    suWindow                ; five seconds to press DEL or M
    cmp     al, 2
    je      .setup
    cmp     al, 1
    je      .menu
    call    gxSplashHide
    jmp     .boot
.setup:
    call    gxSplashHide
    call    gxEnter                 ; comes back when the setup is left
    call    hwApply                 ; a saved clock takes effect now
    jmp     .boot
.menu:
    call    gxSplashHide
    call    bootMenu                ; does not return: it boots, reboots or halts
.boot:
    int     0x19                    ; says which device it is trying, then boots

    mov     si, msg_nodev
    call    puts
.halt:
    cli
    hlt
    jmp     .halt

; ---------------------------------------------------------------------------
; cardGeometry -- ask the card how much of the plane it can actually show.
;
; Ports 0x5A3 and 0x5A4 are the card's own; a real VGA answers that pair with
; CRTC timing, this one answers with the cells that fit on the stage. Scratch's
; own stage is 480 x 360, which is 60 x 22 cells: printing at column 70 there
; is printing into the void, so the console window is cut down to what fits.
; ---------------------------------------------------------------------------
cardGeometry:
    push    ax
    push    dx
    push    es
    xor     ax, ax
    mov     es, ax
    mov     dx, 0x5A3               ; columns
    in      al, dx
    or      al, al                  ; a card that says nothing has the lot
    jnz     .havecols
    mov     al, COLS
.havecols:
    cmp     al, MAXCOLS
    jbe     .colok
    mov     al, MAXCOLS
.colok:
    mov     [es:BDA_VIDCOLS], al
    mov     [es:CON_W], al
    mov     dx, 0x5A4               ; rows
    in      al, dx
    or      al, al
    jnz     .haverows
    mov     al, ROWS
.haverows:
    cmp     al, MAXROWS
    jbe     .rowok
    mov     al, MAXROWS
.rowok:
    mov     [es:BDA_VIDROWS], al
    dec     al
    mov     [es:CON_B], al
    pop     es
    pop     dx
    pop     ax
    ret

; set_vector: bx = the vector's address in the table, dx = the handler,
; ax = the segment it lives in
set_vector:
    push    es
    push    di
    push    ax
    push    cx
    xor     cx, cx
    mov     es, cx
    mov     [es:bx], dx
    mov     [es:bx + 2], ax
    pop     cx
    pop     ax
    pop     di
    pop     es
    ret

; cmos_read: al = the register, returns al = its value
cmos_read:
    push    dx
    mov     dx, 0x70
    out     dx, al
    mov     dx, 0x71
    in      al, dx
    pop     dx
    ret

; cmos_write: al = the register, cl = the value
cmos_write:
    push    dx
    push    ax
    mov     dx, 0x70
    out     dx, al
    mov     al, cl
    mov     dx, 0x71
    out     dx, al
    pop     ax
    pop     dx
    ret

; putc: al = a character, printed through the console
putc:
    push    ax
    push    bx
    mov     ah, 0x0E
    mov     bl, [es:CON_ATTR]
    int     0x10
    pop     bx
    pop     ax
    ret

; putn: ax = a number, printed in decimal with no leading zeros
putn:
    push    ax
    push    bx
    push    cx
    push    dx
    mov     bx, 10
    xor     cx, cx
.divide:
    xor     dx, dx
    div     bx
    push    dx
    inc     cx
    test    ax, ax
    jnz     .divide
.digits:
    pop     ax
    add     al, '0'
    call    putc
    loop    .digits
    pop     dx
    pop     cx
    pop     bx
    pop     ax
    ret

; puthex: al = a byte, printed as two hex digits
puthex:
    push    ax
    push    bx
    push    cx
    mov     ah, al
    mov     cl, 4
    shr     al, cl
    call    .digit
    mov     al, ah
    call    .digit
    pop     cx
    pop     bx
    pop     ax
    ret
.digit:
    and     al, 0x0F
    cmp     al, 10
    jb      .small
    add     al, 'a' - 10
    jmp     putc
.small:
    add     al, '0'
    jmp     putc

; ---------------------------------------------------------------------------
; smpBoot -- put the APs to work.
;
; Real firmware copies a trampoline into low memory and sends the other cores
; there; this does the same, through the board's release port. Each core reads
; its own number back from the chipset and writes a marker, and the boot core
; waits (bounded: a machine with a dead core must still POST) for every marker
; before it says how many are online.
; ---------------------------------------------------------------------------
smpBoot:
    push    ax
    push    bx
    push    cx
    push    dx
    push    si
    push    di
    push    es
    push    ds
    mov     al, [es:BDA_CORES]
    cmp     al, 2
    jb      .done                   ; one core: nothing to bring up
    ; the trampoline, from the ROM to 0x8000
    mov     ax, cs
    mov     ds, ax
    mov     si, apTramp
    xor     ax, ax
    mov     es, ax
    mov     di, 0x8000
    mov     cx, apTrampEnd - apTramp
    cld
    rep     movsb
    ; wake every core but this one
    mov     bl, 2
.wake:
    cmp     bl, [es:BDA_CORES]
    ja      .wait
    mov     al, bl
    mov     dx, 0x5A1
    out     dx, al
    inc     bl
    jmp     .wake
.wait:
    mov     cx, 2000                ; a bounded wait, not an infinite one
.spin:
    mov     bl, 2
.check:
    cmp     bl, [es:BDA_CORES]
    ja      .online
    xor     bh, bh
    cmp     byte [es:bx + 0x8500], 1
    jne     .again
    inc     bl
    jmp     .check
.again:
    loop    .spin
.online:
    mov     si, msg_smp
    call    puts
    ; count who checked in, starting with this core
    mov     dl, 1
    mov     bl, 1
.count:
    cmp     bl, [es:BDA_CORES]
    ja      .report
    xor     bh, bh
    cmp     byte [es:bx + 0x8500], 1
    jne     .next
    inc     dl
.next:
    inc     bl
    jmp     .count
.report:
    xor     ax, ax
    mov     al, dl
    call    putn
    mov     si, msg_cores_online
    call    puts
.done:
    pop     ds
    pop     es
    pop     di
    pop     si
    pop     dx
    pop     cx
    pop     bx
    pop     ax
    ret

; The AP trampoline: position independent, entered at 0x0800:0x0000 with its
; own registers and the shared memory it was given.
apTramp:
    cli
    xor     ax, ax
    mov     ds, ax
    mov     es, ax
    mov     ss, ax
    mov     sp, 0x7C00
    mov     dx, 0x5A2               ; who am I?
    in      al, dx
    mov     bl, al
    xor     bh, bh
    mov     byte [bx + 0x8400], al  ; the roster
    mov     byte [bx + 0x8500], 1   ; and the check-in
apHalt:
    cli
    hlt
    jmp     apHalt
apTrampEnd:

; puts: si = a zero-terminated string in the ROM segment
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

; ---------------------------------------------------------------------------
; int 10h: the console
; ---------------------------------------------------------------------------
clear_text:
    push    ax
    push    cx
    push    di
    push    es
    mov     byte [es:BDA_CURSOR_ROW], 0
    mov     byte [es:BDA_CURSOR_COL], 0
    mov     ax, TEXT_SEG
    mov     es, ax
    xor     di, di
    mov     cx, MAXCOLS * MAXROWS
    mov     ax, 0x0720
    rep     stosw
    pop     es
    pop     di
    pop     cx
    pop     ax
    ret

; tty: al = character, bl = attribute, ds = the ROM, es = 0
tty:
    push    ax
    push    bx
    push    cx
    push    dx
    push    si
    push    di
    push    fs
    push    bp
    mov     si, ax                  ; si = the character
    cmp     al, 13
    jne     .not_cr
    mov     byte [es:BDA_CURSOR_COL], 0
    jmp     .out
.not_cr:
    cmp     al, 10
    jne     .not_lf
    inc     byte [es:BDA_CURSOR_ROW]
    jmp     .scroll
.not_lf:
    cmp     al, 8
    jne     .print
    cmp     byte [es:BDA_CURSOR_COL], 0
    je      .out
    dec     byte [es:BDA_CURSOR_COL]
    jmp     .out
.print:
    cmp     byte [es:BDA_CLIP], 0
    je      .wrap
    mov     cl, [es:BDA_CURSOR_COL]
    cmp     cl, [es:CON_W]
    jae     .out                    ; the clip: the rest of the line is lost
.wrap:
    mov     cl, [es:BDA_CURSOR_ROW]
    xor     ch, ch
    mov     dl, [es:CON_W]          ; the card's stride, not the 80 this was
    xor     dh, dh                  ; written against: at 128 columns a row is
    mov     ax, cx                  ; 128 cells, and using 80 puts every line
    mul     dx                      ; of the log on the row above
    mov     cl, [es:BDA_CURSOR_COL]
    xor     ch, ch
    add     ax, cx
    shl     ax, 1
    mov     di, ax
    mov     cx, TEXT_SEG
    mov     fs, cx
    mov     ax, si
    mov     ah, bl
    mov     [fs:di], ax
    inc     byte [es:BDA_CURSOR_COL]
    mov     al, [es:CON_W]
    cmp     [es:BDA_CURSOR_COL], al
    jb      .out
    mov     byte [es:BDA_CURSOR_COL], 0
    inc     byte [es:BDA_CURSOR_ROW]
.scroll:
    mov     al, [es:BDA_CURSOR_ROW]
    cmp     al, [es:CON_B]
    jbe     .out
    ; Only the window's own columns move. Copying whole rows would drag the
    ; splash's artwork up with the log, because the two share their rows.
    mov     cl, [es:CON_W]
    xor     ch, ch
    mov     al, [es:CON_B]
    sub     al, [es:CON_T]
    xor     ah, ah
    mov     bx, ax                  ; how many rows move up
    test    bx, bx                  ; a one-row window moves nothing
    jz      .blank
    mov     al, [es:CON_T]
    xor     ah, ah
    mov     bp, COLS * 2
    mov     bl, [es:CON_W]
    xor     bh, bh
    add     bp, 0
    mov     bp, bx
    add     bp, bp                  ; the card's stride, in bytes
    mul     bp
    mov     di, ax                  ; the window's top left, in the plane
    mov     si, ax
    add     si, bp                  ; one row further down
    mov     bp, bx
    add     bp, bp
    sub     bp, cx
    sub     bp, cx                  ; the gap at the end of each row
    push    es
    push    ds
    mov     ax, TEXT_SEG
    mov     es, ax
    mov     ds, ax
.row:
    push    cx
    rep     movsw
    pop     cx
    add     si, bp
    add     di, bp
    dec     bx
    jnz     .row
    pop     ds
    pop     es
.blank:
    ; and the window's last row is blanked
    mov     al, [es:CON_B]
    xor     ah, ah
    mov     bl, [es:CON_W]
    xor     bh, bh
    mov     bp, bx
    add     bp, bp
    mul     bp
    mov     di, ax
    mov     ah, [es:CON_ATTR]
    mov     al, ' '
    mov     cl, [es:CON_W]
    xor     ch, ch
    push    es
    mov     bx, TEXT_SEG
    mov     es, bx
    rep     stosw
    pop     es
    mov     al, [es:CON_B]
    mov     [es:BDA_CURSOR_ROW], al
    jmp     .out
.out:
    pop     bp
    pop     fs
    pop     di
    pop     si
    pop     dx
    pop     cx
    pop     bx
    pop     ax
    ret

; ---------------------------------------------------------------------------
; int 11h / 12h: what this machine has
; ---------------------------------------------------------------------------
int11:
    push    ds
    xor     ax, ax
    mov     ds, ax
    mov     ax, [ds:BDA_EQUIP]
    pop     ds
    iret

int12:
    push    ds
    xor     ax, ax
    mov     ds, ax
    mov     ax, [ds:BDA_MEMK]
    pop     ds
    iret

; ---------------------------------------------------------------------------
; int 13h: read sectors from the staged disk
; ---------------------------------------------------------------------------
int13:
    push    bp
    mov     bp, sp
    push    bx
    push    cx
    push    dx
    push    si
    push    di
    push    ds
    ; Two drive numbers, one image: 0x80 is the hard disk it looks like, and
    ; 0x9F is the CD-ROM the same bytes also are. They differ in the one thing
    ; a caller measures a drive by -- a CD counts in 2048-byte sectors -- and a
    ; boot that goes through El Torito is handed the CD's number, so a BIOS
    ; that only knows 0x80 answers every read with "no such drive".
    ; Everything else answers "no such drive" rather than silently reading the
    ; same sector again.
    cmp     dl, 0x80
    je      .drives
    cmp     dl, CDDRIVE
    jne     .fail
.drives:
    cmp     ah, 0x02
    je      .read
    cmp     ah, 0x00                ; reset: nothing to reset yet
    je      .ok
    cmp     ah, 0x01                ; the status of the last operation
    je      .last
    cmp     ah, 0x41                ; does this drive speak EDD?
    je      .edd_check
    cmp     ah, 0x42                ; read sectors through a packet
    je      .edd_read
    cmp     ah, 0x48                ; drive parameters, the EDD way
    je      .edd_params
    cmp     ah, 0x08                ; drive parameters, the old way
    jne     .fail
    ; ---- the geometry was 80 cylinders, 16 heads and 63 sectors for every
    ;      drive, which is a 40 MB disk. The image is five megabytes today, so
    ;      a partition tool asking this question was being told about space
    ;      that is not there. The drive's own capacity is the only source that
    ;      cannot be out of date: 16 heads and 63 sectors is the layout every
    ;      BIOS since 1984 has used, so only the cylinder count has to move.
    cmp     dl, CDDRIVE
    je      .fail                   ; a CD has no cylinders, heads or sectors
    call    ataCapacity
    jc      .fail
    xor     edx, edx
    mov     ebx, 1008               ; 16 heads * 63 sectors: one cylinder
    div     ebx
    test    eax, eax
    jnz     .cyls
    mov     eax, 1                  ; a disk smaller than a cylinder still has one
.cyls:
    cmp     eax, 1024
    jbe     .cyls_ok
    mov     eax, 1024               ; the low ten bits are all the registers hold
.cyls_ok:
    mov     bx, ax                  ; cx = cyl[7:0] in ch, cyl[9:8] in cl[7:6]
    mov     ch, bl
    shr     ax, 8
    and     al, 3
    shl     al, 6
    or      al, 63
    mov     cl, al
    mov     dh, 15                  ; heads 0..15
    mov     dl, 1                   ; and one drive on this channel
    mov     [ss:bp - 4], cx         ; outputs leave through the slots they were
    mov     [ss:bp - 6], dx         ; pushed into, because the pops below
    jmp     .ok                     ; put the caller's registers back
.last:
    push    bx
    push    es
    xor     ax, ax
    mov     es, ax
    mov     al, [es:BDA_LAST_STATUS]
    mov     ah, al
    pop     es
    pop     bx
    jmp     .out

.edd_check:
    ; BX = the signature and AH = the version we speak; CX bit 0 says packet
    ; commands work, which is the only thing a loader actually asks about
    mov     bx, 0xAA55
    mov     ah, 0x30
    mov     cx, 1                   ; packet commands work
    mov     [ss:bp - 2], bx
    mov     [ss:bp - 4], cx
    jmp     .ok_ah                  ; AH is the version, not a status byte
.edd_params:
    ; A drive's size is its own answer, not a number a ROM was built with:
    ; IDENTIFY DEVICE, words 60 and 61, read through the data register. A
    ; 7-megabyte CD told "128 sectors" is a loader that stops at the first
    ; megabyte of a file it can see is longer.
    push    es
    push    di
    push    si
    push    esi
    push    bx
    push    cx
    push    dx
    mov     ax, ds
    mov     es, ax                  ; the caller's buffer is at ds:si
    mov     di, 512                 ; bytes a sector: the disk's answer
    call    ataCapacity             ; eax = sectors, carry when it will not say
    jc      .params_unknown
    mov     dx, [ss:bp - 6]
    cmp     dl, CDDRIVE
    jne     .params_known
    shr     eax, 2                  ; a CD counts in 2048-byte sectors
    mov     di, 2048
.params_known:
    mov     [si + 12], eax          ; total sectors, the low dword ...
    mov     dword [si + 16], 0      ; ... and the high half of the qword
    jmp     .params_fill
.params_unknown:
    mov     dword [si + 12], 128    ; a drive that would not say
    mov     dword [si + 16], 0
.params_fill:
    mov     byte [si], 0x1A
    mov     byte [si + 1], 1        ; geometry is the LBA one, not CHS
    mov     word [si + 4], 16       ; heads
    mov     word [si + 6], 0
    mov     word [si + 8], 63       ; sectors a track
    mov     word [si + 10], 0
    mov     [si + 20], di           ; bytes a sector
    ; cylinders = ceil(total / 1008), so that the product the caller computes
    ; from this geometry is never less than the drive
    mov     esi, eax
    add     esi, 1007
    mov     eax, esi
    xor     edx, edx
    mov     ecx, 1008
    div     ecx
    mov     [si + 2], ax
    pop     dx
    pop     cx
    pop     bx
    pop     esi
    pop     si
    pop     di
    pop     es
    jmp     .ok
.edd_read:
    ; the disk address packet: count, offset, segment, then a 64-bit lba
    mov     cx, [si + 2]            ; how many sectors
    mov     di, [si + 4]            ; where the caller wants them
    mov     dx, [si + 6]
    push    es
    mov     es, dx
    mov     esi, [si + 8]           ; the lba -- not EBP, which is this
    mov     dx, [ss:bp - 6]         ; handler's own frame pointer
    cmp     dl, CDDRIVE             ; a CD counts in 2048-byte sectors
    je      .edd_cd
    call    disk_read_lba
    jmp     .edd_done
.edd_cd:
    call    cd_read_lba
.edd_done:
    pop     es
    jc      .fail
    jmp     .ok
.read:
    ; a CD has no cylinders, heads or sectors to compute an lba from: the only
    ; way to read one is the packet, so the old-style read answers "no such
    ; track" rather than inventing a geometry for it
    cmp     dl, CDDRIVE
    je      .fail
    ; the sector count rides on the stack: ax is about to become the lba
    push    ax
    ; lba = (cyl * 16 + head) * 63 + sector - 1
    mov     al, ch
    xor     ah, ah
    shl     ax, 4
    mov     dl, dh
    xor     dh, dh
    add     ax, dx
    mov     dx, 63
    mul     dx
    mov     dx, cx
    and     dx, 0x3F
    dec     dx
    add     ax, dx
    movzx   esi, ax                 ; the lba, which is already a sector number
    mov     di, bx
    pop     cx                      ; cl = the sector count
    xor     ch, ch
    call    disk_read_lba           ; through the drive, not through a copy
    jc      .fail
.ok:
    and     word [ss:bp + 6], 0xFFFE ; clear carry: it worked
    xor     ah, ah
    call    store_status
    jmp     .out
.ok_ah:
    and     word [ss:bp + 6], 0xFFFE
    jmp     .out
.fail:
    or      word [ss:bp + 6], 1     ; set carry: it did not
    mov     ah, 0x04                ; sector not found
    push    ax
    push    es
    xor     ax, ax
    mov     es, ax
    inc     byte [es:BDA_READ_ERRORS]
    pop     es
    pop     ax
    call    store_status
.out:
    pop     ds
    pop     di
    pop     si
    pop     dx
    pop     cx
    pop     bx
    pop     bp
    iret

; ---------------------------------------------------------------------------
; int 13h keeps its last status byte at 40:04EE, a field every BIOS has there.
; Writing it through the *caller's* ES put it inside the buffer the read had
; just filled: a boot sector that loads its own stage 2 to 7E0:0000 -- MEMZ
; does exactly that, with int 13h ah=02 al=4 -- found byte 4EE of its
; compressed image replaced by the status, which is a zero. The image then
; decompressed into four thousand wrong bytes of nonsense and printed its
; message out of the middle of a mask bitmap. The field has an address of its
; own; this is how to reach it without touching the caller's registers.
;
;   ah = the status, es = the caller's segment
; ---------------------------------------------------------------------------
store_status:
    push    ax
    push    bx
    push    es
    mov     bx, ax                  ; bh = the status
    xor     ax, ax                  ; segment 0: the equates are linear
    mov     es, ax                  ; addresses, and 0x40:0x4EE is 0x8EE --
    mov     [es:BDA_LAST_STATUS], bh ; base memory, where a loader may live
    pop     es
    pop     bx
    pop     ax
    ret

; ---------------------------------------------------------------------------
; disk_read_lba: the drive, one sector at a time.
;
; The firmware used to answer int 13h out of a copy of the disk that lived in
; RAM, which was honest while the disk *was* 128 sectors of a build-time list.
; It is not honest now: the drive is a device, the image is a file on the host,
; and a boot from a cold disk is exactly the case that has to work. So a read
; is the same five port writes and 256 `insw` an operating system would do.
;
;   esi = first lba        cx = how many sectors        es:di = where
;   carry set on a drive error, and AH is left as 0x04 so the caller's int 13h
;   can report "sector not found" the way its callers expect
; ---------------------------------------------------------------------------
disk_read_lba:
    push    eax
    push    ecx
    push    edx
    push    bx
    push    di
    push    es
    push    si
    push    esi
.sector:
    jcxz    .done
    ; the sector number, high byte first: 0x1F6 carries 0xE0 | lba[27:24]
    mov     eax, esi
    shr     eax, 24
    or      al, 0xE0
    mov     dx, 0x1F6
    out     dx, al
    mov     eax, esi
    mov     dx, 0x1F3
    out     dx, al
    mov     eax, esi
    shr     eax, 8
    mov     dx, 0x1F4
    out     dx, al
    mov     eax, esi
    shr     eax, 16
    mov     dx, 0x1F5
    out     dx, al
    mov     dx, 0x1F2
    mov     al, 1                   ; one sector a command
    out     dx, al
    mov     dx, 0x1F7
    mov     al, 0x20                ; READ SECTORS
    out     dx, al
.wait:
    in      al, dx
    test    al, 0x08                ; DRQ: the drive has the sector
    jnz     .ready
    test    al, 0x01                ; ERR: it never will
    jnz     .failed
    jmp     .wait
.ready:
    mov     dx, 0x1F0
    push    cx
    mov     cx, 256                 ; words in a sector
    rep     insw
    pop     cx
    inc     esi
    dec     cx
    jmp     .sector
.done:
    clc
    jmp     .out
.failed:
    stc
.out:
    pop     esi
    pop     si
    pop     es
    pop     di
    pop     bx
    pop     edx
    pop     ecx
    pop     eax
    ret

; ---------------------------------------------------------------------------
; ataCapacity: the drive's own idea of how big it is.
;
; IDENTIFY DEVICE hands over 256 words through the data register, and words 60
; and 61 are the sector count, low word first. The drive is asked instead of
; trusted to a constant because the same port answers for the staged image
; (128 sectors), for a host's ISO (14,336) and for a kernel (31,761), and a
; loader that is told the wrong one stops in the middle of a file it can see
; is longer.
;
;   returns eax = sectors of 512 bytes, carry set when the drive will not say
; ---------------------------------------------------------------------------
ataCapacity:
    push    bx
    push    cx
    push    dx
    push    si
    push    di
    mov     dx, 0x1F6
    mov     al, 0xE0                ; master, LBA
    out     dx, al
    mov     dx, 0x1F2                ; the count is unused by IDENTIFY, but a
    xor     al, al                   ; drive reads the four registers in order
    out     dx, al
    inc     dx                       ; 0x1F3
    out     dx, al
    inc     dx                       ; 0x1F4
    out     dx, al
    inc     dx                       ; 0x1F5
    out     dx, al
    mov     dx, 0x1F7
    mov     al, 0xEC                 ; IDENTIFY DEVICE
    out     dx, al
    ; a thousand polls, not sixty-five thousand: a real drive answers IDENTIFY
    ; in well under a millisecond, and every poll is an instruction the guest
    ; paid for. The CD probe asks this on every boot, and 65,536 of them was
    ; most of a million instructions before the boot had started.
    mov     cx, 1024
.wait:
    in      al, dx
    test    al, 0x08                 ; DRQ: the words are waiting
    jnz     .read
    test    al, 0x01                 ; ERR: they never will be
    jnz     .none
    loop    .wait
    jmp     .none
.read:
    mov     dx, 0x1F0
    xor     si, si                   ; word 60, the count's low half
    xor     di, di                   ; ... and word 61, the high one
    xor     cx, cx                   ; which word is next
.words:
    in      ax, dx
    cmp     cx, 60
    jne     .not60
    mov     si, ax
.not60:
    cmp     cx, 61
    jne     .next
    mov     di, ax
.next:
    inc     cx
    cmp     cx, 256
    jb      .words
    movzx   eax, si
    movzx   ecx, di
    shl     ecx, 16
    or      eax, ecx
    clc
    jmp     .out
.none:
    stc
.out:
    pop     di
    pop     si
    pop     dx
    pop     cx
    pop     bx
    ret

; ---------------------------------------------------------------------------
; cd_read_lba: esi = a CD's sector, cx = how many, es:di = where they go.
;
; A CD sector is four of the drive's 512-byte ones, and everything above this
; call counts in the 2048-byte unit: El Torito names its entries that way, the
; drive number 0x9F is addressed that way, and GRUB's own loader reads that
; way. The firmware's own sector stays 512 bytes, because that is what the
; image is made of.
;
; The destination steps a full CD sector per read and moves the segment when
; the offset would wrap: GRUB asks for 24 of them in one int 13h and 24 * 2048
; is more than a 64 KiB window holds from 0x8000. A segment step of 0x80
; paragraphs is exactly 2048 bytes, so the wrapped offset is the right one.
; ---------------------------------------------------------------------------
cd_read_lba:
    push    eax
    push    ecx
    push    esi
    push    edi
.loop:
    jcxz    .done
    mov     eax, esi
    shl     eax, 2                  ; four 512-byte sectors to a CD sector
    push    cx
    push    esi
    mov     esi, eax
    mov     cx, 4
    call    disk_read_lba           ; es:di, and it leaves DI alone
    pop     esi
    pop     cx
    jc      .failed
    inc     esi
    add     di, 2048
    jnc     .next
    ; the offset wrapped, so the whole 64 KiB the segment would have added is
    ; missing from the address: a paragraph is 16 bytes, and 65536 / 16 is
    ; 0x1000. (Bumping by 0x80 -- one CD sector's worth -- is the mistake this
    ; had: it re-reads a quarter of the buffer for every wrap.)
    mov     ax, es
    add     ax, 0x1000
    jc      .failed                 ; past 0xFFFF0: nothing to read into
    mov     es, ax
.next:
    dec     cx
    jmp     .loop
.done:
    clc
    jmp     .out
.failed:
    stc
.out:
    pop     edi
    pop     esi
    pop     ecx
    pop     eax
    ret

; ---------------------------------------------------------------------------
; El Torito: the CD's own boot entry.
;
; An ISO's boot record is not a boot sector. The firmware has to find it: see
; that volume descriptor 16 says this is an ISO at all, walk the descriptors
; for the boot record, take the catalog address out of it, read the catalog's
; default entry, and load the image that entry names -- with DL = 0x9F in the
; register the image expects its drive in, because that is the number its
; sectors are counted by.
;
; Only the no-emulation entry is booted (the entry's media type is zero). That
; is what every modern ISO uses, GRUB's among them; the floppy and hard-disk
; emulations from 1995 need a whole virtual drive underneath this one, and
; nothing on this machine is booted from a 1.44 MB image pretending to be a
; disk.
;
;   returns carry clear with the entry loaded and BDA_BOOTPTR/BDA_BOOTDEV set
; ---------------------------------------------------------------------------
cdProbe:
    push    eax
    push    ebx
    push    ecx
    push    edx
    push    esi
    push    edi
    push    ds
    push    es
    xor     ax, ax
    mov     ds, ax
    mov     es, ax
    mov     ebx, 16                 ; the volume descriptors start here
.desc:
    mov     esi, ebx
    call    cdSector
    jc      .no
    cmp     dword [CDBUF + 1], 0x30304443   ; "CD00"
    jne     .no                     ; not an ISO: nothing here to boot
    cmp     byte [CDBUF + 5], '1'
    jne     .no
    cmp     byte [CDBUF], 0         ; a boot record?
    je      .boot
    inc     ebx
    cmp     ebx, 20
    jbe     .desc
    jmp     .no
.boot:
    cmp     word [CDBUF + 7], 0x4C45        ; "EL", as in EL TORITO
    jne     .no
    mov     esi, [CDBUF + 0x47]             ; the boot catalog's sector
    call    cdSector
    jc      .no
    cmp     byte [CDBUF], 1                 ; the validation entry
    jne     .no
    cmp     word [CDBUF + 30], 0xAA55       ; ... and its own end
    jne     .no
    cmp     byte [CDBUF + 32], 0x88         ; the default entry, bootable
    jne     .no
    cmp     byte [CDBUF + 33], 0            ; no emulation: a flat image
    jne     .no
    movzx   ecx, word [CDBUF + 38]          ; sectors to load, 512-byte units
    test    cx, cx
    jnz     .count
    mov     cx, 4                           ; the spec's default
.count:
    mov     bx, [CDBUF + 34]                ; the segment it asks for
    test    bx, bx
    jnz     .seg
    mov     bx, 0x07C0                      ; zero means the usual place
.seg:
    mov     esi, [CDBUF + 40]               ; the image's first sector
    add     cx, 3
    shr     cx, 2                           ; a CD is read in whole sectors
    mov     es, bx
    xor     di, di
    call    cd_read_lba
    jc      .no
    xor     ax, ax
    mov     es, ax
    mov     dword [es:BDA_BOOTPTR], 0       ; offset 0 of ...
    mov     [es:BDA_BOOTPTR + 2], bx        ; ... the segment it asked for
    mov     al, CDDRIVE
    mov     [es:BDA_BOOTDEV], al
    clc
    jmp     .out
.no:
    stc
.out:
    pop     es
    pop     ds
    pop     edi
    pop     esi
    pop     edx
    pop     ecx
    pop     ebx
    pop     eax
    ret

; cdSector: esi = a CD sector, read into CDBUF.
cdSector:
    push    cx
    push    di
    push    es
    xor     ax, ax
    mov     es, ax
    mov     di, CDBUF
    mov     cx, 1
    call    cd_read_lba
    pop     es
    pop     di
    pop     cx
    ret

; ---------------------------------------------------------------------------
; int 14h: the first serial port.
;
; COM1 is at 0x3F8 and the card is a 16550, so the port itself is what a driver
; wants; this is the BIOS's own way in, for the firmware and for a loader that
; has no driver of its own. Linux does not use it -- its 8250 driver talks to
; the registers directly -- but everything that has to *report* on the way to
; Linux does.
;
;   AH = 0x00  initialize: AL has the parameters, which are ignored here
;   AH = 0x01  send AL, wait for the transmitter, return AH = the status
;   AH = 0x02  receive into AL, AH bit 7 set on a timeout
;   AH = 0x03  status, in AH
; ---------------------------------------------------------------------------
int14:
    push    dx
    push    si
    mov     dx, COM1
    cmp     ah, 0x00
    je      .init
    cmp     ah, 0x01
    je      .send
    cmp     ah, 0x02
    je      .recv
    cmp     ah, 0x03
    jne     .bad
    call    serStatus
    jmp     .out
.init:
    add     dx, 3                   ; LCR
    mov     al, 0x80                ; DLAB: the next two are the divisor
    out     dx, al
    mov     dx, COM1
    mov     al, 1                   ; 115200
    out     dx, al
    inc     dx
    xor     al, al
    out     dx, al
    mov     dx, COM1
    add     dx, 3
    mov     al, 0x03                ; 8 bits, no parity, one stop
    out     dx, al
    mov     dx, COM1
    inc     dx
    xor     al, al                  ; interrupt enable: nobody to interrupt
    out     dx, al
    mov     dx, COM1
    add     dx, 4
    mov     al, 0x03                ; DTR and RTS, so a peer sees us
    out     dx, al
    mov     ah, 0x60
    jmp     .out
.send:
    mov     [es:SSAVE], al
.wait:
    call    serStatus
    test    ah, 0x20                ; the transmitter has room
    jz      .wait
    mov     dx, COM1
    mov     al, [es:SSAVE]
    out     dx, al
    call    serStatus
    jmp     .out
.recv:
    call    serStatus
    test    ah, 0x01                ; a byte is waiting
    jz      .none
    mov     dx, COM1
    in      al, dx
    and     ah, 0x7F
    jmp     .out
.none:
    or      ah, 0x80                ; timeout, the way this service says so
    jmp     .out
.bad:
    mov     ah, 0x80
.out:
    pop     si
    pop     dx
    iret

; serStatus -- AH = the line status, which is what a 16550 hands back
serStatus:
    push    dx
    mov     dx, COM1
    add     dx, 5
    in      al, dx
    mov     ah, al
    pop     dx
    ret

; ---------------------------------------------------------------------------
; int 15h: the memory map
;
; E820 is how everything after 1995 finds out what memory it may use. The caller
; hands a buffer in ES:DI, a continuation index in EBX and the signature
; 'SMAP' in EDX; each call fills one twenty-byte entry -- base, length, type --
; and returns the next index, with zero meaning "that was the last one". A
; loader that loops until EBX is zero will draw a map of this board:
;
;   0000_0000  640 KiB  usable      (the low half of the first megabyte)
;   0009_FC00    1 KiB  reserved    (where the video card is)
;   0010_0000   ...     usable      (extended memory, from the CMOS)
;
; The extended entry is only there when the board actually has some. A machine
; that reports memory it cannot write is worse than one that reports nothing:
; the kernel trusts this table and puts its own page tables in it.
; ---------------------------------------------------------------------------
int15:
    push    bp
    mov     bp, sp
    push    bx                      ; [bp - 2]: the caller's, and our way out
    push    si
    push    di
    push    ds
    push    es
    cmp     ah, 0xE8
    je      .e820
    cmp     ah, 0x24                ; the A20 gate, the way a loader asks
    je      .a20
    cmp     ah, 0xC0                ; system configuration table: there is none
    je      .bad
    cmp     ah, 0x53                ; the firmware's own: CPU control
    je      .smp
    cmp     ah, 0x86                ; wait CX:DX microseconds
    je      .wait
    cmp     ah, 0x88                ; the old question: how much, in KiB
    jne     .bad
    mov     al, 0x17
    call    cmos_read
    mov     bl, al
    mov     al, 0x18
    call    cmos_read
    mov     bh, al
    mov     ax, bx                  ; megabytes
    mov     cx, 1024
    mul     cx                      ; ... to the kilobytes AH=88 promises
    shl     bx, 6                   ; the high half of megabytes * 1024
    add     dx, bx
    or      dx, dx
    jz      .e88ok
    mov     ax, 0xFFFF              ; more than a word can say: 64 MB is the cap
.e88ok:
    and     word [ss:bp + 6], 0xFFFE
    jmp     .out
.e801:
    ; AX = kilobytes between 1 MiB and 16 MiB, BX = 64 KiB blocks above 16 MiB.
    ; A kernel that cannot ask E820 asks this, and a setup that gets "no
    ; extended memory" here decides the machine has four megabytes. The
    ; arithmetic is in megabytes because 128 MB of kilobytes does not fit in
    ; the sixteen bits the answer has: everything past 15 MB is counted in
    ; 64 KiB blocks, which is exactly what BX is for.
    mov     al, 0x17
    call    cmos_read
    mov     bl, al
    mov     al, 0x18
    call    cmos_read
    mov     bh, al                  ; BX = extended megabytes
    mov     ax, bx
    cmp     ax, 15
    jb      .e801_small
    mov     ax, 15 * 1024           ; KiB below the sixteen megabyte line
    sub     bx, 15
    shl     bx, 4                   ; (megabytes above) * 1024 / 64
    jmp     .e801_out
.e801_small:
    mov     cx, ax
    shl     cx, 10                  ; megabytes to kilobytes
    mov     ax, cx
    xor     bx, bx
.e801_out:
    and     word [ss:bp + 6], 0xFFFE
    jmp     .out

.smp:
    ; the flags live in the slot BP was pushed into, so the implementation is
    ; handed a pointer to it rather than left to guess
    push    bp
    mov     bp, sp
    add     bp, 6                   ; [bp + 6]: inside int 15h's own frame
    call    int15Smp
    pop     bp
    jmp     .out
.a20:
    ; AL=0 off, AL=1 on, AL=2/3 read it back -- the three ways a loader tries
    ; before it starts poking the keyboard controller or port 0x92 itself.
    push    ax
    push    dx
    cmp     al, 0x01
    je      .a20on
    cmp     al, 0x00
    je      .a20off
    in      al, 0x92
    test    al, 2
    jz      .a20was_off
    mov     al, 2
    jmp     .a20done
.a20was_off:
    xor     al, al
    jmp     .a20done
.a20on:
    in      al, 0x92
    or      al, 2
    out     0x92, al
    xor     al, al
    jmp     .a20done
.a20off:
    in      al, 0x92
    and     al, 0xFD
    out     0x92, al
    xor     al, al
.a20done:
    and     word [ss:bp + 6], 0xFFFE
    pop     dx
    pop     ax
    jmp     .out

.bad:
    or      word [ss:bp + 6], 1     ; carry: not supported
    mov     ah, 0x86
    jmp     .out
.e820:
    cmp     al, 0x01                ; AX=E801h: the older memory question
    je      .e801
    mov     eax, 0x534D4150
    cmp     edx, eax                ; the caller has to know what it is asking
    jne     .bad
    cmp     ecx, 20
    jb      .bad
    cmp     ebx, 0
    je      .e0
    cmp     ebx, 1
    je      .e1
    cmp     ebx, 2
    je      .e2
    xor     ebx, ebx                ; past the end: say so
    jmp     .e820out
.e0:
    call    e820_clear
    mov     dword [es:di + 8], 0x9FC00
    mov     dword [es:di + 16], 1
    mov     ebx, 1
    jmp     .e820out
.e1:
    call    e820_clear
    mov     dword [es:di], 0x9FC00
    mov     dword [es:di + 8], 0x400
    mov     dword [es:di + 16], 2
    mov     al, 0x17
    call    cmos_read
    mov     bl, al
    mov     al, 0x18
    call    cmos_read
    mov     bh, al
    or      bx, bx
    jnz     .e1_more
    xor     ebx, ebx                ; the map ends here: this was the last one
    jmp     .e820out
.e1_more:
    mov     ebx, 2
    jmp     .e820out
.e2:
    mov     al, 0x17
    call    cmos_read
    mov     bl, al
    mov     al, 0x18
    call    cmos_read
    mov     bh, al
    or      bx, bx
    jz      .e820end               ; no extended memory: the map ends here
    call    e820_clear
    mov     dword [es:di], 0x100000
    mov     dx, bx                  ; megabytes, zero-extended into a dword
    xor     edx, edx
    mov     dx, bx
    shl     edx, 20                 ; and bytes
    mov     dword [es:di + 8], edx
    mov     dword [es:di + 16], 1
.e820end:
    xor     ebx, ebx               ; no more entries
    jmp     .e820out
.e820out:
    mov     eax, 0x534D4150        ; still the signature
    mov     ecx, 20
    and     word [ss:bp + 6], 0xFFFE
.out:
    ; bx leaves through the slot it was pushed into, so the pops below restore
    ; the caller's other registers and still hand back our answer
    mov     [ss:bp - 2], bx
    pop     es
    pop     ds
    pop     di
    pop     si
    pop     bx
    pop     bp
    iret

; a zeroed twenty-byte entry at es:di, so every field not written is zero
; ---------------------------------------------------------------------------
; int 15h, AH=86h -- wait CX:DX microseconds, at least.
;
; A wait is a request for *time* to pass, and time on this board is the 8254's
; input count: one count every two instructions. So the counts the wait needs --
; microseconds * 1193 / 1000, the PIT's input being 1.193182 MHz -- are waited
; for by polling the chipset's count register at 0x5BD, which is that number.
; The wait therefore costs about two instructions a count, which is exactly the
; rate the clock runs at, so a guest that paces itself with this call moves at
; the speed it would on a board whose crystal is the thing it is counting.
;
; A counter that never moves would make this loop endless, and a counter that
; runs at any other rate would make the wait wrong, so the loop gives up after
; a budget of polls and reports CF with AH=86h rather than hanging: a slow or
; absent counter is the one case this cannot distinguish from a stall.
; ---------------------------------------------------------------------------
.wait:
    push    bx                      ; .out publishes bx, so it has to be the
    push    cx                      ; caller's again by then
    push    dx
    mov     eax, ecx
    shl     eax, 16
    mov     ax, dx                  ; EAX = CX:DX, the microseconds asked for
    test    eax, eax
    jz      .waitok                 ; nothing asked for, nothing to wait
    mov     ebx, 1193               ; counts per millisecond
    mul     ebx                     ; EDX:EAX = us * 1193
    mov     ebx, 1000
    div     ebx                     ; EAX = counts the wait needs
    test    eax, eax
    jz      .waitok                 ; under one count
    ; the count register is sixteen bits, so a longer wait goes in pieces of
    ; sixty thousand counts -- small enough that the elapsed total the poll
    ; computes cannot wrap past the sixteen bits it is compared in
    mov     ecx, eax
.waitmore:
    mov     eax, ecx
    cmp     eax, 60000
    jb      .waitpiece
    mov     eax, 60000
.waitpiece:
    sub     ecx, eax
    push    ecx
    call    wait_counts             ; AX = counts to let go by
    pop     ecx
    jc      .waitfail
    test    ecx, ecx
    jnz     .waitmore
    jmp     .waitok


.waitfail:
    or      word [ss:bp + 6], 1     ; carry: we could not wait
    mov     ah, 0x86
    jmp     .waitout
.waitok:
    and     word [ss:bp + 6], 0xFFFE
.waitout:
    pop     dx
    pop     cx
    pop     bx
    jmp     .out

; ---------------------------------------------------------------------------
; int 15h, AH=53h -- CPI: the firmware's own CPU-control interrupts.
;
;   AL=00h   online cores -> EAX, EBX
;   AL=01h   set them to EBX, if the chipset can take it
;   AL=02h   this core's number -> EAX
;
; A setup utility can write CMOS, but a chipset register that starts a thread is
; not CMOS. This is the one door in: 0x5AE already means "set the core count",
; and the ROM spends one register to expose it as a call.
;
; bp points at the caller's FLAGS. Carry means "no".
; ---------------------------------------------------------------------------
int15Smp:
    push    ax
    push    bx
    push    cx
    push    dx
    cmp     al, 0x00
    je      .online
    cmp     al, 0x01
    je      .set
    cmp     al, 0x02
    je      .id
    jmp     .bad
.online:
    mov     ax, [es:BDA_CORES]
    xor     ah, ah
    mov     bx, ax
    jmp     .ok
.set:
    cmp     bx, 1
    jb      .bad
    cmp     bx, 16
    ja      .bad
    mov     dx, 0x5AE
    mov     ax, bx
    out     dx, al
    mov     [es:BDA_CORES], bl
    jmp     .ok
.id:
    mov     dx, 0x5A2
    in      al, dx
    xor     ah, ah
    jmp     .ok
.bad:
    or      word [ss:bp + 6], 1
    mov     ah, 0x86
    jmp     .out
.ok:
    and     word [ss:bp + 6], 0xFFFE
.out:
    pop     dx
    pop     cx
    pop     bx
    pop     ax
    iret

; ---------------------------------------------------------------------------
; wait_counts -- poll the chipset's count register until AX counts have gone by.
;
; The low byte at 0x5BD latches the high byte at 0x5BE, so a sixteen-bit read
; cannot straddle a carry, and `sub` against the count the wait started from
; gives the elapsed total even though the register wraps. The poll costs about
; nine instructions an iteration and the clock one count every two, so an
; iteration lets four or five counts by: the wait is half instruction and half
; clock, which is the shape a halted 386 has.
;
; A counter that never moves would make this endless, so the loop gives up
; after forty thousand polls -- nearly three times what the longest piece needs
; -- and says so with CF rather than hanging the machine.
; ---------------------------------------------------------------------------
wait_counts:
    push    bx
    push    si
    push    di
    push    dx
    mov     di, ax                  ; the counts to wait for
    mov     dx, 0x5BD
    in      al, dx                  ; latch, and take the low byte
    mov     bl, al
    inc     dx
    in      al, dx                  ; the high byte of that same instant
    mov     bh, al                  ; BX = the count the wait starts from
    dec     dx
    xor     si, si                  ; polls so far: a stall has to end
.poll:
    in      al, dx
    mov     cl, al
    inc     dx
    in      al, dx
    mov     ch, al                  ; CX = the count now
    dec     dx
    sub     cx, bx                  ; how far it has come
    cmp     cx, di
    jae     .reached
    inc     si
    cmp     si, 40000
    jb      .poll
    jmp     .never
.reached:
    clc
    jmp     .out
.never:
    stc
.out:
    pop     dx
    pop     di
    pop     si
    pop     bx
    ret

e820_clear:
    mov     dword [es:di], 0
    mov     dword [es:di + 4], 0
    mov     dword [es:di + 8], 0
    mov     dword [es:di + 12], 0
    mov     dword [es:di + 16], 0
    ret

; ---------------------------------------------------------------------------
; int 16h: the keyboard
; ---------------------------------------------------------------------------
; the keyboard, from the controller to the interrupt
;
; The 8042 hands over one make code at a time, and the two bytes of an
; extended key arrive as `E0` then the code. A caller wants neither: it wants
; `ah` = scan code and `al` = ASCII, which is what int 16h has always
; promised. So the bytes are turned into words here, into the ring a real
; board keeps at 0x41A, and everything above this point only ever sees a word.
;
; The ring is what makes one key *peekable*. Without it `ah=0x01` can only ask
; the controller whether a byte is waiting, and the answer for the second half
; of an extended key is yes -- the caller is told there is a key, peeks at the
; `E0`, and then reads the real code on the next call. GRUB reads both halves
; as keys and its menu stops responding to the arrows.
;
; Nothing here sends break codes (see input.jsw), so a key is a press and the
; typematic repeat that follows it, and the modifier state is always zero.
; ---------------------------------------------------------------------------

; kbdFill -- move every code the controller is holding into the ring.
; Never blocks. Trashes ax, bx, si, di, ds and es; saves them all.
kbdFill:
    push    es
    push    ds
    push    ax
    push    bx
    push    si
    push    di
    xor     ax, ax
    mov     es, ax
.fill:
    mov     di, [es:BDA_KB_TAIL]
    mov     si, di
    add     si, 2
    cmp     si, BDA_KB_BUFFER + KB_RING
    jb      .room
    mov     si, BDA_KB_BUFFER
.room:
    cmp     si, [es:BDA_KB_HEAD]
    je      .done                   ; full: the controller keeps the rest
    in      al, 0x64
    test    al, 1
    jz      .done
    in      al, 0x60
    cmp     al, 0xE0                ; extended prefix: it names the *next* key
    jne     .key
    mov     byte [es:BDA_KB_PEND], 1
    jmp     .fill
.key:
    mov     ah, al                  ; ah = the scan code, always
    xor     al, al                  ; an extended key has no ASCII at all
    cmp     byte [es:BDA_KB_PEND], 0
    jne     .extended
    mov     bl, ah                  ; bx = the code, and the table is in the
    xor     bh, bh                  ; ROM, which is what cs points at
    mov     al, [cs:bx + keymap]
.extended:
    mov     byte [es:BDA_KB_PEND], 0
    mov     [es:di], ax
    mov     [es:BDA_KB_TAIL], si
    jmp     .fill
.done:
    pop     di
    pop     si
    pop     bx
    pop     ax
    pop     ds
    pop     es
    ret

; kbdGet -- the next key, waiting for one. Returns ax as int 16h promises it.
kbdGet:
    push    bx
    push    es
    xor     bx, bx
    mov     es, bx
.wait:
    call    kbdFill
    mov     bx, [es:BDA_KB_HEAD]
    cmp     bx, [es:BDA_KB_TAIL]
    je      .wait
    mov     ax, [es:bx]
    add     bx, 2
    cmp     bx, BDA_KB_BUFFER + KB_RING
    jb      .pop
    mov     bx, BDA_KB_BUFFER
.pop:
    mov     [es:BDA_KB_HEAD], bx
    pop     es
    pop     bx
    ret

; ---------------------------------------------------------------------------
int16:
    push    bp
    mov     bp, sp
    push    bx
    push    ds
    xor     bx, bx
    mov     ds, bx
    test    ah, ah
    jnz     .poll
    call    kbdGet
    jmp     .out
.poll:
    cmp     ah, 0x02                ; shift flags: there are no modifiers here
    je      .shift
    cmp     ah, 0x01
    jne     .out
    call    kbdFill                 ; take what the controller has, so the peek
    mov     bx, [ds:BDA_KB_HEAD]    ; is the word the next call will return
    cmp     bx, [ds:BDA_KB_TAIL]
    je      .empty
    mov     ax, [ds:bx]
    jmp     .out
.shift:
    xor     al, al                  ; no shift, no control, no alt
    jmp     .out
.empty:
    or      word [ss:bp + 6], 0x40  ; ZF: nothing there
.out:
    pop     ds
    pop     bx
    pop     bp
    iret

; ---------------------------------------------------------------------------
; int 19h: boot
; ---------------------------------------------------------------------------
; ---------------------------------------------------------------------------
; int 17h: the printer
;
; There is no parallel port on this board, and a probe that finds a status
; saying "busy, no paper, I/O error" gives up instead of waiting for a byte to
; be accepted for ever. Bit 3 is the I/O error flag and bit 7 is busy.
; ---------------------------------------------------------------------------
int17:
    mov     ah, 0xC8                ; busy, I/O error: no printer here
    iret

; ---------------------------------------------------------------------------
; int 18h: there is nothing to boot
;
; This is the ROM BASIC entry on a PC whose disk was empty, and a loader that
; has given up calls it: GRUB's own boot sector prints its error and then
; `int 18h`, expecting the machine to stop rather than carry on. It does not
; return, because there is nowhere to return to -- the string is printed and
; the core parks, which is a state the debugger can read.
;
; Its vector used to be 0000:0000, so the call executed the *vector table* as
; code: the CPU walked through the data area, stored over the boot sector it
; had just run, and stopped on whichever byte happened to be 0xF4. The symptom
; was a machine that halted inside GRUB's own `puts` with nothing printed.
; ---------------------------------------------------------------------------
int18:
    push    ax
    push    si
    push    ds
    mov     ax, cs
    mov     ds, ax
    mov     si, msg_nodev
    call    puts
    pop     ds
    pop     si
    pop     ax
.halt:
    cli
    hlt
    jmp     .halt

; ---------------------------------------------------------------------------
; int 18h's neighbours: the vectors this ROM does not implement answer and
; return. A board that leaves them at zero runs the vector table instead, and
; one stray interrupt is then a machine executing its own data area.
; ---------------------------------------------------------------------------
intDefault:
    iret

int19:
    push    bp
    mov     bp, sp
    push    ax
    push    bx
    push    cx
    push    dx
    push    es
    xor     ax, ax
    mov     es, ax
    ; ---- the order the setup saved is the order it tries. "Boot Sequence" was
    ;      a row that wrote a byte nothing read: the firmware went CD then disk
    ;      whatever the battery said, so picking "C: only" changed nothing.
    ;      0 is A: then C:, 1 is C: then A:, 2 is C: only, 3 is CD-ROM first --
    ;      which is the row this board ships with, because the CD is where a
    ;      fresh ISO goes. A blank battery reads 0xFF: the clamp lands on that
    ;      same default, not on an index the table never had.
    ; ---- the boot menu has already chosen: it set the device it wants and
    ;      said so at 0x4FF, and a menu that offers "Floppy" has to try the
    ;      floppy and nothing else. Falling back to the saved order there is a
    ;      machine that boots the disk when asked for the floppy, which is the
    ;      one thing a boot menu must not do.
    cmp     byte [es:BDA_BOOTFORCE], 0
    je      .from_cmos
    mov     byte [es:BDA_BOOTFORCE], 0     ; one choice, one use
    mov     al, [es:BDA_BOOTDEV]
    mov     byte [cs:bo_try], al
    mov     byte [cs:bo_try_len], 1
    jmp     .list
.from_cmos:
    mov     al, 0x2D
    call    cmos_read
    cmp     al, 3
    jbe     .order
    mov     al, 3
.order:
    mov     bl, al
    mov     di, bo_try
    call    boFill                  ; bo_try = the devices, bo_try_len = how many
.list:
    mov     byte [cs:bo_at], 0       ; which attempt this is
.try:
    ; the index lives in the ROM, not in a register: the BIOS's own console
    ; routines are entitled to every register in the book, and a boot loop that
    ; kept its counter in one of them walked off the end of its own list and
    ; printed drive 0x42
    mov     bl, [cs:bo_at]
    xor     bh, bh                  ; bx is the index, and the high half is not
    mov     al, [cs:bo_try_len]
    cmp     bl, al
    jae     .fail
    mov     al, [cs:bo_try + bx]
    cmp     al, CDDRIVE
    jne     .disk
    ; ---- the CD. The ISO in build/i386 is a hybrid: sector 0 is a disk's boot
    ;      sector and the El Torito entry is a CD's, and they are different
    ;      programs. GRUB is the second one -- its loader counts in 2048-byte
    ;      sectors and reads the drive number it was handed -- so the firmware
    ;      boots the catalog's entry and hands over 0x9F.
    mov     [es:BDA_BOOTDEV], al    ; so the line below and the hand-off agree
    call    bmSayBoot
    push    bx
    call    cdProbe
    pop     bx
    jnc     .hand
    jmp     .next
.disk:
    mov     [es:BDA_BOOTDEV], al
    call    bmSayBoot
    ; a drive that has just been picked gets a reset and three tries: a spindled
    ; disk answers the first read late often enough that one attempt is a
    ; machine that will not boot from a drive that is fine
    mov     dl, [es:BDA_BOOTDEV]
    xor     ah, ah
    int     0x13
    mov     cx, 3
.retry:
    push    cx
    xor     ax, ax
    mov     es, ax
    mov     bx, 0x7C00
    mov     ah, 0x02
    mov     al, 1
    xor     ch, ch
    mov     cl, 1
    xor     dh, dh
    mov     dl, [es:BDA_BOOTDEV]
    int     0x13
    pop     cx
    jnc     .read
    loop    .retry
    jmp     .next
.read:
    cmp     word [es:0x7C00 + 510], 0xAA55
    jne     .next
    mov     dword [es:BDA_BOOTPTR], 0
    mov     word [es:BDA_BOOTPTR + 2], 0x07C0
.hand:
    pop     es
    pop     dx
    pop     cx
    pop     bx
    pop     ax
    pop     bp
    ; A real BIOS hands the sector a drive number in DL and zeroed segment
    ; registers, and a boot sector reads its own rest with that drive: MEMZ
    ; loads its stage 2 with `int 13h ah=02` and never sets DL at all, so a
    ; DL left over from POST is a machine that reads a floppy nobody has and
    ; decompresses a screenful of zeros. `int 13h` answers "no such drive"
    ; for anything but 0x80 and the CD, which is the failure that was here.
    xor     ax, ax
    mov     ds, ax
    mov     es, ax
    mov     dl, [es:BDA_BOOTDEV]
    jmp     far [es:BDA_BOOTPTR]
.next:
    inc     byte [cs:bo_at]
    jmp     .try
.fail:
    pop     es
    pop     dx
    pop     cx
    pop     bx
    pop     ax
    pop     bp
    iret

; ---------------------------------------------------------------------------
; boFill -- what a Boot Sequence entry means, as a list of drive numbers.
;
;   di -> three bytes of room, bl = the order (0..3)
;
; The list is walked by int 19h, which asks each drive in turn, so a device that
; is in the list and not fitted -- there is no drive 0x00 on this board -- costs
; one failed read rather than a machine that will not boot.
; ---------------------------------------------------------------------------
boFill:
    push    ax
    mov     al, bl
    cmp     al, 2
    je      .conly
    cmp     al, 3
    je      .cdfirst
    test    al, al
    jnz     .cthena
    mov     byte [cs:di], 0x00      ; A: then C:
    mov     byte [cs:di + 1], 0x80
    mov     byte [cs:bo_try_len], 2
    jmp     .out
.cthena:
    mov     byte [cs:di], 0x80      ; C: then A:
    mov     byte [cs:di + 1], 0x00
    mov     byte [cs:bo_try_len], 2
    jmp     .out
.conly:
    mov     byte [cs:di], 0x80      ; C: only
    mov     byte [cs:bo_try_len], 1
    jmp     .out
.cdfirst:
    mov     byte [cs:di], CDDRIVE   ; CD-ROM first
    mov     byte [cs:di + 1], 0x80
    mov     byte [cs:bo_try_len], 2
.out:
    pop     ax
    ret

; ---------------------------------------------------------------------------
; int 1Ah: the clock
; ---------------------------------------------------------------------------
int1a:
    push    bp
    mov     bp, sp
    push    bx
    push    ds
    xor     bx, bx
    mov     ds, bx
    test    ah, ah
    jz      .ticks
    cmp     ah, 0x02                ; the real-time clock, in BCD
    jne     .date
    mov     al, 0x00
    call    cmos_read
    mov     dh, al                  ; seconds
    mov     al, 0x02
    call    cmos_read
    mov     cl, al                  ; minutes
    mov     al, 0x04
    call    cmos_read
    mov     ch, al                  ; hours
    jmp     .out
.date:
    cmp     ah, 0x04                ; the date, in BCD: CX = century:year
    jne     .out                    ; and DX = month:day
    ; The one call DOS's DATE and a Windows 95 boot both make. Century is a
    ; CMOS register on this board (0x32), not a constant: a machine that answers
    ; 19xx on a 20xx clock is a machine whose file dates are wrong for ever.
    mov     al, 0x32
    call    cmos_read
    mov     ch, al                  ; century
    mov     al, 0x09
    call    cmos_read
    mov     cl, al                  ; year
    mov     al, 0x08
    call    cmos_read
    mov     dh, al                  ; month
    mov     al, 0x07
    call    cmos_read
    mov     dl, al                  ; day of month
    jmp     .out
.ticks:
    mov     dx, [ds:BDA_TICKS]
    mov     cx, [ds:BDA_TICKS + 2]
    xor     al, al
.out:
    pop     ds
    pop     bx
    pop     bp
    iret

; ---------------------------------------------------------------------------
; int 08h: the timer
;
; The 8254 counts down, reaches zero, and pulls IRQ0; the 8259 hands the CPU
; the vector; this runs. Its whole job is the tick counter every program reads
; at 0x40:0x6C -- and calling int 1Ch, which is the hook a guest may steal
; without touching the BIOS's own work.
;
; EOI comes last and cannot be forgotten: without it the controller keeps IRQ0
; in service and no further timer interrupt is ever delivered.
; ---------------------------------------------------------------------------
int08:
    push    ax
    push    ds
    xor     ax, ax
    mov     ds, ax
    inc     word [ds:BDA_TICKS]
    jnz     .noHigh
    inc     word [ds:BDA_TICKS + 2]
.noHigh:
    int     0x1C                    ; the user's hook, which is iret by default
    mov     al, 0x20
    out     PIC_CMD, al             ; non-specific EOI
    pop     ds
    pop     ax
    iret

; int 1Ch: the hook int 08h calls. A guest that wants a timer replaces this
; vector and chains to the old one if it wants the BIOS's tick as well.
int1c:
    iret

; ---------------------------------------------------------------------------
; int 09h: the keyboard
;
; The 8042 raises IRQ1 when a scan code lands in its output buffer, and this
; handler's whole job is to get that code out of the controller before the next
; one overwrites it. `kbdFill` is exactly that job -- the same routine int 16h
; calls -- so a key taken by interrupt and a key taken by polling end up in the
; ring the same way, `E0` prefix and all.
;
; It used to store the raw byte into a byte-wide ring of its own, which would
; have corrupted the word ring int 16h reads the first time an interrupt
; actually arrived. Nothing raised IRQ1 then, which is why nobody noticed.
;
; The EOI goes out last and unconditionally: an interrupt left in service parks
; the whole 8259, and that failure looks exactly like a machine that froze.
;
; If the ring is full `kbdFill` leaves the byte in the controller, the line
; stays asserted, and this handler runs again next time round -- which is what a
; real board does, and why an overrun sounds like a beep rather than losing keys.
; ---------------------------------------------------------------------------
int09:
    push    ax
    call    kbdFill
    mov     al, 0x20
    out     PIC_CMD, al
    pop     ax
    iret

%include "setup.inc"

; ---------------------------------------------------------------------------
; int 10h: set the mode, set and get the cursor, and the teletype.
;
; The teletype is the one every program uses: AH=0Eh with the character in AL.
; The rest exist so a program can find out what it is drawing on, and because
; a graphics program asks AH=0Fh before it touches 0xA0000.
; ---------------------------------------------------------------------------
; ---------------------------------------------------------------------------
; int 10h: set the mode, set and get the cursor, and the teletype.
;
; The teletype is the one every program uses: AH=0Eh with the character in AL.
; The rest exist so a program can find out what it is drawing on, and because
; a graphics program asks AH=0Fh before it touches 0xA0000.
;
; This handler saves nothing. It puts what it was asked for in the data area
; and answers through a jump, so every path ends at one iret with the stock
; registers -- which is the only arrangement that survived being tested. A
; handler that pushes nine registers and answers in the tenth has to unwind
; twenty bytes by hand before returning, and one missing pop there sent the
; machine into the middle of nothing.
; ---------------------------------------------------------------------------
int10:
    call    int10body
    iret

int10body:
    push    ax
    mov     ax, es                  ; int 10h works in the data area, and
    xor     ax, ax                  ; `tty` reads the ROM through ds itself
    mov     es, ax
    pop     ax
    mov     [es:BDA_ARG_AL], al
    mov     [es:BDA_ARG_AH], ah
    cmp     ah, 0x0E
    je      tty10
    cmp     ah, 0x00                ; set mode: text, 13h and 12h exist
    je      mode10
    cmp     ah, 0x0F                ; get mode: what was last set
    je      getmode10
    cmp     ah, 0x02                ; set cursor: the BDA is where tty reads it
    je      setpos10
    cmp     ah, 0x03                ; get cursor
    je      getpos10
    cmp     ah, 0x09                ; write character and attribute
    je      write10
    cmp     ah, 0x12                ; alternate select: this is not an EGA/VGA
    je      probe10
    cmp     ah, 0x1A                ; display combination: nor is it PS/2
    je      probe10
    ret

probe10:
    ; AL = 0 is "no such function", which is what a card older than EGA
    ; answers, and what a kernel's video probe expects when it is looking for
    ; something newer.
    xor     al, al
    ret

tty10:
    call    tty
    mov     al, [es:BDA_ARG_AL]
    mov     ah, [es:BDA_ARG_AH]
    ret

mode10:
    mov     al, [es:BDA_ARG_AL]
    mov     [es:BDA_VIDEO_MODE], al
    mov     byte [es:BDA_CURSOR_ROW], 0
    mov     byte [es:BDA_CURSOR_COL], 0
    call    clear_text
    mov     al, [es:BDA_ARG_AL]
    mov     ah, [es:BDA_ARG_AH]
    ret

getmode10:
    mov     al, [es:BDA_VIDEO_MODE]
    mov     ah, [es:CON_W]
    ret

setpos10:
    mov     [es:BDA_CURSOR_ROW], dh
    mov     [es:BDA_CURSOR_COL], dl
    ret

getpos10:
    mov     dh, [es:BDA_CURSOR_ROW]
    mov     dl, [es:BDA_CURSOR_COL]
    xor     cx, cx
    ret

; ---------------------------------------------------------------------------
; int 10h AH=09: write AL, in attribute BL, CX times, at the cursor, and leave
; the cursor where it was. GRUB's console driver is built on exactly this: it asks
; for the cursor (AH=03), paints one character (AH=09), and puts the cursor
; where it wants it (AH=02) -- a driver written against every BIOS since 1984.
; With no AH=09 here GRUB ran perfectly and printed nothing, which looks like a
; dead kernel rather than a missing function.

write10:
    push    ax
    push    bx
    push    cx
    push    dx
    push    di
    push    es
    jcxz    .w9out                  ; a count of zero writes nothing
    mov     dh, [es:BDA_CURSOR_ROW]
    mov     dl, [es:BDA_CURSOR_COL]
    mov     ah, bl                  ; the colour is BL; AH still holds 0x09
    mov     di, ax                  ; so the cell is character | attribute<<8
.w9next:
    push    ax
    mov     al, [es:CON_W]          ; the card's stride, not a hard 80
    xor     bh, bh
    mov     bl, dh
    mul     bl                      ; ax = row * stride
    mov     bl, dl
    add     ax, bx
    shl     ax, 1                   ; and the cell is two bytes
    mov     bx, ax
    pop     ax
    push    es
    mov     ax, TEXT_SEG
    mov     es, ax
    mov     ax, di
    mov     [es:bx], ax
    pop     es
    inc     dl                      ; the write walks the plane for us, but the
    cmp     dl, [es:CON_W]          ; guest's cursor stays where it was
    jb      .w9step
    xor     dl, dl
    inc     dh
.w9step:
    loop    .w9next
.w9out:
    pop     es
    pop     di
    pop     dx
    pop     cx
    pop     bx
    pop     ax
    ret


%include "pci.inc"
%include "splash.inc"
%include "gx620.inc"
%include "pxe.inc"
%include "boot.inc"

; ---------------------------------------------------------------------------
; data
; ---------------------------------------------------------------------------
msg_banner: db "JSW 80386 BIOS v0.02", 13, 10, 0
msg_bootno: db "CMOS boot #", 0
msg_crlf:   db 13, 10, 0
msg_cpu:    db "CPU: 80386, real mode, 16-bit bus", 13, 10, 0
msg_mem:    db "Memory: ", 0
msg_memk:   db "K conventional, ", 0
msg_memx:   db "", 0
msg_memxk:  db " MB extended", 13, 10, 0
msg_memx0:  db "no extended memory", 13, 10, 0
msg_video:  db "Video: ", 0
msg_video_tail: db " text, attribute 0x07", 13, 10, 0
bo_try:     times 3 db 0            ; the drives int 19h will try, in order
bo_try_len: db 0                    ; ... and how many of them there are
bo_at:      db 0                    ; ... and which one it is on
msg_cores:  db "Cores: ", 0
msg_smp:    db "SMP: ", 0
msg_cores_online: db " core(s) online", 13, 10, 0
msg_pci:    db "PCI: ", 0
msg_pci_tail: db " function(s) on bus 0", 13, 10, 0
msg_nodev:  db "No bootable device.", 13, 10, 0

; set 1 scan codes, straight out of the 8042
keymap:
    db 0, 27, '1', '2', '3', '4', '5', '6', '7', '8', '9', '0', '-', '=', 8, 9
    db 'q', 'w', 'e', 'r', 't', 'y', 'u', 'i', 'o', 'p', '[', ']', 13, 0
    db 'a', 's', 'd', 'f', 'g', 'h', 'j', 'k', 'l', ';', 39, '`', 0, 92
    db 'z', 'x', 'c', 'v', 'b', 'n', 'm', ',', '.', '/', 0, '*', 0, ' '
    times 128 - ($ - keymap) db 0
    times 128 db 0

; ---------------------------------------------------------------------------
; the reset vector: the last sixteen bytes of the 64 KiB ROM
; ---------------------------------------------------------------------------
    times 0xFFF0 - ($ - $$) db 0
    jmp     0xF000:start
    times 0x10000 - ($ - $$) db 0
