import json
import logging
import tempfile
import threading
import urllib.error
import urllib.request

from zebra_print_bridge.config import load_config
from zebra_print_bridge.devices import Registry
from zebra_print_bridge.mock_printer import MockPrinter
from zebra_print_bridge.server import BridgeServer

ORIGIN = "https://my.recommercebrands.com"

logging.disable(logging.CRITICAL)


class BridgeFixture:
    """Simulated network printer + bridge on ephemeral ports."""

    def __init__(self, **config_overrides):
        self.tmp = tempfile.TemporaryDirectory()
        self.printer = MockPrinter("127.0.0.1", 0).start()
        host, port = self.printer.address
        overrides = {
            "port": 0,
            "usb": {"enabled": False},
            "network_printers": [{"host": host, "port": port, "name": "Mock ZD421", "uid": "mock-1"}],
            "read_timeout": 1.0,
            "connection_linger": 0.5,
            "virtual_printers": False,
            "virtual_printers_file": f"{self.tmp.name}/virtual-printers.json",
            "settings_file": f"{self.tmp.name}/settings.json",
            "desktop_notifications": False,
        }
        overrides.update(config_overrides)
        config = load_config("/nonexistent/config.json", overrides)
        self.server = BridgeServer(config, Registry(config, discover_usb_fn=lambda: []))
        self.port = self.server.server_address[1]
        self.base = f"http://127.0.0.1:{self.port}/"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def request(self, method, path, body=None, origin=ORIGIN, headers=None,
                content_type="text/plain;charset=UTF-8", raw=False):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.base + path, data=data, method=method)
        if origin:
            req.add_header("Origin", origin)
        if data is not None:
            req.add_header("Content-Type", content_type)
        for key, value in (headers or {}).items():
            req.add_header(key, value)
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                content = resp.read()
                return resp.status, resp.headers, content if raw else content.decode()
        except urllib.error.HTTPError as exc:
            with exc:
                return exc.code, exc.headers, exc.read().decode()

    def ui(self, path, body=None):
        """A viewer request: same origin, JSON."""
        status, headers, content = self.request(
            "POST" if body is not None else "GET", path, body,
            origin=f"http://127.0.0.1:{self.port}" if body is not None else None,
            content_type="application/json")
        return status, json.loads(content) if content and status < 400 else content

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.printer.stop()
        self.tmp.cleanup()
