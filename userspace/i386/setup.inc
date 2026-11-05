; ---------------------------------------------------------------------------
; The setup utility.
;
; A real BIOS gives you a window at POST, and a screen behind it: the machine's
; settings, editable, with the values the firmware will use on the next boot.
; That is what this is -- and every value it edits lives in the CMOS, so the
; screen is not a mock-up, it is the same bytes POST reads.
;
; Layout, the classic three-band screen:
;
;   +------------------------------------------------------------------+
;   |                        JSW BIOS Setup Utility                    |
;   +------------------------------------------------------------------+
;   |  Month          01                                               |
;   |  ...                                                             |
;   +------------------------------------------------------------------+
;   |  Up/Down selects   Left/Right changes   F10 saves   Esc leaves   |
;   +------------------------------------------------------------------+
;
; Keys arrive from the 8042 as set-1 scan codes: 48/50 up/down, 4B/4D
; left/right, 44 F10, 01 Esc, 53 Del, 3C F2. There are no release codes in this
; machine yet, so one press is one event.
; ---------------------------------------------------------------------------

SU_BUF   equ 0x500                  ; the working copy: 16 bytes in the data area
SU_ITEM  equ 0x510                  ; which row is selected
SU_TAB   equ 0x50C                  ; which page: 0 Main, 1 Info, 2 Exit
SU_OLD   equ 0x50D                  ; the value before an edit, for Esc
SU_N     equ 11                     ; how many rows (the last is read-only)

; attribute bytes
SU_PAPER equ 0x17                   ; blue paper, light grey ink
SU_HEAD  equ 0x1E                   ; blue paper, yellow ink
SU_SEL   equ 0x70                   ; light grey paper, black ink
SU_INK   equ 0x4F5                  ; the ink of the row being drawn

; the frame, drawn with CP437's double-line set, the way the rv64 setup does
G_HBAR   equ 0xCD
G_VBAR   equ 0xBA
G_TL     equ 0xC9
G_TR     equ 0xBB
G_BL     equ 0xC8
G_BR     equ 0xBC
G_TJ     equ 0xCB                   ; the rule meets the top border
G_BJ     equ 0xCA                   ; and the bottom border

SU_SPLIT equ 45                     ; the column rule between menu and panel
SU_RX    equ 47                     ; the panel's first column
SU_ROW0  equ 2                      ; the first menu row
SU_VAL   equ 20                     ; the value column

; ---------------------------------------------------------------------------
; suWindow -- the "press DEL" window at POST
;
; `al` comes back as 1 when the user asked for setup. The wait is a counted
; loop, not a clock: this firmware has no timer of its own yet, and a counted
; delay is the honest description of "about a second on the machine it runs on".
; ---------------------------------------------------------------------------
suWindow:
    push    bx
    push    cx
    push    dx
    mov     si, msg_press_del
    call    puts
    ; long enough to hold DEL through now that the machine runs at about a
    ; million instructions a second: a key pressed before the prompt is in the
    ; 8042's buffer, and the first poll takes it
    mov     cx, 40000
.wait:
    in      al, 0x64
    test    al, 1
    jz      .next
    in      al, 0x60                ; a scan code arrived
    cmp     al, 0x53                ; Delete
    je      .yes
    cmp     al, 0x3C                ; F2
    je      .yes
.next:
    loop    .wait
    xor     al, al
    jmp     .out
.yes:
    mov     al, 1
.out:
    pop     dx
    pop     cx
    pop     bx
    ret

; ---------------------------------------------------------------------------
; suEnter -- the screen itself. Returns when the user leaves, with the CMOS
; already written (F10) or untouched (Esc).
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
    ; a checksum a real board keeps, so the battery can be declared good
    mov     al, 0x31
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
    call    suGoto
    call    suFill
    pop     cx
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
.main:
    call    drawSetup
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
    cmp     al, 0x44                ; F10 saves and boots
    je      .save
    cmp     al, 0x01                ; Esc asks
    je      .ask
    cmp     al, 0x0E                ; Backspace asks too
    je      .ask
    jmp     .main

.up:
    mov     al, [es:SU_TAB]
    call    tabItems
    test    bl, bl
    jz      .main                   ; a page with nothing to select
    mov     al, [es:SU_ITEM]
    test    al, al
    jnz     .updec
    mov     al, bl
.updec:
    dec     al
    mov     [es:SU_ITEM], al
    jmp     .main

.down:
    mov     al, [es:SU_TAB]
    call    tabItems
    test    bl, bl
    jz      .main
    mov     al, [es:SU_ITEM]
    inc     al
    cmp     al, bl
    jb      .downset
    xor     al, al
.downset:
    mov     [es:SU_ITEM], al
    jmp     .main

.left:
    mov     al, [es:SU_TAB]
    test    al, al
    jnz     .leftdec
    mov     al, 3
.leftdec:
    dec     al
    mov     [es:SU_TAB], al
    mov     byte [es:SU_ITEM], 0
    jmp     .main

.right:
    mov     al, [es:SU_TAB]
    inc     al
    cmp     al, 3
    jb      .rightset
    xor     al, al
.rightset:
    mov     [es:SU_TAB], al
    mov     byte [es:SU_ITEM], 0
    jmp     .main

.enter:
    mov     al, [es:SU_TAB]
    test    al, al
    jz      .edit
    cmp     al, 2
    je      .exitpage
    jmp     .main                   ; the information page has no actions
.exitpage:
    cmp     byte [es:SU_ITEM], 0
    jne     .leave                  ; "discard and exit"
    jmp     .save
.edit:
    call    suEdit
    jmp     .main
.ask:
    call    suAskExit               ; al = 1 save, 0 discard, 2 cancel
    cmp     al, 1
    je      .save
    cmp     al, 2
    je      .main
    jmp     .leave
.save:
    call    suSave
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

; tabItems: al = page -> bl = how many selectable rows it has
tabItems:
    push    ax
    xor     bl, bl
    test    al, al
    jnz     .notmain
    mov     bl, SU_N
    jmp     .out
.notmain:
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
    call    drawTabs
    ; the title in the top border, the version hard right
    mov     dl, 4
    xor     dh, dh
    call    suGoto
    mov     si, msg_su_title
    mov     bl, SU_HEAD
    call    suPrint
    mov     dl, 70
    xor     dh, dh
    call    suGoto
    mov     si, msg_su_version
    mov     bl, SU_HEAD
    call    suPrint
    ; the page
    mov     al, [es:SU_TAB]
    test    al, al
    jz      .mainpage
    cmp     al, 1
    je      .infopage
    call    pageExit
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

; the tabs, written over the top border
drawTabs:
    push    ax
    push    bx
    push    dx
    mov     dl, 2
    xor     dh, dh
    call    suGoto
    mov     si, msg_tab_main
    mov     bl, SU_HEAD
    cmp     byte [es:SU_TAB], 0
    jne     .t0
    mov     bl, SU_SEL
.t0:
    call    suPrint
    mov     dl, 10
    xor     dh, dh
    call    suGoto
    mov     si, msg_tab_info
    mov     bl, SU_HEAD
    cmp     byte [es:SU_TAB], 1
    jne     .t1
    mov     bl, SU_SEL
.t1:
    call    suPrint
    mov     dl, 18
    xor     dh, dh
    call    suGoto
    mov     si, msg_tab_exit
    mov     bl, SU_HEAD
    cmp     byte [es:SU_TAB], 2
    jne     .t2
    mov     bl, SU_SEL
.t2:
    call    suPrint
    pop     dx
    pop     bx
    pop     ax
    ret

; pageMain: the settings, with the detail panel beside them
pageMain:
    push    ax
    push    si
    mov     dl, 2
    mov     dh, 1
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
    mov     dh, 1
    call    suGoto
    mov     si, msg_su_info
    mov     bl, SU_HEAD
    call    suPrint
    mov     dl, 2
    mov     dh, 3
    call    suGoto
    mov     si, msg_su_biosline
    mov     bl, SU_PAPER
    call    suPrint
    mov     dl, 2
    mov     dh, 4
    call    suGoto
    mov     si, msg_su_cpuline
    mov     bl, SU_PAPER
    call    suPrint
    ; the clock, ticking
    mov     dl, 2
    mov     dh, 5
    call    suGoto
    mov     si, msg_su_clock
    mov     bl, SU_PAPER
    call    suPrint
    call    suClock
    ; memory and cores, from the hardware
    mov     dl, 2
    mov     dh, 6
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
    mov     dh, 7
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
    mov     dh, 8
    call    suGoto
    mov     si, msg_su_diskline
    mov     bl, SU_PAPER
    call    suPrint
    mov     dl, 47
    mov     dh, 3
    call    suGoto
    mov     si, msg_su_tabhelp
    mov     bl, SU_PAPER
    call    suPrint
    mov     dl, 47
    mov     dh, 5
    call    suGoto
    mov     si, msg_su_ramline
    mov     bl, SU_PAPER
    call    suPrint
    mov     dl, 47
    mov     dh, 6
    call    suGoto
    mov     si, msg_su_romline
    mov     bl, SU_PAPER
    call    suPrint
    mov     dl, 47
    mov     dh, 7
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
    push    dx
    push    si
    mov     dl, 2
    mov     dh, 1
    call    suGoto
    mov     si, msg_su_exithead
    mov     bl, SU_HEAD
    call    suPrint
    ; "Save and exit"
    mov     bl, SU_PAPER
    cmp     byte [es:SU_ITEM], 0
    jne     .row0
    mov     bl, SU_SEL
.row0:
    mov     dl, 1
    mov     dh, 2
    mov     cx, 43
    push    bx
    mov     al, ' '
    call    suBar
    pop     bx
    mov     dl, 3
    mov     dh, 2
    call    suGoto
    mov     si, msg_su_save
    call    suPrint
    ; "Discard and exit"
    mov     bl, SU_PAPER
    cmp     byte [es:SU_ITEM], 1
    jne     .row1
    mov     bl, SU_SEL
.row1:
    mov     dl, 1
    mov     dh, 3
    mov     cx, 43
    push    bx
    mov     al, ' '
    call    suBar
    pop     bx
    mov     dl, 3
    mov     dh, 3
    call    suGoto
    mov     si, msg_su_discard
    call    suPrint
    ; what each one does
    mov     dl, 47
    mov     dh, 2
    call    suGoto
    mov     si, msg_su_savehelp
    mov     bl, SU_PAPER
    call    suPrint
    mov     dl, 47
    mov     dh, 5
    call    suGoto
    mov     si, msg_su_discardhelp
    mov     bl, SU_PAPER
    call    suPrint
    pop     si
    pop     dx
    pop     bx
    pop     ax
    ret

suFrame:
    push    ax
    push    bx
    push    cx
    push    dx
    push    si
    ; the two horizontal rules, top and bottom
    xor     dl, dl
    xor     dh, dh
    mov     cx, 80
    mov     al, G_HBAR
    mov     bl, SU_HEAD
    call    suBar
    xor     dl, dl
    mov     dh, 22
    mov     cx, 80
    mov     al, G_HBAR
    mov     bl, SU_HEAD
    call    suBar
    ; the four corners
    xor     dl, dl
    xor     dh, dh
    mov     al, G_TL
    call    suCell
    mov     dl, 79
    xor     dh, dh
    mov     al, G_TR
    call    suCell
    xor     dl, dl
    mov     dh, 22
    mov     al, G_BL
    call    suCell
    mov     dl, 79
    mov     dh, 22
    mov     al, G_BR
    call    suCell
    ; the verticals: the sides and the rule at SU_SPLIT
    mov     dh, 1
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
    cmp     dh, 22
    jb      .side
    ; the junctions where the rule meets the two horizontal borders
    mov     dl, SU_SPLIT
    xor     dh, dh
    mov     al, G_TJ
    call    suCell
    mov     dl, SU_SPLIT
    mov     dh, 22
    mov     al, G_BJ
    call    suCell
    pop     si
    pop     dx
    pop     cx
    pop     bx
    pop     ax
    ret

; suCell: dl/dh = column/row, al = the character, bl = the attribute
suCell:
    push    ax
    push    cx
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
    mov     dh, 1
.row:
    mov     dl, 1
    mov     cx, 78
    mov     al, ' '
    mov     bl, SU_PAPER
    call    suBar
    inc     dh
    cmp     dh, 22
    jb      .row
    pop     dx
    pop     cx
    pop     bx
    pop     ax
    ret

suDrawAll:
    mov     al, 1
    mov     [es:SU_ITEM], 0
    call    suClearBody
    call    suFrame
    ; the title sits in the top border, the version hard right
    mov     dl, 4
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
    ; the headings
    mov     dl, 2
    mov     dh, 1
    call    suGoto
    mov     si, msg_su_settings
    mov     bl, SU_HEAD
    call    suPrint
    ; the menu
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
    call    suStatus
    ret

; the status line under the frame, and the key legend below it
suStatus:
    push    ax
    push    bx
    push    cx
    push    dx
    push    si
    ; the two lines under the frame are a band, not a bare row. The bottom one
    ; stops a cell short: eighty cells wrap the cursor onto row 25, and the
    ; console answers that with a scroll -- the whole screen moves up one line
    xor     dl, dl
    mov     dh, 23
    mov     cx, 80
    mov     al, ' '
    mov     bl, SU_PAPER
    call    suBar
    xor     dl, dl
    mov     dh, 24
    mov     cx, 79
    mov     al, ' '
    mov     bl, SU_PAPER
    call    suBar
    mov     dl, 2
    mov     dh, 23
    call    suGoto
    mov     si, msg_su_battery
    mov     bl, SU_PAPER
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
    mov     bl, SU_PAPER
    call    suPrint
    mov     dl, 42
    mov     dh, 23
    call    suGoto
    mov     si, msg_su_boots
    mov     bl, SU_PAPER
    call    suPrint
    mov     al, 0x30
    call    cmos_read
    call    put2
    ; the keys, under the status line
    mov     dl, 2
    mov     dh, 24
    call    suGoto
    mov     si, msg_su_keys
    mov     bl, SU_PAPER
    call    suPrint
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
    mov     dh, 1
.blank:
    mov     dl, SU_RX
    mov     cx, 32
    mov     al, ' '
    mov     bl, SU_PAPER
    call    suBar
    inc     dh
    cmp     dh, 22
    jb      .blank
    ; the item's name
    mov     al, [es:SU_ITEM]
    xor     ah, ah
    add     ax, ax
    mov     di, ax
    mov     dl, SU_RX
    mov     dh, 1
    call    suGoto
    mov     si, [di + su_labels]
    mov     bl, SU_HEAD
    call    suPrint
    ; its two lines of help
    mov     dl, SU_RX
    mov     dh, 2
    call    suGoto
    mov     si, [di + su_d1]
    mov     bl, SU_PAPER
    call    suPrint
    mov     dl, SU_RX
    mov     dh, 3
    call    suGoto
    mov     si, [di + su_d2]
    mov     bl, SU_PAPER
    call    suPrint
    ; how to change it
    mov     dl, SU_RX
    mov     dh, 5
    call    suGoto
    mov     si, msg_su_hint
    mov     bl, SU_HEAD
    call    suPrint
    ; the system summary: what a setup screen always shows and never edits
    mov     dl, SU_RX
    mov     dh, 8
    call    suGoto
    mov     si, msg_su_system
    mov     bl, SU_HEAD
    call    suPrint
    mov     dl, SU_RX
    mov     dh, 9
    call    suGoto
    mov     si, msg_su_cpu
    mov     bl, SU_PAPER
    call    suPrint
    ; memory, in KiB out of the CMOS
    mov     dl, SU_RX
    mov     dh, 10
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
    mov     dh, 11
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
    mov     dh, 12
    call    suGoto
    mov     si, msg_su_clock
    mov     bl, SU_PAPER
    call    suPrint
    call    suClock
    pop     di
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
    call    suValue
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
.named:
    mov     al, [es:SU_BUF + si]
    xor     ah, ah
    add     ax, ax
    mov     di, ax
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
msg_press_del:  db "Press DEL or F2 to enter setup", 13, 10, 0
msg_su_title:   db "JSW BIOS Setup Utility", 0
msg_su_version: db "v0.3", 0
msg_su_settings: db "Settings", 0
msg_su_system:  db "System", 0
msg_su_hint:    db "< > change", 0
msg_su_cpu:     db "CPU: 80386 real mode", 0
msg_su_mem:     db "Memory: ", 0
msg_su_cores:   db "Cores: ", 0
msg_su_clock:   db "Clock: ", 0
msg_su_keys:    db "^/v select   < > change   Enter saves   Backspace boots", 0
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
    dw su_row_cpu

; the detail panel: two short lines per item, the way a setup utility explains
; the row that is selected
su_d1:
    dw su_h_month_a, su_h_day_a, su_h_year_a, su_h_hour_a, su_h_min_a
    dw su_h_sec_a, su_h_flop_a, su_h_disk_a, su_h_boot_a, su_h_mem_a
    dw su_h_cpu_a
su_d2:
    dw su_h_month_b, su_h_day_b, su_h_year_b, su_h_hour_b, su_h_min_b
    dw su_h_sec_b, su_h_flop_b, su_h_disk_b, su_h_boot_b, su_h_mem_b
    dw su_h_cpu_b

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
su_h_cpu_b:   db "port 0x5A0. This one is fixed.", 0

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
