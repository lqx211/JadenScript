/* One letter, nothing else.
 *
 * Cell 0 of the text plane is 'A' in attr 0 (white). If the card, the font and
 * the painter agree, the screen is black with a single 8 x 15 'A' whose
 * top-left corner is the top-left corner of the stage -- pixel for pixel, no
 * scaling anywhere.
 */
.option norvc
.section .text.init
.globl _start
_start:
    li      s4, 0x50000000

    li      t0, 'A'
    sb      t0, 0(s4)               /* cell 0, char */
    sb      zero, 1(s4)             /* cell 0, attr 0 = white */

    /* control: text mode, then halt */
    li      t0, 0x12300
    add     t1, s4, t0
    sb      zero, 0(t1)
    li      t0, 1
    sb      t0, 1(t1)

1:  j       1b
