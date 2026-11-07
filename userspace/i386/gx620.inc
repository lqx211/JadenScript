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

; 0x560..0x56C is the setup buffer's own memory, forty-five bytes of it, so the
; tree's eight bytes of state start above it: they used to sit at 0x560 and the
; redraw walked straight through the socket kinds and the first four forms.
GX_SEL   equ 0x578                  ; which visible row is selected
GX_TOP   equ 0x579                  ; the first visible row shown
GX_NVIS  equ 0x57A                  ; how many rows the open tree has
GX_HIDE  equ 0x57B                  ; while walking: the depth folded away
GX_NODE  equ 0x57C                  ; the node the drawing loops are on
GX_ROW   equ 0x57D                  ; ... and the screen row
GX_COL   equ 0x57E                  ; ... and the column
GX_LINE  equ 0x57F                  ; ... and the line of a detail being printed
GX_EXP   equ 0x580                  ; one byte per node: a group is open at 1
GX_VIS   equ 0x5C0                  ; 64 bytes: the node index of each visible row
GX_NODES equ 48
GX_LIVE  equ 0xFFFE                 ; a page that reads the hardware, not the ROM
GX_LIVEDRV equ 0xFFFD               ; ... the drive's identity, which it answers
GX_LIVEPCI equ 0xFFFC               ; ... the PCI bus, which it walks
GX_LIVEMEM equ 0xFFFB               ; ... the memory the panel installed

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
GX_HINT  equ 25                     ; where the hint box wants to be
GX_HINT_H equ 5

; The rest of the frame depends on the card, not on this listing: TurboWarp in
; a small window reports fewer cells than Scratch's own 60 x 22 stage, and the
; frame's fixed 80 x 30 is a frame with its right border and its hint box off
; the side of the screen. gxLayout measures it once, on the way in, from the
; geometry POST already read out of the card.
GX_M_RR   equ 0x5B0                 ; the frame's right edge
GX_M_TW   equ 0x5B1                 ; the right pane's text width
GX_M_BOT  equ 0x5B2                 ; ... its bottom border row
GX_M_LAST equ 0x5B3                 ; ... the last row a pane can use
GX_M_SPAN equ 0x5B4                 ; ... last - first: a full window, less one
GX_M_WIN  equ 0x5B5                 ; ... and how many rows that is
GX_M_HINT equ 0x5B6                 ; ... the hint box's top row, or 0 for none
GX_M_LM1  equ 0x5B7                 ; ... and one row above the bottom border
GX_M_COLS equ 0x5B8                 ; ... the card's width in cells

KIND_GROUP equ 0x40
KIND_EDIT  equ 0x80

; ---------------------------------------------------------------------------
; gxLayout -- measure the frame against the card the picture is on.
;
; The tree was drawn for an 80 x 30 plane and a card that showed all of it. A
; card is not always that size: POST asks the card how many cells it can show
; (BDA_VIDCOLS/BDA_VIDROWS) and this turns the answer into the six numbers the
; frame is built from. The right pane gives up width first -- its text is a
; caption and a rule -- and a card shorter than the frame loses the hint box
; rather than the rows the tree needs.
; ---------------------------------------------------------------------------
gxLayout:
    push    ax
    push    bx
    push    cx
    mov     byte [es:CON_T], 0
    mov     al, [es:BDA_VIDROWS]
    dec     al
    mov     byte [es:CON_B], al     ; printing may not run off the card
    mov     byte [es:CON_W], COLS
    mov     al, [es:BDA_VIDCOLS]
    test    al, al
    jnz     .havecols
    mov     al, COLS                ; a card that says nothing has the lot
.havecols:
    cmp     al, COLS
    jbe     .narrow
    mov     al, COLS
.narrow:
    mov     [es:GX_M_COLS], al
    sub     al, 2                   ; the frame's own borders cost a column
    cmp     al, GX_RR               ; a card wide enough keeps the pane's width
    jb      .fit
    mov     al, GX_RR
.fit:
    mov     [es:GX_M_RR], al
    mov     bl, al
    sub     bl, GX_TX
    mov     [es:GX_M_TW], bl
    mov     al, [es:BDA_VIDROWS]
    test    al, al
    jnz     .haverows
    mov     al, ROWS
.haverows:
    cmp     al, ROWS
    jbe     .short
    mov     al, ROWS
.short:
    sub     al, 2
    cmp     al, GX_BOT_I            ; the bottom border row
    jb      .fitrow
    mov     al, GX_BOT_I
.fitrow:
    mov     [es:GX_M_BOT], al
    dec     al
    mov     [es:GX_M_LAST], al
    mov     bl, al
    dec     bl
    mov     [es:GX_M_LM1], bl
    sub     al, GX_FIRST
    mov     [es:GX_M_SPAN], al
    inc     al
    mov     [es:GX_M_WIN], al
    ; the hint box is six rows tall including its border; below the frame it
    ; needs GX_HINT rows to itself, and a card that short does not have them
    mov     byte [es:GX_M_HINT], 0
    mov     al, [es:GX_M_BOT]
    add     al, 3
    mov     bl, al
    add     bl, GX_HINT_H
    cmp     bl, [es:BDA_VIDROWS]
    ja      .out
    mov     [es:GX_M_HINT], al
.out:
    pop     cx
    pop     bx
    pop     ax
    ret

; ---------------------------------------------------------------------------
; gxEnter -- the setup utility. Returns when the user has decided to leave.
; ---------------------------------------------------------------------------
gxEnter:
    push    ax
    mov     ax, cs
    mov     ds, ax
    pop     ax
    mov     byte [es:CON_ATTR], SU_PAPER
    call    gxLayout
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
    and     al, 0x1F                ; the setting's number
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
    ; a save that changed the memory only takes effect at a reboot, and a board
    ; that quietly kept the old memory would be lying about its own setup
    call    suMemDiffers
    test    al, al
    jz      .out
    call    suReboot
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
    add     ah, [es:GX_M_SPAN]
    cmp     al, ah
    jbe     .out
    mov     al, [es:GX_SEL]
    sub     al, [es:GX_M_SPAN]
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
    mov     dl, [es:GX_M_RR]
    mov     al, G_VBAR
    mov     bl, SU_SEL
    call    suPlot
    inc     dh
    cmp     dh, [es:GX_M_BOT]
    jb      .side
    mov     dh, [es:GX_M_BOT]
    xor     dl, dl
    mov     cl, [es:GX_M_COLS]
    xor     ch, ch
    mov     al, G_HBAR
    mov     bl, SU_SEL
    call    suBar
    xor     dl, dl
    mov     dh, [es:GX_M_BOT]
    mov     al, G_BL
    mov     bl, SU_SEL
    call    suPlot
    mov     dl, [es:GX_M_RR]
    mov     dh, [es:GX_M_BOT]
    mov     al, G_BR
    mov     bl, SU_SEL
    call    suPlot
    ; the two panes
    mov     dl, GX_LX
    mov     dh, GX_TOP_I
    mov     cx, GX_LR - GX_LX + 1
    mov     bh, [es:GX_M_BOT]
    call    gxBox
    mov     dl, GX_RX
    mov     dh, GX_TOP_I
    mov     cl, [es:GX_M_RR]
    sub     cl, GX_RX - 1
    mov     ch, 0
    mov     bh, [es:GX_M_BOT]
    call    gxBox
    ; and the hints, if this card is tall enough for them
    mov     al, [es:GX_M_HINT]
    test    al, al
    jz      .nohint
    xor     dl, dl
    mov     dh, al
    mov     cl, [es:GX_M_COLS]
    xor     ch, ch
    mov     bh, GX_HINT_H
    call    gxBox
.nohint:
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
    cmp     dh, [es:GX_M_LAST]
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
    cmp     al, [es:GX_M_LAST]
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
    mov     dh, [es:GX_M_LAST]
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
    cmp     dh, [es:GX_M_LM1]
    jbe     .track
    ; where the thumb goes: the window's share of the tree, at least two rows
    mov     al, GX_FIRST + 1
    mov     [es:GX_COL], al
    mov     al, [es:GX_NVIS]
    cmp     al, [es:GX_M_WIN]
    jbe     .draw
    mov     al, [es:GX_TOP]
    mov     bl, 13                   ; the track, less the thumb
    mul     bl
    mov     bl, [es:GX_NVIS]
    sub     bl, [es:GX_M_WIN]
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
    cmp     dh, [es:GX_M_LM1]
    ja      .out
    loop    .thumb
.out:
    pop     dx
    pop     cx
    pop     bx
    pop     ax
    ret

; ---------------------------------------------------------------------------
; gxPci -- the PCI page: every function the firmware found, read back live.
;
; The records come from the POST-time scan (pci.inc wrote them at PCI_TAB), and
; each line's class comes from the bus itself: select the function, read
; register 2, and the top three bytes are base class, sub-class and programming
; interface. Nothing here is a table of what this board is supposed to have --
; a device that stopped answering would vanish from the page.
; ---------------------------------------------------------------------------
gxPci:
    push    ax
    push    bx
    push    cx
    push    dx
    push    si
    push    di
    mov     byte [es:CON_ATTR], SU_PAPER
    mov     si, msg_gx_pci
    call    gxStrLen
    mov     dl, [es:GX_M_RR]
    sub     dl, al
    dec     dl
    mov     dh, GX_FIRST
    call    suGoto
    mov     si, msg_gx_pci
    mov     bl, SU_HEAD
    call    suPrint
    mov     dh, GX_FIRST + 1
    mov     dl, GX_TX
    mov     cl, [es:GX_M_TW]
    mov     ch, 0
    mov     al, G_HBAR
    mov     bl, SU_SEL
    call    suBar
    mov     byte [es:GX_ROW], GX_FIRST + 2
    mov     byte [es:GX_NODE], 0        ; the record being printed
.rec:
    mov     al, [es:GX_NODE]
    cmp     al, [es:BDA_PCI_NDEV]
    jae     .out
    mov     bl, al
    xor     bh, bh
    shl     bl, 3                       ; eight bytes a record
    mov     di, PCI_TAB
    add     di, bx
    mov     dl, GX_TX
    mov     dh, [es:GX_ROW]
    call    suGoto
    mov     al, ' '
    call    putc
    mov     al, [es:di]                 ; the slot, as two digits
    call    puthex
    mov     al, ':'
    call    putc
    mov     al, [es:di + 1]             ; then the function
    call    puthex
    mov     al, ' '
    call    putc
    call    putc
    mov     al, [es:di + 3]             ; vendor, big-endian on screen
    call    puthex
    mov     al, [es:di + 2]
    call    puthex
    mov     al, ':'
    call    putc
    mov     al, [es:di + 5]
    call    puthex
    mov     al, [es:di + 4]
    call    puthex
    ; the class, out of the bus. The slot goes in bh because that is the
    ; register pciSelectSlot reads it from; dh is the screen row.
    mov     bh, [es:di]
    mov     bl, [es:di + 1]
    call    pciSelectSlot
    mov     dx, PCI_ADDR
    in      eax, dx
    or      eax, 0x08                   ; register 2: class and revision
    out     dx, eax
    mov     dx, PCI_DATA
    in      eax, dx
    shr     eax, 16                     ; drop the revision
    mov     [es:SU_OLD], ah             ; base and sub-class, for the line
    mov     [es:SU_OPT], al
    mov     dl, GX_TX + 18
    mov     dh, [es:GX_ROW]
    call    suGoto
    mov     al, '['
    call    putc
    mov     al, [es:SU_OLD]
    call    puthex
    mov     al, [es:SU_OPT]
    call    puthex
    mov     al, ']'
    call    putc
    inc     byte [es:GX_ROW]
    inc     byte [es:GX_NODE]
    jmp     .rec
.out:
    pop     di
    pop     si
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
; ---------------------------------------------------------------------------
; gxMemory -- the memory page, read out of the board rather than the ROM.
;
; Three numbers matter and all three come from the CMOS the setup wrote: how
; much base memory there is, how much the sockets add up to, and which storage
; that memory is held in. A page of fixed text that says "640 KB" on a board
; with sockets in it is worse than no page at all.
; ---------------------------------------------------------------------------
gxMemory:
    push    ax
    push    bx
    push    cx
    push    dx
    push    si
    mov     byte [es:CON_ATTR], SU_PAPER
    mov     si, msg_gx_mem
    call    gxStrLen
    mov     dl, [es:GX_M_RR]
    sub     dl, al
    dec     dl
    mov     dh, GX_FIRST
    call    suGoto
    mov     si, msg_gx_mem
    mov     bl, SU_HEAD
    call    suPrint
    mov     dh, GX_FIRST + 1
    mov     dl, GX_TX
    mov     cl, [es:GX_M_TW]
    mov     ch, 0
    mov     al, G_HBAR
    mov     bl, SU_SEL
    call    suBar
    ; ---- base memory
    mov     dl, GX_TX
    mov     dh, GX_FIRST + 2
    call    suGoto
    mov     si, msg_me_base
    mov     bl, SU_PAPER
    call    suPrint
    mov     ax, [es:BDA_MEMK]
    call    putn
    mov     si, msg_me_kb
    call    suPrint
    ; ---- extended, out of the battery
    mov     dl, GX_TX
    mov     dh, GX_FIRST + 3
    call    suGoto
    mov     si, msg_me_ext
    mov     bl, SU_PAPER
    call    suPrint
    mov     al, 0x17
    call    cmos_read
    mov     bl, al
    mov     al, 0x18
    call    cmos_read
    mov     bh, al
    mov     ax, bx
    or      ax, ax
    jz      .none
    call    putn
    mov     si, msg_me_kb
    call    suPrint
    jmp     .mode
.none:
    mov     si, msg_me_none
    mov     bl, SU_PAPER
    call    suPrint
.mode:
    ; ---- and where it is held
    mov     dl, GX_TX
    mov     dh, GX_FIRST + 4
    call    suGoto
    mov     si, msg_me_mode
    mov     bl, SU_PAPER
    call    suPrint
    mov     al, 0x3D
    call    cmos_read
    cmp     al, 1
    jne     .internal
    mov     si, msg_me_ext2
    jmp     .printmode
.internal:
    mov     si, msg_me_int2
.printmode:
    mov     bl, SU_PAPER
    call    suPrint
    ; ---- the sockets, one a line
    mov     byte [es:GX_ROW], GX_FIRST + 6
    xor     cx, cx                      ; the socket being printed
.slot:
    cmp     cx, 8
    jae     .out
    mov     dl, GX_TX
    mov     dh, [es:GX_ROW]
    call    suGoto
    mov     si, msg_me_slot
    mov     bl, SU_PAPER
    call    suPrint
    mov     ax, cx
    add     al, '0'
    call    putc
    mov     si, msg_me_arrow
    call    suPrint
    push    cx
    mov     al, cl
    add     al, 0x40                    ; this socket's size, from the battery
    call    cmos_read
    pop     cx
    cmp     al, 0
    jne     .fitted
    mov     si, msg_me_empty
    mov     bl, SU_PAPER
    call    suPrint
    jmp     .next
.fitted:
    ; "128 MB DDR4 SODIMM": the size is a table of powers of two
    push    cx
    xor     ah, ah
    mov     si, ax
    add     si, si
    mov     si, [cs:su_ramsize_names + si]
    mov     bl, SU_PAPER
    call    suPrint
    pop     cx
    mov     al, cl
    add     al, 0x48
    call    cmos_read
    xor     ah, ah
    add     ax, ax
    mov     si, ax
    mov     si, [cs:su_kdnames + si]
    mov     bl, SU_PAPER
    call    suPrint
    mov     al, cl
    add     al, 0x50
    call    cmos_read
    xor     ah, ah
    add     ax, ax
    mov     si, ax
    mov     si, [cs:su_formnames + si]
    mov     bl, SU_PAPER
    call    suPrint
.next:
    inc     byte [es:GX_ROW]
    inc     cx
    jmp     .slot
.out:
    pop     si
    pop     dx
    pop     cx
    pop     bx
    pop     ax
    ret

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
    mov     dl, [es:GX_M_RR]
    sub     dl, al
    dec     dl
    mov     dh, GX_FIRST
    call    suGoto
    mov     si, msg_gx_hwmon
    mov     bl, SU_HEAD
    call    suPrint
    mov     dh, GX_FIRST + 1
    mov     dl, GX_TX
    mov     cl, [es:GX_M_TW]
    mov     ch, 0
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
    ; ---- the sensors ------------------------------------------------------
    ; Everything below comes out of the thermal model the sensor core runs:
    ; the die's temperature is integrated from the power the core is burning
    ; and the resistance the fan is leaving it with, and the fan's own curve
    ; is closed around that. The drive has a spindle that takes seconds to
    ; come up to speed, which is why its RPM is worth a line of its own.
    mov     dl, GX_TX
    mov     dh, [es:GX_ROW]
    inc     dh
    call    suGoto
    mov     byte [es:CON_ATTR], SU_PAPER
    mov     si, msg_hw_cputherm
    mov     bl, SU_PAPER
    call    suPrint
    mov     dx, 0x5B0                   ; the die, in whole degrees
    in      al, dx
    xor     ah, ah
    call    hwNum
    mov     si, msg_hw_deg
    mov     bl, SU_PAPER
    call    suPrint
    call    hwFanRpm                    ; ax = the tachometer
    call    hwNum
    mov     si, msg_hw_rpm
    mov     bl, SU_PAPER
    call    suPrint
    inc     byte [es:GX_ROW]
    mov     dl, GX_TX
    mov     dh, [es:GX_ROW]
    inc     dh
    call    suGoto
    mov     byte [es:CON_ATTR], SU_PAPER
    mov     si, msg_hw_drvtherm
    mov     bl, SU_PAPER
    call    suPrint
    mov     dx, 0x5B3                   ; the drive's own thermistor
    in      al, dx
    xor     ah, ah
    call    hwNum
    mov     si, msg_hw_drvdeg
    mov     bl, SU_PAPER
    call    suPrint
    call    hwHddRpm                    ; ax = the spindle
    call    hwNum
    mov     dx, 0x5B7                   ; SMART attribute 4 is the temperature
    mov     al, 4
    out     dx, al
    mov     dx, 0x5B8
    in      al, dx
    xor     ah, ah
    push    ax                          ; the current value
    mov     dx, 0x5B9
    in      al, dx
    xor     ah, ah
    push    ax                          ; ... and the worst ever
    mov     si, msg_hw_smart
    mov     bl, SU_PAPER
    call    suPrint
    ; The two numbers go on the stack, not in BX: setting the print colour
    ; writes BL, BL is half of BX, and 0x1F -- SU_PAPER -- printed a drive's
    ; worst-ever value as "31". A register that has to survive a call whose
    ; documented job is to change it is not a place to keep a number.
    pop     ax
    call    hwNum
    mov     al, '/'
    call    hwChar
    pop     ax
    call    hwNum
    ; and what the emulation switch is set to, under the list
    inc     byte [es:GX_ROW]
    mov     dl, GX_TX
    mov     dh, [es:GX_ROW]
    inc     dh
    call    suGoto
    mov     byte [es:CON_ATTR], SU_PAPER
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
; gxDrive -- the drive's own page, read out of the drive.
;
; A real BIOS asks the device who it is: IDENTIFY DEVICE is one command byte on
; 0x1F7 and then 256 words out of 0x1F0, and the model, the serial and the
; capacity are text inside that block. This page streams the block, keeps the
; fields that are text, and prints them -- so what appears on the screen came
; out of the emulated drive over the emulated cable, not out of a table in the
; ROM.
;
; The words are streamed; only the interesting ranges are kept. 256 reads is
; nothing, and it means a drive that changes its mind changes the screen.
; ---------------------------------------------------------------------------
DRV_W      equ 0x528                  ; the word being streamed
DRV_MODEL  equ 0x600                  ; 40 bytes: words 27..46
DRV_SERIAL equ 0x62A                  ; 20 bytes: words 10..19
DRV_FW     equ 0x640                  ;  8 bytes: words 23..26
DRV_CAP    equ 0x658                  ; capacity, in sectors
DRV_OK     equ 0x65C                  ; did the drive answer at all?

gxDrive:
    push    ax
    push    bx
    push    cx
    push    dx
    push    si
    push    di
    mov     byte [es:CON_ATTR], SU_PAPER
    mov     si, msg_gx_drive0
    call    gxStrLen
    mov     dl, [es:GX_M_RR]
    sub     dl, al
    dec     dl
    mov     dh, GX_FIRST
    call    suGoto
    mov     si, msg_gx_drive0
    mov     bl, SU_HEAD
    call    suPrint
    mov     dh, GX_FIRST + 1
    mov     dl, GX_TX
    mov     cl, [es:GX_M_TW]
    mov     ch, 0
    mov     al, G_HBAR
    mov     bl, SU_SEL
    call    suBar

    ; ---- ask the drive who it is -------------------------------------
    mov     byte [es:DRV_OK], 0
    mov     dx, 0x1F6                 ; drive 0, LBA mode
    mov     al, 0xA0
    out     dx, al
    mov     dx, 0x1F2                 ; one sector's worth
    mov     al, 1
    out     dx, al
    mov     dx, 0x1F7
    mov     al, 0xEC                  ; IDENTIFY DEVICE
    out     dx, al
    mov     dx, 0x1F7                 ; and wait for it to be ready
    in      al, dx
    test    al, 0x40
    jz      .nodrive
    test    al, 0x01
    jnz     .nodrive
    mov     byte [es:DRV_OK], 1
    mov     word [es:DRV_W], 0
.stream:
    mov     dx, 0x1F0
    in      ax, dx
    mov     bx, [es:DRV_W]
    ; model: words 27..46, which is 0-based 26..45
    cmp     bx, 26
    jb      .serial
    cmp     bx, 45
    ja      .serial
    mov     di, bx
    sub     di, 26
    shl     di, 1
    add     di, DRV_MODEL
    mov     [es:di], ah                 ; ATA puts the first character in the
    mov     [es:di + 1], al             ; high byte of the word
.serial:
    ; serial: words 10..19, 0-based 9..18
    cmp     bx, 9
    jb      .firmware
    cmp     bx, 18
    ja      .firmware
    mov     di, bx
    sub     di, 9
    shl     di, 1
    add     di, DRV_SERIAL
    mov     [es:di], ah                 ; ATA puts the first character in the
    mov     [es:di + 1], al             ; high byte of the word
.firmware:
    ; firmware: words 23..26, 0-based 22..25
    cmp     bx, 22
    jb      .capacity
    cmp     bx, 25
    ja      .capacity
    mov     di, bx
    sub     di, 22
    shl     di, 1
    add     di, DRV_FW
    mov     [es:di], ah                 ; ATA puts the first character in the
    mov     [es:di + 1], al             ; high byte of the word
.capacity:
    ; capacity: word 60, 0-based 59
    cmp     bx, 59
    jne     .next
    mov     [es:DRV_CAP], ax
.next:
    inc     word [es:DRV_W]
    cmp     word [es:DRV_W], 256
    jb      .stream
    jmp     .show

.nodrive:
    mov     dl, GX_TX
    mov     dh, GX_FIRST + 3
    call    suGoto
    mov     si, msg_drv_none
    mov     bl, SU_PAPER
    call    suPrint
    jmp     .out

.show:
    ; The fields were copied into the scratch segment, but this code runs with
    ; DS = the ROM segment -- `mov si, offset` here means F000:xxxx. So the
    ; strings need terminators and a DS that points at where they really are,
    ; or suPrint walks off the end of the buffer into whatever follows.
    mov     byte [es:DRV_MODEL + 40], 0
    mov     byte [es:DRV_SERIAL + 20], 0
    mov     byte [es:DRV_FW + 8], 0
    mov     dl, GX_TX
    mov     dh, GX_FIRST + 2
    call    suGoto
    mov     si, msg_drv_model
    mov     bl, SU_PAPER
    call    suPrint
    mov     si, DRV_MODEL
    call    drvStr
    mov     dl, GX_TX
    mov     dh, GX_FIRST + 3
    call    suGoto
    mov     si, msg_drv_serial
    mov     bl, SU_PAPER
    call    suPrint
    mov     si, DRV_SERIAL
    call    drvStr
    mov     dl, GX_TX
    mov     dh, GX_FIRST + 4
    call    suGoto
    mov     si, msg_drv_fw
    mov     bl, SU_PAPER
    call    suPrint
    mov     si, DRV_FW
    call    drvStr
    mov     dl, GX_TX
    mov     dh, GX_FIRST + 5
    call    suGoto
    mov     si, msg_drv_cap
    mov     bl, SU_PAPER
    call    suPrint
    mov     ax, [es:DRV_CAP]
    call    hwNum
    mov     si, msg_drv_sectors
    mov     bl, SU_PAPER
    call    suPrint
    ; ---- and what the sensors say about it -------------------------
    mov     dl, GX_TX
    mov     dh, GX_FIRST + 7
    call    suGoto
    mov     si, msg_drv_therm
    mov     bl, SU_PAPER
    call    suPrint
    mov     dx, 0x5B3
    in      al, dx
    xor     ah, ah
    call    hwNum
    mov     si, msg_hw_drvdeg
    mov     bl, SU_PAPER
    call    suPrint
    call    hwHddRpm
    call    hwNum
    mov     si, msg_hw_rpm
    mov     bl, SU_PAPER
    call    suPrint
.out:
    pop     di
    pop     si
    pop     dx
    pop     cx
    pop     bx
    pop     ax
    ret

; ---------------------------------------------------------------------------
; drvStr -- print a string that lives in the scratch segment.
;
; suPrint reads through DS, and the setup utility runs with DS pointing at the
; ROM. A buffer filled from the drive is in low memory, so DS has to be borrowed
; from ES for the length of one string.
; ---------------------------------------------------------------------------
drvStr:
    push    ds
    push    ax
    mov     ax, es
    mov     ds, ax
    call    suPrint
    pop     ax
    pop     ds
    ret

; ---------------------------------------------------------------------------
; hwNum / hwChar -- print a sensor reading in the page's own colour.
; suPrint colours a string from BL, and BL is whatever the last call left
; there, so a number printed after a highlighted bar came out in the bar's
; colour. These two set the attribute themselves and then print.
; ---------------------------------------------------------------------------
hwNum:
    mov     byte [es:CON_ATTR], SU_PAPER
    call    putn
    mov     byte [es:CON_ATTR], SU_PAPER
    ret

hwChar:
    mov     byte [es:CON_ATTR], SU_PAPER
    call    putc
    mov     byte [es:CON_ATTR], SU_PAPER
    ret

; ---------------------------------------------------------------------------
; hwFanRpm / hwHddRpm -- one 16-bit number out of two ports.
; A byte is all a port carries, so a tachometer reading of 2189 arrives as
; 0x8D and 0x08 and has to be put back together here.
; ---------------------------------------------------------------------------
hwFanRpm:
    mov     dx, 0x5B1
    in      al, dx
    mov     bl, al
    mov     dx, 0x5B2
    in      al, dx
    mov     bh, al
    mov     ax, bx
    ret

hwHddRpm:
    mov     dx, 0x5B4
    in      al, dx
    mov     bl, al
    mov     dx, 0x5B5
    in      al, dx
    mov     bh, al
    mov     ax, bx
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
    mov     cl, [es:GX_M_TW]
    mov     ch, 0
    mov     al, ' '
    mov     bl, SU_PAPER
    call    suBar
    inc     dh
    cmp     dh, [es:GX_M_LAST]
    jbe     .blank
    call    gxNodeOf
    mov     si, [di + 4]
    test    si, si
    jz      .out
    mov     ax, [si + 2]                ; the page starts with the sentinel
    cmp     ax, GX_LIVE
    je      .monitor
    cmp     ax, GX_LIVEDRV
    je      .drive
    cmp     ax, GX_LIVEPCI
    je      .pci
    cmp     ax, GX_LIVEMEM
    je      .memory
    jmp     .static
.memory:
    call    gxMemory
    jmp     .out
.monitor:
    call    gxMonitor
    jmp     .out
.drive:
    call    gxDrive
    jmp     .out
.pci:
    call    gxPci
    jmp     .out
.static:
    ; the title, flush right
    mov     si, [si]
    call    gxStrLen
    mov     dl, [es:GX_M_RR]
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
    mov     cl, [es:GX_M_TW]
    mov     ch, 0
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
    cmp     al, [es:GX_M_LAST]
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
    mov     al, [es:GX_M_HINT]
    test    al, al                  ; no room for the box, so nothing to say
    jz      .out
    mov     dl, 2
    mov     dh, al
    inc     dh
    call    suGoto
    mov     si, msg_gx_hint1
    mov     bl, SU_PAPER
    call    suPrint
    mov     dl, 2
    mov     dh, [es:GX_M_HINT]
    add     dh, 2
    call    suGoto
    mov     si, msg_gx_hint2
    call    suPrint
    mov     dl, 2
    mov     dh, [es:GX_M_HINT]
    add     dh, 3
    call    suGoto
    mov     si, msg_gx_hint3
    call    suPrint
.out:
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
    NODE 1, KIND_GROUP,             msg_gx_cpug,     gx_d_cpug
    NODE 2, KIND_EDIT | 15,         msg_gx_cores,    gx_d_cores
    NODE 2, KIND_EDIT | 16,         msg_gx_threads,  gx_d_threads
    NODE 2, KIND_EDIT | 17,         msg_gx_perfe,    gx_d_perfe
    NODE 1, KIND_GROUP,             msg_gx_clockg,   gx_d_clockg
    NODE 2, KIND_EDIT | 18,         msg_gx_faketime, gx_d_faketime
    NODE 1, KIND_GROUP,             msg_gx_memg,     gx_d_memg
    NODE 2, KIND_EDIT | 19,         msg_gx_slots,    gx_d_slots
    NODE 2, KIND_EDIT | 20,         msg_gx_memmode,  gx_d_memmode
    NODE 2, KIND_EDIT | 21,         msg_gx_slot0,    gx_d_slot
    NODE 2, KIND_EDIT | 22,         msg_gx_slot1,    gx_d_slot
    NODE 2, KIND_EDIT | 23,         msg_gx_slot2,    gx_d_slot
    NODE 2, KIND_EDIT | 24,         msg_gx_slot3,    gx_d_slot
    NODE 2, KIND_EDIT | 25,         msg_gx_slot4,    gx_d_slot
    NODE 2, KIND_EDIT | 26,         msg_gx_slot5,    gx_d_slot
    NODE 2, KIND_EDIT | 27,         msg_gx_slot6,    gx_d_slot
    NODE 2, KIND_EDIT | 28,         msg_gx_slot7,    gx_d_slot
    NODE 0, 0,                      msg_gx_video,    gx_d_video
    NODE 0, 0,      msg_gx_pci,      gx_d_pci
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
gx_d_mem:       dw msg_gx_mem, GX_LIVEMEM, 0
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
gx_d_drive0:    dw msg_gx_drive0, GX_LIVEDRV, 0
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
gx_d_pci:       dw msg_gx_pci, GX_LIVEPCI, 0
gx_d_cpug:      dw msg_gx_cpug, l_cg1, l_cg2, l_blank, l_cg3, 0
gx_d_cores:     dw msg_gx_cores, l_co1, l_co2, l_blank, l_edit, 0
gx_d_threads:   dw msg_gx_threads, l_th1, l_th2, l_blank, l_edit, 0
gx_d_perfe:     dw msg_gx_perfe, l_pe1, l_pe2, l_pe3, l_blank, l_edit, 0
gx_d_clockg:    dw msg_gx_clockg, l_ck1, l_ck2, l_blank, l_ck3, 0
gx_d_faketime:  dw msg_gx_faketime, l_ft1, l_ft2, l_ft3, l_blank, l_edit, 0
gx_d_video:     dw msg_gx_video, l_vd1, l_vd2, l_vd3, l_blank, l_fixed, 0
gx_d_perf:      dw msg_gx_perf, l_pf1, l_blank, l_fixed, 0
gx_d_memg:      dw msg_gx_memg, l_mg1, l_mg2, l_mg3, l_blank, l_mg4, 0
gx_d_slots:     dw msg_gx_slots, l_ms1, l_ms2, l_blank, l_mg5, 0
gx_d_memmode:   dw msg_gx_memmode, l_mm1, l_mm2, l_blank, l_mg5, 0
gx_d_slot:      dw msg_gx_slot, l_msl1, l_msl2, l_blank, l_mg5, 0
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

msg_drv_model:     db "Model      ", 0
msg_drv_serial:    db "Serial     ", 0
msg_drv_fw:        db "Firmware   ", 0
msg_drv_cap:       db "Capacity   ", 0
msg_drv_sectors:   db " sectors of 512 bytes", 0
msg_drv_therm:     db "Drive 0 ", 0
msg_drv_none:      db "No drive on the primary channel.", 0
msg_hw_cputherm:   db "CPU ", 0
msg_hw_drvtherm:   db "Drive 0 ", 0
msg_hw_deg:        db " C, fan ", 0
msg_hw_drvdeg:     db " C, ", 0
msg_hw_fan:        db "", 0
msg_hw_spin:       db " rpm, spindle ", 0
msg_hw_smart:      db " rpm  SMART ", 0
msg_hw_rpm:        db " rpm", 0
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
msg_gx_pci:        db "PCI Devices", 0
msg_gx_cpug:       db "CPU", 0
msg_gx_cores:      db "CPU Cores", 0
msg_gx_threads:    db "CPU Threads", 0
msg_gx_perfe:      db "Performance Emu", 0
msg_gx_clockg:     db "Clock", 0
msg_gx_faketime:   db "Fake OS Time", 0
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
msg_me_base:       db "Base memory        = ", 0
msg_me_ext:        db "Extended memory    = ", 0
msg_me_kb:         db " KB", 13, 10, 0
msg_me_none:       db "none fitted", 0
msg_me_mode:       db "Storage            = ", 0
msg_me_int2:       db "internal (the board's own list)", 0
msg_me_ext2:       db "extended (the memory module)", 0
msg_me_slot:       db "Slot ", 0
msg_me_arrow:      db "  ", 0
msg_me_empty:      db "empty", 0
msg_gx_memg:       db "Memory", 0
msg_gx_slots:      db "Available Slots", 0
msg_gx_memmode:    db "Mode", 0
msg_gx_slot0:      db "Slot 0", 0
msg_gx_slot1:      db "Slot 1", 0
msg_gx_slot2:      db "Slot 2", 0
msg_gx_slot3:      db "Slot 3", 0
msg_gx_slot4:      db "Slot 4", 0
msg_gx_slot5:      db "Slot 5", 0
msg_gx_slot6:      db "Slot 6", 0
msg_gx_slot7:      db "Slot 7", 0
msg_gx_slot:       db "Memory Slot", 0
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

l_cg1:    db "Every core below is one clone", 0
l_cg2:    db "running this same program.", 0
l_cg3:    db "Raise a count and the chipset makes it.", 0
l_co1:    db "1 to 16 cores. The board reports", 0
l_co2:    db "them on port 0x5A0 and wakes each", 0
l_th1:    db "Work each core is given per frame,", 0
l_th2:    db "as a multiple of the base batch.", 0
l_pe1:    db "The measured instruction rate times", 0
l_pe2:    db "this multiplier, reported as MHz.", 0
l_pe3:    db "Nothing runs any faster for it.", 0
l_ck1:    db "The RTC on the I/O ports, and every", 0
l_ck2:    db "time the guest reads from it.", 0
l_ck3:    db "Rate 100% is real time.", 0
l_ft1:    db "Below 100% the guest's clock crawls", 0
l_ft2:    db "while the CPU keeps running flat out,", 0
l_ft3:    db "so a boot reports fewer seconds.", 0

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
l_mg1:    db "The sockets this board has, and what", 0
l_mg2:    db "is in each of them. A change here is", 0
l_mg3:    db "a change to the machine, so saving it", 0
l_mg4:    db "restarts the board.", 0
l_ms1:    db "How many sockets are fitted, 1 to 8.", 0
l_ms2:    db "Nothing is counted past this number.", 0
l_mm1:    db "Internal keeps memory in the board's own", 0
l_mm2:    db "list; Extended uses the memory module.", 0
l_msl1:   db "One module. Size is what is fitted; the", 0
l_msl2:   db "generation and form are what it says it is.", 0
l_mg5:    db "Saving this restarts the board.", 0
l_vd1:    db "Video Controller   = JSW VGA", 0
l_vd2:    db "Video Memory       = 4 KB of text plane", 0
l_vd3:    db "Mode               = 03h text, 13h, 12h", 0
l_pf1:    db "The clock this board counts instructions with.", 0
l_se1:    db "Admin Password     = {none}", 0
l_se2:    db "System Password    = {none}", 0
l_pw1:    db "Power Management   = disabled", 0
l_mt1:    db "Service Tag        = J1Q24BX", 0
l_mt2:    db "Asset Tag          = {none}", 0
l_po1:    db "Fast Boot          = off", 0
l_po2:    db "Numlock            = on", 0
