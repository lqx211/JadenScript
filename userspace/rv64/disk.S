/* Reads through the disk window at 0x4000_0000, which the machine serves over
 * a localhost WebSocket out of a real file. Two blocks, so a cache miss and a
 * cache hit both happen. */
.option norvc
.section .text.init
.globl _start
_start:
    la      sp, stack_top
    li      s4, 0x40000000

    /* block 0, first 8 bytes */
    lbu     a0, 0(s4)
    call    phex
    lbu     a0, 1(s4)
    call    phex
    lbu     a0, 7(s4)
    call    phex

    /* still block 0: a hit (an offset past 2047 needs its own base) */
    li      s5, 4095
    add     s5, s4, s5
    lbu     a0, 0(s5)
    call    phex

    /* block 1: a second miss */
    li      s5, 4096
    add     s5, s4, s5
    lbu     a0, 0(s5)
    call    phex
    lbu     a0, 1(s5)
    call    phex

    /* block 5, far away */
    li      s5, 20480
    add     s5, s4, s5
    lbu     a0, 0(s5)
    call    phex

1:  j       1b

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

putc:
    li      t0, 0x10000000
1:  lbu     t1, 5(t0)
    andi    t1, t1, 0x20
    beqz    t1, 1b
    sb      a0, 0(t0)
    ret

.section .bss
.align 4
stack:      .space 8192
stack_top:
.section .text
