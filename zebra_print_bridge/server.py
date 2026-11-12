"""HTTP server compatible with the Zebra Browser Print protocol (BrowserPrint-3.1.250)."""

import json
import logging
import urllib.error
import urllib.request
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

from . import __version__, convert, ui
from .config import state_dir
from .devices import PrinterNotFound, Registry
from .jobs import JobLog
from .labelary import LabelaryClient
from .printer_status import StatusMonitor
from .settings import OriginPolicy
from .web import HttpError

log = logging.getLogger(__name__)

MAX_BODY = 64 * 1024 * 1024
MAX_URL_DOWNLOAD = 20 * 1024 * 1024
URL_TIMEOUT = 15


class BridgeServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, config: dict, registry: Registry = None):
        self.config = config
        self.registry = registry or Registry(config)
        history = (config["history_file"] or state_dir() / "history.jsonl") if config["persist_history"] else None
        self.revision = ui.Revision()
        self.jobs = JobLog(
            config["max_jobs"],
            config["encoding"],
            path=history,
            max_bytes=int(config["history_max_mb"] * 1024 * 1024),
            on_change=self.revision.bump,
        )
        self.status = StatusMonitor(self.registry, config["encoding"])
        self.labelary = LabelaryClient(config["labelary_url"]) if config["labelary_url"] else None
        self.stopped_from_viewer = False
        super().__init__((config["host"], config["port"]), Handler)
        self.viewer_url = f"http://127.0.0.1:{self.server_address[1]}/ui"
        self.origins = OriginPolicy(config, self.registry.settings, self.viewer_url)

    def dpi(self, uid: str) -> int:
        """The printer's density: what it reported (~HI) or, until then, what we assume."""
        return self.status.dpi(uid) or self.registry.label_settings(uid)["dpi"]

    def server_close(self):
        super().server_close()
        self.registry.close()


@dataclass
class DeviceRequest:
    """A Browser Print request about one device: a JSON body, or multipart (sendFile, convert)."""

    payload: dict  # the JSON object ({"device": ..., "data"/"url"/"options": ...})
    uid: str = None  # device.uid, None when missing
    multipart: bool = False
    blob: bytes = None  # multipart "blob" part (the file)
    blob_type: str = ""  # its declared type, e.g. image/png

    def require_uid(self) -> str:
        if self.uid is None:
            raise HttpError(400, "Missing device.uid")
        return self.uid

    def options(self) -> dict:
        options = self.payload.get("options")
        return options if isinstance(options, dict) else {}


class Handler(BaseHTTPRequestHandler):
    server_version = f"ZebraPrintBridge/{__version__}"
    protocol_version = "HTTP/1.1"

    # --- plumbing (also used by ui.py) ----------------------------------------------

    def log_message(self, fmt, *args):
        log.debug("%s %s", self.address_string(), fmt % args)

    def _cors_headers(self):
        origin = self.headers.get("Origin")
        # Sent on every response, errors included: without it the browser hides the
        # body and the web app shows an empty error.
        self.send_header("Access-Control-Allow-Origin", origin or "*")
        self.send_header("Vary", "Origin")

    def reply(self, status: int, body="", content_type="text/plain; charset=utf-8", cors=True, headers=None):
        data = body if isinstance(body, bytes) else body.encode("utf-8")
        if status >= 400:
            # The request body may not have been read: do not reuse the connection.
            self.close_connection = True
        self.send_response(status)
        if cors:
            self._cors_headers()
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(data)

    def reply_json(self, payload):
        self.reply(200, json.dumps(payload), "application/json")

    def read_body(self) -> bytes:
        length = int(self.headers.get("Content-Length") or 0)
        if length > MAX_BODY:
            raise HttpError(413, "Request too large")
        return self.rfile.read(length) if length else b""

    def _dispatch(self, routes: dict):
        path = urlsplit(self.path).path.rstrip("/") or "/"
        if path == "/" and self.command == "GET":
            self.reply(302, "", cors=False, headers={"Location": "/ui"})
            return
        if path == "/ui" or path.startswith("/ui/"):
            ui.handle(self, path)
            return
        origin = self.headers.get("Origin")
        decision = self.server.origins.decide(origin)
        if decision != "allowed":
            log.warning("Website %s: %s %s", decision, origin, path)
            # With CORS headers, so the web app can show how to approve the website.
            self.reply(403, self.server.origins.message(origin, decision))
            return
        handler = routes.get(path)
        try:
            if handler is None:
                raise HttpError(404, f"Unknown endpoint: {path}")
            handler()
        except HttpError as exc:
            log.warning("%s %s -> %s %s", self.command, path, exc.status, exc.message)
            self.reply(exc.status, exc.message)
        except PrinterNotFound as exc:
            log.warning("Printer not found: %s", exc)
            self.reply(404, f"Printer not found: {exc}")
        except Exception as exc:  # printer or unexpected error: readable text for the user
            log.exception("%s %s failed", self.command, path)
            self.reply(500, str(exc) or exc.__class__.__name__)

    # --- HTTP methods ------------------------------------------------------------

    def do_OPTIONS(self):
        path = urlsplit(self.path).path
        # A pending website passes the preflight so its real request can return the
        # "approve this website" message; a blocked one does not.
        if path.startswith("/ui") or self.server.origins.decide(self.headers.get("Origin")) == "denied":
            self.reply(403, "Origin not allowed", cors=False)
            return
        self.send_response(204)
        self._cors_headers()
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", self.headers.get("Access-Control-Request-Headers") or "*")
        if self.headers.get("Access-Control-Request-Private-Network", "").lower() == "true":
            self.send_header("Access-Control-Allow-Private-Network", "true")
        self.send_header("Access-Control-Max-Age", "600")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self):
        self._dispatch(
            {
                "/default": self._default,
                "/available": self._available,
                "/config": self._config,
            }
        )

    def do_POST(self):
        self._dispatch(
            {
                "/write": self._write,
                "/read": self._read,
                "/convert": self._convert,
                "/convert/scan": self._unsupported_scan,
            }
        )

    # --- Browser Print endpoints ---------------------------------------------------

    def _default(self):
        # Always 200: when /default fails the web app disables printing without telling anyone.
        info = self.server.registry.default()
        self.reply(
            200,
            json.dumps(info.to_device()) if info else "",
            "application/json" if info else "text/plain; charset=utf-8",
        )

    def _available(self):
        printers = self.server.registry.available()
        self.reply_json({"printer": [info.to_device() for info in printers]})

    def _config(self):
        self.reply_json(
            {
                "application": {
                    "version": __version__,
                    "build_number": 1,
                    "api_level": 2,
                    "platform": "linux",
                    "supportedConversions": convert.supported_conversions(),
                }
            }
        )

    def _device_request(self) -> DeviceRequest:
        """JSON body ({"device": ..., ...}) or, for sendFile/convert, multipart "json" + "blob"."""
        content_type = self.headers.get("Content-Type", "")
        body = self.read_body()
        multipart = content_type.lower().startswith("multipart/")
        fields = {}
        try:
            if multipart:
                fields = convert.parse_multipart(content_type, body)
                payload = convert.parse_form_json(fields)
            else:
                payload = json.loads(body or b"{}")
        except convert.ConversionError as exc:
            raise HttpError(400, str(exc)) from None
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise HttpError(400, "Invalid JSON body") from None
        if not isinstance(payload, dict):
            raise HttpError(400, "Invalid JSON body")
        uid = (payload.get("device") or {}).get("uid")
        return DeviceRequest(
            payload=payload,
            uid=None if uid in (None, "") else str(uid),
            multipart=multipart,
            blob=fields.get("blob"),
            blob_type=fields.get("blob.type", ""),
        )

    def _write(self):
        request = self._device_request()
        uid = request.require_uid()
        payload = request.payload
        if request.multipart:  # sendFile: the file goes to the printer as it is
            if request.blob is None:
                raise HttpError(400, "Missing blob")
            self.write_to_printer(uid, request.blob)
        elif "url" in payload:  # sendUrl: images and PDFs are converted, anything else is sent as it is
            data, content_type = self._download(str(payload["url"]))
            options = request.options()
            if convert.is_convertible(data, options.get("fromFormat") or content_type):
                self._convert_and_apply(uid, data, dict(options, action="print"))
            else:
                self.write_to_printer(uid, data)
        elif "data" in payload:
            self.write_to_printer(uid, str(payload["data"]))
        else:
            raise HttpError(400, "Missing data")
        self.reply(200, "")

    def _download(self, url: str) -> tuple:
        if urlsplit(url).scheme.lower() not in ("http", "https"):
            raise HttpError(400, "sendUrl only accepts http and https URLs")
        request = urllib.request.Request(url, headers={"User-Agent": f"zebra-print-bridge/{__version__}"})
        try:
            with urllib.request.urlopen(request, timeout=URL_TIMEOUT) as response:
                if urlsplit(response.geturl()).scheme.lower() not in ("http", "https"):
                    raise HttpError(400, "sendUrl only accepts http and https URLs")
                data = response.read(MAX_URL_DOWNLOAD + 1)
                content_type = response.headers.get("Content-Type", "")
        except urllib.error.HTTPError as exc:
            exc.close()
            raise HttpError(502, f"Could not download {url}: HTTP {exc.code}") from None
        except (urllib.error.URLError, OSError) as exc:
            raise HttpError(502, f"Could not download {url}: {getattr(exc, 'reason', exc)}") from None
        if len(data) > MAX_URL_DOWNLOAD:
            raise HttpError(413, f"{url} is larger than {MAX_URL_DOWNLOAD // (1024 * 1024)} MB")
        log.info("sendUrl %s: %d bytes (%s)", url, len(data), content_type or "no type")
        return data, content_type

    def _convert(self):
        """convert / convertAndSendFile / printImageAsLabel / getConvertedResource / storeConvertedResource.

        The library parses the response as JSON: the ZPL for "return", the printer path for
        "store" and an empty string for "print".
        """
        request = self._device_request()
        if not request.blob:
            raise HttpError(400, "Resource not specified")
        options = request.options()
        if not options.get("fromFormat") and request.blob_type.startswith(("image/", "application/pdf")):
            options = dict(options, fromFormat=request.blob_type)
        result = self._convert_and_apply(request.uid, request.blob, options)
        self.reply(200, json.dumps(result), "application/json")

    def _convert_and_apply(self, uid, data: bytes, options: dict):
        try:
            action = convert.normalize_options(options)["action"]
            if action != "return" and uid is None:
                raise HttpError(400, "Missing device.uid")
            result = convert.convert(data, options, self.server.dpi(uid) if uid else 203)
        except convert.ConversionError as exc:
            raise HttpError(400, str(exc)) from None
        if "raw" in result:
            self.write_to_printer(uid, result["raw"])
            return ""
        log.info(
            "convert %s -> %s: %d page(s), %d x %d dots",
            result["format"],
            result["action"],
            result["pages"],
            *result["size"],
        )
        if result["action"] == "return":
            return result["zpl"]
        self.write_to_printer(uid, result["zpl"])
        return result.get("path", "")

    def write_to_printer(self, uid: str, text, from_web: bool = True) -> dict:
        """Send text (encoded with `encoding`) or bytes (as they are) to the printer and record
        it in the viewer (failures too). Returns the job."""
        channel = self.server.registry.channel(uid)
        encoding = self.server.config["encoding"]
        if isinstance(text, bytes):
            data, text = text, text.decode(encoding, errors="replace")
        else:
            data = text.encode(encoding)
        log.info("write %s (%s): %d bytes", channel.info.name, channel.info.uid, len(data))
        try:
            # Only the web app follows a write with a read (sendThenRead).
            channel.write(data, expect_read=from_web)
        except OSError as exc:
            message = f"Error writing to printer {channel.info.name}: {exc}"
            self.server.jobs.record(channel.info, text, error=message)
            raise HttpError(500, message) from None
        return self.server.jobs.record(channel.info, text)

    def _read(self):
        request = self._device_request()
        channel = self.server.registry.channel(request.uid)
        data = channel.read()
        log.info("read %s (%s): %d bytes", channel.info.name, channel.info.uid, len(data))
        text = data.decode(self.server.config["encoding"], errors="replace")
        self.server.jobs.attach_response(channel.info.uid, text)
        self.reply(200, text)

    def _unsupported_scan(self):
        self.read_body()
        raise HttpError(400, "scanImage is not supported by zebra-print-bridge")
