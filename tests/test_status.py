import socket
import threading
import time
import unittest
import unittest.mock

from tests.helpers import BridgeFixture

MOCK = {"name": "Mock ZD421", "uid": "mock-1", "connection": "network", "deviceType": "printer", "version": 2}


class StatusTest(unittest.TestCase):
    def setUp(self):
        self.bridge = BridgeFixture(virtual_printers=True, cups_queues=["zebra_raw"], connect_timeout=0.3)
        # An unreachable printer next to the simulated one.
        self.bridge.ui("ui/api/settings/network/add", {"host": "127.0.0.1", "port": 1, "uid": "off"})

    def tearDown(self):
        self.bridge.close()

    def refresh(self, uids=None):
        status, result = self.bridge.ui("ui/api/printers/status", {"uids": uids} if uids else {})
        self.assertEqual(status, 200)
        return result

    def test_status_of_every_kind_of_printer(self):
        self.bridge.printer.errors = ["media_out"]
        self.bridge.ui("ui/api/virtual/update", {"uid": "virtual:zd421-203", "warnings": ["need_calibrate"]})
        result = self.refresh()

        mock = result["mock-1"]
        self.assertEqual(
            (mock["state"], mock["errors"], mock["model"], mock["dpi"]),
            ("error", ["media_out"], "MOCK-ZD421-203dpi", 203),
        )
        virtual = result["virtual:zd421-203"]
        self.assertEqual(
            (virtual["state"], virtual["warnings"], virtual["firmware"]), ("warning", ["need_calibrate"], "V-virtual")
        )
        self.assertEqual(result["cups:zebra_raw"]["state"], "unsupported")
        self.assertEqual(result["off"]["state"], "offline")
        self.assertTrue(result["off"]["error"])

        # Cached in the state the viewer polls, and not recorded as jobs.
        _, state = self.bridge.ui("ui/api/state")
        self.assertEqual(state["printer_status"]["mock-1"]["state"], "error")
        self.assertEqual(state["jobs"], [])

    def test_refresh_a_single_printer(self):
        result = self.refresh(["virtual:zd421-203"])
        self.assertEqual(list(result), ["virtual:zd421-203"])
        self.assertEqual(result["virtual:zd421-203"]["state"], "ready")

    def test_viewer_query_waits_for_the_web_apps_pending_read(self):
        self.bridge.printer.errors = ["head_open"]
        self.bridge.request("POST", "write", {"device": MOCK, "data": "~HQES"})  # sendThenRead, part 1
        done = {}
        query = threading.Thread(target=lambda: done.update(self.refresh(["mock-1"])))
        start = time.monotonic()
        query.start()
        time.sleep(0.4)
        # sendThenRead, part 2: the web app still gets its own answer.
        _, _, body = self.bridge.request("POST", "read", {"device": MOCK})
        self.assertIn("ERRORS:         1 00000000 00000004", body)
        query.join(5)
        self.assertGreaterEqual(time.monotonic() - start, 0.4)
        self.assertEqual(done["mock-1"]["errors"], ["head_open"])

    def test_test_label(self):
        self.refresh(["virtual:zd421-203"])
        status, result = self.bridge.ui("ui/api/printers/test-label", {"uid": "virtual:zd421-203"})
        self.assertEqual(status, 200)
        _, job = self.bridge.ui(f"ui/api/jobs/{result['job_id']}")
        self.assertEqual((job["kind"], job["labels"], job["warnings"]), ("label", 1, []))
        self.assertIn("^FDVirtual ZD421-203dpi^FS", job["data"])
        self.assertIn("ÁÉÍÓÚ ñ ü ç €", job["data"])

        status, result = self.bridge.ui("ui/api/printers/test-label", {"uid": "mock-1"})
        self.assertEqual(status, 200)
        self.assertTrue(self.bridge.printer.wait_for("ÁÉÍÓÚ".encode("utf-8")))
        self.assertEqual(self.bridge.ui("ui/api/printers/test-label", {"uid": "missing"})[0], 404)

    def test_viewer_writes_do_not_block_status_queries(self):
        self.bridge.ui("ui/api/printers/test-label", {"uid": "mock-1"})
        start = time.monotonic()
        self.refresh(["mock-1"])
        self.assertLess(time.monotonic() - start, 1.5)

    def details(self, uid):
        status, result = self.bridge.ui("ui/api/printers/details", {"uid": uid})
        self.assertEqual(status, 200)
        return result

    def test_details_of_a_network_printer(self):
        result = self.details("mock-1")
        self.assertTrue(result["supported"])
        self.assertEqual(result["values"]["serial"], "D2J000000001")
        self.assertEqual(result["values"]["print_width"], "832")
        self.assertEqual(result["values"]["odometer"], "8560 INCHES, 21744 CENTIMETERS")
        self.assertTrue(self.bridge.printer.wait_for(b'! U1 getvar "ezpl.print_width"'))

        # Unknown variables ("?") are None.
        del self.bridge.printer.sgd["appl.link_os_version"]
        self.assertIsNone(self.details("mock-1")["values"]["link_os"])

        # Kept in the status cache across status refreshes, and not recorded as jobs.
        self.refresh(["mock-1"])
        _, state = self.bridge.ui("ui/api/state")
        self.assertEqual(state["printer_status"]["mock-1"]["details"]["values"]["serial"], "D2J000000001")
        self.assertEqual(state["printer_status"]["mock-1"]["state"], "ready")
        self.assertEqual(state["jobs"], [])

    def test_details_of_a_virtual_printer(self):
        values = self.details("virtual:zd421-203")["values"]
        self.assertEqual((values["print_width"], values["label_length"]), ("812", "1218"))  # 4 x 6 in at 203 dpi
        self.assertEqual(values["serial"], "VIRTUAL-ZD421-203")
        self.assertIsNone(values["odometer"])

    def test_details_of_printers_without_sgd(self):
        self.assertEqual(
            self.details("cups:zebra_raw"), {"checked": unittest.mock.ANY, "values": {}, "supported": False}
        )
        self.assertFalse(self.details("off")["supported"])
        self.assertEqual(self.bridge.ui("ui/api/printers/details", {"uid": "missing"})[0], 404)

        # A printer that accepts the connection but never answers: gives up after the first variable.
        silent = socket.create_server(("127.0.0.1", 0))
        self.addCleanup(silent.close)
        accepted = []
        self.addCleanup(lambda: [conn.close() for conn, _ in accepted])
        threading.Thread(target=lambda: accepted.append(silent.accept()), daemon=True).start()
        self.bridge.ui(
            "ui/api/settings/network/add", {"host": "127.0.0.1", "port": silent.getsockname()[1], "uid": "old"}
        )
        start = time.monotonic()
        result = self.details("old")
        self.assertLess(time.monotonic() - start, 2.5)  # one read_timeout (1 s), not one per variable
        self.assertEqual((result["supported"], result["values"]), (False, {}))


if __name__ == "__main__":
    unittest.main()
