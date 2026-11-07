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
BDA_EQUIP       equ 0x410
BDA_MEMK        equ 0x413
BDA_VIDEO_MODE  equ 0x449             ; the mode int 10h last set
BDA_ARG_AL      equ 0x4E4             ; int 10h's own copy of its arguments
BDA_LAST_STATUS equ 0x4EE             ; int 13h's last status byte
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
CON_T           equ 0x52E          ; the first row of the console's window
CON_B           equ 0x52F          ; ... and the last
CON_W           equ 0x530          ; how wide it is, from column 0
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
    mov     cx, COLS * ROWS
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
    ; the keyboard ring the interrupt fills and int 16h drains starts empty
    mov     word [es:BDA_KB_HEAD], 0
    mov     word [es:BDA_KB_TAIL], 0

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
    mov     ax, cs
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
    mov     bx, 0x16 * 4
    mov     dx, int16
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
    mov     si, msg_boot
    call    puts

    int     0x19                    ; does not return on success

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
    mov     cx, COLS * ROWS
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
    mov     dx, COLS
    mov     ax, cx
    mul     dx                      ; ax = row * 80
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
    mul     bp
    mov     di, ax                  ; the window's top left, in the plane
    mov     si, ax
    add     si, bp                  ; one row further down
    mov     bp, COLS * 2
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
    mov     bp, COLS * 2
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
    ; only the disk at 0x80 exists: everything else answers with "no such
    ; drive" rather than silently reading the same sector again
    cmp     dl, 0x80
    jne     .fail
    cmp     ah, 0x02
    je      .read
    cmp     ah, 0x00                ; reset: nothing to reset yet
    je      .ok
    cmp     ah, 0x41                ; does this drive speak EDD?
    je      .edd_check
    cmp     ah, 0x42                ; read sectors through a packet
    je      .edd_read
    cmp     ah, 0x48                ; drive parameters, the EDD way
    je      .edd_params
    cmp     ah, 0x08                ; drive parameters, the old way
    jne     .fail
    mov     ch, 79
    mov     cl, 63
    mov     dh, 15
    mov     dl, 1
    mov     [ss:bp - 4], cx         ; outputs leave through the slots they were
    mov     [ss:bp - 6], dx         ; pushed into, because the pops below
    jmp     .ok                     ; put the caller's registers back
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
    ; a 1.44-megabyte-shape geometry that describes the staged image: 128
    ; sectors, less than one cylinder's worth, so the numbers stay honest
    push    es
    push    di
    mov     ax, ds
    mov     es, ax                  ; the caller's buffer is at ds:si
    mov     byte [si], 0x1A
    mov     byte [si + 1], 0
    mov     word [si + 2], 1        ; cylinders
    mov     word [si + 4], 0
    mov     word [si + 6], 1        ; heads
    mov     word [si + 8], 0
    mov     word [si + 10], 63      ; sectors a track
    mov     word [si + 12], 0
    mov     dword [si + 12], 128    ; total sectors, a qword
    mov     dword [si + 16], 0
    mov     word [si + 20], 512     ; bytes a sector
    pop     di
    pop     es
    jmp     .ok
.edd_read:
    ; the disk address packet: count, offset, segment, then a 64-bit lba
    mov     cx, [si + 2]            ; how many sectors
    shl     cx, 8                   ; ... in words
    mov     di, [si + 4]            ; where the caller wants them
    mov     dx, [si + 6]
    mov     eax, [si + 8]           ; the lba, low half, sectors to bytes
    shl     eax, 9
    push    ds
    push    es
    mov     si, ax
    mov     ax, DISK_SEG
    mov     ds, ax
    mov     es, dx
    rep     movsw
    pop     es
    pop     ds
    jmp     .ok
.read:
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
    shl     ax, 9                   ; 512 bytes a sector
    mov     si, ax
    mov     ax, DISK_SEG
    mov     ds, ax
    mov     di, bx
    pop     cx                      ; cl = the sector count
    and     cx, 0xFF
    shl     cx, 8                   ; sectors * 256 words
    rep     movsw
.ok:
    and     word [ss:bp + 6], 0xFFFE ; clear carry: it worked
    xor     ah, ah
    mov     [es:BDA_LAST_STATUS], ah
    jmp     .out
.ok_ah:
    and     word [ss:bp + 6], 0xFFFE
    jmp     .out
.fail:
    or      word [ss:bp + 6], 1     ; set carry: it did not
    mov     ah, 0x04                ; sector not found
    mov     [es:BDA_LAST_STATUS], ah
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
    cmp     ah, 0x53                ; the firmware's own: CPU control
    je      .smp
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
.smp:
    ; the flags live in the slot BP was pushed into, so the implementation is
    ; handed a pointer to it rather than left to guess
    push    bp
    mov     bp, sp
    add     bp, 6                   ; [bp + 6]: inside int 15h's own frame
    call    int15Smp
    pop     bp
    jmp     .out
.bad:
    or      word [ss:bp + 6], 1     ; carry: not supported
    mov     ah, 0x86
    jmp     .out
.e820:
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
int16:
    push    bp
    mov     bp, sp
    push    bx
    push    ds
    xor     bx, bx
    mov     ds, bx
    test    ah, ah
    jnz     .poll
    call    key_wait
    jmp     .out
.poll:
    cmp     ah, 0x01
    jne     .out
    in      al, 0x64                ; is a key waiting?
    test    al, 1
    jz      .empty
    mov     ax, [ds:0x41A]          ; peek at the buffer head
    jmp     .out
.empty:
    or      word [ss:bp + 6], 0x40  ; ZF: nothing there
.out:
    pop     ds
    pop     bx
    pop     bp
    iret

key_wait:
    in      al, 0x64
    test    al, 1
    jz      key_wait
    in      al, 0x60                ; the scan code
    mov     bx, keymap
    xor     ah, ah
    add     bx, ax
    mov     al, [bx]
    test    al, al
    jnz     .mapped
    mov     al, 0x20                ; unmapped: a space
.mapped:
    xor     ah, ah
    ret

; ---------------------------------------------------------------------------
; int 19h: boot
; ---------------------------------------------------------------------------
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
    mov     bx, 0x7C00
    mov     ah, 0x02
    mov     al, 1
    xor     ch, ch
    mov     cl, 1
    xor     dh, dh
    mov     dl, [es:BDA_BOOTDEV]
    int     0x13
    jc      .fail
    cmp     word [es:0x7C00 + 510], 0xAA55
    jne     .fail
    pop     es
    pop     dx
    pop     cx
    pop     bx
    pop     ax
    pop     bp
    jmp     0x0000:0x7C00
.fail:
    pop     es
    pop     dx
    pop     cx
    pop     bx
    pop     ax
    pop     bp
    iret

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
    jnz     .out
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
; The 8042 raises IRQ1 when a scan code lands in its output buffer. This takes
; the byte, appends the scan code to the buffer at 0x40:0x1E, and EOIs. int 16h
; is what reads the buffer back.
; ---------------------------------------------------------------------------
int09:
    push    ax
    push    bx
    push    ds
    xor     ax, ax
    mov     ds, ax
    in      al, 0x60                ; the scan code, and that clears the buffer
    mov     bx, [ds:BDA_KB_TAIL]
    mov     [ds:BDA_KB_BUFFER + bx], al
    inc     bx
    and     bx, 0x1F                ; a ring of thirty-two scan codes
    mov     [ds:BDA_KB_TAIL], bx
    mov     al, 0x20
    out     PIC_CMD, al
    pop     ds
    pop     bx
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
    mov     ah, COLS
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
msg_boot:   db "Booting from device 0x80...", 13, 10, 0
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
