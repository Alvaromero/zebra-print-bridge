import socket
import struct
import threading
import unittest
from unittest import mock

from tests.helpers import BridgeFixture
from zebra_print_bridge import discovery
from zebra_print_bridge.discovery import (
    SERVICE,
    TYPE_A,
    TYPE_PTR,
    TYPE_SRV,
    TYPE_TXT,
    DnsError,
    build_query,
    collect,
    encode_name,
    parse_packet,
)


def record(name, rtype, rdata):
    return encode_name(name) + struct.pack("!HHIH", rtype, 0x8001, 120, len(rdata)) + rdata


def txt(**values):
    return b"".join(bytes([len(e)]) + e for e in (f"{k}={v}".encode() for k, v in values.items()))


def response(instance, hostname, ip, port=9100, **txt_values):
    """An mDNS answer like a Zebra Link-OS printer's: PTR + SRV/TXT/A as additional records.

    The PTR target uses a compression pointer to the question name, as real responders do.
    """
    question = encode_name(SERVICE) + struct.pack("!2H", TYPE_PTR, 1)
    label = instance.encode()
    ptr_rdata = bytes([len(label)]) + label + b"\xc0\x0c"  # "<instance>." + pointer to offset 12
    full = f"{instance}.{SERVICE}"
    body = (
        record(SERVICE, TYPE_PTR, ptr_rdata)
        + record(full, TYPE_SRV, struct.pack("!3H", 0, 0, port) + encode_name(hostname))
        + record(full, TYPE_TXT, txt(**txt_values))
        + record(hostname, TYPE_A, socket.inet_aton(ip))
    )
    return struct.pack("!6H", 0, 0x8400, 1, 1, 0, 3) + question + body


class FakeResponder:
    """Answers any PTR query for the service on 127.0.0.1, like a legacy-unicast mDNS responder."""

    def __init__(self, packets):
        self.packets = packets
        self.queries = []
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind(("127.0.0.1", 0))
        self.address = self.sock.getsockname()
        threading.Thread(target=self._serve, daemon=True).start()

    def _serve(self):
        while True:
            try:
                query, sender = self.sock.recvfrom(9000)
            except OSError:
                return
            self.queries.append(query)
            for packet in self.packets:
                self.sock.sendto(packet, sender)

    def close(self):
        self.sock.close()


ZEBRA = response(
    "ZTC ZD421-203dpi ZPL",
    "zd421-warehouse.local",
    "192.168.1.50",
    ty="ZTC ZD421-203dpi ZPL",
    usb_MFG="Zebra Technologies",
)
OTHER = response("HP LaserJet M404", "hp-office.local", "192.168.1.60", ty="HP LaserJet Pro M404")


class ParserTest(unittest.TestCase):
    def test_query_asks_for_the_pdl_datastream_service(self):
        query = build_query(SERVICE)
        self.assertEqual(query[:12], struct.pack("!6H", 0, 0, 1, 0, 0, 0))
        self.assertIn(b"\x0f_pdl-datastream\x04_tcp\x05local\x00", query)

    def test_records_with_compressed_names(self):
        records = parse_packet(ZEBRA)
        by_type = {r["type"]: r for r in records}
        self.assertEqual(by_type[TYPE_PTR]["target"], f"ZTC ZD421-203dpi ZPL.{SERVICE}")
        self.assertEqual(by_type[TYPE_SRV]["port"], 9100)
        self.assertEqual(by_type[TYPE_SRV]["target"], "zd421-warehouse.local")
        self.assertEqual(by_type[TYPE_TXT]["txt"]["usb_mfg"], "Zebra Technologies")
        self.assertEqual(by_type[TYPE_A]["address"], "192.168.1.50")

    def test_malformed_packets(self):
        for packet in (b"", b"\x00" * 11, ZEBRA[:40], struct.pack("!6H", 0, 0, 0, 1, 0, 0) + b"\xc0\x0c"):
            with self.subTest(packet=packet):
                with self.assertRaises(DnsError):
                    parse_packet(packet)
        # A malformed packet is skipped, not fatal.
        self.assertEqual(len(collect([(b"junk", "10.0.0.1"), (ZEBRA, "192.168.1.50")])), 1)

    def test_collect_joins_records_and_ranks_zebra_first(self):
        printers = collect([(OTHER, "192.168.1.60"), (ZEBRA, "192.168.1.50"), (ZEBRA, "192.168.1.50")])
        self.assertEqual(
            printers,
            [
                {
                    "name": "ZTC ZD421-203dpi ZPL",
                    "host": "192.168.1.50",
                    "port": 9100,
                    "hostname": "zd421-warehouse.local",
                    "model": "ZTC ZD421-203dpi ZPL",
                    "zebra": True,
                },
                {
                    "name": "HP LaserJet M404",
                    "host": "192.168.1.60",
                    "port": 9100,
                    "hostname": "hp-office.local",
                    "model": "HP LaserJet Pro M404",
                    "zebra": False,
                },
            ],
        )

    def test_sender_address_when_there_is_no_a_record(self):
        label = b"ZD621"
        packet = struct.pack("!6H", 0, 0x8400, 0, 1, 0, 0) + record(
            SERVICE, TYPE_PTR, bytes([len(label)]) + label + encode_name(SERVICE)
        )
        [printer] = collect([(packet, "10.1.2.3")])
        self.assertEqual((printer["host"], printer["port"], printer["name"]), ("10.1.2.3", 9100, "ZD621"))


class DiscoverTest(unittest.TestCase):
    def test_discover_over_udp(self):
        responder = FakeResponder([ZEBRA, OTHER])
        try:
            printers = discovery.discover(timeout=0.6, address=responder.address)
        finally:
            responder.close()
        self.assertEqual([p["host"] for p in printers], ["192.168.1.50", "192.168.1.60"])
        self.assertEqual(len(responder.queries), 2)  # the query is sent again halfway
        self.assertEqual(responder.queries[0], build_query(SERVICE))

    def test_no_answer(self):
        responder = FakeResponder([])
        try:
            self.assertEqual(discovery.discover(timeout=0.3, address=responder.address), [])
        finally:
            responder.close()


class DiscoverEndpointTest(unittest.TestCase):
    def setUp(self):
        self.bridge = BridgeFixture()

    def tearDown(self):
        self.bridge.close()

    def test_results_say_which_printers_are_already_added(self):
        responder = FakeResponder([ZEBRA, OTHER])
        self.bridge.ui("ui/api/settings/network/add", {"host": "192.168.1.60", "name": "Office"})
        try:
            with mock.patch.object(discovery, "MDNS_ADDR", responder.address):
                status, body = self.bridge.ui("ui/api/settings/network/discover", {"timeout": 0.6})
        finally:
            responder.close()
        self.assertEqual(status, 200)
        self.assertTrue(body["ok"])
        self.assertEqual(
            [(p["uid"], p["added"]) for p in body["printers"]], [("192.168.1.50", False), ("192.168.1.60", True)]
        )

    def test_network_error_is_reported(self):
        with mock.patch.object(discovery, "discover", side_effect=OSError("Network is unreachable")):
            status, body = self.bridge.ui("ui/api/settings/network/discover", {})
        self.assertEqual((status, body["ok"], body["error"]), (200, False, "Network is unreachable"))

    def test_invalid_timeout(self):
        status, _ = self.bridge.ui("ui/api/settings/network/discover", {"timeout": "soon"})
        self.assertEqual(status, 400)


if __name__ == "__main__":
    unittest.main()
