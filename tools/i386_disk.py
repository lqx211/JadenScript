#!/usr/bin/env python3
"""A hard disk for the i386 machine, in a file, on the network.

    python3 tools/i386_disk.py build/i386/disk.img          # serve it
    python3 tools/i386_disk.py --ctl status                 # ask it how it is

The Scratch machine cannot hold a real disk in a Stage list -- an item per byte
runs out at a few tens of megabytes, and a kernel is a megabyte before it has
done anything. A disk is different from RAM: it is read in sectors, it is mostly
sequential, and the working set at any instant is small. So the disk lives here,
in a file, and the project asks for one sector at a time.

Two servers, because they answer two different questions:

* the **WebSocket** port speaks to the machine, one line of text each way, the
  same protocol `tools/rv64_disk.py` uses;
* the **HTTP** port speaks to a panel -- or to a person with curl -- and can
  change how the disk behaves while it is mounted.

The interesting part is not the storage. It is that a disk is a device that
fails, and a machine that has never seen a disk fail is a machine whose error
paths have never run. So this one can:

  * fail writes, on a probability or on demand, one sector and no more;
  * go offline, so the next request is answered with an error rather than data;
  * have an **accident** -- power lost mid-transfer, the volume left dirty, and
    the sectors that were in flight not written;
  * be slow, so a driver's timeout handling is exercised rather than assumed.

Protocol, one text line each way:

    R <lba> <count>      read count sectors of 512 bytes, answer D <hex>
    W <lba> <hex>        write, answer W ok or W <reason>
    WW <lba> <w> [<w>..] write 32-bit words, little-endian, as the machine holds
                         them; answers like W
    H <lba> <count>      a read the machine served itself: count it, send it
                         no data, so SMART sees the traffic
    I                    IDENTIFY DEVICE as JSON
    S                    SMART as JSON
    T                    the host clock, in microseconds
    P                    progress: flush and answer with the counters

Everything is hex because `messageData` in the WebSocket extension is a string,
and hex survives that with no encoding step on either side.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import random
import sys
import threading
import time
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import websockets

SECTOR = 512
WS_PORT = 65440
HTTP_PORT = 65441

#: what a real drive would call itself
MODEL = "JSW VIRTUAL DISK"
SERIAL = "JSW2024A00001"
FIRMWARE = "A07"


@dataclass
class Faults:
    """How the disk misbehaves. Every field is something a real one does."""

    write_error_rate: float = 0.0     # 0..1, per sector written
    read_error_rate: float = 0.0
    offline: bool = False
    latency_ms: float = 0.0
    accident: bool = False            # power lost, volume dirty until remount
    reject_after: int = 0             # go offline after this many requests


@dataclass
class Smart:
    """The counters a drive keeps about itself, and the file that keeps them."""

    power_on_seconds: float = 0.0
    writes: int = 0
    reads: int = 0
    reallocated: int = 0
    pending: int = 0
    crc_errors: int = 0
    power_cycles: int = 1
    dirty: bool = False
    hours: float = 0.0

    def as_dict(self) -> dict:
        return {
            "power_on_hours": round(self.power_on_seconds / 3600, 3),
            "power_cycles": self.power_cycles,
            "reads": self.reads,
            "writes": self.writes,
            "reallocated_sectors": self.reallocated,
            "pending_sectors": self.pending,
            "crc_errors": self.crc_errors,
            "dirty": self.dirty,
            # 100 is "as new"; these are the values a monitor shows
            "attributes": {
                "5": max(1, 100 - self.reallocated),
                "9": int(self.power_on_seconds / 3600) & 0xFF,
                "12": 100,
                "194": max(1, 100 - self.crc_errors),
                "197": max(1, 100 - self.pending),
            },
        }


class Volume:
    """The image, the faults and the counters, behind one lock."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.lock = threading.RLock()
        self.faults = Faults()
        self.smart = Smart()
        self.fh = None
        self.size = 0
        self.mounted = False
        self.started = time.time()
        self.served = 0
        self.errors = 0
        self.last_error = ""
        self.metafile = self.path.with_suffix(self.path.suffix + ".smart.json")
        self.clean = True          # nothing has failed since this was mounted
        self.mount()

    # ---- mounting ----------------------------------------------------

    def mount(self) -> None:
        with self.lock:
            if self.fh is not None:
                self.fh.close()
            if not self.path.exists():
                self.path.parent.mkdir(parents=True, exist_ok=True)
                self.path.write_bytes(b"\0" * (SECTOR * 128))
            self.fh = self.path.open("r+b")
            self.size = self.path.stat().st_size
            self.mounted = True
            self.faults.offline = False
            self.faults.accident = False
            # the log first, then this mount counted on top of it
            self.load_meta()
            self.smart.power_cycles += 1
            # the dirty bit is the volume's, not this process's: a drive that
            # lost power comes back dirty and stays dirty until something
            # checks it, which is what a disk checker reads it for
            self.clean = True

    def unmount(self) -> None:
        with self.lock:
            if self.clean:
                self.smart.dirty = False      # a flush, so nothing is in doubt
            self.save_meta()
            if self.fh is not None:
                self.fh.close()
            self.fh = None
            self.mounted = False

    def load_meta(self) -> None:
        if not self.metafile.exists():
            return
        try:
            data = json.loads(self.metafile.read_text())
        except (OSError, ValueError):
            return
        for key in ("power_on_seconds", "writes", "reads", "reallocated",
                    "pending", "crc_errors", "power_cycles", "dirty"):
            if key in data:
                setattr(self.smart, key, data[key])

    def save_meta(self) -> None:
        self.smart.power_on_seconds += time.time() - self.started
        self.started = time.time()
        try:
            self.metafile.write_text(json.dumps(self.smart.as_dict(), indent=1))
        except OSError:
            pass

    # ---- the device --------------------------------------------------

    @property
    def sectors(self) -> int:
        return self.size // SECTOR

    def identify(self) -> dict:
        with self.lock:
            return {
                "model": MODEL,
                "serial": SERIAL,
                "firmware": FIRMWARE,
                "sectors": self.sectors,
                "sector_size": SECTOR,
                "bytes": self.size,
                "mounted": self.mounted,
                "offline": self.faults.offline,
            }

    def status(self) -> dict:
        with self.lock:
            return {
                "model": MODEL,
                "serial": SERIAL,
                "firmware": FIRMWARE,
                "path": str(self.path),
                "mounted": self.mounted,
                "bytes": self.size,
                "sectors": self.sectors,
                "clients": CLIENTS,
                "served": self.served,
                "errors": self.errors,
                "last_error": self.last_error,
                "uptime_s": round(time.time() - self.started, 2),
                "faults": {
                    "write_error_rate": self.faults.write_error_rate,
                    "read_error_rate": self.faults.read_error_rate,
                    "offline": self.faults.offline,
                    "latency_ms": self.faults.latency_ms,
                    "accident": self.faults.accident,
                },
                "smart": self.smart.as_dict(),
            }

    def _wait(self) -> None:
        if self.faults.latency_ms > 0:
            time.sleep(self.faults.latency_ms / 1000.0)

    def read(self, lba: int, count: int = 1) -> tuple[bool, str]:
        with self.lock:
            if not self.mounted or self.faults.offline:
                self._fail("drive is offline")
                return False, "offline"
            if lba < 0 or lba >= self.sectors:
                self._fail(f"sector {lba} past the end ({self.sectors})")
                return False, "past the end"
            # A read that runs off the end answers with what is left. A client
            # pulling a whole image asks for a round number of sectors and the
            # image is not a round number of sectors; refusing the last short
            # request would truncate the kernel by up to sixty-three sectors.
            count = min(count, self.sectors - lba)
            self._wait()
            if self.faults.read_error_rate and random.random() < self.faults.read_error_rate:
                self.smart.crc_errors += 1
                self._fail("uncorrectable read error")
                return False, "read error"
            self.fh.seek(lba * SECTOR)
            data = self.fh.read(count * SECTOR)
            self.smart.reads += count
            self.served += 1
            return True, data.hex()

    def hint(self, lba: int, count: int = 1) -> None:
        """A read the machine served itself: count it, and let the panel and the
        SMART log see the traffic without shipping the data twice."""
        with self.lock:
            if not self.mounted or self.faults.offline:
                self._fail("drive is offline")
                return
            if lba < 0 or lba + count > self.sectors:
                self._fail(f"sector {lba}+{count} past the end ({self.sectors})")
                return
            self.smart.reads += count
            self.served += 1

    def write(self, lba: int, blob: bytes) -> tuple[bool, str]:
        with self.lock:
            if not self.mounted or self.faults.offline:
                self._fail("drive is offline")
                return False, "offline"
            count = max(1, len(blob) // SECTOR)
            if lba < 0 or lba + count > self.sectors:
                self._fail(f"sector {lba}+{count} past the end")
                return False, "past the end"
            self._wait()
            if self.faults.write_error_rate and random.random() < self.faults.write_error_rate:
                # a real drive hands the sector back to the spare pool when it
                # cannot write it, which is what "reallocated" counts
                self.smart.reallocated += 1
                self.smart.dirty = True
                self.clean = False
                self._fail("write fault, sector reallocated")
                return False, "write error"
            if self.faults.accident:
                self.accident()
                return False, "power lost"
            self.fh.seek(lba * SECTOR)
            self.fh.write(blob)
            self.fh.flush()
            self.smart.writes += count
            self.served += 1
            return True, "ok"

    def _fail(self, why: str) -> None:
        self.errors += 1
        self.last_error = why

    def accident(self) -> None:
        """Power lost in the middle of a transfer.

        The sectors in flight are gone, the volume is left dirty, and the drive
        stops answering until it is mounted again -- which is what a machine
        with a broken filesystem has to be able to survive.
        """
        with self.lock:
            self.smart.dirty = True
            self.clean = False
            self.smart.pending += 1
            self.faults.offline = True
            self.faults.accident = True
            self.faults.write_error_rate = 1.0
            self._fail("power lost during a write")
            self.save_meta()

    def set_faults(self, **kw) -> dict:
        with self.lock:
            for key, value in kw.items():
                if not hasattr(self.faults, key):
                    continue
                if key in ("offline", "accident"):
                    setattr(self.faults, key, bool(value))
                elif key == "reject_after":
                    setattr(self.faults, key, int(value))
                else:
                    setattr(self.faults, key, float(value))
            return self.status()

    def progress(self) -> dict:
        with self.lock:
            self.save_meta()
            return {
                "saved": str(self.metafile),
                **self.smart.as_dict(),
            }



#: The panel. One page, no build step, no dependency: it is served out of this
#: file and talks to the same control API curl talks to, so anything it can do
#: can be done from a shell, and anything a shell can do it shows.
PANEL = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<title>i386 disk</title>
<style>
 body{font:13px/1.45 ui-monospace,Menlo,Consolas,monospace;margin:0;background:#14161a;color:#d8dee9}
 header{padding:14px 20px;background:#1b1f27;border-bottom:1px solid #2b3140;display:flex;
        gap:18px;align-items:baseline}
 h1{font-size:15px;margin:0;font-weight:600;letter-spacing:.04em}
 h2{font-size:12px;margin:0 0 8px;color:#8f9bb3;text-transform:uppercase;letter-spacing:.08em}
 main{display:grid;grid-template-columns:repeat(auto-fit,minmax(310px,1fr));gap:14px;padding:16px 20px}
 section{background:#1b1f27;border:1px solid #2b3140;border-radius:6px;padding:12px 14px}
 table{width:100%;border-collapse:collapse}
 td{padding:2px 0} td:first-child{color:#8f9bb3}
 .ok{color:#7fd18b} .bad{color:#e06c75} .warn{color:#e5c07b}
 input[type=range]{width:100%}
 button{background:#252b36;color:#d8dee9;border:1px solid #39414f;border-radius:4px;
        padding:5px 10px;font:inherit;cursor:pointer}
 button:hover{background:#313a48}
 button.danger{border-color:#7a2f34;color:#e06c75}
 label{display:block;margin:7px 0 2px;color:#8f9bb3}
 .row{display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin-top:10px}
 .dot{width:8px;height:8px;border-radius:50%;display:inline-block;margin-right:6px}
</style></head><body>
<header><h1>i386 &middot; disk</h1>
  <span id="state"><span class="dot"></span>connecting</span>
  <span id="where"></span></header>
<main>
  <section><h2>Drive</h2><table id="ident"></table>
    <div class="row"><button onclick="post('/api/mount')">Mount</button>
      <button onclick="post('/api/unmount')">Unmount</button>
      <button onclick="post('/api/progress')">Save progress</button></div></section>
  <section><h2>SMART</h2><table id="smart"></table></section>
  <section><h2>Faults</h2>
    <label>write error rate <b id="we">0</b>%</label>
    <input id="weR" type="range" min="0" max="100" oninput="setFault('write_error_rate')">
    <label>read error rate <b id="re">0</b>%</label>
    <input id="reR" type="range" min="0" max="100" oninput="setFault('read_error_rate')">
    <label>latency <b id="lat">0</b> ms</label>
    <input id="latR" type="range" min="0" max="500" oninput="setFault('latency_ms')">
    <div class="row"><button onclick="toggleOffline()" id="off">Take offline</button>
      <button class="danger" onclick="accident()">Accident</button></div></section>
  <section><h2>Traffic</h2><table id="traffic"></table></section>
</main>
<script>
const $ = id => document.getElementById(id);
const rows = (into, obj) => {
  into.innerHTML = Object.entries(obj).map(([k, v]) =>
    `<tr><td>${k.replace(/_/g, ' ')}</td><td>${v}</td></tr>`).join('');
};
let status = {};
async function post(path, body) {
  const r = await fetch(path, {method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(body || {})});
  status = await r.json(); draw();
}
const pct = v => Math.round((v || 0) * 100);
function setFault(key) {
  const map = {write_error_rate: ['weR', 'we'], read_error_rate: ['reR', 're'],
               latency_ms: ['latR', 'lat']};
  const [slider, label] = map[key];
  const raw = +$(slider).value;
  $(label).textContent = raw;
  post('/api/faults', {[key]: key === 'latency_ms' ? raw : raw / 100});
}
function toggleOffline() { post('/api/faults', {offline: !status.faults?.offline}); }
function accident() { post('/api/accident'); }
function draw() {
  const f = status.faults || {}, s = status.smart || {};
  const live = status.mounted && !f.offline;
  $('state').innerHTML = `<span class="dot" style="background:${live ? '#7fd18b' : '#e06c75'}">` +
    `</span>${live ? 'online' : (status.mounted ? 'offline' : 'unmounted')}`;
  $('where').textContent = `${status.path} \u00b7 ${status.sectors} sectors \u00b7 ` +
    `${status.clients} client(s)`;
  rows($('ident'), {model: status.model || '-', path: status.path,
    bytes: status.bytes, sectors: status.sectors});
  rows($('smart'), s);
  rows($('traffic'), {served: status.served, errors: status.errors,
    last_error: status.last_error || '-', uptime_s: status.uptime_s});
  $('weR').value = pct(f.write_error_rate); $('we').textContent = pct(f.write_error_rate);
  $('reR').value = pct(f.read_error_rate); $('re').textContent = pct(f.read_error_rate);
  $('latR').value = f.latency_ms || 0; $('lat').textContent = f.latency_ms || 0;
  $('off').textContent = f.offline ? 'Bring online' : 'Take offline';
}
async function tick() {
  try {
    const r = await fetch('/api/status');
    status = await r.json();
    draw();
  } catch (e) {
    $('state').innerHTML = '<span class="dot" style="background:#e06c75"></span>no server';
  }
}
tick(); setInterval(tick, 1000);
</script></body></html>"""

CLIENTS = 0


def _num(text: str) -> int:
    """A number as a Scratch project prints it.

    Everything the machine sends is a number that went through a Scratch
    variable, and those print as doubles: a sector count can arrive as `4.0`
    and a big word as `3.2e+09`. Everything here is under 2^53, so reading it
    back through a float is exact -- and refusing it would turn a working drive
    into an error message.
    """
    return int(float(text))


def handle(volume: Volume, text: str) -> str:
    """One request, one reply. Anything longer is several lines joined."""
    parts = text.strip().split(maxsplit=2)
    if not parts:
        return "E empty"
    verb = parts[0].upper()
    if verb == "T":
        # microseconds: a Scratch number is a double and is exact in integers
        # only up to 2^53, which is 9e15 -- thirteen digits fit
        return f"T {time.time_ns() // 1000}"
    if verb == "I":
        return "I " + json.dumps(volume.identify())
    if verb == "S":
        return "S " + json.dumps(volume.smart.as_dict())
    if verb == "P":
        return "P " + json.dumps(volume.progress())
    if verb == "R":
        if len(parts) < 2:
            return "E read wants a sector number"
        try:
            lba = _num(parts[1])
            count = _num(parts[2]) if len(parts) > 2 else 1
        except ValueError:
            return "E read wants numbers"
        ok, payload = volume.read(lba, count)
        return f"D {payload}" if ok else f"E {payload}"
    if verb == "W":
        if len(parts) < 3:
            return "E write wants a sector and a payload"
        try:
            lba = _num(parts[1])
            blob = bytes.fromhex(parts[2])
        except ValueError:
            return "E write wants a number and hex"
        ok, why = volume.write(lba, blob)
        return f"W {why}" if not ok else "W ok"
    if verb == "WW":
        # what the machine actually has: a list of signed 32-bit words. The
        # bytes go out little-endian, which is what the guest's ATA bus does.
        if len(parts) < 3:
            return "E write wants a sector and words"
        head = parts[2].split()
        try:
            lba = _num(parts[1])
            # `int(float(w))`: a Scratch number arrives as whatever the VM
            # printed, and a big one can print as `3.2e+09`. Everything here is
            # below 2^53, so the round trip through a double is exact.
            words = [int(float(word)) & 0xFFFFFFFF for word in head]
        except ValueError:
            return "E write wants a number and decimal words"
        blob = b"".join(word.to_bytes(4, "little") for word in words)
        ok, why = volume.write(lba, blob)
        return f"W {why}" if not ok else "W ok"
    if verb == "H":
        # the machine served this read from the image it staged at build time;
        # the traffic is real, so the counters and the panel see it
        if len(parts) < 2:
            return "E hint wants a sector number"
        try:
            lba = _num(parts[1])
            count = _num(parts[2]) if len(parts) > 2 else 1
        except ValueError:
            return "E hint wants numbers"
        volume.hint(lba, count)
        return "H ok"
    return f"E unknown request {verb!r}"


# ---- the two servers ------------------------------------------------------

def start_http(volume: Volume, port: int) -> ThreadingHTTPServer:
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args) -> None:      # quiet
            pass

        def reply(self, code: int, body: dict) -> None:
            blob = json.dumps(body).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(blob)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(blob)

        def do_GET(self) -> None:
            if self.path in ("/", "/panel"):
                blob = PANEL.encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(blob)))
                self.end_headers()
                self.wfile.write(blob)
            elif self.path.startswith("/api/status"):
                self.reply(200, volume.status())
            elif self.path.startswith("/api/smart"):
                self.reply(200, volume.smart.as_dict())
            elif self.path.startswith("/api/identify"):
                self.reply(200, volume.identify())
            elif self.path.startswith("/api/progress"):
                self.reply(200, volume.progress())
            else:
                self.reply(404, {"error": "no such thing", "path": self.path})

        def do_POST(self) -> None:
            length = int(self.headers.get("Content-Length") or 0)
            body = {}
            if length:
                try:
                    body = json.loads(self.rfile.read(length) or b"{}")
                except ValueError:
                    self.reply(400, {"error": "body is not JSON"})
                    return
            if self.path.startswith("/api/faults"):
                self.reply(200, volume.set_faults(**body))
            elif self.path.startswith("/api/progress"):
                self.reply(200, {**volume.status(), **volume.progress()})
            elif self.path.startswith("/api/accident"):
                volume.accident()
                self.reply(200, volume.status())
            elif self.path.startswith("/api/mount"):
                if "path" in body:
                    volume.unmount()
                    volume.path = Path(body["path"])
                    volume.metafile = volume.path.with_suffix(
                        volume.path.suffix + ".smart.json")
                volume.mount()
                self.reply(200, volume.status())
            elif self.path.startswith("/api/unmount"):
                volume.unmount()
                self.reply(200, volume.status())
            else:
                self.reply(404, {"error": "no such thing", "path": self.path})

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


async def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("image", nargs="?", default=str(Path("build/i386/disk.img")))
    ap.add_argument("--port", type=int, default=WS_PORT, help="the machine's port")
    ap.add_argument("--http", type=int, default=HTTP_PORT, help="the panel's port")
    ap.add_argument("--no-http", action="store_true")
    args = ap.parse_args(argv)

    volume = Volume(Path(args.image))
    if not args.no_http:
        start_http(volume, args.http)
        print(f"[disk] panel:    http://127.0.0.1:{args.http}/", flush=True)

    async def echo(sock) -> None:
        global CLIENTS
        CLIENTS += 1
        print(f"[disk] client connected ({CLIENTS} attached)", flush=True)
        try:
            async for message in sock:
                reply = handle(volume, str(message))
                # one request can answer with several lines; the extension
                # delivers one message, so they are joined
                await sock.send(reply)
        except websockets.ConnectionClosed:
            pass
        finally:
            CLIENTS -= 1
            print(f"[disk] client gone ({CLIENTS} attached)", flush=True)

    print(f"[disk] {volume.path} rows={volume.sectors} sectors, "
          f"ws://127.0.0.1:{args.port}", flush=True)
    async with websockets.serve(echo, "127.0.0.1", args.port):
        await asyncio.Future()
    return 0


def ctl(argv: list[str]) -> int:
    """A command line in front of the control API, so the panel is optional."""
    import urllib.request

    ap = argparse.ArgumentParser(description="talk to a running disk")
    ap.add_argument("--http", type=int, default=HTTP_PORT)
    ap.add_argument("command", choices=("status", "smart", "identify", "progress",
                                        "accident", "mount", "unmount", "offline",
                                        "online", "fail-writes", "slow", "clear"))
    ap.add_argument("value", nargs="*")
    args = ap.parse_args(argv)
    base = f"http://127.0.0.1:{args.http}"

    def post(path: str, body: dict) -> dict:
        req = urllib.request.Request(base + path, data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"})
        return json.loads(urllib.request.urlopen(req, timeout=5).read())

    def get(path: str) -> dict:
        return json.loads(urllib.request.urlopen(base + path, timeout=5).read())

    if args.command == "status":
        print(json.dumps(get("/api/status"), indent=1))
    elif args.command == "smart":
        print(json.dumps(get("/api/smart"), indent=1))
    elif args.command == "identify":
        print(json.dumps(get("/api/identify"), indent=1))
    elif args.command == "progress":
        print(json.dumps(get("/api/progress"), indent=1))
    elif args.command == "accident":
        print(json.dumps(post("/api/accident", {}), indent=1))
    elif args.command == "mount":
        print(json.dumps(post("/api/mount", {"path": args.value[0]} if args.value else {}), indent=1))
    elif args.command == "unmount":
        print(json.dumps(post("/api/unmount", {}), indent=1))
    elif args.command == "offline":
        print(json.dumps(post("/api/faults", {"offline": True}), indent=1))
    elif args.command == "online":
        print(json.dumps(post("/api/faults", {"offline": False,
                                              "write_error_rate": 0.0,
                                              "latency_ms": 0.0}), indent=1))
    elif args.command == "fail-writes":
        rate = float(args.value[0]) if args.value else 0.05
        print(json.dumps(post("/api/faults", {"write_error_rate": rate}), indent=1))
    elif args.command == "slow":
        ms = float(args.value[0]) if args.value else 20.0
        print(json.dumps(post("/api/faults", {"latency_ms": ms}), indent=1))
    elif args.command == "clear":
        print(json.dumps(post("/api/faults", {"offline": False, "accident": False,
                                              "write_error_rate": 0.0,
                                              "read_error_rate": 0.0,
                                              "latency_ms": 0.0}), indent=1))
    return 0


if __name__ == "__main__":
    if "--ctl" in sys.argv:
        i = sys.argv.index("--ctl")
        sys.exit(ctl(sys.argv[i + 1:]))
    sys.exit(asyncio.run(main(sys.argv[1:])))
