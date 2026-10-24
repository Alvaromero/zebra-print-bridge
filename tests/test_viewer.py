import json
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from tests.helpers import BridgeFixture
from zebra_print_bridge.ui import parse_labelary_warnings

VIRTUAL = {"name": "Virtual ZD421-203dpi", "uid": "virtual:zd421-203", "connection": "driver",
           "deviceType": "printer", "version": 2}
MOCK = {"name": "Mock ZD421", "uid": "mock-1", "connection": "network", "deviceType": "printer", "version": 2}
ICON = "~DGR:BOX.GRF,00004,001,\nFF00FF00\n"


class FakeLabelary(ThreadingHTTPServer):
    def __init__(self):
        self.requests = []
        super().__init__(("127.0.0.1", 0), FakeLabelaryHandler)
        threading.Thread(target=self.serve_forever, daemon=True).start()

    @property
    def url(self):
        return f"http://127.0.0.1:{self.server_address[1]}"


class FakeLabelaryHandler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_POST(self):
        body = self.rfile.read(int(self.headers["Content-Length"])).decode()
        self.server.requests.append((self.path, dict(self.headers), body))
        headers = {}
        if "^FDbad" in body:
            payload, status, ctype = b"ERROR: bad ZPL", 400, "text/plain"
        elif "^FDslow" in body:
            payload, status, ctype = b"ERROR: Request rate limit exceeded", 429, "text/plain"
        elif self.headers["Accept"] == "application/pdf":
            payload, status, ctype = b"%PDF fake", 200, "application/pdf"
        else:
            payload, status, ctype = b"\x89PNG fake", 200, "image/png"
            headers["X-Total-Count"] = "1"
            # Warning at the position of "^GB" in the body Labelary received.
            position = body.find("^GB")
            if position >= 0:
                headers["X-Warnings"] = f"{position}|3|^GB|2|Value 1 is less than minimum value 3"
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(payload)))
        for key, value in headers.items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(payload)


class VirtualPrinterTest(unittest.TestCase):
    def setUp(self):
        self.labelary = FakeLabelary()
        self.bridge = BridgeFixture(virtual_printers=True, labelary_url=self.labelary.url)

    def tearDown(self):
        self.bridge.close()
        self.labelary.shutdown()
        self.labelary.server_close()

    def job_ids(self):
        _, state = self.bridge.ui("ui/api/state")
        return [j["id"] for j in state["jobs"]]

    def preview(self, body):
        data = json.dumps(body).encode()
        import urllib.request
        import urllib.error
        req = urllib.request.Request(self.bridge.base + "ui/api/preview", data=data, method="POST",
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req) as resp:
                return resp.status, resp.headers, resp.read()
        except urllib.error.HTTPError as exc:
            with exc:
                return exc.code, exc.headers, exc.read().decode()

    def test_virtual_printer_is_listed_after_real_ones(self):
        _, _, body = self.bridge.request("GET", "available")
        uids = [p["uid"] for p in json.loads(body)["printer"]]
        self.assertEqual(uids, ["mock-1", "virtual:zd421-203"])
        _, _, body = self.bridge.request("GET", "default?type=printer")
        self.assertEqual(json.loads(body)["uid"], "mock-1")

    def test_virtual_status_is_immediate_and_configurable(self):
        self.bridge.ui("ui/api/virtual/update", {"uid": VIRTUAL["uid"], "errors": ["head_open"]})
        self.bridge.request("POST", "write", {"device": VIRTUAL, "data": "~HQES"})
        start = time.monotonic()
        status, _, body = self.bridge.request("POST", "read", {"device": VIRTUAL})
        self.assertEqual(status, 200)
        self.assertIn("ERRORS:         1 00000000 00000004", body)
        self.assertLess(time.monotonic() - start, 0.3)

    def test_virtual_read_without_response_does_not_wait(self):
        self.bridge.request("POST", "write", {"device": VIRTUAL, "data": "~WC"})
        start = time.monotonic()
        status, _, body = self.bridge.request("POST", "read", {"device": VIRTUAL})
        self.assertEqual((status, body), (200, ""))
        self.assertLess(time.monotonic() - start, 0.3)

    def test_jobs_are_recorded_with_response(self):
        label = "^XA^PW812^LL1218^FO50,50^FDhello^FS^XZ"
        self.bridge.request("POST", "write", {"device": VIRTUAL, "data": label})
        self.bridge.request("POST", "write", {"device": MOCK, "data": "~HQES"})
        self.bridge.request("POST", "read", {"device": MOCK})
        _, state = self.bridge.ui("ui/api/state")
        jobs = state["jobs"]
        self.assertEqual([(j["uid"], j["kind"]) for j in jobs],
                         [("virtual:zd421-203", "label"), ("mock-1", "status")])
        self.assertNotIn("data", jobs[0])
        self.assertIn("PRINTER STATUS", jobs[1]["response"])

        _, detail = self.bridge.ui(f"ui/api/jobs/{jobs[0]['id']}")
        self.assertEqual(detail["data"], label)
        self.assertEqual(detail["label_list"], [{"index": 0, "size": [4.0, 6.0], "graphics": []}])
        self.assertEqual(detail["printer_settings"]["dpi"], 203)

    def test_encoding_warnings_follow_the_printer_charset(self):
        self.bridge.request("POST", "write", {"device": VIRTUAL, "data": "^XA^FDSão Paulo^FS^XZ"})
        self.bridge.request("POST", "write", {"device": VIRTUAL, "data": "^XA^CI28^XZ"})
        self.bridge.request("POST", "write", {"device": VIRTUAL, "data": "^XA^FDSão Paulo^FS^XZ"})
        _, state = self.bridge.ui("ui/api/state")
        warnings = [[w["code"] for w in j["warnings"]] for j in state["jobs"]]
        # The third job inherits ^CI28 from the second one, as the printer would.
        self.assertEqual(warnings, [["not_utf8"], [], []])

    def test_lint_endpoint_for_the_editor(self):
        status, result = self.bridge.ui("ui/api/lint", {"zpl": "^XA^PW406^LL203^FD深圳^FS^XZ", "dpi": 203})
        self.assertEqual(status, 200)
        self.assertEqual([w["code"] for w in result["warnings"]], ["not_utf8", "font_coverage"])
        self.assertEqual(result["label_list"][0]["size"], [2.0, 1.0])

    def test_failed_write_is_recorded(self):
        self.bridge.close()
        self.bridge = BridgeFixture(virtual_printers=True,
                                    network_printers=[{"host": "127.0.0.1", "port": 1, "uid": "off"}],
                                    connect_timeout=0.3)
        status, _, _ = self.bridge.request("POST", "write", {"device": dict(MOCK, uid="off"), "data": "^XA^FDx^FS^XZ"})
        self.assertEqual(status, 500)
        _, state = self.bridge.ui("ui/api/state")
        self.assertTrue(state["jobs"][0]["error"].startswith("Error writing to printer"))

    def test_resend_job_and_send_editor_zpl(self):
        self.bridge.request("POST", "write", {"device": VIRTUAL, "data": "^XA^FDresend^FS^XZ"})
        status, _ = self.bridge.ui("ui/api/print", {"uid": "mock-1", "job_id": self.job_ids()[0]})
        self.assertEqual(status, 200)
        self.assertTrue(self.bridge.printer.wait_for(b"^XA^FDresend^FS^XZ"))
        status, _ = self.bridge.ui("ui/api/print", {"uid": "mock-1", "data": "^XA^FDeditor^FS^XZ"})
        self.assertEqual(status, 200)
        self.assertTrue(self.bridge.printer.wait_for(b"^XA^FDeditor^FS^XZ"))

    def test_add_update_delete_virtual_printer_persists(self):
        status, printer = self.bridge.ui("ui/api/virtual", {"name": "Desk 3 300dpi", "dpi": 300,
                                                            "width": 100 / 25.4, "height": 50 / 25.4})
        self.assertEqual(status, 201)
        self.assertEqual(printer["uid"], "virtual:desk-3-300dpi")
        _, again = self.bridge.ui("ui/api/virtual", {"name": "Desk 3 300dpi", "dpi": 999})
        self.assertEqual(again["uid"], "virtual:desk-3-300dpi-2")
        self.assertEqual(again["dpi"], 203)  # invalid dpi falls back to 203

        _, _, body = self.bridge.request("GET", "available")
        self.assertIn("virtual:desk-3-300dpi", body)
        with open(self.bridge.server.config["virtual_printers_file"]) as fh:
            saved = json.load(fh)
        self.assertEqual(saved[1]["dpi"], 300)

        self.bridge.ui("ui/api/virtual/delete", {"uid": "virtual:desk-3-300dpi"})
        _, _, body = self.bridge.request("GET", "available")
        self.assertNotIn('"virtual:desk-3-300dpi"', body)
        status, _ = self.bridge.ui("ui/api/virtual/delete", {"uid": "virtual:nope"})
        self.assertEqual(status, 404)

    def test_preview_options_and_stored_graphics(self):
        self.bridge.request("POST", "write", {"device": VIRTUAL, "data": ICON})
        self.bridge.request("POST", "write", {"device": VIRTUAL, "data": "^XA^FO1,1^XGR:BOX.GRF,1,1^FS^XZ"})
        job_id = self.job_ids()[-1]
        status, headers, png = self.preview({"job_id": job_id, "index": 0, "dpi": 300, "width": 2,
                                             "height": 1, "rotation": 90, "quality": "bitonal"})
        self.assertEqual(status, 200)
        self.assertEqual(headers["Content-Type"], "image/png")
        self.assertEqual(png, b"\x89PNG fake")
        path, sent_headers, body = self.labelary.requests[-1]
        self.assertEqual(path, "/v1/printers/12dpmm/labels/2x1/0/")
        self.assertEqual(sent_headers["X-Rotation"], "90")
        self.assertEqual(sent_headers["X-Quality"], "Bitonal")
        self.assertEqual(sent_headers["X-Linter"], "On")
        self.assertEqual(body, ICON + "^XA^FO1,1^XGR:BOX.GRF,1,1^FS^XZ")

        # Second time from the cache.
        self.preview({"job_id": job_id, "index": 0, "dpi": 300, "width": 2, "height": 1,
                      "rotation": 90, "quality": "bitonal"})
        self.assertEqual(len(self.labelary.requests), 1)

    def test_pdf_with_every_label(self):
        self.bridge.request("POST", "write", {"device": VIRTUAL, "data": "^XA^FDone^FS^XZ^XA^FDtwo^FS^XZ"})
        status, headers, pdf = self.preview({"job_id": self.job_ids()[0], "all": True, "format": "pdf"})
        self.assertEqual((status, pdf), (200, b"%PDF fake"))
        path, sent_headers, body = self.labelary.requests[-1]
        self.assertEqual(path, "/v1/printers/8dpmm/labels/4x6/")  # no index: every label
        self.assertNotIn("X-Quality", sent_headers)
        self.assertEqual(body, "^XA^FDone^FS^XZ^XA^FDtwo^FS^XZ")

    def test_editor_zpl_preview_and_linter_offsets(self):
        self.bridge.request("POST", "write", {"device": VIRTUAL, "data": ICON})
        zpl_text = "^XA^XGR:BOX.GRF,1,1^FS^FO1,1^GB10,10,1^FS^XZ"
        status, headers, _ = self.preview({"zpl": zpl_text, "uid": VIRTUAL["uid"]})
        self.assertEqual(status, 200)
        warnings = json.loads(headers["X-Labelary-Warnings"])
        # Byte positions refer to the editor text, not to the graphics prepended for Labelary.
        self.assertEqual(warnings, [{"byte": zpl_text.find("^GB"), "size": 3, "command": "^GB",
                                     "parameter": 2, "message": "Value 1 is less than minimum value 3"}])
        self.assertEqual(headers["X-Total-Count"], "1")

    def test_preview_errors_are_readable(self):
        self.bridge.request("POST", "write", {"device": VIRTUAL, "data": "^XA^FDbad^FS^XZ^XA^FDslow^FS^XZ"})
        job_id = self.job_ids()[0]
        self.assertEqual(self.preview({"job_id": job_id, "index": 0})[::2], (502, "Labelary 400: ERROR: bad ZPL"))
        status, _, body = self.preview({"job_id": job_id, "index": 1})
        self.assertEqual(status, 429)
        self.assertIn("rate limit", body)
        self.assertEqual(self.preview({"job_id": job_id, "index": 5})[0], 404)
        self.assertEqual(self.preview({"job_id": job_id, "width": 20})[0], 400)
        self.assertEqual(self.preview({"job_id": job_id, "dpi": 250})[0], 400)

    def test_viewer_page_and_assets(self):
        status, _, body = self.bridge.request("GET", "ui", origin=None)
        self.assertEqual(status, 200)
        self.assertIn("<title>Zebra Print Bridge</title>", body)
        for asset in ("app.js", "i18n.js"):
            status, headers, _ = self.bridge.request("GET", f"ui/assets/{asset}", origin=None)
            self.assertEqual(status, 200)
            self.assertTrue(headers["Content-Type"].startswith("text/javascript"))
        status, _, _ = self.bridge.request("GET", "ui/assets/..%2Fui.py", origin=None)
        self.assertEqual(status, 404)
        status, _, _ = self.bridge.request("GET", "", origin=None)
        self.assertEqual(status, 200)  # urllib follows the redirect from / to /ui


class LabelaryWarningsTest(unittest.TestCase):
    def test_parse_documented_example(self):
        header = "303|1|^GB|2|Value 1 is less than minimum value 3; used 3 instead|591|3|||Ignored unrecognized content"
        self.assertEqual(parse_labelary_warnings(header, 100), [
            {"byte": 203, "size": 1, "command": "^GB", "parameter": 2,
             "message": "Value 1 is less than minimum value 3; used 3 instead"},
            {"byte": 491, "size": 3, "command": "", "parameter": None, "message": "Ignored unrecognized content"},
        ])

    def test_warnings_inside_prepended_graphics_are_dropped(self):
        self.assertEqual(parse_labelary_warnings("10|1|~DG||Bad data", 50), [])
        self.assertEqual(parse_labelary_warnings("", 0), [])


class ViewerSecurityTest(unittest.TestCase):
    def setUp(self):
        self.bridge = BridgeFixture(virtual_printers=True)

    def tearDown(self):
        self.bridge.close()

    def test_allowed_web_origin_cannot_read_viewer(self):
        status, headers, _ = self.bridge.request("GET", "ui/api/state")  # the web app's Origin
        self.assertEqual(status, 403)
        self.assertIsNone(headers["Access-Control-Allow-Origin"])

    def test_cross_origin_mutation_rejected(self):
        status, _, _ = self.bridge.request("POST", "ui/api/jobs/clear", {}, content_type="application/json")
        self.assertEqual(status, 403)
        status, _, _ = self.bridge.request("OPTIONS", "ui/api/jobs/clear",
                                           headers={"Access-Control-Request-Method": "POST"})
        self.assertEqual(status, 403)

    def test_mutation_requires_json(self):
        status, _, _ = self.bridge.request("POST", "ui/api/jobs/clear", {}, origin=None)
        self.assertEqual(status, 415)

    def test_dns_rebinding_host_rejected(self):
        status, _, _ = self.bridge.request("GET", "ui/api/state", origin=None,
                                           headers={"Host": f"evil.example.com:{self.bridge.port}"})
        self.assertEqual(status, 403)


if __name__ == "__main__":
    unittest.main()
