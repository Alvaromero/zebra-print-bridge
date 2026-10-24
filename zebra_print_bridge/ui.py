"""Web viewer at http://127.0.0.1:9100/ui: virtual printers, sent jobs, ZPL editor and preview.

It only answers the viewer itself: it checks Host (against DNS rebinding) and Origin and
sends no CORS headers, so other websites can neither read nor change it.
"""

import hashlib
import json
import logging
import re
import os
import threading
import time
import urllib.error
import urllib.request
from collections import OrderedDict
from pathlib import Path

from . import __version__, zpl
from .devices import PrinterNotFound
from .settings import SettingsError, network_uid
from .transports import NetworkTransport
from .virtual import DPI_VALUES

log = logging.getLogger(__name__)

STATIC = Path(__file__).parent / "static"
ASSET_TYPES = {".js": "text/javascript; charset=utf-8", ".css": "text/css; charset=utf-8"}
DPMM = {152: 6, 203: 8, 300: 12, 600: 24}
ROTATIONS = (0, 90, 180, 270)
LABELARY_MAX_BODY = 1024 * 1024
LABELARY_MAX_LABELS = 50
STARTED = time.time()


class UiError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


class PreviewCache:
    def __init__(self, size: int = 200):
        self._items = OrderedDict()
        self._size = size
        self._lock = threading.Lock()

    def get(self, key):
        with self._lock:
            if key in self._items:
                self._items.move_to_end(key)
                return self._items[key]
        return None

    def put(self, key, value):
        with self._lock:
            self._items[key] = value
            if len(self._items) > self._size:
                self._items.popitem(last=False)


def handle(h, path: str):
    try:
        _check_request(h)
        if h.command == "GET":
            _get(h, path)
        elif h.command == "POST":
            _post(h, path, _json_body(h))
        else:
            raise UiError(405, "Method not allowed")
    except PrinterNotFound as exc:
        h._send(404, f"Printer not found: {exc}", cors=False)
    except SettingsError as exc:
        h._send(400, str(exc), cors=False)
    except Exception as exc:
        status = getattr(exc, "status", 500)
        message = getattr(exc, "message", None) or str(exc) or exc.__class__.__name__
        if status >= 500 and status != 502:
            log.exception("UI %s %s failed", h.command, path)
        h._send(status, message, cors=False)


def _check_request(h):
    port = h.server.server_address[1]
    host = h.headers.get("Host", "")
    if host not in {f"127.0.0.1:{port}", f"localhost:{port}", f"[::1]:{port}"}:
        raise UiError(403, "The viewer only answers on 127.0.0.1 / localhost")
    origin = h.headers.get("Origin")
    if origin and origin != f"http://{host}":
        raise UiError(403, "Cross-origin requests to the viewer are not allowed")


def _json_body(h) -> dict:
    # Requiring JSON forces a CORS preflight on any other website, and the preflight is rejected.
    if not h.headers.get("Content-Type", "").startswith("application/json"):
        raise UiError(415, "Content-Type must be application/json")
    try:
        return json.loads(h._read_body() or b"{}")
    except ValueError:
        raise UiError(400, "Invalid JSON body")


def _send_json(h, payload, status=200):
    h._send(status, json.dumps(payload, ensure_ascii=False), "application/json", cors=False,
            headers={"Cache-Control": "no-store"})


# --- GET ----------------------------------------------------------------------------

def _get(h, path):
    if path in ("/ui", "/ui/index.html"):  # _dispatch already stripped the trailing slash
        h._send(200, (STATIC / "index.html").read_bytes(), "text/html; charset=utf-8", cors=False,
                headers={"Cache-Control": "no-store"})
        return
    asset = re.fullmatch(r"/ui/assets/([a-z0-9_-]+\.(?:js|css))", path)
    if asset:
        file = STATIC / asset.group(1)
        if not file.is_file():
            raise UiError(404, "Asset not found")
        h._send(200, file.read_bytes(), ASSET_TYPES[file.suffix], cors=False,
                headers={"Cache-Control": "no-store"})
        return
    if path == "/ui/api/state":
        _send_json(h, _state(h.server))
        return
    if path == "/ui/api/settings":
        _send_json(h, _settings(h.server))
        return
    match = re.fullmatch(r"/ui/api/jobs/(\d+)(/zpl)?", path)
    if match:
        job = _job(h.server, match.group(1))
        if match.group(2):
            h._send(200, job["data"], "application/octet-stream", cors=False, headers={
                "Content-Disposition": f'attachment; filename="job-{job["id"]}.zpl"'})
        else:
            _send_json(h, _job_detail(h.server, job))
        return
    raise UiError(404, f"Unknown endpoint: {path}")


def _job(server, job_id):
    job = server.jobs.get(int(job_id))
    if job is None:
        raise UiError(404, "Job not found (the log only keeps the latest jobs)")
    return job


def _printer_settings(server, uid: str) -> dict:
    virtual = server.registry.virtual.get(uid) if server.config["virtual_printers"] else None
    if virtual:
        return {"virtual": True, **{k: virtual[k] for k in ("dpi", "width", "height", "errors", "warnings")}}
    try:
        name = server.registry.channel(uid).info.name
    except PrinterNotFound:
        name = ""
    dpi = next((d for d in (600, 300, 152) if f"{d}dpi" in name.replace(" ", "").lower()), 203)
    return {"virtual": False, "dpi": dpi, "width": 4, "height": 6}


def _state(server) -> dict:
    printers = server.registry.available()
    return {
        "version": __version__,
        "rev": server.jobs.rev,
        "labelary": bool(server.config["labelary_url"]),
        "printers": [dict(info.to_device(), **_printer_settings(server, info.uid)) for info in printers],
        "jobs": server.jobs.summaries(),
        "pending_origins": server.origins.pending(),
        "flags": {
            "errors": {key: value[0] for key, value in zpl.ERROR_FLAGS.items()},
            "warnings": {key: value[0] for key, value in zpl.WARNING_FLAGS.items()},
        },
    }


def _settings(server) -> dict:
    config, settings = server.config, server.registry.settings
    snapshot = settings.snapshot()
    default = server.registry.default()
    return {
        "network_printers": {
            "config": [dict(p, uid=network_uid(p)) for p in config["network_printers"]],
            "user": [dict(p, uid=network_uid(p)) for p in snapshot["network_printers"]],
        },
        "default_printer": {"config": config["default_printer"], "user": snapshot["default_printer"],
                            "effective": default.uid if default else None},
        "origins": {
            "approval": config["origin_approval"],
            "patterns": config["allowed_origins"],
            "allowed": snapshot["origins"]["allowed"],
            "denied": snapshot["origins"]["denied"],
            "pending": server.origins.pending(),
        },
        "service": {
            "version": __version__,
            "pid": os.getpid(),
            "started": STARTED,
            "address": f"http://{config['host']}:{server.server_address[1]}/",
            "config_file": config["_config_file"],
            "settings_file": str(settings.path),
            "virtual_printers_file": str(server.registry.virtual.path),
            "systemd": _under_systemd(),
        },
    }


def _under_systemd() -> bool:
    """True when the parent process is systemd (the --user service), not a terminal."""
    try:
        return Path(f"/proc/{os.getppid()}/comm").read_text().strip() == "systemd"
    except OSError:
        return False


def _test_network_printer(server, host: str, port: int) -> dict:
    """Connect, ask ~HQES and report how long it took. Nothing is printed."""
    transport = NetworkTransport(host, port, server.config["connect_timeout"])
    start = time.monotonic()
    try:
        transport.write(b"~HQES", timeout=5)
        deadline = time.monotonic() + server.config["read_timeout"]
        response = b""
        while time.monotonic() < deadline and b"\x03" not in response:
            chunk = transport.read(deadline - time.monotonic())
            if not chunk:
                break
            response += chunk
    except OSError as exc:
        return {"ok": False, "error": str(exc) or exc.__class__.__name__}
    finally:
        transport.close()
    return {"ok": True, "ms": round((time.monotonic() - start) * 1000),
            "response": response.decode("utf-8", errors="replace")}


def _label_list(text: str, dpi: int) -> list:
    return [
        {"index": i, "size": zpl.label_size_inches(label, dpi),
         "graphics": sorted(zpl.referenced_graphics(label))}
        for i, label in enumerate(zpl.labels(text))
    ]


def _job_detail(server, job) -> dict:
    settings = _printer_settings(server, job["uid"])
    return dict(job, printer_settings=settings, label_list=_label_list(job["data"], settings["dpi"]))


# --- POST ---------------------------------------------------------------------------

def _post(h, path, body):
    server = h.server
    store = server.registry.virtual
    if path == "/ui/api/preview":
        _preview(h, body)
    elif path == "/ui/api/lint":
        text = str(body.get("zpl", ""))
        uid = body.get("uid")
        settings = _printer_settings(server, uid) if uid else {"dpi": int(body.get("dpi", 203))}
        _send_json(h, {
            "warnings": zpl.lint(text, server.jobs.charset(uid) if uid else None, server.config["encoding"]),
            "label_list": _label_list(text, settings["dpi"]),
        })
    elif path == "/ui/api/shutdown":
        _send_json(h, {"ok": True})
        log.info("Stop requested from the viewer")
        server.stopped_from_viewer = True
        # shutdown() waits for serve_forever, so it cannot run on this request's thread.
        threading.Thread(target=server.shutdown, daemon=True).start()
    elif path == "/ui/api/settings/origin":
        server.origins.resolve(str(body.get("origin", "")), str(body.get("action", "")))
        server.jobs.bump()
        _send_json(h, _settings(server))
    elif path == "/ui/api/settings/network/add":
        try:
            port = int(body.get("port") or 9100)
        except (TypeError, ValueError):
            raise UiError(400, "Invalid port")
        candidate = {"host": str(body.get("host", "")).strip(), "port": port, "uid": str(body.get("uid", "")).strip()}
        if candidate["host"] and network_uid(candidate) in {p.uid for p in server.registry.available()}:
            raise UiError(409, f"A printer with uid {network_uid(candidate)} already exists")
        server.registry.settings.add_network_printer(candidate["host"], port, str(body.get("name", "")),
                                                     candidate["uid"])
        server.registry.refresh()
        server.jobs.bump()
        _send_json(h, _settings(server), status=201)
    elif path == "/ui/api/settings/network/remove":
        try:
            server.registry.settings.remove_network_printer(str(body.get("uid", "")))
        except KeyError:
            raise UiError(404, "Only printers added in the viewer can be removed here")
        server.registry.refresh()
        server.jobs.bump()
        _send_json(h, _settings(server))
    elif path == "/ui/api/settings/network/test":
        try:
            port = int(body.get("port") or 9100)
        except (TypeError, ValueError):
            raise UiError(400, "Invalid port")
        host = str(body.get("host", "")).strip()
        if not host:
            raise UiError(400, "host is required")
        _send_json(h, _test_network_printer(server, host, port))
    elif path == "/ui/api/settings/default":
        uid = body.get("uid") or None
        if uid and uid not in {p.uid for p in server.registry.available()}:
            raise UiError(404, f"Printer not found: {uid}")
        server.registry.settings.set_default_printer(uid)
        server.jobs.bump()
        _send_json(h, _settings(server))
    elif path == "/ui/api/jobs/clear":
        server.jobs.clear()
        _send_json(h, {"ok": True})
    elif path == "/ui/api/print":
        uid = body.get("uid")
        data = _job(server, body["job_id"])["data"] if "job_id" in body else str(body.get("data", ""))
        if not uid or not data:
            raise UiError(400, "uid and job_id/data are required")
        h.write_to_printer(str(uid), data)
        _send_json(h, {"ok": True})
    elif path == "/ui/api/virtual":
        name = str(body.get("name", "")).strip()
        if not name:
            raise UiError(400, "name is required")
        printer = store.add(name, dpi=int(body.get("dpi", 203)),
                            width=float(body.get("width", 4)), height=float(body.get("height", 6)))
        server.registry.refresh()
        server.jobs.bump()
        _send_json(h, printer, status=201)
    elif path in ("/ui/api/virtual/update", "/ui/api/virtual/delete"):
        uid = str(body.get("uid", ""))
        try:
            if path.endswith("delete"):
                store.remove(uid)
                result = {"ok": True}
            else:
                changes = {k: body[k] for k in ("name", "dpi", "width", "height", "errors", "warnings")
                           if k in body}
                result = store.update(uid, **changes)
        except KeyError:
            raise UiError(404, f"Virtual printer not found: {uid}")
        server.registry.refresh()
        server.jobs.bump()
        _send_json(h, result)
    else:
        raise UiError(404, f"Unknown endpoint: {path}")


def _preview_options(body) -> dict:
    try:
        options = {
            "dpi": int(body.get("dpi", 203)),
            "width": float(body.get("width", 4)),
            "height": float(body.get("height", 6)),
            "rotation": int(body.get("rotation", 0)),
            "quality": str(body.get("quality", "grayscale")).lower(),
            "format": str(body.get("format", "png")).lower(),
            "lint": bool(body.get("lint", True)),
        }
    except (TypeError, ValueError):
        raise UiError(400, "Invalid preview options")
    if options["dpi"] not in DPI_VALUES:
        raise UiError(400, f"dpi must be one of {DPI_VALUES}")
    if not (0.1 <= options["width"] <= 15 and 0.1 <= options["height"] <= 15):
        raise UiError(400, "Label size must be between 0.1 and 15 inches")
    if options["rotation"] not in ROTATIONS:
        raise UiError(400, "rotation must be 0, 90, 180 or 270")
    if options["quality"] not in ("grayscale", "bitonal") or options["format"] not in ("png", "pdf"):
        raise UiError(400, "quality must be grayscale/bitonal and format png/pdf")
    return options


def _preview_source(server, body):
    """(ZPL to render, Labelary label index or None for every label, printer uid)."""
    every_label = bool(body.get("all"))
    if "job_id" in body:
        job = _job(server, body["job_id"])
        labels = zpl.labels(job["data"])
        if not labels:
            raise UiError(404, "This job has no labels")
        if every_label:
            return "".join(labels[:LABELARY_MAX_LABELS]), None, job["uid"]
        index = int(body.get("index", 0))
        if not 0 <= index < len(labels):
            raise UiError(404, "Label not found")
        return labels[index], 0, job["uid"]
    text = str(body.get("zpl", ""))
    if not text.strip():
        raise UiError(400, "zpl is required")
    return text, None if every_label else int(body.get("index", 0)), body.get("uid")


def parse_labelary_warnings(header: str, offset: int) -> list:
    """X-Warnings: byte|size|command|parameter|message, repeated, pipe-delimited."""
    parts = header.split("|") if header else []
    warnings = []
    for i in range(0, len(parts) - 4, 5):
        byte_index, size, command, parameter, message = parts[i:i + 5]
        try:
            position = int(byte_index) - offset
        except ValueError:
            continue
        if position < 0:
            continue  # inside the ~DG graphics the viewer prepended
        warnings.append({"byte": position, "size": int(size or 0), "command": command,
                         "parameter": int(parameter) if parameter else None, "message": message})
    return warnings


def _preview(h, body):
    server = h.server
    base_url = server.config["labelary_url"]
    if not base_url:
        raise UiError(404, "Preview is disabled (labelary_url is null)")
    options = _preview_options(body)
    text, index, uid = _preview_source(server, body)
    # Graphics synced earlier (~DG) live in the printer's memory: prepend them so ^XG renders.
    prefix = server.jobs.graphics_for(uid, text) if uid else ""
    payload = (prefix + text).encode("utf-8")
    if len(payload) > LABELARY_MAX_BODY:
        raise UiError(413, "Too large for Labelary (1 MB maximum)")
    if index is None and options["format"] == "png":
        index = 0  # PNG is always a single label

    key = hashlib.sha256(payload + json.dumps([index, options], sort_keys=True).encode()).hexdigest()
    cached = server.preview_cache.get(key)
    if cached is None:
        path = f"/v1/printers/{DPMM[options['dpi']]}dpmm/labels/{options['width']:g}x{options['height']:g}/"
        if index is not None:
            path += f"{index}/"
        headers = {
            "Accept": "application/pdf" if options["format"] == "pdf" else "image/png",
            "Content-Type": "application/x-www-form-urlencoded",
            "X-Rotation": str(options["rotation"]),
            "X-Linter": "On" if options["lint"] else "Off",
        }
        if options["format"] == "png":
            headers["X-Quality"] = options["quality"].capitalize()
        request = urllib.request.Request(base_url.rstrip("/") + path, data=payload, method="POST",
                                         headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                cached = (
                    response.read(),
                    response.headers.get("Content-Type", headers["Accept"]),
                    response.headers.get("X-Total-Count", ""),
                    parse_labelary_warnings(response.headers.get("X-Warnings", ""), len(prefix.encode("utf-8"))),
                )
        except urllib.error.HTTPError as exc:
            with exc:
                detail = exc.read().decode(errors="replace").strip()
            if exc.code == 429:
                raise UiError(429, "Labelary rate limit reached (3 requests per second on the free plan). "
                                   "Try again in a moment.")
            raise UiError(502, f"Labelary {exc.code}: {detail[:300]}")
        except OSError as exc:
            raise UiError(502, f"Labelary unreachable: {exc}")
        server.preview_cache.put(key, cached)

    content, content_type, total, warnings = cached
    h._send(200, content, content_type, cors=False, headers={
        "Cache-Control": "no-store",
        "X-Total-Count": total,
        "X-Labelary-Warnings": json.dumps(warnings, ensure_ascii=True),
    })
