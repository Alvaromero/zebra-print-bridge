"""HTTP server compatible with the Zebra Browser Print protocol (BrowserPrint-3.1.250)."""

import json
import logging
from fnmatch import fnmatchcase
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

from . import __version__, ui
from .devices import PrinterNotFound, Registry
from .jobs import JobLog

log = logging.getLogger(__name__)

MAX_BODY = 64 * 1024 * 1024


class HttpError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


class BridgeServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, config: dict, registry: Registry = None):
        self.config = config
        self.registry = registry or Registry(config)
        self.jobs = JobLog(config["max_jobs"], config["encoding"])
        self.preview_cache = ui.PreviewCache()
        super().__init__((config["host"], config["port"]), Handler)

    def server_close(self):
        super().server_close()
        self.registry.close()


class Handler(BaseHTTPRequestHandler):
    server_version = f"ZebraPrintBridge/{__version__}"
    protocol_version = "HTTP/1.1"

    # --- plumbing --------------------------------------------------------------

    def log_message(self, fmt, *args):
        log.debug("%s %s", self.address_string(), fmt % args)

    def _origin_allowed(self) -> bool:
        origin = self.headers.get("Origin")
        patterns = self.server.config["allowed_origins"]
        if not origin or not patterns:
            return True
        return any(fnmatchcase(origin, pattern) for pattern in patterns)

    def _cors_headers(self):
        origin = self.headers.get("Origin")
        # Sent on every response, errors included: without it the browser hides the
        # body and the web app shows an empty error.
        self.send_header("Access-Control-Allow-Origin", origin or "*")
        self.send_header("Vary", "Origin")

    def _send(self, status: int, body="", content_type="text/plain; charset=utf-8",
              cors=True, headers=None):
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

    def _send_json(self, payload):
        self._send(200, json.dumps(payload), "application/json")

    def _read_body(self) -> bytes:
        length = int(self.headers.get("Content-Length") or 0)
        if length > MAX_BODY:
            raise HttpError(413, "Request too large")
        return self.rfile.read(length) if length else b""

    def _dispatch(self, routes: dict):
        path = urlsplit(self.path).path.rstrip("/") or "/"
        if path == "/" and self.command == "GET":
            self._send(302, "", cors=False, headers={"Location": "/ui"})
            return
        if path == "/ui" or path.startswith("/ui/"):
            ui.handle(self, path)
            return
        if not self._origin_allowed():
            origin = self.headers.get("Origin")
            log.warning("Origin rejected: %s %s", origin, path)
            self._send(403, f"Origin not allowed: {origin}", cors=False)
            return
        handler = routes.get(path)
        try:
            if handler is None:
                raise HttpError(404, f"Unknown endpoint: {path}")
            handler()
        except HttpError as exc:
            log.warning("%s %s -> %s %s", self.command, path, exc.status, exc.message)
            self._send(exc.status, exc.message)
        except PrinterNotFound as exc:
            log.warning("Printer not found: %s", exc)
            self._send(404, f"Printer not found: {exc}")
        except Exception as exc:  # printer or unexpected error: readable text for the user
            log.exception("%s %s failed", self.command, path)
            self._send(500, str(exc) or exc.__class__.__name__)

    # --- HTTP methods ------------------------------------------------------------

    def do_OPTIONS(self):
        path = urlsplit(self.path).path
        if not self._origin_allowed() or path.startswith("/ui"):
            self._send(403, "Origin not allowed", cors=False)
            return
        self.send_response(204)
        self._cors_headers()
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers",
                         self.headers.get("Access-Control-Request-Headers") or "*")
        if self.headers.get("Access-Control-Request-Private-Network", "").lower() == "true":
            self.send_header("Access-Control-Allow-Private-Network", "true")
        self.send_header("Access-Control-Max-Age", "600")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self):
        self._dispatch({
            "/default": self._default,
            "/available": self._available,
            "/config": self._config,
        })

    def do_POST(self):
        self._dispatch({
            "/write": self._write,
            "/read": self._read,
            "/convert": self._unsupported_convert,
            "/convert/scan": self._unsupported_convert,
        })

    # --- Browser Print endpoints ---------------------------------------------------

    def _default(self):
        # Always 200: when /default fails the web app disables printing without telling anyone.
        info = self.server.registry.default()
        self._send(200, json.dumps(info.to_device()) if info else "",
                   "application/json" if info else "text/plain; charset=utf-8")

    def _available(self):
        printers = self.server.registry.available()
        self._send_json({"printer": [info.to_device() for info in printers]})

    def _config(self):
        self._send_json({"application": {
            "version": __version__,
            "build_number": 1,
            "api_level": 2,
            "platform": "linux",
            "supportedConversions": {},
        }})

    def _parse_device_request(self) -> dict:
        content_type = self.headers.get("Content-Type", "")
        if content_type.startswith("multipart/"):
            raise HttpError(400, "sendFile is not supported by zebra-print-bridge")
        try:
            payload = json.loads(self._read_body() or b"{}")
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise HttpError(400, "Invalid JSON body")
        uid = (payload.get("device") or {}).get("uid")
        if uid in (None, ""):
            raise HttpError(400, "Missing device.uid")
        payload["_uid"] = str(uid)
        return payload

    def _write(self):
        payload = self._parse_device_request()
        if "data" not in payload:
            if "url" in payload:
                raise HttpError(400, "sendUrl is not supported by zebra-print-bridge")
            raise HttpError(400, "Missing data")
        self.write_to_printer(payload["_uid"], str(payload["data"]))
        self._send(200, "")

    def write_to_printer(self, uid: str, text: str):
        """Send to the printer and record it in the viewer (failures too)."""
        channel = self.server.registry.channel(uid)
        data = text.encode(self.server.config["encoding"])
        log.info("write %s (%s): %d bytes", channel.info.name, channel.info.uid, len(data))
        try:
            channel.write(data)
        except OSError as exc:
            message = f"Error writing to printer {channel.info.name}: {exc}"
            self.server.jobs.record(channel.info, text, error=message)
            raise HttpError(500, message)
        self.server.jobs.record(channel.info, text)

    def _read(self):
        payload = self._parse_device_request()
        channel = self.server.registry.channel(payload["_uid"])
        data = channel.read()
        log.info("read %s (%s): %d bytes", channel.info.name, channel.info.uid, len(data))
        text = data.decode(self.server.config["encoding"], errors="replace")
        self.server.jobs.attach_response(channel.info.uid, text)
        self._send(200, text)

    def _unsupported_convert(self):
        self._read_body()
        raise HttpError(400, "Conversion is not supported by zebra-print-bridge")
