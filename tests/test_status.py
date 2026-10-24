import threading
import time
import unittest

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
        self.assertEqual((mock["state"], mock["errors"], mock["model"], mock["dpi"]),
                         ("error", ["media_out"], "MOCK-ZD421-203dpi", 203))
        virtual = result["virtual:zd421-203"]
        self.assertEqual((virtual["state"], virtual["warnings"], virtual["firmware"]),
                         ("warning", ["need_calibrate"], "V-virtual"))
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


if __name__ == "__main__":
    unittest.main()
