"""A reference RV64IMAC interpreter, used as the spec for the JSW emulator.

The point of this file is *ground truth*, not speed: `tools/rv64_ref.py` runs the
same bare-metal payload that `qemu-system-riscv64` runs, and the two outputs are
compared byte for byte. Only once the Python and QEMU agree is the Scratch
version allowed to be written, because debugging a 64-bit carry chain inside
Scratch blocks without a reference is otherwise a losing game.

    python3 tools/rv64_ref.py build/rv64/payload.elf

Deliberate limits, matching the Scratch target:

* RV64IMAC, no floating point. The compressed forms are expanded to their
  32-bit equivalents before decoding, so the rest of the model never sees a
  16-bit instruction — ``rv64_rvc`` holds the table.
* The machine is the QEMU ``virt`` map: RAM at 0x8000_0000, a 16550 at
  0x1000_0000, the CLINT at 0x0200_0000.
* M-mode only: no S-mode, no paging, no external interrupts. Machine-mode traps
  *are* taken through ``mtvec``; with ``mtvec == 0`` there is nowhere to go, so
  a fault is reported as an error with the offending PC, which is what the
  bring-up payloads rely on.
"""

from __future__ import annotations

import struct
import sys
from dataclasses import dataclass, field

import rv64_rvc as rvc
from pathlib import Path

MASK64 = (1 << 64) - 1
MASK32 = (1 << 32) - 1

RAM_BASE = 0x8000_0000
UART_BASE = 0x1000_0000
#: the firmware ROM window, at the address QEMU virt maps its boot ROM to
ROM_BASE = 0x0000_1000
ROM_SIZE = 1 << 20
#: battery-backed settings, one small page
NVRAM_BASE = 0x1000_2000
NVRAM_SIZE = 256
NIC_BASE = 0x1000_3000      # the network card's window
#: the chipset: how much RAM the board gives the guest, and the reset button
BOARD_BASE = 0x1000_4000
BOARD_MEM_MIB = 0x0        # the size to give the guest, in MiB
BOARD_CMD = 0x4            # write 1: reset and apply MEM_MIB
BOARD_NOW = 0x8            # MiB actually live, read-only
BOARD_RESETS = 0xC         # warm resets since power-on, read-only
NIC_TXCTL = 0x00           # write 1: send the frame in the transmit buffer
NIC_TXLEN = 0x04
NIC_RXCTL = 0x10           # bit 0: a frame is waiting; write 1 to take it
NIC_RXLEN = 0x14
NIC_FRAME = 2048
NIC_TXBUF = 0x40
NIC_RXBUF = 0x840
CLINT_BASE = 0x0200_0000
CLINT_MSIP = CLINT_BASE + 0x0000
#: machine interrupt bits in `mie`/`mip`
MIP_MSIP = 1 << 3
MIP_MTIP = 1 << 7
#: `mcause` for a timer interrupt
CAUSE_MTIMER = (1 << 63) | 7
MSTATUS_SIE = 1 << 1
MSTATUS_MIE = 1 << 3
MSTATUS_SPIE = 1 << 5
MSTATUS_MPIE = 1 << 7
MSTATUS_SPP = 1 << 8
MSTATUS_MPP = 0b11 << 11
#: the bits `sstatus` shows: the S-mode subset of `mstatus`
SSTATUS_MASK = MSTATUS_SIE | MSTATUS_SPIE | MSTATUS_SPP
#: privilege levels, numbered the way the ISA numbers them
PRIV_U, PRIV_S, PRIV_M = 0, 1, 3

#: Sv39: three levels of nine bits, 4 KiB pages
SAT_PAGE = 4096
SAT_LEVELS = (1 << 30, 1 << 21, 1 << 12)     # the size a leaf at that level maps
PTE_V, PTE_R, PTE_W, PTE_X, PTE_U, PTE_G, PTE_A, PTE_D = (
    1 << 0, 1 << 1, 1 << 2, 1 << 3, 1 << 4, 1 << 5, 1 << 6, 1 << 7)
PA_TOP = 1 << 56
MSTATUS_MXR = 1 << 19
MSTATUS_SUM = 1 << 18
FAULT_FETCH, FAULT_LOAD, FAULT_STORE = 12, 13, 15
CLINT_MTIMECMP = CLINT_BASE + 0x4000
CLINT_MTIME = CLINT_BASE + 0xBFF8

# ---- the display, the machine's own card (there is no VGA on RISC-V) --------
PS2_BASE = 0x1000_1000
PS2_DATA = 0            # read: next scan code
PS2_STATUS = 4          # bit 0: one is waiting
DISP_BASE = 0x5000_0000
DISP_TEXT = 0              # 60 x 24 cells of (char, attr)
DISP_COLS = 80
DISP_ROWS = 30
DISP_GFX_OFF = 0x2000      # 320 x 200 palette indices, past the text plane
DISP_GFX_W = 320
DISP_GFX_H = 200
DISP_PAL_OFF = 0x50000     # 256 x (r, g, b), past a 640 x 480 plane
DISP_CTL_OFF = 0x50300     # byte 0 = mode, byte 1 = halt
DISP_SIZE = 0x51000      # text, a 640 x 480 plane, the palette, the control

REGS = (
    "zero", "ra", "sp", "gp", "tp", "t0", "t1", "t2",
    "s0", "s1", "a0", "a1", "a2", "a3", "a4", "a5",
    "a6", "a7", "s2", "s3", "s4", "s5", "s6", "s7",
    "s8", "s9", "s10", "s11", "t3", "t4", "t5", "t6",
)


def sext(value: int, bits: int) -> int:
    """Sign-extend ``value`` from ``bits`` to 64."""
    sign = 1 << (bits - 1)
    return (value & ((1 << bits) - 1)) - (2 * sign) if value & sign else value & ((1 << bits) - 1)


def sext64(value: int) -> int:
    value &= MASK64
    return value - (1 << 64) if value >> 63 else value


@dataclass
class Machine:
    ram_size: int = 8 << 20
    #: the memory size the chipset would hand out, in MiB. The reference model
    #: caps it at `max_mem_mib` because a bytearray is real host memory, while
    #: the Scratch side can afford a gigabyte for free. The *registers* behave
    #: identically, which is what the BIOS talks to.
    mem_mib: int = 8
    max_mem_mib: int = 8
    #: the image a reset loads again, kept so the reset vector has a program
    image: bytes = b""
    board_cmd: int = 0
    resets: int = 0
    reset_pending: bool = False
    regs: list[int] = field(default_factory=lambda: [0] * 32)
    pc: int = RAM_BASE
    ram: bytearray = field(default_factory=bytearray)
    out: bytearray = field(default_factory=bytearray)
    steps: int = 0
    mtime: int = 0
    mtimecmp: int = (1 << 64) - 1
    uart_thr: int = 0
    #: the keyboard's output buffer, oldest first
    ps2_fifo: list[int] = field(default_factory=list)
    #: machine-mode trap state. `mtvec == 0` means no handler is installed and
    #: a fault stays fatal, which is what the bring-up payloads rely on.
    mtvec: int = 0
    mepc: int = 0
    mcause: int = 0
    mtval: int = 0
    mstatus: int = 0
    mie: int = 0
    mip: int = 0
    mscratch: int = 0
    #: supervisor-mode trap state and the delegation registers. `stvec == 0`
    #: means an S-mode trap has nowhere to go, exactly like `mtvec`.
    priv: int = PRIV_M
    stvec: int = 0
    sscratch: int = 0
    sepc: int = 0
    scause: int = 0
    stval: int = 0
    satp: int = 0
    sie: int = 0
    sip: int = 0
    medeleg: int = 0
    mideleg: int = 0
    #: retired instructions, for `rdinstret`/`rdcycle`
    instret: int = 0
    #: every trap taken, as `(cause, epc, pc_after)`, for tests and debugging
    traps: list[tuple[int, int, int]] = field(default_factory=list)
    #: what the ROM window answers. `load_elf` fills it from the image, so a
    #: payload can read its own firmware back out of the window.
    rom: bytearray = field(default_factory=bytearray)
    #: the NVRAM page; 0xFF is "erased", so a blank one has no valid magic
    nvram: bytearray = field(default_factory=lambda: bytearray([0xFF] * NVRAM_SIZE))
    nvram_writes: int = 0
    rom_writes: int = 0
    #: how much `mtime` moves on a WFI. The board's smallest step is a frame,
    #: and WFI waits for the next one -- so a loop of WFI is a clock.
    ticks_per_wfi: int = 166_667
    #: the display planes are plain byte arrays here; the Scratch side keeps
    #: them in lists and repaints only the cells a store touched
    text: bytearray = field(default_factory=lambda: bytearray([32, 0] * (DISP_COLS * DISP_ROWS)))
    gfx: bytearray = field(default_factory=lambda: bytearray(DISP_GFX_W * DISP_GFX_H))
    palette: bytearray = field(default_factory=lambda: bytearray(768))
    mode: int = 0
    #: the network card: a transmit buffer, a receive buffer and a queue of
    #: frames the host bridge handed back
    net: object = None
    txbuf: bytearray = field(default_factory=lambda: bytearray(NIC_FRAME))
    rxbuf: bytearray = field(default_factory=lambda: bytearray(NIC_FRAME))
    txlen: int = 0
    rxlen: int = 0
    rxq: list = field(default_factory=list)
    tx_frames: int = 0
    resv: int | None = None          # LR/SC reservation
    gfx_w: int = DISP_GFX_W
    gfx_h: int = DISP_GFX_H
    error: str | None = None
    halted: bool = False
    #: every instruction retires through here, so a trace can be diffed too
    trace: list[str] | None = None

    def __post_init__(self) -> None:
        if not self.ram:
            # `mem_mib` is the strap and `ram_size` is what is live: a machine
            # that starts with a size only the strap knows about has to allocate
            # it, or the chipset's NOW register would disagree with reality.
            self.ram_size = min(max(self.mem_mib, 1), self.max_mem_mib) << 20
            self.ram = bytearray(self.ram_size)
        if self.net is None:
            import rv64_net
            self.net = rv64_net.Bridge()

    # ---- the chipset ---------------------------------------------------
    def _board_read(self, offset: int) -> int:
        if offset == BOARD_MEM_MIB:
            return self.mem_mib
        if offset == BOARD_NOW:
            return self.ram_size >> 20
        if offset == BOARD_RESETS:
            return self.resets
        return 0

    def _board_write(self, offset: int, value: int) -> None:
        """One *byte* of a register, the way the bus delivers it.

        A 32-bit store arrives as four of these, so the value between them is
        half written and can look absurd (a lane cleared, the bytes above it not
        there yet). Nothing here clamps: the size is clamped where it is spent,
        in `reset`, which is the only place that can judge it.
        """
        shift = 8 * (offset & 3)
        if offset < 4:
            self.mem_mib = (self.mem_mib & ~(0xFF << shift)) | ((value & 0xFF) << shift)
            return
        if offset < 8:
            self.board_cmd = (self.board_cmd & ~(0xFF << shift)) | ((value & 0xFF) << shift)
            if self.board_cmd & 1:
                self.reset_pending = True
            return

    def reset(self) -> None:
        """What the board does when the guest writes 1 to CMD.

        The guest gets `mem_mib` -- capped at what the model will allocate --
        and runs again from the reset vector. Kept out of `step` so the caller
        decides *when* (the Scratch build resets between frames), and out of
        `_board_write` so a store cannot re-enter the instruction that made it.
        """
        size = min(max(self.mem_mib, 1), self.max_mem_mib) << 20
        self.ram = bytearray(size)
        self.ram_size = size
        self.regs = [0] * 32
        self.out = bytearray()
        self.resets += 1
        self.reset_pending = False
        self.board_cmd = 0
        self.halted = False
        self.error = None
        self.mtvec = self.mepc = self.mcause = self.mtval = 0
        self.mstatus = self.mie = self.mip = self.mscratch = 0
        self.mtime = 0
        self.mtimecmp = (1 << 64) - 1
        self.resv = None
        self.pc = RAM_BASE
        if self.image:
            self.pc = load_elf(self, self.image)

    # ---- the network card ---------------------------------------------
    def _nic_read(self, offset: int) -> int:
        if offset == NIC_TXCTL:
            return 0
        if offset == NIC_TXLEN:
            return self.txlen & 0xFF
        if offset == NIC_RXCTL:
            return 1 if self.rxq else 0
        if offset == NIC_RXLEN:
            return self.rxlen & 0xFF
        if NIC_TXBUF <= offset < NIC_TXBUF + NIC_FRAME:
            return self.txbuf[offset - NIC_TXBUF]
        if NIC_RXBUF <= offset < NIC_RXBUF + NIC_FRAME:
            if offset - NIC_RXBUF < self.rxlen:
                return self.rxbuf[offset - NIC_RXBUF]
            return 0
        return 0

    def _nic_write(self, offset: int, value: int) -> None:
        if offset == NIC_TXCTL:
            if value & 1 and 0 < self.txlen <= NIC_FRAME:
                self.tx_frames += 1
                for reply in self.net.from_guest(bytes(self.txbuf[: self.txlen])):
                    self.rxq.append(reply[:NIC_FRAME])
            return
        if offset == NIC_TXLEN:
            self.txlen = value % (NIC_FRAME + 1)
            return
        if offset == NIC_RXCTL:
            if value & 1:
                if self.rxq:
                    frame = self.rxq.pop(0)
                    self.rxbuf[: len(frame)] = frame
                    self.rxlen = len(frame)
                else:
                    self.rxlen = 0
            return
        if NIC_TXBUF <= offset < NIC_TXBUF + NIC_FRAME:
            self.txbuf[offset - NIC_TXBUF] = value & 0xFF
            return
        if NIC_RXBUF <= offset < NIC_RXBUF + NIC_FRAME:
            self.rxbuf[offset - NIC_RXBUF] = value & 0xFF
            return

    # ---- memory -------------------------------------------------------
    def _uart_read(self, offset: int) -> int:
        if offset == 5:  # LSR: transmitter always ready
            return 0x20
        if offset == 0:
            return self.uart_thr
        return 0

    def _uart_write(self, offset: int, value: int) -> None:
        if offset == 0:
            self.uart_thr = value & 0xFF
            self.out.append(value & 0xFF)

    def _ps2_read(self, offset: int) -> int:
        if offset == PS2_DATA:
            return self.ps2_fifo.pop(0) if self.ps2_fifo else 0
        if offset == PS2_STATUS:
            return 0x05 if self.ps2_fifo else 0x04
        return 0

    def set_mode(self, mode: int) -> None:
        """A mode set, the way the card does it: new geometry, empty plane."""
        self.mode = mode
        w, h = (640, 480) if mode == 2 else (DISP_GFX_W, DISP_GFX_H)
        self.gfx_w, self.gfx_h = w, h
        self.gfx = bytearray(w * h)

    def _disp_read(self, offset: int) -> int:
        if offset < len(self.text):
            return self.text[offset]
        if DISP_GFX_OFF <= offset < DISP_GFX_OFF + len(self.gfx):
            return self.gfx[offset - DISP_GFX_OFF]
        if DISP_PAL_OFF <= offset < DISP_PAL_OFF + len(self.palette):
            return self.palette[offset - DISP_PAL_OFF]
        if offset == DISP_CTL_OFF:
            return self.mode
        if offset == DISP_CTL_OFF + 1:
            return 1 if self.halted else 0
        raise MemoryError(f"unmapped display read at +0x{offset:x}")

    def _disp_write(self, offset: int, value: int) -> None:
        if offset < len(self.text):
            self.text[offset] = value
            return
        if DISP_GFX_OFF <= offset < DISP_GFX_OFF + len(self.gfx):
            self.gfx[offset - DISP_GFX_OFF] = value
            return
        if DISP_PAL_OFF <= offset < DISP_PAL_OFF + len(self.palette):
            self.palette[offset - DISP_PAL_OFF] = value
            return
        if offset == DISP_CTL_OFF:
            self.set_mode(value % 3)
            return
        if offset == DISP_CTL_OFF + 1:
            if value & 1:
                self.halted = True
            return
        raise MemoryError(f"unmapped display write at +0x{offset:x}")

    def _clint_read(self, addr: int, size: int) -> int:
        if addr >= CLINT_MTIME:
            return (self.mtime >> (8 * (addr - CLINT_MTIME))) & ((1 << (8 * size)) - 1)
        return 0

    def _clint_write(self, addr: int, value: int, size: int) -> None:
        if CLINT_MTIMECMP <= addr < CLINT_MTIMECMP + 8:
            shift = 8 * (addr - CLINT_MTIMECMP)
            self.mtimecmp = (self.mtimecmp & ~(((1 << (8 * size)) - 1) << shift)) | (
                (value & ((1 << (8 * size)) - 1)) << shift
            )
            self.update_mtip()

    def read(self, addr: int, size: int) -> int:
        addr &= MASK64
        if RAM_BASE <= addr < RAM_BASE + self.ram_size:
            start = addr - RAM_BASE
            chunk = self.ram[start : start + size]
            if len(chunk) < size:
                raise MemoryError(f"read past RAM end at 0x{addr:x}")
            return int.from_bytes(chunk, "little")
        if ROM_BASE <= addr < ROM_BASE + ROM_SIZE:
            offset = addr - ROM_BASE
            return int.from_bytes(
                (self.rom[offset : offset + size] or b"\xff" * size).ljust(size, b"\xff"),
                "little",
            )
        if NVRAM_BASE <= addr < NVRAM_BASE + NVRAM_SIZE:
            return int.from_bytes(self.nvram[addr - NVRAM_BASE : addr - NVRAM_BASE + size].ljust(size, b"\xff"), "little")
        if BOARD_BASE <= addr < BOARD_BASE + 16:
            value = 0
            for k in range(size):
                off = addr - BOARD_BASE + k
                reg = self._board_read(off & ~3)
                value |= ((reg >> (8 * (off & 3))) & 0xFF) << (8 * k)
            return value
        if UART_BASE <= addr < UART_BASE + 8:
            return self._uart_read(addr - UART_BASE)
        if CLINT_BASE <= addr < CLINT_BASE + 0xC000:
            return self._clint_read(addr, size)
        if PS2_BASE <= addr < PS2_BASE + 8:
            return self._ps2_read(addr - PS2_BASE)
        if NIC_BASE <= addr < NIC_BASE + 0x1000:
            value = 0
            for k in range(size):
                value |= self._nic_read(addr - NIC_BASE + k) << (8 * k)
            return value
        if DISP_BASE <= addr < DISP_BASE + DISP_SIZE:
            value = 0
            for k in range(size):
                value |= self._disp_read(addr - DISP_BASE + k) << (8 * k)
            return value
        raise MemoryError(f"unmapped read of {size} at 0x{addr:x} (pc=0x{self.pc:x})")

    def write(self, addr: int, value: int, size: int) -> None:
        addr &= MASK64
        value &= (1 << (8 * size)) - 1
        if RAM_BASE <= addr < RAM_BASE + self.ram_size:
            start = addr - RAM_BASE
            if start + size > self.ram_size:
                raise MemoryError(f"write past RAM end at 0x{addr:x}")
            self.ram[start : start + size] = value.to_bytes(size, "little")
            return
        if ROM_BASE <= addr < ROM_BASE + ROM_SIZE:
            self.rom_writes += 1     # a ROM swallows the store
            return
        if NVRAM_BASE <= addr < NVRAM_BASE + NVRAM_SIZE:
            self.nvram[addr - NVRAM_BASE : addr - NVRAM_BASE + size] = value.to_bytes(size, "little")
            self.nvram_writes += 1
            return
        if BOARD_BASE <= addr < BOARD_BASE + 16:
            for k in range(size):
                self._board_write(addr - BOARD_BASE + k, (value >> (8 * k)) & 0xFF)
            return
        if UART_BASE <= addr < UART_BASE + 8:
            self._uart_write(addr - UART_BASE, value)
            return
        if CLINT_BASE <= addr < CLINT_BASE + 0xC000:
            self._clint_write(addr, value, size)
            return
        if PS2_BASE <= addr < PS2_BASE + 8:
            if addr - PS2_BASE == 0 and value & 0xFF == 0xAE:
                pass          # "enable scanning": nothing to do here
            return
        if NIC_BASE <= addr < NIC_BASE + 0x1000:
            for k in range(size):
                self._nic_write(addr - NIC_BASE + k, (value >> (8 * k)) & 0xFF)
            return
        if DISP_BASE <= addr < DISP_BASE + DISP_SIZE:
            for k in range(size):
                self._disp_write(addr - DISP_BASE + k, (value >> (8 * k)) & 0xFF)
            return
        raise MemoryError(f"unmapped write of {size} at 0x{addr:x} (pc=0x{self.pc:x})")

    def fetch(self) -> int:
        return self.read(self.pc, 4)

    def set_reg(self, index: int, value: int) -> None:
        if index:
            self.regs[index] = value & MASK64

    def get_reg(self, index: int) -> int:
        return self.regs[index] if index else 0

    # ---- execution ----------------------------------------------------
    # ---- traps and the timer -------------------------------------------
    def update_mtip(self) -> None:
        """MTIP is `mtime >= mtimecmp`, recomputed -- a level, not a latch.

        Writing `mtimecmp` into the future has to *deassert* a pending timer
        interrupt immediately; treating MTIP as sticky meant a handler that
        rearmed still saw its own interrupt pending and took it again.
        """
        if self.mtime >= self.mtimecmp:
            self.mip |= MIP_MTIP
        else:
            self.mip &= ~MIP_MTIP

    def tick(self, count: int = 1) -> None:
        """Advance `mtime`. One call is one frame on the Scratch side."""
        self.mtime = (self.mtime + count) & MASK64
        self.update_mtip()

    def pending_interrupt(self) -> int:
        """The highest-priority enabled machine interrupt, or 0."""
        if not (self.mstatus & MSTATUS_MIE and self.mtvec):
            return 0
        if self.mip & self.mie & MIP_MTIP:
            return MIP_MTIP
        if self.mip & self.mie & MIP_MSIP:
            return MIP_MSIP
        return 0

    def _retire(self) -> None:
        """Count an instruction that took an early exit.

        `mret`, `sret` and a trapping `ecall` all leave `step()` before the
        counter at the bottom, and the Scratch core counts every instruction it
        executes -- so the two models have to agree here or a differential run
        drifts by one per trap.
        """
        self.steps += 1
        self.instret += 1

    def _delegated(self, cause: int) -> bool:
        """Does this trap belong to S-mode? Interrupts use `mideleg`, exceptions
        `medeleg`, and only an S-mode trap can be delegated at all."""
        if self.priv > PRIV_S:
            return False
        if cause >> 63:
            return bool(self.mideleg >> (cause & 0x3F) & 1)
        return bool(self.medeleg >> cause & 1)

    def paging(self) -> bool:
        """Is Sv39 on? Only S and U translate, and only when `satp` says 8."""
        return self.priv <= PRIV_S and (self.satp >> 60) == 8

    def translate(self, va: int, kind: int) -> int | None:
        """`va` -> `pa`, or None when the walk faults.

        `kind` is FAULT_FETCH/LOAD/STORE, which picks the R/W/X check and the
        cause the caller raises. A/D are set by the walker the way the spec
        allows, so a kernel does not have to take a fault and retry on first
        touch of every page.
        """
        if not self.paging():
            return va
        va &= (1 << 39) - 1
        table = (self.satp & ((1 << 44) - 1)) << 12
        for level, span in enumerate(SAT_LEVELS):
            index = (va >> (30 - 9 * level)) & 0x1FF
            at = table + index * 8 - RAM_BASE
            if at < 0 or at + 8 > self.ram_size:
                return None
            pte = int.from_bytes(self.ram[at:at + 8], "little")
            if not pte & PTE_V:
                return None
            leaf = pte & (PTE_R | PTE_W | PTE_X)
            if leaf:
                if pte & PTE_U:
                    if self.priv == PRIV_S and not self.mstatus & MSTATUS_SUM:
                        return None
                elif self.priv == PRIV_U:
                    return None
                if kind == FAULT_STORE:
                    if not pte & PTE_W:
                        return None
                elif kind == FAULT_FETCH:
                    if not pte & PTE_X:
                        return None
                else:
                    readable = pte & PTE_R or (pte & PTE_X and self.mstatus & MSTATUS_MXR)
                    if not readable:
                        return None
                self._set_ad(table + index * 8, pte, kind)
                return ((pte >> 10) << 12) + (va & (span - 1))
            if level == 2:
                return None                      # a non-leaf at the last level
            table = (pte >> 10) << 12
        return None

    def _set_ad(self, pte_addr: int, pte: int, kind: int) -> None:
        want = PTE_A | (PTE_D if kind == FAULT_STORE else 0)
        if pte & want != want:
            start = pte_addr - RAM_BASE
            self.ram[start:start + 8] = (pte | want).to_bytes(8, "little")

    def take_trap(self, cause: int, epc: int, tval: int = 0) -> None:
        """Enter the handler: CSRs first, then the pc.

        A trap is delivered to S-mode when the hart was in S and the
        delegation register for that cause is set -- that is what lets an M-mode
        firmware hand `ecall` and page faults to a kernel.
        """
        if self._delegated(cause):
            if not self.stvec:
                raise RuntimeError(f"trap {cause:#x} at pc {epc:#x} with no stvec")
            self.sepc = epc
            self.scause = cause
            self.stval = tval
            was_enabled = self.mstatus & MSTATUS_SIE
            self.mstatus = (self.mstatus & ~MSTATUS_SIE) | MSTATUS_SPP
            if was_enabled:
                self.mstatus |= MSTATUS_SPIE
            base = self.stvec & ~0b11
            if self.stvec & 0b11 and cause >> 63:
                base = (base + 4 * (cause & 0x7FFFFFFF)) & MASK64
            self.traps.append((cause, epc, base))
            self.pc = base
            return
        if not self.mtvec:
            raise RuntimeError(f"trap {cause:#x} at pc {epc:#x} with no mtvec")
        self.mepc = epc
        self.mcause = cause
        self.mtval = tval
        was_enabled = self.mstatus & MSTATUS_MIE
        self.mstatus = (self.mstatus & ~MSTATUS_MIE & ~MSTATUS_MPP) | (self.priv << 11)
        self.priv = PRIV_M            # the handler itself runs in machine mode
        if was_enabled:
            self.mstatus |= MSTATUS_MPIE
        base = self.mtvec & ~0b11
        if self.mtvec & 0b11 and cause >> 63:
            base = (base + 4 * (cause & 0x7FFFFFFF)) & MASK64
        self.traps.append((cause, epc, base))
        self.pc = base

    def _csr_read(self, csr: int) -> int:
        return {
            0x100: self.mstatus & SSTATUS_MASK,   # sstatus is a view of mstatus
            0x104: self.sie,
            0x105: self.stvec,
            0x140: self.sscratch,
            0x141: self.sepc,
            0x142: self.scause,
            0x143: self.stval,
            0x144: self.sip,
            0x180: self.satp,
            0x300: self.mstatus,
            0x302: self.medeleg,
            0x303: self.mideleg,
            0x304: self.mie,
            0x305: self.mtvec,
            0x340: self.mscratch,
            0x341: self.mepc,
            0x342: self.mcause,
            0x343: self.mtval,
            0x344: self.mip,
            0xC00: self.instret,      # cycle
            0xC01: self.mtime,        # time
            0xC02: self.instret,      # instret
        }.get(csr, 0)

    @staticmethod
    def _machine_csr(csr: int) -> bool:
        """CSRs only M-mode may touch: the `m*` trap group and delegation."""
        return csr in (0x300, 0x302, 0x303, 0x304, 0x305, 0x340, 0x341, 0x342,
                       0x343, 0x344)

    def _csr_write(self, csr: int, value: int) -> None:
        value &= MASK64
        if csr == 0x100:
            self.mstatus = (self.mstatus & ~SSTATUS_MASK) | (value & SSTATUS_MASK)
        elif csr == 0x104:
            self.sie = value & 0xFFFF
        elif csr == 0x105:
            self.stvec = value
        elif csr == 0x140:
            self.sscratch = value
        elif csr == 0x141:
            self.sepc = value & ~1
        elif csr == 0x142:
            self.scause = value
        elif csr == 0x143:
            self.stval = value
        elif csr == 0x144:
            self.sip = value & 0xFFFF
        elif csr == 0x180:
            self.satp = value
        elif csr == 0x302:
            self.medeleg = value
        elif csr == 0x303:
            self.mideleg = value
        elif csr == 0x300:
            self.mstatus = value & 0xFFFF
        elif csr == 0x304:
            self.mie = value & 0xFFFF
        elif csr == 0x305:
            self.mtvec = value & MASK64
        elif csr == 0x340:
            self.mscratch = value
        elif csr == 0x341:
            self.mepc = value & ~1
        elif csr == 0x342:
            self.mcause = value
        elif csr == 0x343:
            self.mtval = value
        elif csr == 0x344:
            self.mip = (self.mip & MIP_MTIP) | (value & MIP_MSIP)
        # writes to the read-only counters are ignored, as the spec says

    def step(self) -> None:
        """Fetch, decode and execute one instruction."""
        interrupt = self.pending_interrupt()
        if interrupt:
            if interrupt == MIP_MTIP:
                self.mip &= ~MIP_MTIP          # the handler has to rearm mtimecmp
                self.take_trap(CAUSE_MTIMER, self.pc)
            else:
                self.take_trap((1 << 63) | 3, self.pc)

        pc0 = self.pc
        fetch_at = self.translate(pc0, FAULT_FETCH)
        if fetch_at is None:
            self.take_trap(FAULT_FETCH, pc0, pc0)
            self._retire()
            return
        ir = self.read(fetch_at, 4)
        if ir & 3 != 3:
            # A compressed instruction is the same operation in a smaller
            # encoding, so it is *expanded* and the decoder below runs anyway.
            # The pc still moves by two, which is what makes `c.jalr`'s link
            # register come out right without a special case.
            wide = rvc.expand(ir)
            if wide is None:
                raise ValueError(f"unsupported compressed instruction {ir:04x} at 0x{pc0:x}")
            ir = wide
            self.pc = (self.pc + 2) & MASK64
        else:
            self.pc = (self.pc + 4) & MASK64
        opcode = ir & 0x7F
        rd = (ir >> 7) & 31
        funct3 = (ir >> 12) & 7
        rs1 = (ir >> 15) & 31
        rs2 = (ir >> 20) & 31
        funct7 = (ir >> 25) & 0x7F
        a = self.get_reg(rs1)
        b = self.get_reg(rs2)
        imm_i = sext(ir >> 20, 12)
        imm_s = sext(((ir >> 25) << 5) | ((ir >> 7) & 0x1F), 12)
        imm_b = sext(
            (((ir >> 31) & 1) << 12)
            | (((ir >> 7) & 1) << 11)
            | (((ir >> 25) & 0x3F) << 5)
            | (((ir >> 8) & 0xF) << 1),
            13,
        )
        imm_u = sext(ir & 0xFFFF_F000, 32)
        imm_j = sext(
            (((ir >> 31) & 1) << 20)
            | (((ir >> 12) & 0xFF) << 12)
            | (((ir >> 20) & 1) << 11)
            | (((ir >> 21) & 0x3FF) << 1),
            21,
        )

        if self.trace is not None:
            self.trace.append(f"{pc0:08x} {ir:08x}")

        if opcode == 0b0110111:  # LUI
            self.set_reg(rd, imm_u)
        elif opcode == 0b0010111:  # AUIPC
            self.set_reg(rd, (pc0 + imm_u) & MASK64)
        elif opcode == 0b1101111:  # JAL
            self.set_reg(rd, self.pc)
            self.pc = (pc0 + imm_j) & MASK64
        elif opcode == 0b1100111:  # JALR
            target = (a + imm_i) & MASK64 & ~1
            self.set_reg(rd, self.pc)
            self.pc = target
        elif opcode == 0b1100011:  # BRANCH
            taken = {
                0: a == b,
                1: a != b,
                4: sext64(a) < sext64(b),
                5: sext64(a) >= sext64(b),
                6: a < b,
                7: a >= b,
            }.get(funct3, False)
            if taken:
                self.pc = (pc0 + imm_b) & MASK64
        elif opcode == 0b0000011:  # LOAD
            va = (a + imm_i) & MASK64
            addr = self.translate(va, FAULT_LOAD)
            if addr is None:
                self.take_trap(FAULT_LOAD, pc0, va)
                self._retire()
                return
            size = {0: 1, 1: 2, 2: 4, 3: 8, 4: 1, 5: 2, 6: 4}.get(funct3)
            if size is None:
                raise ValueError(f"bad load funct3 {funct3}")
            raw = self.read(addr, size)
            self.set_reg(rd, sext(raw, size * 8) if funct3 < 4 else raw)
        elif opcode == 0b0100011:  # STORE
            va = (a + imm_s) & MASK64
            addr = self.translate(va, FAULT_STORE)
            if addr is None:
                self.take_trap(FAULT_STORE, pc0, va)
                self._retire()
                return
            size = {0: 1, 1: 2, 2: 4, 3: 8}.get(funct3)
            if size is None:
                raise ValueError(f"bad store funct3 {funct3}")
            self.write(addr, b, size)
        elif opcode in (0b0010011, 0b0110011, 0b0011011, 0b0111011):
            self._alu(opcode, funct3, funct7, rd, a, b, imm_i, ir)
        elif opcode == 0b0101111:  # AMO
            self._amo(funct3, rd, a, b, (ir >> 27) & 0x1F)
        elif opcode == 0b0001111:  # FENCE
            pass
        elif opcode == 0b1110011:  # SYSTEM
            if ir == 0x00000073:  # ECALL
                if not self.mtvec:
                    raise RuntimeError("ecall with no SBI (M-mode payload only)")
                self.take_trap(11 if self.priv == PRIV_M else 9, pc0)
                self._retire()
                return
            if ir == 0x00100073:  # EBREAK
                if not self.mtvec:
                    raise RuntimeError(f"ebreak at pc {pc0:#x}")
                self.take_trap(3, pc0)           # breakpoint
                self._retire()
                return
            if ir == 0x30200073:  # MRET
                if not self.mtvec:
                    raise RuntimeError("mret without a trap handler installed")
                if self.priv != PRIV_M:
                    self.take_trap(2, pc0, ir)       # illegal from S or U
                    return
                self.pc = self.mepc
                self.priv = (self.mstatus & MSTATUS_MPP) >> 11
                if self.mstatus & MSTATUS_MPIE:
                    self.mstatus |= MSTATUS_MIE
                else:
                    self.mstatus &= ~MSTATUS_MIE
                self.mstatus &= ~MSTATUS_MPIE & MASK64
                self.mstatus &= ~MSTATUS_MPP & MASK64
                self._retire()
                return
            if ir == 0x10200073:  # SRET
                self.pc = self.sepc
                self.priv = PRIV_S if self.mstatus & MSTATUS_SPP else PRIV_U
                if self.mstatus & MSTATUS_SPIE:
                    self.mstatus |= MSTATUS_SIE
                else:
                    self.mstatus &= ~MSTATUS_SIE
                self.mstatus &= ~MSTATUS_SPIE & MASK64
                self.mstatus &= ~MSTATUS_SPP & MASK64
                self._retire()
                return
            if funct3 == 0:
                if (ir >> 25) == 0x09:            # SFENCE.VMA
                    self._retire()                # no TLB: every walk is fresh
                    return
                if ir == 0x10500073:              # WFI
                    # "wait for an interrupt", and the next one can only come
                    # with the next frame: that is the clock advancing
                    self.tick(self.ticks_per_wfi)
                    return
                raise ValueError(f"SYSTEM 0x{ir:08x} at 0x{pc0:x} is not implemented")
            # CSRRW/CSRRS/CSRRC and the immediate forms
            csr = (ir >> 20) & 0xFFF
            if self.priv != PRIV_M and self._machine_csr(csr):
                self.take_trap(2, pc0, ir)       # M-only CSR from S: illegal
                return
            old_value = self._csr_read(csr)
            if funct3 in (5, 6, 7):
                src = rs1
            else:
                src = a
            if funct3 in (1, 5):
                new_value = src
            elif funct3 in (2, 6):
                new_value = old_value | src if rs1 else old_value
            else:
                new_value = (old_value & ~src) & MASK64 if rs1 else old_value
            if rs1 or funct3 in (1, 5):
                self._csr_write(csr, new_value)
            self.set_reg(rd, old_value)
            self.regs[0] = 0
            self.steps += 1
            self.instret += 1
            return
        else:
            raise ValueError(f"unknown opcode 0x{opcode:02x} at 0x{pc0:x} (ir=0x{ir:08x})")

        self.regs[0] = 0
        self.steps += 1
        self.instret += 1

    def _alu(self, opcode, funct3, funct7, rd, a, b, imm_i, ir) -> None:
        is_word = opcode in (0b0011011, 0b0111011)
        is_imm = opcode in (0b0010011, 0b0011011)
        # A shift takes its amount from shamt in the immediate form and from rs2
        # in the register form; reading single bits 25:20 for a register shift
        # silently uses the rs2 encoding instead of the register's value.
        width = 0x1F if is_word else 0x3F
        shamt = ((ir >> 20) if is_imm else b) & width
        # Only the ALU immediates are *values*; a shift immediate carries its
        # opcode in funct7 and its amount in shamt, so it must not be folded in
        # as a number (`srai x, x, 4` would otherwise shift by 0x404 & 63).
        rhs = a if False else (imm_i if is_imm else b)
        if is_imm and funct3 not in (0b001, 0b101):
            rhs = imm_i & MASK64

        def word(value: int) -> int:
            return sext(value & MASK32, 32) & MASK64

        result = 0
        if funct3 == 0b000:  # ADD / ADDI / SUB, ADDW / ADDIW / SUBW
            result = a + rhs
            if not is_imm and funct7 == 0b0100000:
                result = a - rhs
            if is_word:
                result = word(result)
        elif funct3 == 0b001:  # SLL / SLLI / SLLW / SLLIW
            result = (a << shamt) & MASK64 if not is_word else word(a << shamt)
        elif funct3 == 0b010:  # SLT / SLTI
            result = 1 if sext64(a) < sext64(rhs) else 0
        elif funct3 == 0b011:  # SLTU / SLTIU
            result = 1 if a < rhs else 0
        elif funct3 == 0b100:  # XOR / XORI
            result = a ^ rhs
        elif funct3 == 0b101:  # SRL / SRA / SRLI / SRAI (+ W forms)
            if is_word:
                piece = sext(a & MASK32, 32)
            else:
                piece = a
            if funct7 == 0b0100000:
                # `a` is held zero-extended, so there is no sign bit for Python's
                # >> to replicate: sign-extend to a real negative int first.
                result = sext64(piece) >> shamt
            else:
                result = (piece & MASK64) >> shamt
            if is_word:
                result = word(result)
            else:
                result &= MASK64
        elif funct3 == 0b110:  # OR / ORI
            result = a | rhs
        elif funct3 == 0b111:  # AND / ANDI
            result = a & rhs

        if funct7 == 0b0000001 and not is_imm:
            result = self._muldiv(funct3, a, b, is_word)

        self.set_reg(rd, result & MASK64)

    def _muldiv(self, funct3, a, b, is_word: bool) -> int:
        if is_word:
            a32, b32 = sext(a & MASK32, 32), sext(b & MASK32, 32)
            if funct3 == 0:
                return sext((a32 * b32) & MASK32, 32) & MASK64
            if funct3 == 4:
                if b32 == 0:
                    return MASK64
                q = abs(a32) // abs(b32)
                return sext((-q if (a32 < 0) != (b32 < 0) else q) & MASK32, 32) & MASK64
            if funct3 == 6:
                if b32 == 0:
                    return a32 & MASK32
                r = abs(a32) % abs(b32)
                return sext((-r if a32 < 0 else r) & MASK32, 32) & MASK64
        if funct3 == 0:  # MUL
            return (a * b) & MASK64
        if funct3 == 1:  # MULH, signed x signed, high half
            return ((sext64(a) * sext64(b)) >> 64) & MASK64
        if funct3 == 2:  # MULHSU
            return ((sext64(a) * b) >> 64) & MASK64
        if funct3 == 3:  # MULHU
            return ((a * b) >> 64) & MASK64
        if funct3 == 4:  # DIV
            if b == 0:
                return MASK64
            sa, sb = sext64(a), sext64(b)
            q = abs(sa) // abs(sb)
            return (-q if (sa < 0) != (sb < 0) else q) & MASK64
        if funct3 == 5:  # DIVU
            return MASK64 if b == 0 else a // b
        if funct3 == 6:  # REM
            if b == 0:
                return a
            sa, sb = sext64(a), sext64(b)
            r = abs(sa) % abs(sb)
            return (-r if sa < 0 else r) & MASK64
        if funct3 == 7:  # REMU
            return a if b == 0 else a % b
        return 0

    def _amo(self, funct3, rd, addr_reg, src, op) -> None:
        addr = addr_reg & MASK64
        size = {2: 4, 3: 8}.get(funct3)
        if size is None:
            raise ValueError(f"AMO funct3 {funct3}")
        old = self.read(addr, size)
        if size == 8:
            so, ss = sext64(old), sext64(src)
        else:
            so, ss = sext(old & MASK32, 32), sext(src & MASK32, 32)

        if op == 0b00010:      # LR: load and reserve
            self.resv = addr
            self.set_reg(rd, sext(old, size * 8) if size == 4 else old)
            return
        if op == 0b00011:      # SC: store only if nothing intervened
            if self.resv == addr:
                self.write(addr, src & ((1 << (8 * size)) - 1), size)
                self.set_reg(rd, 0)
            else:
                self.set_reg(rd, 1)
            self.resv = None
            return

        if op == 0b00001:      # AMOSWAP
            new = src
        elif op == 0b00000:    # AMOADD
            new = old + src
        elif op == 0b00100:    # AMOXOR
            new = old ^ src
        elif op == 0b01100:    # AMOAND
            new = old & src
        elif op == 0b01000:    # AMOOR
            new = old | src
        elif op == 0b10000:    # AMOMIN
            new = so if so < ss else ss
        elif op == 0b10100:    # AMOMAX
            new = so if so > ss else ss
        elif op == 0b11000:    # AMOMINU
            new = old if old < src else src
        elif op == 0b11100:    # AMOMAXU
            new = old if old > src else src
        else:
            raise ValueError(f"AMO op {op:05b}")

        self.write(addr, new & ((1 << (8 * size)) - 1), size)
        self.set_reg(rd, sext(old, size * 8) if size == 4 else old)

    def run_frames(self, frames: int, batch: int = 2000, ticks: int = 166_667) -> str:
        """Run like the Scratch build does: `batch` instructions, then a tick.

        The Scratch machine has no wall clock of its own; `mtime` advances once
        per frame by `ticks`, which is the whole of its clock model. A payload
        that waits for a timer therefore has to be run this way, or the times
        the reference and the project see are different lengths.
        """
        # this loop *is* the frame clock, so a WFI must not add a second tick
        saved, self.ticks_per_wfi = self.ticks_per_wfi, 0
        try:
            for _ in range(frames):
                target = self.steps + batch
                while self.steps < target and not self.halted and self.error is None:
                    try:
                        self.step()
                    except (ValueError, RuntimeError) as exc:
                        self.error = str(exc)
                        break
                if self.reset_pending:
                    # the board's reset button, between frames, the way the
                    # Scratch build applies it
                    self.reset()
                    continue
                self.tick(ticks)
        finally:
            self.ticks_per_wfi = saved
        return self.out.decode("latin-1")

    def run(self, limit: int = 2_000_000) -> str:
        while self.steps < limit:
            if self.reset_pending:
                self.reset()
                continue
            try:
                self.step()
            except MemoryError as exc:
                self.error = str(exc)
                break
            except (ValueError, RuntimeError) as exc:
                self.error = str(exc)
                break
            if self.halted:
                break
        return self.out.decode("latin-1")


# ---- ELF loading -------------------------------------------------------

def load_elf(machine: Machine, image: bytes) -> int:
    """Load every PT_LOAD segment and return the entry point.

    The whole file also becomes the ROM window's contents, the way a board's
    flash holds the image the loader is reading from.
    """
    machine.rom = bytearray(image)
    machine.image = image           # so a chipset reset can load it again
    if image[:4] != b"\x7fELF":
        raise ValueError("not an ELF")
    if image[4] != 2:
        raise ValueError("need an ELFCLASS64 image")
    if image[5] != 1:
        raise ValueError("need a little-endian image")
    e_entry, e_phoff = struct.unpack_from("<QQ", image, 0x18)
    e_phentsize, e_phnum = struct.unpack_from("<HH", image, 0x36)
    for i in range(e_phnum):
        off = e_phoff + i * e_phentsize
        p_type, p_flags, p_offset, p_vaddr, p_paddr, p_filesz, p_memsz, _align = (
            struct.unpack_from("<IIQQQQQQ", image, off)
        )
        if p_type != 1:
            continue
        start = p_vaddr - RAM_BASE
        if start < 0 or start + p_memsz > machine.ram_size:
            raise ValueError(f"segment at 0x{p_vaddr:x} does not fit in RAM")
        machine.ram[start : start + p_filesz] = image[p_offset : p_offset + p_filesz]
        # memsz > filesz is .bss: already zero in a fresh bytearray
    return e_entry


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__)
        return 2
    image = Path(argv[1]).read_bytes()
    mach = Machine()
    mach.pc = load_elf(mach, image)
    text = mach.run()
    sys.stdout.write(text)
    if mach.error:
        sys.stderr.write(f"\n[ref] stopped after {mach.steps} steps: {mach.error}\n")
        return 1
    sys.stderr.write(f"\n[ref] {mach.steps} instructions\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
