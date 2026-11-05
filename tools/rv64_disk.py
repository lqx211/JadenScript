#!/usr/bin/env python3
"""A block device on localhost, so the guest's disk does not have to be a list.

    python3 tools/rv64_disk.py userspace/rv64/disk.img [port]

The Scratch machine cannot hold a real root filesystem in a Stage list — one
item per byte runs out at a few tens of megabytes. A disk is different from RAM
though: it is read in blocks, it is mostly sequential, and the working set at
any instant is small. So the disk lives here, in a file, and the project keeps
only a few cached blocks in lists.

Protocol is one line of text each way:

    request   R <block>              block = 4096-byte index into the image
    reply     D <block> <hex>        8192 hex characters, or fewer at EOF
    request   T                      "what time is it on the host?"
    reply     T <us>                 wall clock in microseconds
    error     E <message>

Text is deliberate: `messageData` in the WebSocket extension is a string, and a
hex payload survives that with no encoding step on either side.

Reads are served from a small in-process page cache, so a run that pages the
same block repeatedly never touches the disk again — the same thing the project
does one level up in `X.diskCache`.
"""

from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path

import websockets

BLOCK = 4096
PORT = 65433
CLIENTS = 0
SERVED = 0


class Image:
    """Block reads over a file, with a tiny cache in front of it."""

    def __init__(self, path: Path, slots: int = 64) -> None:
        self.path = path
        self.fh = path.open("rb")
        self.size = path.stat().st_size
        self.slots = slots
        self.cache: dict[int, bytes] = {}
        self.order: list[int] = []
        self.reads = 0
        self.hits = 0

    @property
    def blocks(self) -> int:
        return (self.size + BLOCK - 1) // BLOCK

    def read_block(self, index: int) -> bytes:
        self.reads += 1
        if index in self.cache:
            self.hits += 1
            return self.cache[index]
        self.fh.seek(index * BLOCK)
        data = self.fh.read(BLOCK)
        if len(self.cache) >= self.slots:
            self.cache.pop(self.order.pop(0), None)
        self.cache[index] = data
        self.order.append(index)
        return data


def handle(image: Image, text: str, net=None) -> str:
    parts = text.strip().split()
    if parts and parts[0] == "N" and net is not None:
        # a frame from the guest's NIC: the bridge answers ARP, ping and DHCP,
        # and anything it hands back goes out as one reply per frame
        try:
            frame = bytes.fromhex(parts[1] if len(parts) > 1 else "")
        except ValueError:
            return "E not hex"
        replies = net.from_guest(frame)
        if not replies:
            return "N"
        return "\n".join("N " + reply.hex() for reply in replies)
    if not parts:
        return "E empty"
    if parts[0] == "S":                      # size probe
        return f"S {image.size} {BLOCK}"
    if parts[0] == "T":                      # the host clock, for clockMode 2
        # microseconds, not nanoseconds: a Scratch number is a double and is
        # exact in integers only up to 2^53, which is 9e15 -- 13 digits fit
        return f"T {time.time_ns() // 1000}"
    if parts[0] != "R" or len(parts) < 2:
        return f"E unknown request {parts[0]!r}"
    try:
        index = int(parts[1])
    except ValueError:
        return f"E not a block number: {parts[1]!r}"
    if index < 0 or index >= image.blocks:
        return f"E block {index} past the end ({image.blocks})"
    return f"D {index} {image.read_block(index).hex()}"


async def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__)
        return 2
    image = Image(Path(argv[1]))
    port = int(argv[2]) if len(argv) > 2 else PORT

    import rv64_net

    net = rv64_net.Bridge()

    async def echo(sock) -> None:
        global CLIENTS, SERVED
        CLIENTS += 1
        print(f"[disk] client connected ({CLIENTS} total)", flush=True)
        try:
            async for message in sock:
                reply = handle(image, str(message), net)
                SERVED += 1
                await sock.send(reply)
        except websockets.ConnectionClosed:
            pass
        finally:
            CLIENTS -= 1
            print(f"[disk] client gone; served {SERVED} requests, "
                  f"{image.hits}/{image.reads} cache hits", flush=True)

    print(f"[disk] {image.path} — {image.size} bytes, {image.blocks} blocks of {BLOCK}", flush=True)
    print(f"[disk] ws://127.0.0.1:{port}", flush=True)
    async with websockets.serve(echo, "127.0.0.1", port, max_size=64 * 1024 * 1024):
        await asyncio.Future()
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(asyncio.run(main(sys.argv)))
    except KeyboardInterrupt:
        print("\n[disk] stopped")
