/* smode: the privilege boundary, end to end.
 *
 * M-mode sets up the trap vectors and drops into S-mode with `mret`. S-mode
 * then talks to the firmware the way a kernel talks to an SBI: `ecall` with a
 * call number in a7 and arguments in a0/a1. Nothing here is a stub -- if the
 * return to S-mode, the delegation registers or the CSR views were wrong, the
 * payload would print the wrong bytes or stop.
 *
 *     "S-OK"     S-mode ran, made two SBI calls, took one delegated trap and
 *                came back through `sret`
 *     "S-BAD n"  the n-th check that did not hold
 *
 * The M-mode handler is deliberately tiny: console putchar (a7 = 1) and
 * shutdown (a7 = 8), which is all a bring-up payload needs.
 */
.option norvc
.equ UART, 0x10000000
.equ HALT, 0x50050301              /* control + 1: writing a byte here halts */

.equ SSTATUS_SPP, 1 << 8
.equ MSTATUS_MPP_S, 1 << 11

.section .text.init
.globl _start
_start:
    la      sp, stack_top
    la      t0, mtrap
    csrw    mtvec, t0

    /* give S-mode a trap vector of its own, for the delegated ecall later */
    la      t0, strap
    csrw    stvec, t0

    /* the first ecall is *not* delegated: it has to reach the M-mode handler */
    csrw    medeleg, zero

    /* mret lands in S-mode at smain */
    la      t0, smain
    csrw    mepc, t0
    li      t0, MSTATUS_MPP_S
    csrw    mstatus, t0
    mret

/* ---- S-mode ---------------------------------------------------------- */

smain:
    /* a CSR only S-mode may see, to prove the view exists and is writable */
    csrr    t0, sstatus
    csrw    sscratch, t0
    csrr    t1, sscratch
    bne     t0, t1, bad1

    /* SBI console putchar: a7 = 1, a0 = the byte */
    li      a7, 1
    li      a0, 'S'
    ecall
    li      a7, 1
    li      a0, '-'
    ecall

    /* a7 = 9 asks the firmware to delegate `ecall from S` back to us -- the
     * kernel side of a two-stage trap setup */
    li      a7, 9
    ecall

    /* that ecall took the M-mode path; this one must land on stvec */
    ecall
after_ecall:
    /* `strap` printed 'D'; finish the word straight from S-mode */
    li      t0, UART
    li      t1, 'O'
    sb      t1, 0(t0)
    li      t1, 'K'
    sb      t1, 0(t0)
    li      t1, 10
    sb      t1, 0(t0)

    /* and stop here: this proves an S-mode store reaches a device */
    li      t0, HALT
    li      t1, 1                    /* the halt bit, not the byte value 0 */
    sb      t1, 0(t0)
1:  j       1b

bad1:
    li      t0, UART
    li      t1, 'S'
    sb      t1, 0(t0)
    li      t1, '-'
    sb      t1, 0(t0)
    li      t1, 'B'
    sb      t1, 0(t0)
    li      t1, 'A'
    sb      t1, 0(t0)
    li      t1, 'D'
    sb      t1, 0(t0)
    li      t1, '1'
    sb      t1, 0(t0)
    li      t0, HALT
    li      t1, 1                    /* the halt bit, not the byte value 0 */
    sb      t1, 0(t0)
2:  j       2b

/* ---- the S-mode trap handler, reached through medeleg ---------------- */

strap:
    csrr    t0, scause
    li      t1, 9                    /* ecall from S-mode */
    bne     t0, t1, bad1
    csrr    t2, sstatus
    andi    t3, t2, SSTATUS_SPP      /* it was an S-mode trap, not user */
    beqz    t3, bad1
    li      t0, UART                 /* no SBI here: S-mode can touch it */
    li      t1, 'D'
    sb      t1, 0(t0)
    csrr    t0, sepc
    addi    t0, t0, 4                /* step over the ecall */
    csrw    sepc, t0
    sret

/* ---- the M-mode handler: a two-call SBI ------------------------------ */

mtrap:
    csrr    t0, mcause
    li      t1, 9                    /* ecall from S-mode */
    bne     t0, t1, mbad
    li      t1, 1
    beq     a7, t1, mputchar
    li      t1, 8
    beq     a7, t1, mshutdown
    li      t1, 9
    beq     a7, t1, mdelegate
    j       mbad

mdelegate:
    li      t0, 1 << 9               /* hand `ecall from S` to S-mode */
    csrw    medeleg, t0
    csrr    t0, mepc
    addi    t0, t0, 4
    csrw    mepc, t0
    mret

mputchar:
    li      t0, UART
    sb      a0, 0(t0)
    csrr    t0, mepc
    addi    t0, t0, 4
    csrw    mepc, t0
    mret

mshutdown:
    li      t0, HALT
    li      t1, 1                    /* the halt bit, not the byte value 0 */
    sb      t1, 0(t0)              /* byte 1 of the control: halt */
    j       mshutdown

mbad:
    li      t0, UART
    li      t1, 'M'
    sb      t1, 0(t0)
    li      t1, '!'
    sb      t1, 0(t0)
    li      t0, HALT
    li      t1, 1                    /* the halt bit, not the byte value 0 */
    sb      t1, 0(t0)
2:  j       2b

.section .bss
.align 4
stack:      .space 4096
stack_top:
