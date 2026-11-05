/* rv64 bios: power-on self test on the text plane, then halt.
 *
 * This is the first thing that runs on the machine. It programs the display,
 * prints what it found, and hands nothing on yet -- there is no boot sector to
 * jump to, so it ends with "no bootable device" the way a PC does.
 *
 * Text plane: 80 x 30 cells of (char, attr) at 0x5000_0000, two bytes each.
 *   0x5000_0000  cells       0x5000_012c .. the first row
 *   0x5000_2000  graphics    320 x 200 palette indices
 *   0x5000_12000 palette     256 x (r, g, b)
 *   0x5000_12300 control     byte 0 = mode, byte 1 = halt the CPU
 */
.option norvc
.equ NVRAM, 0x10002000              /* the battery-backed page */
/* Where a bootable payload stages itself: a 16-byte record -- magic, a1, a2
 * and the address of the code that copies it down -- above the largest
 * firmware this board boots. */
.equ BOOTREC,  0x80043000
.equ ROM,   0x00001000              /* the firmware window */
/* The chipset window: the memory size the board gives the guest, and the
 * reset button that applies a change. A real board straps its RAM; this one
 * has no sockets, so the size is a register the setup screen writes. */
.equ BOARD,    0x10004000
.equ B_MEM,    0x0                  /* the size to give the guest, MiB */
.equ B_CMD,    0x4                  /* write 1: reset into that size */
.equ B_NOW,    0x8                  /* MiB live now */
.equ B_RESETS, 0xC                  /* warm resets since power-on */
.equ MEM_MAX,  1024                 /* what the board will promise, in MiB */
.equ MEM_MIN,  1
/* The count walks the RAM in about this many steps, like a PC that counts
 * faster the more it has: 8 MiB counts by 256 KiB, 1 GiB by 16 MiB. */
.equ MEM_STEPS, 64

/*
 * Text attributes: the high nibble is the paper, the low nibble the ink, both
 * an EGA colour 0..15 -- the way a VGA card stores one. White on blue is 0x1F.
 */
.equ A_GREY,   0x07                 /* black paper, light grey ink */
.equ A_DKGREY, 0x08
.equ A_CYAN,   0x0B
.equ A_GREEN,  0x0A
.equ A_RED,    0x0C
.equ A_YELLOW, 0x0E
.equ A_WHITE,  0x0F
.equ A_BAR,    0x70                 /* light grey paper, black ink */
.equ A_CURSOR, 0x07                 /* black paper, light grey ink: a lit block */
.equ A_FOOT,   0x71                 /* light grey paper, blue ink */
.equ S_BG,     0x17                 /* setup: blue paper, light grey ink */
.equ S_TXT,    0x1F                 /* setup: blue paper, white ink */
.equ S_HEAD,   0x1E                 /* setup: blue paper, yellow ink */
.equ S_SEP,    0x1F                 /* setup: blue paper, white ink -- the frame */
.equ S_SEL,    0x70                 /* selected row: light grey paper, black */
.equ S_TABON,  0x70
.equ S_TABOFF, 0x1F

/* CP437 glyph codes the setup screen draws with (see tools/gen_rv64_font.py) */
.equ G_UP,     0x18
.equ G_DOWN,   0x19
.equ G_RIGHT,  0x1A
.equ G_LEFT,   0x1B
.equ G_PTR_R,  0x10                 /* a filled arrow, for the key legend */
.equ G_PTR_L,  0x11
.equ G_TRI_U,  0x1E
.equ G_TRI_D,  0x1F
.equ G_VBAR,   0xBA                 /* double vertical */
.equ G_HBAR,   0xCD                 /* double horizontal */
.equ G_TL,     0xC9                 /* top left */
.equ G_TR,     0xBB
.equ G_BL,     0xC8
.equ G_BR,     0xBC
.equ G_TJ,     0xCB                 /* T junctions */
.equ G_BJ,     0xCA
.equ G_SOLID,  0xDB                 /* full block */
.equ G_SHADE,  0xB1                 /* medium shade */
.equ G_DOT,    0xFA
.equ G_SMALL,  0xA0                 /* small block, the option bullet */

/* the setup screen's own geometry: the menu takes the room, the detail panel
 * only what a paragraph needs */
.equ SPLIT,    45                   /* the column rule */

/* a dialog: grey paper with black ink, a blue title bar, and a shadow that is
 * a dither of black over whatever it covers -- there is no alpha on the glass,
 * and a checker of dark pixels is what a 2017 setup utility does anyway */
.equ D_BODY,   0x70
.equ D_EDGE,   0x70                 /* the frame of a dialog: black on grey */
.equ D_TITLE,  0x1F
.equ D_SEL,    0x1F                 /* the lit line */
.equ D_SHADOW, 0x08

.section .text.init
.globl _start
_start:
    la      sp, stack_top
    li      s4, 0x50000000

    /* palette: 16 grey entries, then a red-to-blue ramp for the graphics plane */
    li      t0, 0
    li      t1, 16
1:  slli    t4, t0, 1
    add     t4, t4, t0
    li      t5, 0x50000
    add     t4, t4, t5
    add     t4, s4, t4
    slli    t5, t0, 4
    sb      t5, 0(t4)
    sb      t5, 1(t4)
    sb      t5, 2(t4)
    addi    t0, t0, 1
    blt     t0, t1, 1b
    li      t1, 256
2:  slli    t4, t0, 1
    add     t4, t4, t0
    li      t5, 0x50000
    add     t4, t4, t5
    add     t4, s4, t4
    sb      t0, 0(t4)
    li      t5, 64
    sb      t5, 1(t4)
    li      t5, 255
    sub     t5, t5, t0
    sb      t5, 2(t4)
    addi    t0, t0, 1
    blt     t0, t1, 2b

    /* the POST screen. `post` is a label because setup comes back here. */
post:
    li      s1, 0x10001000          /* PS/2 data port, for the setup key */
    /* Blank the whole screen first, the console rows included. Coming back
     * from setup, a line of the setup screen -- or of a dialog that was open
     * when the user chose to leave -- may be longer than the POST line that
     * replaces it, and a blank POST line writes nothing at all, so the old
     * text would survive. The prompt and the status bar are written after. */
    li      a0, 0
    li      a1, 28
    li      a2, A_GREY              /* black paper: the POST screen, not setup */
    call    fillRows
    li      s2, 0                   /* row */
    li      s3, 0                   /* attr */

    li      a1, A_CYAN
    la      a2, msg_title
    call    row

    li      a1, A_DKGREY
    la      a2, msg_rule
    call    row

    li      a1, A_GREY
    la      a2, msg_blank
    call    row

    li      a1, A_WHITE
    la      a2, msg_cpu
    call    row
    la      a2, msg_mem_pre
    call    row
    call    memRefresh            /* ask the chipset what it is really giving */
    lw      a0, memsize
    call    fmtNum
    li      t0, 80
    addi    t1, s2, -1
    mul     t0, t1, t0
    addi    a0, t0, 11
    li      a1, A_WHITE
    la      a2, numbuf
    call    puttxt
    li      t0, 80
    addi    t1, s2, -1
    mul     t0, t1, t0
    addi    a0, t0, 19
    li      a1, A_WHITE
    la      a2, msg_mem_post
    call    puttxt
    la      a2, msg_uart
    call    row
    la      a2, msg_disp
    call    row
    la      a2, msg_disk
    call    row

    /* storage: two regions with different manners, like a real board */
    call    nvramLoad             /* s0 = 1 when the NVRAM had a valid record */
    call    memSync               /* act on the stored size before the rows */
    li      a1, A_WHITE
    la      a2, msg_rom
    call    row
    beqz    s0, 21f
    li      a1, A_WHITE
    la      a2, msg_nvram_ok
    call    row
    j       22f
21: li      a1, A_YELLOW            /* it had to be rebuilt */
    la      a2, msg_nvram_new
    call    row
22:
    li      a1, A_CYAN
    la      a2, msg_map
    call    row
    li      a1, A_GREY
    la      a2, msg_map2
    call    row
    la      a2, msg_map3
    call    row

    li      a1, A_GREY
    la      a2, msg_blank
    call    row

    li      a1, A_GREEN
    la      a2, msg_post
    call    row

    /* the count a PC shows while it walks its RAM; any key skips it */
    call    memTest
    li      t1, 113                 /* Delete during the count opens setup too */
    bne     a0, t1, 2f
    call    setupScreen
    call    ps2Drain
    j       post
2:
    /* A firmware staged above POST (the generator puts a boot record at
     * 0x8004_3000) gets the hand-off a boot ROM would give it: the record
     * carries the values for a1/a2, and the code after it copies the payload
     * down over this program before jumping to it. */
    lui     s7, 0x80043             /* BOOTREC as a positive 64-bit address:
                                       `li` would sign-extend it to 0xFFFF_... */
    slli    s7, s7, 32
    srli    s7, s7, 32
    lw      t1, 0(s7)
    li      t2, 0x4A535742          /* 'JSWB' */
    bne     t1, t2, 23f
    li      a1, A_GREEN
    la      a2, msg_booting
    call    row
    li      a0, 0                   /* hart id */
    lwu     a1, 4(s7)               /* device tree (`lw` would sign-extend) */
    lwu     a2, 8(s7)               /* struct fw_dynamic_info */
    addi    t0, s7, 16              /* the code follows the record; `lw` would
                                       sign-extend the address instead */
    jr      t0
23:
    li      a1, A_RED
    la      a2, msg_noboot
    call    row

    /* the status line, in reverse video at the bottom */
    li      s2, 29
    li      a1, A_BAR
    la      a2, msg_status
    call    row

    /* The setup window. Delete during POST enters it, exactly like a PC; the
     * scan code is buffered by the controller even before the console exists. */
    call    setupMaybe

    /* one 32 x 16 block in the graphics plane, so mode 1 is not all black */
    li      t0, 0                   /* y */
3:  li      t2, 0                   /* x */
4:  li      t5, 320
    mul     t4, t0, t5
    add     t4, t4, t2
    li      t5, 0x2000
    add     t4, t4, t5
    add     t4, s4, t4
    xor     t5, t0, t2
    andi    t5, t5, 15
    addi    t5, t5, 16
    sb      t5, 0(t4)
    addi    t2, t2, 1
    li      t5, 32
    blt     t2, t5, 4b
    addi    t0, t0, 1
    li      t5, 16
    blt     t0, t5, 3b

    /* control: text mode (0x12300 does not fit an immediate) */
    li      t0, 0x50300
    add     t1, s4, t0
    sb      zero, 0(t1)

    /*
     * ---- the console ----
     * Rows 17..29 are a terminal: a prompt, the keys echoed after it, and a
     * scroll when the region fills up. `s5` is the cell the next character
     * goes to, `s6`/`s7` are the 0xE0 and 0xF0 prefixes of the last byte.
     */
    li      s1, 0x10001000          /* PS/2 data port */
    li      a0, 1360                /* row 17, column 0 */
    li      a1, A_YELLOW
    la      a2, msg_ready
    call    puttxt

    li      s5, 1370                /* just after the prompt */
    li      s6, 0
    li      s7, 0
    li      s9, 0                   /* shift is up */
    li      s10, 0                  /* control is up */
    li      s11, 1                  /* the block cursor is lit... */
    li      s8, 30                  /* ...for thirty frames, then it blinks */
    call    cursorDraw
5:  lbu     t0, 4(s1)               /* status */
    andi    t0, t0, 1
    bnez    t0, 13f                 /* something waiting */
    addi    s8, s8, -1              /* idle: one blink step a frame */
    bgtz    s8, 6f
    li      s8, 30
    xori    s11, s11, 1
    call    cursorDraw
6:  wfi                             /* idle a frame instead of a hot poll */
    j       5b
13: lbu     t0, 0(s1)               /* one scan code */

    li      t1, 224
    beq     t0, t1, 6f              /* 0xE0: the next code is extended */
    li      t1, 240
    beq     t0, t1, 7f              /* 0xF0: the next code is a release */

    /* shift is a modifier, not a character: it only changes which table the
     * next key is looked up in */
    li      t1, 18                  /* left shift */
    beq     t0, t1, 15f
    li      t1, 89                  /* right shift */
    beq     t0, t1, 15f

    /* control is counted too, for the same reason as shift */
    li      t1, 20                  /* left control */
    beq     t0, t1, 45f

    /* escape opens setup. Delete only works during POST, which is a window a
     * second long; this is the path that is always there. */
    li      t1, 118
    beq     t0, t1, 50f

    bnez    s7, 8f                  /* this code came after 0xF0: ignore it */
    bnez    s6, 8f                  /* extended keys are ignored for now */

    la      t1, keymap
    blez    s9, 19f                 /* shift held: the upper table */
    la      t1, keymap_shift
19: add     t1, t1, t0
    lbu     t2, 0(t1)
    beqz    t2, 8f                  /* unmapped: ignore */

    /*
     * With control held a letter is a control code, and the guest never sees
     * the letter: `c` becomes 0x03, `d` 0x04, `z` 0x1A. That arithmetic is the
     * whole ASCII layout, so there is no table for it.
     */
    blez    s10, 40f
    li      t1, 97                  /* 'a' */
    bge     t2, t1, 47f
    li      t1, 65                  /* 'A': shift is still down, ^C is ^C */
    blt     t2, t1, 40f
    li      t1, 90
    bgt     t2, t1, 40f
    addi    t2, t2, -64
    j       40f
47: li      t1, 122                 /* 'z' */
    bgt     t2, t1, 40f
    addi    t2, t2, -96
40: li      t1, 3
    beq     t2, t1, 41f             /* ^C: throw the line away */
    li      t1, 13
    bne     t2, t1, 42f
    li      t2, 10                  /* CR is the same enter as LF */
42: li      t1, 10
    beq     t2, t1, 9f              /* enter */
    li      t1, 8
    beq     t2, t1, 10f             /* backspace */
    li      t1, 32
    bge     t2, t1, 43f
    j       8f                      /* a control code with no glyph */
43: li      t1, 80
    remu    t1, s5, t1
    bnez    t1, 14f                 /* not at the end of the row */
    call    newline                 /* wrap: the next row, then a prompt */
14: la      t3, lptr
    lw      t4, 0(t3)
    li      t5, 90
    bge     t4, t5, pk_full
    la      t5, linebuf
    add     t5, t5, t4
    sb      t2, 0(t5)
    addi    t4, t4, 1
    sw      t4, 0(t3)
pk_full:
    slli    t1, s5, 1
    add     t1, s4, t1
    sb      t2, 0(t1)
    li      t3, A_WHITE
    sb      t3, 1(t1)
    addi    s5, s5, 1
    call    cursorWake
    j       8f
9:  call    shellEnter
    j       8f

10: li      t1, 80                  /* backspace: never eat the prompt */
    remu    t2, s5, t1
    li      t1, 10
    bleu    t2, t1, 12f
    addi    s5, s5, -1
    slli    t1, s5, 1
    add     t1, s4, t1
    li      t3, 32
    sb      t3, 0(t1)
    la      t3, lptr
    lw      t4, 0(t3)
    blez    t4, bs_done
    addi    t4, t4, -1
    sw      t4, 0(t3)
bs_done:
    call    cursorWake
12: j       8f

50: call    setupScreen             /* came back out: redraw, then the prompt */
    j       post

41: la      t3, lptr
    sw      zero, 0(t3)
    li      a0, 94                  /* '^' */
    call    putch
    li      a0, 67                  /* 'C' */
    call    putch
    call    newline                 /* and a fresh prompt, like a shell */
    j       8f

    /*
     * Shift is counted, not flagged. Two things press it: the key itself, and
     * the controller, which wraps a character that only exists shifted (`!`)
     * in a shift it makes and breaks on its own. A count is what keeps the two
     * from cancelling each other -- a `1` here leaked, and every key typed
     * after the first capital came out shifted.
     */
15: bnez    s6, 8f                  /* an 0xE0-prefixed 0x12 is not a shift */
    bnez    s7, 16f
    addi    s9, s9, 1               /* make: one more shift is down */
    j       8f
16: blez    s9, 8f                  /* break: never below zero */
    addi    s9, s9, -1
    j       8f

45: bnez    s6, 8f                  /* an 0xE0-prefixed 0x14 is not ctrl */
    bnez    s7, 46f
    addi    s10, s10, 1             /* make */
    j       8f
46: blez    s10, 8f                  /* break, never below zero */
    addi    s10, s10, -1
    j       8f

6:  li      s6, 1
    j       5b
7:  li      s7, 1
    j       5b
8:  li      s6, 0
    li      s7, 0
    j       5b

/*
 * Move the console to the next line and print the prompt there. Scrolling is
 * the guest's job for the same reason it is on a real board: the video card
 * knows nothing about a cursor.
 */
newline:
    addi    sp, sp, -16
    sd      ra, 8(sp)
    sd      a0, 0(sp)
    li      t0, 80
    remu    t1, s5, t0
    beqz    t1, 1f                  /* a wrapped line is already on the next row */
    sub     s5, s5, t1              /* back to the start of this row */
    addi    s5, s5, 80              /* one row down */
1:  li      t0, 2320                /* one past the console: row 29 is the bar */
    blt     s5, t0, 2f
    call    scrollcon
    li      s5, 2240                /* the last console row */
2:  mv      a0, s5
    li      a1, A_YELLOW
    la      a2, msg_ready
    call    puttxt
    addi    s5, s5, 10              /* the prompt is ten cells wide */
    call    cursorWake
    ld      a0, 0(sp)
    ld      ra, 8(sp)
    addi    sp, sp, 16
    ret

/*
 * Copy rows 18..28 over rows 17..27 and blank row 28. Row 29 is the status
 * bar, not part of the terminal: dragging it into the scroll would print
 * fragments of POST text in the middle of whatever the user is typing.
 */
scrollcon:
    addi    sp, sp, -32
    sd      ra, 24(sp)
    sd      s2, 16(sp)
    sd      s3, 8(sp)
    li      s2, 1360                /* the top of the console */
    li      s3, 2240                /* where the copying stops */
1:  addi    t0, s2, 80              /* the cell one row below */
    slli    t0, t0, 1
    add     t0, s4, t0
    lbu     t1, 0(t0)
    lbu     t2, 1(t0)
    slli    t0, s2, 1
    add     t0, s4, t0
    sb      t1, 0(t0)
    sb      t2, 1(t0)
    addi    s2, s2, 1
    blt     s2, s3, 1b
2:  slli    t0, s2, 1               /* and blank what is left */
    add     t0, s4, t0
    li      t1, 32
    sb      t1, 0(t0)
    li      t1, 0
    sb      t1, 1(t0)
    addi    s2, s2, 1
    li      t0, 2320
    blt     s2, t0, 2b
    ld      ra, 24(sp)
    ld      s2, 16(sp)
    ld      s3, 8(sp)
    addi    sp, sp, 32
    ret

/* a0 = a character at the console cursor, no wrap check (callers do that) */
putch:
    slli    t1, s5, 1
    add     t1, s4, t1
    sb      a0, 0(t1)
    li      t3, A_WHITE
    sb      t3, 1(t1)
    addi    s5, s5, 1
    ret

/* print a2 at row s2 in attr a1, then the next row */
row:
    addi    sp, sp, -16
    sd      ra, 8(sp)
    sd      s2, 0(sp)
    li      t0, 80
    mul     a0, s2, t0
    call    puttxt
    ld      s2, 0(sp)
    addi    s2, s2, 1
    ld      ra, 8(sp)
    addi    sp, sp, 16
    ret

/* a0 = cell index, a1 = attr, a2 = NUL-terminated string */
puttxt:
    addi    sp, sp, -16
    sd      ra, 8(sp)
    sd      s6, 0(sp)
    mv      s6, a0
6:  lbu     t0, 0(a2)
    beqz    t0, 7f
    slli    t1, s6, 1
    add     t1, s4, t1
    sb      t0, 0(t1)
    sb      a1, 1(t1)
    addi    s6, s6, 1
    addi    a2, a2, 1
    j       6b
7:  ld      s6, 0(sp)
    ld      ra, 8(sp)
    addi    sp, sp, 16
    ret

.section .rodata
msg_title:  .asciz "rv64 bios 0.1 - RV64IMAC, display first"
msg_rule:   .asciz "-------------------------------------------------------------------------------"
msg_blank:  .asciz ""
msg_cpu:    .asciz "cpu      rv64imac, 32 registers, pc 0x80000000"
msg_mem_pre:  .asciz "memory    "
msg_mem_post: .asciz " KiB of ram at 0x80000000"
msg_memtest:  .asciz "memory test "
msg_ok:       .asciz "K OK"
msg_uart:   .asciz "uart     16550 at 0x10000000, one byte per store"
msg_disp:   .asciz "display  80x30 text and 320x200x8 graphics at 0x50000000"
msg_disk:   .asciz "disk     block window at 0x40000000 over ws://127.0.0.1:65433"
msg_map:    .asciz "text   +0x00000  80 x 30 cells of (char, attr)"
msg_map2:   .asciz "gfx    +0x02000  320 x 200 x 8bpp"
msg_map3:   .asciz "pal    +0x50000 256 x (r, g, b)    ctl +0x50300 mode, halt"
msg_post:   .asciz "POST: cpu ok, memory ok, uart ok, display ok, no disk read yet"
msg_setup_title: .asciz " JSW BIOS Setup Utility                                 v0.2 "
msg_setup_keys:  .asciz " ^/v select   </> change   Enter next   S save   Esc discard "
msg_setup_saved: .asciz "saved"
msg_rom:    .asciz "rom      0x00001000, the firmware image, read-only"
msg_nvram_ok:  .asciz "nvram    0x10002000, 256 bytes, settings loaded"
msg_nvram_new: .asciz "nvram    0x10002000, 256 bytes, no record: defaults written"
msg_noboot: .asciz "no bootable device"
msg_booting: .asciz "boot: staged firmware, copying it into place"
msg_status: .asciz " POST complete - Esc opens setup, DEL works during POST, or just type "
msg_ready:  .asciz "keyboard> "
msg_badaddr: .asciz "mw: an address is required"
msg_badcmd: .asciz " ? for the commands"
msg_help1:  .asciz " md ADDR [N]  dump N bytes (16)   mw ADDR B.. write bytes"
msg_help2:  .asciz " r regs+CSRs  t clock  v mode  n arp  cls clear  boot reset"
msg_r_pc:   .asciz " pc "
msg_r_sp:   .asciz "  sp "
msg_r_mtvec: .asciz " mtvec "
msg_r_mepc: .asciz "  mepc "
msg_r_cause: .asciz " mcause "
msg_r_mie:  .asciz "  mie "
msg_t_time: .asciz " mtime "
msg_t_cmp:  .asciz "  mtimecmp "
msg_bytes:  .asciz " bytes written"
msg_video:  .asciz " (0 text, 1 320x200, 2 640x480)"
msg_net_reply: .asciz " arp reply "
msg_net_none:  .asciz " no reply (is the host bridge running?)"
/* who-has 10.0.2.2 tell 10.0.2.15: the standard 42-byte ARP request */
arp_req:
    .byte 255,255,255,255,255,255
    .byte 0x52,0x54,0x00,0x12,0x34,0x56
    .byte 0x08,0x06
    .byte 0x00,0x01,0x08,0x00,0x06,0x04,0x00,0x01
    .byte 0x52,0x54,0x00,0x12,0x34,0x56
    .byte 10,0,2,15
    .byte 0,0,0,0,0,0
    .byte 10,0,2,2
arp_req_end:

/* scan code -> ASCII, set-2 make codes; 0 = not mapped. `keymap_shift` is the
 * same table with shift held: a keyboard controller does not translate, it only
 * sends the code, so tracking the modifier is the BIOS's business -- which is
 * why shift must never turn into a character of its own. */
.section .rodata
.align 4
keymap:
    .byte 0,0,0,0,0,0,0,0,0,0,0,0,0,9,96,0
    .byte 0,0,0,0,0,113,49,0,0,0,122,115,97,119,50,0
    .byte 0,99,120,100,101,52,51,0,0,32,118,102,116,114,53,0
    .byte 0,110,98,104,103,121,54,0,0,0,109,106,117,55,56,0
    .byte 0,44,107,105,111,48,57,0,0,46,47,108,59,112,45,0
    .byte 0,0,39,0,91,61,0,0,0,0,10,93,0,92,0,0
    .byte 0,0,0,0,0,0,8,0,0,0,0,0,0,0,0,0
    .byte 0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0

keymap_shift:
    .byte 0,0,0,0,0,0,0,0,0,0,0,0,0,9,126,0
    .byte 0,0,0,0,0,81,33,0,0,0,90,83,65,87,64,0
    .byte 0,67,88,68,69,36,35,0,0,32,86,70,84,82,37,0
    .byte 0,78,66,72,71,89,94,0,0,0,77,74,85,38,42,0
    .byte 0,60,75,73,79,41,40,0,0,62,63,76,58,80,95,0
    .byte 0,0,34,0,123,43,0,0,0,0,10,125,0,124,0,0
    .byte 0,0,0,0,0,0,8,0,0,0,0,0,0,0,0,0
    .byte 0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0


.section .text
/* ------------------------------------------------------------------ */
/* the chipset: how much RAM the board gives, and the reset that does it */
/* ------------------------------------------------------------------ */

/* Read the board and keep the answer in `memmib` (MiB) and `memsize` (KiB). */
memRefresh:
    li      t0, BOARD
    lw      t1, B_NOW(t0)
    la      t2, memmib
    sw      t1, 0(t2)
    li      t3, 1024
    mul     t1, t1, t3
    la      t2, memsize
    sw      t1, 0(t2)
    ret

/*
 * Bring the board's memory size in line with the record.
 *
 * A change needs a reboot -- the map the firmware is standing on has to be the
 * map the guest gets -- so this asks the board for the new size, presses its
 * reset button, and never comes back: POST runs again with the new RAM and the
 * two agree. `B_RESETS` is what makes that happen at most once. If the board
 * could not give what was asked (it clamps, or the browser refused the
 * allocation) the record takes what the board actually gave, which is the only
 * way this cannot turn into a reset loop.
 */
memSync:
    addi    sp, sp, -16
    sd      ra, 8(sp)
    la      t0, cfg
    lhu     t1, 4(t0)               /* the MiB the record asks for */
    li      t2, MEM_MAX
    bleu    t1, t2, ms_low
    li      t1, MEM_MAX
    sh      t1, 4(t0)
ms_low:
    bnez    t1, ms_read
    li      t1, MEM_MIN
    sh      t1, 4(t0)
ms_read:
    call    memRefresh
    la      t0, memmib
    lw      t0, 0(t0)
    la      t1, cfg
    lhu     t1, 4(t1)
    beq     t0, t1, ms_done
    li      t2, BOARD
    lw      t3, B_RESETS(t2)
    bnez    t3, ms_adopt            /* it has already rebooted once */
    sw      t1, B_MEM(t2)
    li      t3, 1
    sw      t3, B_CMD(t2)           /* whoever runs next sees the new RAM */
    j       ms_halt
ms_adopt:
    /* Write the number the board really gave back, so the next boot does not
     * ask again and spend another reset on it. */
    la      t1, cfg
    sh      t0, 4(t1)
    call    nvramWrite
ms_done:
    call    fmtMem
    ld      ra, 8(sp)
    addi    sp, sp, 16
    ret
ms_halt:
    /* The board resets between frames, and the guest must not run on with a
     * memory map that is about to move under it. */
    j       ms_halt

/* a0 = value, a1 = where to write it: decimal, no padding, NUL-terminated.
 * Returns a1 pointing at that NUL, so a caller can append to it. */
fmtDec:
    addi    sp, sp, -16
    sd      ra, 8(sp)
    sd      a1, 0(sp)
    call    fmtNum                  /* numbuf: eight cells, space padded */
    la      t0, numbuf
fd_skip:
    lbu     t1, 0(t0)
    li      t2, 32
    bne     t1, t2, fd_copy
    addi    t0, t0, 1
    j       fd_skip
fd_copy:
    ld      t3, 0(sp)
fd_next:
    lbu     t1, 0(t0)
    sb      t1, 0(t3)
    beqz    t1, fd_out
    addi    t0, t0, 1
    addi    t3, t3, 1
    j       fd_next
fd_out:
    mv      a1, t3
    ld      ra, 8(sp)
    addi    sp, sp, 16
    ret

/* t0 = source, a1 = where to write it: copy a whole string, a1 ends at the NUL */
strCopy:
    lbu     t2, 0(t0)
    sb      t2, 0(a1)
    beqz    t2, sc_out
    addi    t0, t0, 1
    addi    a1, a1, 1
    j       strCopy
sc_out:
    ret

/* a1 = a NUL, t2 = where the string must end: spaces up to t2, then a NUL */
strPadTo:
    li      t3, 32
sp_next:
    bgeu    a1, t2, sp_done
    sb      t3, 0(a1)
    addi    a1, a1, 1
    j       sp_next
sp_done:
    sb      zero, 0(t2)
    ret

/*
 * The two strings the screens show for the memory size: `memtxt` is the value
 * column of the menu ("1024 MiB", ten cells) and `meminfo` is the row in the
 * panel ("Memory   1024 MiB at 0x80000000", thirty-three cells). Both are
 * space padded, because a row is redrawn by overwriting it and a shorter string
 * would leave the tail of the old one on the glass.
 */
fmtMem:
    addi    sp, sp, -16
    sd      ra, 8(sp)

    /* the menu shows the size the record asks for, so a number typed into the
     * window is visible before it is saved -- and the panel keeps showing what
     * the board is running, because that is what the guest actually has */
    la      t0, cfg
    lhu     a0, 4(t0)
    la      a1, memtxt
    call    fmtDec
    la      t0, str_mem_suffix
    call    strCopy
    la      t2, memtxt
    addi    t2, t2, 10
    call    strPadTo

    lw      a0, memmib
    la      a1, memnow
    call    fmtDec
    la      t0, str_mem_suffix
    call    strCopy
    la      t2, memnow
    addi    t2, t2, 10
    call    strPadTo

    la      t0, str_meminfo_pre
    la      a1, meminfo
    call    strCopy
    lw      a0, memmib
    call    fmtDec
    la      t0, str_meminfo_post
    call    strCopy
    la      t2, meminfo
    addi    t2, t2, 33
    call    strPadTo

    ld      ra, 8(sp)
    addi    sp, sp, 16
    ret


/* ------------------------------------------------------------------ */
/* storage: the NVRAM record and the setup screen                     */
/* ------------------------------------------------------------------ */

/*
 * The NVRAM record, 12 bytes of header in a 256-byte page:
 *
 *   +0  'J' 'S' 'W' '2'   magic: an erased page has none, so a blank NVRAM
 *                         is a first boot rather than a corrupt one
 *   +4  version            2
 *   +5  boot device        0 none, 1 disk
 *   +6  text attribute     1..15
 *   +7  graphics demo      0 off, 1 on
 *   +8  memory             the RAM to give the guest, in MiB, 16-bit LE
 *   +10 checksum           16-bit sum of bytes 0..9
 *   +12 zero
 *
 * The bus moves bytes; the layout is the firmware's business. That split is
 * why core.jsw knows nothing about any of these fields -- the memory size is
 * the one exception, and it goes through the chipset window rather than a
 * field name the board would have to know.
 *
 * The working copy in `cfg` is byte 0 boot, 1 attribute, 2 graphics, and 3..4
 * the size in MiB, so the screen and the record keep the same order.
 */

/* s0 = 1 when a valid record was found, 0 when the defaults had to be written */
nvramLoad:
    addi    sp, sp, -32
    sd      ra, 24(sp)
    li      s7, NVRAM               /* the page */
    la      s6, cfg                 /* the working copy */

    lbu     t1, 0(s7)
    li      t2, 74                  /* 'J' */
    bne     t1, t2, nv_default
    lbu     t1, 1(s7)
    li      t2, 83                  /* 'S' */
    bne     t1, t2, nv_default
    lbu     t1, 2(s7)
    li      t2, 87                  /* 'W' */
    bne     t1, t2, nv_default
    lbu     t1, 3(s7)
    li      t2, 50                  /* '2' */
    bne     t1, t2, nv_default
    lbu     t1, 4(s7)
    li      t2, 2                   /* version */
    bne     t1, t2, nv_default

    call    nvChecksum              /* t3 = the sum of bytes 0..9 */
    lbu     t1, 10(s7)
    lbu     t2, 11(s7)
    slli    t2, t2, 8
    or      t2, t2, t1
    bne     t3, t2, nv_default

    /* a good record: adopt it */
    lbu     t1, 5(s7)
    sb      t1, 0(s6)               /* boot device */
    lbu     t1, 6(s7)
    sb      t1, 1(s6)               /* attribute */
    lbu     t1, 7(s7)
    sb      t1, 2(s6)               /* graphics */
    lbu     t1, 8(s7)
    lbu     t2, 9(s7)
    slli    t2, t2, 8
    or      t1, t1, t2
    sh      t1, 4(s6)               /* memory, MiB, on an aligned halfword */
    call    cfgClamp
    li      s0, 1
    ld      ra, 24(sp)
    addi    sp, sp, 32
    ret

nv_default:
    /* First boot, or the record does not survive a checksum: defaults. */
    li      t1, 1                   /* boot from disk when there is one */
    sb      t1, 0(s6)
    li      t1, 15                  /* white */
    sb      t1, 1(s6)
    li      t1, 0                   /* graphics demo off */
    sb      t1, 2(s6)
    /* The size a fresh record asks for is the size the board already gives,
     * so a first boot never spends a reset on a number nobody chose. */
    call    memRefresh
    la      t0, memmib
    lw      t0, 0(t0)
    sh      t0, 4(s6)
    call    nvramWrite
    li      s0, 0
    ld      ra, 24(sp)
    addi    sp, sp, 32
    ret

/* t3 = sum of NVRAM bytes 0..9 (s7 must point at the page) */
nvChecksum:
    li      t3, 0
    li      t4, 0
1:  add     t5, s7, t4
    lbu     t6, 0(t5)
    add     t3, t3, t6
    addi    t4, t4, 1
    li      t6, 10
    blt     t4, t6, 1b
    ret

/* keep a record that came from elsewhere inside the values the screen knows */
cfgClamp:
    la      t0, cfg
    lbu     t1, 0(t0)
    li      t2, 1
    bleu    t1, t2, 1f
    sb      zero, 0(t0)             /* anything but 0/1 means none */
1:  lbu     t1, 1(t0)
    bnez    t1, 2f
    li      t1, 15
    sb      t1, 1(t0)
2:  li      t2, 15
    lbu     t1, 1(t0)
    bleu    t1, t2, 3f
    li      t1, 15
    sb      t1, 1(t0)
3:  lbu     t1, 2(t0)
    bleu    t1, t2, 4f
    sb      zero, 2(t0)
4:  lhu     t1, 4(t0)
    li      t2, MEM_MAX
    bleu    t1, t2, 5f
    li      t1, MEM_MAX
    sh      t1, 4(t0)
5:  bnez    t1, 6f
    li      t1, MEM_MIN
    sh      t1, 4(t0)
6:  ret

/* write cfg back as a complete record, checksum included */
nvramWrite:
    addi    sp, sp, -16
    sd      ra, 8(sp)
    la      s6, cfg
    li      s7, NVRAM

    li      t1, 74
    sb      t1, 0(s7)               /* magic */
    li      t1, 83
    sb      t1, 1(s7)
    li      t1, 87
    sb      t1, 2(s7)
    li      t1, 50
    sb      t1, 3(s7)
    li      t1, 2
    sb      t1, 4(s7)               /* version */

    lbu     t1, 0(s6)
    sb      t1, 5(s7)
    lbu     t1, 1(s6)
    sb      t1, 6(s7)
    lbu     t1, 2(s6)
    sb      t1, 7(s7)
    lhu     t1, 4(s6)
    sb      t1, 8(s7)               /* memory, low byte */
    srli    t2, t1, 8
    sb      t2, 9(s7)               /* and high */

    call    nvChecksum
    sb      t3, 10(s7)
    srli    t3, t3, 8
    sb      t3, 11(s7)

    li      t4, 12                  /* and a clean tail */
    li      t6, 256
1:  add     t5, s7, t4
    sb      zero, 0(t5)
    addi    t4, t4, 1
    blt     t4, t6, 1b
    ld      ra, 8(sp)
    addi    sp, sp, 16
    ret

/*
 * A one-second window for Delete, the way a PC says "press DEL to enter
 * setup". A frame is this machine's quantum and WFI waits for the next one,
 * so the deadline is real time in both the Scratch build and the reference.
 */
setupMaybe:
    addi    sp, sp, -16
    sd      ra, 8(sp)
    csrr    s7, 0xc01               /* rdtime: mtime, 10 MHz */
sm_drain:
    lbu     t0, 4(s1)
    andi    t0, t0, 1
    beqz    t0, sm_wait
    lbu     t0, 0(s1)               /* POST drains its buffer, like a PC */
    li      t1, 113                 /* delete */
    beq     t0, t1, sm_enter
    j       sm_drain
sm_wait:
    csrr    t2, 0xc01
    sub     t2, t2, s7
    li      t3, 10000000            /* 1 s */
    bgeu    t2, t3, sm_done
    wfi
    j       sm_drain
sm_enter:
    call    setupScreen
    /* the key that got us in may still be repeating: drain before the redraw,
     * or every make of a held Delete re-enters setup */
    call    ps2Drain
    j       post
sm_done:
    ld      ra, 8(sp)
    addi    sp, sp, 16
    ret

/* throw away whatever is queued, bounded so a stuck line cannot hang POST */
ps2Drain:
    li      t0, 64
1:  lbu     t1, 4(s1)
    andi    t1, t1, 1
    beqz    t1, 2f
    lbu     t1, 0(s1)
    addi    t0, t0, -1
    bgtz    t0, 1b
2:  ret


/* ------------------------------------------------------------------ */
/* the screen: tabs, a menu, a detail panel, a footer                 */
/* ------------------------------------------------------------------ */

/*
 * 80x30, the whole glass blue:
 *
 *   row  0        tab bar: Main  Info  Exit
 *   row  1        rule
 *   rows 4..9     the menu, one item a row      |  the detail panel
 *   row 28        rule
 *   row 29        the key legend
 *
 * Every text run is a fixed-width string, so a row is redrawn by overwriting
 * it and never has to be cleared first -- the same trick a real setup program
 * uses, and it keeps the painter's work proportional to what changed.
 */

/* s10 = the selected tab's first item, s11 = how many it has */
tabBase:
    la      t0, tab_base
    slli    t1, s9, 2
    add     t0, t0, t1
    lw      s10, 0(t0)
    la      t0, tab_n
    add     t0, t0, t1
    lw      s11, 0(t0)
    ret

/* a0 = item index -> a0 = its kind (0 info, 1..3 setting, 4 save, 5 discard) */
itemKind:
    la      t0, items_kind
    add     t0, t0, a0
    lbu     a0, 0(t0)
    ret

/* a0 = cell, a1 = attr, a2 = character, a3 = how many */
fillCells:
1:  slli    t0, a0, 1
    add     t0, s4, t0
    sb      a2, 0(t0)
    sb      a1, 1(t0)
    addi    a0, a0, 1
    addi    a3, a3, -1
    bgtz    a3, 1b
    ret

/* a0 = row, a1 = attr, a2 = character: the whole 80-cell row */
fillRow:
    addi    sp, sp, -16
    sd      ra, 8(sp)
    li      t0, 80
    mul     a0, a0, t0
    li      a3, 80
    call    fillCells
    ld      ra, 8(sp)
    addi    sp, sp, 16
    ret

/* a0..a1 = rows inclusive, a2 = attr: blank them */
fillRows:
    addi    sp, sp, -32
    sd      ra, 24(sp)
    sd      s2, 16(sp)
    sd      s3, 8(sp)
    sd      a2, 0(sp)
    mv      s2, a0
    mv      s3, a1
1:  mv      a0, s2
    ld      a1, 0(sp)
    li      a2, 32
    call    fillRow
    addi    s2, s2, 1
    ble     s2, s3, 1b
    ld      ra, 24(sp)
    ld      s2, 16(sp)
    ld      s3, 8(sp)
    addi    sp, sp, 32
    ret

/* the value of a setting item, as a pointer to a fixed-width string */
/* a0 = item index -> a2 = the string */
itemValue:
    la      t0, items_val
    slli    t1, a0, 3
    add     t0, t0, t1
    ld      a2, 0(t0)
    bnez    a2, iv_done         /* a static value needs no work */
    la      t0, items_kind
    add     t0, t0, a0
    lbu     t0, 0(t0)
    li      t1, 1
    beq     t0, t1, iv_boot
    li      t1, 2
    beq     t0, t1, iv_ink
    li      t1, 3
    beq     t0, t1, iv_gfx
    /* kind 6 is the setting (the record's number), 7 is the fact (the board's) */
    li      t1, 7
    beq     t0, t1, iv_now
    la      a2, memtxt
    ret
iv_now:
    la      a2, memnow
    ret
iv_gfx:
    la      a2, val_off
    la      t1, cfg
    lbu     t1, 2(t1)
    beqz    t1, iv_done
    la      a2, val_on
    ret
iv_boot:
    la      a2, val_none
    la      t1, cfg
    lbu     t1, 0(t1)
    beqz    t1, iv_done
    la      a2, val_disk
    ret
iv_ink:
    la      a2, val_grey
    la      t1, cfg
    lbu     t1, 1(t1)
    li      t2, 15
    beq     t1, t2, iv_white
    li      t2, 11
    beq     t1, t2, iv_cyan
    ret
iv_white:
    la      a2, val_white
    ret
iv_cyan:
    la      a2, val_cyan
    ret
iv_done:
    ret

/* one menu row: a0 = item index, a1 = the row, a2 = 1 when selected */
drawItem:
    addi    sp, sp, -32
    sd      ra, 24(sp)
    sd      s2, 16(sp)
    sd      s3, 8(sp)
    sd      s7, 0(sp)
    mv      s2, a0                 /* item */
    mv      s3, a1                 /* row */
    li      s7, S_TXT              /* label ink */
    li      t6, S_HEAD             /* value ink */
    beqz    a2, 1f
    li      s7, S_SEL              /* the selected row: black on light grey */
    li      t6, S_SEL
    li      t0, 80
    mul     t0, s3, t0
    addi    a0, t0, 1
    li      a1, S_SEL
    li      a2, 32
    li      a3, 43
    call    fillCells
1:
    /* the label: 22 cells from column 2 */
    li      t0, 80
    mul     t0, s3, t0
    addi    a0, t0, 2
    mv      a1, s7
    la      t0, items_lbl
    slli    t1, s2, 3
    add     t0, t0, t1
    ld      a2, 0(t0)
    call    puttxt

    /* the value: 10 cells ending at the column rule */
    mv      a0, s2
    call    itemValue              /* -> a2 */
    mv      s7, a2
    li      t0, 80
    mul     t0, s3, t0
    addi    a0, t0, 34
    mv      a1, t6
    mv      a2, s7
    call    puttxt

    ld      ra, 24(sp)
    ld      s2, 16(sp)
    ld      s3, 8(sp)
    ld      s7, 0(sp)
    addi    sp, sp, 32
    ret

/* the whole screen, from s8 (selected item) and s9 (tab) */
drawSetup:
    addi    sp, sp, -40
    sd      ra, 32(sp)
    sd      s2, 24(sp)
    sd      s3, 16(sp)
    sd      s8, 8(sp)
    sd      s9, 0(sp)

    call    fmtMem                /* the menu may show a number just typed */
    /* blue everywhere, then the rules */
    li      a0, 0
    li      a1, 29
    li      a2, S_BG
    call    fillRows
    /* a double-line box, the way a setup utility frames itself */
    li      a0, 1
    li      a1, S_SEP
    li      a2, G_HBAR
    call    fillRow
    li      a0, 28
    li      a1, S_SEP
    li      a2, G_HBAR
    call    fillRow

    li      a0, 80                  /* row 1 */
    li      a1, S_SEP
    li      a2, G_TL
    call    putcell
    li      a0, 159
    li      a1, S_SEP
    li      a2, G_TR
    call    putcell
    li      a0, 2240                /* row 28 */
    li      a1, S_SEP
    li      a2, G_BL
    call    putcell
    li      a0, 2319
    li      a1, S_SEP
    li      a2, G_BR
    call    putcell
    li      a0, 125                 /* the T junctions with the column rule */
    li      a1, S_SEP
    li      a2, G_TJ
    call    putcell
    li      a0, 2285
    li      a1, S_SEP
    li      a2, G_BJ
    call    putcell

    /* the vertical rule at column 45: the menu gets the room, the panel
     * only what a paragraph needs */
    li      s3, 2
    li      s2, 28
7:  li      t0, 80
    mul     a0, s3, t0
    addi    a0, a0, SPLIT
    li      a1, S_SEP
    li      a2, G_VBAR
    call    putcell
    addi    s3, s3, 1
    blt     s3, s2, 7b

    /* the tabs */
    li      s3, 0
8:  la      t0, tab_col
    slli    t1, s3, 2
    add     t0, t0, t1
    lw      a0, 0(t0)
    li      a1, S_TABOFF
    bne     s3, s9, 9f
    li      a1, S_TABON
9:  la      t0, tab_ptr
    slli    t1, s3, 3              /* tab_ptr is quads, tab_col was words */
    add     t0, t0, t1
    ld      a2, 0(t0)
    call    puttxt
    addi    s3, s3, 1
    li      t0, 3
    blt     s3, t0, 8b

    /* the version, hard right of the tab bar */
    li      a0, 74
    li      a1, S_TXT
    la      a2, msg_version
    call    puttxt

    /* the heading over the menu */
    la      t0, tab_head
    slli    t1, s9, 3
    add     t0, t0, t1
    ld      a2, 0(t0)
    li      a0, 162                /* row 2, column 2 */
    li      a1, S_HEAD
    call    puttxt

    /* the menu */
    mv      s2, s10                /* item */
    li      s3, 4                  /* row */
10: add     t0, s10, s11
    bge     s2, t0, 12f
    mv      a0, s2
    mv      a1, s3
    li      a2, 0
    bne     s2, s8, 11f
    li      a2, 1
11: call    drawItem
    addi    s2, s2, 1
    addi    s3, s3, 1
    j       10b

12: /* the detail panel */
    la      t0, items_lbl
    slli    t1, s8, 3
    add     t0, t0, t1
    ld      a2, 0(t0)
    li      a0, 207                /* row 2, column 47 */
    li      a1, S_HEAD
    call    puttxt

    la      t0, items_d1
    slli    t1, s8, 3
    add     t0, t0, t1
    ld      a2, 0(t0)
    li      a0, 367                /* row 4, column 47 */
    li      a1, S_TXT
    call    puttxt

    la      t0, items_d2
    slli    t1, s8, 3
    add     t0, t0, t1
    ld      a2, 0(t0)
    li      a0, 447                /* row 5, column 47 */
    li      a1, S_TXT
    call    puttxt

    /* a setting says how to change it; an action or a fact does not */
    mv      a0, s8
    call    itemKind
    beqz    a0, 13f
    li      t0, 6
    beq     a0, t0, 14f
    li      t0, 4
    bge     a0, t0, 13f
14:
    li      a0, 607                /* row 7, column 47 */
    li      a1, S_SEP
    la      a2, msg_hint
    call    puttxt

13: /* the system summary, under the detail panel: the facts a setup utility
     * always shows and never lets you change */
    li      a0, 847                /* row 10, column 47 */
    li      a1, S_HEAD
    la      a2, msg_system
    call    puttxt
    li      a0, 927                /* row 11 */
    li      a1, S_BG
    la      a2, info_cpu
    call    puttxt
    li      a0, 1007               /* row 12 */
    li      a1, S_BG
    la      a2, meminfo
    call    puttxt
    li      a0, 1087               /* row 13 */
    li      a1, S_BG
    la      a2, info_rom
    call    puttxt
    li      a0, 1167               /* row 14 */
    li      a1, S_BG
    la      a2, info_nvram
    call    puttxt
    li      a0, 1247               /* row 15 */
    li      a1, S_BG
    la      a2, info_clock
    call    puttxt

    /* the footer: the bar, the keys, the version */
    li      a0, 29
    li      a1, S_BG
    li      a2, 32
    call    fillRow
    li      a0, 2322               /* row 29, column 2 */
    li      a1, S_TXT
    la      a2, msg_footer
    call    puttxt

    ld      ra, 32(sp)
    ld      s2, 24(sp)
    ld      s3, 16(sp)
    ld      s8, 8(sp)
    ld      s9, 0(sp)
    addi    sp, sp, 40
    ret

setupScreen:
    addi    sp, sp, -48
    sd      ra, 40(sp)
    sd      s8, 32(sp)
    sd      s9, 24(sp)
    sd      s10, 16(sp)
    sd      s11, 8(sp)
    la      s6, cfg
    li      s9, 0                  /* the Main tab */
    li      s8, 0                  /* its first item */
    call    tabBase

ss_loop:
    call    drawSetup
    call    getMake                /* a0 = make code, a1 = 1 when extended */
    mv      t2, a0

    li      t1, 27                 /* 's' */
    beq     t2, t1, ss_save
    li      t1, 118                /* escape: never throws work away unasked */
    beq     t2, t1, ss_ask
    li      t1, 13                 /* tab: the next section */
    beq     t2, t1, ss_next
    li      t1, 90                 /* enter */
    beq     t2, t1, ss_enter
    beqz    a1, ss_loop

    li      t1, 117                /* up */
    beq     t2, t1, ss_up
    li      t1, 114                /* down */
    beq     t2, t1, ss_down
    li      t1, 107                /* left: the section before */
    beq     t2, t1, ss_prev
    li      t1, 116                /* right: the section after */
    beq     t2, t1, ss_next
    j       ss_loop

ss_next:
    addi    a0, s9, 1
    li      t0, 3
    blt     a0, t0, ss_goto
    li      a0, 0
    j       ss_goto

ss_prev:
    addi    a0, s9, -1
    bge     a0, zero, ss_goto
    li      a0, 2

ss_goto:
    mv      s9, a0
    call    tabBase
    mv      s8, s10
    j       ss_loop

ss_up:
    addi    s8, s8, -1
    bge     s8, s10, ss_loop
    add     s8, s10, s11
    addi    s8, s8, -1
    j       ss_loop

ss_down:
    addi    s8, s8, 1
    add     t0, s10, s11
    blt     s8, t0, ss_loop
    mv      s8, s10
    j       ss_loop

ss_enter:
    mv      a0, s8
    call    itemKind
    beqz    a0, ss_loop            /* an information row leads nowhere */
    li      t0, 4
    beq     a0, t0, ss_save
    li      t0, 5
    beq     a0, t0, ss_ask
    li      t0, 6
    beq     a0, t0, ss_number
    li      t0, 7
    beq     a0, t0, ss_loop        /* a memory row on the info tab */
    mv      a0, s8                 /* a setting: open the list of values */
    call    optionDialog
    j       ss_loop

ss_number:
    la      t0, cfg
    lhu     a0, 4(t0)              /* the size in force */
    mv      a1, s8
    call    numDialog              /* -> a0 = the new size, or -1 */
    bltz    a0, ss_loop
    la      t0, cfg
    sh      a0, 4(t0)
    j       ss_loop

/* Escape and "Exit without saving" both land here. The machine asks first,
 * and only a keypress that means "throw it away" throws it away. */
ss_ask:
    call    exitDialog             /* a0 = 0 keep, 1 save, 2 discard */
    li      t0, 1
    beq     a0, t0, ss_save
    li      t0, 2
    beq     a0, t0, ss_discard
    j       ss_loop

ss_save:
    call    nvramWrite
    li      a0, 12
    li      a1, 28
    li      a2, 24
    li      a3, 4
    la      a4, msg_saved
    call    showDialog
    li      a0, 1150               /* row 14, column 30 */
    li      a1, D_BODY
    la      a2, msg_saved_body
    call    puttxt
    /* show it for half a second. The wait has to WFI: `mtime` moves with
     * frames, and a tight loop reading it would never see it change. */
    csrr    t0, 0xc01
    li      t1, 5000000
    add     t0, t0, t1
1:  csrr    t2, 0xc01
    bgeu    t2, t0, ss_exit
    wfi
    j       1b

ss_discard:
    call    nvramLoad              /* no write: reload what is stored */

ss_exit:
    ld      ra, 40(sp)
    ld      s8, 32(sp)
    ld      s9, 24(sp)
    ld      s10, 16(sp)
    ld      s11, 8(sp)
    addi    sp, sp, 48
    ret

/* a0 = row, a1 = column, a2 = width, a3 = height, a4 = attr, a5 = character */
fillRect:
    li      t6, 80
    mul     t0, a0, t6
    add     t0, t0, a1
    li      t1, 0
1:  bge     t1, a3, 3f
    mv      t2, t0
    mv      t3, a2
2:  blez    t3, 4f
    slli    t4, t2, 1
    add     t4, s4, t4
    sb      a5, 0(t4)
    sb      a4, 1(t4)
    addi    t2, t2, 1
    addi    t3, t3, -1
    j       2b
4:  add     t0, t0, t6
    addi    t1, t1, 1
    j       1b
3:  ret

/* a0 = top, a1 = left, a2 = width, a3 = height, a4 = title
 *
 * A grey panel with a blue title bar and a solid black shadow. The title gets
 * its own row *inside* the frame: a double rule drawn through the text reads
 * as a broken corner, which is exactly how it looked.
 */
showDialog:
    addi    sp, sp, -48
    sd      ra, 40(sp)
    sd      s2, 32(sp)
    sd      s3, 24(sp)
    sd      s5, 16(sp)
    sd      s6, 8(sp)
    sd      s7, 0(sp)
    mv      s2, a0                  /* top */
    mv      s3, a1                  /* left */
    mv      s5, a2                  /* width */
    mv      s7, a3                  /* height */
    mv      s6, a4                  /* title */

    /* the shadow: solid black, one row down and two columns right */
    addi    a0, s2, 1
    addi    a1, s3, 2
    mv      a2, s5
    mv      a3, s7
    li      a4, 0
    li      a5, 32
    call    fillRect

    /* the body */
    mv      a0, s2
    mv      a1, s3
    mv      a2, s5
    mv      a3, s7
    li      a4, D_BODY
    li      a5, 32
    call    fillRect

    /* the top rule, with its own corners */
    mv      a0, s2
    mv      a1, s3
    mv      a2, s5
    li      a3, 1
    li      a4, D_EDGE
    li      a5, G_HBAR
    call    fillRect
    li      t0, 80
    mul     t0, s2, t0
    add     t0, t0, s3
    mv      a0, t0
    li      a1, D_EDGE
    li      a2, G_TL
    call    putcell
    li      t0, 80
    mul     t0, s2, t0
    add     t0, t0, s3
    add     t0, t0, s5
    addi    a0, t0, -1
    li      a1, D_EDGE
    li      a2, G_TR
    call    putcell

    /* the title bar is the row under the rule: white on blue, no rule in it */
    addi    a0, s2, 1
    mv      a1, s3
    mv      a2, s5
    li      a3, 1
    li      a4, D_TITLE
    li      a5, 32
    call    fillRect
    li      t0, 80
    addi    t1, s2, 1
    mul     t0, t1, t0
    add     t0, t0, s3
    addi    a0, t0, 2
    li      a1, D_TITLE
    mv      a2, s6
    call    puttxt

    /* the bottom rule */
    add     t0, s2, s7
    addi    a0, t0, -1
    mv      a1, s3
    mv      a2, s5
    li      a3, 1
    li      a4, D_BODY
    li      a5, G_HBAR
    call    fillRect
    li      t0, 80
    add     t2, s2, s7
    addi    t2, t2, -1
    mul     t2, t2, t0
    add     t2, t2, s3
    mv      a0, t2
    li      a1, D_BODY
    li      a2, G_BL
    call    putcell
    add     t2, t2, s5
    addi    a0, t2, -1
    li      a1, D_BODY
    li      a2, G_BR
    call    putcell

    /* the sides */
    addi    s6, s2, 1
    add     s7, s2, s7
    addi    s7, s7, -1
1:  bge     s6, s7, 2f
    li      t0, 80
    mul     t0, s6, t0
    add     t0, t0, s3
    mv      a0, t0
    li      a1, D_BODY
    li      a2, G_VBAR
    call    putcell
    li      t0, 80
    mul     t0, s6, t0
    add     t0, t0, s3
    add     t0, t0, s5
    addi    a0, t0, -1
    li      a1, D_BODY
    li      a2, G_VBAR
    call    putcell
    addi    s6, s6, 1
    j       1b

2:  ld      ra, 40(sp)
    ld      s2, 32(sp)
    ld      s3, 24(sp)
    ld      s5, 16(sp)
    ld      s6, 8(sp)
    ld      s7, 0(sp)
    addi    sp, sp, 48
    ret

/* a0 = item -> a0 = its label */
labelOf:
    la      t0, items_lbl
    slli    t1, a0, 3
    add     t0, t0, t1
    ld      a0, 0(t0)
    ret

/* a0 = item -> a0 = which of its options is in force */
currIndex:
    addi    sp, sp, -16
    sd      ra, 8(sp)
    sd      s2, 0(sp)
    mv      s2, a0
    call    itemKind
    li      t0, 1
    beq     a0, t0, ci_boot
    li      t0, 2
    beq     a0, t0, ci_ink
    lbu     a0, 2(s6)              /* the graphics flag is the index */
    j       ci_out
ci_boot:
    lbu     a0, 0(s6)
    j       ci_out
ci_ink:
    lbu     t0, 1(s6)
    li      a0, 1
    li      t1, 15                 /* white */
    beq     t0, t1, ci_zero
    li      t1, 11                 /* cyan */
    beq     t0, t1, ci_two
    j       ci_out                 /* everything else is grey */
ci_zero:
    li      a0, 0
    j       ci_out
ci_two:
    li      a0, 2
ci_out:
    ld      ra, 8(sp)
    ld      s2, 0(sp)
    addi    sp, sp, 16
    ret

/* a0 = item, a1 = the option to put in force */
applyIndex:
    addi    sp, sp, -32
    sd      ra, 24(sp)
    sd      s2, 16(sp)
    sd      s3, 8(sp)
    sd      s7, 0(sp)
    mv      s2, a0
    mv      s7, a1
    call    itemKind
    li      t0, 2
    beq     a0, t0, ai_ink
    sb      s7, 0(s6)              /* boot device */
    li      t0, 3
    bne     a0, t0, ai_out
    sb      s7, 2(s6)              /* graphics demo */
    j       ai_out
ai_ink:
    la      t0, ink_by_index
    add     t0, t0, s7
    lbu     t0, 0(t0)
    sb      t0, 1(s6)
ai_out:
    ld      ra, 24(sp)
    ld      s2, 16(sp)
    ld      s3, 8(sp)
    ld      s7, 0(sp)
    addi    sp, sp, 32
    ret

/* one line of an option list: a0 = cell, a1 = attr, a2 = text, a3 = selected */
dialogRow:
    addi    sp, sp, -48
    sd      ra, 40(sp)
    sd      s2, 32(sp)
    sd      s3, 24(sp)
    sd      s6, 16(sp)
    sd      s7, 8(sp)
    sd      s8, 0(sp)
    mv      s2, a0
    mv      s3, a1
    mv      s6, a2
    mv      s8, a3
    li      a2, 32
    li      a3, 36
    call    fillCells
    beqz    s8, 1f
    mv      a0, s2
    mv      a1, s3
    li      a2, G_SMALL
    call    putcell
1:  addi    a0, s2, 2
    mv      a1, s3
    mv      a2, s6
    call    puttxt
    ld      ra, 40(sp)
    ld      s2, 32(sp)
    ld      s3, 24(sp)
    ld      s6, 16(sp)
    ld      s7, 8(sp)
    ld      s8, 0(sp)
    addi    sp, sp, 48
    ret

/* a0 = item -> a0 = 1 when the value changed.
 *
 * Enter on a setting opens this, and nothing about the setting changes until
 * a value is accepted -- so a stray Enter is harmless.
 */
optionDialog:
    addi    sp, sp, -64
    sd      ra, 56(sp)
    sd      s2, 48(sp)
    sd      s3, 40(sp)
    sd      s5, 32(sp)
    sd      s6, 24(sp)
    sd      s7, 16(sp)
    sd      s8, 8(sp)
    mv      s2, a0                 /* item */
    call    itemKind
    addi    s3, a0, -1             /* which list: 0 boot, 1 ink, 2 gfx */
    la      t0, opt_counts
    slli    t1, s3, 2
    add     t0, t0, t1
    lw      s5, 0(t0)              /* how many */
    mv      a0, s2
    call    currIndex
    mv      s7, a0                 /* the highlighted one */

    mv      a0, s2
    call    labelOf
    mv      a4, a0
    li      a0, 11
    li      a1, 19
    li      a2, 42
    addi    a3, s5, 6
    call    showDialog

od_paint:
    li      s8, 0
1:  bge     s8, s5, 2f
    la      t0, opt_lists          /* s6 is the config page: leave it alone */
    slli    t1, s3, 3
    add     t0, t0, t1
    ld      t0, 0(t0)
    slli    t1, s8, 3
    add     t0, t0, t1
    ld      a2, 0(t0)
    li      t0, 80
    li      t1, 14
    add     t1, t1, s8
    mul     t1, t1, t0
    addi    a0, t1, 21
    li      a1, D_BODY
    li      a3, 0
    bne     s8, s7, 3f
    li      a1, D_SEL
    li      a3, 1
3:  call    dialogRow
    addi    s8, s8, 1
    j       1b

2:  li      t0, 80
    li      t1, 15
    add     t1, t1, s5
    mul     t1, t1, t0
    addi    a0, t1, 21
    li      a1, D_BODY
    la      a2, msg_dlg_keys
    call    puttxt

od_wait:
    call    getMake
    mv      t2, a0
    li      t1, 118                /* escape: keep the old value */
    beq     t2, t1, od_cancel
    li      t1, 90                 /* enter: take the highlighted one */
    beq     t2, t1, od_accept
    beqz    a1, od_wait
    li      t1, 117                /* up */
    beq     t2, t1, od_up
    li      t1, 107                /* left */
    beq     t2, t1, od_up
    li      t1, 114                /* down */
    beq     t2, t1, od_down
    li      t1, 116                /* right */
    beq     t2, t1, od_down
    j       od_wait

od_up:
    addi    s7, s7, -1
    bge     s7, zero, od_paint
    addi    s7, s5, -1
    j       od_paint

od_down:
    addi    s7, s7, 1
    blt     s7, s5, od_paint
    li      s7, 0
    j       od_paint

od_accept:
    mv      a0, s2
    mv      a1, s7
    call    applyIndex
    li      a0, 1
    j       od_out

od_cancel:
    li      a0, 0

od_out:
    ld      ra, 56(sp)
    ld      s2, 48(sp)
    ld      s3, 40(sp)
    ld      s5, 32(sp)
    ld      s6, 24(sp)
    ld      s7, 16(sp)
    ld      s8, 8(sp)
    addi    sp, sp, 64
    ret

/*
 * The number field, the way a 2017 setup utility draws one: a small window
 * over the middle of the screen, a label, a lit input box with a block cursor,
 * and a line under it that says what is wrong with what was typed.
 *
 *   a0 = the value in force      a1 = the item (its label becomes the title)
 *   -> a0 = the accepted value, or -1 when the user cancelled
 *
 * Nothing is committed until Enter: Escape leaves with -1 and the caller keeps
 * the old number, so a stray keypress can never change the machine's memory.
 */
numDialog:
    addi    sp, sp, -64
    sd      ra, 56(sp)
    sd      s2, 48(sp)
    sd      s3, 40(sp)
    sd      s5, 32(sp)
    sd      s7, 24(sp)
    sd      s8, 16(sp)
    sd      s9, 8(sp)
    mv      s3, a0                 /* the value in force */
    mv      a0, a1
    call    labelOf
    mv      a4, a0
    li      a0, 10                 /* top */
    li      a1, 20                 /* left */
    li      a2, 40                 /* width */
    li      a3, 8                  /* height */
    call    showDialog

    li      a0, 902                /* row 11, column 22 */
    li      a1, D_BODY
    la      a2, msg_num_label
    call    puttxt

    /* the field starts holding the value in force: Enter is a no-op, and
     * typing replaces nothing until a key is pressed */
    mv      a0, s3
    la      a1, numbuf2
    call    fmtDec
    la      t0, numbuf2
    li      s5, 0                  /* how many digits are in it */
nd_len:
    lbu     t1, 0(t0)
    beqz    t1, nd_paint
    addi     t0, t0, 1
    addi    s5, s5, 1
    j       nd_len

nd_paint:
    /* the box: sixteen cells of light grey, the digits black on it */
    li      t0, 80
    li      t1, 13
    mul     t0, t1, t0
    addi    t0, t0, 24
    mv      a0, t0
    li      a1, D_SEL
    li      a2, 32
    li      a3, 16
    call    fillCells
    li      t0, 80
    li      t1, 13
    mul     t0, t1, t0
    addi    a0, t0, 26
    li      a1, D_SEL
    la      a2, numbuf2
    call    puttxt
    /* the block cursor sits after the last digit */
    li      t0, 80
    li      t1, 13
    mul     t0, t1, t0
    addi     t0, t0, 26
    add     a0, t0, s5
    li      a1, D_SEL
    li      a2, G_SOLID
    call    putcell

    li      a0, 1222               /* row 15, column 22: the error line */
    li      a1, D_BODY
    la      a2, msg_num_blank
    call    puttxt
    li      a0, 1302               /* row 16: how the window is used */
    li      a1, D_BODY
    la      a2, msg_num_keys
    call    puttxt

nd_wait:
    call    getMake
    mv      t2, a0
    li      t1, 118                /* escape: keep the old value */
    beq     t2, t1, nd_cancel
    li      t1, 90                 /* enter: take what is in the field */
    beq     t2, t1, nd_accept
    beqz    a1, nd_check
    j       nd_wait
nd_check:
    li      t1, 102                /* backspace */
    beq     t2, t1, nd_back
    /* anything that is not a digit is not a keystroke this window knows */
    la      t1, keymap
    add     t1, t1, t2
    lbu     t3, 0(t1)
    li      t4, 48                 /* '0' */
    blt     t3, t4, nd_wait
    li      t4, 57                 /* '9' */
    bgt     t3, t4, nd_wait
    li      t4, 4                  /* four digits is every size there is */
    bge     s5, t4, nd_wait
    la      t1, numbuf2
    add     t1, t1, s5
    sb      t3, 0(t1)
    addi    s5, s5, 1
    addi    t1, t1, 1
    sb      zero, 0(t1)
    j       nd_paint

nd_back:
    blez    s5, nd_paint
    addi    s5, s5, -1
    la      t1, numbuf2
    add     t1, t1, s5
    sb      zero, 0(t1)
    j       nd_paint

nd_accept:
    blez    s5, nd_bad
    la      t0, numbuf2
    li      s7, 0                  /* the number being built */
    li      s8, 0                  /* how many digits walked */
nd_atoi:
    bge     s8, s5, nd_range
    lbu     t1, 0(t0)
    addi    t1, t1, -48
    li      t2, 10
    mul     s7, s7, t2
    add     s7, s7, t1
    addi    t0, t0, 1
    addi    s8, s8, 1
    j       nd_atoi
nd_range:
    li      t0, MEM_MAX
    bgt     s7, t0, nd_bad
    blez    s7, nd_bad
    mv      a0, s7
    j       nd_out
nd_bad:
    li      a0, 1222               /* say why, and stay in the window */
    li      a1, D_BODY
    la      a2, msg_num_range
    call    puttxt
    j       nd_wait

nd_cancel:
    li      a0, -1
nd_out:
    ld      ra, 56(sp)
    ld      s2, 48(sp)
    ld      s3, 40(sp)
    ld      s5, 32(sp)
    ld      s7, 24(sp)
    ld      s8, 16(sp)
    ld      s9, 8(sp)
    addi    sp, sp, 64
    ret

/* the question Escape raises: a0 = 0 keep editing, 1 save, 2 discard */
exitDialog:
    addi    sp, sp, -32
    sd      ra, 24(sp)
    sd      s2, 16(sp)
    sd      s3, 8(sp)
    sd      s7, 0(sp)
    li      a0, 11
    li      a1, 19
    li      a2, 42
    li      a3, 8
    la      a4, msg_ask_title
    call    showDialog
    li      a0, 1061               /* row 13, column 21 */
    li      a1, D_BODY
    la      a2, msg_ask_body
    call    puttxt
    li      s7, 2                  /* the safe choice is already lit */

ed_paint:
    li      s2, 0                  /* which line */
    la      s3, ask_lines
1:  li      t0, 3
    bge     s2, t0, 2f
    slli    t0, s2, 3
    add     t0, s3, t0
    ld      a2, 0(t0)
    li      t0, 80
    li      t1, 15
    add     t1, t1, s2
    mul     t1, t1, t0
    addi    a0, t1, 21
    li      a1, D_BODY
    li      a3, 0
    bne     s2, s7, 3f
    li      a1, D_SEL
    li      a3, 1
3:  call    dialogRow
    addi    s2, s2, 1
    j       1b

2:  call    getMake
    mv      t2, a0
    li      t1, 115                /* 's' */
    beq     t2, t1, ed_save
    li      t1, 100                /* 'd' */
    beq     t2, t1, ed_discard
    li      t1, 118                /* escape: keep editing */
    beq     t2, t1, ed_keep
    li      t1, 90                 /* enter: take the lit line */
    beq     t2, t1, ed_take
    beqz    a1, 2b
    li      t1, 117                /* up */
    beq     t2, t1, ed_up
    li      t1, 107                /* left */
    beq     t2, t1, ed_up
    li      t1, 114                /* down */
    beq     t2, t1, ed_down
    li      t1, 116                /* right */
    beq     t2, t1, ed_down
    j       2b

ed_up:
    addi    s7, s7, -1
    bge     s7, zero, ed_paint
    li      s7, 2
    j       ed_paint

ed_down:
    addi    s7, s7, 1
    li      t0, 3
    blt     s7, t0, ed_paint
    li      s7, 0
    j       ed_paint

ed_take:
    li      t0, 0                  /* 0 save, 1 discard, 2 keep */
    beq     s7, t0, ed_save
    li      t0, 1
    beq     s7, t0, ed_discard
    j       ed_keep

ed_save:
    li      a0, 1
    j       ed_out

ed_discard:
    li      a0, 2
    j       ed_out

ed_keep:
    li      a0, 0

ed_out:
    ld      ra, 24(sp)
    ld      s2, 16(sp)
    ld      s3, 8(sp)
    ld      s7, 0(sp)
    addi    sp, sp, 32
    ret

/* a0 = a number -> numbuf, eight digits right-aligned behind spaces */
fmtNum:
    la      t0, numbuf
    addi    t0, t0, 8
    sb      zero, 0(t0)
    li      t1, 8
    mv      t5, a0
1:  addi    t1, t1, -1
    addi    t0, t0, -1
    li      t3, 10
    remu    t4, t5, t3
    divu    t5, t5, t3
    addi    t4, t4, 48
    sb      t4, 0(t0)
    beqz    t5, 4f
    bgtz    t1, 1b
    j       3f
4:  beqz    t1, 3f
    addi    t1, t1, -1
    addi    t0, t0, -1
    li      t4, 32
    sb      t4, 0(t0)
    j       4b
3:  ret

/*
 * The count a PC shows while it walks its RAM: one 256 KiB step a frame, the
 * number redrawn in place. Any key stops it early and is handed back, so Delete
 * still opens setup. -> a0 = that key, or 0.
 */
memTest:
    addi    sp, sp, -32
    sd      ra, 24(sp)
    sd      s8, 16(sp)
    sd      s11, 8(sp)
    li      s11, 0
    li      a1, A_GREY
    la      a2, msg_memtest
    li      t0, 80
    mul     t0, s2, t0
    mv      a0, t0
    call    puttxt
    /*
     * How far one step walks. A PC counts 8 MiB by 256 KiB, and this machine
     * counts a gigabyte the same way in about the same time: the step is a
     * sixty-fourth of the total, rounded up to a whole 256 KiB, so the count is
     * always thirty-two to sixty-four frames long however much RAM there is.
     */
    lw      t0, memsize
    li      t1, MEM_STEPS
    divu    t0, t0, t1
    addi    t0, t0, 255
    li      t1, 256
    divu    t0, t0, t1
    mul     t0, t0, t1
    bnez    t0, mt_ready
    li      t0, 256
mt_ready:
    la      t1, memstep
    sw      t0, 0(t1)
    li      s8, 0
mt_step:
    lw      t0, memstep
    add     s8, s8, t0
    mv      a0, s8
    call    fmtNum
    li      t0, 80
    mul     t0, s2, t0
    addi    a0, t0, 12
    li      a1, A_GREY
    la      a2, numbuf
    call    puttxt
    lw      t1, memsize
    bge     s8, t1, mt_done
    lbu     t0, 4(s1)
    andi    t0, t0, 1
    bnez    t0, mt_key
    wfi
    j       mt_step
mt_key:
    lbu     s11, 0(s1)
    lw      s8, memsize
    mv      a0, s8
    call    fmtNum
    li      t0, 80
    mul     t0, s2, t0
    addi    a0, t0, 12
    li      a1, A_GREY
    la      a2, numbuf
    call    puttxt
mt_done:
    li      t0, 80
    mul     t0, s2, t0
    addi    a0, t0, 20
    li      a1, A_GREEN
    la      a2, msg_ok
    call    puttxt
    addi    s2, s2, 1
    mv      a0, s11
    ld      ra, 24(sp)
    ld      s8, 16(sp)
    ld      s11, 8(sp)
    addi    sp, sp, 32
    ret

/*
 * The console cursor is a block, not a bar: it is a lit cell, drawn by the
 * guest like everything else. `s5` is where the next character goes, `s11` says
 * whether the block is lit, and `s8` counts the frames to the next blink.
 */
cursorDraw:
    slli    t1, s5, 1
    add     t1, s4, t1
    li      t3, 32
    li      t4, 0
    beqz    s11, 1f
    li      t3, G_SOLID
    li      t4, A_CURSOR
1:  sb      t3, 0(t1)
    sb      t4, 1(t1)
    ret

/* a key landed: light the block again and restart the blink */
cursorWake:
    li      s11, 1
    li      s8, 30
    j       cursorDraw


/*
 * ---- the shell ----
 *
 * The prompt is not a toy echo any more: the line is kept in `linebuf` and
 * parsed on Enter. The dialect is deliberately small and debug.exe-shaped --
 * `md`, `mw`, `r`, `t`, `cls`, `boot` -- because the point is to look at memory
 * and the machine's own CSRs from inside the machine.
 */

/* s0 = the cursor into linebuf; skip the spaces there */
skipSp:
1:  lbu     t0, 0(s0)
    li      t1, 32
    bne     t0, t1, 2f
    addi    s0, s0, 1
    j       1b
2:  ret

/* t0 = a character -> a0 = its value, or -1 */
hexDig:
    li      a0, -1
    li      t1, 48                  /* '0' */
    blt     t0, t1, 9f
    li      t1, 57                  /* '9' */
    ble     t0, t1, 1f
    li      t1, 97                  /* 'a' */
    blt     t0, t1, 3f
    li      t1, 102                 /* 'f' */
    ble     t0, t1, 2f
3:  li      t1, 65                  /* 'A' */
    blt     t0, t1, 9f
    li      t1, 70                  /* 'F' */
    bgt     t0, t1, 9f
    addi    a0, t0, -55
    ret
1:  addi    a0, t0, -48
    ret
2:  addi    a0, t0, -87
9:  ret

/* s0 = hex digits -> a0 = the value, s0 past them, a1 = 1 when there were any */
hexNum:
    addi    sp, sp, -24
    sd      ra, 16(sp)
    sd      s2, 8(sp)
    sd      s3, 0(sp)
    li      s2, 0
    li      a1, 0
1:  lbu     t0, 0(s0)
    call    hexDig
    bltz    a0, 2f
    slli    s2, s2, 4
    or      s2, s2, a0
    addi    s0, s0, 1
    li      a1, 1
    j       1b
2:  mv      a0, s2
    ld      ra, 16(sp)
    ld      s2, 8(sp)
    ld      s3, 0(sp)
    addi    sp, sp, 24
    ret

/* a0 = value, a1 = how many digits: hex, no prefix */
puthex:
    addi    sp, sp, -32
    sd      ra, 24(sp)
    sd      s2, 16(sp)
    sd      s3, 8(sp)
    sd      s7, 0(sp)
    mv      s2, a0
    mv      s3, a1
1:  addi    s3, s3, -1
    slli    t0, s3, 2               /* a digit is four bits wide */
    srl     a0, s2, t0
    andi    a0, a0, 15
    li      t0, 10
    blt     a0, t0, 2f
    addi    a0, a0, 55              /* 'A' - 10 */
    j       3f
2:  addi    a0, a0, 48
3:  call    shPutc
    bgtz    s3, 1b
    ld      ra, 24(sp)
    ld      s2, 16(sp)
    ld      s3, 8(sp)
    ld      s7, 0(sp)
    addi    sp, sp, 32
    ret

/* the next row, with no prompt in front of it: shell output is not a line */
shNl:
    addi    sp, sp, -16
    sd      ra, 8(sp)
    li      t0, 80
    remu    t1, s5, t0
    beqz    t1, 1f
    sub     s5, s5, t1
    addi    s5, s5, 80
1:  li      t0, 2320
    blt     s5, t0, 2f
    call    scrollcon
    li      s5, 2240
2:  ld      ra, 8(sp)
    addi    sp, sp, 16
    ret

/* a0 = a character at the console cursor, scrolled if it runs off the glass */
shPutc:
    addi    sp, sp, -16
    sd      ra, 8(sp)
    li      t1, 80
    remu    t1, s5, t1
    bnez    t1, 1f
    li      t1, 2320
    blt     s5, t1, 1f
    call    scrollcon
    li      s5, 2240
1:  call    putch
    ld      ra, 8(sp)
    addi    sp, sp, 16
    ret

/* a0 = a string */
shPuts:
    addi    sp, sp, -32
    sd      ra, 24(sp)
    sd      s2, 16(sp)
    sd      s3, 8(sp)
    sd      s7, 0(sp)
    mv      s2, a0
1:  lbu     a0, 0(s2)
    beqz    a0, 2f
    addi    s2, s2, 1
    call    shPutc
    j       1b
2:  ld      ra, 24(sp)
    ld      s2, 16(sp)
    ld      s3, 8(sp)
    ld      s7, 0(sp)
    addi    sp, sp, 32
    ret

/* one space */
shSp:
    li      a0, 32
    j       shPutc

/* finish the line, run it, and leave a fresh prompt under the output */
shellEnter:
    addi    sp, sp, -16
    sd      ra, 8(sp)
    la      t0, linebuf
    la      t1, lptr
    lw      t2, 0(t1)
    add     t0, t0, t2
    sb      zero, 0(t0)             /* NUL-terminate */
    sw      zero, 0(t1)             /* and start again */
    call    shellRun
    call    newline
    ld      ra, 8(sp)
    addi    sp, sp, 16
    ret

shellRun:
    addi    sp, sp, -32
    sd      ra, 24(sp)
    sd      s0, 16(sp)
    sd      s2, 8(sp)
    sd      s3, 0(sp)
    la      s0, linebuf
    call    skipSp
    lbu     t0, 0(s0)
    beqz    t0, sr_out              /* an empty line is a new prompt */
    li      t1, 63                  /* ? */
    beq     t0, t1, sh_help
    li      t1, 114                 /* r */
    beq     t0, t1, sh_regs
    li      t1, 109                 /* m */
    beq     t0, t1, sh_mem
    li      t1, 116                 /* t */
    beq     t0, t1, sh_time
    li      t1, 99                  /* c */
    beq     t0, t1, sh_cls
    li      t1, 118                 /* v */
    beq     t0, t1, sh_video
    li      t1, 110                 /* n: one ARP request, and what came back */
    beq     t0, t1, sh_net
    li      t1, 98                  /* b */
    beq     t0, t1, sh_boot
    call    shNl
    la      a0, msg_badcmd
    call    shPuts
sr_out:
    ld      ra, 24(sp)
    ld      s0, 16(sp)
    ld      s2, 8(sp)
    ld      s3, 0(sp)
    addi    sp, sp, 32
    ret

sh_help:
    call    shNl
    la      a0, msg_help1
    call    shPuts
    call    shNl
    la      a0, msg_help2
    call    shPuts
    j       sr_out

/* r: where the machine is, and the CSRs a trap would report */
sh_regs:
    call    shNl
    la      a0, msg_r_pc
    call    shPuts
    auipc   a0, 0
    li      a1, 8
    call    puthex
    la      a0, msg_r_sp
    call    shPuts
    mv      a0, sp
    li      a1, 8
    call    puthex
    call    shNl
    la      a0, msg_r_mtvec
    call    shPuts
    csrr    a0, 0x305
    li      a1, 8
    call    puthex
    la      a0, msg_r_mepc
    call    shPuts
    csrr    a0, 0x341
    li      a1, 8
    call    puthex
    call    shNl
    call    shNl
    la      a0, msg_r_cause
    call    shPuts
    csrr    a0, 0x342
    li      a1, 8
    call    puthex
    la      a0, msg_r_mie
    call    shPuts
    csrr    a0, 0x304
    li      a1, 8
    call    puthex
    j       sr_out

/* t: the clock */
sh_time:
    call    shNl
    la      a0, msg_t_time
    call    shPuts
    csrr    a0, 0xc01
    li      a1, 8
    call    puthex
    la      a0, msg_t_cmp
    call    shPuts
    csrr    a0, 0xc00
    li      a1, 8
    call    puthex
    j       sr_out

/* md/mw: memory. `md addr [n]` dumps n bytes, eight a row (16 by default) */
sh_mem:
    addi    s0, s0, 1
    lbu     t0, 0(s0)
    li      t1, 119                 /* w */
    beq     t0, t1, sh_mw
    addi    s0, s0, 1
    call    skipSp                 /* past the space after `md` */
    call    hexNum
    mv      s2, a0
    call    skipSp
    li      s3, 16
    call    hexNum
    beqz    a1, 1f
    mv      s3, a0
1:  call    shNl
    li      s7, 0
md_row:
    bge     s7, s3, md_done
    add     a0, s2, s7
    li      a1, 8
    call    puthex
    call    shSp
    li      s0, 0
md_b:
    li      t0, 8
    bge     s0, t0, md_ascii
    add     t0, s7, s0
    bge     t0, s3, md_ascii
    add     t0, s2, t0
    lbu     a0, 0(t0)
    li      a1, 2
    call    puthex
    call    shSp
    addi    s0, s0, 1
    j       md_b
md_ascii:
    li      t0, 8
    bge     s0, t0, md_txt
    li      a0, 32
    call    shPutc
    call    shPutc
    call    shPutc
    addi    s0, s0, 1
    j       md_ascii
md_txt:
    li      s0, 0
md_c:
    li      t0, 8
    bge     s0, t0, md_next
    add     t0, s7, s0
    bge     t0, s3, md_next
    add     t0, s2, t0
    lbu     a0, 0(t0)
    li      t0, 32
    blt     a0, t0, md_dot
    li      t0, 126
    bgt     a0, t0, md_dot
    j       md_put
md_dot:
    li      a0, 46
md_put:
    call    shPutc
    addi    s0, s0, 1
    j       md_c
md_next:
    addi    s7, s7, 8
    bge     s7, s3, md_done
    call    shNl
    j       md_row
md_done:
    j       sr_out

/* mw addr b0 [b1 ...] -- the bytes are hex, separated by spaces */
sh_mw:
    addi    s0, s0, 2
    call    skipSp
    call    hexNum
    /* No address parses as zero, and a store to zero faults the machine. A
     * debugger that dies on a typo is not a debugger. */
    beqz    a1, mw_bad
    mv      s2, a0
    li      s3, 0
1:  call    skipSp
    call    hexNum
    beqz    a1, 3f
    add     t0, s2, s3
    sb      a0, 0(t0)
    addi    s3, s3, 1
    j       1b
3:  call    shNl
    mv      a0, s3
    li      a1, 2
    call    puthex
    la      a0, msg_bytes
    call    shPuts
    j       sr_out
mw_bad:
    call    shNl
    la      a0, msg_badaddr
    call    shPuts
    j       sr_out

/* v [mode]: 0 text, 1 320x200, 2 640x480. A graphics set paints a test card
 * and holds it until a key, because the shell itself lives in the text plane. */
/* n: put one ARP request on the wire and report the reply. It is the shortest
 * path that proves the whole card: frame out, host bridge, frame back in. */
sh_net:
    li      s2, 0x10003000          /* the card's window */
    la      t0, arp_req
    la      t5, arp_req_end         /* a length, not a terminator: a MAC has 00 */
    li      t2, 0
1:  bgeu    t0, t5, 2f
    lbu     t3, 0(t0)
    add     t4, s2, t2
    addi    t4, t4, 0x40            /* the transmit buffer */
    sb      t3, 0(t4)
    addi    t0, t0, 1
    addi    t2, t2, 1
    j       1b
2:  addi    t4, s2, 4
    sb      t2, 0(t4)               /* TXLEN */
    li      t3, 1
    sb      t3, 0(s2)               /* TXCTL: send it */
    li      t2, 0
3:  addi    t4, s2, 16
    lbu     t3, 0(t4)               /* RXCTL: is a frame waiting? */
    andi    t3, t3, 1
    bnez    t3, 4f
    addi    t2, t2, 1
    li      t3, 400000
    blt     t2, t3, 3b
    call    shNl
    la      a0, msg_net_none
    call    shPuts
    j       9f
4:  li      t3, 1
    sb      t3, 0(t4)               /* take it */
    call    shNl
    la      a0, msg_net_reply
    call    shPuts
    li      s3, 0
5:  li      t4, 0x840              /* the receive buffer */
    add     t4, s2, t4
    add     t4, t4, s3
    lbu     a0, 0(t4)
    li      a1, 2
    call    puthex
    addi    s3, s3, 1
    li      t0, 6
    bge     s3, t0, 6f
    li      a0, 58                  /* ':' */
    call    shPutc
    j       5b
6:  li      a0, 32
    call    shPutc
    li      s3, 0
7:  li      t4, 0x840
    add     t4, s2, t4
    add     t4, t4, s3
    addi    t4, t4, 28              /* the sender's address */
    lbu     a0, 0(t4)
    li      a1, 2
    call    puthex
    addi    s3, s3, 1
    li      t0, 4
    blt     s3, t0, 7b
9:  j       sr_out

sh_video:
    addi    s0, s0, 1
    call    skipSp
    call    hexNum
    beqz    a1, sv_show
    mv      s2, a0
    li      t0, 0x50300
    add     t0, s4, t0
    sb      s2, 0(t0)
    beqz    s2, sr_out              /* text: the plane is still there */
    call    sv_pattern
    /* hold the card for two seconds, or until a key. A picture you cannot
     * leave is not a test card, it is a crash -- so it leaves by itself, and a
     * stale byte in the buffer must not dismiss it before it is drawn. */
    call    ps2Drain
    csrr    t2, 0xc01
    li      t3, 20000000            /* two seconds of mtime */
    add     t3, t2, t3
1:  lbu     t0, 4(s1)
    andi    t0, t0, 1
    bnez    t0, 2f
    csrr    t2, 0xc01
    bgeu    t2, t3, 2f
    wfi
    j       1b
2:  call    ps2Drain
    li      t0, 0x50300
    add     t0, s4, t0
    sb      zero, 0(t0)             /* and back to text */
    j       sr_out

sv_show:
    call    shNl
    li      t0, 0x50300
    add     t0, s4, t0
    lbu     a0, 0(t0)
    li      a1, 2
    call    puthex
    la      a0, msg_video
    call    shPuts
    j       sr_out

/* eight colour bars and a white diagonal: bands prove the row is addressed and
 * the diagonal proves a single pixel lands where it was put */
sv_pattern:
    addi    sp, sp, -16
    sd      ra, 8(sp)
    li      t3, 320
    li      t5, 200
    li      t0, 1
    beq     s2, t0, 1f
    li      t3, 640
    li      t5, 480
1:  li      t0, 0
2:  li      t1, 0
3:  li      t2, 8
    mul     t2, t1, t2
    divu    t2, t2, t3
    mul     t4, t1, t5
    divu    t4, t4, t3
    bne     t4, t0, 4f
    li      t2, 15
4:  mul     t4, t0, t3
    add     t4, t4, t1
    li      t6, 0x2000              /* the graphics plane's offset */
    add     t4, t4, t6
    add     t4, s4, t4
    sb      t2, 0(t4)
    addi    t1, t1, 1
    blt     t1, t3, 3b
    addi    t0, t0, 1
    blt     t0, t5, 2b
    ld      ra, 8(sp)
    addi    sp, sp, 16
    ret

sh_cls:
    li      a0, 17
    li      a1, 28
    li      a2, 0
    call    fillRows
    li      s5, 1360
    call    newline
    j       sr_out

sh_boot:
    li      t0, 0x80000000
    jr      t0

/* a0 = cell, a1 = attr, a2 = character */
putcell:
    slli    t0, a0, 1
    add     t0, s4, t0
    sb      a2, 0(t0)
    sb      a1, 1(t0)
    ret


getMake:
    addi    sp, sp, -16
    sd      ra, 8(sp)              /* gm_next is a call: keep our own return */
    li      a1, 0
gm_wait:
    lbu     t0, 4(s1)
    andi    t0, t0, 1
    bnez    t0, gm_byte
    wfi
    j       gm_wait
gm_byte:
    lbu     t0, 0(s1)
    li      t1, 224
    bne     t0, t1, gm_notext
    li      a1, 1
    j       gm_wait
gm_notext:
    li      t1, 240
    bne     t0, t1, gm_done
    /* a release: drop the code that follows and forget any prefix */
    call    gm_next
    li      a1, 0
    j       gm_wait
gm_done:
    mv      a0, t0
    ld      ra, 8(sp)
    addi    sp, sp, 16
    ret

/* t0 = the next byte, waiting for it if need be */
gm_next:
    lbu     t0, 4(s1)
    andi    t0, t0, 1
    bnez    t0, 1f
    wfi
    j       gm_next
1:  lbu     t0, 0(s1)
    ret

/* three 30-cell rows, so drawing a field never has to clear one first */
f_boot_none: .asciz " Boot device           None   "
f_boot_disk: .asciz " Boot device           Disk   "
f_attr:      .asciz " Text attribute               "
f_gfx_off:   .asciz " Graphics demo         Off    "
f_gfx_on:    .asciz " Graphics demo         On     "

.section .rodata
/* setup screen text: fixed widths, so a row never needs clearing */
tab_0:      .asciz " Main "
tab_1:      .asciz " Info "
tab_2:      .asciz " Exit "

tabh_0:     .asciz "Main settings"
tabh_1:     .asciz "System information"
tabh_2:     .asciz "Save or discard"

i0_lbl: .asciz "Boot device           "
i0_val: .quad 0
i0_d1:  .asciz "Which device the firmware tries"
i0_d2:  .asciz "first. Disk reads block 0."
i1_lbl: .asciz "Console ink           "
i1_val: .quad 0
i1_d1:  .asciz "The colour the console writes"
i1_d2:  .asciz "its prompt and its echo in."
i2_lbl: .asciz "Graphics demo         "
i2_val: .quad 0
i2_d1:  .asciz "Draw a 32x16 block in the"
i2_d2:  .asciz "graphics plane at POST."
i3_lbl: .asciz "Memory size           "
i3_d1:  .asciz "How much RAM the board gives"
i3_d2:  .asciz "the guest, in MiB, 1 to 1024."
i4_lbl: .asciz "CPU                   "
i4_val: .asciz "RV64IMAC  "
i4_d1:  .asciz "Machine mode, 32 registers,"
i4_d2:  .asciz "no compressed instructions."
i5_lbl: .asciz "Memory                "
i5_d1:  .asciz "The RAM the guest is running"
i5_d2:  .asciz "in, at 0x80000000."
i6_lbl: .asciz "Firmware ROM          "
i6_val: .asciz "0x1000    "
i6_d1:  .asciz "Read-only window on the image,"
i6_d2:  .asciz "a store is swallowed."
i7_lbl: .asciz "NVRAM                 "
i7_val: .asciz "256 bytes "
i7_d1:  .asciz "Battery-backed settings at"
i7_d2:  .asciz "0x10002000; survives reset."
i8_lbl: .asciz "Clock                 "
i8_val: .asciz "10 MHz    "
i8_d1:  .asciz "The CLINT counter, advanced"
i8_d2:  .asciz "once a frame."
i9_lbl: .asciz "Traps                 "
i9_val: .asciz "MTIP      "
i9_d1:  .asciz "Machine timer interrupts through"
i9_d2:  .asciz "mtvec and mret."
i10_lbl: .asciz "Save and exit         "
i10_val: .asciz "          "
i10_d1:  .asciz "Write the record and boot."
i10_d2:  .asciz ""
i11_lbl: .asciz "Exit without saving   "
i11_val: .asciz "          "
i11_d1:  .asciz "Throw the changes away and"
i11_d2:  .asciz "boot with what was stored."

.align 3
items_lbl:  .quad i0_lbl, i1_lbl, i2_lbl, i3_lbl, i4_lbl, i5_lbl, i6_lbl, i7_lbl, i8_lbl, i9_lbl, i10_lbl, i11_lbl
.align 3
/* a null slot means "ask itemValue": the value is a setting, not a string */
items_val:  .quad 0, 0, 0, 0, i4_val, 0, i6_val, i7_val, i8_val, i9_val, i10_val, i11_val
items_d1:   .quad i0_d1, i1_d1, i2_d1, i3_d1, i4_d1, i5_d1, i6_d1, i7_d1, i8_d1, i9_d1, i10_d1, i11_d1
items_d2:   .quad i0_d2, i1_d2, i2_d2, i3_d2, i4_d2, i5_d2, i6_d2, i7_d2, i8_d2, i9_d2, i10_d2, i11_d2
/* 1 boot, 2 ink, 3 graphics, 6 the memory number, 4 save, 5 discard, 0 info,
 * 7 an information row whose value itemValue builds (the memory size) */
items_kind: .byte 1, 2, 3, 6, 0, 7, 0, 0, 0, 0, 4, 5

tab_base:   .word 0, 4, 10
tab_n:      .word 4, 6, 2
.align 3
tab_ptr:    .quad tab_0, tab_1, tab_2
.align 3
tab_head:   .quad tabh_0, tabh_1, tabh_2
tab_col:    .word 2, 11, 20

/* the values a setting can take, one list per kind, in menu order */
val2_none:  .asciz "None"
val2_disk:  .asciz "Disk"
val2_white: .asciz "White"
val2_grey:  .asciz "Grey"
val2_cyan:  .asciz "Cyan"
val2_off:   .asciz "Off"
val2_on:    .asciz "On"

.align 3
opts_boot:  .quad val2_none, val2_disk
opts_ink:   .quad val2_white, val2_grey, val2_cyan
opts_gfx:   .quad val2_off, val2_on
.align 3
opt_lists:  .quad opts_boot, opts_ink, opts_gfx
opt_counts: .word 2, 3, 2
/* option index -> console ink: white, grey, cyan */
ink_by_index: .byte 15, 7, 11, 0

str_mem_suffix:   .asciz " MiB"
str_meminfo_pre:  .asciz "Memory   "
str_meminfo_post: .asciz " MiB at 0x80000000"
msg_num_label:  .asciz "How much RAM to give, in MiB"
msg_num_blank:  .asciz "                              "
msg_num_range:  .ascii "Out of range: 1 - 1024.        "
    .byte 0
msg_num_keys:   .ascii "digits    Enter accept    Esc cancel"
    .byte 0
msg_dlg_keys: .byte 32,24,47,25,32,99,104,111,111,115,101,32,32,32
    .byte 69,110,116,101,114,32,97,99,99,101,112,116,32,32,32
    .byte 69,115,99,32,99,97,110,99,101,108,0

msg_ask_title:  .asciz " Unsaved changes "
msg_ask_body:   .asciz "The settings have not been written."
ask_save:       .asciz " Save them and exit"
ask_discard:    .asciz " Throw them away"
ask_keep:       .asciz " Keep editing"
.align 3
ask_lines:  .quad ask_save, ask_discard, ask_keep

msg_saved:      .asciz " Saving "
msg_saved_body: .asciz "Written to NVRAM."

msg_footer: .byte 32,24,47,25,32,105,116,101,109,32,32,32,17,47,16,32
    .byte 116,97,98,32,32,32,69,110,116,101,114,32,99,104,97,110
    .byte 103,101,32,32,32,69,115,99,32,101,120,105,116,0
msg_system: .asciz "System"
info_cpu:   .asciz "CPU      RV64IMAC, machine mode"
info_rom:   .asciz "ROM      0x00001000, read-only"
info_nvram: .asciz "NVRAM    0x10002000, 256 bytes"
info_clock: .asciz "Clock    10 MHz from the CLINT"
msg_version: .asciz "v0.2"
msg_hint:   .asciz "Press Enter to change it."
msg_sp10:   .asciz "          "

val_disk:   .asciz "Disk      "
val_none:   .asciz "None      "
val_on:     .asciz "On        "
val_off:    .asciz "Off       "
val_white:  .asciz "White     "
val_grey:   .asciz "Grey      "
val_cyan:   .asciz "Cyan      "

.section .bss
.align 4
cfg:        .space 16               /* boot, attr, graphics, pad, memory MiB */
memsize:    .word 0                 /* KiB the chipset reports */
memstep:    .word 256               /* how far the POST count walks a frame */
memmib:     .word 0                 /* MiB the chipset reports */
.align 3
memtxt:     .space 16               /* the size the record asks for, "1024 MiB" */
memnow:     .space 16               /* the size the board is giving right now */
meminfo:    .space 40               /* "Memory   1024 MiB at 0x80000000" */
numbuf2:    .space 12               /* the size field's digits */
.align 3
numbuf:     .space 12               /* eight digits, a NUL, and slack */
linebuf:    .space 96               /* the shell's line */
lptr:       .word 0                 /* how much of it is used */
stack:      .space 8192
stack_top:
