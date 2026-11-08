; ---------------------------------------------------------------------------
; pxe -- booting from the network, with a network stack written here.
;
; Every real NIC shipped with an option ROM that did DHCP, then TFTP, then
; jumped to the file it had fetched. This is that ROM, written against the card
; this machine has: a Realtek 8139 in the IO window its BAR0 names. Nothing
; here goes around the emulator -- the card is driven through the registers a
; 1999 driver used, the frames go out on the wire, and the file comes back over
; UDP one 512-byte block at a time.
;
; The stack is the smallest one that can boot: Ethernet, ARP, IPv4 with no
; options, UDP, a DHCP client that asks for a boot file, and a TFTP client. No
; TCP, because PXE never needs it -- a boot ROM that had to finish a handshake
; before it could fetch its own firmware would be a ROM that could not fetch
; its own firmware.
;
; Three areas of memory, all of them pages the firmware is not using:
;
;   gs:0x0000  one frame being built (linear 0x44000)
;   gs:0x0800  one frame being read, copied out of the card's ring
;   0x040000   the card's receive ring, 8 KiB
;   0x007C00   where the fetched file lands -- where a boot sector goes, so a
;              network boot ends the same way a disk boot does
;
; ds is the ROM (so `mov si, message` works), es is zero (the data area), and
; gs is the frame buffer. Nothing here touches fs, which the console owns.
; ---------------------------------------------------------------------------

R_IDR0      equ 0x00
R_TSD0      equ 0x10
R_TSAD0     equ 0x20
R_RBSTART   equ 0x30
R_CR        equ 0x37
R_CAPR      equ 0x38
R_CBR       equ 0x3A
R_IMR       equ 0x3C
R_ISR       equ 0x3E
R_MSR       equ 0x5C
R_TCR       equ 0x40
R_RCR       equ 0x44

CR_RE       equ 0x08
CR_TE       equ 0x04
CR_RST      equ 0x10
TSD_OWN     equ 0x2000

; what the loader has learned, in the data area
PXE_ST      equ 0x600
PXE_IO      equ PXE_ST + 0x00        ; word: the IO window the bus reported
PXE_MAC     equ PXE_ST + 0x02        ; six bytes, read out of the card
PXE_IP      equ PXE_ST + 0x08
PXE_SRV     equ PXE_ST + 0x0C
PXE_SMAC    equ PXE_ST + 0x10        ; the server's MAC, from ARP
PXE_XID     equ PXE_ST + 0x16
PXE_DIP     equ PXE_ST + 0x1A
PXE_SIP     equ PXE_ST + 0x1E
PXE_DMAC    equ PXE_ST + 0x22
PXE_SPORT   equ PXE_ST + 0x28
PXE_DPORT   equ PXE_ST + 0x2A
PXE_PLEN    equ PXE_ST + 0x2C        ; the payload length of the next frame
PXE_NAME    equ PXE_ST + 0x2E        ; 64 bytes: the boot file
PXE_BLOCK   equ PXE_ST + 0x6E
PXE_SIZE    equ PXE_ST + 0x70
PXE_TYPE    equ PXE_ST + 0x74
PXE_ERRS    equ PXE_ST + 0x75
PXE_MODE    equ PXE_ST + 0x76
PXE_LEN     equ PXE_ST + 0x7C        ; the last block's length, which the ACK
                                     ; frame would otherwise overwrite

PXE_RX      equ 0x40000
PXE_RX_SEG  equ 0x4000               ; the same page, as a segment
PXE_RX_WRAP equ 8192
PXE_TX      equ 0x44000              ; linear address of gs:0
PXE_FB      equ 0x0800               ; where a received frame is copied to
PXE_FB_SEG  equ 0x4400               ; the segment both of those live in
PXE_LOAD    equ 0x07C0

ETH_IPV4    equ 0x0800
ETH_ARP     equ 0x0806
IP_UDP      equ 17

; ---------------------------------------------------------------------------
; pxeBoot -- al = 0 to boot the file DHCP names, 1 to chainload iPXE.
;
; Does not return when it worked: a PXE boot ends in the file it fetched, which
; is the point of it. Comes back with the carry set when it did not, so the
; menu can say so and return to its list.
; ---------------------------------------------------------------------------
pxeBoot:
    push    ax
    push    bx
    push    cx
    push    dx
    push    si
    push    di
    push    gs
    push    ds
    ; the messages live in the ROM, so ds has to be the ROM: a loader reached
    ; by a jump rather than by the menu inherits whatever the caller had, and
    ; "PXEClient" read from segment zero is nine zero bytes
    mov     dx, cs
    mov     ds, dx
    mov     [es:PXE_MODE], al
    mov     byte [es:PXE_ERRS], 0
    mov     word [es:PXE_SIZE], 0
    mov     word [es:PXE_BLOCK], 1
    mov     ax, PXE_FB_SEG
    mov     gs, ax
    call    pxeFind
    jc      .nocard
    call    pxeLink
    jc      .nolink
    call    pxeInit
    jc      .nocard
    call    pxeDhcp
    jc      .nodhcp
    call    pxeArp
    jc      .noserver
    call    pxeTftp
    jc      .nofile
    call    pxeRun                  ; does not return
.nocard:
    mov     si, msg_pxe_nocard
    jmp     .say
.nolink:
    call    pxeClear
    mov     si, msg_pxe_nolink
    call    puts
    mov     si, msg_crlf
    call    puts
    mov     si, msg_pxe_nowire
    call    puts
    mov     si, msg_crlf
    call    puts
    stc
    jmp     .back
.nodhcp:
    mov     si, msg_pxe_nodhcp
    jmp     .say
.noserver:
    mov     si, msg_pxe_noserver
    jmp     .say
.nofile:
    mov     si, msg_pxe_nofile
.say:
    call    pxeClear
    call    puts                    ; the reason, on a screen of its own: the
    mov     al, [es:PXE_ERRS]       ; loader drew a panel of its own before it
                                    ; gave up, and a message over it is noise

    call    puthex
    mov     si, msg_close
    call    puts
    mov     si, msg_crlf
    call    puts
    stc
.back:
    pop     ds
    pop     gs
    pop     di
    pop     si
    pop     dx
    pop     cx
    pop     bx
    pop     ax
    ret

; ---------------------------------------------------------------------------
; A way in that is not a menu. The boot order can name the network without a
; key being pressed, and a test that wants the loader should not have to drive
; a panel to reach it: the marker is followed by the address to jump to, and
; the mode comes out of the data area.
; ---------------------------------------------------------------------------
pxeMark:        db "JSWPXE", 0
                dw pxeTrampoline
pxeTrampoline:
    xor     ax, ax
    mov     al, [es:PXE_MODE]
    jmp     pxeBoot

; ---------------------------------------------------------------------------
; pxeFind -- ask the bus where the card is rather than assume.
; ---------------------------------------------------------------------------
pxeFind:
    push    eax
    push    dx
    mov     eax, 0x80000000 + (4 << 11) + 0x00
    mov     dx, 0xCF8
    out     dx, eax
    mov     dx, 0xCFC
    in      eax, dx
    cmp     eax, 0x813910EC            ; device id high, vendor id low
    jne     .no
    mov     eax, 0x80000000 + (4 << 11) + 0x10
    mov     dx, 0xCF8
    out     dx, eax
    mov     dx, 0xCFC
    in      eax, dx
    and     ax, 0xFFFC                 ; the low bits are flags, not address
    jz      .no
    mov     [es:PXE_IO], ax
    clc
    jmp     .out
.no:
    stc
.out:
    pop     dx
    pop     eax
    ret

; ---------------------------------------------------------------------------
; pxeClear -- a blank screen with the cursor at the top left, for a message
; that is about to explain a failure.
; ---------------------------------------------------------------------------
pxeClear:
    push    ax
    push    dx
    mov     byte [es:CON_ATTR], 0x07
    call    clear_text
    xor     dl, dl
    xor     dh, dh
    call    suGoto
    pop     dx
    pop     ax
    ret

; ---------------------------------------------------------------------------
; pxeLink -- is there a wire in the card? MSR bit 2 is LINK_FAIL.
;
; A machine with no host wire answers nothing, and the failure a person sees is
; "no address from DHCP", which is true and useless: the fault is not in the
; DHCP exchange, it is that there was never anybody to exchange with. The card
; knows -- it reports the link -- so the ROM asks it first and says what it
; found.
; ---------------------------------------------------------------------------
pxeLink:
    push    dx
    mov     dx, [es:PXE_IO]
    add     dx, R_MSR
    in      al, dx
    pop     dx
    test    al, 4
    jz      .up
    stc
    ret
.up:
    clc
    ret

; ---------------------------------------------------------------------------
; pxeInit -- reset the card, give it a ring, turn the receiver on.
;
; The order is the datasheet's: reset, program the ring, enable. A card told
; about its ring after it was enabled has already written a frame somewhere
; else.
; ---------------------------------------------------------------------------
pxeInit:
    push    ax
    push    cx
    push    dx
    push    eax
    push    di
    mov     dx, [es:PXE_IO]
    add     dx, R_CR
    mov     al, CR_RST
    out     dx, al
    mov     cx, 0x4000
.wait:
    in      al, dx
    test    al, CR_RST                 ; the reset bit clears itself
    jz      .reset
    loop    .wait
    jmp     .bad
.reset:
    mov     dx, [es:PXE_IO]
    add     dx, R_IDR0
    mov     di, PXE_MAC
    mov     cx, 6
.mac:
    in      al, dx
    mov     [es:di], al
    inc     dx
    inc     di
    loop    .mac
    mov     dx, [es:PXE_IO]
    add     dx, R_RBSTART
    mov     eax, PXE_RX
    out     dx, eax
    mov     dx, [es:PXE_IO]
    add     dx, R_CAPR
    xor     ax, ax
    out     dx, ax
    mov     dx, [es:PXE_IO]
    add     dx, R_CBR
    xor     ax, ax
    out     dx, ax
    mov     dx, [es:PXE_IO]
    add     dx, R_RCR
    mov     eax, 0x8F                  ; AB|AM|APM|AAP, and wrap at 8K
    out     dx, eax
    mov     dx, [es:PXE_IO]
    add     dx, R_TCR
    mov     eax, 0x03000700
    out     dx, eax
    mov     dx, [es:PXE_IO]
    add     dx, R_IMR
    xor     ax, ax                     ; no interrupts: this loader polls
    out     dx, ax
    mov     dx, [es:PXE_IO]
    add     dx, R_ISR
    mov     ax, 0xFFFF                 ; and clear whatever the reset left
    out     dx, ax
    mov     dx, [es:PXE_IO]
    add     dx, R_CR
    mov     al, CR_RE + CR_TE
    out     dx, al
    clc
    jmp     .out
.bad:
    stc
.out:
    pop     di
    pop     eax
    pop     dx
    pop     cx
    pop     ax
    ret

; ---------------------------------------------------------------------------
; pxeSend -- put the frame in gs:0 on the wire. bx = its length.
; ---------------------------------------------------------------------------
pxeSend:
    push    ax
    push    cx
    push    dx
    push    eax
    movzx   ebx, bx
    mov     dx, [es:PXE_IO]
    add     dx, R_TSD0
    mov     cx, 0x8000
.wait:
    in      eax, dx
    test    eax, TSD_OWN
    jz      .free
    loop    .wait
    jmp     .out
.free:
    mov     dx, [es:PXE_IO]
    add     dx, R_TSAD0
    mov     eax, PXE_TX
    out     dx, eax
    mov     dx, [es:PXE_IO]
    add     dx, R_TSD0
    mov     eax, ebx
    or      eax, TSD_OWN
    out     dx, eax
    mov     cx, 0x8000
.wait2:
    in      eax, dx
    test    eax, TSD_OWN
    jz      .out
    loop    .wait2
.out:
    pop     eax
    pop     dx
    pop     cx
    pop     ax
    ret

; ---------------------------------------------------------------------------
; pxeRecv -- copy one frame out of the card's ring into gs:PXE_FB.
;
; Carry set when nothing had arrived. The ring is handed back to the card
; before the frame is looked at: a frame held while the next one is waited for
; is a receiver that stops receiving.
; ---------------------------------------------------------------------------
pxeRecv:
    push    ax
    push    bx
    push    cx
    push    dx
    push    si
    push    di
    push    bp
    push    es
    push    ds
    mov     bp, 0x1000                 ; how long to wait before giving up
.wait:
    mov     dx, [es:PXE_IO]
    add     dx, R_CBR
    in      ax, dx
    mov     si, ax                     ; the card's write pointer
    mov     dx, [es:PXE_IO]
    add     dx, R_CAPR
    in      ax, dx                     ; and the driver's read pointer
    cmp     ax, si
    je      .again
    ; the ring is one page, so a segment of its own reaches all of it
    mov     dx, PXE_RX_SEG
    mov     ds, dx
    mov     bx, ax                     ; CAPR: where the frame to read starts,
                                       ; which is not where the card writes next
    mov     di, bx
    mov     cx, [di + 2]               ; the length includes its own header
    add     bx, cx
    and     bx, PXE_RX_WRAP - 1
    ; hand the ring back before looking at the frame
    mov     dx, [es:PXE_IO]
    add     dx, R_CAPR
    mov     ax, bx
    out     dx, ax
    ; and copy it where the parser can reach it
    mov     si, di
    add     si, 4
    sub     cx, 4
    mov     ax, PXE_FB_SEG
    mov     es, ax
    mov     di, PXE_FB
    cld
    rep     movsb
    clc
    jmp     .out
.again:
    dec     bp
    jnz     .wait
    stc
.out:
    pop     ds
    pop     es
    pop     bp
    pop     di
    pop     si
    pop     dx
    pop     cx
    pop     bx
    pop     ax
    ret

; ---------------------------------------------------------------------------
; pxeChecksum -- the internet checksum of gs:si, cx bytes, in ax.
; ---------------------------------------------------------------------------
pxeChecksum:
    push    bx
    push    cx
    push    si
    xor     ax, ax
    shr     cx, 1
.sum:
    jcxz    .done
    mov     bx, [gs:si]
    add     ax, bx
    adc     ax, 0
    add     si, 2
    dec     cx
    jmp     .sum
.done:
    not     ax
    pop     si
    pop     cx
    pop     bx
    ret

; ---------------------------------------------------------------------------
; pxeBuild -- the Ethernet, IP and UDP headers of the frame in gs:0.
;
; The payload is already at gs:42 and its length is in PXE_PLEN, so a caller
; only has to fill in PXE_DMAC/PXE_DIP/PXE_SIP/ports and write the payload.
; ---------------------------------------------------------------------------
pxeBuild:
    push    ax
    push    bx
    push    cx
    push    si
    push    di
    mov     di, 0
    mov     si, PXE_DMAC
    mov     cx, 6
    call    pxeCopyState
    mov     di, 6
    mov     si, PXE_MAC
    mov     cx, 6
    call    pxeCopyState
    mov     byte [gs:12], 0x08         ; an ethertype is big-endian on the wire
    mov     byte [gs:13], 0x00
    mov     byte [gs:14], 0x45
    mov     byte [gs:15], 0
    mov     ax, [es:PXE_PLEN]
    add     ax, 28                     ; the IP header and the UDP header
    mov     bh, ah
    mov     bl, al
    mov     [gs:16], bh                ; a header field is big-endian...
    mov     [gs:17], bl
    mov     word [gs:18], 0x0100       ; an id, any id
    mov     word [gs:20], 0
    mov     byte [gs:22], 64
    mov     byte [gs:23], IP_UDP
    mov     word [gs:24], 0
    mov     di, 26
    mov     si, PXE_SIP
    mov     cx, 4
    call    pxeCopyState
    mov     di, 30
    mov     si, PXE_DIP
    mov     cx, 4
    call    pxeCopyState
    mov     si, 14
    mov     cx, 20
    call    pxeChecksum
    mov     [gs:24], ah
    mov     [gs:25], al
    mov     ax, [es:PXE_SPORT]
    mov     bh, ah
    mov     bl, al
    mov     [gs:34], bh
    mov     [gs:35], bl
    mov     ax, [es:PXE_DPORT]
    mov     bh, ah
    mov     bl, al
    mov     [gs:36], bh
    mov     [gs:37], bl
    mov     ax, [es:PXE_PLEN]
    add     ax, 8
    mov     bh, ah
    mov     bl, al
    mov     [gs:38], bh
    mov     [gs:39], bl
    mov     word [gs:40], 0            ; no UDP checksum: legal, and a boot ROM
                                       ; takes the legal shortcut
    pop     di
    pop     si
    pop     cx
    pop     bx
    pop     ax
    ret

; pxeSendUdp -- headers, then the frame.
pxeSendUdp:
    push    ax
    call    pxeBuild
    mov     ax, [es:PXE_PLEN]
    add     ax, 42
    mov     bx, ax
    call    pxeSend
    pop     ax
    ret

; pxeCopyState -- cx bytes from es:si to gs:di.
pxeCopyState:
    push    ax
.one:
    jcxz    .out
    mov     al, [es:si]
    mov     [gs:di], al
    inc     si
    inc     di
    dec     cx
    jmp     .one
.out:
    pop     ax
    ret

; pxeCopyFrame -- cx bytes from gs:di to es:si.
pxeCopyFrame:
    push    ax
.one:
    jcxz    .out
    mov     al, [gs:di]
    mov     [es:si], al
    inc     si
    inc     di
    dec     cx
    jmp     .one
.out:
    pop     ax
    ret

; pxeZero -- cx bytes of zero at gs:di.
pxeZero:
    push    ax
    push    di
.one:
    jcxz    .out
    mov     byte [gs:di], 0
    inc     di
    dec     cx
    jmp     .one
.out:
    pop     di
    pop     ax
    ret

; ---------------------------------------------------------------------------
; pxeDhcp -- DISCOVER, OFFER, REQUEST, ACK.
;
; The vendor class is the whole trick: a server that sees "PXEClient" answers
; with options 66 and 67, and one that does not is a server handing addresses
; to machines that already know what to boot.
; ---------------------------------------------------------------------------
pxeDhcp:
    push    ax
    push    cx
    push    si
    push    di
    mov     ax, [es:0x46C]             ; the tick counter: different every boot
    xor     ax, 0x5A5A
    mov     [es:PXE_XID], ax
    mov     ax, 0x2468
    mov     [es:PXE_XID + 2], ax
    mov     byte [es:PXE_TYPE], 0
    mov     si, msg_pxe_dhcp
    call    puts
    ; ---- DISCOVER
    mov     di, 42
    mov     cx, 300
    call    pxeZero
    call    pxeBootp
    mov     di, 42 + 240
    mov     byte [gs:di], 53
    mov     byte [gs:di + 1], 1
    mov     byte [gs:di + 2], 1        ; DISCOVER
    mov     byte [gs:di + 3], 60
    mov     byte [gs:di + 4], 9
    mov     si, msg_pxe_class
    add     di, 5
    mov     cx, 9
    call    pxeCopyText
    mov     di, 42 + 240 + 14
    mov     byte [gs:di], 55           ; what we would like back
    mov     byte [gs:di + 1], 3
    mov     byte [gs:di + 2], 1
    mov     byte [gs:di + 3], 3
    mov     byte [gs:di + 4], 6
    mov     byte [gs:di + 5], 255
    mov     word [es:PXE_PLEN], 240 + 20
    call    pxeBroadcast
    call    pxeSendUdp
    call    pxeWaitDhcp
    jc      .nooffer
    cmp     byte [es:PXE_TYPE], 2
    jne     .nooffer
    ; ---- REQUEST, so the server writes the lease down
    mov     di, 42
    mov     cx, 300
    call    pxeZero
    call    pxeBootp
    mov     di, 42 + 240
    mov     byte [gs:di], 53
    mov     byte [gs:di + 1], 1
    mov     byte [gs:di + 2], 3        ; REQUEST
    mov     byte [gs:di + 3], 50       ; for the address we were offered
    mov     byte [gs:di + 4], 4
    push    di
    mov     di, 42 + 240 + 9
    mov     si, PXE_IP
    mov     cx, 4
    call    pxeCopyState
    pop     di
    mov     di, 42 + 240 + 13
    mov     byte [gs:di], 54           ; asked of the server that offered it
    mov     byte [gs:di + 1], 4
    mov     di, 42 + 240 + 15
    mov     si, PXE_SRV
    mov     cx, 4
    call    pxeCopyState
    mov     di, 42 + 240 + 19
    mov     byte [gs:di], 255
    mov     word [es:PXE_PLEN], 240 + 20
    call    pxeBroadcast
    call    pxeSendUdp
    call    pxeWaitDhcp
    jc      .noack
    cmp     byte [es:PXE_TYPE], 5
    jne     .noack
    ; an iPXE boot fetches iPXE: the server's boot file is for the first kind
    ; of client, and the second kind asks for a different file by definition
    cmp     byte [es:PXE_MODE], 1
    jne     .plain
    mov     di, PXE_NAME
    mov     si, msg_pxe_ipxe
    mov     cx, 10
    call    pxeCopyText
.plain:
    clc
    jmp     .out
.nooffer:
    mov     byte [es:PXE_ERRS], 0x21
    stc
    jmp     .out
.noack:
    mov     byte [es:PXE_ERRS], 0x22
    stc
.out:
    pop     di
    pop     si
    pop     cx
    pop     ax
    ret

; pxeBootp -- the fixed 236 bytes of a BOOTP message, and its cookie.
pxeBootp:
    push    ax
    push    cx
    push    si
    push    di
    mov     byte [gs:42], 1            ; BOOTREQUEST
    mov     byte [gs:43], 1            ; ethernet
    mov     byte [gs:44], 6            ; six bytes of address
    mov     ax, [es:PXE_XID]
    mov     [gs:46], ax                ; the xid is a value, not a header
    mov     ax, [es:PXE_XID + 2]       ; field, so it goes native-order
    mov     [gs:48], ax
    mov     word [gs:52], 0x0080       ; and broadcast the answer back
    mov     di, 42 + 28
    mov     si, PXE_MAC
    mov     cx, 6
    call    pxeCopyState
    mov     di, 42 + 236
    mov     byte [gs:di], 0x63
    mov     byte [gs:di + 1], 0x82
    mov     byte [gs:di + 2], 0x53
    mov     byte [gs:di + 3], 0x63
    pop     di
    pop     si
    pop     cx
    pop     ax
    ret

; pxeCopyText -- cx bytes from ds:si (the ROM) to gs:di.
pxeCopyText:
    push    ax
.one:
    jcxz    .out
    mov     al, [si]
    mov     [gs:di], al
    inc     si
    inc     di
    dec     cx
    jmp     .one
.out:
    pop     ax
    ret

; pxeBroadcast -- send this to everything, from nowhere.
pxeBroadcast:
    push    si
    push    di
    mov     di, PXE_DMAC
    mov     cx, 6
    mov     al, 0xFF
    call    pxeFillState
    mov     di, PXE_DIP
    mov     cx, 4
    call    pxeFillState
    mov     di, PXE_SIP
    mov     cx, 4
    xor     al, al
    call    pxeFillState
    mov     word [es:PXE_SPORT], 68
    mov     word [es:PXE_DPORT], 67
    pop     di
    pop     si
    ret

; pxeFillState -- cx bytes of al at es:di.
pxeFillState:
    push    cx
    push    di
.one:
    jcxz    .out
    mov     [es:di], al
    inc     di
    dec     cx
    jmp     .one
.out:
    pop     di
    pop     cx
    ret

; pxeWaitDhcp -- wait for the answer to our own transaction.
pxeWaitDhcp:
    push    ax
    push    cx
    push    si
    push    di
    mov     byte [es:PXE_ERRS], 0x23
    mov     cx, 4                      ; four tries, a frame apart
.try:
    push    cx
    call    pxeRecv
    pop     cx
    jc      .next
    cmp     byte [gs:PXE_FB + 12], 0x08            ; an ethertype is two bytes
    jne     .next                                  ; in network order, and a
    cmp     byte [gs:PXE_FB + 13], 0x00            ; word read here is not
    jne     .next
    cmp     byte [gs:PXE_FB + 14 + 9], IP_UDP
    jne     .next
    cmp     byte [gs:PXE_FB + 14 + 20], 0x00       ; sport 67
    jne     .next
    cmp     byte [gs:PXE_FB + 14 + 21], 0x43
    jne     .next
    cmp     byte [gs:PXE_FB + 14 + 22], 0x00       ; dport 68
    jne     .next
    cmp     byte [gs:PXE_FB + 14 + 23], 0x44
    jne     .next
    mov     ax, [gs:PXE_FB + 42 + 4]
    cmp     ax, [es:PXE_XID]
    jne     .next
    mov     di, PXE_FB + 42 + 16
    mov     si, PXE_IP
    mov     cx, 4
    call    pxeCopyFrame
    mov     di, PXE_FB + 42 + 20
    mov     si, PXE_SRV
    mov     cx, 4
    call    pxeCopyFrame
    mov     byte [es:PXE_TYPE], 0
    mov     si, PXE_FB + 42 + 240
    call    pxeOptions
    clc
    jmp     .out
.next:
    dec     cx
    jnz     .try
    stc
.out:
    pop     di
    pop     si
    pop     cx
    pop     ax
    ret

; pxeOptions -- walk the option list at gs:si, keeping the two that matter.
pxeOptions:
    push    ax
    push    bx
    push    cx
    push    si
    push    di
.walk:
    mov     al, [gs:si]
    cmp     al, 255
    je      .out
    cmp     al, 0
    jne     .real
    inc     si
    jmp     .walk
.real:
    inc     si
    mov     bl, [gs:si]                ; the length of this option
    xor     bh, bh                     ; and only the length: bh still holds
    inc     si                         ; whatever the last comparison left
    cmp     al, 53
    jne     .not_type
    mov     al, [gs:si]
    mov     [es:PXE_TYPE], al
    jmp     .skip
.not_type:
    cmp     al, 67                     ; the name of the file to boot
    jne     .skip
    mov     di, PXE_NAME
    mov     cx, 0
.namecopy:
    cmp     cx, bx
    jae     .walk                    ; the copy already moved si past the
    mov     al, [gs:si]              ; option, so skipping it again would walk
    mov     [es:di], al              ; into the next one's middle
    inc     si
    inc     di
    inc     cx
    jmp     .namecopy
.skip:
    mov     al, bl
    xor     ah, ah
    add     si, ax
    jmp     .walk
.out:
    pop     di
    pop     si
    pop     cx
    pop     bx
    pop     ax
    ret

; ---------------------------------------------------------------------------
; pxeArp -- "who has the boot server", and remember the answer.
; ---------------------------------------------------------------------------
pxeArp:
    push    ax
    push    bx
    push    cx
    push    si
    push    di
    mov     si, msg_pxe_arp
    call    puts
    mov     di, 0
    mov     cx, 64
    call    pxeZero
    mov     di, 0
    mov     cx, 6
    mov     al, 0xFF
    call    pxeFillState              ; everyone
    mov     di, 6
    mov     si, PXE_MAC
    mov     cx, 6
    call    pxeCopyState
    mov     byte [gs:12], 0x08
    mov     byte [gs:13], 0x06
    mov     word [gs:14], 0x0100       ; hardware type 1: ethernet
    mov     byte [gs:16], 0x08         ; protocol type 0x0800, big-endian
    mov     byte [gs:17], 0x00
    mov     byte [gs:18], 6
    mov     byte [gs:19], 4
    mov     word [gs:20], 0x0100       ; a request
    mov     di, 22
    mov     si, PXE_MAC
    mov     cx, 6
    call    pxeCopyState
    mov     di, 28
    mov     si, PXE_IP
    mov     cx, 4
    call    pxeCopyState
    mov     di, 38
    mov     si, PXE_SRV
    mov     cx, 4
    call    pxeCopyState
    mov     bx, 42
    call    pxeSend
    mov     cx, 4
.try:
    push    cx
    call    pxeRecv
    pop     cx
    jc      .next
    cmp     byte [gs:PXE_FB + 12], 0x08
    jne     .next
    cmp     byte [gs:PXE_FB + 13], 0x06            ; ARP
    jne     .next
    cmp     byte [gs:PXE_FB + 21], 0x02            ; and it is a reply
    jne     .next
    mov     ax, [gs:PXE_FB + 28]                   ; the address it is from
    cmp     ax, [es:PXE_SRV]
    jne     .next
    mov     ax, [gs:PXE_FB + 30]
    cmp     ax, [es:PXE_SRV + 2]
    jne     .next
    mov     di, PXE_FB + 22
    mov     si, PXE_SMAC
    mov     cx, 6
    call    pxeCopyFrame
    clc
    jmp     .out
.next:
    dec     cx
    jnz     .try
    stc
.out:
    pop     di
    pop     si
    pop     cx
    pop     bx
    pop     ax
    ret

; ---------------------------------------------------------------------------
; pxeTftp -- fetch PXE_NAME one 512-byte block at a time, into 0x7C00.
;
; The block number in the ACK is what makes TFTP work over a datagram that may
; be lost: the server does not move on until it hears it, and this side does
; not move on until the block it wants arrives.
; ---------------------------------------------------------------------------
pxeTftp:
    push    ax
    push    bx
    push    cx
    push    si
    push    di
    mov     si, msg_pxe_tftp
    call    puts
    mov     word [es:PXE_SPORT], 0x4000
    mov     word [es:PXE_DPORT], 69
    mov     di, PXE_DMAC
    mov     si, PXE_SMAC
    mov     cx, 6
    call    pxeCopyState
    mov     di, PXE_DIP
    mov     si, PXE_SRV
    mov     cx, 4
    call    pxeCopyState
    mov     di, PXE_SIP
    mov     si, PXE_IP
    mov     cx, 4
    call    pxeCopyState
    ; the read request, once. Asking again for every block would restart the
    ; transfer from block one, because that is what an RRQ means.
    mov     di, 42
    mov     cx, 80
    call    pxeZero
    mov     byte [gs:42], 0
    mov     byte [gs:43], 1            ; RRQ
    mov     di, 44
    mov     si, PXE_NAME
    mov     cx, 64
    call    pxeCopyState
    mov     di, 44 + 64
    mov     si, msg_pxe_octet
    mov     cx, 6
    call    pxeCopyText
    mov     word [es:PXE_PLEN], 44 + 64 + 6 - 42
    call    pxeSendUdp
.block:
    mov     cx, 6
.try:
    push    cx
    call    pxeRecv
    pop     cx
    jc      .next
    cmp     byte [gs:PXE_FB + 12], 0x08
    jne     .next
    cmp     byte [gs:PXE_FB + 13], 0x00
    jne     .next
    cmp     byte [gs:PXE_FB + 14 + 9], IP_UDP
    jne     .next
    cmp     byte [gs:PXE_FB + 14 + 20], 0x00       ; from the server's port 69
    jne     .next
    cmp     byte [gs:PXE_FB + 14 + 21], 0x45
    jne     .next
    mov     al, [gs:PXE_FB + 43]       ; the opcode is a big-endian word, so
    cmp     al, 3                      ; its low byte comes second
    je      .data
    cmp     al, 5                      ; an error, which says why
    je      .error
    jmp     .next
.data:
    mov     ax, [gs:PXE_FB + 42 + 2]   ; the block number, big-endian
    xchg    al, ah
    cmp     ax, [es:PXE_BLOCK]
    jne     .next
    mov     bx, [gs:PXE_FB + 38]       ; the UDP length
    xchg    bl, bh
    sub     bx, 8 + 4                  ; the UDP header and the TFTP header
    cmp     bx, 512
    jbe     .fits
    mov     bx, 512
.fits:
    mov     ax, PXE_LOAD
    mov     es, ax
    mov     di, PXE_FB + 42 + 4        ; pxeCopyFrame reads gs:di into es:si
    xor     si, si
    mov     cx, bx
    call    pxeCopyFrame
    ; back to the data area: the frame is built through gs, but the loader's
    ; own state -- the payload length the ACK needs -- lives in the data area
    xor     ax, ax
    mov     es, ax
    ; the ACK, which is the whole of the flow control
    mov     di, 42
    mov     byte [gs:di], 0
    mov     byte [gs:di + 1], 4
    mov     ax, [es:PXE_BLOCK]         ; a field on the wire is big-endian, and
    mov     [gs:di + 2], ah            ; ah already holds the high byte
    mov     [gs:di + 3], al
    mov     [es:PXE_LEN], bx           ; sending the ACK overwrites bx
    mov     word [es:PXE_PLEN], 4
    call    pxeSendUdp
    mov     ax, [es:PXE_SIZE]
    add     ax, [es:PXE_LEN]
    mov     [es:PXE_SIZE], ax
    inc     word [es:PXE_BLOCK]
    cmp     word [es:PXE_LEN], 512
    jb      .done                      ; a short block is the last one
    jmp     .block
.next:
    dec     cx
    jnz     .try
    mov     byte [es:PXE_ERRS], 0x31
    stc
    jmp     .out
.error:
    mov     al, [gs:PXE_FB + 42 + 3]
    mov     [es:PXE_ERRS], al
    stc
    jmp     .out
.done:
    clc
.out:
    pop     di
    pop     si
    pop     cx
    pop     bx
    pop     ax
    ret

; ---------------------------------------------------------------------------
; pxeRun -- hand the machine to what was fetched.
;
; 0x7C00, ds and es at zero, and dl naming the drive it came from is the
; contract a boot sector is written to, and a file fetched over a network is
; still a boot sector.
; ---------------------------------------------------------------------------
pxeRun:
    mov     si, msg_pxe_loaded
    call    puts
    mov     ax, [es:PXE_SIZE]
    call    putn
    mov     si, msg_pxe_bytes
    call    puts
    mov     si, msg_crlf
    call    puts
    cli
    mov     ax, PXE_LOAD
    mov     ds, ax
    xor     ax, ax
    mov     es, ax
    mov     ss, ax
    mov     sp, 0x7C00
    mov     dl, 0x80
    jmp     0x07C0:0000

msg_pxe_nocard:  db "no network card on the bus (", 0
msg_pxe_nodhcp:  db "no address from DHCP (", 0
msg_pxe_noserver: db "no ARP reply from the server (", 0
msg_pxe_nofile:  db "no boot file (", 0
msg_close:       db ")", 0
msg_pxe_dhcp:    db "DHCP...", 13, 10, 0
msg_pxe_arp:     db "ARP...", 13, 10, 0
msg_pxe_tftp:    db "TFTP...", 13, 10, 0
msg_pxe_loaded:  db "loaded ", 0
msg_pxe_bytes:   db " bytes", 13, 10, 0
msg_pxe_ipxe:    db "ipxe.pxe", 0, 0
msg_pxe_nolink:  db "no network link", 0
msg_pxe_nowire:  db "the host wire is not running", 0
msg_pxe_class:   db "PXEClient"
msg_pxe_octet:   db 0, "octet", 0
