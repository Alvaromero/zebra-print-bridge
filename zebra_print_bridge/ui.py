"""Web viewer at http://127.0.0.1:9100/ui: virtual printers, sent jobs, ZPL editor, preview, settings.

It only answers the viewer itself: it checks Host (against DNS rebinding) and Origin and
sends no CORS headers, so other websites can neither read nor change it.

Each endpoint is a function registered with @get or @post; `h` is the request handler
(server.Handler) and `h.server` the BridgeServer.
"""

import json
import logging
import os
import re
import threading
import time
from pathlib import Path

from . import __version__, control, discovery, labelary, zpl
from .devices import PrinterNotFound
from .settings import SettingsError, network_uid
from .transports import NetworkTransport
from .virtual import DPI_VALUES
from .web import HttpError, float_field, int_field, str_field

log = logging.getLogger(__name__)

STATIC = Path(__file__).parent / "static"
ASSET = re.compile(r"/ui/assets/([a-z0-9_-]+\.(?:js|css|svg))")
ASSET_TYPES = {".js": "text/javascript; charset=utf-8", ".css": "text/css; charset=utf-8", ".svg": "image/svg+xml"}
JOB = re.compile(r"/ui/api/jobs/(\d+)(/zpl)?")
ROTATIONS = (0, 90, 180, 270)
STARTED = time.time()
NO_STORE = {"Cache-Control": "no-store"}


class Revision:
    """Changes whenever something the viewer shows changes; it only redraws when it moves."""

    def __init__(self):
        self.value = 0
        self._lock = threading.Lock()

    def bump(self):
        with self._lock:
            self.value += 1


GET_ROUTES = {}  # path -> fn(h)
POST_ROUTES = {}  # path -> fn(h, body)


def get(path):
    def register(fn):
        GET_ROUTES[path] = fn
        return fn

    return register


def post(path):
    def register(fn):
        POST_ROUTES[path] = fn
        return fn

    return register


# --- request handling -------------------------------------------------------------------


def handle(h, path: str):
    try:
        _check_request(h)
        if h.command == "GET":
            _get(h, path)
        elif h.command == "POST":
            body = _json_body(h)
            handler = POST_ROUTES.get(path)
            if handler is None:
                raise HttpError(404, f"Unknown endpoint: {path}")
            handler(h, body)
        else:
            raise HttpError(405, "Method not allowed")
    except PrinterNotFound as exc:
        h.reply(404, f"Printer not found: {exc}", cors=False)
    except SettingsError as exc:
        h.reply(400, str(exc), cors=False)
    except Exception as exc:
        status = exc.status if isinstance(exc, HttpError) else 500
        message = exc.message if isinstance(exc, HttpError) else (str(exc) or exc.__class__.__name__)
        if status >= 500 and status != 502:
            log.exception("UI %s %s failed", h.command, path)
        h.reply(status, message, cors=False)


def _check_request(h):
    port = h.server.server_address[1]
    host = h.headers.get("Host", "")
    if host not in {f"127.0.0.1:{port}", f"localhost:{port}", f"[::1]:{port}"}:
        raise HttpError(403, "The viewer only answers on 127.0.0.1 / localhost")
    origin = h.headers.get("Origin")
    if origin and origin != f"http://{host}":
        raise HttpError(403, "Cross-origin requests to the viewer are not allowed")


def _json_body(h) -> dict:
    # Requiring JSON forces a CORS preflight on any other website, and the preflight is rejected.
    if not h.headers.get("Content-Type", "").startswith("application/json"):
        raise HttpError(415, "Content-Type must be application/json")
    try:
        body = json.loads(h.read_body() or b"{}")
    except ValueError:
        raise HttpError(400, "Invalid JSON body") from None
    if not isinstance(body, dict):
        raise HttpError(400, "Invalid JSON body")
    return body


def _send_json(h, payload, status=200):
    h.reply(status, json.dumps(payload, ensure_ascii=False), "application/json", cors=False, headers=NO_STORE)


def _get(h, path):
    handler = GET_ROUTES.get(path)
    if handler is not None:
        handler(h)
    elif match := ASSET.fullmatch(path):
        _asset(h, match.group(1))
    elif match := JOB.fullmatch(path):
        _job_endpoint(h, int(match.group(1)), raw=bool(match.group(2)))
    else:
        raise HttpError(404, f"Unknown endpoint: {path}")


# --- shared pieces -----------------------------------------------------------------------


def _job(server, job_id: int) -> dict:
    job = server.jobs.get(job_id)
    if job is None:
        raise HttpError(404, "Job not found (the log only keeps the latest jobs)")
    return job


def _known_uids(server) -> set:
    return {p.uid for p in server.registry.available()}


def _port(body) -> int:
    return int_field(body, "port", 9100) or 9100


def _changed(server):
    """After changing printers or settings: refresh the printer list and let the viewer redraw."""
    server.registry.refresh()
    server.revision.bump()


def _label_list(text: str, dpi: int) -> list:
    return [
        {"index": i, "size": zpl.label_size_inches(label, dpi), "graphics": sorted(zpl.referenced_graphics(label))}
        for i, label in enumerate(zpl.labels(text))
    ]


def _under_systemd() -> bool:
    """True when the parent process is systemd (the --user service), not a terminal."""
    try:
        return Path(f"/proc/{os.getppid()}/comm").read_text().strip() == "systemd"
    except OSError:
        return False


# --- viewer page and state ---------------------------------------------------------------


@get("/ui")
@get("/ui/index.html")  # _dispatch already stripped the trailing slash
def _index(h):
    h.reply(200, (STATIC / "index.html").read_bytes(), "text/html; charset=utf-8", cors=False, headers=NO_STORE)


def _asset(h, name: str):
    file = STATIC / name
    if not file.is_file():
        raise HttpError(404, "Asset not found")
    h.reply(200, file.read_bytes(), ASSET_TYPES[file.suffix], cors=False, headers=NO_STORE)


@get("/ui/api/state")
def _state(h):
    server = h.server
    _send_json(
        h,
        {
            "version": __version__,
            "rev": server.revision.value,
            "labelary": server.labelary is not None,
            "printers": [
                dict(info.to_device(), **server.registry.label_settings(info.uid))
                for info in server.registry.available()
            ],
            "jobs": server.jobs.summaries(),
            "pending_origins": server.origins.pending(),
            "printer_status": server.status.snapshot(),
            "flags": {
                "errors": {key: value[0] for key, value in zpl.ERROR_FLAGS.items()},
                "warnings": {key: value[0] for key, value in zpl.WARNING_FLAGS.items()},
            },
        },
    )


def _settings(server) -> dict:
    config, settings = server.config, server.registry.settings
    snapshot = settings.snapshot()
    default = server.registry.default()
    return {
        "network_printers": {
            "config": [dict(p, uid=network_uid(p)) for p in config["network_printers"]],
            "user": [dict(p, uid=network_uid(p)) for p in snapshot["network_printers"]],
        },
        "default_printer": {
            "config": config["default_printer"],
            "user": snapshot["default_printer"],
            "effective": default.uid if default else None,
        },
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
            "config_file": " + ".join(config["_config_files"]) or config["_config_file"],
            "settings_file": str(settings.path),
            "virtual_printers_file": str(server.registry.virtual.path),
            "history_file": str(server.jobs.path) if server.jobs.path else None,
            "systemd": _under_systemd(),
            "autostart": control.autostart_state(),
        },
    }


@get("/ui/api/settings")
def _get_settings(h):
    _send_json(h, _settings(h.server))


# --- jobs ----------------------------------------------------------------------------


def _job_endpoint(h, job_id: int, raw: bool):
    job = _job(h.server, job_id)
    if raw:
        h.reply(
            200,
            job["data"],
            "application/octet-stream",
            cors=False,
            headers={"Content-Disposition": f'attachment; filename="job-{job["id"]}.zpl"'},
        )
        return
    settings = h.server.registry.label_settings(job["uid"])
    _send_json(h, dict(job, printer_settings=settings, label_list=_label_list(job["data"], settings["dpi"])))


@post("/ui/api/jobs/clear")
def _clear_jobs(h, body):
    h.server.jobs.clear()
    _send_json(h, {"ok": True})


@post("/ui/api/lint")
def _lint(h, body):
    server = h.server
    text = str(body.get("zpl", ""))
    uid = body.get("uid")
    dpi = server.registry.label_settings(uid)["dpi"] if uid else int_field(body, "dpi", 203)
    _send_json(
        h,
        {
            "warnings": zpl.lint(text, server.jobs.printers.charset(uid) if uid else None, server.config["encoding"]),
            "label_list": _label_list(text, dpi),
        },
    )


@post("/ui/api/print")
def _print(h, body):
    uid = body.get("uid")
    data = _job(h.server, int_field(body, "job_id"))["data"] if "job_id" in body else str(body.get("data", ""))
    if not uid or not data:
        raise HttpError(400, "uid and job_id/data are required")
    job = h.write_to_printer(str(uid), data, from_web=False)
    _send_json(h, {"ok": True, "job_id": job["id"]})


# --- printers ------------------------------------------------------------------------


@post("/ui/api/printers/status")
def _printer_status(h, body):
    uids = body.get("uids")
    _send_json(h, h.server.status.refresh(set(map(str, uids)) if uids else None))


@post("/ui/api/printers/details")
def _printer_details(h, body):
    _send_json(h, h.server.status.details(str_field(body, "uid")))


@post("/ui/api/printers/test-label")
def _test_label(h, body):
    server = h.server
    uid = str_field(body, "uid")
    channel = server.registry.channel(uid)
    label = zpl.test_label(channel.info.name, uid, server.dpi(uid), time.strftime("%Y-%m-%d %H:%M:%S %z"))
    job = h.write_to_printer(uid, label, from_web=False)
    _send_json(h, {"ok": True, "job_id": job["id"]})


@post("/ui/api/virtual")
def _add_virtual(h, body):
    printer = h.server.registry.virtual.add(
        str_field(body, "name", required=True),
        dpi=int_field(body, "dpi", 203),
        width=float_field(body, "width", 4),
        height=float_field(body, "height", 6),
    )
    _changed(h.server)
    _send_json(h, printer, status=201)


@post("/ui/api/virtual/update")
def _update_virtual(h, body):
    uid = str_field(body, "uid")
    changes = {k: body[k] for k in ("name", "dpi", "width", "height", "errors", "warnings") if k in body}
    try:
        printer = h.server.registry.virtual.update(uid, **changes)
    except KeyError:
        raise HttpError(404, f"Virtual printer not found: {uid}") from None
    _changed(h.server)
    _send_json(h, printer)


@post("/ui/api/virtual/delete")
def _delete_virtual(h, body):
    uid = str_field(body, "uid")
    try:
        h.server.registry.virtual.remove(uid)
    except KeyError:
        raise HttpError(404, f"Virtual printer not found: {uid}") from None
    _changed(h.server)
    _send_json(h, {"ok": True})


# --- settings ------------------------------------------------------------------------


@post("/ui/api/settings/origin")
def _decide_origin(h, body):
    h.server.origins.resolve(str_field(body, "origin"), str_field(body, "action"))
    h.server.revision.bump()
    _send_json(h, _settings(h.server))


@post("/ui/api/settings/default")
def _set_default(h, body):
    uid = body.get("uid") or None
    if uid and uid not in _known_uids(h.server):
        raise HttpError(404, f"Printer not found: {uid}")
    h.server.registry.settings.set_default_printer(uid)
    h.server.revision.bump()
    _send_json(h, _settings(h.server))


@post("/ui/api/settings/network/add")
def _add_network_printer(h, body):
    server = h.server
    host, port, uid = str_field(body, "host"), _port(body), str_field(body, "uid")
    candidate = network_uid({"host": host, "port": port, "uid": uid})
    if host and candidate in _known_uids(server):
        raise HttpError(409, f"A printer with uid {candidate} already exists")
    server.registry.settings.add_network_printer(host, port, str_field(body, "name"), uid)
    _changed(server)
    _send_json(h, _settings(server), status=201)


@post("/ui/api/settings/network/remove")
def _remove_network_printer(h, body):
    try:
        h.server.registry.settings.remove_network_printer(str_field(body, "uid"))
    except KeyError:
        raise HttpError(404, "Only printers added in the viewer can be removed here") from None
    _changed(h.server)
    _send_json(h, _settings(h.server))


@post("/ui/api/settings/network/test")
def _test_network_printer(h, body):
    """Connect, ask ~HQES and report how long it took. Nothing is printed."""
    config = h.server.config
    transport = NetworkTransport(str_field(body, "host", required=True), _port(body), config["connect_timeout"])
    start = time.monotonic()
    try:
        transport.write(b"~HQES", timeout=5)
        deadline = time.monotonic() + config["read_timeout"]
        response = b""
        while time.monotonic() < deadline and b"\x03" not in response:
            chunk = transport.read(deadline - time.monotonic())
            if not chunk:
                break
            response += chunk
    except OSError as exc:
        _send_json(h, {"ok": False, "error": str(exc) or exc.__class__.__name__})
        return
    finally:
        transport.close()
    _send_json(
        h,
        {
            "ok": True,
            "ms": round((time.monotonic() - start) * 1000),
            "response": response.decode("utf-8", errors="replace"),
        },
    )


@post("/ui/api/settings/network/discover")
def _discover(h, body):
    """mDNS search; each result says whether it is already one of our printers."""
    timeout = min(max(float_field(body, "timeout", 2.0) or 2.0, 0.5), 10.0)
    try:
        found = discovery.discover(timeout)
    except OSError as exc:
        _send_json(h, {"ok": False, "error": str(exc) or exc.__class__.__name__, "printers": []})
        return
    known = _known_uids(h.server)
    for printer in found:
        printer["uid"] = network_uid(printer)
        printer["added"] = printer["uid"] in known
    _send_json(h, {"ok": True, "printers": found})


@post("/ui/api/settings/autostart")
def _set_autostart(h, body):
    try:
        control.set_autostart(bool(body.get("enabled")))
    except RuntimeError as exc:
        raise HttpError(409, str(exc)) from None
    _send_json(h, _settings(h.server))


@post("/ui/api/shutdown")
def _shutdown(h, body):
    server = h.server
    _send_json(h, {"ok": True})
    log.info("Stop requested from the viewer")
    server.stopped_from_viewer = True
    # shutdown() waits for serve_forever, so it cannot run on this request's thread.
    threading.Thread(target=server.shutdown, daemon=True).start()


# --- preview (Labelary) ----------------------------------------------------------------


def _preview_options(body) -> dict:
    options = {
        "dpi": int_field(body, "dpi", 203),
        "width": float_field(body, "width", 4),
        "height": float_field(body, "height", 6),
        "rotation": int_field(body, "rotation", 0),
        "quality": str(body.get("quality", "grayscale")).lower(),
        "format": str(body.get("format", "png")).lower(),
        "lint": bool(body.get("lint", True)),
    }
    if options["dpi"] not in DPI_VALUES:
        raise HttpError(400, f"dpi must be one of {DPI_VALUES}")
    if not (0.1 <= options["width"] <= 15 and 0.1 <= options["height"] <= 15):
        raise HttpError(400, "Label size must be between 0.1 and 15 inches")
    if options["rotation"] not in ROTATIONS:
        raise HttpError(400, "rotation must be 0, 90, 180 or 270")
    if options["quality"] not in ("grayscale", "bitonal") or options["format"] not in ("png", "pdf"):
        raise HttpError(400, "quality must be grayscale/bitonal and format png/pdf")
    return options


def _preview_source(server, body):
    """(ZPL to render, Labelary label index or None for every label, printer uid)."""
    every_label = bool(body.get("all"))
    if "job_id" in body:
        job = _job(server, int_field(body, "job_id"))
        labels = zpl.labels(job["data"])
        if not labels:
            raise HttpError(404, "This job has no labels")
        if every_label:
            return "".join(labels[: labelary.MAX_LABELS]), None, job["uid"]
        index = int_field(body, "index", 0)
        if not 0 <= index < len(labels):
            raise HttpError(404, "Label not found")
        return labels[index], 0, job["uid"]
    text = str(body.get("zpl", ""))
    if not text.strip():
        raise HttpError(400, "zpl is required")
    return text, None if every_label else int_field(body, "index", 0), body.get("uid")


@post("/ui/api/preview")
def _preview(h, body):
    server = h.server
    if server.labelary is None:
        raise HttpError(404, "Preview is disabled (labelary_url is null)")
    options = _preview_options(body)
    text, index, uid = _preview_source(server, body)
    # Graphics synced earlier (~DG) live in the printer's memory: prepend them so ^XG renders.
    prefix = server.jobs.printers.graphics_for(uid, text) if uid else ""
    if index is None and options["format"] == "png":
        index = 0  # PNG is always a single label
    preview = server.labelary.render(
        (prefix + text).encode("utf-8"), index, options, prefix_bytes=len(prefix.encode("utf-8"))
    )
    h.reply(
        200,
        preview.content,
        preview.content_type,
        cors=False,
        headers={
            **NO_STORE,
            "X-Total-Count": preview.total,
            "X-Labelary-Warnings": json.dumps(preview.warnings, ensure_ascii=True),
        },
    )
