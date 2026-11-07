#!/usr/bin/env python3
"""A network for the i386 machine: real frames, in a host process.

    python3 tools/i386_net.py                        # NAT: no privileges
    sudo python3 tools/i386_net.py --mode bridge --iface en10
    python3 tools/i386_net.py --ctl status           # ask it how it is

The machine has a NIC (an RTL8139 on the PCI bus). What it does not have is a
cable. So the wire is here, on the host, and the guest's frames arrive over the
same WebSocket the disk uses -- one frame, one message, hex either way:

    T <hex>     the guest transmitted a frame
    R <hex>     here is a frame for the guest's receiver
    X <text>    something the machine should know that is not a frame

Two ways to be that wire, and they answer different questions:

  * **NAT** (the default, and what works on a Mac with no cable in `en0`) is a
    router in this process. The guest lives at 10.0.2.15, this process is
    10.0.2.2 and its DNS is 10.0.2.3. ARP, DHCP and ICMP are answered here;
    TCP and UDP are *terminated* here and re-made with host sockets, the way
    slirp does it. The guest reaches the real internet through whatever the
    host is using, and needs no privileges -- because the packets that leave
    this machine are the host's own, from the host's own address.

  * **BRIDGE** is the real thing: `--mode bridge --iface en0` puts that
    interface in promiscuous mode through BPF and hands the guest the actual
    segment. The guest's MAC goes out on the wire, the home router's DHCP
    answers it, and the machine has a real address -- 192.168.0.x, pingable
    from the rest of the house. It needs root (`/dev/bpf*` is root-only) and a
    *wired* interface: a Wi-Fi AP drops frames from a MAC that never
    authenticated, so bridging `en1` looks like it works and then half of the
    traffic vanishes.

Both modes speak the same protocol to the machine, so the project does not know
which one it is talking to.
"""

from __future__ import annotations

import argparse
import asyncio
import errno
import fcntl
import json
import os
import random
import select
import socket
import struct
import sys
import threading
import time
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

WS_PORT = 65450
HTTP_PORT = 65451

#: the guest's address on the private wire, and ours
GUEST_MAC = bytes.fromhex("525400123456")
HOST_MAC = bytes.fromhex("525400000001")
GUEST_IP = "10.0.2.15"
HOST_IP = "10.0.2.2"
DNS_IP = "10.0.2.3"
NETMASK = "255.255.255.0"
BROADCAST_IP = "10.0.2.255"
PREFIX = 24

ETH_IPV4 = 0x0800
ETH_ARP = 0x0806

IP_ICMP = 1
IP_TCP = 6
IP_UDP = 17

TCP_FIN = 0x01
TCP_SYN = 0x02
TCP_RST = 0x04
TCP_PSH = 0x08
TCP_ACK = 0x10

TFTP_PORT = 69
DHCP_SERVER_PORT = 67
DHCP_CLIENT_PORT = 68

MASK32 = 0xFFFFFFFF


# ---------------------------------------------------------------------------
# the packet layer -- bytes in, bytes out, no objects in between
# ---------------------------------------------------------------------------

def ip_bytes(text: str) -> bytes:
    return bytes(int(part) for part in text.split("."))


def ip_text(raw: bytes) -> str:
    return ".".join(str(b) for b in raw)


def mac_text(raw: bytes) -> str:
    return ":".join(f"{b:02x}" for b in raw)


def checksum(data: bytes) -> int:
    """The internet checksum: one's complement of the one's complement sum."""
    if len(data) % 2:
        data += b"\x00"
    total = 0
    for i in range(0, len(data), 2):
        total += (data[i] << 8) | data[i + 1]
    while total >> 16:
        total = (total & 0xFFFF) + (total >> 16)
    return (~total) & 0xFFFF


def ethernet(dst: bytes, src: bytes, ethertype: int, payload: bytes) -> bytes:
    return dst + src + struct.pack(">H", ethertype) + payload


def ipv4(src: str, dst: str, proto: int, payload: bytes, ident: int = 0,
         ttl: int = 64) -> bytes:
    if ident == 0:
        ident = random.randint(1, 0xFFFF)
    header = struct.pack(">BBHHHBBH4s4s", 0x45, 0, 20 + len(payload), ident, 0,
                         ttl, proto, 0, ip_bytes(src), ip_bytes(dst))
    return header[:10] + struct.pack(">H", checksum(header)) + header[12:] + payload


def _pseudo(src: str, dst: str, proto: int, length: int) -> bytes:
    return ip_bytes(src) + ip_bytes(dst) + struct.pack(">BBH", 0, proto, length)


def udp(src: str, dst: str, sport: int, dport: int, payload: bytes) -> bytes:
    length = 8 + len(payload)
    header = struct.pack(">HHHH", sport, dport, length, 0)
    summed = checksum(_pseudo(src, dst, IP_UDP, length) + header + payload)
    # a computed zero means "no checksum", which is legal for UDP and is what
    # every sender writes when the sum happens to come out zero
    return struct.pack(">HHHH", sport, dport, length, summed or 0xFFFF) + payload


def tcp(src: str, dst: str, sport: int, dport: int, seq: int, ack: int,
        flags: int, payload: bytes = b"", window: int = 32768) -> bytes:
    offset = 5 << 4
    header = struct.pack(">HHIIBBHHH", sport, dport, seq & MASK32, ack & MASK32,
                         offset, flags, window, 0, 0)
    summed = checksum(_pseudo(src, dst, IP_TCP, len(header) + len(payload))
                      + header + payload)
    return struct.pack(">HHIIBBHHH", sport, dport, seq & MASK32, ack & MASK32,
                       offset, flags, window, summed, 0) + payload


@dataclass
class Segment:
    """The parts of a frame this process cares about, or None where absent."""

    dst: bytes = b""
    src: bytes = b""
    ethertype: int = 0
    src_ip: str = ""
    dst_ip: str = ""
    proto: int = 0
    payload: bytes = b""
    sport: int = 0
    dport: int = 0
    seq: int = 0
    ack: int = 0
    flags: int = 0
    icmp_type: int = -1


def dissect(frame: bytes) -> Segment | None:
    """Read a frame the way a host reads one: no assumptions past the lengths."""
    if len(frame) < 14:
        return None
    seg = Segment(dst=frame[0:6], src=frame[6:12],
                  ethertype=struct.unpack(">H", frame[12:14])[0])
    if seg.ethertype != ETH_IPV4 or len(frame) < 34:
        return seg
    ip = frame[14:]
    if ip[0] >> 4 != 4:
        return None
    ihl = (ip[0] & 0x0F) * 4
    total = struct.unpack(">H", ip[2:4])[0]
    seg.proto = ip[9]
    seg.src_ip = ip_text(ip[12:16])
    seg.dst_ip = ip_text(ip[16:20])
    seg.payload = ip[ihl:total] if total >= ihl else ip[ihl:]
    if seg.proto == IP_TCP and len(seg.payload) >= 20:
        (seg.sport, seg.dport, seg.seq, seg.ack, off, seg.flags) = \
            struct.unpack(">HHIIBB", seg.payload[:14])
        seg.payload = seg.payload[((off >> 4) * 4):]
    elif seg.proto == IP_UDP and len(seg.payload) >= 8:
        seg.sport, seg.dport = struct.unpack(">HH", seg.payload[:4])
        seg.payload = seg.payload[8:]
    elif seg.proto == IP_ICMP and len(seg.payload) >= 1:
        seg.icmp_type = seg.payload[0]
    return seg


# ---------------------------------------------------------------------------
# DHCP -- including the two options PXE lives on
# ---------------------------------------------------------------------------

def _dhcp_options(blob: bytes) -> dict[int, bytes]:
    out: dict[int, bytes] = {}
    i = 240                                   # past the fixed BOOTP part
    while i < len(blob):
        code = blob[i]
        if code == 255:
            break
        if code == 0:
            i += 1
            continue
        if i + 1 >= len(blob):
            break
        length = blob[i + 1]
        out[code] = blob[i + 2:i + 2 + length]
        i += 2 + length
    return out


def _dhcp_option(code: int, value: bytes) -> bytes:
    return bytes([code, len(value)]) + value


@dataclass
class Lease:
    """What the router told the guest it had, and what it asked to boot."""

    mac: str = ""
    ip: str = ""
    hostname: str = ""
    boot_file: str = ""
    vendor: str = ""
    at: float = 0.0


class Dhcp:
    """DISCOVER, REQUEST, and the PXE vendor options in between."""

    def __init__(self) -> None:
        self.leases: dict[str, Lease] = {}
        self.offers = 0
        self.acks = 0
        self.pxe_requests = 0
        self.boot_file = "boot.nbp"       # what a PXE client is told to fetch
        self.next_server = HOST_IP
        self.enabled = True

    def handle(self, seg: Segment) -> bytes | None:
        """One datagram to port 67 in, one answer frame out."""
        if seg.proto != IP_UDP or seg.dport != DHCP_SERVER_PORT:
            return None
        blob = seg.payload
        if len(blob) < 240 or blob[0] != 1:          # BOOTREQUEST
            return None
        if blob[236:240] != bytes([0x63, 0x82, 0x53, 0x63]):
            return None

        options = _dhcp_options(blob)
        kind = options.get(53, b"\x00")[0]
        vendor = options.get(60, b"").decode("latin-1", "replace")
        hostname = options.get(12, b"").decode("latin-1", "replace")
        mac = mac_text(blob[28:34])
        wants_pxe = "PXEClient" in vendor
        if wants_pxe:
            self.pxe_requests += 1

        if kind == 1:                                  # DISCOVER
            self.offers += 1
            answer = 2                                 # OFFER
        elif kind == 3:                                # REQUEST
            self.acks += 1
            answer = 5                                 # ACK
        else:
            return None                                # RELEASE and friends

        lease = self.leases.get(mac)
        if lease is None:
            lease = Lease(mac=mac, ip=GUEST_IP)
            self.leases[mac] = lease
        lease.hostname = hostname or lease.hostname
        lease.vendor = vendor
        lease.at = time.time()
        if wants_pxe:
            lease.boot_file = self.boot_file

        reply = bytearray(236)                         # BOOTP fixed part
        reply[0] = 2                                   # BOOTREPLY
        reply[1] = 1                                   # ethernet
        reply[2] = 6
        reply[4:8] = blob[4:8]                         # transaction id
        reply[10:12] = b"\x80\x00"                     # broadcast, as asked
        reply[16:20] = ip_bytes(lease.ip)
        reply[20:24] = ip_bytes(HOST_IP)
        reply[28:34] = blob[28:34]                     # chaddr
        reply += bytes([0x63, 0x82, 0x53, 0x63])
        reply += _dhcp_option(53, bytes([answer]))
        reply += _dhcp_option(54, ip_bytes(HOST_IP))   # server identifier
        reply += _dhcp_option(1, ip_bytes(NETMASK))
        reply += _dhcp_option(3, ip_bytes(HOST_IP))
        reply += _dhcp_option(6, ip_bytes(DNS_IP))
        reply += _dhcp_option(51, struct.pack(">I", 86400))
        if wants_pxe:
            # options 66 and 67 are the whole of "PXE": a server to fetch from
            # and a file to fetch. 43 says this is a boot server, not just a
            # router that happens to know the word.
            reply += _dhcp_option(66, self.next_server.encode())
            reply += _dhcp_option(67, self.boot_file.encode())
            reply += _dhcp_option(43, bytes([6, 1, 8]))   # discovery control: boot
            reply += _dhcp_option(60, b"PXEClient")
        reply += bytes([255])

        datagram = udp(HOST_IP, BROADCAST_IP, DHCP_SERVER_PORT, DHCP_CLIENT_PORT,
                       bytes(reply))
        return ethernet(b"\xff" * 6, HOST_MAC, ETH_IPV4,
                        ipv4(HOST_IP, BROADCAST_IP, IP_UDP, datagram))


# ---------------------------------------------------------------------------
# TFTP -- the file transfer PXE boots with, and the reason it is UDP
# ---------------------------------------------------------------------------

OP_RRQ, OP_WRQ, OP_DATA, OP_ACK, OP_ERROR = 1, 2, 3, 4, 5


@dataclass
class TftpSession:
    guest: tuple[str, int] = ("", 0)
    name: str = ""
    blob: bytes = b""
    block: int = 0
    sent_at: float = 0.0
    tries: int = 0
    done: bool = False


class Tftp:
    """A read-only TFTP server, one file at a time per client port."""

    BLOCK = 512

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.sessions: dict[tuple[str, int], TftpSession] = {}
        self.reads = 0
        self.blocks = 0
        self.errors = 0
        self.last = ""

    def file_for(self, name: str) -> Path | None:
        """A name from the wire, kept inside the directory it was given."""
        clean = name.split("\x00")[0].strip().lstrip("/")
        if not clean or ".." in clean.split("/"):
            return None
        path = (self.root / clean).resolve()
        try:
            path.relative_to(self.root.resolve())
        except ValueError:
            return None
        return path if path.is_file() else None

    def _error(self, guest: tuple[str, int], code: int, why: str) -> bytes:
        self.errors += 1
        self.last = why
        return struct.pack(">HH", OP_ERROR, code) + why.encode() + b"\x00"

    def _data(self, session: TftpSession) -> bytes:
        start = session.block * self.BLOCK
        chunk = session.blob[start:start + self.BLOCK]
        session.sent_at = time.time()
        return struct.pack(">HH", OP_DATA, session.block + 1) + chunk

    def request(self, guest: tuple[str, int], payload: bytes) -> bytes | None:
        """A datagram to port 69. Returns what to send back, or None."""
        if len(payload) < 4:
            return None
        opcode = struct.unpack(">H", payload[:2])[0]
        if opcode == OP_RRQ:
            name = payload[2:].split(b"\x00")[0].decode("latin-1", "replace")
            path = self.file_for(name)
            self.reads += 1
            if path is None:
                return self._error(guest, 1, f"no such file {name}")
            session = TftpSession(guest=guest, name=name, blob=path.read_bytes())
            self.sessions[guest] = session
            self.last = f"{name} ({len(session.blob)} bytes)"
            return self._data(session)
        if opcode == OP_WRQ:
            return self._error(guest, 2, "read only")
        if opcode == OP_ACK:
            session = self.sessions.get(guest)
            if session is None:
                return None
            block = struct.unpack(">H", payload[2:4])[0]
            if block != session.block + 1:
                return None                       # an old ACK: already moved on
            session.block += 1
            self.blocks += 1
            if session.block * self.BLOCK >= len(session.blob):
                session.done = True
                del self.sessions[guest]
                return None
            return self._data(session)
        return None

    def tick(self, now: float) -> list[tuple[tuple[str, int], bytes]]:
        """Retransmit a block nobody acknowledged, the way TFTP must."""
        out = []
        for guest, session in list(self.sessions.items()):
            if now - session.sent_at < 1.0:
                continue
            session.tries += 1
            if session.tries > 5:
                self.errors += 1
                self.last = f"{session.name}: gave up"
                out.append((guest, self._error(guest, 0, "timeout")))
                del self.sessions[guest]
                continue
            out.append((guest, self._data(session)))
        return out


# ---------------------------------------------------------------------------
# NAT -- the router in this process
# ---------------------------------------------------------------------------

class TcpFlow:
    """One connection, terminated here and re-made from a host socket."""

    def __init__(self, key: tuple[str, int, str, int], isn: int) -> None:
        self.key = key
        self.guest_ip, self.guest_port, self.peer_ip, self.peer_port = key
        self.ours = isn
        self.theirs = 0                    # next sequence number we expect
        self.state = "syn-recv"
        self.sock: socket.socket | None = None
        self.opened_at = time.time()
        self.used_at = self.opened_at
        self.sent = 0
        self.got = 0
        self.fin_sent = False
        self.fin_got = False
        self.opened = False                # the handshake finished
        self.out = b""                     # bytes the guest sent that the
                                           # peer has not taken yet

    def segment(self, flags: int, payload: bytes = b"") -> bytes:
        """One segment for the guest, already wrapped the way its NIC wants it."""
        seq = self.ours
        if flags & (TCP_SYN | TCP_FIN) or payload:
            self.ours = (self.ours + len(payload) + (1 if flags & (TCP_SYN | TCP_FIN) else 0)) & MASK32
        inner = tcp(self.peer_ip, self.guest_ip, self.peer_port, self.guest_port,
                    seq, self.theirs, flags, payload)
        return ethernet(GUEST_MAC, HOST_MAC, ETH_IPV4,
                        ipv4(self.peer_ip, self.guest_ip, IP_TCP, inner))


class Nat:
    """The guest's wire, when the wire is a process.

    Everything the guest sends is looked at here: ARP and DHCP and ICMP are
    answered as the router, TCP and UDP are carried out of this process through
    ordinary host sockets. Nothing here needs a privilege -- the packets that
    leave the Mac are the Mac's own.
    """

    def __init__(self, tftp_root: Path) -> None:
        self.arp_table: dict[str, str] = {GUEST_IP: mac_text(GUEST_MAC),
                                          HOST_IP: mac_text(HOST_MAC),
                                          DNS_IP: mac_text(HOST_MAC)}
        self.dhcp = Dhcp()
        self.tftp = Tftp(tftp_root)
        self.udp_flows: dict[tuple[int, str, int], socket.socket] = {}
        self.udp_keys: dict[int, tuple[int, str, int]] = {}
        self.tcp_flows: dict[tuple[str, int, str, int], TcpFlow] = {}
        self.dns_upstream = self._resolver()
        self.tx = 0
        self.rx = 0
        self.arp_in = 0
        self.arp_out = 0
        self.pings = 0
        self.udp_out = 0
        self.udp_in = 0
        self.tcp_opened = 0
        self.tcp_closed = 0
        self.dropped = 0
        self.throttle: float = 0.0         # seconds of delay per frame, for fault tests
        self.blackhole = False             # drop everything: a cable pulled out

    @staticmethod
    def _resolver() -> str:
        """The nameserver the host itself would use."""
        try:
            for line in Path("/etc/resolv.conf").read_text().splitlines():
                parts = line.split()
                if len(parts) >= 2 and parts[0] == "nameserver":
                    return parts[1]
        except OSError:
            pass
        return "192.168.0.1"

    # ---- the wire ----------------------------------------------------
    def from_guest(self, frame: bytes) -> list[bytes]:
        self.tx += 1
        if self.blackhole:
            self.dropped += 1
            return []
        seg = dissect(frame)
        if seg is None:
            self.dropped += 1
            return []
        if seg.ethertype == ETH_ARP:
            return self._arp(frame, seg)
        if seg.ethertype != ETH_IPV4:
            self.dropped += 1
            return []
        self.arp_table[seg.src_ip] = mac_text(seg.src)
        if seg.proto == IP_ICMP:
            return self._icmp(seg)
        if seg.proto == IP_UDP:
            return self._udp(seg)
        if seg.proto == IP_TCP:
            return self._tcp(seg)
        return []

    def _arp(self, frame: bytes, seg: Segment) -> list[bytes]:
        if len(frame) < 42:
            return []
        htype, ptype, hlen, plen, oper = struct.unpack(">HHBBH", frame[14:22])
        if (htype, ptype, hlen, plen, oper) != (1, ETH_IPV4, 6, 4, 1):
            return []
        self.arp_in += 1
        want = ip_text(frame[38:42])
        if want not in (HOST_IP, DNS_IP, GUEST_IP):
            # a bridge would carry this to the real segment; a router answers
            # for itself and lets the guest's stack time out on the rest
            return []
        sender_mac = frame[22:28]
        body = struct.pack(">HHBBH", 1, ETH_IPV4, 6, 4, 2)
        body += HOST_MAC + ip_bytes(want) + sender_mac + frame[28:32]
        self.arp_out += 1
        return [ethernet(sender_mac, HOST_MAC, ETH_ARP, body)]

    def _icmp(self, seg: Segment) -> list[bytes]:
        if seg.icmp_type != 8 or len(seg.payload) < 8:
            return []
        self.pings += 1
        reply = bytearray(seg.payload)
        reply[0] = 0
        reply[2:4] = b"\x00\x00"
        reply[2:4] = struct.pack(">H", checksum(bytes(reply)))
        payload = ipv4(seg.dst_ip, seg.src_ip, IP_ICMP, bytes(reply))
        return [ethernet(GUEST_MAC, HOST_MAC, ETH_IPV4, payload)]

    # ---- UDP ---------------------------------------------------------
    def _udp(self, seg: Segment) -> list[bytes]:
        if seg.dport == DHCP_SERVER_PORT:
            answer = self.dhcp.handle(seg)
            return [answer] if answer else []
        if seg.dport == TFTP_PORT:
            answer = self.tftp.request((seg.src_ip, seg.sport), seg.payload)
            return [self._to_guest(seg.src_ip, seg.sport, HOST_IP, TFTP_PORT, answer)] \
                if answer else []
        if seg.dst_ip in (HOST_IP, DNS_IP) or seg.dport == 53:
            return self._udp_to_internet(seg)
        return self._udp_to_internet(seg)

    def _udp_to_internet(self, seg: Segment) -> list[bytes]:
        if not seg.payload and seg.sport == 0:
            return []
        key = (seg.sport, seg.dst_ip, seg.dport)
        sock = self.udp_flows.get(key)
        target = (self.dns_upstream if seg.dport == 53 else seg.dst_ip, seg.dport)
        if sock is None:
            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                sock.setblocking(False)
                sock.bind(("0.0.0.0", 0))
                sock.sendto(seg.payload, target)
            except OSError:
                if sock is not None:
                    sock.close()
                self.dropped += 1
                return []
            self.udp_flows[key] = sock
            self.udp_keys[sock.fileno()] = key
        else:
            try:
                sock.sendto(seg.payload, target)
            except OSError:
                self.dropped += 1
                return []
        self.udp_out += 1
        return []

    def _to_guest(self, guest_ip: str, guest_port: int, src_ip: str,
                  src_port: int, payload: bytes) -> bytes:
        datagram = udp(src_ip, guest_ip, src_port, guest_port, payload)
        return ethernet(GUEST_MAC, HOST_MAC, ETH_IPV4,
                        ipv4(src_ip, guest_ip, IP_UDP, datagram))

    def _udp_replies(self, now: float) -> list[bytes]:
        out: list[bytes] = []
        for fileno, key in list(self.udp_keys.items()):
            sock = self.udp_flows.get(key)
            if sock is None:
                continue
            gport, dst_ip, dport = key
            for _ in range(8):
                # a socket the wire owns can be blocking by accident -- one
                # handed in by a test, say -- and the wire must never stall on
                # it, so ask which sockets have something before reading
                try:
                    ready, _, _ = select.select([sock], [], [], 0)
                except OSError:
                    break
                if not ready:
                    break
                try:
                    data, peer = sock.recvfrom(65535)
                except OSError:
                    break
                # the source the guest asked for is the source it is told
                # about: a DNS query to 10.0.2.3 comes back from 10.0.2.3
                src_ip = DNS_IP if dst_ip == DNS_IP else (peer[0] if not dst_ip else dst_ip)
                out.append(self._to_guest(GUEST_IP, gport, src_ip, dport, data))
                self.udp_in += 1
        return out

    # ---- TCP ---------------------------------------------------------
    def _reset(self, seg: Segment, ack: int) -> bytes:
        """Nothing is listening: say so the way TCP says so."""
        inner = tcp(seg.dst_ip, seg.src_ip, seg.dport, seg.sport, 0, ack,
                    TCP_RST | TCP_ACK)
        return ethernet(GUEST_MAC, HOST_MAC, ETH_IPV4,
                        ipv4(seg.dst_ip, seg.src_ip, IP_TCP, inner))

    def _tcp(self, seg: Segment) -> list[bytes]:
        key = (seg.src_ip, seg.sport, seg.dst_ip, seg.dport)
        flow = self.tcp_flows.get(key)

        if seg.flags & TCP_RST:
            if flow is not None:
                self._close(flow, tell_guest=False)
            return []

        if seg.flags & TCP_SYN and flow is None:
            flow = TcpFlow(key, random.randint(1, MASK32 >> 1))
            flow.theirs = (seg.seq + 1) & MASK32
            self.tcp_flows[key] = flow
            try:
                flow.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                flow.sock.setblocking(False)
                flow.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                # a refusal can come back before connect_ex even returns, and a
                # guest that waits a poll interval to learn "nothing there" has
                # waited too long: answer it now
                err = flow.sock.connect_ex((seg.dst_ip, seg.dport))
                if err not in (0, errno.EINPROGRESS, errno.EWOULDBLOCK, errno.EALREADY):
                    self._close(flow, tell_guest=False)
                    return [self._reset(seg, (seg.seq + 1) & MASK32)]
            except OSError:
                self._close(flow, tell_guest=False)
                return [self._reset(seg, (seg.seq + 1) & MASK32)]
            flow.state = "syn-recv"
            self._watch(flow)
            return [flow.segment(TCP_SYN | TCP_ACK)]

        if flow is None:
            return [self._reset(seg, seg.seq)]

        flow.used_at = time.time()
        if seg.flags & TCP_ACK and flow.state == "syn-recv":
            flow.state = "open"
            flow.opened = True
            self.tcp_opened += 1

        if seg.payload:
            if seg.seq == flow.theirs:
                flow.theirs = (flow.theirs + len(seg.payload)) & MASK32
                flow.got += len(seg.payload)
                self._write(flow, seg.payload)
            # a retransmission is not new data: acknowledge where we are, so
            # the guest stops waiting instead of the connection stalling
            return [flow.segment(TCP_ACK)]

        if seg.flags & TCP_FIN:
            flow.theirs = (flow.theirs + 1) & MASK32
            flow.fin_got = True
            self._shutdown(flow)
            if flow.fin_sent:
                self._close(flow, tell_guest=False)
                return [flow.segment(TCP_ACK)]
            flow.state = "closing"
            return [flow.segment(TCP_FIN | TCP_ACK)]

        return []

    def _watch(self, flow: TcpFlow) -> None:
        if flow.sock is not None:
            try:
                flow.sock.setblocking(False)
            except OSError:
                pass

    def _write(self, flow: TcpFlow, data: bytes) -> None:
        """Queue what the guest sent, then give the peer as much as it takes.

        A non-blocking socket can take half a segment and refuse the rest, and
        dropping the rest would corrupt the stream rather than slow it down --
        so what is left over waits for the next poll.
        """
        if flow.sock is None:
            return
        flow.out += data
        self._flush(flow)

    def _flush(self, flow: TcpFlow) -> None:
        while flow.out and flow.sock is not None:
            try:
                taken = flow.sock.send(flow.out)
            except BlockingIOError:
                return
            except OSError as exc:
                if exc.errno in (errno.EAGAIN, errno.ENOTCONN):
                    return                # the connect is still in flight
                self._close(flow, tell_guest=True)
                return
            if taken <= 0:
                return
            flow.out = flow.out[taken:]

    def _shutdown(self, flow: TcpFlow) -> None:
        if flow.sock is None:
            return
        try:
            flow.sock.shutdown(socket.SHUT_WR)
        except OSError:
            pass

    def _close(self, flow: TcpFlow, tell_guest: bool) -> bytes | None:
        self.tcp_flows.pop(flow.key, None)
        if flow.opened:
            self.tcp_closed += 1
        if flow.sock is not None:
            try:
                flow.sock.close()
            except OSError:
                pass
            flow.sock = None
        if tell_guest:
            return flow.segment(TCP_RST | TCP_ACK)
        return None

    def _tcp_progress(self, now: float) -> list[bytes]:
        out: list[bytes] = []
        for flow in list(self.tcp_flows.values()):
            sock = flow.sock
            if sock is None:
                continue
            self._flush(flow)
            connected = flow.state == "open"
            if not connected:
                # the connect completes, or it does not; there is no half way
                err = sock.getsockopt(socket.SOL_SOCKET, socket.SO_ERROR)
                if err:
                    self._close(flow, tell_guest=False)
                    inner = tcp(flow.peer_ip, flow.guest_ip, flow.peer_port,
                                flow.guest_port, 0, flow.theirs, TCP_RST | TCP_ACK)
                    out.append(ethernet(GUEST_MAC, HOST_MAC, ETH_IPV4,
                                        ipv4(flow.peer_ip, flow.guest_ip,
                                             IP_TCP, inner)))
                    continue
            for _ in range(4):
                try:
                    ready, _, _ = select.select([sock], [], [], 0)
                except OSError:
                    break
                if not ready:
                    break
                try:
                    data = sock.recv(1460)
                except OSError:
                    data = b""
                if data == b"":
                    if not flow.fin_sent:
                        flow.fin_sent = True
                        out.append(flow.segment(TCP_FIN | TCP_ACK))
                    break
                if not connected and flow.state == "syn-recv":
                    # data before we saw the ACK still proves the connection is
                    # up: say so with an ACK the guest is already expecting
                    flow.state = "open"
                    flow.opened = True
                    self.tcp_opened += 1
                out.append(flow.segment(TCP_ACK | TCP_PSH, data))
                flow.sent += len(data)
            if now - flow.used_at > 120.0 and flow.state == "open":
                rst = self._close(flow, tell_guest=True)
                if rst:
                    out.append(rst)
        return out

    # ---- the poll ----------------------------------------------------
    def poll(self, now: float | None = None) -> list[bytes]:
        """Whatever the host sockets have to say, as frames for the guest."""
        now = time.time() if now is None else now
        if self.blackhole:
            return []
        out = self._udp_replies(now)
        for guest, payload in self.tftp.tick(now):
            out.append(self._to_guest(guest[0], guest[1], HOST_IP, TFTP_PORT, payload))
        out.extend(self._tcp_progress(now))
        self.rx += len(out)
        return out

    # ---- the panel ---------------------------------------------------
    def status(self) -> dict:
        return {
            "mode": "nat",
            "guest": {"ip": GUEST_IP, "mac": mac_text(GUEST_MAC),
                      "gateway": HOST_IP, "dns": DNS_IP, "netmask": NETMASK},
            "counters": {
                "tx": self.tx, "rx": self.rx, "arp_in": self.arp_in,
                "arp_out": self.arp_out, "pings": self.pings,
                "udp_out": self.udp_out, "udp_in": self.udp_in,
                "tcp_opened": self.tcp_opened, "tcp_closed": self.tcp_closed,
                "dropped": self.dropped,
            },
            "arp": dict(self.arp_table),
            "leases": [lease.__dict__ for lease in self.dhcp.leases.values()],
            "dhcp": {"offers": self.dhcp.offers, "acks": self.dhcp.acks,
                     "pxe_requests": self.dhcp.pxe_requests,
                     "boot_file": self.dhcp.boot_file,
                     "next_server": self.dhcp.next_server,
                     "enabled": self.dhcp.enabled},
            "tftp": {"root": str(self.tftp.root),
                     "reads": self.tftp.reads, "blocks": self.tftp.blocks,
                     "errors": self.tftp.errors, "last": self.tftp.last,
                     "files": self._tftp_files()},
            "tcp": [{"guest_port": key[1], "peer": f"{key[2]}:{key[3]}",
                     "state": flow.state, "sent": flow.sent, "got": flow.got}
                    for key, flow in list(self.tcp_flows.items())[:32]],
            "udp": [{"guest_port": key[0], "peer": f"{key[1]}:{key[2]}"}
                    for key in list(self.udp_flows)[:32]],
            "faults": {"blackhole": self.blackhole, "throttle": self.throttle},
            "dns_upstream": self.dns_upstream,
        }

    def _tftp_files(self) -> list[dict]:
        out = []
        if self.tftp.root.is_dir():
            for path in sorted(self.tftp.root.iterdir()):
                if path.is_file():
                    out.append({"name": path.name, "size": path.stat().st_size})
        return out


# ---------------------------------------------------------------------------
# BPF -- the real wire, when the host has one and the caller has root
# ---------------------------------------------------------------------------

BIOCSETIF = 0x8020426C
BIOCIMMEDIATE = 0x80044270
BIOCPROMISC = 0x20004269
BIOCSHDRCMPLT = 0x80044275
BIOCSBLEN = 0xC0044266
BIOCSSEESENT = 0x80044277
BIOCGDLT = 0x4004426A


class BridgeError(RuntimeError):
    pass


class BpfBridge:
    """Promiscuous BPF on one interface: the guest's MAC, on the real segment.

    This is what "a real IP from the home router" costs: root, and a wired
    interface. A Wi-Fi interface will accept the frames and then drop them --
    the AP never saw that MAC associate -- which is why the mode refuses to
    pretend otherwise and says so when the link is not Ethernet.
    """

    HEADER = 32                 # bpf_hdr on a 64-bit Mac; bh_hdrlen is checked

    def __init__(self, iface: str, buffer_size: int = 1 << 20) -> None:
        self.iface = iface
        self.fd = -1
        for index in range(64):
            try:
                self.fd = os.open(f"/dev/bpf{index}", os.O_RDWR)
                break
            except PermissionError as exc:
                raise BridgeError("BPF needs root: sudo python3 tools/i386_net.py "
                                  "--mode bridge --iface %s" % iface) from exc
            except OSError as exc:
                if exc.errno != errno.EBUSY:
                    raise
        if self.fd < 0:
            raise BridgeError("no free /dev/bpf device")
        try:
            fcntl.ioctl(self.fd, BIOCSBLEN, struct.pack("I", buffer_size))
            request = struct.pack("16s", iface.encode()[:15].ljust(16, b"\x00"))
            request = request.ljust(32, b"\x00")
            fcntl.ioctl(self.fd, BIOCSETIF, request)
            fcntl.ioctl(self.fd, BIOCIMMEDIATE, struct.pack("I", 1))
            fcntl.ioctl(self.fd, BIOCPROMISC, b"\x00\x00\x00\x00")
            fcntl.ioctl(self.fd, BIOCSHDRCMPLT, struct.pack("I", 1))
            fcntl.ioctl(self.fd, BIOCSSEESENT, struct.pack("I", 0))
            self.dlt = struct.unpack("I", fcntl.ioctl(self.fd, BIOCGDLT,
                                                      struct.pack("I", 0)))[0]
            self.length = struct.unpack("I", fcntl.ioctl(self.fd, BIOCSBLEN,
                                                         struct.pack("I", buffer_size)))[0]
        except OSError as exc:
            os.close(self.fd)
            self.fd = -1
            raise BridgeError(f"cannot attach to {iface}: {exc}") from exc
        if self.dlt != 1:            # DLT_EN10MB
            os.close(self.fd)
            self.fd = -1
            raise BridgeError(f"{iface} is not Ethernet (DLT {self.dlt})")
        self.queued: list[bytes] = []
        self.sent = 0
        self.received = 0

    def close(self) -> None:
        if self.fd >= 0:
            os.close(self.fd)
            self.fd = -1

    def write(self, frame: bytes) -> None:
        if self.fd < 0:
            return
        try:
            os.write(self.fd, frame)
            self.sent += 1
        except OSError:
            pass

    def read_ready(self, timeout: float = 0.0) -> bool:
        if self.fd < 0:
            return False
        ready, _, _ = select.select([self.fd], [], [], timeout)
        return bool(ready)

    def read(self) -> list[bytes]:
        """Every frame waiting, each one already stripped of its bpf_hdr."""
        if self.fd < 0:
            return []
        frames: list[bytes] = []
        while True:
            try:
                blob = os.read(self.fd, self.length)
            except OSError:
                break
            if not blob:
                break
            offset = 0
            while offset + self.HEADER <= len(blob):
                caplen, datalen, hdrlen = struct.unpack(">IIH", blob[offset + 16:offset + 26])
                start = offset + hdrlen
                end = start + caplen
                if hdrlen < 26 or end > len(blob):
                    break
                frames.append(blob[start:end])
                offset += (hdrlen + caplen + 3) & ~3
            if len(blob) < self.length:
                break
        self.received += len(frames)
        return frames


# ---------------------------------------------------------------------------
# the server: one socket to the machine, one wire underneath
# ---------------------------------------------------------------------------

class Net:
    """Everything the machine's socket reaches: the wire, and the counters."""

    def __init__(self, mode: str = "nat", iface: str = "en10",
                 tftp_root: Path | None = None) -> None:
        self.mode = mode
        self.nat = Nat(tftp_root or Path("build/i386/tftp"))
        self.bridge: BpfBridge | None = None
        self.error = ""
        if mode == "bridge":
            try:
                self.bridge = BpfBridge(iface)
            except BridgeError as exc:
                self.error = str(exc)
                raise
        self.clients = 0
        self.to_guest: asyncio.Queue[bytes] = asyncio.Queue()
        self.dropped_frames = 0
        self.frames_in = 0
        self.frames_out = 0
        self.started = time.time()

    # ---- from the machine --------------------------------------------
    def from_guest(self, frame: bytes) -> None:
        self.frames_in += 1
        if self.bridge is not None:
            self.bridge.write(frame)
            return
        for answer in self.nat.from_guest(frame):
            self._queue(answer)

    def _queue(self, frame: bytes) -> None:
        self.frames_out += 1
        self.to_guest.put_nowait(frame)

    # ---- from the wire -----------------------------------------------
    def pump(self) -> None:
        if self.bridge is not None:
            for frame in self.bridge.read():
                self._queue(frame)
            return
        for frame in self.nat.poll():
            self._queue(frame)

    def status(self) -> dict:
        base = {
            "mode": self.mode,
            "frames": {"in": self.frames_in, "out": self.frames_out,
                       "dropped": self.dropped_frames},
            "clients": self.clients,
            "uptime": round(time.time() - self.started, 1),
            "error": self.error,
        }
        if self.bridge is not None:
            base.update({
                "bridge": {"iface": self.bridge.iface,
                           "sent": self.bridge.sent,
                           "received": self.bridge.received},
                "guest": {"mac": mac_text(GUEST_MAC), "ip": "from the real DHCP"},
            })
            return base
        base.update(self.nat.status())
        return base


PANEL = """<!doctype html>
<meta charset="utf-8"><title>i386 network</title>
<style>
 body{font:13px/1.45 ui-monospace,SFMono-Regular,Menlo,monospace;background:#14161a;color:#d8dee9;margin:0;padding:18px}
 h1{font-size:15px;letter-spacing:.12em;text-transform:uppercase;color:#88c0d0;margin:0 0 14px}
 h2{font-size:12px;letter-spacing:.1em;text-transform:uppercase;color:#81a1c1;margin:18px 0 6px;border-bottom:1px solid #2b303b;padding-bottom:4px}
 table{border-collapse:collapse;width:100%;max-width:760px}
 td,th{text-align:left;padding:3px 10px 3px 0;font-weight:400;vertical-align:top}
 th{color:#7b8794}
 .k{color:#7b8794;width:150px}
 .v{color:#a3be8c}
 .bad{color:#bf616a}
 input{background:#1d2027;border:1px solid #2b303b;color:#d8dee9;padding:3px 6px;font:inherit;width:200px}
 button{background:#2b303b;border:1px solid #3b4252;color:#d8dee9;padding:3px 10px;font:inherit;cursor:pointer}
 button:hover{background:#3b4252}
</style>
<h1>i386 network</h1>
<div id="root">…</div>
<script>
const rows = (o, keys) => keys.map(k => `<tr><td class="k">${k}</td><td class="v">${o[k]}</td></tr>`).join("");
async function tick() {
  const s = await (await fetch('/api/status')).json();
  let html = `<h2>wire</h2><table>${rows({mode:s.mode, uptime:s.uptime+'s', clients:s.clients, error:s.error||'—'}, ['mode','uptime','clients','error'])}</table>`;
  if (s.frames) html += `<h2>frames</h2><table>${rows(s.frames, ['in','out','dropped'])}</table>`;
  if (s.counters) html += `<h2>counters</h2><table>${rows(s.counters, Object.keys(s.counters))}</table>`;
  if (s.guest) html += `<h2>guest</h2><table>${rows(s.guest, Object.keys(s.guest))}</table>`;
  if (s.dhcp) html += `<h2>dhcp</h2><table>${rows(s.dhcp, Object.keys(s.dhcp))}</table>
    <p><input id="boot" value="${s.dhcp.boot_file}"><button onclick="setBoot()">boot file</button></p>`;
  if (s.leases) html += `<h2>leases</h2><table><tr><th>mac</th><th>ip</th><th>host</th><th>boot</th></tr>` +
     s.leases.map(l=>`<tr><td>${l.mac}</td><td class="v">${l.ip}</td><td>${l.hostname||'—'}</td><td>${l.boot_file||'—'}</td></tr>`).join("") + `</table>`;
  if (s.arp) html += `<h2>arp</h2><table>${rows(s.arp, Object.keys(s.arp))}</table>`;
  if (s.tftp) html += `<h2>tftp</h2><table>${rows({root:s.tftp.root,reads:s.tftp.reads,blocks:s.tftp.blocks,errors:s.tftp.errors,last:s.tftp.last||'—'}, ['root','reads','blocks','errors','last'])}</table>` +
     `<table><tr><th>file</th><th>bytes</th></tr>` + s.tftp.files.map(f=>`<tr><td>${f.name}</td><td class="v">${f.size}</td></tr>`).join("") + `</table>`;
  if (s.tcp && s.tcp.length) html += `<h2>tcp</h2><table><tr><th>peer</th><th>port</th><th>state</th><th>sent</th><th>got</th></tr>` +
     s.tcp.map(c=>`<tr><td>${c.peer}</td><td>${c.guest_port}</td><td class="v">${c.state}</td><td>${c.sent}</td><td>${c.got}</td></tr>`).join("") + `</table>`;
  if (s.faults) html += `<h2>faults</h2><table>${rows(s.faults, Object.keys(s.faults))}</table>
     <p><button onclick="post('/api/faults',{blackhole:${!s.faults.blackhole}})">${s.faults.blackhole?'plug the cable back in':'pull the cable out'}</button></p>`;
  document.getElementById('root').innerHTML = html;
}
async function post(path, body) {
  await fetch(path, {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(body)});
  tick();
}
function setBoot(){ post('/api/bootfile', {file: document.getElementById('boot').value}); }
tick(); setInterval(tick, 1000);
</script>
"""


def start_http(net: Net, port: int) -> ThreadingHTTPServer:
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args) -> None:
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
                self.reply(200, net.status())
            elif self.path.startswith("/api/leases"):
                self.reply(200, {"leases": [l.__dict__ for l in net.nat.dhcp.leases.values()]})
            elif self.path.startswith("/api/files"):
                self.reply(200, {"files": net.nat._tftp_files(),
                                 "root": str(net.nat.tftp.root)})
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
            if self.path.startswith("/api/bootfile"):
                net.nat.dhcp.boot_file = str(body.get("file") or "").strip() or "boot.nbp"
                self.reply(200, net.status())
            elif self.path.startswith("/api/faults"):
                if "blackhole" in body:
                    net.nat.blackhole = bool(body["blackhole"])
                if "throttle" in body:
                    net.nat.throttle = float(body["throttle"])
                self.reply(200, net.status())
            elif self.path.startswith("/api/tftp-root"):
                net.nat.tftp.root = Path(body.get("path") or net.nat.tftp.root)
                self.reply(200, net.status())
            else:
                self.reply(404, {"error": "no such thing", "path": self.path})

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


async def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--mode", choices=("nat", "bridge"), default="nat",
                    help="nat: a router in this process; bridge: the real segment")
    ap.add_argument("--iface", default="en10", help="the interface to bridge")
    ap.add_argument("--port", type=int, default=WS_PORT, help="the machine's port")
    ap.add_argument("--http", type=int, default=HTTP_PORT, help="the panel's port")
    ap.add_argument("--tftp", default=str(Path("build/i386/tftp")),
                    help="what the guest can download")
    ap.add_argument("--no-http", action="store_true")
    args = ap.parse_args(argv)

    try:
        net = Net(mode=args.mode, iface=args.iface, tftp_root=Path(args.tftp))
    except BridgeError as exc:
        print(f"[net] {exc}", file=sys.stderr)
        return 1

    if not args.no_http:
        start_http(net, args.http)
        print(f"[net] panel:    http://127.0.0.1:{args.http}/", flush=True)

    if net.mode == "nat":
        print(f"[net] nat:      guest {GUEST_IP} gw {HOST_IP} dns {DNS_IP} "
              f"(upstream {net.nat.dns_upstream})", flush=True)
        print(f"[net] tftp:     {net.nat.tftp.root} "
              f"-> boot file {net.nat.dhcp.boot_file}", flush=True)
    else:
        print(f"[net] bridge:   {net.bridge.iface} promiscuous, "
              f"guest {mac_text(GUEST_MAC)}", flush=True)

    async def echo(sock) -> None:
        net.clients += 1
        print(f"[net] client connected ({net.clients} attached)", flush=True)
        pump_task = asyncio.create_task(pusher(sock))
        try:
            async for message in sock:
                line = str(message)
                if not line:
                    continue
                if line[0] == "T":
                    try:
                        net.from_guest(bytes.fromhex(line[1:].strip()))
                    except ValueError:
                        net.dropped_frames += 1
                elif line[0] == "P":
                    net.pump()
                elif line[0] == "S":
                    await sock.send(json.dumps(net.status()))
        except Exception:
            pass
        finally:
            pump_task.cancel()
            net.clients -= 1
            print(f"[net] client gone ({net.clients} attached)", flush=True)

    async def pusher(sock) -> None:
        """Every frame the wire produces, as one message, in order."""
        while True:
            net.pump()
            try:
                frame = await asyncio.wait_for(net.to_guest.get(), 0.004)
            except asyncio.TimeoutError:
                await asyncio.sleep(0)
                continue
            await sock.send("R" + frame.hex())

    print(f"[net] machine:  ws://127.0.0.1:{args.port}", flush=True)
    async with websockets_serve(echo, args.port):
        await asyncio.Future()
    return 0


def websockets_serve(handler, port: int):
    import websockets
    return websockets.serve(handler, "127.0.0.1", port)


def ctl(argv: list[str]) -> int:
    import urllib.request

    ap = argparse.ArgumentParser(description="talk to a running network")
    ap.add_argument("--http", type=int, default=HTTP_PORT)
    ap.add_argument("command", choices=("status", "leases", "files", "boot-file",
                                        "unplug", "plug", "throttle"))
    ap.add_argument("value", nargs="*")
    args = ap.parse_args(argv)
    base = f"http://127.0.0.1:{args.http}"

    def get(path: str) -> dict:
        return json.loads(urllib.request.urlopen(base + path, timeout=5).read())

    def post(path: str, body: dict) -> dict:
        request = urllib.request.Request(base + path, data=json.dumps(body).encode(),
                                         headers={"Content-Type": "application/json"})
        return json.loads(urllib.request.urlopen(request, timeout=5).read())

    if args.command == "status":
        print(json.dumps(get("/api/status"), indent=1))
    elif args.command == "leases":
        print(json.dumps(get("/api/leases"), indent=1))
    elif args.command == "files":
        print(json.dumps(get("/api/files"), indent=1))
    elif args.command == "boot-file":
        print(json.dumps(post("/api/bootfile", {"file": args.value[0] if args.value else "boot.nbp"}), indent=1))
    elif args.command == "unplug":
        print(json.dumps(post("/api/faults", {"blackhole": True}), indent=1))
    elif args.command == "plug":
        print(json.dumps(post("/api/faults", {"blackhole": False, "throttle": 0.0}), indent=1))
    elif args.command == "throttle":
        ms = float(args.value[0]) if args.value else 0.0
        print(json.dumps(post("/api/faults", {"throttle": ms / 1000.0}), indent=1))
    return 0


if __name__ == "__main__":
    if "--ctl" in sys.argv:
        index = sys.argv.index("--ctl")
        sys.exit(ctl(sys.argv[index + 1:]))
    sys.exit(asyncio.run(main(sys.argv[1:])))
