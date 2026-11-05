"""A host-side network for the rv64 board: ARP, ICMP echo and DHCP.

The guest has a NIC; the frames it sends arrive here. This is a *relay*, not a
bridge. A bridge (`tap`, `vmnet`) hands the guest a raw Ethernet segment and
needs root; a relay answers ARP and DHCP itself, which means the machine gets a
working network without privileges and without touching the host's real
interface:

    guest 10.0.2.15/24   ──  NIC  ──  this ──  host sockets  ──  the internet
    gateway 10.0.2.2 (us)                     DNS 10.0.2.3 (us)

The frame format is ordinary Ethernet II, because that is what a NIC sees. When
this grows a TCP path it terminates the connection in the host process, which is
where slirp puts it, and the guest only ever speaks its own stack.
"""

from __future__ import annotations

import struct

GUEST_MAC = bytes.fromhex("525400123456")
HOST_MAC = bytes.fromhex("525400000001")
GUEST_IP = "10.0.2.15"
HOST_IP = "10.0.2.2"
DNS_IP = "10.0.2.3"
PREFIX = 24

ETH_IPV4 = 0x0800
ETH_ARP = 0x0806

IP_ICMP = 1
IP_TCP = 6
IP_UDP = 17


def ip_bytes(text: str) -> bytes:
    return bytes(int(part) for part in text.split("."))


def ip_text(raw: bytes) -> str:
    return ".".join(str(b) for b in raw)


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


def _ethernet(dst: bytes, src: bytes, ethertype: int, payload: bytes) -> bytes:
    return dst + src + struct.pack(">H", ethertype) + payload


def _ipv4(src: bytes, dst: bytes, proto: int, payload: bytes, ident: int = 1) -> bytes:
    header = struct.pack(">BBHHHBBH4s4s", 0x45, 0, 20 + len(payload), ident, 0, 64, proto, 0, src, dst)
    total = checksum(header)
    header = header[:10] + struct.pack(">H", total) + header[12:]
    return header + payload


def arp_reply(frame: bytes) -> bytes | None:
    """Answer an ARP request for any address we own (the gateway, the DNS)."""
    if len(frame) < 42 or struct.unpack(">H", frame[12:14])[0] != ETH_ARP:
        return None
    htype, ptype, hlen, plen, oper = struct.unpack(">HHBBH", frame[14:22])
    if (htype, ptype, hlen, plen, oper) != (1, ETH_IPV4, 6, 4, 1):
        return None
    sender_mac = frame[22:28]
    target_ip = frame[38:42]
    if target_ip not in (ip_bytes(HOST_IP), ip_bytes(DNS_IP)):
        return None
    body = struct.pack(">HHBBH", 1, ETH_IPV4, 6, 4, 2)
    body += HOST_MAC + target_ip + sender_mac + frame[28:32]
    return _ethernet(sender_mac, HOST_MAC, ETH_ARP, body)


def icmp_echo_reply(frame: bytes) -> bytes | None:
    """Ping. The IP header is mirrored back, the way a host does it."""
    if len(frame) < 42 or struct.unpack(">H", frame[12:14])[0] != ETH_IPV4:
        return None
    ip = frame[14:]
    if ip[0] >> 4 != 4:
        return None
    ihl = (ip[0] & 0xF) * 4
    if ip[9] != IP_ICMP:
        return None
    dst_ip = ip[16:20]
    if dst_ip not in (ip_bytes(HOST_IP), ip_bytes(GUEST_IP), ip_bytes(DNS_IP)):
        return None
    icmp = ip[ihl:]
    if len(icmp) < 8 or icmp[0] != 8:          # echo request only
        return None
    reply = bytearray(icmp)
    reply[0] = 0                                # echo reply
    reply[2:4] = b"\x00\x00"
    reply[2:4] = struct.pack(">H", checksum(bytes(reply)))
    out = _ipv4(dst_ip, ip[12:16], IP_ICMP, bytes(reply))
    return _ethernet(frame[6:12], HOST_MAC, ETH_IPV4, out)


def dhcp_offer(frame: bytes) -> bytes | None:
    """Answer a DHCP DISCOVER, so the guest can be `ip addr`-clean."""
    if len(frame) < 42 or struct.unpack(">H", frame[12:14])[0] != ETH_IPV4:
        return None
    ip = frame[14:]
    ihl = (ip[0] & 0xF) * 4
    if ip[9] != IP_UDP:
        return None
    udp = ip[ihl:]
    if len(udp) < 240:
        return None
    sport, dport = struct.unpack(">HH", udp[0:4])
    if not (sport == 68 and dport == 67):
        return None
    bootp = bytearray(udp[8:])
    if len(bootp) < 240 or bootp[0] != 1:      # BOOTREQUEST
        return None
    xid = bootp[4:8]
    chaddr = bootp[28:34]

    offer = bytearray(240)
    offer[0] = 2                                # BOOTREPLY
    offer[1] = 1                                # ethernet
    offer[2] = 6
    offer[4:8] = xid
    offer[10:12] = b"\x80\x00"                  # broadcast flag
    offer[16:20] = ip_bytes(GUEST_IP)
    offer[20:24] = ip_bytes(HOST_IP)
    offer[28:34] = chaddr
    offer += bytes([0x63, 0x82, 0x53, 0x63])    # magic cookie
    offer += bytes([53, 1, 2])                  # DHCP message type: OFFER
    offer += bytes([1, 4]) + ip_bytes("255.255.255.0")
    offer += bytes([3, 4]) + ip_bytes(HOST_IP)
    offer += bytes([6, 4]) + ip_bytes(DNS_IP)
    offer += bytes([54, 4]) + ip_bytes(HOST_IP)
    offer += bytes([51, 4]) + struct.pack(">I", 86400)
    offer += bytes([255])

    udp_len = 8 + len(offer)
    udp_hdr = struct.pack(">HHHH", 67, 68, udp_len, 0)
    pseudo = ip_bytes(HOST_IP) + ip_bytes("255.255.255.255") + struct.pack(">BBH", 0, IP_UDP, udp_len)
    summed = checksum(pseudo + udp_hdr + bytes(offer))
    udp_hdr = struct.pack(">HHHH", 67, 68, udp_len, summed)
    out = _ipv4(ip_bytes(HOST_IP), ip_bytes("255.255.255.255"), IP_UDP, udp_hdr + bytes(offer))
    return _ethernet(b"\xff" * 6, HOST_MAC, ETH_IPV4, out)


class Bridge:
    """The host end of the NIC. `from_guest` returns the frames to hand back."""

    def __init__(self) -> None:
        self.tx_count = 0
        self.rx_count = 0
        self.arp_count = 0
        self.ping_count = 0
        self.dhcp_count = 0

    def from_guest(self, frame: bytes) -> list[bytes]:
        self.tx_count += 1
        out: list[bytes] = []
        reply = arp_reply(frame)
        if reply is not None:
            self.arp_count += 1
            out.append(reply)
        reply = icmp_echo_reply(frame)
        if reply is not None:
            self.ping_count += 1
            out.append(reply)
        reply = dhcp_offer(frame)
        if reply is not None:
            self.dhcp_count += 1
            out.append(reply)
        self.rx_count += len(out)
        return out
