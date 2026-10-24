import time
import unittest

from tests.helpers import ORIGIN, BridgeFixture

DEVICE = {"name": "Mock ZD421", "uid": "mock-1", "connection": "network",
          "deviceType": "printer", "version": 2}


class ServerTest(unittest.TestCase):
    def setUp(self):
        self.bridge = BridgeFixture()

    def tearDown(self):
        self.bridge.close()

    def test_default_returns_first_printer(self):
        status, headers, body = self.bridge.request("GET", "default?type=printer")
        self.assertEqual(status, 200)
        self.assertEqual(headers["Access-Control-Allow-Origin"], ORIGIN)
        self.assertIn('"uid": "mock-1"', body)
        self.assertIn('"manufacturer": "Zebra Technologies"', body)

    def test_available_lists_printers(self):
        status, _, body = self.bridge.request("GET", "available")
        self.assertEqual(status, 200)
        self.assertIn('"printer": [', body)
        self.assertIn('"connection": "network"', body)

    def test_config(self):
        status, _, body = self.bridge.request("GET", "config")
        self.assertEqual(status, 200)
        self.assertIn('"platform": "linux"', body)

    def test_write_sends_raw_data(self):
        status, _, body = self.bridge.request("POST", "write", {"device": DEVICE, "data": "^XA^FDhello^FS^XZ"})
        self.assertEqual((status, body), (200, ""))
        self.assertTrue(self.bridge.printer.wait_for(b"^XA^FDhello^FS^XZ"))

    def test_utf8_data_reaches_printer_as_utf8(self):
        self.bridge.request("POST", "write", {"device": DEVICE, "data": "^XA^CI28^FDSão Paulo ñ ü^FS^XZ"})
        self.assertTrue(self.bridge.printer.wait_for("São Paulo ñ ü".encode("utf-8")))

    def test_send_then_read_status(self):
        self.bridge.printer.errors = ["head_open", "media_out"]
        self.bridge.request("POST", "write", {"device": DEVICE, "data": "~HQES"})
        start = time.monotonic()
        status, _, body = self.bridge.request("POST", "read", {"device": DEVICE})
        elapsed = time.monotonic() - start
        self.assertEqual(status, 200)
        self.assertIn("ERRORS:         1 00000000 00000005", body)
        self.assertTrue(body.startswith("\x02") and "\x03" in body)
        # Cut at ETX, without waiting for the whole read_timeout.
        self.assertLess(elapsed, 0.8)

    def test_read_without_response_returns_empty_after_timeout(self):
        self.bridge.request("POST", "write", {"device": DEVICE, "data": "~WC"})
        start = time.monotonic()
        status, _, body = self.bridge.request("POST", "read", {"device": DEVICE})
        self.assertEqual((status, body), (200, ""))
        self.assertGreaterEqual(time.monotonic() - start, 0.9)

    def test_read_discards_stale_data_on_next_write(self):
        self.bridge.request("POST", "write", {"device": DEVICE, "data": "~HQES"})
        self.assertTrue(self.bridge.printer.wait_for(b"~HQES"))
        time.sleep(0.1)  # the response stays unread
        self.bridge.request("POST", "write", {"device": DEVICE, "data": "~WC"})
        status, _, body = self.bridge.request("POST", "read", {"device": DEVICE})
        self.assertEqual((status, body), (200, ""))

    def test_reconnects_after_linger_close(self):
        self.bridge.request("POST", "write", {"device": DEVICE, "data": "^XA^FDone^FS^XZ"})
        time.sleep(0.8)  # connection_linger = 0.5
        status, _, _ = self.bridge.request("POST", "write", {"device": DEVICE, "data": "^XA^FDtwo^FS^XZ"})
        self.assertEqual(status, 200)
        self.assertTrue(self.bridge.printer.wait_for(b"^XA^FDtwo^FS^XZ"))

    def test_unknown_printer_has_readable_error(self):
        device = dict(DEVICE, uid="missing")
        status, headers, body = self.bridge.request("POST", "write", {"device": device, "data": "x"})
        self.assertEqual(status, 404)
        self.assertEqual(headers["Access-Control-Allow-Origin"], ORIGIN)
        self.assertEqual(body, "Printer not found: missing")

    def test_send_url_not_supported(self):
        status, headers, body = self.bridge.request("POST", "write", {"device": DEVICE, "url": "http://x"})
        self.assertEqual(status, 400)
        self.assertIn("sendUrl", body)
        self.assertEqual(headers["Access-Control-Allow-Origin"], ORIGIN)

    def test_preflight_private_network_access(self):
        status, headers, _ = self.bridge.request(
            "OPTIONS", "write",
            headers={"Access-Control-Request-Method": "POST",
                     "Access-Control-Request-Private-Network": "true"})
        self.assertEqual(status, 204)
        self.assertEqual(headers["Access-Control-Allow-Private-Network"], "true")
        self.assertEqual(headers["Access-Control-Allow-Origin"], ORIGIN)
        self.assertIn("POST", headers["Access-Control-Allow-Methods"])

    def test_unknown_origin_cannot_print(self):
        status, _, body = self.bridge.request("POST", "write", {"device": DEVICE, "data": "x"},
                                              origin="https://unknown.example.com")
        self.assertEqual(status, 403)
        self.assertIn("not authorized", body)
        self.assertFalse(self.bridge.printer.wait_for(b"x", timeout=0.2))

    def test_qa_subdomain_allowed(self):
        status, _, _ = self.bridge.request("GET", "available", origin="https://dev1.recommercebrands.com")
        self.assertEqual(status, 200)


class NoPrinterTest(unittest.TestCase):
    def test_default_is_empty_200_without_printers(self):
        bridge = BridgeFixture(network_printers=[])
        try:
            status, headers, body = bridge.request("GET", "default?type=printer")
            self.assertEqual((status, body), (200, ""))
            self.assertEqual(headers["Access-Control-Allow-Origin"], ORIGIN)
            status, _, body = bridge.request("GET", "available")
            self.assertEqual((status, body), (200, '{"printer": []}'))
        finally:
            bridge.close()

    def test_unreachable_network_printer_has_readable_error(self):
        bridge = BridgeFixture(network_printers=[{"host": "127.0.0.1", "port": 1, "uid": "off"}],
                               connect_timeout=0.5)
        try:
            device = dict(DEVICE, uid="off")
            status, _, body = bridge.request("POST", "write", {"device": device, "data": "x"})
            self.assertEqual(status, 500)
            self.assertTrue(body.startswith("Error writing to printer Zebra 127.0.0.1"), body)
        finally:
            bridge.close()


if __name__ == "__main__":
    unittest.main()
