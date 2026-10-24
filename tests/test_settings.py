import json
import time
import unittest
import urllib.error
import urllib.request

from tests.helpers import BridgeFixture

UNKNOWN = "https://shop.example.com"
DEVICE = {"name": "Mock ZD421", "uid": "mock-1", "connection": "network", "deviceType": "printer", "version": 2}


class OriginApprovalTest(unittest.TestCase):
    def setUp(self):
        self.bridge = BridgeFixture()

    def tearDown(self):
        self.bridge.close()

    def settings(self):
        return self.bridge.ui("ui/api/settings")[1]

    def test_unknown_website_is_pending_with_a_readable_message(self):
        status, headers, body = self.bridge.request("GET", "default?type=printer", origin=UNKNOWN)
        self.assertEqual(status, 403)
        # CORS headers so the web app can show the message to the user.
        self.assertEqual(headers["Access-Control-Allow-Origin"], UNKNOWN)
        self.assertIn(f"/ui", body)
        self.assertIn("not authorized", body)
        pending = self.bridge.ui("ui/api/state")[1]["pending_origins"]
        self.assertEqual([p["origin"] for p in pending], [UNKNOWN])
        self.assertEqual(pending[0]["requests"], 1)

    def test_pending_website_passes_the_preflight(self):
        status, headers, _ = self.bridge.request(
            "OPTIONS", "write", origin=UNKNOWN,
            headers={"Access-Control-Request-Method": "POST", "Access-Control-Request-Private-Network": "true"})
        self.assertEqual(status, 204)
        self.assertEqual(headers["Access-Control-Allow-Private-Network"], "true")

    def test_approve_block_and_forget(self):
        self.bridge.request("GET", "available", origin=UNKNOWN)
        status, settings = self.bridge.ui("ui/api/settings/origin", {"origin": UNKNOWN, "action": "allow"})
        self.assertEqual(status, 200)
        self.assertEqual(settings["origins"]["allowed"], [UNKNOWN])
        self.assertEqual(settings["origins"]["pending"], [])
        status, _, _ = self.bridge.request("POST", "write", {"device": DEVICE, "data": "^XA^FDok^FS^XZ"}, origin=UNKNOWN)
        self.assertEqual(status, 200)
        with open(self.bridge.server.config["settings_file"]) as fh:
            self.assertEqual(json.load(fh)["origins"]["allowed"], [UNKNOWN])

        self.bridge.ui("ui/api/settings/origin", {"origin": UNKNOWN, "action": "deny"})
        status, _, body = self.bridge.request("GET", "available", origin=UNKNOWN)
        self.assertEqual(status, 403)
        self.assertIn("blocked", body)
        status, _, _ = self.bridge.request("OPTIONS", "write", origin=UNKNOWN,
                                           headers={"Access-Control-Request-Method": "POST"})
        self.assertEqual(status, 403)

        self.bridge.ui("ui/api/settings/origin", {"origin": UNKNOWN, "action": "forget"})
        self.assertEqual(self.settings()["origins"]["denied"], [])
        status, _, body = self.bridge.request("GET", "available", origin=UNKNOWN)
        self.assertIn("not authorized", body)

    def test_invalid_origin_action(self):
        status, _ = self.bridge.ui("ui/api/settings/origin", {"origin": "javascript:alert(1)", "action": "allow"})
        self.assertEqual(status, 400)
        status, _ = self.bridge.ui("ui/api/settings/origin", {"origin": UNKNOWN, "action": "maybe"})
        self.assertEqual(status, 400)

    def test_without_approval_only_config_patterns(self):
        self.bridge.close()
        self.bridge = BridgeFixture(origin_approval=False)
        status, headers, _ = self.bridge.request("GET", "available", origin=UNKNOWN)
        self.assertEqual(status, 403)
        self.assertEqual(self.bridge.ui("ui/api/state")[1]["pending_origins"], [])
        self.bridge.close()
        self.bridge = BridgeFixture(origin_approval=False, allowed_origins=[])
        self.assertEqual(self.bridge.request("GET", "available", origin=UNKNOWN)[0], 200)


class NetworkSettingsTest(unittest.TestCase):
    def setUp(self):
        self.bridge = BridgeFixture(network_printers=[])
        self.host, self.port = self.bridge.printer.address

    def tearDown(self):
        self.bridge.close()

    def available(self):
        return [p["uid"] for p in json.loads(self.bridge.request("GET", "available")[2])["printer"]]

    def test_add_use_and_remove_network_printer(self):
        status, settings = self.bridge.ui("ui/api/settings/network/add",
                                          {"host": self.host, "port": self.port, "name": "Dock 2"})
        self.assertEqual(status, 201)
        uid = f"{self.host}:{self.port}"
        self.assertEqual(settings["network_printers"]["user"][0]["uid"], uid)
        self.assertEqual(self.available(), [uid])
        device = dict(DEVICE, uid=uid)
        self.assertEqual(self.bridge.request("POST", "write", {"device": device, "data": "^XA^FDdock^FS^XZ"})[0], 200)
        self.assertTrue(self.bridge.printer.wait_for(b"^XA^FDdock^FS^XZ"))

        status, _ = self.bridge.ui("ui/api/settings/network/add", {"host": self.host, "port": self.port})
        self.assertEqual(status, 409)

        self.bridge.ui("ui/api/settings/network/remove", {"uid": uid})
        self.assertEqual(self.available(), [])

    def test_config_printers_cannot_be_removed_from_the_viewer(self):
        self.bridge.close()
        self.bridge = BridgeFixture()
        status, body = self.bridge.ui("ui/api/settings/network/remove", {"uid": "mock-1"})
        self.assertEqual(status, 404)
        self.assertIn("added in the viewer", body)
        settings = self.bridge.ui("ui/api/settings")[1]
        self.assertEqual(settings["network_printers"]["config"][0]["uid"], "mock-1")

    def test_invalid_network_printer(self):
        self.assertEqual(self.bridge.ui("ui/api/settings/network/add", {"host": "bad host!"})[0], 400)
        self.assertEqual(self.bridge.ui("ui/api/settings/network/add", {"host": "10.0.0.1", "port": 70000})[0], 400)
        self.assertEqual(self.bridge.ui("ui/api/settings/network/add", {"host": "10.0.0.1", "port": "x"})[0], 400)

    def test_connection_test(self):
        self.bridge.printer.errors = ["media_out"]
        status, result = self.bridge.ui("ui/api/settings/network/test", {"host": self.host, "port": self.port})
        self.assertEqual(status, 200)
        self.assertTrue(result["ok"])
        self.assertIn("ERRORS:         1 00000000 00000001", result["response"])
        status, result = self.bridge.ui("ui/api/settings/network/test", {"host": "127.0.0.1", "port": 1})
        self.assertEqual(status, 200)
        self.assertFalse(result["ok"])
        self.assertTrue(result["error"])

    def test_default_printer_from_the_viewer(self):
        self.bridge.close()
        self.bridge = BridgeFixture(virtual_printers=True)
        _, _, body = self.bridge.request("GET", "default?type=printer")
        self.assertEqual(json.loads(body)["uid"], "mock-1")
        status, settings = self.bridge.ui("ui/api/settings/default", {"uid": "virtual:zd421-203"})
        self.assertEqual(settings["default_printer"]["effective"], "virtual:zd421-203")
        _, _, body = self.bridge.request("GET", "default?type=printer")
        self.assertEqual(json.loads(body)["uid"], "virtual:zd421-203")
        self.assertEqual(self.bridge.ui("ui/api/settings/default", {"uid": "missing"})[0], 404)
        self.bridge.ui("ui/api/settings/default", {"uid": None})
        _, _, body = self.bridge.request("GET", "default?type=printer")
        self.assertEqual(json.loads(body)["uid"], "mock-1")

    def test_service_info(self):
        service = self.bridge.ui("ui/api/settings")[1]["service"]
        self.assertTrue(service["settings_file"].endswith("settings.json"))
        self.assertEqual(service["config_file"], "/nonexistent/config.json")


class StopTest(unittest.TestCase):
    def test_stop_from_the_viewer(self):
        bridge = BridgeFixture()
        try:
            status, _ = bridge.ui("ui/api/shutdown", {})
            self.assertEqual(status, 200)
            self.assertTrue(bridge.server.stopped_from_viewer)
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline:
                try:
                    urllib.request.urlopen(bridge.base + "available", timeout=0.5).close()
                except (urllib.error.URLError, OSError):
                    break
                time.sleep(0.1)
            else:
                self.fail("the server kept answering after the stop")
        finally:
            bridge.close()

    def test_stop_requires_the_viewer_origin(self):
        bridge = BridgeFixture()
        try:
            status, _, _ = bridge.request("POST", "ui/api/shutdown", {}, content_type="application/json")
            self.assertEqual(status, 403)
            self.assertFalse(bridge.server.stopped_from_viewer)
        finally:
            bridge.close()


if __name__ == "__main__":
    unittest.main()
