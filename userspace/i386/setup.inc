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

SU_BUF   equ 0x500                  ; the working copy: 16 bytes in the data area
SU_ITEM  equ 0x510                  ; which row is selected
SU_TAB   equ 0x50C                  ; which page: 0 Main, 1 Info, 2 Exit
SU_OLD   equ 0x50D                  ; the value before an edit, for Esc
SU_OPT   equ 0x50E                  ; which button the exit prompt has lit
SU_N     equ 11                     ; how many rows (the last is read-only)

; attribute bytes. White on blue, not grey on blue: a 720 x 400 screen that is
; scaled up in the player has had every pixel line smeared, and grey text turns
; into fog at that size while white stays readable.
SU_PAPER equ 0x1F                   ; blue paper, white ink
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
    mov     si, msg_press_del
    call    puts
    ; Long enough to hold DEL through. `loop` counts in cx, and cx is sixteen
    ; bits -- 500,000 does not fit, it truncates to 41,088 and the window comes
    ; back to a blink. An outer counter of eight makes it 400,000 polls, about
    ; half a second on this machine.
    mov     bx, 8
.round:
    mov     cx, 50000
.wait:
    in      al, 0x64
    test    al, 1
    jz      .next
    in      al, 0x60                ; a scan code arrived
    cmp     al, 0x53                ; Delete
    je      .yes
    cmp     al, 0x3C                ; F2
    je      .yes
    ; any other key means "get on with it", the way a real board does
    xor     al, al
    jmp     .out
.next:
    loop    .wait
    dec     bx
    jnz     .round
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
; suAdjust -- change the selected value by dl (-1 or +1)
; ---------------------------------------------------------------------------
suAdjust:
    push    ax
    push    bx
    push    cx
    mov     al, [es:SU_ITEM]
    mov     ah, al                  ; ah = the row, kept while we work
    xor     bx, bx
    mov     bl, al
    mov     al, [es:SU_BUF + bx]    ; the value
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
    cmp     bl, 2
    jne     .not_year
    add     al, dl
    cmp     al, 100
    jb      .y_ok
    xor     al, al
.y_ok:
    cmp     al, 0
    jge     .y_done
    mov     al, 99
.y_done:
    jmp     .store
.not_year:
    cmp     bl, 3
    jne     .not_hour
    add     al, dl
    cmp     al, 24
    jb      .h_ok
    xor     al, al
.h_ok:
    cmp     al, 0
    jge     .h_done
    mov     al, 23
.h_done:
    jmp     .store
.not_hour:
    cmp     bl, 4
    jne     .not_min
    call    suStep60
    jmp     .store
.not_min:
    cmp     bl, 5
    jne     .not_sec
    call    suStep60
    jmp     .store
.not_sec:
    cmp     bl, 6
    jne     .not_flop
    add     al, dl
    cmp     al, 5
    jb      .f_ok
    xor     al, al
.f_ok:
    cmp     al, 0
    jge     .f_done
    mov     al, 4
.f_done:
    jmp     .store
.not_flop:
    cmp     bl, 7
    jne     .not_hd
    add     al, dl
    cmp     al, 4
    jb      .d_ok
    xor     al, al
.d_ok:
    cmp     al, 0
    jge     .d_done
    mov     al, 3
.d_done:
    jmp     .store
.not_hd:
    cmp     bl, 8
    jne     .not_boot
    add     al, dl
    cmp     al, 4
    jb      .b_ok
    xor     al, al
.b_ok:
    cmp     al, 0
    jge     .b_done
    mov     al, 3
.b_done:
    jmp     .store
.not_boot:
    ; base memory: the buffer counts 64 KiB steps, the outer range is 0..10
    add     al, dl
    cmp     al, 10
    jbe     .m_lo
    mov     al, 10
.m_lo:
    cmp     al, 0
    jge     .m_done
    xor     al, al
.m_done:
.store:
    mov     bl, ah
    mov     [es:SU_BUF + bx], al
    mov     al, ah
    call    suDrawRow
    pop     cx
    pop     bx
    pop     ax
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
    mov     al, 3
.leftdec:
    dec     al
    mov     [es:SU_TAB], al
    mov     byte [es:SU_ITEM], 0
    call    drawSetup
    jmp     .key

.right:
    mov     al, [es:SU_TAB]
    inc     al
    cmp     al, 3
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
    jmp     .key                    ; the information page has no actions
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
    mov     al, [es:SU_BUF + di]
    mov     [es:SU_OLD], al
.key:
    call    suEditBox
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
    jmp     .key
.right:
    mov     dl, 1
    call    suAdjust
    jmp     .key
.revert:
    mov     al, [es:SU_OLD]
    mov     [es:SU_BUF + di], al
.done:
    pop     di
    pop     dx
    pop     cx
    pop     bx
    pop     ax
    ret

; suEditBox: the editing dialog, 33 columns by 7 rows, centred
suEditBox:
    push    ax
    push    bx
    push    cx
    push    dx
    push    si
    mov     dh, 12
.paper:
    mov     dl, 24
    mov     cx, 33
    mov     al, ' '
    mov     bl, SU_SEL
    call    suBar
    inc     dh
    cmp     dh, 18
    jb      .paper
    ; the title strip carries the row's own name
    mov     dl, 24
    mov     dh, 11
    mov     cx, 33
    mov     al, ' '
    mov     bl, SU_HEAD
    call    suBar
    mov     dl, 26
    mov     dh, 11
    call    suGoto
    mov     al, [es:SU_ITEM]
    xor     ah, ah
    add     ax, ax
    mov     si, ax
    mov     si, [si + su_labels]
    mov     bl, SU_HEAD
    call    suPrint
    ; the value, arrow either side, in the row's own printer
    mov     dl, 30
    mov     dh, 13
    call    suGoto
    mov     si, msg_su_lt
    mov     bl, SU_HEAD
    call    suPrint
    mov     dl, 34
    mov     dh, 13
    call    suGoto
    mov     al, [es:SU_ITEM]
    mov     bl, SU_SEL
    call    suValue
    mov     dl, 52
    mov     dh, 13
    call    suGoto
    mov     si, msg_su_gt
    mov     bl, SU_HEAD
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

; suAskBox: the prompt, 36 columns by 7 rows, centred
suAskBox:
    push    ax
    push    bx
    push    cx
    push    dx
    push    si
    mov     dh, 12
.paper:
    mov     dl, 22
    mov     cx, 36
    mov     al, ' '
    mov     bl, SU_PAPER
    call    suBar
    inc     dh
    cmp     dh, 18
    jb      .paper
    mov     dl, 22
    mov     dh, 11
    mov     cx, 36
    mov     al, ' '
    mov     bl, SU_HEAD
    call    suBar
    mov     dl, 24
    mov     dh, 11
    call    suGoto
    mov     si, msg_su_ask_title
    mov     bl, SU_HEAD
    call    suPrint
    ; Save
    mov     dl, 24
    mov     dh, 14
    mov     cx, 11
    mov     al, ' '
    mov     bl, SU_PAPER
    cmp     byte [es:SU_OPT], 0
    jne     .s0
    mov     bl, SU_SEL
.s0:
    call    suBar
    mov     dl, 27
    mov     dh, 14
    call    suGoto
    mov     si, msg_su_yes
    mov     bl, SU_PAPER
    cmp     byte [es:SU_OPT], 0
    jne     .s1
    mov     bl, SU_SEL
.s1:
    call    suPrint
    ; Discard
    mov     dl, 36
    mov     dh, 14
    mov     cx, 11
    mov     al, ' '
    mov     bl, SU_PAPER
    cmp     byte [es:SU_OPT], 1
    jne     .d0
    mov     bl, SU_SEL
.d0:
    call    suBar
    mov     dl, 37
    mov     dh, 14
    call    suGoto
    mov     si, msg_su_no
    mov     bl, SU_PAPER
    cmp     byte [es:SU_OPT], 1
    jne     .d1
    mov     bl, SU_SEL
.d1:
    call    suPrint
    ; Cancel
    mov     dl, 48
    mov     dh, 14
    mov     cx, 9
    mov     al, ' '
    mov     bl, SU_PAPER
    cmp     byte [es:SU_OPT], 2
    jne     .c0
    mov     bl, SU_SEL
.c0:
    call    suBar
    mov     dl, 49
    mov     dh, 14
    call    suGoto
    mov     si, msg_su_cancel
    mov     bl, SU_PAPER
    cmp     byte [es:SU_OPT], 2
    jne     .c1
    mov     bl, SU_SEL
.c1:
    call    suPrint
    mov     dl, 24
    mov     dh, 16
    call    suGoto
    mov     si, msg_su_ask_hint
    mov     bl, SU_PAPER
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
    ; Main
    mov     dl, 2
    mov     dh, SU_TABS
    mov     cx, 6
    mov     al, ' '
    mov     bl, SU_PAPER
    cmp     byte [es:SU_TAB], 0
    jne     .m0
    mov     bl, SU_SEL
.m0:
    call    suBar
    mov     dl, 3
    mov     dh, SU_TABS
    call    suGoto
    mov     si, msg_tab_main
    mov     bl, SU_PAPER
    cmp     byte [es:SU_TAB], 0
    jne     .m1
    mov     bl, SU_SEL
.m1:
    call    suPrint
    ; Info
    mov     dl, 10
    mov     dh, SU_TABS
    mov     cx, 6
    mov     al, ' '
    mov     bl, SU_PAPER
    cmp     byte [es:SU_TAB], 1
    jne     .i0
    mov     bl, SU_SEL
.i0:
    call    suBar
    mov     dl, 11
    mov     dh, SU_TABS
    call    suGoto
    mov     si, msg_tab_info
    mov     bl, SU_PAPER
    cmp     byte [es:SU_TAB], 1
    jne     .i1
    mov     bl, SU_SEL
.i1:
    call    suPrint
    ; Exit
    mov     dl, 18
    mov     dh, SU_TABS
    mov     cx, 6
    mov     al, ' '
    mov     bl, SU_PAPER
    cmp     byte [es:SU_TAB], 2
    jne     .e0
    mov     bl, SU_SEL
.e0:
    call    suBar
    mov     dl, 19
    mov     dh, SU_TABS
    call    suGoto
    mov     si, msg_tab_exit
    mov     bl, SU_PAPER
    cmp     byte [es:SU_TAB], 2
    jne     .e1
    mov     bl, SU_SEL
.e1:
    call    suPrint
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
msg_su_tabhelp: db "< > switches pages", 0

; the exit page
msg_su_exithead: db "Exit", 0
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
