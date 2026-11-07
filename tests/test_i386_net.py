"""The i386's network: the wire in a host process.

`tools/i386_net.py` is the other half of the machine's NIC, and like the disk it
lives outside the project, so a Scratch test cannot reach it. These tests drive
it directly -- the same functions the WebSocket handler calls -- so a change to
the packet layer or the NAT fails here instead of in a boot that cannot find its
own gateway.

Nothing here talks to the internet. Every peer is a socket this test opened, so
the suite passes on a train.
"""

from __future__ import annotations

import asyncio
import json
import socket
import struct
import sys
import tempfile
import threading
import time
import unittest
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import i386_net as net  # noqa: E402


def _tftp_root(sizes: dict[str, int] | None = None) -> Path:
    root = Path(tempfile.mkdtemp())
    for name, size in (sizes or {"boot.nbp": 768}).items():
        (root / name).write_bytes(bytes((i * 7) & 0xFF for i in range(size)))
    return root


def _frame(payload: bytes, proto: int, src: str, dst: str) -> bytes:
    return net.ethernet(net.HOST_MAC, net.GUEST_MAC, net.ETH_IPV4,
                        net.ipv4(src, dst, proto, payload))


def _udp_frame(sport: int, dport: int, payload: bytes, dst: str = net.HOST_IP,
               src: str = net.GUEST_IP) -> bytes:
    return _frame(net.udp(src, dst, sport, dport, payload), net.IP_UDP, src, dst)


def _tcp_frame(sport: int, dport: int, seq: int, ack: int, flags: int,
               payload: bytes = b"", dst: str = "127.0.0.1") -> bytes:
    return _frame(net.tcp(net.GUEST_IP, dst, sport, dport, seq, ack, flags, payload),
                  net.IP_TCP, net.GUEST_IP, dst)


def _dhcp_request(kind: int, vendor: str = "") -> bytes:
    blob = bytearray(236)
    blob[0], blob[1], blob[2] = 1, 1, 6
    blob[4:8] = b"\x01\x02\x03\x04"
    blob[28:34] = net.GUEST_MAC
    blob += bytes([0x63, 0x82, 0x53, 0x63]) + bytes([53, 1, kind])
    if vendor:
        blob += bytes([60, len(vendor)]) + vendor.encode()
    blob += bytes([255])
    return _udp_frame(68, 67, bytes(blob), dst="255.255.255.255", src="0.0.0.0")


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


class PacketTests(unittest.TestCase):
    def test_a_checksum_makes_the_sum_come_out_zero(self) -> None:
        header = struct.pack(">BBHHHBBH4s4s", 0x45, 0, 40, 7, 0, 64, 6, 0,
                             net.ip_bytes("10.0.2.15"), net.ip_bytes("10.0.2.2"))
        fixed = header[:10] + struct.pack(">H", net.checksum(header)) + header[12:]
        self.assertEqual(net.checksum(fixed), 0)

    def test_a_frame_says_what_it_carries(self) -> None:
        frame = _tcp_frame(4000, 80, 100, 200, net.TCP_SYN)
        seg = net.dissect(frame)
        self.assertEqual((seg.src_ip, seg.dst_ip), (net.GUEST_IP, "127.0.0.1"))
        self.assertEqual((seg.sport, seg.dport), (4000, 80))
        self.assertEqual(seg.flags, net.TCP_SYN)
        self.assertEqual(seg.proto, net.IP_TCP)

    def test_a_udp_payload_is_the_payload_and_not_the_header(self) -> None:
        seg = net.dissect(_udp_frame(4001, 53, b"question"))
        self.assertEqual(seg.payload, b"question")

    def test_a_short_frame_is_not_a_frame(self) -> None:
        self.assertIsNone(net.dissect(b"\x00" * 10))


class ArpTests(unittest.TestCase):
    def setUp(self) -> None:
        self.nat = net.Nat(_tftp_root())

    def test_the_router_answers_for_itself(self) -> None:
        request = struct.pack(">HHBBH", 1, 0x0800, 6, 4, 1) + net.GUEST_MAC \
            + net.ip_bytes(net.GUEST_IP) + b"\x00" * 6 + net.ip_bytes(net.HOST_IP)
        out = self.nat.from_guest(net.ethernet(b"\xff" * 6, net.GUEST_MAC,
                                               net.ETH_ARP, request))
        self.assertEqual(len(out), 1)
        self.assertEqual(net.ip_text(out[0][28:32]), net.HOST_IP)
        self.assertEqual(out[0][22:28], net.HOST_MAC)

    def test_the_dns_address_is_the_same_machine_as_the_gateway(self) -> None:
        self.assertEqual(net.mac_text(net.HOST_MAC), self.nat.arp_table[net.DNS_IP])

    def test_an_address_we_do_not_own_is_left_to_time_out(self) -> None:
        request = struct.pack(">HHBBH", 1, 0x0800, 6, 4, 1) + net.GUEST_MAC \
            + net.ip_bytes(net.GUEST_IP) + b"\x00" * 6 + net.ip_bytes("10.0.2.99")
        out = self.nat.from_guest(net.ethernet(b"\xff" * 6, net.GUEST_MAC,
                                               net.ETH_ARP, request))
        self.assertEqual(out, [])


class IcmpTests(unittest.TestCase):
    def setUp(self) -> None:
        self.nat = net.Nat(_tftp_root())

    def test_a_ping_is_answered_with_its_own_payload(self) -> None:
        body = struct.pack(">BBHHH", 8, 0, 0, 0x1234, 1) + b"hello"
        body = body[:2] + struct.pack(">H", net.checksum(body)) + body[4:]
        out = self.nat.from_guest(_frame(body, net.IP_ICMP, net.GUEST_IP, net.HOST_IP))
        self.assertEqual(len(out), 1)
        seg = net.dissect(out[0])
        self.assertEqual(seg.icmp_type, 0)              # echo reply
        self.assertEqual(seg.payload[8:], b"hello")
        self.assertEqual(seg.src_ip, net.HOST_IP)       # from who was asked

    def test_every_ping_is_counted(self) -> None:
        body = struct.pack(">BBHHH", 8, 0, 0, 1, 1)
        self.nat.from_guest(_frame(body, net.IP_ICMP, net.GUEST_IP, net.HOST_IP))
        self.assertEqual(self.nat.pings, 1)


class DhcpTests(unittest.TestCase):
    def setUp(self) -> None:
        self.nat = net.Nat(_tftp_root())

    def _answer(self, frame: bytes):
        out = self.nat.from_guest(frame)
        self.assertEqual(len(out), 1)
        seg = net.dissect(out[0])
        return seg, net._dhcp_options(seg.payload)

    def test_a_discover_is_offered_the_guest_address(self) -> None:
        seg, options = self._answer(_dhcp_request(1))
        self.assertEqual(net.ip_text(seg.payload[16:20]), net.GUEST_IP)
        self.assertEqual(options[53], bytes([2]))        # OFFER
        self.assertEqual(options[3], net.ip_bytes(net.HOST_IP))

    def test_a_request_is_acknowledged(self) -> None:
        _, options = self._answer(_dhcp_request(3))
        self.assertEqual(options[53], bytes([5]))        # ACK
        self.assertEqual(self.nat.dhcp.acks, 1)

    def test_a_pxe_client_is_told_where_the_boot_file_is(self) -> None:
        self.nat.dhcp.boot_file = "boot.nbp"
        _, options = self._answer(_dhcp_request(1, vendor="PXEClient:Arch:00000"))
        self.assertEqual(options[66], net.HOST_IP.encode())
        self.assertEqual(options[67], b"boot.nbp")
        self.assertEqual(self.nat.dhcp.pxe_requests, 1)

    def test_a_plain_client_is_not_told_about_boot_files(self) -> None:
        _, options = self._answer(_dhcp_request(1, vendor="dhclient"))
        self.assertNotIn(67, options)
        self.assertEqual(self.nat.dhcp.pxe_requests, 0)

    def test_the_lease_is_remembered_against_the_mac(self) -> None:
        self._answer(_dhcp_request(1, vendor="PXEClient"))
        lease = self.nat.dhcp.leases[net.mac_text(net.GUEST_MAC)]
        self.assertEqual(lease.ip, net.GUEST_IP)
        self.assertEqual(lease.boot_file, "boot.nbp")

    def test_a_release_gets_no_answer(self) -> None:
        self.assertEqual(self.nat.from_guest(_dhcp_request(7)), [])


class TftpTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = _tftp_root({"boot.nbp": 768, "second.bin": 100})
        self.nat = net.Nat(self.root)

    def _rrq(self, name: str, port: int = 40000):
        payload = struct.pack(">H", net.OP_RRQ) + name.encode() + b"\x00octet\x00"
        return self.nat.from_guest(_udp_frame(port, 69, payload))

    def test_the_first_block_is_a_full_512_bytes(self) -> None:
        out = self._rrq("boot.nbp")
        self.assertEqual(len(out), 1)
        seg = net.dissect(out[0])
        self.assertEqual(struct.unpack(">H", seg.payload[:2])[0], net.OP_DATA)
        self.assertEqual(struct.unpack(">H", seg.payload[2:4])[0], 1)
        self.assertEqual(len(seg.payload) - 4, 512)

    def test_each_ack_unlocks_the_next_block(self) -> None:
        self._rrq("boot.nbp")
        ack = struct.pack(">HH", net.OP_ACK, 1)
        out = self.nat.from_guest(_udp_frame(40000, 69, ack))
        seg = net.dissect(out[0])
        self.assertEqual(struct.unpack(">H", seg.payload[2:4])[0], 2)
        self.assertEqual(len(seg.payload) - 4, 256)      # the short last block

    def test_the_last_block_ends_the_session(self) -> None:
        self._rrq("boot.nbp")
        self.nat.from_guest(_udp_frame(40000, 69, struct.pack(">HH", net.OP_ACK, 1)))
        out = self.nat.from_guest(_udp_frame(40000, 69, struct.pack(">HH", net.OP_ACK, 2)))
        self.assertEqual(out, [])
        self.assertEqual(self.nat.tftp.sessions, {})

    def test_a_block_nobody_acknowledges_is_sent_again(self) -> None:
        self._rrq("boot.nbp")
        again = self.nat.tftp.tick(time.time() + 2.0)
        self.assertEqual(len(again), 1)
        self.assertEqual(struct.unpack(">H", again[0][1][2:4])[0], 1)

    def test_an_old_ack_does_not_move_the_session(self) -> None:
        self._rrq("boot.nbp")
        self.nat.from_guest(_udp_frame(40000, 69, struct.pack(">HH", net.OP_ACK, 1)))
        out = self.nat.from_guest(_udp_frame(40000, 69, struct.pack(">HH", net.OP_ACK, 1)))
        self.assertEqual(out, [])

    def test_a_missing_file_is_an_error_not_a_hang(self) -> None:
        out = self._rrq("nothing.here")
        seg = net.dissect(out[0])
        self.assertEqual(struct.unpack(">H", seg.payload[:2])[0], net.OP_ERROR)
        self.assertIn(b"no such file", seg.payload)

    def test_a_name_cannot_climb_out_of_the_directory(self) -> None:
        out = self._rrq("../../etc/passwd")
        seg = net.dissect(out[0])
        self.assertEqual(struct.unpack(">H", seg.payload[:2])[0], net.OP_ERROR)

    def test_a_write_is_refused(self) -> None:
        payload = struct.pack(">H", net.OP_WRQ) + b"new.bin\x00octet\x00"
        out = self.nat.from_guest(_udp_frame(40000, 69, payload))
        seg = net.dissect(out[0])
        self.assertEqual(struct.unpack(">H", seg.payload[:2])[0], net.OP_ERROR)
        self.assertFalse((self.root / "new.bin").exists())


class UdpNatTests(unittest.TestCase):
    def setUp(self) -> None:
        self.nat = net.Nat(_tftp_root())
        self.peer = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.peer.bind(("127.0.0.1", 0))
        self.peer.settimeout(2.0)

    def tearDown(self) -> None:
        self.peer.close()
        for sock in self.nat.udp_flows.values():
            sock.close()

    def test_a_datagram_reaches_the_peer_it_named(self) -> None:
        self.nat.from_guest(_udp_frame(40010, self.peer.getsockname()[1], b"hi",
                                       dst="127.0.0.1"))
        self.assertEqual(self.peer.recvfrom(64)[0], b"hi")

    def test_the_reply_carries_the_address_the_guest_asked_for(self) -> None:
        port = self.peer.getsockname()[1]
        self.nat.from_guest(_udp_frame(40011, port, b"ping", dst="127.0.0.1"))
        _, ours = self.peer.recvfrom(64)              # the socket the NAT opened
        self.peer.sendto(b"pong", ours)               # the peer answers that
        out = []
        for _ in range(20):
            out = self.nat.poll()
            if out:
                break
            time.sleep(0.02)
        self.assertTrue(out)
        seg = net.dissect(out[0])
        self.assertEqual(seg.payload, b"pong")
        self.assertEqual(seg.src_ip, "127.0.0.1")     # what the guest dialled
        self.assertEqual(seg.dport, 40011)

    def test_a_dns_reply_comes_back_from_the_dns_address(self) -> None:
        self.nat.dns_upstream = "127.0.0.1"
        self.nat.udp_flows[(40012, net.DNS_IP, 53)] = self.peer
        self.nat.udp_keys[self.peer.fileno()] = (40012, net.DNS_IP, 53)
        self.peer.sendto(b"\x12\x34answer", self.peer.getsockname())
        out = []
        for _ in range(20):
            out = self.nat.poll()
            if out:
                break
            time.sleep(0.02)
        self.assertTrue(out)
        seg = net.dissect(out[0])
        self.assertEqual(seg.src_ip, net.DNS_IP)
        self.assertEqual(seg.payload, b"\x12\x34answer")


class TcpNatTests(unittest.TestCase):
    def setUp(self) -> None:
        self.nat = net.Nat(_tftp_root())
        self.server = socket.socket()
        self.server.bind(("127.0.0.1", 0))
        self.server.listen(1)
        self.port = self.server.getsockname()[1]
        self.seen: dict[str, bytes] = {}
        self.frames: list[bytes] = []

    def tearDown(self) -> None:
        self.server.close()

    def _talk(self, on_server) -> None:
        thread = threading.Thread(target=on_server, daemon=True)
        thread.start()
        out = self.nat.from_guest(_tcp_frame(40020, self.port, 1000, 0, net.TCP_SYN))
        self.assertEqual(len(out), 1)
        synack = net.dissect(out[0])
        self.assertEqual(synack.flags, net.TCP_SYN | net.TCP_ACK)
        self.assertEqual(synack.ack, 1001)
        self.nat.from_guest(_tcp_frame(40020, self.port, 1001, synack.seq + 1, net.TCP_ACK))
        self.nat.from_guest(_tcp_frame(40020, self.port, 1001, synack.seq + 1,
                                       net.TCP_ACK | net.TCP_PSH, b"hello"))
        for _ in range(50):
            self.frames.extend(self.nat.poll())   # the wire is always polling
            if "in" in self.seen or not thread.is_alive():
                break
            time.sleep(0.01)
        thread.join(2.0)

    def test_the_handshake_is_a_syn_ack_with_our_own_sequence(self) -> None:
        out = self.nat.from_guest(_tcp_frame(40021, self.port, 5000, 0, net.TCP_SYN))
        synack = net.dissect(out[0])
        self.assertEqual(synack.flags, net.TCP_SYN | net.TCP_ACK)
        self.assertEqual(synack.ack, 5001)
        self.assertEqual(self.nat.tcp_opened, 0)      # offered, not open
        self.nat.from_guest(_tcp_frame(40021, self.port, 5001, synack.seq + 1, net.TCP_ACK))
        self.assertEqual(self.nat.tcp_opened, 1)

    def test_data_reaches_the_peer_and_the_answer_comes_back(self) -> None:
        def serve():
            conn, _ = self.server.accept()
            self.seen["in"] = conn.recv(64)
            conn.sendall(b"hi back")
            conn.close()

        self._talk(serve)
        for _ in range(60):
            self.frames.extend(self.nat.poll())
            if any(net.dissect(f).payload == b"hi back" for f in self.frames):
                break
            time.sleep(0.02)
        self.assertEqual(self.seen["in"], b"hello")
        payloads = [net.dissect(f) for f in self.frames]
        self.assertIn(b"hi back", [seg.payload for seg in payloads])
        self.assertTrue(any(seg.flags & net.TCP_FIN for seg in payloads))

    def test_a_retransmission_is_acknowledged_and_not_written_twice(self) -> None:
        def serve():
            conn, _ = self.server.accept()
            self.seen["first"] = conn.recv(64)
            conn.settimeout(0.4)
            try:
                self.seen["second"] = conn.recv(64)
            except socket.timeout:
                self.seen["second"] = b""
            conn.close()

        thread = threading.Thread(target=serve, daemon=True)
        thread.start()
        out = self.nat.from_guest(_tcp_frame(40022, self.port, 10, 0, net.TCP_SYN))
        synack = net.dissect(out[0])
        self.nat.from_guest(_tcp_frame(40022, self.port, 11, synack.seq + 1, net.TCP_ACK))
        again = self.nat.from_guest(_tcp_frame(40022, self.port, 11, synack.seq + 1,
                                               net.TCP_ACK | net.TCP_PSH, b"once"))
        self.assertEqual(net.dissect(again[0]).flags, net.TCP_ACK)
        self.nat.from_guest(_tcp_frame(40022, self.port, 11, synack.seq + 1,
                                       net.TCP_ACK | net.TCP_PSH, b"once"))
        for _ in range(50):
            self.frames.extend(self.nat.poll())
            if "first" in self.seen:
                break
            time.sleep(0.01)
        thread.join(2.0)
        self.assertEqual(self.seen["first"], b"once")
        self.assertEqual(self.seen["second"], b"")

    def test_a_port_nobody_listens_on_is_a_reset(self) -> None:
        dead = _free_port()
        out = self.nat.from_guest(_tcp_frame(40023, dead, 77, 0, net.TCP_SYN))
        for _ in range(100):
            if any(net.dissect(f).flags & net.TCP_RST for f in out):
                break
            out.extend(self.nat.poll())
            time.sleep(0.02)
        self.assertTrue(any(net.dissect(f).flags & net.TCP_RST for f in out))
        self.assertEqual(self.nat.tcp_closed, 0)      # it was never open

    def test_a_segment_for_no_connection_is_a_reset_too(self) -> None:
        out = self.nat.from_guest(_tcp_frame(40024, 80, 1, 1, net.TCP_ACK))
        self.assertTrue(net.dissect(out[0]).flags & net.TCP_RST)

    def test_the_guest_hanging_up_ends_the_flow(self) -> None:
        out = self.nat.from_guest(_tcp_frame(40025, self.port, 300, 0, net.TCP_SYN))
        synack = net.dissect(out[0])
        self.nat.from_guest(_tcp_frame(40025, self.port, 301, synack.seq + 1, net.TCP_ACK))
        out = self.nat.from_guest(_tcp_frame(40025, self.port, 301, synack.seq + 1,
                                             net.TCP_FIN | net.TCP_ACK))
        self.assertTrue(net.dissect(out[0]).flags & net.TCP_FIN)
        flow = self.nat.tcp_flows[(net.GUEST_IP, 40025, "127.0.0.1", self.port)]
        self.assertTrue(flow.fin_got)
        self.assertEqual(flow.state, "closing")


class FaultTests(unittest.TestCase):
    def setUp(self) -> None:
        self.nat = net.Nat(_tftp_root())

    def test_a_pulled_cable_drops_everything(self) -> None:
        self.nat.blackhole = True
        body = struct.pack(">BBHHH", 8, 0, 0, 1, 1)
        self.assertEqual(self.nat.from_guest(_frame(body, net.IP_ICMP, net.GUEST_IP,
                                                    net.HOST_IP)), [])
        self.assertEqual(self.nat.poll(), [])
        self.assertEqual(self.nat.dropped, 1)

    def test_status_is_what_the_panel_draws(self) -> None:
        body = struct.pack(">BBHHH", 8, 0, 0, 1, 1)
        self.nat.from_guest(_frame(body, net.IP_ICMP, net.GUEST_IP, net.HOST_IP))
        status = self.nat.status()
        self.assertEqual(status["guest"]["ip"], net.GUEST_IP)
        self.assertEqual(status["counters"]["pings"], 1)
        self.assertIn("boot_file", status["dhcp"])
        self.assertIn("files", status["tftp"])


class LinkTests(unittest.TestCase):
    """The machine's side of the socket: T out, R in."""

    def test_a_frame_goes_out_and_an_answer_comes_back(self) -> None:
        link = net.Net(mode="nat", tftp_root=_tftp_root())
        port = _free_port()

        async def run() -> list[bytes]:
            import websockets

            async def echo(sock):
                pump = asyncio.create_task(pusher(sock))
                try:
                    async for message in sock:
                        if str(message)[0] == "T":
                            link.from_guest(bytes.fromhex(str(message)[1:]))
                finally:
                    pump.cancel()

            async def pusher(sock):
                while True:
                    link.pump()
                    try:
                        frame = await asyncio.wait_for(link.to_guest.get(), 0.004)
                    except asyncio.TimeoutError:
                        await asyncio.sleep(0)
                        continue
                    await sock.send("R" + frame.hex())

            server = await websockets.serve(echo, "127.0.0.1", port)
            answers = []
            async with websockets.connect(f"ws://127.0.0.1:{port}") as ws:
                request = struct.pack(">HHBBH", 1, 0x0800, 6, 4, 1) + net.GUEST_MAC \
                    + net.ip_bytes(net.GUEST_IP) + b"\x00" * 6 + net.ip_bytes(net.HOST_IP)
                frame = net.ethernet(b"\xff" * 6, net.GUEST_MAC, net.ETH_ARP, request)
                await ws.send("T" + frame.hex())
                deadline = time.time() + 3
                while time.time() < deadline and not answers:
                    try:
                        message = await asyncio.wait_for(ws.recv(), 0.5)
                    except asyncio.TimeoutError:
                        continue
                    if message[0] == "R":
                        answers.append(bytes.fromhex(message[1:]))
            server.close()
            return answers

        answers = asyncio.run(run())
        self.assertTrue(answers, "the wire answered nothing")
        self.assertEqual(net.ip_text(answers[0][28:32]), net.HOST_IP)

    def test_the_panel_and_the_api_are_there(self) -> None:
        from http.server import ThreadingHTTPServer

        link = net.Net(mode="nat", tftp_root=_tftp_root())
        port = _free_port()
        server = net.start_http(link, port)
        try:
            page = urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=5).read()
            self.assertIn(b"i386 network", page)
            status = json.loads(urllib.request.urlopen(
                f"http://127.0.0.1:{port}/api/status", timeout=5).read())
            self.assertEqual(status["mode"], "nat")
            request = urllib.request.Request(
                f"http://127.0.0.1:{port}/api/bootfile",
                data=json.dumps({"file": "netboot.bin"}).encode(),
                headers={"Content-Type": "application/json"})
            urllib.request.urlopen(request, timeout=5).read()
            self.assertEqual(link.nat.dhcp.boot_file, "netboot.bin")
        finally:
            server.shutdown()


if __name__ == "__main__":
    unittest.main()
