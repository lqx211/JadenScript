/* Isolates the RV64M divide-by-zero rules, which the big payload buries
 * thousands of instructions deep. */
.option norvc
.section .text.init
.globl _start
_start:
    la      sp, stack_top
    li      t0, -1
    li      t1, 0
    div     a0, t0, t1
    call    phex
    rem     a0, t0, t1
    call    phex
    li      t0, 7
    li      t1, 0
    div     a0, t0, t1
    call    phex
    rem     a0, t0, t1
    call    phex
/* a0 = value */
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
stack:      .space 4096
stack_top:
.section .text
