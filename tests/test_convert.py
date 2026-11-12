import http.server
import io
import json
import struct
import threading
import unittest
import uuid
import zlib
from functools import partial
from unittest import mock

from tests.helpers import ORIGIN, BridgeFixture
from zebra_print_bridge import convert
from zebra_print_bridge.convert import Bitmap, ConversionError

try:
    from PIL import Image
except ImportError:  # pragma: no cover
    Image = None

DEVICE = {"name": "Mock ZD421", "uid": "mock-1", "connection": "network", "deviceType": "printer", "version": 2}


def png(width, height, rows, color=0, depth=8, palette=None, trns=None):
    """Minimal PNG encoder (filter 0) for the tests that do not depend on Pillow."""

    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))

    raw = b"".join(b"\x00" + bytes(row) for row in rows)
    out = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, depth, color, 0, 0, 0))
    if palette:
        out += chunk(b"PLTE", b"".join(bytes(c) for c in palette))
    if trns:
        out += chunk(b"tRNS", trns)
    return out + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b"")


# 16 x 2: left half black, right half white; second row inverted.
CHECKER = png(16, 2, [[0] * 8 + [255] * 8, [255] * 8 + [0] * 8])


def multipart(fields):
    """(content type, body) like the browser's FormData: {"name": (bytes, content type)}."""
    boundary = f"----bridge{uuid.uuid4().hex}"
    body = b""
    for name, (data, ctype) in fields.items():
        filename = '; filename="blob"' if ctype else ""
        body += (
            (
                f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"{filename}\r\n'
                + (f"Content-Type: {ctype}\r\n" if ctype else "")
                + "\r\n"
            ).encode()
            + data
            + b"\r\n"
        )
    return f"multipart/form-data; boundary={boundary}", body + f"--{boundary}--\r\n".encode()


class DecodeTest(unittest.TestCase):
    def test_minimal_png(self):
        bitmap = convert.decode_png(CHECKER)
        self.assertEqual((bitmap.width, bitmap.height), (16, 2))
        self.assertEqual(list(bitmap.pixels), [0] * 8 + [255] * 8 + [255] * 8 + [0] * 8)

    def test_png_transparency_is_white(self):
        # Gray + alpha: a black pixel that is fully transparent, one that is opaque.
        bitmap = convert.decode_png(png(2, 1, [[0, 0, 0, 255]], color=4))
        self.assertEqual(list(bitmap.pixels), [255, 0])
        # Palette with tRNS.
        bitmap = convert.decode_png(png(2, 1, [[0, 1]], color=3, palette=[(0, 0, 0), (0, 0, 0)], trns=b"\x00\xff"))
        self.assertEqual(list(bitmap.pixels), [255, 0])

    def test_png_1_bit(self):
        bitmap = convert.decode_png(png(10, 1, [[0b10101010, 0b11000000]], depth=1))
        self.assertEqual(list(bitmap.pixels), [255, 0, 255, 0, 255, 0, 255, 0, 255, 255])

    def test_invalid_files(self):
        for data in (b"", b"\x89PNG\r\n\x1a\n", CHECKER[:40], b"BM" + b"\x00" * 10):
            with self.subTest(data=data[:12]), self.assertRaises(ConversionError):
                convert.decode(data, convert.detect_format(data, "png"))
        with self.assertRaises(ConversionError):
            convert.decode(b"plain text", "txt")

    def test_detect_format(self):
        self.assertEqual(convert.detect_format(CHECKER), "png")
        self.assertEqual(convert.detect_format(b"%PDF-1.7 ..."), "pdf")
        self.assertEqual(convert.detect_format(b"????", "image/jpeg"), "jpg")
        self.assertEqual(convert.detect_format(b"????", ".TIFF"), "tif")
        self.assertEqual(convert.detect_format(b"^XA^XZ", "text/plain"), "plain")
        self.assertFalse(convert.is_convertible(b"^XA^FDx^FS^XZ", "text/plain"))

    @unittest.skipUnless(Image, "Pillow is not installed")
    def test_native_decoders_match_pillow(self):
        """PNG (every filter Pillow chooses, several color types) and BMP decoded without Pillow."""
        source = Image.new("RGBA", (37, 23))
        source.putdata(
            [
                ((x * 7) % 256, (y * 11) % 256, (x * y) % 256, 255 if (x + y) % 5 else 90)
                for y in range(23)
                for x in range(37)
            ]
        )
        cases = [
            ("PNG", "RGBA"),
            ("PNG", "RGB"),
            ("PNG", "L"),
            ("PNG", "LA"),
            ("PNG", "P"),
            ("PNG", "1"),
            ("BMP", "RGB"),
            ("BMP", "L"),
            ("BMP", "P"),
            ("BMP", "1"),
        ]
        for fmt, mode in cases:
            with self.subTest(fmt=fmt, mode=mode):
                buffer = io.BytesIO()
                source.convert(mode).save(buffer, fmt)
                data = buffer.getvalue()
                expected = convert._decode_pillow(data)
                native = (convert.decode_png if fmt == "PNG" else convert.decode_bmp)(data)
                self.assertEqual((native.width, native.height), (37, 23))
                worst = max(abs(a - b) for a, b in zip(native.pixels, expected.pixels, strict=True))
                self.assertLessEqual(worst, 2)


class SizeTest(unittest.TestCase):
    bitmap = Bitmap(400, 200, bytearray(400 * 200))

    def size(self, **options):
        return convert.target_size(self.bitmap, options)

    def test_options(self):
        self.assertEqual(self.size(), (400, 200))
        self.assertEqual(self.size(fitTo={"width": 812, "height": 1218}), (812, 406))
        self.assertEqual(self.size(fitTo={"width": 812, "height": 100}), (200, 100))
        self.assertEqual(self.size(fitTo={"width": 100}), (100, 50))
        self.assertEqual(self.size(resize={"width": 50, "height": 50}), (50, 50))
        self.assertEqual(self.size(resize={"height": 50}), (100, 50))
        self.assertEqual(self.size(scale=0.5), (200, 100))
        for bad in ({"scale": "big"}, {"scale": 0}, {"fitTo": {"width": -3}}):
            with self.subTest(bad=bad), self.assertRaises(ConversionError):
                self.size(**bad)

    def test_shrinking_averages(self):
        stripes = Bitmap(4, 1, bytearray([0, 255, 0, 255]))
        self.assertEqual(list(convert.resample(stripes, 2, 1).pixels), [127, 127])
        self.assertEqual(list(convert.resample(stripes, 8, 1).pixels), [0, 0, 255, 255, 0, 0, 255, 255])


class ZplTest(unittest.TestCase):
    def test_graphic_field(self):
        bitmap = convert.decode_png(CHECKER)
        self.assertEqual(convert.graphic_field(bitmap), "^GFA,4,4,2,FF0000FF")
        self.assertEqual(convert.label(bitmap), "^XA^FO0,0^GFA,4,4,2,FF0000FF^FS^XZ\n")

    def test_storage_name(self):
        self.assertEqual(convert.storage_name(""), "E:IMAGE.GRF")
        self.assertEqual(convert.storage_name("logo"), "E:LOGO.GRF")
        self.assertEqual(convert.storage_name("R:company-logo.png"), "R:COMPANYL.GRF")

    def test_convert_actions(self):
        printed = convert.convert(CHECKER, {})
        self.assertEqual((printed["action"], printed["zpl"]), ("print", "^XA^FO0,0^GFA,4,4,2,FF0000FF^FS^XZ\n"))
        stored = convert.convert(CHECKER, {"action": "store", "storageName": "logo"})
        self.assertEqual((stored["path"], stored["zpl"]), ("E:LOGO.GRF", "~DGE:LOGO.GRF,4,2,FF0000FF\n"))
        self.assertEqual(convert.convert(b"raw", {"toFormat": "none"})["raw"], b"raw")
        for options in ({"toFormat": "cpcl"}, {"action": "fax"}, {"toFormat": "none", "action": "return"}):
            with self.subTest(options=options), self.assertRaises(ConversionError):
                convert.convert(CHECKER, options)

    def test_without_pillow(self):
        with mock.patch.object(convert, "_PIL", None):
            self.assertEqual(convert.convert(CHECKER, {})["size"], [16, 2])
            self.assertNotIn("jpg", convert.supported_conversions())
            with self.assertRaisesRegex(ConversionError, "Pillow"):
                convert.convert(b"\xff\xd8\xff\xe0 jpeg", {})

    @unittest.skipUnless(Image and convert.pdf_available(), "needs Pillow (to make the PDF) and pdftoppm")
    def test_pdf_one_label_per_page(self):
        pages = [Image.new("L", (100, 50), 255), Image.new("L", (100, 50), 0)]
        buffer = io.BytesIO()
        pages[0].save(buffer, "PDF", save_all=True, append_images=pages[1:], resolution=72)
        result = convert.convert(buffer.getvalue(), {"fitTo": {"width": 200}}, dpi=203)
        self.assertEqual(result["pages"], 2)
        self.assertEqual(result["zpl"].count("^XA"), 2)
        self.assertEqual(result["size"][0], 200)
        with self.assertRaisesRegex(ConversionError, "single-page"):
            convert.convert(buffer.getvalue(), {"action": "store"})


class _Files(http.server.SimpleHTTPRequestHandler):
    FILES = {"/label.png": (CHECKER, "image/png"), "/label.zpl": (b"^XA^FDfrom url^FS^XZ", "text/plain")}

    def do_GET(self):
        data, ctype = self.FILES.get(self.path, (None, None))
        if data is None:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):
        pass


class EndpointTest(unittest.TestCase):
    def setUp(self):
        self.bridge = BridgeFixture()

    def tearDown(self):
        self.bridge.close()

    def post_form(self, path, payload, blob, blob_type="application/octet-stream"):
        fields = {"json": (json.dumps(payload).encode(), None)}
        if blob is not None:
            fields["blob"] = (blob, blob_type)
        ctype, body = multipart(fields)
        import urllib.error
        import urllib.request

        req = urllib.request.Request(
            self.bridge.base + path, data=body, method="POST", headers={"Content-Type": ctype, "Origin": ORIGIN}
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                return resp.status, resp.read().decode()
        except urllib.error.HTTPError as exc:
            with exc:
                return exc.code, exc.read().decode()

    def test_config_lists_conversions(self):
        _, _, body = self.bridge.request("GET", "config")
        conversions = json.loads(body)["application"]["supportedConversions"]
        self.assertEqual(conversions["png"], ["zpl"])
        self.assertEqual(conversions["bmp"], ["zpl"])

    def test_send_file_is_written_as_it_is(self):
        data = b"^XA^FDfile \xc3\xb1^FS^XZ\x00\xff"
        status, body = self.post_form("write", {"device": DEVICE}, data)
        self.assertEqual((status, body), (200, ""))
        self.assertTrue(self.bridge.printer.wait_for(data))
        _, state = self.bridge.ui("ui/api/state")
        self.assertEqual(state["jobs"][0]["uid"], "mock-1")

    def test_convert_print_return_store(self):
        status, body = self.post_form(
            "convert", {"device": DEVICE, "options": {"action": "print"}}, CHECKER, "image/png"
        )
        self.assertEqual((status, json.loads(body)), (200, ""))
        self.assertTrue(self.bridge.printer.wait_for(b"^GFA,4,4,2,FF0000FF"))

        status, body = self.post_form("convert", {"options": {"action": "return", "fitTo": {"width": 32}}}, CHECKER)
        self.assertEqual(status, 200)
        self.assertTrue(json.loads(body).startswith("^XA^FO0,0^GFA,16,16,4,"))

        options = {"action": "store", "storageName": "logo"}
        status, body = self.post_form("convert", {"device": DEVICE, "options": options}, CHECKER)
        self.assertEqual((status, json.loads(body)), (200, "E:LOGO.GRF"))
        self.assertTrue(self.bridge.printer.wait_for(b"~DGE:LOGO.GRF,4,2,FF0000FF"))

    def test_convert_errors_are_readable(self):
        cases = [
            ({"device": DEVICE}, None, "Resource not specified"),
            ({"device": DEVICE, "options": {"toFormat": "cpcl"}}, CHECKER, "only zpl"),
            ({"device": DEVICE}, b"not an image", "Unsupported format"),
            ({"options": {"action": "print"}}, CHECKER, "Missing device.uid"),
            ({"device": dict(DEVICE, uid="missing")}, CHECKER, "Printer not found"),
        ]
        for payload, blob, message in cases:
            with self.subTest(message=message):
                status, body = self.post_form("convert", payload, blob)
                self.assertIn(status, (400, 404))
                self.assertIn(message, body)

    def serve_files(self):
        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), partial(_Files))
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        return f"http://127.0.0.1:{server.server_address[1]}"

    def test_send_url(self):
        base = self.serve_files()
        status, _, _ = self.bridge.request("POST", "write", {"device": DEVICE, "url": f"{base}/label.zpl"})
        self.assertEqual(status, 200)
        self.assertTrue(self.bridge.printer.wait_for(b"^XA^FDfrom url^FS^XZ"))

        status, _, _ = self.bridge.request(
            "POST", "write", {"device": DEVICE, "url": f"{base}/label.png", "options": {"scale": 2}}
        )
        self.assertEqual(status, 200)
        self.assertTrue(self.bridge.printer.wait_for(b"^GFA,16,16,4,"))

        status, headers, body = self.bridge.request("POST", "write", {"device": DEVICE, "url": f"{base}/missing"})
        self.assertEqual(status, 502)
        self.assertIn("HTTP 404", body)
        self.assertEqual(headers["Access-Control-Allow-Origin"], ORIGIN)

        status, _, body = self.bridge.request("POST", "write", {"device": DEVICE, "url": "file:///etc/passwd"})
        self.assertEqual(status, 400)
        self.assertIn("http and https", body)


if __name__ == "__main__":
    unittest.main()
