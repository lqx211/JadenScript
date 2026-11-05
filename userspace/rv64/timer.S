/* rv64 timer payload: three machine timer interrupts, then a clean stop.
 *
 * This is the smallest program that proves the whole interrupt path:
 *
 *   CLINT mtime  ──▶ mtimecmp reached ──▶ mip.MTIP ──▶ mie.MTIE + mstatus.MIE
 *                 ──▶ trap to mtvec ──▶ handler rearm ──▶ mret back to wfi
 *
 * Everything it prints is a line of text, so the Python reference and QEMU can
 * be diffed literally. The clock is counted, not measured: the trap count, the
 * `mepc` the handler came back to and the `mcause` bits are what a machine has
 * to get right, and none of them depend on how fast the host is.
 *
 * QEMU virt has the same CLINT at 0x0200_0000 with a 10 MHz `mtime`, so the
 * same numbers have to come out of both.
 */
.option norvc
.section .text.init
.globl _start
_start:
    la      sp, stack_top
    la      t0, handler
    csrw    0x305, t0               /* mtvec: direct mode, one entry */

    /* arm the first timer: mtimecmp = mtime + PERIOD */
    call    rearm
    li      t0, 0x80                /* mie.MTIE */
    csrw    0x304, t0
    li      t0, 8                   /* mstatus.MIE */
    csrs    0x300, t0

    la      a0, msg_armed
    call    puts

    /* Wait for three interrupts. Each one returns to `wait`, so the loop is a
     * wfi that a timer can wake: that is what the flag is for. */
    li      s3, 0                   /* ticks the handler has counted */
wait:
    li      t1, 3
    bge     s3, t1, done
    wfi
    j       wait

done:
    /* stop the timer and report what the handler saw */
    csrw    0x304, zero             /* mie = 0 */
    la      a0, msg_done
    call    puts
    csrr    a0, 0x342               /* mcause */
    call    phex
    mv      a0, s3                  /* how many times the handler ran */
    call    pdec
    la      t0, wait
    sub     a0, s5, t0              /* s5 is the last mepc */
    li      t1, 16
    sltu    a0, a0, t1              /* 1 = the last trap came from the loop */
    call    pdec
    la      a0, msg_halt
    call    puts
1:  j       1b

/* mtimecmp = mtime + PERIOD, and re-enabling is what clears MTIP */
rearm:
    li      t0, 0x0200BFF8          /* mtime */
    ld      t1, 0(t0)
    /* 50 ms at 10 MHz. The period has to be longer than a frame: a frame is
     * this machine's time quantum, and a timer that expires inside one cannot
     * be observed at all. */
    li      t2, 500000
    add     t1, t1, t2
    li      t0, 0x02004000          /* mtimecmp, hart 0 */
    sd      t1, 0(t0)
    ret

handler:
    /* Save nothing but the registers used: the payload has no ABI to honour,
     * and mepc/mstatus live in CSRs the trap already wrote. */
    addi    s3, s3, 1
    csrr    s4, 0x342               /* mcause, to prove the interrupt bit */
    csrr    s5, 0x341               /* mepc of this trap */
    csrr    s6, 0x300               /* mstatus: MIE has to be clear here */
    call    rearm
    call    report
    mret

/* a0 is not preserved by puts, so keep the arguments in s-registers */
report:
    addi    sp, sp, -16
    sd      ra, 8(sp)
    la      a0, msg_tick
    call    puts
    mv      a0, s3
    call    pdec
    mv      a0, s4                  /* mcause */
    call    phex
    andi    a0, s6, 8               /* mstatus.MIE inside the handler: 0 */
    call    phex
    /* mepc depends on how far into the wait loop the frame ended, so report
     * only the fact that matters: the trap came from inside that loop. */
    la      t0, wait
    sub     a0, s5, t0
    li      t1, 16                  /* the loop is four instructions */
    sltu    a0, a0, t1
    call    pdec
    ld      ra, 8(sp)
    addi    sp, sp, 16
    ret

/* a0 = unsigned decimal, no padding, then a newline */
pdec:
    addi    sp, sp, -32
    sd      ra, 24(sp)
    sd      s2, 16(sp)
    sd      s3, 8(sp)
    mv      s2, a0
    li      s3, 0                   /* digits pushed */
1:  li      t0, 10
    remu    t1, s2, t0
    divu    s2, s2, t0
    addi    t1, t1, 48
    addi    sp, sp, -16             /* keep them on the stack, reversed */
    sd      t1, 0(sp)
    addi    s3, s3, 1
    bnez    s2, 1b
2:  ld      a0, 0(sp)
    addi    sp, sp, 16
    call    putc
    addi    s3, s3, -1
    bnez    s3, 2b
    li      a0, 10
    call    putc
    ld      s3, 8(sp)
    ld      s2, 16(sp)
    ld      ra, 24(sp)
    addi    sp, sp, 32
    ret

/* a0 = value, printed as 16 hex digits and a newline */
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

/* a0 = char; leaf */
putc:
    li      t0, 0x10000000
1:  lbu     t1, 5(t0)
    andi    t1, t1, 0x20
    beqz    t1, 1b
    sb      a0, 0(t0)
    ret

.section .rodata
msg_armed:  .asciz "timer armed, waiting for three ticks\n"
msg_tick:   .asciz "tick "
msg_space:  .asciz " "
msg_done:   .asciz "done, mcause / mepc / handler runs:\n"
msg_halt:   .asciz "halted\n"

.section .bss
.align 4
stack:      .space 8192
stack_top:
