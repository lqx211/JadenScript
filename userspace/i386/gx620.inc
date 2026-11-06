; ---------------------------------------------------------------------------
; The setup utility, laid out the way a Dell OptiPlex GX620 lays its own out,
; with this machine's name in the title bar where Dell's used to be.
;
;   +- JSW System 80386 ---------------------------------------------------+
;   |+--------------------------+ +--------------------------------------+|
;   ||- System                  | |                      System Info     ||
;   ||    System Info           |^|--------------------------------------||
;   ||    Processor Info        |#| System          = JSW 80386         ||
;   ||+ Drives                  |v| BIOS Version    = A07 (03/31/06)    ||
;   |+--------------------------+ +--------------------------------------+|
;   +----------------------------------------------------------------------+
;   +----------------------------------------------------------------------+
;   | Press Up/Down arrows to select a different field                     |
;   | Press Left/Right keys to expand or collapse a group                  |
;   | Press Esc to exit Setup                                              |
;   +----------------------------------------------------------------------+
;
; The tree on the left is data: one six-byte record per node in pre-order, so
; an indent is a depth and a group is a node that opens. Everything the right
; pane says is data too -- a title and a list of lines -- which is what keeps
; the drawing down to two loops instead of a page per setting.
;
; Editable nodes carry the setting's number in the low four bits of their kind
; byte, and the values are the same eleven bytes in the CMOS that the rest of
; this BIOS has always used. Nothing here invents state.
; ---------------------------------------------------------------------------

GX_SEL   equ 0x521                  ; which visible row is selected
GX_TOP   equ 0x522                  ; the first visible row shown
GX_NVIS  equ 0x523                  ; how many rows the open tree has
GX_HIDE  equ 0x524                  ; while walking: the depth folded away
GX_NODE  equ 0x525                  ; the node the drawing loops are on
GX_ROW   equ 0x526                  ; ... and the screen row
GX_COL   equ 0x527                  ; ... and the column
GX_LINE  equ 0x528                  ; ... and the line of a detail being printed
GX_EXP   equ 0x5A0                  ; one byte per node: a group is open at 1
GX_VIS   equ 0x5C0                  ; 32 bytes: the node index of each visible row
GX_NODES equ 31
GX_LIVE  equ 0xFFFF                 ; a page that reads the hardware, not the ROM

GX_TOP_I equ 1                      ; the outer frame's first row inside
GX_BOT_I equ 22                     ; ... and its bottom border
GX_LX    equ 1                      ; the left pane
GX_LR    equ 27
GX_RX    equ 29                     ; the right pane, one column clear of the tree
GX_RR    equ 78
GX_CX    equ 2                      ; the left pane's text
GX_SBAR  equ 26                     ; its scrollbar
GX_TX    equ 30                     ; the right pane's text
GX_TW    equ 48                     ; its width
GX_FIRST equ 2                      ; the first row either pane can use
GX_LAST  equ 21                     ; ... and the last
GX_HINT  equ 25                     ; the hint box
GX_HINT_H equ 5

KIND_GROUP equ 0x40
KIND_EDIT  equ 0x80

; ---------------------------------------------------------------------------
; gxEnter -- the setup utility. Returns when the user has decided to leave.
; ---------------------------------------------------------------------------
gxEnter:
    push    ax
    mov     ax, cs
    mov     ds, ax
    pop     ax
    mov     byte [es:CON_ATTR], SU_PAPER
    mov     byte [es:GX_SEL], 0
    mov     byte [es:GX_TOP], 0
    call    suLoad                  ; the CMOS into the working copy
    call    gxDefaults
    call    gxDraw
.key:
    call    keyScan
    cmp     al, 0x48                ; up
    je      .up
    cmp     al, 0x50                ; down
    je      .down
    cmp     al, 0x4B                ; left
    je      .toggle
    cmp     al, 0x4D                ; right
    je      .toggle
    cmp     al, 0x1C                ; Enter
    je      .enter
    cmp     al, 0x01                ; Esc
    je      .leave
    cmp     al, 0x0E
    je      .leave
    jmp     .key
.up:
    mov     al, [es:GX_SEL]
    test    al, al
    jz      .key
    dec     al
    mov     [es:GX_SEL], al
    call    gxFollow
    call    gxDrawLeft
    call    gxDrawRight
    jmp     .key
.down:
    mov     al, [es:GX_SEL]
    inc     al
    cmp     al, [es:GX_NVIS]
    jae     .key
    mov     [es:GX_SEL], al
    call    gxFollow
    call    gxDrawLeft
    call    gxDrawRight
    jmp     .key
.toggle:
.enter:
    call    gxNodeOf                ; di = the record under the light
    mov     al, [di + 1]            ; its kind
    test    al, KIND_GROUP
    jz      .editable
    call    gxFold
    call    gxDraw
    jmp     .key
.editable:
    test    al, KIND_EDIT
    jz      .key                    ; a page of facts: nothing to change
    and     al, 0x0F                ; the setting's number
    mov     [es:SU_ITEM], al
    call    suEdit                  ; the dialog this BIOS already had
    call    gxDraw
    jmp     .key
.leave:
    call    suAskExit               ; al = 1 save, 0 discard, 2 stay
    cmp     al, 1
    je      .save
    cmp     al, 2
    je      .stay
    jmp     .out
.save:
    call    suSave
    jmp     .out
.stay:
    call    gxDraw
    jmp     .key
.out:
    mov     byte [es:CON_ATTR], 0x07
    call    clear_text
    xor     dl, dl
    xor     dh, dh
    call    suGoto
    ret

; ---------------------------------------------------------------------------
; gxDefaults -- System, Date/Time and Drives start open, as the GX620 shows
; them; Onboard Devices starts folded.
; ---------------------------------------------------------------------------
gxDefaults:
    push    ax
    push    cx
    push    di
    mov     di, GX_EXP
    mov     cx, GX_NODES
    xor     al, al
.fill:
    mov     [es:di], al
    inc     di
    loop    .fill
    mov     byte [es:GX_EXP + 0], 1
    mov     byte [es:GX_EXP + 4], 1
    mov     byte [es:GX_EXP + 12], 1
    mov     byte [es:GX_EXP + 16], 0
    mov     byte [es:GX_EXP + 19], 1   ; HW Emulation shows what it is doing
    pop     di
    pop     cx
    pop     ax
    ret

; ---------------------------------------------------------------------------
; gxVisible -- which rows the tree is showing, into GX_VIS, and how many.
;
; The records are in pre-order, so one pass is enough: a folded group hides
; everything after it that is deeper, until a node comes back out.
; ---------------------------------------------------------------------------
gxVisible:
    push    ax
    push    bx
    push    cx
    push    si
    push    di
    mov     byte [es:GX_HIDE], 0xFF
    mov     di, GX_VIS
    xor     cx, cx
    mov     bx, gx_nodes
    mov     byte [es:GX_NODE], 0
.walk:
    mov     al, [es:GX_NODE]
    cmp     al, GX_NODES
    jae     .done
    mov     si, bx
    mov     al, [si]
    cmp     byte [es:GX_HIDE], 0xFF
    je      .shown
    cmp     al, [es:GX_HIDE]
    ja      .hidden
    mov     byte [es:GX_HIDE], 0xFF
.shown:
    mov     al, [es:GX_NODE]
    mov     [es:di], al
    inc     di
    inc     cx
    mov     al, [si + 1]
    test    al, KIND_GROUP
    jz      .hidden
    mov     al, [es:GX_NODE]
    push    bx
    mov     bx, GX_EXP
    add     bx, ax
    mov     al, [es:bx]
    pop     bx
    test    al, al
    jnz     .hidden
    mov     al, [si]
    mov     [es:GX_HIDE], al
.hidden:
    inc     byte [es:GX_NODE]
    add     bx, 6
    jmp     .walk
.done:
    mov     [es:GX_NVIS], cl
    test    cx, cx
    jnz     .clamp
    mov     byte [es:GX_SEL], 0
    jmp     .out
.clamp:
    mov     al, [es:GX_SEL]
    cmp     al, cl
    jb      .out
    mov     al, cl
    dec     al
    mov     [es:GX_SEL], al
.out:
    pop     di
    pop     si
    pop     cx
    pop     bx
    pop     ax
    ret

; ---------------------------------------------------------------------------
; gxFollow -- keep the selection inside the window, one row at a time.
; ---------------------------------------------------------------------------
gxFollow:
    push    ax
    mov     al, [es:GX_SEL]
    cmp     al, [es:GX_TOP]
    jae     .below
    mov     [es:GX_TOP], al
    jmp     .out
.below:
    mov     ah, [es:GX_TOP]
    add     ah, GX_LAST - GX_FIRST
    cmp     al, ah
    jbe     .out
    mov     al, [es:GX_SEL]
    sub     al, GX_LAST - GX_FIRST
    mov     [es:GX_TOP], al
.out:
    pop     ax
    ret

; ---------------------------------------------------------------------------
; gxNodeOf -- di = the record the light is on
; ---------------------------------------------------------------------------
gxNodeOf:
    push    ax
    push    bx
    mov     al, [es:GX_SEL]
    xor     ah, ah
    mov     bx, GX_VIS
    add     bx, ax
    mov     al, [es:bx]
    xor     ah, ah
    mov     bl, 6
    mul     bl
    mov     di, gx_nodes
    add     di, ax
    pop     bx
    pop     ax
    ret

; ---------------------------------------------------------------------------
; gxFold -- open the group the light is on, or fold it away
; ---------------------------------------------------------------------------
gxFold:
    push    ax
    push    bx
    mov     al, [es:GX_SEL]
    xor     ah, ah
    mov     bx, GX_VIS
    add     bx, ax
    mov     al, [es:bx]             ; the node number
    xor     ah, ah
    mov     bx, GX_EXP
    add     bx, ax
    mov     al, [es:bx]
    xor     al, 1
    mov     [es:bx], al
    call    gxVisible
    call    gxFollow
    pop     bx
    pop     ax
    ret

; ---------------------------------------------------------------------------
; gxDraw -- everything
; ---------------------------------------------------------------------------
gxDraw:
    call    gxVisible
    call    gxClear
    call    gxFrame
    call    gxDrawLeft
    call    gxDrawRight
    call    gxHint
    ret

; ---------------------------------------------------------------------------
; gxClear -- the whole screen in the paper colour. Seventy-nine cells a row and
; the last column poked, because a console that writes the last cell of the
; last row scrolls the screen.
; ---------------------------------------------------------------------------
gxClear:
    push    ax
    push    bx
    push    cx
    push    dx
    xor     dh, dh
.row:
    xor     dl, dl
    mov     cx, 79
    mov     al, ' '
    mov     bl, SU_PAPER
    call    suBar
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
    pop     dx
    pop     cx
    pop     bx
    pop     ax
    ret

; ---------------------------------------------------------------------------
; gxFrame -- the outer border with the machine's name in it, the two panes, and
; the box the key hints live in.
; ---------------------------------------------------------------------------
gxFrame:
    push    ax
    push    bx
    push    cx
    push    dx
    push    si
    xor     dh, dh
    xor     dl, dl
    mov     cx, 80
    mov     al, G_HBAR
    mov     bl, SU_SEL
    call    suBar
    xor     dh, dh
    xor     dl, dl
    mov     al, G_TL
    mov     bl, SU_SEL
    call    suPlot
    mov     dl, 79
    xor     dh, dh
    mov     al, G_TR
    mov     bl, SU_SEL
    call    suPlot
    xor     dh, dh
    mov     dl, 2
    call    suGoto
    mov     si, msg_gx_title
    mov     bl, SU_HEAD
    call    suPrint
    mov     dh, GX_TOP_I
.side:
    xor     dl, dl
    mov     al, G_VBAR
    mov     bl, SU_SEL
    call    suPlot
    mov     dl, 79
    mov     al, G_VBAR
    mov     bl, SU_SEL
    call    suPlot
    inc     dh
    cmp     dh, GX_BOT_I
    jb      .side
    mov     dh, GX_BOT_I
    xor     dl, dl
    mov     cx, 80
    mov     al, G_HBAR
    mov     bl, SU_SEL
    call    suBar
    xor     dl, dl
    mov     dh, GX_BOT_I
    mov     al, G_BL
    mov     bl, SU_SEL
    call    suPlot
    mov     dl, 79
    mov     dh, GX_BOT_I
    mov     al, G_BR
    mov     bl, SU_SEL
    call    suPlot
    ; the two panes
    mov     dl, GX_LX
    mov     dh, GX_TOP_I
    mov     cx, GX_LR - GX_LX + 1
    mov     bh, GX_BOT_I - GX_TOP_I + 1
    call    gxBox
    mov     dl, GX_RX
    mov     dh, GX_TOP_I
    mov     cx, GX_RR - GX_RX + 1
    mov     bh, GX_BOT_I - GX_TOP_I + 1
    call    gxBox
    ; and the hints
    xor     dl, dl
    mov     dh, GX_HINT
    mov     cx, 80
    mov     bh, GX_HINT_H
    call    gxBox
    pop     si
    pop     dx
    pop     cx
    pop     bx
    pop     ax
    ret

; ---------------------------------------------------------------------------
; gxBox -- a single-line frame. dl/dh = the top-left, cl = the width,
; bh = the height.
; ---------------------------------------------------------------------------
gxBox:
    push    ax
    push    bx
    push    cx
    push    dx
    push    si
    mov     [es:GX_COL], dl
    mov     [es:GX_ROW], dh
    mov     [es:SU_DLG_W], cl
    mov     [es:SU_DLG_N], bh
    ; the top and bottom rules
    mov     dh, [es:GX_ROW]
    mov     dl, [es:GX_COL]
    inc     dl
    mov     cl, [es:SU_DLG_W]
    sub     cl, 2
    mov     al, G_HBAR
    mov     bl, SU_SEL
    call    suBar
    mov     dh, [es:GX_ROW]
    add     dh, [es:SU_DLG_N]
    dec     dh
    mov     dl, [es:GX_COL]
    inc     dl
    mov     cl, [es:SU_DLG_W]
    sub     cl, 2
    mov     al, G_HBAR
    mov     bl, SU_SEL
    call    suBar
    ; the four corners
    mov     dh, [es:GX_ROW]
    mov     dl, [es:GX_COL]
    mov     al, G_TL
    mov     bl, SU_SEL
    call    suPoke
    mov     dh, [es:GX_ROW]
    mov     dl, [es:GX_COL]
    add     dl, [es:SU_DLG_W]
    dec     dl
    mov     al, G_TR
    mov     bl, SU_SEL
    call    suPoke
    mov     dh, [es:GX_ROW]
    add     dh, [es:SU_DLG_N]
    dec     dh
    mov     dl, [es:GX_COL]
    mov     al, G_BL
    mov     bl, SU_SEL
    call    suPoke
    mov     dh, [es:GX_ROW]
    add     dh, [es:SU_DLG_N]
    dec     dh
    mov     dl, [es:GX_COL]
    add     dl, [es:SU_DLG_W]
    dec     dl
    mov     al, G_BR
    mov     bl, SU_SEL
    call    suPoke
    ; the sides
    mov     dh, [es:GX_ROW]
    inc     dh
.sides:
    mov     al, [es:GX_ROW]
    add     al, [es:SU_DLG_N]
    dec     al
    cmp     dh, al
    jae     .out
    mov     dl, [es:GX_COL]
    mov     al, G_VBAR
    mov     bl, SU_SEL
    call    suPoke
    mov     dl, [es:GX_COL]
    add     dl, [es:SU_DLG_W]
    dec     dl
    mov     al, G_VBAR
    mov     bl, SU_SEL
    call    suPoke
    inc     dh
    jmp     .sides
.out:
    pop     si
    pop     dx
    pop     cx
    pop     bx
    pop     ax
    ret

; ---------------------------------------------------------------------------
; gxDrawLeft -- the tree, and the scrollbar beside it
; ---------------------------------------------------------------------------
gxDrawLeft:
    push    ax
    push    bx
    push    cx
    push    dx
    mov     dh, GX_FIRST
.blank:
    mov     dl, GX_CX
    mov     cx, GX_SBAR - GX_CX
    mov     al, ' '
    mov     bl, SU_PAPER
    call    suBar
    inc     dh
    cmp     dh, GX_LAST
    jbe     .blank
    call    gxScrollBar
    mov     al, [es:GX_TOP]
    mov     [es:GX_ROW], al
    mov     byte [es:GX_NODE], GX_FIRST
.row:
    mov     al, [es:GX_ROW]
    cmp     al, [es:GX_NVIS]
    jae     .done
    mov     al, [es:GX_NODE]
    cmp     al, GX_LAST
    ja      .done
    call    gxRow
    inc     byte [es:GX_ROW]
    inc     byte [es:GX_NODE]
    jmp     .row
.done:
    pop     dx
    pop     cx
    pop     bx
    pop     ax
    ret

; ---------------------------------------------------------------------------
; gxRow -- one line of the tree: the bar, the indent, a group's sign, the name
; ---------------------------------------------------------------------------
gxRow:
    push    ax
    push    bx
    push    cx
    push    dx
    push    si
    push    di
    mov     al, [es:GX_ROW]
    xor     ah, ah
    mov     bx, GX_VIS
    add     bx, ax
    mov     al, [es:bx]
    xor     ah, ah
    mov     bl, 6
    mul     bl
    mov     si, gx_nodes
    add     si, ax
    ; the bar: the selected row is the green one
    mov     bl, SU_PAPER
    mov     al, [es:GX_ROW]
    cmp     al, [es:GX_SEL]
    jne     .ink
    mov     bl, SU_GREEN
.ink:
    push    bx
    mov     dl, GX_CX
    mov     dh, [es:GX_NODE]
    mov     cx, GX_SBAR - GX_CX
    mov     al, ' '
    call    suBar
    pop     bx
    ; the indent is two columns a level
    mov     al, [si]
    xor     ah, ah
    add     ax, ax
    add     al, GX_CX
    mov     [es:GX_COL], al
    ; a group carries its sign
    mov     al, [si + 1]
    test    al, KIND_GROUP
    jz      .name
    mov     al, [es:GX_ROW]
    xor     ah, ah
    mov     bx, GX_VIS
    add     bx, ax
    mov     al, [es:bx]
    xor     ah, ah
    mov     bx, GX_EXP
    add     bx, ax
    mov     al, '+'
    cmp     byte [es:bx], 0
    je      .sign
    mov     al, '-'
.sign:
    mov     dl, [es:GX_COL]
    mov     dh, [es:GX_NODE]
    mov     bl, SU_PAPER
    mov     ah, [es:GX_ROW]
    cmp     ah, [es:GX_SEL]
    jne     .signplot
    mov     bl, SU_GREEN
.signplot:
    call    suPlot
.name:
    mov     dl, [es:GX_COL]
    add     dl, 2
    mov     dh, [es:GX_NODE]
    call    suGoto
    mov     si, [si + 2]
    mov     bl, SU_PAPER
    mov     al, [es:GX_ROW]
    cmp     al, [es:GX_SEL]
    jne     .print
    mov     bl, SU_GREEN
.print:
    call    suPrint
    pop     di
    pop     si
    pop     dx
    pop     cx
    pop     bx
    pop     ax
    ret

; ---------------------------------------------------------------------------
; gxScrollBar -- the arrows and the thumb. There is no light pen here: the
; thumb is a block, and it sits where the window sits in the tree.
; ---------------------------------------------------------------------------
gxScrollBar:
    push    ax
    push    bx
    push    cx
    push    dx
    mov     dl, GX_SBAR
    mov     dh, GX_FIRST
    mov     al, 0x1E                 ; the up arrow
    mov     bl, SU_SEL
    call    suPlot
    mov     dl, GX_SBAR
    mov     dh, GX_LAST
    mov     al, 0x1F                 ; and the down arrow
    mov     bl, SU_SEL
    call    suPlot
    mov     dh, GX_FIRST + 1
.track:
    mov     dl, GX_SBAR
    mov     al, 0xB0                 ; the shade the track is drawn in
    mov     bl, SU_SEL
    call    suPlot
    inc     dh
    cmp     dh, GX_LAST - 1
    jbe     .track
    ; where the thumb goes: the window's share of the tree, at least two rows
    mov     al, GX_FIRST + 1
    mov     [es:GX_COL], al
    mov     al, [es:GX_NVIS]
    cmp     al, GX_LAST - GX_FIRST + 1
    jbe     .draw
    mov     al, [es:GX_TOP]
    mov     bl, 13                   ; the track, less the thumb
    mul     bl
    mov     bl, [es:GX_NVIS]
    sub     bl, GX_LAST - GX_FIRST + 1
    jz      .draw
    div     bl
    add     al, GX_FIRST + 1
    mov     [es:GX_COL], al
.draw:
    mov     dh, [es:GX_COL]
    mov     cx, 2
.thumb:
    mov     dl, GX_SBAR
    mov     al, 0xDB
    mov     bl, SU_SEL
    call    suPlot
    inc     dh
    cmp     dh, GX_LAST - 1
    ja      .out
    loop    .thumb
.out:
    pop     dx
    pop     cx
    pop     bx
    pop     ax
    ret

; ---------------------------------------------------------------------------
; gxMonitor -- the Hardware Monitor page: one line per core, read live.
;
; Every other page is a table in the ROM. This one is not, so the page's detail
; pointer is GX_LIVE and gxDrawRight calls here instead. The numbers come out of
; the card's own register file -- select a core on 0x5A5, read its usage on
; 0x5A6 and its clock on 0x5A7 -- which is the same door a real board's hardware
; monitor chip would be behind.
; ---------------------------------------------------------------------------
gxMonitor:
    push    ax
    push    bx
    push    cx
    push    dx
    push    si
    mov     byte [es:CON_ATTR], SU_PAPER ; the numbers print through the console
    ; the title, flush right, and the rule under it -- the same furniture the
    ; static pages get from gxDrawRight
    mov     si, msg_gx_hwmon
    call    gxStrLen
    mov     dl, GX_RR
    sub     dl, al
    dec     dl
    mov     dh, GX_FIRST
    call    suGoto
    mov     si, msg_gx_hwmon
    mov     bl, SU_HEAD
    call    suPrint
    mov     dh, GX_FIRST + 1
    mov     dl, GX_TX
    mov     cx, GX_TW
    mov     al, G_HBAR
    mov     bl, SU_SEL
    call    suBar
    mov     byte [es:GX_ROW], GX_FIRST + 2
    mov     byte [es:GX_NODE], 1        ; the core being printed
.core:
    mov     dx, 0x5A0                   ; how many cores are there
    in      al, dx
    xor     ah, ah
    mov     cl, al                      ; cl = how many cores there are
    mov     al, [es:GX_NODE]
    cmp     al, cl
    ja      .out
    mov     dx, 0x5A5                   ; select it
    out     dx, al
    mov     dx, 0x5A6                   ; what it did with its frame
    in      al, dx
    mov     ch, al
    mov     dx, 0x5A7                   ; and the clock it reports
    in      al, dx
    mov     bh, al
    ; "Core n     usage NN %     NN MHz"
    mov     dl, GX_TX
    mov     dh, [es:GX_ROW]
    call    suGoto
    mov     si, msg_hw_core
    mov     bl, SU_PAPER
    call    suPrint
    mov     al, [es:GX_NODE]
    add     al, '0'
    call    putc
    mov     si, msg_hw_usage
    call    suPrint
    mov     al, ch
    xor     ah, ah
    call    putn
    mov     si, msg_hw_pct
    call    suPrint
    mov     al, bh                   ; the clock, in whole MHz
    xor     ah, ah
    call    putn
    mov     si, msg_hw_mhz
    call    suPrint
    inc     byte [es:GX_ROW]
    inc     byte [es:GX_NODE]
    jmp     .core
.out:
    ; and what the emulation switch is set to, under the list
    mov     dl, GX_TX
    mov     dh, [es:GX_ROW]
    inc     dh
    call    suGoto
    mov     si, msg_hw_all
    call    suPrint
    ; the nominal, which is what the setup utility says, beside the measured
    ; figure above -- the two are not the same number, and that is the point
    mov     al, 0x36
    call    cmos_read
    cmp     al, 4
    jbe     .nom
    mov     al, 2
.nom:
    xor     ah, ah
    mov     si, ax
    mov     al, [cs:hw_mhz + si]
    xor     ah, ah
    call    putn
    mov     si, msg_hw_nom
    call    suPrint
    mov     dx, 0x5A9
    in      al, dx
    mov     si, msg_hw_off
    test    al, al
    jz      .say
    mov     si, msg_hw_on
.say:
    call    suPrint
    pop     si
    pop     dx
    pop     cx
    pop     bx
    pop     ax
    ret

; ---------------------------------------------------------------------------
; gxDrawRight -- the page for the selected node: its title at the right of the
; pane, a rule under it, then the lines the detail table points at.
; ---------------------------------------------------------------------------
gxDrawRight:
    push    ax
    push    bx
    push    cx
    push    dx
    push    si
    push    di
    mov     dh, GX_FIRST
.blank:
    mov     dl, GX_TX
    mov     cx, GX_TW
    mov     al, ' '
    mov     bl, SU_PAPER
    call    suBar
    inc     dh
    cmp     dh, GX_LAST
    jbe     .blank
    call    gxNodeOf
    mov     si, [di + 4]
    test    si, si
    jz      .out
    cmp     word [si + 2], GX_LIVE      ; the page starts with the sentinel
    jne     .static
    call    gxMonitor
    jmp     .out
.static:
    ; the title, flush right
    mov     si, [si]
    call    gxStrLen
    mov     dl, GX_RR
    sub     dl, al
    dec     dl
    mov     dh, GX_FIRST
    call    suGoto
    mov     bl, SU_HEAD
    call    suPrint
    ; the rule under it
    call    gxNodeOf
    mov     si, [di + 4]
    mov     dh, GX_FIRST + 1
    mov     dl, GX_TX
    mov     cx, GX_TW
    mov     al, G_HBAR
    mov     bl, SU_SEL
    call    suBar
    ; the lines
    add     si, 2
    mov     byte [es:GX_LINE], GX_FIRST + 2
.line:
    mov     bx, [si]
    test    bx, bx
    jz      .out
    mov     al, [es:GX_LINE]
    cmp     al, GX_LAST
    ja      .out
    mov     dl, GX_TX
    mov     dh, al
    call    suGoto
    mov     si, bx
    mov     bl, SU_PAPER
    call    suPrint
    call    gxNodeOf
    mov     si, [di + 4]
    mov     al, [es:GX_LINE]
    sub     al, GX_FIRST + 2
    inc     al
    xor     ah, ah
    add     ax, ax
    add     si, ax
    add     si, 2
    inc     byte [es:GX_LINE]
    jmp     .line
.out:
    pop     di
    pop     si
    pop     dx
    pop     cx
    pop     bx
    pop     ax
    ret

; ---------------------------------------------------------------------------
; gxHint -- the three lines in the box at the bottom
; ---------------------------------------------------------------------------
gxHint:
    push    ax
    push    bx
    push    dx
    push    si
    mov     dl, 2
    mov     dh, GX_HINT + 1
    call    suGoto
    mov     si, msg_gx_hint1
    mov     bl, SU_PAPER
    call    suPrint
    mov     dl, 2
    mov     dh, GX_HINT + 2
    call    suGoto
    mov     si, msg_gx_hint2
    call    suPrint
    mov     dl, 2
    mov     dh, GX_HINT + 3
    call    suGoto
    mov     si, msg_gx_hint3
    call    suPrint
    pop     si
    pop     dx
    pop     bx
    pop     ax
    ret

; gxStrLen: si = a string, al = its length
gxStrLen:
    push    si
    push    bx
    xor     bl, bl
.loop:
    lodsb
    test    al, al
    jz      .done
    inc     bl
    jmp     .loop
.done:
    mov     al, bl
    pop     bx
    pop     si
    ret

; ---------------------------------------------------------------------------
; the tree: depth, kind, name, detail
; ---------------------------------------------------------------------------
%macro NODE 4
    db %1, %2
    dw %3, %4
%endmacro

gx_nodes:
    NODE 0, KIND_GROUP,             msg_gx_system,   gx_d_system
    NODE 1, 0,                      msg_gx_sysinfo,  gx_d_sysinfo
    NODE 1, 0,                      msg_gx_cpu,      gx_d_cpu
    NODE 1, 0,                      msg_gx_mem,      gx_d_mem
    NODE 1, KIND_GROUP,             msg_gx_datetime, gx_d_datetime
    NODE 2, KIND_EDIT | 0,          msg_gx_month,    gx_d_month
    NODE 2, KIND_EDIT | 1,          msg_gx_day,      gx_d_day
    NODE 2, KIND_EDIT | 2,          msg_gx_year,     gx_d_year
    NODE 2, KIND_EDIT | 3,          msg_gx_hour,     gx_d_hour
    NODE 2, KIND_EDIT | 4,          msg_gx_minute,   gx_d_minute
    NODE 2, KIND_EDIT | 5,          msg_gx_second,   gx_d_second
    NODE 1, KIND_EDIT | 8,          msg_gx_bootseq,  gx_d_bootseq
    NODE 0, KIND_GROUP,             msg_gx_drives,   gx_d_drives
    NODE 1, KIND_EDIT | 6,          msg_gx_diskette, gx_d_diskette
    NODE 1, KIND_EDIT | 7,          msg_gx_drive0,   gx_d_drive0
    NODE 1, 0,                      msg_gx_drive1,   gx_d_drive1
    NODE 0, KIND_GROUP,             msg_gx_onboard,  gx_d_onboard
    NODE 1, 0,                      msg_gx_serial,   gx_d_serial
    NODE 1, 0,                      msg_gx_audio,    gx_d_audio
    NODE 0, KIND_GROUP,             msg_gx_emu,      gx_d_emu
    NODE 1, KIND_EDIT | 11,         msg_gx_emusw,    gx_d_emusw
    NODE 1, KIND_EDIT | 12,         msg_gx_batt,     gx_d_batt
    NODE 1, KIND_EDIT | 13,         msg_gx_freq,     gx_d_freq
    NODE 1, KIND_EDIT | 14,         msg_gx_repeat,   gx_d_repeat
    NODE 1, 0,                      msg_gx_hwmon,    gx_d_hwmon
    NODE 0, 0,                      msg_gx_video,    gx_d_video
    NODE 0, 0,                      msg_gx_perf,     gx_d_perf
    NODE 0, 0,                      msg_gx_security, gx_d_security
    NODE 0, 0,                      msg_gx_power,    gx_d_power
    NODE 0, 0,                      msg_gx_maint,    gx_d_maint
    NODE 0, 0,                      msg_gx_post,     gx_d_post

; ---------------------------------------------------------------------------
; what the right pane says. Each table is a title, then its lines, then a zero.
; ---------------------------------------------------------------------------
gx_d_system:    dw msg_gx_system, l_sys1, 0
gx_d_sysinfo:   dw msg_gx_sysinfo, l_sys1, l_blank, l_si2, l_si3, l_si4, l_si5, l_blank, l_fixed, 0
gx_d_cpu:       dw msg_gx_cpu, l_cpu1, l_cpu2, l_cpu3, l_cpu4, l_cpu5, l_blank, l_fixed, 0
gx_d_mem:       dw msg_gx_mem, l_mem1, l_mem2, l_mem3, l_mem4, l_blank, l_fixed, 0
gx_d_datetime:  dw msg_gx_datetime, l_dt1, l_dt2, l_blank, l_dt3, l_dt4, 0
gx_d_month:     dw msg_gx_month, l_mo1, l_blank, l_edit, 0
gx_d_day:       dw msg_gx_day, l_dy1, l_blank, l_edit, 0
gx_d_year:      dw msg_gx_year, l_yr1, l_yr2, l_blank, l_edit, 0
gx_d_hour:      dw msg_gx_hour, l_hr1, l_blank, l_edit, 0
gx_d_minute:    dw msg_gx_minute, l_mn1, l_blank, l_edit, 0
gx_d_second:    dw msg_gx_second, l_sc1, l_blank, l_edit, 0
gx_d_bootseq:   dw msg_gx_bootseq, l_bs1, l_blank, l_bs2, l_bs3, l_bs4, l_blank, l_edit, 0
gx_d_drives:    dw msg_gx_drives, l_drv1, l_drv2, l_drv3, 0
gx_d_diskette:  dw msg_gx_diskette, l_dk1, l_dk2, l_blank, l_dk3, l_blank, l_dk4, l_blank, l_edit, 0
gx_d_drive0:    dw msg_gx_drive0, l_d01, l_d02, l_d03, l_blank, l_d04, l_d05, l_d06, l_d07, l_blank, l_d08, l_blank, l_d09, l_d10, l_d11, l_d12, 0
gx_d_drive1:    dw msg_gx_drive1, l_d1a, l_blank, l_fixed, 0
gx_d_onboard:   dw msg_gx_onboard, l_ob1, l_blank, l_fixed, 0
gx_d_serial:    dw msg_gx_serial, l_sr1, l_sr2, l_blank, l_fixed, 0
gx_d_audio:     dw msg_gx_audio, l_au1, l_blank, l_fixed, 0
gx_d_emu:       dw msg_gx_emu, l_em1, l_em2, l_blank, l_em3, 0
gx_d_emusw:     dw msg_gx_emusw, l_emsw1, l_blank, l_edit, 0
gx_d_batt:      dw msg_gx_batt, l_bt1, l_bt2, l_blank, l_edit, 0
gx_d_freq:      dw msg_gx_freq, l_fq1, l_fq2, l_blank, l_edit, 0
gx_d_repeat:    dw msg_gx_repeat, l_kr1, l_kr2, l_blank, l_edit, 0
gx_d_hwmon:     dw msg_gx_hwmon, GX_LIVE, 0
gx_d_video:     dw msg_gx_video, l_vd1, l_vd2, l_vd3, l_blank, l_fixed, 0
gx_d_perf:      dw msg_gx_perf, l_pf1, l_blank, l_fixed, 0
gx_d_security:  dw msg_gx_security, l_se1, l_se2, l_blank, l_fixed, 0
gx_d_power:     dw msg_gx_power, l_pw1, l_blank, l_fixed, 0
gx_d_maint:     dw msg_gx_maint, l_mt1, l_mt2, l_blank, l_fixed, 0
gx_d_post:      dw msg_gx_post, l_po1, l_po2, l_blank, l_fixed, 0

; ---------------------------------------------------------------------------
; the words
; ---------------------------------------------------------------------------
msg_gx_title:      db " JSW System 80386 ", 0
msg_gx_hint1:      db "Press Up/Down arrows to select a different field", 0
msg_gx_hint2:      db "Press Left/Right keys to expand or collapse a group", 0
msg_gx_hint3:      db "Press Esc to exit Setup", 0

msg_hw_core:       db "Core ", 0
msg_hw_usage:      db "     usage ", 0
msg_hw_pct:        db " %     ", 0
msg_hw_mhz:        db " MHz", 0
msg_hw_all:        db "Nominal ", 0
msg_hw_nom:        db " MHz. Emulation is ", 0
msg_hw_on:         db "on.", 0
msg_hw_off:        db "off.", 0
msg_gx_emu:        db "HW Emulation", 0
msg_gx_emusw:      db "Emulation", 0
msg_gx_batt:       db "BIOS Battery", 0
msg_gx_freq:       db "CPU Frequency", 0
msg_gx_hwmon:      db "Hardware Monitor", 0
msg_gx_repeat:     db "Key Repeat", 0
msg_gx_system:     db "System", 0
msg_gx_sysinfo:    db "System Info", 0
msg_gx_cpu:        db "Processor Info", 0
msg_gx_mem:        db "Memory Info", 0
msg_gx_datetime:   db "Date/Time", 0
msg_gx_month:      db "Month", 0
msg_gx_day:        db "Day", 0
msg_gx_year:       db "Year", 0
msg_gx_hour:       db "Hour", 0
msg_gx_minute:     db "Minute", 0
msg_gx_second:     db "Second", 0
msg_gx_bootseq:    db "Boot Sequence", 0
msg_gx_drives:     db "Drives", 0
msg_gx_diskette:   db "Diskette Drive", 0
msg_gx_drive0:     db "Drive 0: SATA-0", 0
msg_gx_drive1:     db "Drive 1: SATA-1", 0
msg_gx_onboard:    db "Onboard Devices", 0
msg_gx_serial:     db "Serial Port", 0
msg_gx_audio:      db "Audio Controller", 0
msg_gx_video:      db "Video", 0
msg_gx_perf:       db "Performance", 0
msg_gx_security:   db "Security", 0
msg_gx_power:      db "Power Management", 0
msg_gx_maint:      db "Maintenance", 0
msg_gx_post:       db "POST Behavior", 0

l_em1:   db "This board is a program. Everything it", 0
l_em2:   db "pretends to be is listed below.", 0
l_em3:   db "Emulation is the master switch.", 0
l_emsw1: db "Off: the cores report no clock and the", 0
l_bt1:   db "The CMOS battery keeps the clock and", 0
l_bt2:   db "these settings across a power cut.", 0
l_fq1:   db "The clock each core reports. It is not", 0
l_fq2:   db "measured: it wanders by a MHz either way.", 0
l_kr1:   db "A held key repeats, the way the keyboard", 0
l_kr2:   db "itself does. This is how often.", 0
l_blank:  db "", 0
l_fixed:  db "None of these fields are changeable.", 0
l_edit:   db "Enter edits it; Esc leaves it alone.", 0

l_sys1:   db "System            = JSW 80386", 0
l_si2:    db "BIOS Version          = A07 (03/31/06)", 0
l_si3:    db "Service Tag           = J1Q24BX", 0
l_si4:    db "Express Service Code  = 41463099501", 0
l_si5:    db "Asset Tag             = {none}", 0
l_cpu1:   db "Processor Type     = JSW 80386", 0
l_cpu2:   db "Processor Speed    = 33 MHz", 0
l_cpu3:   db "Processor Bus      = 32-bit registers, 16-bit bus", 0
l_cpu4:   db "Cache              = not fitted", 0
l_cpu5:   db "Coprocessor        = 80387 socket, empty", 0
l_mem1:   db "Installed Memory   = 640 KB", 0
l_mem2:   db "Memory Type        = conventional", 0
l_mem3:   db "Cores              = see below", 0
l_mem4:   db "Top of memory      = 0xA0000", 0
l_dt1:    db "These fields set the clock the BIOS keeps in", 0
l_dt2:    db "the CMOS, on the battery, across a power cut.", 0
l_dt3:    db "Enter edits a field. The value is written when", 0
l_dt4:    db "you leave through the exit prompt.", 0
l_mo1:    db "The month, as a number from 1 to 12.", 0
l_dy1:    db "The day of the month, 1 to 31.", 0
l_yr1:    db "The year, as two digits. The century is not", 0
l_yr2:    db "stored: this board's RTC has no century byte.", 0
l_hr1:    db "The hour, on the 24-hour clock.", 0
l_mn1:    db "The minute.", 0
l_sc1:    db "The second.", 0
l_bs1:    db "The order the BIOS tries the drives in.", 0
l_bs2:    db "A: then C:   floppy first, then the disk", 0
l_bs3:    db "C: only      the disk, and nothing else", 0
l_bs4:    db "C: then A:   the disk first", 0
l_drv1:   db "These fields control the drives this board can", 0
l_drv2:   db "see. A field that is off is a controller the", 0
l_drv3:   db "BIOS does not probe, which makes POST quicker.", 0
l_dk1:    db "This field allows the user to enable or disable", 0
l_dk2:    db "the diskette drive controller.", 0
l_dk3:    db "The factory default setting is 1.2M, 5.25 in.", 0
l_dk4:    db "Controller details:  NEC 765, port 0x3F0", 0
l_d01:    db "This field allows the user to enable or disable", 0
l_d02:    db "an ATA or SATA device (such as Hard Drive, CD", 0
l_d03:    db "Drive, or DVD Drive).", 0
l_d04:    db "Off = The interface is disabled and an attached", 0
l_d05:    db "      device is not usable", 0
l_d06:    db "On  = The interface is enabled and an attached", 0
l_d07:    db "      device is usable", 0
l_d08:    db "The factory default setting is On", 0
l_d09:    db "Controller details:", 0
l_d10:    db " Controller = Serial ATA      Port = SATA-0", 0
l_d11:    db "Drive ID   = JSW STAGED DISK", 0
l_d12:    db "Capacity   = 64 KB   BIOS = System BIOS", 0
l_d1a:    db "There is no second drive on this channel.", 0
l_ob1:    db "The devices soldered to the board.", 0
l_sr1:    db "Serial Port        = COM1, 0x3F8, IRQ4", 0
l_sr2:    db "The port a terminal or a debug cable would use.", 0
l_au1:    db "Audio Controller   = not fitted", 0
l_vd1:    db "Video Controller   = JSW VGA, text mode", 0
l_vd2:    db "Video Memory       = 4 KB of text plane", 0
l_vd3:    db "Mode               = 80 x 30, 16 colours", 0
l_pf1:    db "The clock this board counts instructions with.", 0
l_se1:    db "Admin Password     = {none}", 0
l_se2:    db "System Password    = {none}", 0
l_pw1:    db "Power Management   = disabled", 0
l_mt1:    db "Service Tag        = J1Q24BX", 0
l_mt2:    db "Asset Tag          = {none}", 0
l_po1:    db "Fast Boot          = off", 0
l_po2:    db "Numlock            = on", 0
