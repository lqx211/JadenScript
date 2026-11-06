; ---------------------------------------------------------------------------
; The boot menu, and the two programs behind it.
;
; A real board gives you a second or two at POST and one key to stop in: this
; is where DEL lands. The screen goes blue, a panel comes up in the middle with
; what you can do, and the arrows move the light. Nothing on it is decoration --
; every entry either boots, reboots, sleeps the machine, or hands it to the two
; programs below (the setup utility and the debug shell).
;
;   +--------------------------------------+
;   | Boot Menu                            |
;   |                                      |
;   |   Boot Options                       |
;   |   Firmware Setup                     |
;   |   Reboot                             |
;   |   Halt                               |
;   |   Debug Shell                        |
;   |                                      |
;   +--------------------------------------+
;
; The panel is the same suDialog the setup screen uses, with the height passed
; in: a menu is as tall as it has entries, and an eight-row dialog with five
; entries in it is a list that ends up drawn through its own bottom border.
; ---------------------------------------------------------------------------

BM_ITEM    equ 0x511                ; which entry the light was last on
BM_TBL     equ 0x512                ; word: the table of names being shown
BM_CNT     equ 0x514                ; how many entries
BM_SEL     equ 0x515                ; which one the light is on
BM_ROW     equ 0x516                ; the first entry's row
BM_L       equ 0x517                ; the panel's left column
BM_W       equ 0x518                ; ... and its width
BM_DEV     equ 0x519                ; the device the menu asked for
BM_DBG     equ 0x51A                ; the debug shell's address pointer
BM_TITLE   equ 0x51C                ; word: the panel's title

DBG_BUF    equ 0x530                ; the shell's line
DBG_MAX    equ 14

BM_LEFT    equ 20
BM_WIDTH   equ 40
BM_TOP     equ 10
BM_MAIN_N  equ 5
BO_N       equ 4

; ---------------------------------------------------------------------------
; bootMenu -- the screen DEL earns you. It does not return: every way out is a
; boot, a reset, a halt, or another turn round this loop.
; ---------------------------------------------------------------------------
bootMenu:
    push    ax
    mov     ax, cs
    mov     ds, ax                  ; the names live in the ROM, so ds has to
    pop     ax
    cmp     byte [es:BM_ITEM], BM_MAIN_N
    jb      .carry
    mov     byte [es:BM_ITEM], 0
.carry:
    mov     word [es:BM_TBL], bm_items
    mov     word [es:BM_TITLE], msg_bm_title
    mov     byte [es:BM_CNT], BM_MAIN_N
    mov     byte [es:BM_ROW], BM_TOP + 3
    mov     byte [es:BM_L], BM_LEFT
    mov     byte [es:BM_W], BM_WIDTH
    mov     al, [es:BM_ITEM]
    mov     [es:BM_SEL], al
    mov     al, [es:BDA_BOOTDEV]
    mov     [es:BM_DEV], al         ; Esc boots what was booted last time
    call    bmRedraw
    call    bmList                  ; al = the entry, or 0xFF for Esc
    cmp     al, 0xFF
    je      .boot
    mov     [es:BM_ITEM], al
    cmp     al, 0
    je      .options
    cmp     al, 1
    je      .setup
    cmp     al, 2
    je      .reboot
    cmp     al, 3
    je      .halt
    call    dbgShell
    jmp     bootMenu
.options:
    call    bootOptions             ; al = the device, or 0xFF for Esc
    cmp     al, 0xFF
    je      bootMenu
    call    bmBoot
    jmp     bootMenu                ; nothing there: back to the list
.setup:
    call    gxEnter
    jmp     bootMenu
.reboot:
    call    bmReboot
    jmp     bootMenu                ; not reached: bmReboot ends at the reset
.halt:
    mov     byte [es:CON_ATTR], 0x07
    call    clear_text
    xor     dl, dl
    xor     dh, dh
    call    suGoto
    mov     si, msg_bm_halted
    call    puts
    jmp     bmHang
.boot:
    ; Esc: boot what the menu would have booted, and if there is nothing to
    ; boot, stop here rather than coming back -- a menu you cannot leave is a
    ; menu that is really a trap
    mov     al, [es:BM_DEV]
    call    bmBoot
    jmp     bmHang

bmHang:
    cli
    hlt
    jmp     bmHang

; ---------------------------------------------------------------------------
; bootOptions -- what to boot from. Returns al = the device, or 0xFF.
;
; The board has one disk at 0x80 and nothing else, which is what picking any of
; the other three finds out: int 13h refuses the device and the boot fails with
; "No bootable device", the same sentence a real machine prints.
; ---------------------------------------------------------------------------
bootOptions:
    push    ax
    mov     ax, cs
    mov     ds, ax
    pop     ax
    mov     word [es:BM_TBL], bo_items
    mov     word [es:BM_TITLE], msg_bo_title
    mov     byte [es:BM_CNT], BO_N
    mov     byte [es:BM_ROW], BM_TOP + 3
    mov     byte [es:BM_L], BM_LEFT
    mov     byte [es:BM_W], BM_WIDTH
    mov     byte [es:BM_SEL], 0
    call    bmRedraw
    call    bmList
    cmp     al, 0xFF
    je      .out
    mov     bx, bo_devs
    add     bx, ax
    mov     al, [bx]
.out:
    ret

; ---------------------------------------------------------------------------
; bmRedraw -- the blue screen and the panel, from nothing.
;
; The plane is painted in the paper colour first, so a menu opened out of the
; debug shell does not come up on top of the shell's last page.
; ---------------------------------------------------------------------------
bmRedraw:
    push    ax
    push    bx
    push    cx
    push    dx
    push    si
    xor     dh, dh
.row:
    xor     dl, dl
    mov     cx, 79                  ; seventy-nine cells, not eighty: the
    mov     al, ' '                 ; console advances after the last one and a
    mov     bl, SU_PAPER            ; cursor that steps past column 79 of row 29
    call    suBar                   ; scrolls the whole screen up a line
    mov     dl, 79
    mov     al, ' '
    mov     bl, SU_PAPER
    call    suPoke
    inc     dh
    cmp     dh, ROWS
    jb      .row
    xor     dl, dl
    xor     dh, dh
    call    suGoto
    ; the panel: a top rule, the title bar, a blank, the entries, a blank and
    ; the bottom rule
    mov     dl, BM_LEFT
    mov     dh, BM_TOP
    mov     cl, BM_WIDTH
    mov     al, [es:BM_CNT]
    add     al, 5
    mov     bl, al
    mov     bh, SU_PAPER            ; blue paper: the menu is part of the screen
    mov     si, [es:BM_TITLE]
    call    suDialogN
    pop     si
    pop     dx
    pop     cx
    pop     bx
    pop     ax
    ret

; ---------------------------------------------------------------------------
; bmList -- draw the entries, then move the light until Enter or Esc.
;   Returns al = the chosen index, or 0xFF.
; ---------------------------------------------------------------------------
bmList:
    push    bx                      ; not ax: the answer comes back in it
    push    cx
    xor     ax, ax                  ; al is the index, and ah has to go too:
    ; a stale ah makes the index 0x0D00 and the names come out of the middle of
    ; the ROM instead of out of the table
.draw:
    push    ax
    call    bmRow
    pop     ax
    inc     al
    cmp     al, [es:BM_CNT]
    jb      .draw
.key:
    call    keyScan
    cmp     al, 0x48                ; up
    je      .up
    cmp     al, 0x50                ; down
    je      .down
    cmp     al, 0x1C                ; Enter
    je      .enter
    cmp     al, 0x01                ; Esc
    je      .cancel
    cmp     al, 0x0E                ; Backspace leaves too
    je      .cancel
    jmp     .key
.up:
    mov     al, [es:BM_SEL]
    test    al, al
    jnz     .updec
    mov     al, [es:BM_CNT]
.updec:
    dec     al
    jmp     .moved
.down:
    mov     al, [es:BM_SEL]
    inc     al
    cmp     al, [es:BM_CNT]
    jb      .moved
    xor     al, al
.moved:
    ; two rows, not the whole panel: the arrow keys come faster than a repaint.
    ; The light has to move *first*, because bmRow decides its ink from BM_SEL
    ; -- repainting the old row before the move simply relights it.
    mov     bl, [es:BM_SEL]
    mov     [es:BM_SEL], al
    push    ax
    mov     al, bl
    call    bmRow
    pop     ax
    call    bmRow
    jmp     .key
.enter:
    mov     al, [es:BM_SEL]
    jmp     .out
.cancel:
    mov     al, 0xFF
.out:
    pop     cx
    pop     bx
    ret

; ---------------------------------------------------------------------------
; bmRow -- one entry: the whole row in the paper colour, or lit when it is the
; selected one, with its name two columns in from the frame.
;   al = the index
; ---------------------------------------------------------------------------
bmRow:
    push    ax
    push    bx
    push    cx
    push    dx
    push    si
    push    di
    xor     ah, ah
    mov     di, ax                  ; the index
    mov     al, [es:BM_ROW]
    xor     ah, ah
    add     ax, di
    mov     dh, al                  ; the row it belongs on
    mov     bl, SU_PAPER
    mov     al, [es:BM_SEL]
    xor     ah, ah
    cmp     ax, di
    jne     .ink
    mov     bl, SU_SEL
.ink:
    mov     dl, [es:BM_L]
    inc     dl
    mov     cl, [es:BM_W]
    sub     cl, 2
    mov     al, ' '
    call    suBar
    mov     dl, [es:BM_L]
    add     dl, 2
    call    suGoto
    mov     si, [es:BM_TBL]
    add     si, di
    add     si, di
    mov     si, [si]
    call    suPrint                 ; bl is still the ink the bar was drawn in
    pop     di
    pop     si
    pop     dx
    pop     cx
    pop     bx
    pop     ax
    ret

; ---------------------------------------------------------------------------
; bmBoot -- hand over to the device in al. Returns only when there was nothing
; there to boot.
; ---------------------------------------------------------------------------
bmBoot:
    push    ax
    mov     [es:BM_DEV], al
    mov     [es:BDA_BOOTDEV], al
    mov     byte [es:CON_ATTR], 0x07
    call    clear_text
    xor     dl, dl
    xor     dh, dh
    call    suGoto
    pop     ax
    call    bmSayBoot
    int     0x19                    ; does not return when it worked
    mov     si, msg_bm_nodev
    call    puts
    ret

; bmSayBoot -- "Booting from device XX", the line POST and the menu both print
bmSayBoot:
    mov     si, msg_bm_booting
    call    puts
    mov     al, [es:BDA_BOOTDEV]
    call    puthex
    mov     si, msg_crlf
    jmp     puts

; ---------------------------------------------------------------------------
; hwApply -- hand the emulation settings in the CMOS to the chipset.
;
; The setup utility edits the CMOS, not the hardware: a board that changed its
; clock the moment a key was pressed would never remember anything. So the
; settings are written here, once at POST and once when the setup is left with
; a save, which is exactly when a real board would load its registers.
; ---------------------------------------------------------------------------
hwApply:
    push    ax
    push    bx
    push    dx
    mov     al, 0x34                ; the master switch
    call    cmos_read
    cmp     al, 1
    jbe     .master
    mov     al, 1                   ; a blank battery reads as "on"
.master:
    mov     dx, 0x5A9
    out     dx, al
    mov     al, 0x36                ; the clock, out of the setup's table
    call    cmos_read
    cmp     al, 4
    jbe     .freq
    mov     al, 2                   ; 33 MHz
.freq:
    xor     ah, ah
    mov     si, ax
    mov     al, [cs:hw_mhz + si]
    mov     dx, 0x5A8
    out     dx, al
    mov     al, 0x37                ; and how often a held key repeats
    call    cmos_read
    cmp     al, 4
    jbe     .repeat
    mov     al, 2                   ; 1/8 s
.repeat:
    xor     ah, ah
    mov     si, ax
    mov     al, [cs:hw_keyrep + si]
    mov     dx, 0x5AA               ; in units of 10 ms: a byte cannot hold 1/2 s
    out     dx, al
    pop     dx
    pop     bx
    pop     ax
    ret

hw_mhz:    db 16, 25, 33, 40, 50
hw_keyrep: db 50, 25, 12, 6, 4      ; 1/2 s, 1/4 s, 1/8 s, 1/16 s, 1/24 s

; ---------------------------------------------------------------------------
; gxSplash -- the power-on screen, and the way back to the text underneath.
;
; A real board shows a logo and prints POST straight over it: nothing is
; reserved for the log, so a long line runs into the artwork and the artwork
; loses. That is what these two do -- the console keeps the whole screen the
; whole time, and "hiding" the splash is not a clear, it is just the cursor
; going home so the boot line prints at the top left like it always did.
; ---------------------------------------------------------------------------
SPLASH_STEP_ATTR equ 0x1E           ; yellow on blue, like the notice below it

; ---------------------------------------------------------------------------
; gxSplashShow -- put the picture up, cursor at the top left.
; ---------------------------------------------------------------------------
gxSplashShow:
    push    ax
    push    cx
    push    si
    push    di
    push    ds
    push    es
    mov     byte [es:BDA_CURSOR_COL], 0
    mov     byte [es:BDA_CURSOR_ROW], 0
    mov     byte [es:BDA_SPLASH], 1
    mov     ax, cs
    mov     ds, ax
    mov     si, splash_cells
    mov     ax, TEXT_SEG
    mov     es, ax
    xor     di, di
    mov     cx, COLS * ROWS
    rep     movsw
    pop     es
    pop     ds
    pop     di
    pop     si
    pop     cx
    pop     ax
    ret

; ---------------------------------------------------------------------------
; gxSplashHide -- the splash stays on screen and the cursor goes home.
;
; No clear: a clear is a white flash where the picture was, and it throws away
; the log POST just printed. The boot line lands on row 0 over the top of the
; artwork, which is exactly where the countdown used to start.
; ---------------------------------------------------------------------------
gxSplashHide:
    push    ax
    push    es
    xor     ax, ax
    mov     es, ax
    mov     byte [es:BDA_CURSOR_COL], 0
    mov     byte [es:BDA_CURSOR_ROW], 0
    pop     es
    pop     ax
    ret

; ---------------------------------------------------------------------------
; splashStep -- si = the line for the splash's top right corner.
;
; The step name changes as POST walks along, the way a GX620 prints its own
; progress there, so it is printed rather than baked into the splash -- and it
; is right aligned, so the corner grows to the left as the words get longer.
; ---------------------------------------------------------------------------
splashStep:
    push    ax
    push    bx
    push    cx
    push    di
    push    si
    push    fs
    push    es
    xor     ax, ax
    mov     es, ax                  ; the BDA, for the card's width
    mov     bx, si
    xor     cx, cx
.len:
    cmp     byte [cs:bx], 0
    je      .got
    inc     bx
    inc     cx
    jmp     .len
.got:
    mov     al, [es:BDA_VIDCOLS]
    xor     ah, ah
    sub     ax, cx
    shl     ax, 1                   ; a column count, in bytes, along row 0
    mov     di, ax
    mov     ax, TEXT_SEG
    mov     fs, ax
.loop:
    lodsb
    test    al, al
    jz      .out
    mov     ah, SPLASH_STEP_ATTR     ; yellow on the splash's blue...
    cmp     byte [es:BDA_SPLASH], 0
    jne     .attr
    mov     ah, 0x07                 ; ... or plain grey on a bare screen
.attr:
    mov     [fs:di], ax
    add     di, 2
    jmp     .loop
.out:
    pop     es
    pop     fs
    pop     si
    pop     di
    pop     cx
    pop     bx
    pop     ax
    ret

; ---------------------------------------------------------------------------
; bmReboot -- start again from the top of the ROM.
;
; The 8042's reset line is asked first, because that is what a real board does;
; the far jump is the part that works here, and it is also the part that works
; on a machine whose keyboard controller has no reset line at all. FFFF:0000 is
; the last sixteen bytes of the ROM, which is the ljmp to the entry point.
; ---------------------------------------------------------------------------
bmReboot:
    mov     al, 0xFE
    out     0x64, al
    mov     ax, 0xFFFF
    push    ax
    xor     ax, ax
    push    ax
    retf

; ===========================================================================
; The debug shell.
;
; Five commands, because five is what fits on a screen and because a shell you
; can actually type into is worth more than a long list you cannot:
;
;   m [addr]   memory: eight rows of eight bytes, then on to the next page
;   i          the interrupt table, four vectors to a line
;   c          clear the screen
;   h or ?     the list
;   q          back to the menu
; ===========================================================================
dbgShell:
    push    ax
    mov     ax, cs
    mov     ds, ax
    pop     ax
    xor     ax, ax
    mov     es, ax
    mov     byte [es:CON_ATTR], 0x07
    call    clear_text
    xor     dl, dl
    xor     dh, dh
    call    suGoto
    mov     word [es:BM_DBG], 0x0000
    mov     si, msg_dbg_banner
    call    puts
    mov     si, msg_dbg_help
    call    puts
.repl:
    mov     si, msg_dbg_prompt
    call    puts
    call    dbgLine                 ; the line lands in DBG_BUF
    mov     al, [es:DBG_BUF]
    cmp     al, 'm'
    je      .mem
    cmp     al, 'i'
    je      .ivt
    cmp     al, 'c'
    je      .clear
    cmp     al, 'q'
    je      .quit
    cmp     al, 'h'
    je      .help
    cmp     al, '?'
    je      .help
    test    al, al
    jz      .repl
    mov     si, msg_dbg_unknown
    call    puts
    jmp     .repl
.mem:
    mov     si, DBG_BUF + 1
    call    dbgHex                  ; ax = the address, carry when there was none
    jnc     .haveaddr
    mov     ax, [es:BM_DBG]         ; no number: carry on from where we were
.haveaddr:
    mov     [es:BM_DBG], ax
    call    dbgDump
    jmp     .repl
.ivt:
    call    dbgIvt
    jmp     .repl
.clear:
    call    clear_text
    xor     dl, dl
    xor     dh, dh
    call    suGoto
    jmp     .repl
.help:
    mov     si, msg_dbg_help
    call    puts
    jmp     .repl
.quit:
    mov     byte [es:CON_ATTR], 0x07
    call    clear_text
    ret

; ---------------------------------------------------------------------------
; dbgLine -- read a line into DBG_BUF. Returns al = its length.
;
; The scan code is turned into a letter by the same table int 16h uses, so the
; shell types the same alphabet the BIOS does. Backspace erases a character by
; hand: the console's backspace only walks the cursor left, the way a real
; teletype's did.
; ---------------------------------------------------------------------------
dbgLine:
    push    bx
    push    cx
    push    si
    xor     cx, cx
.next:
    call    keyScan
    xor     ah, ah
    cmp     al, 0x1C                ; Enter
    je      .done
    cmp     al, 0x0E                ; Backspace
    je      .back
    mov     bx, keymap
    add     bx, ax
    mov     al, [bx]
    test    al, al
    jz      .next
    cmp     al, ' '
    jb      .next                   ; a control character: nothing to show
    cmp     cx, DBG_MAX
    jae     .next
    mov     si, DBG_BUF
    add     si, cx
    mov     [es:si], al
    inc     cx
    call    putc
    jmp     .next
.back:
    test    cx, cx
    jz      .next
    dec     cx
    mov     al, 8
    call    putc
    mov     al, ' '
    call    putc
    mov     al, 8
    call    putc
    jmp     .next
.done:
    mov     al, 13
    call    putc
    mov     al, 10
    call    putc
    mov     si, DBG_BUF
    add     si, cx
    mov     byte [es:si], 0
    mov     ax, cx
    pop     si
    pop     cx
    pop     bx
    ret

; ---------------------------------------------------------------------------
; dbgHex -- si = text in segment 0. Returns ax = the number, and carry set when
; there was not a single hex digit in it.
; ---------------------------------------------------------------------------
dbgHex:
    push    bx
    push    cx
    push    dx
    xor     bx, bx
    xor     cx, cx
.digit:
    mov     al, [es:si]
    cmp     al, ' '
    jne     .try
    inc     si
    jmp     .digit
.try:
    cmp     al, '0'
    jb      .done
    cmp     al, '9'
    jbe     .number
    or      al, 0x20                ; fold A-F down, so case does not matter
    cmp     al, 'a'
    jb      .done
    cmp     al, 'f'
    ja      .done
    sub     al, 'a' - 10
    jmp     .take
.number:
    sub     al, '0'
.take:
    shl     bx, 1
    shl     bx, 1
    shl     bx, 1
    shl     bx, 1
    xor     ah, ah
    add     bx, ax
    inc     si
    inc     cx
    jmp     .digit
.done:
    mov     ax, bx
    test    cx, cx
    jz      .none
    clc
    jmp     .out
.none:
    stc
.out:
    pop     dx
    pop     cx
    pop     bx
    ret

; ---------------------------------------------------------------------------
; dbgDump -- eight rows of eight bytes from [BM_DBG], then on to the next page
; ---------------------------------------------------------------------------
dbgDump:
    push    ax
    push    bx
    push    cx
    push    dx
    push    si
    mov     bx, [es:BM_DBG]
    mov     dx, 8
.row:
    mov     ax, bx
    call    puthexw
    mov     al, ' '
    call    putc
    mov     si, 8
.col:
    mov     al, [es:bx]
    call    puthex
    mov     al, ' '
    call    putc
    inc     bx
    dec     si
    jnz     .col
    sub     bx, 8
    mov     si, 8
.txt:
    mov     al, [es:bx]
    call    dbgDot
    inc     bx
    dec     si
    jnz     .txt
    mov     si, msg_crlf
    call    puts
    dec     dx
    jnz     .row
    mov     [es:BM_DBG], bx         ; the next page starts where this one ended
    pop     si
    pop     dx
    pop     cx
    pop     bx
    pop     ax
    ret

; dbgDot: al = a byte, printed as itself when it is printable and a full stop
; when it is not
dbgDot:
    cmp     al, 32
    jb      .dot
    cmp     al, 127
    jb      putc
.dot:
    mov     al, '.'
    jmp     putc

; puthexw: ax = a word, printed as four hex digits
puthexw:
    push    ax
    mov     al, ah
    call    puthex
    pop     ax
    jmp     puthex

; ---------------------------------------------------------------------------
; dbgIvt -- the interrupt vector table, four vectors to a line.
;
; This is the answer to "is the BIOS I think is running actually installed":
; every entry is an offset:segment pair, and an empty one is 0000:0000.
; ---------------------------------------------------------------------------
dbgIvt:
    push    ax
    push    bx
    push    cx
    push    dx
    push    si
    xor     bx, bx                  ; the vector number
.line:
    mov     ax, bx
    call    puthex
    mov     al, ':'
    call    putc
    call    dbgVector
    mov     al, ' '
    call    putc
    mov     al, ' '
    call    putc
    inc     bx
    mov     ax, bx
    and     ax, 3
    jnz     .line
    mov     si, msg_crlf
    call    puts
    cmp     bx, 0x20
    jb      .line
    pop     si
    pop     dx
    pop     cx
    pop     bx
    pop     ax
    ret

; dbgVector: bx = the vector, printed as offset:segment
dbgVector:
    push    ax
    push    bx
    push    cx
    push    dx
    push    si
    shl     bx, 1
    shl     bx, 1                   ; four bytes a vector
    mov     ax, [es:bx]
    mov     cx, [es:bx + 2]
    call    puthexw
    mov     al, ':'
    call    putc
    mov     ax, cx
    call    puthexw
    pop     si
    pop     dx
    pop     cx
    pop     bx
    pop     ax
    ret

; ---------------------------------------------------------------------------
; the strings
; ---------------------------------------------------------------------------
msg_step_post:  db "POST", 0
msg_step_boot:  db "Booting", 0
msg_bm_title:   db "Boot Menu", 0
msg_bo_title:   db "Boot Options", 0
msg_bm_0:       db "Boot Options", 0
msg_bm_1:       db "Firmware Setup", 0
msg_bm_2:       db "Reboot", 0
msg_bm_3:       db "Halt", 0
msg_bm_4:       db "Debug Shell", 0
bm_items:       dw msg_bm_0, msg_bm_1, msg_bm_2, msg_bm_3, msg_bm_4

msg_bo_0:       db "Hard Disk (0x80)", 0
msg_bo_1:       db "Floppy (0x00)", 0
msg_bo_2:       db "CD-ROM (0x9F)", 0
msg_bo_3:       db "Network (PXE)", 0
bo_items:       dw msg_bo_0, msg_bo_1, msg_bo_2, msg_bo_3
bo_devs:        db 0x80, 0x00, 0x9F, 0xE0

msg_bm_booting: db "Booting from device 0x", 0
msg_bm_nodev:   db "No bootable device.", 13, 10, 0
msg_bm_halted:  db "System halted.", 13, 10, 0

msg_dbg_banner: db "JSW 80386 debug shell", 13, 10, 0
msg_dbg_help:   db "m [addr]  memory    i  interrupts    c  clear    q  quit", 13, 10, 0
msg_dbg_prompt: db "jsw> ", 0
msg_dbg_unknown: db "?", 13, 10, 0
