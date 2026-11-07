; ---------------------------------------------------------------------------
; The setup utility.
;
; A real BIOS gives you a window at POST, and a screen behind it: the machine's
; settings, editable, with the values the firmware will use on the next boot.
; That is what this is -- and every value it edits lives in the CMOS, so the
; screen is not a mock-up, it is the same bytes POST reads.
;
; Layout -- the rv64 setup's, which is what a SeaBIOS-era screen looks like:
; a title bar, then a double-line frame with the pages written into its top
; border, the settings on the left and a panel that explains the selected one
; on the right.
;
;   JSW BIOS Setup Utility                                     v0.3
;   ╔═ Main ═══ Info ══ Exit ══════════════════╦══════════════════════╗
;   ║ Settings                                 ║ Month                ║
;   ║ Month             01                     ║ The clock's month... ║
;   ...
;   ╚══════════════════════════════════════════╩══════════════════════╝
;     Battery: OK                             Boots: 01
;     ^/v select  </> page  Enter edit  F10 save  Esc exit
;
; Keys arrive from the 8042 as set-1 scan codes: 48/50 up/down, 4B/4D
; left/right, 1C Enter, 01 Esc, 0E Backspace, 53 Del, 3C F2. There are no
; release codes in this machine yet, so one press is one event. Leaving is a
; place you go, not a key you press: the Exit page is the only way out.
; ---------------------------------------------------------------------------

SU_BUF   equ 0x540                  ; the working copy: 45 bytes in the data area
SU_MN    equ 11                     ; how many rows the memory page has
SU_ITEM  equ 0x56E                  ; which row is selected
; these moved twice: once out of the buffer when it grew past eleven rows, and
; again when the memory page made the buffer forty-five bytes long. They sit
; below the GX tree's own state on purpose -- the tree and the pages share one
; screen and one buffer, and 0x511..0x51C is the boot menu's scratch.
SU_TAB   equ 0x56F                  ; which page: 0 Main, 1 Info, 2 Exit, 3 Memory
SU_OLD   equ 0x570                  ; the value before an edit, for Esc
SU_OPT   equ 0x571                  ; which button the exit prompt has lit
SU_AUTO_N equ 0x572                 ; the socket count auto-alloc is using
SU_AUTO_MB equ 0x573                ; ... and the megabytes it has left (a word)
SU_N     equ 11                     ; how many rows (the last is read-only)
; the memory panel's own layout inside SU_BUF: rows 0..3, then eight sockets
; whose size, kind and form live at +20, +28 and +36, and the total at 44
SU_M_ROWS  equ 0
SU_M_COUNT equ 4                    ; "Avail Slots"
SU_M_MODE  equ 5                    ; "Mode"
SU_M_AUTO  equ 6                    ; "Auto Alloc"
SU_M_S0    equ 7                    ; socket 0 .. socket 7
SU_M_SIZE  equ 20
SU_M_KIND  equ 28
SU_M_FORM  equ 36
SU_M_TOTAL equ 44

; attribute bytes. White on blue, not grey on blue: a 720 x 400 screen that is
; scaled up in the player has had every pixel line smeared, and grey text turns
; into fog at that size while white stays readable.
SU_PAPER equ 0x1F                   ; blue paper, white ink
SU_HEAD  equ 0x1E                   ; blue paper, yellow ink
SU_SEL   equ 0x70                   ; light grey paper, black ink
SU_GREEN equ 0x20                   ; green paper, black ink: the Dell light
SU_SHADOW equ 0x00                  ; black on black: the drop shadow
SU_INK   equ 0x575                  ; the ink of the row being drawn. It cannot
                                    ; share 0x4F5 with BDA_RESET: every redraw
                                    ; would clear the board's reboot request.
SU_INBOX equ 0x576                  ; 1 while the modal editor owns the screen
SU_DLG_L equ 0x4F6                  ; a dialog's left column, while it draws
SU_DLG_T equ 0x4F7                  ; its top row
SU_DLG_W equ 0x4F8                  ; its width
SU_DLG_R equ 0x4F9                  ; which of its rows is being drawn
SU_DLG_H equ 8                      ; the editing dialogs are eight rows tall
SU_DLG_N equ 0x4FA                  ; a dialog's height, while it draws
SU_DLG_P equ 0x4FD                  ; ... and the paper its body is painted in
SU_DLG_E equ 0x4FB                  ; ... and that plus one, for the shadow
SU_DLG_B equ 0x4FC                  ; ... and that minus one, its last row

; the frame, drawn with CP437's double-line set, the way the rv64 setup does
G_HBAR   equ 0xCD
G_VBAR   equ 0xBA
G_TL     equ 0xC9
G_TR     equ 0xBB
G_BL     equ 0xC8
G_BR     equ 0xBC
G_TJ     equ 0xCB                   ; the rule meets the top border
G_BJ     equ 0xCA                   ; and the bottom border
G_ML     equ 0xCC                   ; ... and the rule under the menu bar
G_MR     equ 0xB9
G_MJ     equ 0xCE

SU_TOP   equ 1                      ; the frame's top border; row 0 is the title
SU_BOT   equ 28                     ; and its bottom border
SU_TABS  equ 2                      ; the menu bar, inside the frame
SU_SEP   equ 3                      ; the rule that closes it off
SU_SPLIT equ 45                     ; the column rule between menu and panel
SU_RX    equ 47                     ; the panel's first column
SU_ROW0  equ 5                      ; the first menu row
SU_VAL   equ 20                     ; the value column

; ---------------------------------------------------------------------------
; suWindow -- the "press DEL" window at POST
;
; `al` comes back as 1 when the user asked for setup. The wait is a counted
; loop, not a clock: this firmware has no timer of its own yet, and a counted
; delay is the honest description of "about a second on the machine it runs
; on". Delete and F2 open the utility; any other key boots at once, which is
; also how the boot tests skip the wait.
; ---------------------------------------------------------------------------
suWindow:
    push    bx
    push    cx
    push    dx
    push    si
    ; the countdown is a line of its own, outside POST's little window: give it
    ; a one-row window, or printing it here would scroll the log instead
    mov     ax, [es:CON_T]          ; CON_T in al, CON_B in ah
    push    ax
    mov     al, [es:CON_W]
    push    ax
    mov     al, [es:CON_ATTR]
    push    ax
    mov     al, [es:BDA_VIDROWS]    ; three rows up from the bottom of the card
    sub     al, 3
    mov     byte [es:CON_T], al
    mov     byte [es:CON_B], al
    mov     dh, al                  ; under the artwork, above its labels
    mov     al, [es:BDA_VIDCOLS]    ; the card's width, not the plane's: a narrow
    mov     byte [es:CON_W], al     ; card keeps the notice on one row
    mov     byte [es:CON_ATTR], 0x07   ; a plain black line, if there is no splash
    cmp     byte [es:BDA_SPLASH], 0
    je      .attr
    mov     byte [es:CON_ATTR], 0x1E   ; yellow on the splash's blue
.attr:
    mov     dl, 1                   ; hard against the left edge of the card
    call    suGoto
    mov     si, msg_press_del
    call    puts
    mov     bx, 5                   ; the seconds on the counter
.tick:
    mov     al, bl
    add     al, '0'
    call    putc
    mov     si, msg_tick            ; "... "
    cmp     bx, 1
    jne     .dots
    mov     si, msg_tick_last       ; the last one is a dot short: the paren
.dots:                              ; comes straight after it
    call    puts
    call    suWait                  ; about a second, or less if a key arrives
    test    al, al
    jnz     .key
    dec     bx
    jz      .counted_out
    mov     al, ' '
    call    putc
    jmp     .tick
.counted_out:
    mov     si, msg_tick_end
    call    puts
    xor     al, al
    jmp     .out
.key:
    mov     si, msg_tick_end        ; close the line whatever was pressed
    push    ax
    call    puts
    pop     ax
    cmp     al, 0x53                ; Delete: the setup
    je      .setup
    cmp     al, 0x3C                ; F2: the same
    je      .setup
    cmp     al, 0x32                ; M: the boot menu
    je      .menu
    cmp     al, 0x58                ; F12: the same
    je      .menu
    ; any other key means "get on with it", the way a real board does
    xor     al, al
    jmp     .out
.setup:
    mov     al, 2
    jmp     .out
.menu:
    mov     al, 1
.out:
    mov     dl, al                  ; keep the answer across the restore
    pop     ax
    mov     [es:CON_ATTR], al
    pop     ax
    mov     [es:CON_W], al
    pop     ax
    mov     [es:CON_T], al
    mov     [es:CON_B], ah
    mov     al, dl
    pop     si
    pop     dx
    pop     cx
    pop     bx
    ret

; ---------------------------------------------------------------------------
; suWait -- about a second of wall clock, cut short by a keypress.
;
; The delay is counted, not timed: this firmware has no clock it trusts at POST
; and a counted loop is the honest description of "about a second on the
; machine it runs on". `loop` counts in cx, and cx is sixteen bits -- 500,000
; does not fit, it truncates to 41,088 and the countdown would come back to a
; blink. Twelve rounds of 50,000 polls is the second.
;
; Returns al = the scan code of the key that interrupted it, or 0.
; ---------------------------------------------------------------------------
suWait:
    push    bx
    push    cx
    mov     bx, 12
.round:
    mov     cx, 50000
.poll:
    in      al, 0x64
    test    al, 1
    jz      .next
    in      al, 0x60                ; a scan code arrived
    jmp     .out
.next:
    loop    .poll
    dec     bx
    jnz     .round
    xor     al, al
.out:
    pop     cx
    pop     bx
    ret

; ---------------------------------------------------------------------------
; suLoad / suSave -- the CMOS and the working copy
; ---------------------------------------------------------------------------
suLoad:
    mov     al, 0x08                ; month
    call    cmos_read
    call    bcd2bin
    mov     [es:SU_BUF + 0], al
    mov     al, 0x07                ; day
    call    cmos_read
    call    bcd2bin
    mov     [es:SU_BUF + 1], al
    mov     al, 0x09                ; year
    call    cmos_read
    call    bcd2bin
    mov     [es:SU_BUF + 2], al
    mov     al, 0x04                ; hour
    call    cmos_read
    call    bcd2bin
    mov     [es:SU_BUF + 3], al
    mov     al, 0x02                ; minute
    call    cmos_read
    call    bcd2bin
    mov     [es:SU_BUF + 4], al
    mov     al, 0x00                ; second
    call    cmos_read
    call    bcd2bin
    mov     [es:SU_BUF + 5], al
    mov     al, 0x10                ; floppy
    call    cmos_read
    shr     al, 4
    and     al, 0x0F
    cmp     al, 4
    jbe     .fl_ok
    xor     al, al
.fl_ok:
    mov     [es:SU_BUF + 6], al
    mov     al, 0x12                ; hard disk
    call    cmos_read
    cmp     al, 3
    jbe     .hd_ok
    xor     al, al
.hd_ok:
    mov     [es:SU_BUF + 7], al
    mov     al, 0x2D                ; boot order
    call    cmos_read
    cmp     al, 3
    jbe     .bo_ok
    xor     al, al
.bo_ok:
    mov     [es:SU_BUF + 8], al
    mov     al, 0x34                ; the emulation master switch
    call    cmos_read
    cmp     al, 1
    jbe     .emu_ok
    mov     al, 1                   ; a blank battery reads as "on"
.emu_ok:
    mov     [es:SU_BUF + 11], al
    mov     al, 0x35                ; the battery itself
    call    cmos_read
    cmp     al, 1
    jbe     .bat_ok
    mov     al, 1
.bat_ok:
    mov     [es:SU_BUF + 12], al
    mov     al, 0x36                ; the clock the board reports
    call    cmos_read
    cmp     al, 4
    jbe     .frq_ok
    mov     al, 2                   ; 33 MHz, the number on the crystal
.frq_ok:
    mov     [es:SU_BUF + 13], al
    mov     al, 0x37                ; the keyboard's repeat rate
    call    cmos_read
    cmp     al, 4
    jbe     .rep_ok
    mov     al, 2                   ; 1/8 s, the one that feels like a PC
.rep_ok:
    mov     [es:SU_BUF + 14], al
    mov     al, 0x15                ; base memory, low
    call    cmos_read
    mov     bl, al
    mov     al, 0x16                ; high
    call    cmos_read
    mov     bh, al
    mov     ax, bx
    ; the row edits it in 64 KiB steps, so store the step count
    mov     cl, 6
    shr     ax, cl
    cmp     al, 10
    jbe     .mem_ok
    mov     al, 10
.mem_ok:
    mov     [es:SU_BUF + 9], al
    ; the emulation's own knobs: cores, threads, performance, clock rate. Each
    ; is edited as an index and stored as one, so a blank battery is row 0.
    mov     al, 0x38                ; the core count, less one
    call    cmos_read
    cmp     al, 15
    jbe     .cores_ok
    mov     al, 1                   ; two cores, the way the chipset comes up
.cores_ok:
    mov     [es:SU_BUF + 15], al
    mov     al, 0x39                ; threads per core, less one
    call    cmos_read
    cmp     al, 3
    jbe     .threads_ok
    xor     al, al
.threads_ok:
    mov     [es:SU_BUF + 16], al
    mov     al, 0x3A                ; which entry of the performance table
    call    cmos_read
    cmp     al, 7
    jbe     .perf_ok
    xor     al, al                  ; 1x: nobody's multiplier but the real one
.perf_ok:
    mov     [es:SU_BUF + 17], al
    mov     al, 0x3B                ; which entry of the clock-rate table
    call    cmos_read
    cmp     al, 5
    jbe     .rate_ok
    xor     al, al                  ; 100%: the guest's clock keeps real time
.rate_ok:
    mov     [es:SU_BUF + 18], al
    call    suMemLoad               ; and the memory panel, on its own page
    ret

; suMemLoad -- the panel's rows out of the CMOS, with blank-battery defaults:
; four sockets, extended memory on, and 128 MiB in the first one, which is the
; board this machine has been booting with all along.
suMemLoad:
    push    ax
    push    bx
    push    cx
    push    dx
    push    si
    push    di
    xor     di, di
.blank:
    mov     byte [es:SU_BUF + SU_M_ROWS + di], 0
    inc     di
    cmp     di, 45
    jb      .blank
    mov     al, 0x3C
    call    cmos_read
    cmp     al, 1
    jae     .count_ok
    mov     al, 4
.count_ok:
    cmp     al, 8
    jbe     .count_max
    mov     al, 8
.count_max:
    mov     [es:SU_BUF + SU_M_COUNT], al
    mov     al, 0x3D                ; internal memory or the module
    call    cmos_read
    cmp     al, 1
    jbe     .mode_ok
    mov     al, 1                   ; a blank battery takes the module
.mode_ok:
    mov     [es:SU_BUF + SU_M_MODE], al
    xor     bx, bx
    xor     bh, bh                  ; bl counts the sockets, bh must not be in di
.slot:
    mov     di, bx
    mov     al, bl
    add     al, 0x40
    call    cmos_read
    cmp     al, 8
    jbe     .size_ok
    xor     al, al
.size_ok:
    mov     [es:SU_BUF + SU_M_SIZE + di], al
    mov     al, bl
    add     al, 0x48
    call    cmos_read
    cmp     al, 3
    jbe     .kind_ok
    xor     al, al
.kind_ok:
    mov     [es:SU_BUF + SU_M_KIND + di], al
    mov     al, bl
    add     al, 0x50
    call    cmos_read
    cmp     al, 1
    jbe     .form_ok
    xor     al, al
.form_ok:
    mov     [es:SU_BUF + SU_M_FORM + di], al
    inc     bl
    cmp     bl, 8
    jb      .slot
    ; a board with nothing recorded gets the one this build has always had.
    ; The test is the mark suSave leaves at 0x58, not the total: a board whose
    ; sockets were all emptied on purpose has a total of zero too.
    mov     al, 0x58
    call    cmos_read
    cmp     al, 0xA5
    je      .total_ok
    mov     byte [es:SU_BUF + SU_M_COUNT], 4
    mov     byte [es:SU_BUF + SU_M_MODE], 1
    mov     byte [es:SU_BUF + SU_M_SIZE], 8    ; 128 MiB in socket 0
    call    suMemTotal
    jmp     .done
.total_ok:
    call    suMemTotal
.done:
    pop     di
    pop     si
    pop     dx
    pop     cx
    pop     bx
    pop     ax
    ret

; suMemDiffers -- did the memory the panel asks for differ from the one that is
; running? al = 1 if so.
;
; The running board's settings came from the CMOS at power-on, so comparing the
; panel against the CMOS is comparing it against the machine -- and it is the
; only comparison that matters, because anything that differs now is something
; that needs a reboot and anything that does not need one must not cause one.
suMemDiffers:
    push    bx
    push    cx
    push    dx
    push    di
    mov     al, 0x3C
    call    cmos_read
    cmp     al, [es:SU_BUF + SU_M_COUNT]
    jne     .yes
    mov     al, 0x34
    call    cmos_read
    cmp     al, [es:SU_BUF + SU_M_MODE]
    je      .slots
    cmp     byte [es:SU_BUF + SU_M_COUNT], 0
    je      .slots                  ; no sockets: the mode changes nothing
    jmp     .yes
.slots:
    xor     di, di
.slot:
    mov     ax, di
    add     al, 0x40
    call    cmos_read
    mov     si, di
    add     si, SU_BUF + SU_M_SIZE
    cmp     al, [es:si]
    jne     .yes
    mov     ax, di
    add     al, 0x48
    call    cmos_read
    mov     si, di
    add     si, SU_BUF + SU_M_KIND
    cmp     al, [es:si]
    mov     si, di
    add     si, SU_BUF + SU_M_KIND
    cmp     al, [es:si]
    jne     .yes
    mov     ax, di
    add     al, 0x50
    call    cmos_read
    mov     si, di
    add     si, SU_BUF + SU_M_FORM
    cmp     al, [es:si]
    mov     si, di
    add     si, SU_BUF + SU_M_FORM
    cmp     al, [es:si]
    jne     .yes
    inc     di
    cmp     di, 8
    jb      .slot
    xor     al, al
    jmp     .out
.yes:
    mov     al, 1
.out:
    pop     di
    pop     dx
    pop     cx
    pop     bx
    ret

; suReboot -- ask the machine to restart, and wait for it to do so.
;
; The reset comes from the chipset: a write to 0x64 asks for it, and the
; emulator answers by reloading its registers and heading for the reset vector.
; That is not this firmware's to do from inside an interrupt handler, so the
; request is staged at 0x4F4 and picked up by the board's own main loop, which
; is where a real chipset's reset would be.
suReboot:
    push    ax
    push    es
    xor     ax, ax
    mov     es, ax
    mov     byte [es:BDA_RESET], 1  ; the chipset watches this byte and resets
    pop     es
    pop     ax
    ret

suSave:
    mov     al, [es:SU_BUF + 0]
    call    bin2bcd
    mov     cl, al
    mov     al, 0x08
    call    cmos_write
    mov     al, [es:SU_BUF + 1]
    call    bin2bcd
    mov     cl, al
    mov     al, 0x07
    call    cmos_write
    mov     al, [es:SU_BUF + 2]
    call    bin2bcd
    mov     cl, al
    mov     al, 0x09
    call    cmos_write
    mov     al, [es:SU_BUF + 3]
    call    bin2bcd
    mov     cl, al
    mov     al, 0x04
    call    cmos_write
    mov     al, [es:SU_BUF + 4]
    call    bin2bcd
    mov     cl, al
    mov     al, 0x02
    call    cmos_write
    mov     al, [es:SU_BUF + 5]
    call    bin2bcd
    mov     cl, al
    mov     al, 0x00
    call    cmos_write
    mov     al, [es:SU_BUF + 6]
    mov     cl, 4
    shl     al, cl
    mov     cl, al
    mov     al, 0x10
    call    cmos_write
    mov     al, [es:SU_BUF + 7]
    mov     cl, al
    mov     al, 0x12
    call    cmos_write
    mov     al, [es:SU_BUF + 8]
    mov     cl, al
    mov     al, 0x2D
    call    cmos_write
    ; base memory: 64 KiB steps back to a sixteen-bit KiB count, split across
    ; 0x15 and 0x16 -- the shift has to be sixteen bits wide or 576 becomes 64
    mov     al, [es:SU_BUF + 9]
    xor     ah, ah
    mov     cl, 6
    shl     ax, cl
    mov     cl, al
    mov     al, 0x15
    call    cmos_write
    mov     al, [es:SU_BUF + 9]
    xor     ah, ah
    mov     cl, 6
    shl     ax, cl
    mov     cl, 8
    shr     ax, cl
    mov     cl, al
    mov     al, 0x16
    call    cmos_write
    mov     al, [es:SU_BUF + 11]
    mov     cl, al
    mov     al, 0x34
    call    cmos_write
    mov     al, [es:SU_BUF + 12]
    mov     cl, al
    mov     al, 0x35
    call    cmos_write
    mov     al, [es:SU_BUF + 13]
    mov     cl, al
    mov     al, 0x36
    call    cmos_write
    mov     al, [es:SU_BUF + 14]
    mov     cl, al
    mov     al, 0x37
    call    cmos_write
    mov     al, [es:SU_BUF + 15]
    mov     cl, al
    mov     al, 0x38
    call    cmos_write
    mov     al, [es:SU_BUF + 16]
    mov     cl, al
    mov     al, 0x39
    call    cmos_write
    mov     al, [es:SU_BUF + 17]
    mov     cl, al
    mov     al, 0x3A
    call    cmos_write
    mov     al, [es:SU_BUF + 18]
    mov     cl, al
    mov     al, 0x3B
    call    cmos_write
    ; the memory panel: how many slots are fitted, then each slot's size, its
    ; kind and its form. The chipset works out the extended total from these
    ; at power-on, and E820 reads that total back -- so the setup decides how
    ; much memory the next boot has without the firmware ever multiplying.
    mov     al, [es:SU_BUF + SU_M_COUNT]
    mov     cl, al
    mov     al, 0x3C
    call    cmos_write
    mov     al, [es:SU_BUF + SU_M_MODE]
    mov     cl, al
    mov     al, 0x3D                ; internal or extended, for the next boot
    call    cmos_write
    mov     bl, 0
    xor     bh, bh                  ; bl counts the sockets, bh must not be in di
.memslot:
    mov     al, bl
    add     al, 0x40                ; slot sizes start at 0x40
    mov     di, bx
    add     di, SU_M_SIZE           ; ... and live at SU_BUF + SU_M_SIZE + slot
    mov     cl, [es:SU_BUF + di]
    call    cmos_write
    mov     al, bl
    add     al, 0x48                ; slot kinds at 0x48
    mov     cl, [es:SU_BUF + di + SU_M_KIND - SU_M_SIZE]
    call    cmos_write
    mov     al, bl
    add     al, 0x50                ; forms at 0x50
    mov     cl, [es:SU_BUF + di + SU_M_FORM - SU_M_SIZE]
    call    cmos_write
    inc     bl
    cmp     bl, 8
    jb      .memslot
    ; and the total, which the chipset's own registers will agree with
    mov     al, [es:SU_BUF + SU_M_TOTAL]    ; extended memory, in megabytes
    mov     cl, al
    mov     al, 0x17                ; low byte: E820 and int 15h AH=88 read this
    call    cmos_write
    mov     al, [es:SU_BUF + SU_M_TOTAL]
    shr     al, 1
    shr     al, 1
    shr     al, 1
    shr     al, 1
    shr     al, 1
    shr     al, 1
    shr     al, 1
    shr     al, 1
    mov     cl, al
    mov     al, 0x18
    call    cmos_write
    ; the mark that says these sockets are the ones the operator chose, so a
    ; blank battery can still be told from a board that was emptied on purpose
    mov     al, 0x58
    mov     cl, 0xA5
    call    cmos_write
    ret

; al = BCD -> al = binary
bcd2bin:
    push    bx
    push    cx
    mov     ah, al
    and     al, 0x0F
    mov     bl, al
    mov     al, ah
    mov     cl, 4
    shr     al, cl
    and     al, 0x0F
    mov     cl, 10
    mul     cl
    add     al, bl
    pop     cx
    pop     bx
    ret

; al = binary -> al = BCD
bin2bcd:
    push    bx
    push    cx
    xor     ah, ah
    mov     bl, 10
    div     bl
    mov     cl, al
    shl     cl, 4
    or      cl, ah
    mov     al, cl
    pop     cx
    pop     bx
    ret

; ---------------------------------------------------------------------------
; drawing
; ---------------------------------------------------------------------------

; suGoto: dl = column, dh = row
suGoto:
    mov     [es:BDA_CURSOR_COL], dl
    mov     [es:BDA_CURSOR_ROW], dh
    ret

; suFill: cx = count, al = the character, bl = the attribute
suFill:
    push    ax
    push    cx
    push    dx
.loop:
    push    ax
    mov     ah, 0x0E
    int     0x10
    pop     ax
    loop    .loop
    pop     dx
    pop     cx
    pop     ax
    ret

; suPrint: si = a zero-terminated string, bl = the attribute
suPrint:
    push    ax
    push    si
.loop:
    lodsb
    test    al, al
    jz      .done
    mov     ah, 0x0E
    int     0x10
    jmp     .loop
.done:
    pop     si
    pop     ax
    ret

; suBar: dl/dh = the start, cx = the width, al = the character
suBar:
    push    cx
    push    dx
    push    ax
    push    bx
    call    suGoto
    call    suFill
    pop     bx
    pop     ax
    pop     dx
    pop     cx
    ret

; ---------------------------------------------------------------------------
; suAdjust -- change the selected value by dl (-1 or +1)
; ---------------------------------------------------------------------------
suAdjust:
    push    ax
    push    bx
    push    cx
    push    si
    mov     al, [es:SU_ITEM]
    mov     ah, al                  ; ah = the setting, kept while we work
    xor     bx, bx
    mov     bl, al
    mov     si, bx
    add     si, si                  ; the table is words, the setting is a byte
    mov     si, [si + su_bufof]     ; and si = the row it lives in
    mov     al, [es:SU_BUF + si]    ; the value
    cmp     bl, 19
    jae     .memory
    cmp     bl, 0
    jne     .not_month
    call    suStep12                ; 1..12
    jmp     .store
.not_month:
    cmp     bl, 1
    jne     .not_day
    call    suStep31
    jmp     .store
.not_day:
    ; everything else counts from zero, and the count is the length of the
    ; table the row reads its words out of
    mov     cl, 100                 ; the year
    cmp     bl, 2
    je      .wrap
    mov     cl, 24                  ; the hour
    cmp     bl, 3
    je      .wrap
    mov     cl, 60                  ; minute and second
    cmp     bl, 4
    je      .wrap
    cmp     bl, 5
    je      .wrap
    mov     cl, 5                   ; the floppy table
    cmp     bl, 6
    je      .wrap
    mov     cl, 4                   ; the disk and the boot order
    cmp     bl, 7
    je      .wrap
    cmp     bl, 8
    je      .wrap
    mov     cl, 11                  ; base memory, in 64 KiB steps
    cmp     bl, 9
    je      .wrap
    mov     cl, 2                   ; the two On/Off switches
    cmp     bl, 11
    je      .wrap
    cmp     bl, 12
    je      .wrap
    mov     cl, 8                   ; the performance multiplier
    cmp     bl, 17
    je      .wrap
    mov     cl, 6                   ; the guest clock's rate
    cmp     bl, 18
    je      .wrap
    mov     cl, 16                  ; how many cores there are, less one
    cmp     bl, 15
    je      .wrap
    mov     cl, 4                   ; threads per core, less one
    cmp     bl, 16
    je      .wrap
    mov     cl, 5                   ; the clock and the repeat rate
.wrap:
    call    suStepN
.store:
    mov     [es:SU_BUF + si], al
    cmp     bl, 19
    jb      .drawn
    call    suMemTotal              ; the sockets just changed, so the total has
.drawn:                             ; to change with them, in the buffer too
    cmp     byte [es:SU_INBOX], 0
    jne     .out                    ; the dialog repaints a field, not a page row
    mov     al, ah
    call    suDrawRow
.out:
    pop     si
    pop     cx
    pop     bx
    pop     ax
    ret

; the memory panel's own three kinds of value: how many sockets, which storage,
; and a socket's size -- nine entries, none of them a number the guest sees
.memory:
    mov     cl, 8                   ; the socket count, 1 to 8, less one
    cmp     bl, 19
    je      .mwrap
    mov     cl, 2                   ; internal or extended
    cmp     bl, 20
    je      .mwrap
    mov     cl, 9                   ; none, 1, 2, 4 ... 128 MB
.mwrap:
    call    suStepN
    jmp     .store

; al/dx = the value and the step, cl = how many entries. Wraps at both ends.
;
; The downward wrap is the one an unsigned compare gets wrong: 0 - 1 is 0xFF,
; which is bigger than every count on this screen, so a plain "if it is over
; the top, wrap to zero" turns Left on the first entry into a value that stays
; on the first entry. The sign flag knows the difference.
suStepN:
    add     al, dl
    jns     .pos
    mov     al, cl
    dec     al
    ret
.pos:
    cmp     al, cl
    jb      .done
    xor     al, al
.done:
    ret

; 1..12, wrapping
suStep12:
    add     al, dl
    cmp     al, 13
    jb      .a
    mov     al, 1
.a:
    cmp     al, 1
    jge     .b
    mov     al, 12
.b:
    ret

; 1..31
suStep31:
    add     al, dl
    cmp     al, 32
    jb      .a
    mov     al, 1
.a:
    cmp     al, 1
    jge     .b
    mov     al, 31
.b:
    ret

; 0..59
suStep60:
    add     al, dl
    cmp     al, 60
    jb      .a
    xor     al, al
.a:
    cmp     al, 0
    jge     .b
    mov     al, 59
.b:
    ret

; ---------------------------------------------------------------------------
; suEnter: the setup utility. Three pages on the top border, arrows move,
; Enter edits the selected setting in a dialog, Esc asks before it throws
; anything away -- the shape the rv64 setup uses.
; ---------------------------------------------------------------------------
suEnter:
    mov     byte [es:CON_ATTR], SU_PAPER
    call    suLoad
    mov     byte [es:SU_TAB], 0
    mov     byte [es:SU_ITEM], 0
    call    drawSetup               ; the whole screen, once
.key:
    call    keyScan
    cmp     al, 0x48                ; up
    je      .up
    cmp     al, 0x50                ; down
    je      .down
    cmp     al, 0x4B                ; left: the page before
    je      .left
    cmp     al, 0x4D                ; right: the page after
    je      .right
    cmp     al, 0x1C                ; Enter: edit, or run the action
    je      .enter
    cmp     al, 0x01                ; Esc asks
    je      .ask
    cmp     al, 0x0E                ; Backspace asks too
    je      .ask
    jmp     .key

.up:
    mov     cl, [es:SU_ITEM]        ; the row that was lit
    mov     al, [es:SU_TAB]
    call    tabItems
    test    bl, bl
    jz      .key                    ; a page with nothing to select
    mov     al, [es:SU_ITEM]
    test    al, al
    jnz     .updec
    mov     al, bl
.updec:
    dec     al
    mov     [es:SU_ITEM], al
    jmp     .moved

.down:
    mov     cl, [es:SU_ITEM]
    mov     al, [es:SU_TAB]
    call    tabItems
    test    bl, bl
    jz      .key
    mov     al, [es:SU_ITEM]
    inc     al
    cmp     al, bl
    jb      .downset
    xor     al, al
.downset:
    mov     [es:SU_ITEM], al
.moved:
    ; two rows and the panel, not the whole screen: a full repaint is nearly
    ; two thousand characters and the arrow keys come faster than that
    cmp     byte [es:SU_TAB], 0
    jne     .movedpage
    mov     al, cl
    call    suDrawRow
    mov     al, [es:SU_ITEM]
    call    suDrawRow
    call    suPanelTop
    jmp     .key
.movedpage:
    ; the exit page has its own painter for its two bars
    call    pageExit
    jmp     .key

.left:
    mov     al, [es:SU_TAB]
    test    al, al
    jnz     .leftdec
    mov     al, 4
.leftdec:
    dec     al
    mov     [es:SU_TAB], al
    mov     byte [es:SU_ITEM], 0
    call    drawSetup
    jmp     .key

.right:
    mov     al, [es:SU_TAB]
    inc     al
    cmp     al, 4
    jb      .rightset
    xor     al, al
.rightset:
    mov     [es:SU_TAB], al
    mov     byte [es:SU_ITEM], 0
    call    drawSetup
    jmp     .key

.enter:
    mov     al, [es:SU_TAB]
    test    al, al
    jz      .edit
    cmp     al, 2
    je      .exitpage
    cmp     al, 3
    je      .mempage
    jmp     .key                    ; the information page has no actions
.mempage:
    cmp     byte [es:SU_ITEM], 4
    jne     .key                    ; the size row: edit it like any other
    mov     al, [es:SU_BUF + SU_M_COUNT]
    mov     cx, [es:SU_AUTO_MB]
    call    suAutoAlloc
    call    pageMemory
    jmp     .key
.exitpage:
    cmp     byte [es:SU_ITEM], 0
    jne     .leave                  ; "discard and exit"
    jmp     .save
.edit:
    call    suEdit
    call    drawSetup               ; the dialog left its paper behind
    jmp     .key
.ask:
    call    suAskExit               ; al = 1 save, 0 discard, 2 cancel
    cmp     al, 1
    je      .save
    cmp     al, 2
    je      .cancel
    jmp     .leave
.cancel:
    call    drawSetup
    jmp     .key
.save:
    call    suSave
    ; a save that changed the memory can only take effect at a reboot, and a
    ; board that quietly kept the old memory would be lying about its own
    ; setup. The chipset applies the rest at POST either way.
    call    suMemDiffers
    test    al, al
    jz      .leave
    call    suReboot
    jmp     .leave
.leave:
    mov     byte [es:CON_ATTR], 0x07
    push    ax
    push    cx
    push    dx
    call    clear_text
    xor     dl, dl
    xor     dh, dh
    call    suGoto
    pop     dx
    pop     cx
    pop     ax
    ret

; ---------------------------------------------------------------------------
; suDialog -- a grey panel with a blue title bar and a black drop shadow.
;
; The shadow is drawn first, one row down and two columns right, and the panel
; then covers all of it but its right and bottom edge -- which is what makes it
; read as something lying on top of the screen instead of a hole cut in it.
; The title gets its own row *inside* the frame: a double rule drawn through
; the text reads as a broken corner, which is exactly how it looked.
;
;   dl = left column, dh = top row, cx = width, si = the title
; ---------------------------------------------------------------------------
suDialog:
    mov     byte [es:SU_DLG_N], SU_DLG_H
    mov     bh, SU_SEL              ; the editing dialogs sit on a grey panel
    jmp     suDialogGo

; suDialogN: the same panel, any number of rows, on paper of your choosing.
;   bl = the height, bh = the paper
suDialogN:
    mov     [es:SU_DLG_N], bl
suDialogGo:
    push    ax
    push    bx
    push    cx
    push    dx
    push    si
    ; A dialog owns the whole screen while it is up. The tree leaves the console
    ; window aimed at one of its panes -- twenty-eight columns wide, ending at
    ; row 22 -- and printing a wider line through that window wraps and scrolls
    ; the pane: that is what tore the title bar off this box.
    mov     byte [es:CON_T], 0
    mov     al, [es:BDA_VIDROWS]
    dec     al
    mov     byte [es:CON_B], al
    mov     byte [es:CON_W], COLS
    mov     byte [es:CON_ATTR], SU_PAPER
    push    bx
    mov     [es:SU_DLG_L], dl
    mov     [es:SU_DLG_T], dh
    mov     [es:SU_DLG_W], cl
    mov     [es:SU_DLG_P], bh       ; the paper the body is painted in
    pop     bx
    ; the two derived rows are worked out once, because every loop in here
    ; compares against one of them
    mov     al, [es:SU_DLG_N]
    inc     al
    mov     [es:SU_DLG_E], al
    dec     al
    dec     al
    mov     [es:SU_DLG_B], al
    ; --- the shadow, one row down and two columns right
    mov     byte [es:SU_DLG_R], 1
.shadow:
    mov     al, [es:SU_DLG_R]
    cmp     al, [es:SU_DLG_E]
    jae     .body
    mov     dh, [es:SU_DLG_T]
    add     dh, al
    mov     dl, [es:SU_DLG_L]
    add     dl, 2
    mov     cl, [es:SU_DLG_W]
    mov     al, ' '
    mov     bl, SU_SHADOW
    call    suBar
    inc     byte [es:SU_DLG_R]
    jmp     .shadow
    ; --- the paper, every row of it
.body:
    mov     byte [es:SU_DLG_R], 0
.row:
    mov     al, [es:SU_DLG_R]
    cmp     al, [es:SU_DLG_N]
    jae     .title
    mov     dh, [es:SU_DLG_T]
    add     dh, al
    mov     dl, [es:SU_DLG_L]
    mov     cl, [es:SU_DLG_W]
    mov     al, ' '
    mov     bl, [es:SU_DLG_P]
    call    suBar
    inc     byte [es:SU_DLG_R]
    jmp     .row
    ; --- the title bar, one row in from the top rule
.title:
    mov     dh, [es:SU_DLG_T]
    inc     dh
    mov     dl, [es:SU_DLG_L]
    inc     dl
    mov     cl, [es:SU_DLG_W]
    sub     cl, 2
    mov     al, ' '
    mov     bl, SU_HEAD
    call    suBar
    mov     dh, [es:SU_DLG_T]
    inc     dh
    mov     dl, [es:SU_DLG_L]
    add     dl, 2
    call    suGoto
    mov     bl, SU_HEAD
    call    suPrint
    ; --- the frame: a double rule top and bottom, a bar either side
    mov     dh, [es:SU_DLG_T]
    call    dlgRuleTop
    mov     dh, [es:SU_DLG_T]
    add     dh, [es:SU_DLG_B]
    call    dlgRuleBottom
    mov     byte [es:SU_DLG_R], 1
.side:
    mov     al, [es:SU_DLG_R]
    cmp     al, [es:SU_DLG_B]
    jae     .done
    mov     dh, [es:SU_DLG_T]
    add     dh, al
    mov     dl, [es:SU_DLG_L]
    mov     al, G_VBAR
    mov     bl, SU_SEL
    call    suPlot
    mov     dh, [es:SU_DLG_T]
    add     dh, [es:SU_DLG_R]
    mov     dl, [es:SU_DLG_L]
    add     dl, [es:SU_DLG_W]
    dec     dl
    mov     al, G_VBAR
    mov     bl, SU_SEL
    call    suPlot
    inc     byte [es:SU_DLG_R]
    jmp     .side
.done:
    pop     si
    pop     dx
    pop     cx
    pop     bx
    pop     ax
    ret

; dlgRuleTop: dh = the row, the top border of the dialog being drawn
dlgRuleTop:
    push    ax
    push    bx
    push    cx
    push    dx
    mov     dl, [es:SU_DLG_L]
    inc     dl
    mov     cl, [es:SU_DLG_W]
    sub     cl, 2
    mov     al, G_HBAR
    mov     bl, SU_SEL
    call    suBar
    mov     dl, [es:SU_DLG_L]
    mov     al, G_TL
    mov     bl, SU_SEL
    call    suPlot
    mov     dl, [es:SU_DLG_L]
    add     dl, [es:SU_DLG_W]
    dec     dl
    mov     al, G_TR
    mov     bl, SU_SEL
    call    suPlot
    pop     dx
    pop     cx
    pop     bx
    pop     ax
    ret

; dlgRuleBottom: dh = the row, the bottom border
dlgRuleBottom:
    push    ax
    push    bx
    push    cx
    push    dx
    mov     dl, [es:SU_DLG_L]
    inc     dl
    mov     cl, [es:SU_DLG_W]
    sub     cl, 2
    mov     al, G_HBAR
    mov     bl, SU_SEL
    call    suBar
    mov     dl, [es:SU_DLG_L]
    mov     al, G_BL
    mov     bl, SU_SEL
    call    suPlot
    mov     dl, [es:SU_DLG_L]
    add     dl, [es:SU_DLG_W]
    dec     dl
    mov     al, G_BR
    mov     bl, SU_SEL
    call    suPlot
    pop     dx
    pop     cx
    pop     bx
    pop     ax
    ret

; ---------------------------------------------------------------------------
; suEdit -- Enter on a setting. A dialog holds the value with an arrow either
; side of it; Left/Right change it and repaint, Enter keeps it, Esc puts the
; old one back.
; ---------------------------------------------------------------------------
suEdit:
    push    ax
    push    bx
    push    cx
    push    dx
    push    di
    mov     al, [es:SU_ITEM]
    xor     ah, ah
    mov     di, ax
    add     di, di                  ; the table is words, the setting is a byte
    mov     di, [di + su_bufof]     ; the row the setting lives in
    mov     al, [es:SU_BUF + di]
    mov     [es:SU_OLD], al
    mov     byte [es:SU_INBOX], 1
    call    suEditBox               ; the panel, once: a key changes a number
.key:
    call    keyScan
    cmp     al, 0x4B                ; left
    je      .left
    cmp     al, 0x4D                ; right
    je      .right
    cmp     al, 0x1C                ; Enter keeps it
    je      .done
    cmp     al, 0x01                ; Esc throws it away
    je      .revert
    cmp     al, 0x0E                ; Backspace too
    je      .revert
    jmp     .key
.left:
    mov     dl, -1
    call    suAdjust
    call    suEditValue
    jmp     .key
.right:
    mov     dl, 1
    call    suAdjust
    call    suEditValue
    jmp     .key
.revert:
    mov     al, [es:SU_OLD]
    mov     [es:SU_BUF + di], al
    call    suEditValue
.done:
    mov     byte [es:SU_INBOX], 0   ; the screen is the caller's again
    pop     di
    pop     dx
    pop     cx
    pop     bx
    pop     ax
    ret

; suMemSetting -- a setting number to the memory page's own row number.
suMemSetting:
    cmp     al, 19
    jne     .notcount
    mov     al, SU_M_COUNT
    ret
.notcount:
    cmp     al, 20
    jne     .notmode
    mov     al, SU_M_MODE
    ret
.notmode:
    sub     al, 21
    add     al, SU_M_S0
    ret

; suEditValue -- just the number inside the dialog, redrawn on its own.
;
; The box is painted once when the dialog opens; a key only changes the value,
; so only the value is repainted. Repainting the whole panel per keypress is
; what made the title bar tear: the card paints cells it has seen change, and
; the firmware was rewriting them faster than the card could follow.
suEditValue:
    push    ax
    push    bx
    push    cx
    push    dx
    push    si
    mov     dl, 33                  ; the field, cleared so a shorter value
    mov     dh, 13                  ; cannot leave the last one's tail behind
    mov     cx, 20                  ; wide enough for "128 MB, DDR4 SODIMM"
    mov     al, ' '
    mov     bl, SU_SEL
    call    suBar
    mov     dl, 34
    mov     dh, 13
    call    suGoto
    mov     al, [es:SU_ITEM]
    mov     bl, SU_SEL
    call    suValue
    pop     si
    pop     dx
    pop     cx
    pop     bx
    pop     ax
    ret

; suEditBox: the editing dialog on the framed panel
suEditBox:
    push    ax
    push    bx
    push    cx
    push    dx
    push    si
    ; the panel, with the setting's own name on its title bar
    mov     al, [es:SU_ITEM]
    xor     ah, ah
    add     ax, ax
    mov     si, ax
    mov     si, [si + su_labels]
    mov     dl, 24
    mov     dh, 10
    mov     cx, 37                  ; wide enough for an arrow either side of
    call    suDialog                ; twenty columns of value
    ; the value, an arrow either side of it
    mov     dl, 30
    mov     dh, 13
    call    suGoto
    mov     si, msg_su_lt
    mov     bl, SU_SEL
    call    suPrint
    call    suEditValue
    mov     dl, 54
    mov     dh, 13
    call    suGoto
    mov     si, msg_su_gt
    mov     bl, SU_SEL
    call    suPrint
    mov     dl, 26
    mov     dh, 15
    call    suGoto
    mov     si, msg_su_edit_hint
    mov     bl, SU_SEL
    call    suPrint
    pop     si
    pop     dx
    pop     cx
    pop     bx
    pop     ax
    ret

; ---------------------------------------------------------------------------
; suAskExit -- the "you have changed something" prompt. Returns al = 1 to
; save, 0 to throw the changes away, 2 to stay.
; ---------------------------------------------------------------------------
suAskExit:
    push    bx
    push    cx
    push    dx
    push    si
    mov     byte [es:SU_OPT], 0
.draw:
    call    suAskBox
    call    keyScan
    cmp     al, 0x4B                ; left
    je      .left
    cmp     al, 0x4D                ; right
    je      .right
    cmp     al, 0x1C                ; Enter takes the highlighted one
    je      .choose
    cmp     al, 0x01                ; Esc means stay
    je      .stay
    cmp     al, 0x0E
    je      .stay
    jmp     .draw
.left:
    mov     al, [es:SU_OPT]
    test    al, al
    jnz     .leftdec
    mov     al, 3
.leftdec:
    dec     al
    mov     [es:SU_OPT], al
    jmp     .draw
.right:
    mov     al, [es:SU_OPT]
    inc     al
    cmp     al, 3
    jb      .rightset
    xor     al, al
.rightset:
    mov     [es:SU_OPT], al
    jmp     .draw
.choose:
    mov     al, [es:SU_OPT]
    test    al, al
    jz      .save
    cmp     al, 1
    je      .discard
.stay:
    mov     al, 2
    jmp     .out
.save:
    mov     al, 1
    jmp     .out
.discard:
    xor     al, al
.out:
    pop     si
    pop     dx
    pop     cx
    pop     bx
    ret

; suAskBox: the save prompt, on the same framed panel
suAskBox:
    push    ax
    push    bx
    push    cx
    push    dx
    push    si
    mov     si, msg_su_ask_title
    mov     dl, 22
    mov     dh, 10
    mov     cx, 36
    call    suDialog
    ; Save
    mov     dl, 23
    mov     dh, 13
    mov     cx, 11
    mov     al, ' '
    mov     bl, SU_SEL
    cmp     byte [es:SU_OPT], 0
    jne     .s0
    mov     bl, SU_HEAD
.s0:
    call    suBar
    mov     dl, 25
    mov     dh, 13
    call    suGoto
    mov     si, msg_su_yes
    mov     bl, SU_SEL
    cmp     byte [es:SU_OPT], 0
    jne     .s1
    mov     bl, SU_HEAD
.s1:
    call    suPrint
    ; Discard
    mov     dl, 35
    mov     dh, 13
    mov     cx, 11
    mov     al, ' '
    mov     bl, SU_SEL
    cmp     byte [es:SU_OPT], 1
    jne     .d0
    mov     bl, SU_HEAD
.d0:
    call    suBar
    mov     dl, 36
    mov     dh, 13
    call    suGoto
    mov     si, msg_su_no
    mov     bl, SU_SEL
    cmp     byte [es:SU_OPT], 1
    jne     .d1
    mov     bl, SU_HEAD
.d1:
    call    suPrint
    ; Cancel
    mov     dl, 47
    mov     dh, 13
    mov     cx, 10
    mov     al, ' '
    mov     bl, SU_SEL
    cmp     byte [es:SU_OPT], 2
    jne     .c0
    mov     bl, SU_HEAD
.c0:
    call    suBar
    mov     dl, 48
    mov     dh, 13
    call    suGoto
    mov     si, msg_su_cancel
    mov     bl, SU_SEL
    cmp     byte [es:SU_OPT], 2
    jne     .c1
    mov     bl, SU_HEAD
.c1:
    call    suPrint
    mov     dl, 24
    mov     dh, 15
    call    suGoto
    mov     si, msg_su_ask_hint
    mov     bl, SU_SEL
    call    suPrint
    pop     si
    pop     dx
    pop     cx
    pop     bx
    pop     ax
    ret

; tabItems: al = page -> bl = how many selectable rows it has
tabItems:
    push    ax
    xor     bl, bl
    test    al, al
    jnz     .notmain
    mov     bl, SU_N
    jmp     .out
.notmain:
    cmp     al, 3
    jne     .notmem
    mov     bl, SU_MN
    jmp     .out
.notmem:
    cmp     al, 2
    jne     .out
    mov     bl, 2
.out:
    pop     ax
    ret

; drawSetup: the whole screen for the current page
drawSetup:
    push    ax
    push    bx
    push    cx
    push    dx
    push    si
    call    suClearBody
    call    suFrame
    ; row 0 is the title bar, above the frame
    xor     dl, dl
    xor     dh, dh
    mov     cx, 80
    mov     al, ' '
    mov     bl, SU_PAPER
    call    suBar
    mov     dl, 1
    xor     dh, dh
    call    suGoto
    mov     si, msg_su_title
    mov     bl, SU_HEAD
    call    suPrint
    mov     dl, 74
    xor     dh, dh
    call    suGoto
    mov     si, msg_su_version
    mov     bl, SU_HEAD
    call    suPrint
    call    drawTabs
    ; the page
    mov     al, [es:SU_TAB]
    test    al, al
    jz      .mainpage
    cmp     al, 1
    je      .infopage
    cmp     al, 3
    je      .mempage
    call    pageExit
    jmp     .done
.mempage:
    call    pageMemory
    jmp     .done
.mainpage:
    call    pageMain
    jmp     .done
.infopage:
    call    pageInfo
.done:
    call    suStatus
    pop     si
    pop     dx
    pop     cx
    pop     bx
    pop     ax
    ret

; drawTabs: the menu bar on its own row, closed off by the rule underneath.
; The page you are on is lit; the others are plain text, and the space either
; side of a lit one goes with it so it reads as a key rather than a word in
; the middle of the line.
drawTabs:
    push    ax
    push    bx
    push    cx
    push    dx
    push    si
    push    di
    xor     di, di                  ; which tab is being drawn
    mov     dl, 2
.tab:
    ; the role of the band decides the ink for both halves of the tab
    mov     bl, SU_PAPER
    mov     al, [es:SU_TAB]
    cmp     di, ax
    jne     .ink
    mov     bl, SU_SEL
.ink:
    push    bx
    mov     dh, SU_TABS
    mov     cx, 8
    mov     al, ' '
    call    suBar
    mov     dh, SU_TABS
    call    suGoto
    pop     bx
    add     di, di
    mov     si, [cs:tab_names + di]
    shr     di, 1
    call    suPrint
    inc     di
    add     dl, 10
    cmp     dl, 42
    jb      .tab
    pop     di
    pop     si
    pop     dx
    pop     cx
    pop     bx
    pop     ax
    ret

tab_names:
    dw msg_tab_main, msg_tab_info, msg_tab_memory, msg_tab_exit

; pageMemory -- the RAM.
;
; A real board's setup has a memory page because the memory is modules: how many
; sockets, what is in each, and whether the total is what you paid for. The
; rows mirror that, and eight of them are one socket each.
;
; Like every other page this one writes nothing: the CMOS is the board's memory
; and it changes on Save. What Save does with these rows is the interesting
; part -- it stores the modules *and* the total they add up to, and the chipset
; reads that total at power-on to decide how much extended memory exists, which
; is what E820 then reports to a kernel.
; ---------------------------------------------------------------------------
pageMemory:
    push    ax
    push    bx
    push    cx
    push    dx
    push    si
    mov     dl, 2
    mov     dh, SU_ROW0 - 1
    call    suGoto
    mov     si, msg_su_memory
    mov     bl, SU_HEAD
    call    suPrint
    xor     al, al
.paint:
    push    ax
    call    suDrawRow
    pop     ax
    inc     al
    cmp     al, SU_MN
    jb      .paint
    mov     al, [es:SU_ITEM]
    call    suMemoryHint
    pop     si
    pop     dx
    pop     cx
    pop     bx
    pop     ax
    ret

suMemoryHint:
    push    ax
    push    bx
    push    dx
    push    si
    mov     dl, SU_SPLIT
    mov     dh, SU_ROW0 - 1
    call    suGoto
    mov     si, msg_su_memhlp
    mov     bl, SU_HEAD
    call    suPrint
    mov     dl, SU_SPLIT
    mov     dh, SU_ROW0
    call    suGoto
    xor     ah, ah
    add     ax, ax
    mov     si, ax
    mov     si, [cs:su_mem_help + si]
    mov     bl, SU_PAPER
    call    suPrint
    mov     dl, SU_SPLIT
    mov     dh, SU_ROW0 + 2
    call    suGoto
    mov     al, [es:SU_ITEM]
    xor     ah, ah
    add     ax, ax
    mov     si, ax
    mov     si, [cs:su_mem_help2 + si]
    mov     bl, SU_PAPER
    call    suPrint
    pop     si
    pop     dx
    pop     bx
    pop     ax
    ret

; suAutoAlloc -- fill the sockets with the largest modules that fit.
;
; "Auto" means what it means on a real board: take the total you asked for, and
; populate sockets with the biggest module each can hold until it is used up.
; Four sockets of 128 MB is half a gigabyte; 96 MB gives one 64 and one 32, in
; that order, and leaves the rest empty.
;
; al = how many sockets are fitted, cx = megabytes wanted
suAutoAlloc:
    push    ax
    push    bx
    push    cx
    push    dx
    push    si
    push    di
    mov     [es:SU_AUTO_N], al
    mov     [es:SU_AUTO_MB], cl
    mov     [es:SU_AUTO_MB + 1], ch
    xor     di, di
.blank:
    mov     byte [es:SU_BUF + SU_M_SIZE + di], 0
    inc     di
    cmp     di, 8
    jb      .blank
    xor     di, di                  ; di = the socket being filled
.fill:
    mov     al, [es:SU_AUTO_N]
    xor     ah, ah
    cmp     di, ax
    jae     .total
    mov     cx, [es:SU_AUTO_MB]
    or      ch, ch
    jnz     .have
    or      cl, cl
    jz      .total                  ; nothing left to give
.have:
    ; the largest entry of the size table that fits what is left
    mov     bl, 7                   ; index: 1 MB, 2, 4 ... 128
.try:
    mov     al, bl
    xor     ah, ah
    add     ax, ax
    mov     si, ax
    mov     si, [cs:su_ramsizes + si]   ; a word: the megabytes it holds
    mov     cx, [es:SU_AUTO_MB]
    cmp     si, cx
    jbe     .fits
    dec     bl
    jnz     .try
    jmp     .total                  ; under a megabyte: nothing sensible fits
.fits:
    mov     si, di
    add     si, SU_BUF + SU_M_SIZE
    mov     [es:si], bl             ; the size index, which is what CMOS wants
    ; and take it off the total
    mov     al, bl
    xor     ah, ah
    add     ax, ax
    mov     si, ax
    mov     si, [cs:su_ramsizes + si]
    mov     cx, [es:SU_AUTO_MB]
    sub     cx, si
    mov     [es:SU_AUTO_MB], cl
    mov     [es:SU_AUTO_MB + 1], ch
    inc     di
    jmp     .fill
.total:
    call    suMemTotal
    mov     di, [es:SU_AUTO_N]
.blank2:
    cmp     di, 8
    jae     .done
    mov     byte [es:SU_BUF + SU_M_SIZE + di], 0
    inc     di
    jmp     .blank2
.done:
    pop     di
    pop     si
    pop     dx
    pop     cx
    pop     bx
    pop     ax
    ret

; suMemTotal -- add up the fitted modules into SU_BUF + SU_M_TOTAL, in megabytes
suMemTotal:
    push    ax
    push    bx
    push    cx
    push    dx
    push    si
    mov     cx, 0
    xor     bx, bx
.sum:
    mov     al, [es:SU_BUF + SU_M_COUNT]
    xor     ah, ah
    cmp     bx, ax
    jae     .store
    mov     si, bx
    add     si, SU_BUF + SU_M_SIZE
    mov     al, [es:si]
    xor     ah, ah
    add     ax, ax
    mov     si, ax
    mov     si, [cs:su_ramsizes + si]
    add     cx, si
    inc     bx
    jmp     .sum
.store:
    cmp     cx, 255
    jbe     .fits
    mov     cx, 255                 ; the CMOS byte is the limit, not the idea
.fits:
    mov     [es:SU_BUF + SU_M_TOTAL], cl
    pop     si
    pop     dx
    pop     cx
    pop     bx
    pop     ax
    ret

; pageMain: the settings, with the detail panel beside them
pageMain:
    push    ax
    push    si
    mov     dl, 2
    mov     dh, SU_ROW0 - 1
    call    suGoto
    mov     si, msg_su_settings
    mov     bl, SU_HEAD
    call    suPrint
    xor     al, al
.rows:
    cmp     al, SU_N
    jae     .panel
    push    ax
    call    suDrawRow
    pop     ax
    inc     al
    jmp     .rows
.panel:
    call    suPanel
    pop     si
    pop     ax
    ret

; pageInfo: the facts a setup utility shows and never edits
pageInfo:
    push    ax
    push    si
    mov     dl, 2
    mov     dh, SU_ROW0 - 1
    call    suGoto
    mov     si, msg_su_info
    mov     bl, SU_HEAD
    call    suPrint
    mov     dl, 2
    mov     dh, SU_ROW0
    call    suGoto
    mov     si, msg_su_biosline
    mov     bl, SU_PAPER
    call    suPrint
    mov     dl, 2
    mov     dh, SU_ROW0 + 1
    call    suGoto
    mov     si, msg_su_cpuline
    mov     bl, SU_PAPER
    call    suPrint
    ; the clock, ticking
    mov     dl, 2
    mov     dh, SU_ROW0 + 2
    call    suGoto
    mov     si, msg_su_clock
    mov     bl, SU_PAPER
    call    suPrint
    call    suClock
    ; memory and cores, from the hardware
    mov     dl, 2
    mov     dh, SU_ROW0 + 3
    call    suGoto
    mov     si, msg_su_mem
    mov     bl, SU_PAPER
    call    suPrint
    mov     al, 0x15
    call    cmos_read
    mov     bl, al
    mov     al, 0x16
    call    cmos_read
    mov     bh, al
    mov     ax, bx
    mov     bl, SU_PAPER
    call    put3
    mov     si, msg_su_kb
    call    suPrint
    mov     dl, 2
    mov     dh, SU_ROW0 + 4
    call    suGoto
    mov     si, msg_su_cores
    mov     bl, SU_PAPER
    call    suPrint
    mov     dx, 0x5A0
    in      al, dx
    xor     ah, ah
    mov     bl, SU_PAPER
    call    put2
    ; the disk, and how to get back
    mov     dl, 2
    mov     dh, SU_ROW0 + 5
    call    suGoto
    mov     si, msg_su_diskline
    mov     bl, SU_PAPER
    call    suPrint
    ; the chips, and the tick counter they drive -- the number climbing here
    ; is the proof that the 8254 is counting and int 08h is running
    mov     dl, 2
    mov     dh, SU_ROW0 + 6
    call    suGoto
    mov     si, msg_su_timer
    mov     bl, SU_PAPER
    call    suPrint
    mov     dl, 2
    mov     dh, SU_ROW0 + 7
    call    suGoto
    mov     si, msg_su_ticks
    mov     bl, SU_PAPER
    call    suPrint
    push    ds
    xor     ax, ax
    mov     ds, ax
    mov     ax, [ds:BDA_TICKS]
    pop     ds
    mov     bl, SU_PAPER
    call    put3
    mov     dl, SU_RX
    mov     dh, SU_ROW0 + 1
    call    suGoto
    mov     si, msg_su_tabhelp
    mov     bl, SU_HEAD
    call    suPrint
    mov     dl, SU_RX
    mov     dh, SU_ROW0 + 3
    call    suGoto
    mov     si, msg_su_ramline
    mov     bl, SU_PAPER
    call    suPrint
    mov     dl, SU_RX
    mov     dh, SU_ROW0 + 4
    call    suGoto
    mov     si, msg_su_romline
    mov     bl, SU_PAPER
    call    suPrint
    mov     dl, SU_RX
    mov     dh, SU_ROW0 + 5
    call    suGoto
    mov     si, msg_su_videoline
    mov     bl, SU_PAPER
    call    suPrint
    pop     si
    pop     ax
    ret

; pageExit: the two ways out
pageExit:
    push    ax
    push    bx
    push    cx
    push    dx
    push    si
    mov     dl, 2
    mov     dh, SU_ROW0 - 1
    call    suGoto
    mov     si, msg_su_exithead
    mov     bl, SU_HEAD
    call    suPrint
    ; "Save and exit"
    mov     dl, 1
    mov     dh, SU_ROW0
    mov     cx, 43
    mov     al, ' '
    mov     bl, SU_PAPER
    cmp     byte [es:SU_ITEM], 0
    jne     .r0
    mov     bl, SU_SEL
.r0:
    call    suBar
    mov     dl, 3
    mov     dh, SU_ROW0
    call    suGoto
    mov     si, msg_su_save
    mov     bl, SU_PAPER
    cmp     byte [es:SU_ITEM], 0
    jne     .r1
    mov     bl, SU_SEL
.r1:
    call    suPrint
    ; "Discard and exit"
    mov     dl, 1
    mov     dh, SU_ROW0 + 1
    mov     cx, 43
    mov     al, ' '
    mov     bl, SU_PAPER
    cmp     byte [es:SU_ITEM], 1
    jne     .r2
    mov     bl, SU_SEL
.r2:
    call    suBar
    mov     dl, 3
    mov     dh, SU_ROW0 + 1
    call    suGoto
    mov     si, msg_su_discard
    mov     bl, SU_PAPER
    cmp     byte [es:SU_ITEM], 1
    jne     .r3
    mov     bl, SU_SEL
.r3:
    call    suPrint
    ; what each one does
    mov     dl, SU_RX
    mov     dh, SU_ROW0
    call    suGoto
    mov     si, msg_su_savehelp
    mov     bl, SU_PAPER
    call    suPrint
    mov     dl, SU_RX
    mov     dh, SU_ROW0 + 2
    call    suGoto
    mov     si, msg_su_discardhelp
    mov     bl, SU_PAPER
    call    suPrint
    pop     si
    pop     dx
    pop     cx
    pop     bx
    pop     ax
    ret

suFrame:
    push    ax
    push    bx
    push    cx
    push    dx
    push    si
    ; three horizontal rules: the top border, the line under the menu bar, and
    ; the bottom border
    mov     dh, SU_TOP
    call    frameRule
    mov     dh, SU_SEP
    call    frameRule
    mov     dh, SU_BOT
    call    frameRule
    ; the corners and the junctions where the column rule crosses them
    xor     dl, dl
    mov     dh, SU_TOP
    mov     al, G_TL
    call    suCell
    mov     dl, 79
    mov     dh, SU_TOP
    mov     al, G_TR
    call    suCell
    mov     dl, SU_SPLIT
    mov     dh, SU_TOP
    mov     al, G_TJ
    call    suCell
    xor     dl, dl
    mov     dh, SU_SEP
    mov     al, G_ML
    call    suCell
    mov     dl, 79
    mov     dh, SU_SEP
    mov     al, G_MR
    call    suCell
    mov     dl, SU_SPLIT
    mov     dh, SU_SEP
    mov     al, G_MJ
    call    suCell
    xor     dl, dl
    mov     dh, SU_BOT
    mov     al, G_BL
    call    suCell
    mov     dl, 79
    mov     dh, SU_BOT
    mov     al, G_BR
    call    suCell
    mov     dl, SU_SPLIT
    mov     dh, SU_BOT
    mov     al, G_BJ
    call    suCell
    ; the verticals: the two sides and the column rule, from under the top
    ; border to the bottom one
    mov     dh, SU_TOP + 1
.side:
    xor     dl, dl
    mov     al, G_VBAR
    call    suCell
    mov     dl, 79
    mov     al, G_VBAR
    call    suCell
    mov     dl, SU_SPLIT
    mov     al, G_VBAR
    call    suCell
    inc     dh
    cmp     dh, SU_BOT
    jb      .side
    pop     si
    pop     dx
    pop     cx
    pop     bx
    pop     ax
    ret

; frameRule: dh = the row, a full-width double rule
frameRule:
    push    ax
    push    bx
    push    cx
    push    dx
    xor     dl, dl
    mov     cx, 80
    mov     al, G_HBAR
    mov     bl, SU_HEAD
    call    suBar
    pop     dx
    pop     cx
    pop     bx
    pop     ax
    ret

; suPoke: dl/dh = column/row, al = the character, bl = the attribute. It writes
; straight into the text plane, so the console cursor never moves -- see the
; note in suStatus about the last cell of the last row.
suPoke:
    push    ax
    push    bx
    push    cx
    push    dx
    push    di
    push    fs
    push    ax                      ; the character, out of the way
    mov     al, dh
    xor     ah, ah
    mov     cl, COLS
    mul     cl                      ; ax = row * 80
    xor     dh, dh
    add     ax, dx                  ; ... and the column
    shl     ax, 1
    mov     di, ax
    pop     ax                      ; the character is back in al
    mov     cx, TEXT_SEG
    mov     fs, cx
    mov     ah, bl
    mov     [fs:di], ax
    pop     fs
    pop     di
    pop     dx
    pop     cx
    pop     bx
    pop     ax
    ret

; suPlot: dl/dh = column/row, al = the character, bl = the attribute. Unlike
; suCell the ink is the caller's -- a dialog's border is grey, not the screen
; frame's yellow.
suPlot:
    push    ax
    push    cx
    call    suGoto
    mov     cx, 1
    call    suFill
    pop     cx
    pop     ax
    ret

; suCell: dl/dh = column/row, al = the character. The frame is always drawn in
; the header ink, so this sets it rather than trusting whatever bl held.
suCell:
    push    ax
    push    cx
    mov     bl, SU_HEAD
    call    suGoto
    mov     cx, 1
    call    suFill
    pop     cx
    pop     ax
    ret

; paint the inside of the frame in the paper colour
suClearBody:
    push    ax
    push    bx
    push    cx
    push    dx
    mov     dh, SU_TOP + 1
.row:
    mov     dl, 1
    mov     cx, 78
    mov     al, ' '
    mov     bl, SU_PAPER
    call    suBar
    inc     dh
    cmp     dh, SU_BOT
    jb      .row
    pop     dx
    pop     cx
    pop     bx
    pop     ax
    ret

; suStatus: the key hints on the last line inside the frame, and the battery
; and boot count on a lit strip at the very bottom
suStatus:
    push    ax
    push    bx
    push    cx
    push    dx
    push    si
    mov     dl, 2
    mov     dh, SU_BOT - 1
    call    suGoto
    mov     si, msg_su_keys
    mov     bl, SU_HEAD
    call    suPrint
    ; A lit strip across the row after the frame, all eighty cells. The last
    ; one goes straight into the plane: the console advances its cursor after a
    ; character, and a cursor that steps past the last column of the last row
    ; makes the whole screen scroll up a line.
    mov     dl, 79
    mov     dh, SU_BOT + 1
    mov     al, ' '
    mov     bl, SU_SEL
    call    suPoke
    xor     dl, dl
    mov     dh, SU_BOT + 1
    mov     cx, 79
    mov     al, ' '
    mov     bl, SU_SEL
    call    suBar
    mov     dl, 2
    mov     dh, SU_BOT + 1
    call    suGoto
    mov     si, msg_su_battery
    mov     bl, SU_SEL
    call    suPrint
    mov     al, 0x0D
    call    cmos_read
    test    al, 0x80                ; bit 7 clear = the battery is dead
    jz      .dead
    mov     si, msg_su_ok
    jmp     .batt
.dead:
    mov     si, msg_su_dead
.batt:
    mov     bl, SU_SEL
    call    suPrint
    mov     dl, 42
    mov     dh, SU_BOT + 1
    call    suGoto
    mov     si, msg_su_boots
    mov     bl, SU_SEL
    call    suPrint
    ; the digits go through putc, which reads the console attribute, so it has
    ; to be the strip's ink for as long as they print
    mov     byte [es:CON_ATTR], SU_SEL
    mov     al, 0x30
    call    cmos_read
    call    put2
    mov     byte [es:CON_ATTR], SU_PAPER
    pop     si
    pop     dx
    pop     cx
    pop     bx
    pop     ax
    ret

; suPanel: the detail panel for the selected item, and the system summary
suPanel:
    push    ax
    push    bx
    push    cx
    push    dx
    push    si
    push    di
    ; blank the panel's columns so a redraw starts clean
    mov     dh, SU_TOP + 1
.blank:
    mov     dl, SU_RX
    mov     cx, 32
    mov     al, ' '
    mov     bl, SU_PAPER
    call    suBar
    inc     dh
    cmp     dh, SU_BOT
    jb      .blank
    call    suPanelTop
    call    suPanelFacts
    pop     di
    pop     si
    pop     dx
    pop     cx
    pop     bx
    pop     ax
    ret

; suPanelTop: the name of the selected row and what it does. This is the part
; that changes when the arrow keys move, so it is kept apart from the facts
; below it -- and it only clears its own five rows
suPanelTop:
    push    ax
    push    bx
    push    cx
    push    dx
    push    si
    push    di
    mov     dh, SU_ROW0 - 1
.blank:
    mov     dl, SU_RX
    mov     cx, 32
    mov     al, ' '
    mov     bl, SU_PAPER
    call    suBar
    inc     dh
    cmp     dh, SU_ROW0 + 3
    jb      .blank
    ; the item's name
    mov     al, [es:SU_ITEM]
    xor     ah, ah
    add     ax, ax
    mov     di, ax
    mov     dl, SU_RX
    mov     dh, SU_ROW0 - 1
    call    suGoto
    mov     si, [di + su_labels]
    mov     bl, SU_HEAD
    call    suPrint
    ; its two lines of help
    mov     dl, SU_RX
    mov     dh, SU_ROW0
    call    suGoto
    mov     si, [di + su_d1]
    mov     bl, SU_PAPER
    call    suPrint
    mov     dl, SU_RX
    mov     dh, SU_ROW0 + 1
    call    suGoto
    mov     si, [di + su_d2]
    mov     bl, SU_PAPER
    call    suPrint
    ; how to change it
    mov     dl, SU_RX
    mov     dh, SU_ROW0 + 3
    call    suGoto
    mov     si, msg_su_hint
    mov     bl, SU_HEAD
    call    suPrint
    pop     di
    pop     si
    pop     dx
    pop     cx
    pop     bx
    pop     ax
    ret

; suPanelFacts: the system summary -- what a setup screen always shows and
; never edits
suPanelFacts:
    push    ax
    push    bx
    push    cx
    push    dx
    push    si
    mov     dl, SU_RX
    mov     dh, SU_ROW0 + 5
    call    suGoto
    mov     si, msg_su_system
    mov     bl, SU_HEAD
    call    suPrint
    mov     dl, SU_RX
    mov     dh, SU_ROW0 + 6
    call    suGoto
    mov     si, msg_su_cpu
    mov     bl, SU_PAPER
    call    suPrint
    ; memory, in KiB out of the CMOS
    mov     dl, SU_RX
    mov     dh, SU_ROW0 + 7
    call    suGoto
    mov     si, msg_su_mem
    mov     bl, SU_PAPER
    call    suPrint
    mov     al, 0x15
    call    cmos_read
    mov     bl, al
    mov     al, 0x16
    call    cmos_read
    mov     bh, al
    mov     ax, bx                  ; ax = base memory in KiB
    mov     bl, SU_PAPER
    call    put3
    mov     si, msg_su_kb
    call    suPrint
    ; cores, from the chipset
    mov     dl, SU_RX
    mov     dh, SU_ROW0 + 8
    call    suGoto
    mov     si, msg_su_cores
    mov     bl, SU_PAPER
    call    suPrint
    mov     dx, 0x5A0
    in      al, dx
    xor     ah, ah
    mov     bl, SU_PAPER
    call    put2
    ; the clock, straight out of the RTC
    mov     dl, SU_RX
    mov     dh, SU_ROW0 + 9
    call    suGoto
    mov     si, msg_su_clock
    mov     bl, SU_PAPER
    call    suPrint
    call    suClock
    pop     si
    pop     dx
    pop     cx
    pop     bx
    pop     ax
    ret

; suClock: hh:mm:ss from the CMOS, right after the caller's text
suClock:
    push    ax
    push    bx
    push    cx
    push    dx
    mov     al, 4
    call    cmos_read
    call    bcd2bin
    mov     bh, al
    mov     al, 2
    call    cmos_read
    call    bcd2bin
    mov     bl, al
    mov     al, 0
    call    cmos_read
    call    bcd2bin
    mov     cl, al
    mov     al, bh
    call    put2
    mov     al, ':'
    call    putc
    mov     al, bl
    call    put2
    mov     al, ':'
    call    putc
    mov     al, cl
    call    put2
    pop     dx
    pop     cx
    pop     bx
    pop     ax
    ret

; suDrawRow: al = the item to draw
suDrawRow:
    push    ax
    push    bx
    push    cx
    push    dx
    push    si
    push    di
    mov     [es:SU_ROWSAVE], al
    ; the row's band, inside the frame, in the selected or the normal colour
    mov     bl, SU_PAPER
    cmp     al, [es:SU_ITEM]
    jne     .band
    mov     bl, SU_SEL
.band:
    mov     dl, 1
    mov     dh, al
    add     dh, SU_ROW0
    mov     cx, SU_SPLIT - 1
    mov     al, ' '
    call    suBar
    ; the label
    mov     al, [es:SU_ROWSAVE]
    mov     bl, SU_PAPER
    cmp     al, [es:SU_ITEM]
    jne     .labour
    mov     bl, SU_SEL
.labour:
    mov     dl, 2
    mov     dh, al
    add     dh, SU_ROW0
    call    suGoto
    xor     ah, ah
    add     ax, ax
    mov     di, ax
    mov     si, [di + su_labels]
    mov     al, [es:SU_ROWSAVE]
    mov     bl, SU_PAPER
    cmp     al, [es:SU_ITEM]
    jne     .printl
    mov     bl, SU_SEL
.printl:
    call    suPrint
    ; the value
    mov     dl, SU_VAL
    mov     al, [es:SU_ROWSAVE]
    mov     dh, al
    add     dh, SU_ROW0
    call    suGoto
    mov     al, [es:SU_ROWSAVE]
    mov     bl, SU_PAPER
    cmp     al, [es:SU_ITEM]
    jne     .value
    mov     bl, SU_SEL
.value:
    cmp     byte [es:SU_TAB], 3
    je      .memval
    call    suValue
    jmp     .valued
.memval:
    call    suMemValue
.valued:
    pop     di
    pop     si
    pop     dx
    pop     cx
    pop     bx
    pop     ax
    ret

; ---------------------------------------------------------------------------
; suValue: print row al's value. Every number is two digits or four, and the
; three "type" rows print a name out of a table.
; ---------------------------------------------------------------------------
; suMemValue -- print the value of memory row al into bl's ink.
suMemValue:
    push    ax
    push    bx
    push    cx
    push    dx
    push    si
    push    di
    mov     [es:SU_INK], bl
    xor     ah, ah
    mov     si, ax
    cmp     si, SU_M_COUNT
    je      .count
    cmp     si, SU_M_MODE
    je      .mode
    cmp     si, SU_M_AUTO
    je      .auto
    jmp     .slot
.count:
    mov     al, [es:SU_BUF + SU_M_COUNT]
    xor     ah, ah
    mov     si, ax
    mov     si, [cs:su_slotcount + si]
    jmp     .print
.mode:
    mov     al, [es:SU_BUF + SU_M_MODE]
    xor     ah, ah
    add     ax, ax
    mov     si, ax
    mov     si, [cs:su_mem_mode + si]
    jmp     .print
.auto:
    mov     si, msg_su_autoval
    jmp     .print
.slot:
    sub     si, SU_M_S0
    mov     di, si                  ; the socket number
    ; its size, as a name
    mov     si, di
    add     si, SU_BUF + SU_M_SIZE
    mov     al, [es:si]
    xor     ah, ah
    add     ax, ax
    mov     si, ax
    mov     si, [cs:su_ramsize_names + si]
    mov     bl, [es:SU_INK]
    call    suPrint
    ; and what is in it: a generation and a form
    mov     si, di
    add     si, SU_BUF + SU_M_SIZE
    cmp     byte [es:si], 0
    je      .done                   ; an empty socket has no kind
    mov     si, msg_su_comma
    mov     bl, [es:SU_INK]
    call    suPrint
    mov     si, di
    add     si, SU_BUF + SU_M_KIND
    mov     al, [es:si]
    xor     ah, ah
    add     ax, ax
    mov     si, ax
    mov     si, [cs:su_kdnames + si]
    mov     bl, [es:SU_INK]
    call    suPrint
    mov     si, di
    add     si, SU_BUF + SU_M_FORM
    mov     al, [es:si]
    xor     ah, ah
    add     ax, ax
    mov     si, ax
    mov     si, [cs:su_formnames + si]
    mov     bl, [es:SU_INK]
    call    suPrint
.done:
    jmp     .out
.print:
    mov     bl, [es:SU_INK]
    call    suPrint
.out:
    pop     di
    pop     si
    pop     dx
    pop     cx
    pop     bx
    pop     ax
    ret

suValue:
    push    ax
    push    bx
    push    cx
    push    dx
    push    si
    push    di
    mov     [es:SU_INK], bl         ; the row's ink, before bl becomes the row
    mov     ah, al                  ; ah = the row
    xor     bh, bh
    mov     bl, al
    mov     si, bx                  ; si = the row, for the buffer and the tables
    mov     al, [es:SU_BUF + si]    ; the value, 0..99
    cmp     ah, 19
    jb      .known
    cmp     ah, 28
    ja      .done                   ; no such setting
    ; the memory panel's rows are drawn by the page that owns them, so the
    ; dialog borrows that painter rather than growing a second copy of it
    mov     al, ah
    call    suMemSetting            ; setting -> the page's own row number
    mov     bl, [es:SU_INK]
    call    suMemValue
    jmp     .done
.known:
.known:
    cmp     ah, 15
    jae     .hardware               ; the emulation's own four knobs
    cmp     ah, 11
    jae     .named                  ; the emulation rows are named values
    cmp     ah, 10
    je      .cores
    cmp     ah, 9
    ja      .done                   ; no such row
    je      .memory
    cmp     ah, 6
    jae     .named
    ; a number. Rows 0/1 are month/day, 2 the year, 3-5 the clock
    cmp     ah, 2
    jne     .two
    mov     si, msg_y2k      ; the year prints as 20xx
    mov     bl, [es:SU_INK]
    call    suPrint
    mov     al, [es:SU_BUF + 2]
.two:
    mov     bl, [es:SU_INK]
    call    put2
    jmp     .done
.hardware:
    ; rows 15..18 each read their name out of their own table, so the value the
    ; dialog shows is a word ("4", "2x", "100%"), not a raw index
    mov     al, [es:SU_BUF + si]
    xor     ah, ah
    add     ax, ax                  ; the value, as a word index
    mov     di, ax
    mov     al, [es:SU_ITEM]
    sub     al, 15
    xor     ah, ah
    add     ax, ax
    mov     si, ax
    mov     bx, [si + su_hwtabs]    ; si+di is not an 8086 address, bx+di is
    mov     si, [di + bx]
    mov     bl, [es:SU_INK]
    call    suPrint
    jmp     .done
.named:
    mov     al, [es:SU_BUF + si]
    xor     ah, ah
    add     ax, ax
    mov     di, ax
    cmp     si, 11
    jne     .not_emu
    mov     si, [di + su_onoff]
    mov     bl, [es:SU_INK]
    call    suPrint
    jmp     .done
.not_emu:
    cmp     si, 12
    jne     .not_batt
    mov     si, [di + su_onoff]
    mov     bl, [es:SU_INK]
    call    suPrint
    jmp     .done
.not_batt:
    cmp     si, 13
    jne     .not_freq
    mov     si, [di + su_freq]
    mov     bl, [es:SU_INK]
    call    suPrint
    jmp     .done
.not_freq:
    cmp     si, 14
    jne     .not_repeat
    mov     si, [di + su_krepeat]
    mov     bl, [es:SU_INK]
    call    suPrint
    jmp     .done
.not_repeat:
    cmp     si, 6
    jne     .not_flop
    mov     si, [di + su_floppy]
    mov     bl, [es:SU_INK]
    call    suPrint
    jmp     .done
.not_flop:
    cmp     si, 7
    jne     .not_disk
    mov     si, [di + su_disk]
    mov     bl, [es:SU_INK]
    call    suPrint
    jmp     .done
.not_disk:
    cmp     si, 8
    jne     .bootrow
    mov     si, [di + su_boot]
    mov     bl, [es:SU_INK]
    call    suPrint
    jmp     .done
.bootrow:
    mov     si, [di + su_boot]
    mov     bl, [es:SU_INK]
    call    suPrint
    jmp     .done
.cores:
    ; the chipset owns this one: a core count is not something a setup screen
    ; gets to invent
    mov     dx, 0x5A0
    in      al, dx
    xor     ah, ah
    mov     bl, [es:SU_INK]
    call    put2
    jmp     .done
.memory:
    ; the buffer holds 64 KiB steps; the screen wants KiB, which needs three
    ; digits, so this is a sixteen-bit multiply and two divisions
    mov     al, [es:SU_BUF + 9]
    xor     ah, ah
    mov     cl, 6
    shl     ax, cl
    mov     bl, [es:SU_INK]
    call    put3
    mov     si, msg_su_kb
    mov     bl, [es:SU_INK]
    call    suPrint
.done:
    pop     di
    pop     si
    pop     dx
    pop     cx
    pop     bx
    pop     ax
    ret

; put2: al = 0..99, printed as two digits
put2:
    push    ax
    push    bx
    push    cx
    xor     ah, ah
    mov     bl, 10
    div     bl
    mov     cl, ah
    call    .digit
    mov     al, cl
    call    .digit
    pop     cx
    pop     bx
    pop     ax
    ret
.digit:
    add     al, '0'
    jmp     putc

; put3: ax = 0..999, printed as three digits
put3:
    push    ax
    push    bx
    push    cx
    push    dx
    mov     bx, 100
    xor     dx, dx
    div     bx
    mov     cl, dl                  ; the last two digits
    xor     ch, ch
    call    .digit
    mov     ax, cx
    xor     ah, ah
    mov     bl, 10
    div     bl
    mov     cl, ah
    xor     ch, ch
    call    .digit
    mov     ax, cx
    xor     ah, ah
    call    .digit
    pop     dx
    pop     cx
    pop     bx
    pop     ax
    ret
.digit:
    ; through putc, not straight to int 10h: the teletype takes its attribute
    ; from bl, and bl is 100 or 10 here, so the digit would land in whatever
    ; colour the last divide left behind
    add     al, '0'
    jmp     putc

; keyScan: wait for one make code, skipping the 0xE0 of an extended key
keyScan:
    in      al, 0x64
    test    al, 1
    jz      keyScan
    in      al, 0x60
    cmp     al, 0xE0
    je      keyScan
    cmp     al, 0xE1
    je      keyScan
    ret

; ---------------------------------------------------------------------------
; the strings
; ---------------------------------------------------------------------------
msg_press_del:  db "Press DEL for setup, M for options (", 0
msg_tick:       db "...", 0
msg_tick_last:  db "..", 0
; no CRLF on the close: the window is one row, so a newline would scroll the
; line away at the exact moment the user has to read it
msg_tick_end:   db ")", 0
msg_su_title:   db "JSW BIOS Setup Utility", 0
msg_su_version: db "v0.4", 0
msg_su_settings: db "Settings", 0
msg_su_system:  db "System", 0
msg_su_hint:    db "Enter edits it", 0
msg_su_cpu:     db "CPU: 80386 real mode", 0
msg_su_mem:     db "Memory: ", 0
msg_su_cores:   db "Cores: ", 0
msg_su_clock:   db "Clock: ", 0
msg_su_keys:    db "^/v select   </> page   Enter edit   Esc exit", 0

; the pages, on the top border
msg_tab_memory: db "Memory", 0
msg_tab_main:   db "Main", 0
msg_tab_info:   db "Info", 0
msg_tab_exit:   db "Exit", 0

; the information page: what the machine is, never editable
msg_su_info:    db "System information", 0
msg_su_biosline: db "BIOS: JSW BIOS v0.4", 0
msg_su_cpuline: db "CPU: Intel 80386, real mode", 0
msg_su_diskline: db "Disk: int 13h, one boot sector", 0
msg_su_ramline: db "RAM: 640 KB below 1 MB", 0
msg_su_romline: db "ROM: 64 KB at F000:0000", 0
msg_su_videoline: db "Video: VGA text, 80x30", 0
msg_su_timer:   db "Chipset: 8254 timer, 8259 PIC", 0
msg_su_ticks:   db "Ticks: ", 0
msg_su_tabhelp: db "< > switches pages", 0

; the exit page
msg_su_exithead: db "Exit", 0
msg_su_memory:  db "Memory", 0
msg_su_memhlp:  db "Memory Configuration", 0

msg_su_autoval: db "Auto", 0
msg_su_comma:   db ", ", 0

su_sc_0: db "1", 0
su_sc_1: db "2", 0
su_sc_2: db "3", 0
su_sc_3: db "4", 0
su_sc_4: db "5", 0
su_sc_5: db "6", 0
su_sc_6: db "7", 0
su_sc_7: db "8", 0
su_slotcount:
    dw su_sc_0, su_sc_1, su_sc_2, su_sc_3, su_sc_4, su_sc_5, su_sc_6, su_sc_7

su_h_mem_count: db "How many sockets this board has, 1 to 8.", 0
su_h_mem_mode:  db "Use internal or extended memory. Extended", 0
su_h_mem_mode2: db "needs the module; both take a reboot.", 0
su_h_mem_auto:  db "Auto Alloc fills the sockets: give it a size", 0
su_h_mem_auto2: db "and it fits the largest modules that add up.", 0
su_h_mem_slot:  db "Socket. Size is this module; kind is its DDR", 0
su_h_mem_slot2: db "generation, form is DIMM or SODIMM.", 0
su_mem_help:
    dw su_h_mem_count, su_h_mem_mode, su_h_mem_auto
    dw su_h_mem_slot, su_h_mem_slot, su_h_mem_slot, su_h_mem_slot
    dw su_h_mem_slot, su_h_mem_slot, su_h_mem_slot, su_h_mem_slot
su_mem_help2:
    dw su_h_mem_count, su_h_mem_mode2, su_h_mem_auto2
    dw su_h_mem_slot2, su_h_mem_slot2, su_h_mem_slot2, su_h_mem_slot2
    dw su_h_mem_slot2, su_h_mem_slot2, su_h_mem_slot2, su_h_mem_slot2

su_ms_0: db "none", 0
su_ms_1: db "1 MB", 0
su_ms_2: db "2 MB", 0
su_ms_3: db "4 MB", 0
su_ms_4: db "8 MB", 0
su_ms_5: db "16 MB", 0
su_ms_6: db "32 MB", 0
su_ms_7: db "64 MB", 0
su_ms_8: db "128 MB", 0
su_ramsize_names:
    dw su_ms_0, su_ms_1, su_ms_2, su_ms_3, su_ms_4
    dw su_ms_5, su_ms_6, su_ms_7, su_ms_8

su_kd_0: db "DDR2 ", 0             ; the trailing space joins it to the form
su_kd_1: db "DDR3 ", 0
su_kd_2: db "DDR4 ", 0
su_kdnames:
    dw su_kd_0, su_kd_1, su_kd_2

su_fm_0: db "DIMM", 0
su_fm_1: db "SODIMM", 0
su_formnames:
    dw su_fm_0, su_fm_1

su_mi_0: db "Internal (list)", 0
su_mi_1: db "Extended (module)", 0
su_mem_mode:
    dw su_mi_0, su_mi_1

su_row_memcount: db "Avail Slots", 0
su_row_memmode:  db "Mode", 0
su_row_memauto:  db "Auto Alloc", 0
su_row_slot0:    db "Slot 0", 0
su_row_slot1:    db "Slot 1", 0
su_row_slot2:    db "Slot 2", 0
su_row_slot3:    db "Slot 3", 0
su_row_slot4:    db "Slot 4", 0
su_row_slot5:    db "Slot 5", 0
su_row_slot6:    db "Slot 6", 0
su_row_slot7:    db "Slot 7", 0

su_mem_labels:
    dw su_row_memcount, su_row_memmode, su_row_memauto
    dw su_row_slot0, su_row_slot1, su_row_slot2, su_row_slot3
    dw su_row_slot4, su_row_slot5, su_row_slot6, su_row_slot7

msg_su_save:    db "Save and exit", 0
msg_su_discard: db "Discard and exit", 0
msg_su_savehelp: db "Write these settings to CMOS", 0
msg_su_discardhelp: db "Leave CMOS as it was", 0

; the dialogs
msg_su_edit_hint: db "< > changes, Enter keeps", 0
msg_su_lt:      db "<", 0
msg_su_gt:      db ">", 0
msg_su_ask_title: db "Save the changes?", 0
msg_su_ask_hint: db "Enter picks, Esc stays", 0
msg_su_yes:     db "Save", 0
msg_su_no:      db "Discard", 0
msg_su_cancel:  db "Cancel", 0
msg_su_battery: db "Battery: ", 0
msg_su_ok:      db "OK", 0
msg_su_dead:    db "DEAD", 0
msg_su_boots:   db "Boots: ", 0
msg_y2k:        db "20", 0
msg_su_kb:      db " KB", 0

su_row_month:   db "Month", 0
su_row_day:     db "Day", 0
su_row_year:    db "Year", 0
su_row_hour:    db "Hour", 0
su_row_min:     db "Minute", 0
su_row_sec:     db "Second", 0
su_row_flop:    db "Floppy drive A", 0
su_row_disk:    db "Hard disk 0", 0
su_row_boot:    db "Boot order", 0
su_row_mem:     db "Base memory", 0
su_row_cpu:     db "CPU cores", 0

su_labels:
    dw su_row_month, su_row_day, su_row_year, su_row_hour, su_row_min
    dw su_row_sec, su_row_flop, su_row_disk, su_row_boot, su_row_mem
    dw su_row_cpu, su_row_emu, su_row_batt, su_row_freq, su_row_repeat
    dw su_row_cores, su_row_threads, su_row_perf, su_row_time
    dw su_row_memcount, su_row_memmode
    dw su_row_slot0, su_row_slot1, su_row_slot2, su_row_slot3
    dw su_row_slot4, su_row_slot5, su_row_slot6, su_row_slot7

; the detail panel: two short lines per item, the way a setup utility explains
; the row that is selected
su_d1:
    dw su_h_month_a, su_h_day_a, su_h_year_a, su_h_hour_a, su_h_min_a
    dw su_h_sec_a, su_h_flop_a, su_h_disk_a, su_h_boot_a, su_h_mem_a
    dw su_h_cpu_a, su_h_emu_a, su_h_batt_a, su_h_freq_a, su_h_rep_a
    dw su_h_cores_a, su_h_thr_a, su_h_perf_a, su_h_time_a
su_d2:
    dw su_h_month_b, su_h_day_b, su_h_year_b, su_h_hour_b, su_h_min_b
    dw su_h_sec_b, su_h_flop_b, su_h_disk_b, su_h_boot_b, su_h_mem_b
    dw su_h_cpu_b, su_h_emu_b, su_h_batt_b, su_h_freq_b, su_h_rep_b
    dw su_h_cores_b, su_h_thr_b, su_h_perf_b, su_h_time_b

su_h_month_a: db "The clock's month at boot.", 0
su_h_month_b: db "CMOS keeps it in BCD.", 0
su_h_day_a:   db "Day of the month at boot.", 0
su_h_day_b:   db "1 to 31, checked.", 0
su_h_year_a:  db "Years since 2000, 0 to 99.", 0
su_h_year_b:  db "POST prints it as 20xx.", 0
su_h_hour_a:  db "Hour at boot, 0 to 23.", 0
su_h_hour_b:  db "The RTC keeps counting.", 0
su_h_min_a:   db "Minute at boot, 0 to 59.", 0
su_h_min_b:   db "", 0
su_h_sec_a:   db "Second at boot, 0 to 59.", 0
su_h_sec_b:   db "", 0
su_h_flop_a:  db "The drive A: the BIOS reports", 0
su_h_flop_b:  db "to the boot loader.", 0
su_h_disk_a:  db "The hard disk the BIOS calls", 0
su_h_disk_b:  db "disk 0x80 through int 13h.", 0
su_h_boot_a:  db "Which drive int 19h tries", 0
su_h_boot_b:  db "first. A: then C: is the PC.", 0
su_h_mem_a:   db "Base memory in 64 KiB steps.", 0
su_h_mem_b:   db "640 KB is conventional.", 0
su_h_cpu_a:   db "Cores the chipset reports on", 0
su_h_cpu_b:   db "port 0x5A0. CPU Cores sets it.", 0
su_h_emu_a:   db "The master switch for everything", 0
su_h_emu_b:   db "on the HW Emulation page.", 0
su_h_batt_a:  db "The CMOS battery the settings", 0
su_h_batt_b:  db "live on. Off means no battery.", 0
su_h_freq_a:  db "The clock the cores report.", 0
su_h_freq_b:  db "It wanders around this value.", 0

su_row_emu:   db "Emulation", 0
su_row_batt:  db "BIOS Battery", 0
su_row_freq:  db "CPU Frequency", 0
su_row_repeat: db "Key Repeat", 0

su_h_rep_a:   db "How often a held key repeats.", 0
su_h_rep_b:   db "The delay before it starts is 2/5 s.", 0

su_row_cores:  db "CPU Cores", 0
su_row_threads: db "CPU Threads", 0
su_row_perf:   db "Performance Emu", 0
su_row_time:   db "Fake OS Time", 0

su_h_cores_a: db "How many cores the chipset gives", 0
su_h_cores_b: db "the guest. 1 to 16; takes a reboot.", 0
su_h_thr_a:   db "Instruction budget per core, per", 0
su_h_thr_b:   db "frame. More threads, more steps.", 0
su_h_perf_a:  db "Multiplies the clock the monitor", 0
su_h_perf_b:  db "reports. Changes the claim, not the speed.", 0
su_h_time_a:  db "The guest's clock, as a percentage", 0
su_h_time_b:  db "of real time. The CPU ignores it.", 0

su_kr_0:      db "1/2 s", 0
su_kr_1:      db "1/4 s", 0
su_kr_2:      db "1/8 s", 0
su_kr_3:      db "1/16 s", 0
su_kr_4:      db "1/24 s", 0
su_krepeat:
    dw su_kr_0, su_kr_1, su_kr_2, su_kr_3, su_kr_4

su_on_0:      db "Off", 0
su_on_1:      db "On", 0
su_onoff:
    dw su_on_0, su_on_1

su_mhz_0:     db "16 MHz", 0
su_mhz_1:     db "25 MHz", 0
su_mhz_2:     db "33 MHz", 0
su_mhz_3:     db "40 MHz", 0
su_mhz_4:     db "50 MHz", 0
su_freq:
    dw su_mhz_0, su_mhz_1, su_mhz_2, su_mhz_3, su_mhz_4

su_fl_0:        db "none", 0
su_fl_1:        db "360K, 5.25 in", 0
su_fl_2:        db "1.2M, 5.25 in", 0
su_fl_3:        db "720K, 3.5 in", 0
su_fl_4:        db "1.44M, 3.5 in", 0
su_floppy:
    dw su_fl_0, su_fl_1, su_fl_2, su_fl_3, su_fl_4

su_hd_0:        db "none", 0
su_hd_1:        db "type 1 (10 MB)", 0
su_hd_2:        db "type 2 (20 MB)", 0
su_hd_3:        db "type 3 (40 MB)", 0
su_disk:
    dw su_hd_0, su_hd_1, su_hd_2, su_hd_3

su_bo_0:        db "A: then C:", 0
su_bo_1:        db "C: then A:", 0
su_bo_2:        db "C: only", 0
su_bo_3:        db "CD-ROM first", 0
su_boot:
    dw su_bo_0, su_bo_1, su_bo_2, su_bo_3

; ---------------------------------------------------------------------------
; The emulation's four knobs, as words. These are the only settings on this
; board that change what the *emulator* does rather than what the guest sees,
; so each is stored as an index into a table that the chipset is then handed.
; ---------------------------------------------------------------------------

; CPU Cores. The chipset takes the count, one to sixteen; the row edits
; "count less one", because a wrap table counts from zero.
su_co_0:  db "1", 0
su_co_1:  db "2", 0
su_co_2:  db "3", 0
su_co_3:  db "4", 0
su_co_4:  db "5", 0
su_co_5:  db "6", 0
su_co_6:  db "7", 0
su_co_7:  db "8", 0
su_co_8:  db "9", 0
su_co_9:  db "10", 0
su_co_10: db "11", 0
su_co_11: db "12", 0
su_co_12: db "13", 0
su_co_13: db "14", 0
su_co_14: db "15", 0
su_co_15: db "16", 0
su_cores:
    dw su_co_0, su_co_1, su_co_2, su_co_3, su_co_4, su_co_5, su_co_6, su_co_7
    dw su_co_8, su_co_9, su_co_10, su_co_11, su_co_12, su_co_13, su_co_14
    dw su_co_15

; CPU Threads: how much of a frame each core is given, as a multiplier on the
; instruction budget. One is a 386; four is a machine that finishes its batch
; and spends the rest of the frame waiting, which is what a second thread does.
su_th_0:  db "1 per core", 0
su_th_1:  db "2 per core", 0
su_th_2:  db "3 per core", 0
su_th_3:  db "4 per core", 0
su_threads:
    dw su_th_0, su_th_1, su_th_2, su_th_3

; Performance Emu: the multiplier applied to the measured instruction rate
; before it is printed as a clock. It is a claim about the machine, not a
; change to it -- a 25 MHz board that retires 4x runs as a 100 MHz one.
su_pf_0:  db "1x (measured)", 0
su_pf_1:  db "2x", 0
su_pf_2:  db "3x", 0
su_pf_3:  db "4x", 0
su_pf_4:  db "6x", 0
su_pf_5:  db "8x", 0
su_pf_6:  db "12x", 0
su_pf_7:  db "16x", 0
su_perf:
    dw su_pf_0, su_pf_1, su_pf_2, su_pf_3, su_pf_4, su_pf_5, su_pf_6, su_pf_7

; Fake OS Time: the guest's clock as a percentage of real time. Below 100 the
; RTC crawls while the CPU keeps running, so a boot that took real seconds
; reports fewer of them -- the guest believes the machine is faster than it is.
su_tm_0:  db "100% (real)", 0
su_tm_1:  db "75%", 0
su_tm_2:  db "50%", 0
su_tm_3:  db "25% (4x faster)", 0
su_tm_4:  db "10% (10x faster)", 0
su_tm_5:  db "5% (20x faster)", 0
su_time:
    dw su_tm_0, su_tm_1, su_tm_2, su_tm_3, su_tm_4, su_tm_5

; The setup edits *settings*, and a setting is not always the same as its row in
; the working copy: the memory panel keeps its sockets where the page that shows
; them wants them. This is the one place that knows both.
su_bufof:                                       ; words, because both readers
    dw 0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18
    dw SU_M_COUNT, SU_M_MODE                    ; 19, 20
    dw SU_M_SIZE, SU_M_SIZE + 1, SU_M_SIZE + 2, SU_M_SIZE + 3
    dw SU_M_SIZE + 4, SU_M_SIZE + 5, SU_M_SIZE + 6, SU_M_SIZE + 7

su_ramsizes:
    dw 0, 1, 2, 4, 8, 16, 32, 64, 128

su_hwtabs:
    dw su_cores, su_threads, su_perf, su_time

; the words the numeric rows hold, for the chipset to read back
su_hwcores:  db 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16
su_hwthreads: db 1, 2, 3, 4
su_hwperf:   db 1, 2, 3, 4, 6, 8, 12, 16
su_hwtime:   db 100, 75, 50, 25, 10, 5
