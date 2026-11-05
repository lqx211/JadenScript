/* rv64 core test payload: M-mode, no OpenSBI, QEMU virt memory map.
 * Every result is printed as 16 hex digits so the JSW emulator can be diffed
 * against QEMU byte for byte. */
.option norvc
.section .text.init
.globl _start
_start:
    la      sp, stack_top
    la      a0, msg_start
    call    puts

    /* ---- RV64I: 64-bit register arithmetic ---- */
    li      s4, 0x123456789ABCDEF0
    li      s5, 0x1111111111111111
    add     a0, s4, s5
    call    phex
    sub     a0, s4, s5
    call    phex
    /* LUI and AUIPC sign-extend bit 31 on RV64 */
    lui     a0, 0x80000
    call    phex
    auipc   a0, 1
    call    phex

    /* ---- word ops keep 32 bits and sign-extend ---- */
    li      s4, 0x00000000FFFFFFFF
    addiw   a0, s4, 1
    call    phex
    li      s4, 0x80000000
    li      s5, 0x80000000
    addw    a0, s4, s5
    call    phex
    slli    a0, s4, 1
    call    phex
    slliw   a0, s4, 1
    call    phex

    /* ---- shifts use only the low 6 bits ---- */
    li      s4, 1
    sll     a0, s4, s4
    call    phex
    li      s5, 63
    sll     a0, s4, s5
    call    phex
    li      s5, 64
    sll     a0, s4, s5
    call    phex
    li      s4, 0x8000000000000000
    srai    a0, s4, 4
    call    phex

    /* ---- M extension, 64-bit ---- */
    li      s4, -7
    li      s5, 3
    mul     a0, s4, s5
    call    phex
    div     a0, s4, s5
    call    phex
    rem     a0, s4, s5
    call    phex
    li      s4, 0xFFFFFFFFFFFFFFFF
    li      s5, 2
    divu    a0, s4, s5
    call    phex
    mulhu   a0, s4, s5
    call    phex
    li      s4, -1
    mulh    a0, s4, s5
    call    phex
    /* divide by zero: quotient all ones, remainder the dividend */
    li      s5, 0
    div     a0, s4, s5
    call    phex
    rem     a0, s4, s5
    call    phex
    /* 32-bit M forms sign-extend their result */
    li      s4, 0x7FFFFFFF
    li      s5, 2
    mulw    a0, s4, s5
    call    phex
    divw    a0, s4, s5
    call    phex

    /* ---- A extension on a scratch cell ---- */
    la      s0, cell
    li      s4, 0x1122334455667788
    sd      s4, 0(s0)
    li      s5, 0x100
    amoadd.d a0, s5, (s0)
    call    phex
    ld      a0, 0(s0)
    call    phex
    li      s5, 0xFFFFFFFFFFFFFFFF
    amoxor.d a0, s5, (s0)
    call    phex
    ld      a0, 0(s0)
    call    phex
    /* AMO*.W works on 32 bits and sign-extends the old value */
    li      s4, 0x00000000FFFFFFFF
    sw      s4, 0(s0)
    li      s5, 1
    amoadd.w a0, s5, (s0)
    call    phex
    lw      a0, 0(s0)
    call    phex
    lwu     a0, 0(s0)
    call    phex

    /* ---- loads sign/zero extend correctly ---- */
    li      s4, 0x00000000000000FF
    sb      s4, 8(s0)
    lb      a0, 8(s0)
    call    phex
    lbu     a0, 8(s0)
    call    phex
    li      s4, 0xFFFF
    sh      s4, 10(s0)
    lh      a0, 10(s0)
    call    phex
    lhu     a0, 10(s0)
    call    phex
    li      s4, 0xFFFFFFFF
    sw      s4, 12(s0)
    lw      a0, 12(s0)
    call    phex
    lwu     a0, 12(s0)
    call    phex

    /* ---- branches: signed vs unsigned ---- */
    li      s4, -1
    li      s5, 1
    li      s6, 0
    blt     s4, s5, 1f
    ori     s6, s6, 1
1:  bltu    s4, s5, 2f
    ori     s6, s6, 2
2:  bge     s5, s4, 3f
    ori     s6, s6, 4
3:  bgeu    s5, s4, 4f
    ori     s6, s6, 8
4:  mv      a0, s6
    call    phex

    /* ---- JALR clears bit 0 ---- */
    la      s4, after_jalr
    addi    s4, s4, 1
    jalr    ra, 0(s4)
    li      a0, 0xBAD
    call    phex
after_jalr:

    la      a0, msg_done
    call    puts
1:  j       1b

/* a0 = value, prints 16 hex digits + newline.
 * ra is saved: every helper here calls putc, and a plain `call` would overwrite
 * the return address the caller depended on. */
phex:
    addi    sp, sp, -32
    sd      ra, 24(sp)
    sd      s2, 16(sp)
    sd      s3, 8(sp)
    mv      s2, a0
    li      s3, 60
1:  srl     t0, s2, s3
    andi    t0, t0, 15
    li      t1, 10
    blt     t0, t1, 2f
    addi    t0, t0, 87
    j       3f
2:  addi    t0, t0, 48
3:  mv      a0, t0
    call    putc
    addi    s3, s3, -4
    bgez    s3, 1b
    li      a0, 10
    call    putc
    ld      s3, 8(sp)
    ld      s2, 16(sp)
    ld      ra, 24(sp)
    addi    sp, sp, 32
    ret

/* a0 = NUL-terminated string */
puts:
    addi    sp, sp, -32
    sd      ra, 24(sp)
    sd      s2, 16(sp)
    mv      s2, a0
1:  lbu     a0, 0(s2)
    beqz    a0, 2f
    call    putc
    addi    s2, s2, 1
    j       1b
2:  ld      s2, 16(sp)
    ld      ra, 24(sp)
    addi    sp, sp, 32
    ret

/* a0 = char; leaf, so it may use ra freely */
putc:
    li      t0, 0x10000000
1:  lbu     t1, 5(t0)
    andi    t1, t1, 0x20
    beqz    t1, 1b
    sb      a0, 0(t0)
    ret

.section .rodata
msg_start:  .asciz "== rv64 core ==\n"
msg_done:   .asciz "== done ==\n"

.section .bss
.align 4
cell:       .space 64
stack:      .space 8192
stack_top:

.section .text
