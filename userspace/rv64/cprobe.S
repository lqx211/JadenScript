/* cprobe: a payload that is *compressed* on purpose.
 *
 * The machine is RV64IMA **C** now, and the only way to know the C extension
 * works is to run instructions that exist only in the 16-bit encoding. So this
 * is one byte of output per instruction form: `c.li`, `c.addi`, `c.ld`, `c.sd`,
 * `c.lw`, `c.mv`, `c.add`, `c.slli`, `c.beqz`, `c.j`, `c.addi16sp`'s cousins
 * and the stack forms, with the message written through the UART.
 *
 *     "C-OK"          if every form produced what it means
 *     "C-BAD <n>"     the first form that did not
 */
.option rvc
.equ UART, 0x10000000
.equ HALT, 0x50050301

.section .text.init
.globl _start
_start:
    la      sp, stack_top
    /* c.li + c.addi: 3 + 1 = 4 -> 'C' is 67; check the arithmetic first */
    li      a0, 0
    c.li    a0, 3
    c.addi  a0, 1
    li      t0, 4
    bne     a0, t0, fail_1

    /* c.ld / c.sd / c.lw / c.sw, once through the stack pointer (the *sp
     * forms) and once through a saved pointer (the r' forms, which only name
     * x8..x15 -- that restriction is the whole trick of the encoding) */
    li      a0, 0x11223344
    c.sdsp  a0, 0(sp)
    c.ldsp  a1, 0(sp)
    bne     a0, a1, fail_2
    la      s1, scratch
    c.sd    a0, 0(s1)
    c.ld    a1, 0(s1)
    bne     a0, a1, fail_2
    li      a0, 0x5A
    c.sw    a0, 8(s1)
    c.lw    a1, 8(s1)
    andi    a1, a1, 255
    li      t5, 0x5A
    bne     a1, t5, fail_3
    c.swsp  a0, 16(sp)
    c.lwsp  a1, 16(sp)
    andi    a1, a1, 255
    bne     a1, t5, fail_3

    /* c.mv, c.add, c.slli: a0 = 4 again, since the block above moved it */
    c.li    a0, 3
    c.addi  a0, 1
    c.mv    a1, a0
    c.add   a1, a0
    li      t0, 8
    bne     a1, t0, fail_4
    c.slli  a1, 4
    li      t0, 128
    bne     a1, t0, fail_5

    /* c.beqz / c.j: take the branch only when the value is zero */
    li      a2, 0
    c.beqz  a2, beq_ok
    j       fail_6
beq_ok:
    li      a2, 1
    c.bnez  a2, bne_ok
    j       fail_7
bne_ok:
    /* c.andi / c.srli / c.sub */
    li      a3, 0xFF
    c.andi  a3, 15
    li      t0, 15
    bne     a3, t0, fail_8
    c.srli  a3, 2
    li      t0, 3
    bne     a3, t0, fail_9
    li      a4, 10
    c.sub   a4, a3
    li      t0, 7
    bne     a4, t0, fail_10

    /* the message */
    la      a5, msg_ok
    c.j     emit
fail_1: la a5, msg_bad_1
    j       emit
fail_2: la a5, msg_bad_2
    j       emit
fail_3: la a5, msg_bad_3
    j       emit
fail_4: la a5, msg_bad_4
    j       emit
fail_5: la a5, msg_bad_5
    j       emit
fail_6: la a5, msg_bad_6
    j       emit
fail_7: la a5, msg_bad_7
    j       emit
fail_8: la a5, msg_bad_8
    j       emit
fail_9: la a5, msg_bad_9
    j       emit
fail_10: la a5, msg_bad_10

emit:
    li      t0, UART
1:  lbu     s1, 0(a5)
    c.beqz  s1, done
    sb      s1, 0(t0)
    c.addi  a5, 1
    j       1b
done:
    la      t0, HALT
    li      t1, 1
    sb      t1, 0(t0)
2:  j       2b

.section .rodata
msg_ok:    .asciz "C-OK\n"
msg_bad_1: .asciz "C-BAD 1\n"
msg_bad_2: .asciz "C-BAD 2\n"
msg_bad_3: .asciz "C-BAD 3\n"
msg_bad_4: .asciz "C-BAD 4\n"
msg_bad_5: .asciz "C-BAD 5\n"
msg_bad_6: .asciz "C-BAD 6\n"
msg_bad_7: .asciz "C-BAD 7\n"
msg_bad_8: .asciz "C-BAD 8\n"
msg_bad_9: .asciz "C-BAD 9\n"
msg_bad_10: .asciz "C-BAD 10\n"

.section .bss
.align 4
stack: .space 4096
stack_top:
scratch: .space 64
