/* sv39: paging, end to end.
 *
 * The firmware builds a three-level table, turns `satp` on, and drops into
 * S-mode. From there the payload proves four things and then takes a fault on
 * purpose:
 *
 *   1. the same word is reachable at two different virtual addresses, one
 *      identity and one through the table
 *   2. a store through the window is a store through the identity mapping
 *   3. the hardware set A on its own, and D only because of the store
 *   4. a load from an address that was never mapped traps to `stvec` with
 *      scause 13 and the address in stval
 *
 *     "SV39-OK"   every check held
 *     "SV39-BAD n" check n did not
 *     "SV39-NOFAULT" the bad load came back, which means there is no paging
 *
 * The mapping is three 1 GiB identity leaves (so the UART, the halt latch and
 * RAM itself stay reachable while the code is still running on the same pc) and
 * one more leaf at 0x3FC0_0000_0000 pointing at RAM: two names for one page.
 * That last one is above 4 GiB's worth of address space on purpose -- a walker
 * that only ever looks at the low word of the address cannot find it.
 *
 * Supervisor pages have U = 0, which is what keeps this a *kernel* test: an
 * S-mode access to a U page would be a fault, and none of these are.
 */
.option norvc
.equ UART, 0x10000000
.equ HALT, 0x50050301              /* control + 1: writing a byte here halts */
.equ WINDOW, 0x3FC0000000          /* root index 255, aimed at RAM */
.equ WINDOW_END, 0x4000000000
.equ UNMAPPED, 0xC0000000          /* root index 3: never filled */

.equ PTE_V, 1
.equ PTE_RWX, 14
.equ PTE_A, 1 << 6
.equ PTE_D, 1 << 7
.equ PTE_LEAF, PTE_V | PTE_RWX | PTE_A | PTE_D

.equ MSTATUS_MPP_S, 1 << 11
.equ FAULT_LOAD, 13

.section .text.init
.globl _start
_start:
    la      sp, stack_top
    la      t0, mtrap
    csrw    mtvec, t0

    /* an empty root table: 4 KiB of zeroes */
    la      s0, root
    li      t1, 0
    li      t2, 512
1:  sd      zero, 0(s0)
    addi    s0, s0, 8
    addi    t1, t1, 1
    blt     t1, t2, 1b

    la      s0, root
    li      t4, PTE_LEAF
    li      t2, 0                      /* VA 0     -> PA 0     (UART) */
    call    leaf
    li      t2, 1                      /* VA 1 GiB -> PA 1 GiB (halt latch) */
    call    leaf
    li      t2, 2                      /* VA 2 GiB -> PA 2 GiB (RAM, identity) */
    call    leaf
    li      t2, 255                    /* VA 0x3FC0_0000_0000 -> the same RAM */
    li      t3, 2                      /* ... at the physical gigabyte RAM lives in */
    call    leaf_at

    /* S-mode owns its own page faults from here */
    la      t0, strap
    csrw    stvec, t0
    li      t0, (1 << FAULT_LOAD) | (1 << 12) | (1 << 15)
    csrw    medeleg, t0

    la      t0, root
    srli    t0, t0, 12
    li      t1, 8
    slli    t1, t1, 60                 /* mode 8: Sv39 */
    or      t0, t0, t1
    csrw    satp, t0
    sfence.vma

    la      t0, smain
    csrw    mepc, t0
    li      t0, MSTATUS_MPP_S
    csrw    mstatus, t0
    mret

/* root[t2] = a leaf for the gigabyte t3 -> t3 (t3 defaults to t2) */
leaf:
    mv      t3, t2
leaf_at:
    slli    t5, t3, 30                 /* PA = t3 GiB */
    srli    t5, t5, 12                 /* PPN */
    slli    t5, t5, 10
    or      t5, t5, t4
    slli    t6, t2, 3
    add     t6, s0, t6
    sd      t5, 0(t6)
    ret

/* ---- S-mode ---------------------------------------------------------- */

smain:
    /* scratch through the identity mapping, and through the window */
    la      s1, scratch
    li      t3, 0x80000000
    sub     s2, s1, t3
    li      t3, WINDOW
    add     s2, s2, t3

    /* the table has to be in RAM before anything can be read through it */
    la      t0, root
    ld      t1, 2 * 8(t0)
    li      t2, 0x200000CF
    bne     t1, t2, bad4
    ld      t1, 255 * 8(t0)
    bne     t1, t2, bad5

    li      t1, 0x12345678
    sw      t1, 0(s1)                  /* write the identity name */
    lw      t4, 0(s2)                  /* read it back through the table */
    bne     t1, t4, bad1

    li      t4, 0x0BADF00D
    sw      t4, 0(s2)                  /* and the other way round */
    lw      t4, 0(s1)
    li      t5, 0x0BADF00D
    bne     t4, t5, bad2

    /* A was set by the walk itself; D only because those were stores */
    la      t0, root
    ld      t1, 255 * 8(t0)
    andi    t2, t1, PTE_A
    beqz    t2, bad3
    andi    t2, t1, PTE_D
    beqz    t2, bad3

    /* a gigabyte that was never mapped: this must not come back */
    li      t0, UNMAPPED
    lw      t1, 0(t0)
    la      a0, nofault
    call    puts
    j       halt

bad1:
    la      a0, bad_1
    j       report
bad2:
    la      a0, bad_2
    j       report
bad3:
    la      a0, bad_3
    j       report
bad4:
    la      a0, bad_4
    j       report
bad5:
    la      a0, bad_5
    j       report

report:
    call    puts
    j       halt

/* ---- the S-mode fault handler, reached through medeleg --------------- */

strap:
    csrr    t0, scause
    li      t1, FAULT_LOAD
    bne     t0, t1, sbad
    csrr    t0, stval
    li      t1, UNMAPPED
    bne     t0, t1, sbad
    csrr    t0, sepc                  /* sepc is the load itself, not the next one */
    la      t1, smain                 /* so it is somewhere in this payload */
    bltu    t0, t1, sbad
    la      a0, ok
    call    puts
    j       halt

sbad:
    la      a0, sbad_msg
    call    puts
    j       halt

/* ---- M-mode faults: a paging bug that reached the firmware ----------- */

mtrap:
    la      a0, mbad_msg
    call    puts
    j       halt

puts:                                  /* a0 = zero-terminated string */
    li      t0, UART
1:  lbu     t1, 0(a0)
    beqz    t1, 2f
    sb      t1, 0(t0)
    addi    a0, a0, 1
    j       1b
2:  ret

halt:
    li      t0, HALT
    li      t1, 1                      /* the halt bit, not the byte value 0 */
    sb      t1, 0(t0)
3:  j       3b

.section .rodata
ok:         .asciz "SV39-OK\n"
nofault:    .asciz "SV39-NOFAULT\n"
bad_1:      .asciz "SV39-BAD 1\n"
bad_2:      .asciz "SV39-BAD 2\n"
bad_3:      .asciz "SV39-BAD 3\n"
bad_4:      .asciz "SV39-BAD 4 root[2]\n"
bad_5:      .asciz "SV39-BAD 5 root[255]\n"
sbad_msg:   .asciz "SV39-BAD trap\n"
mbad_msg:   .asciz "SV39-BAD machine\n"

.section .bss
.align 12
root:       .space 4096
scratch:    .space 16
.align 4
stack:      .space 4096
stack_top:
