"""`zebra-print-bridge-ctl diagnose`: what the bridge sees and why a printer may not work.

Checks the system (usblp, udev rule, device permissions, CUPS), the configuration, the service
and, unless told not to, asks every printer for ~HI, ~HQES and SGD details. Nothing is printed.
While the service runs, printers are queried through it: opening a USB printer next to the
service would fail (usblp allows one open at a time) or steal its answers.
"""

import getpass
import grp
import json
import os
import platform
import shutil
import socket
import stat
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

from . import __version__, zpl
from .config import ConfigError, load_config
from .control import Control, autostart_state
from .devices import ZEBRA_VENDOR_ID, Registry
from .printer_status import StatusMonitor
from .settings import SettingsStore

UDEV_RULES = (
    Path("/usr/lib/udev/rules.d/70-zebra-print-bridge.rules"),
    Path("/etc/udev/rules.d/70-zebra-print-bridge.rules"),
)
PRINTER_CLASS = "07"
OK, WARN, FAIL, INFO = "ok", "warning", "error", "info"


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return ""


# --- system ------------------------------------------------------------------------------


def system_info() -> dict:
    os_release = dict(line.split("=", 1) for line in _read(Path("/etc/os-release")).splitlines() if "=" in line)
    try:
        groups = sorted({grp.getgrgid(g).gr_name for g in os.getgroups()})
    except KeyError:
        groups = [str(g) for g in os.getgroups()]
    return {
        "bridge": __version__,
        "python": platform.python_version(),
        "os": os_release.get("PRETTY_NAME", "").strip('"') or platform.system(),
        "kernel": platform.release(),
        "user": getpass.getuser(),
        "groups": groups,
    }


def usb_devices(sysfs: str = "/sys", dev: str = "/dev") -> list:
    """Every Zebra on the USB bus, bound to usblp or not, with its device node and permissions."""
    devices = []
    for device in sorted(Path(sysfs, "bus", "usb", "devices").glob("*")):
        if ":" in device.name or _read(device / "idVendor").lower() != ZEBRA_VENDOR_ID:
            continue  # interfaces have a ":" in their name; we want devices
        interfaces = []
        for interface in sorted(device.parent.glob(f"{device.name}:*")):
            driver = interface / "driver"
            nodes = sorted(p.name for p in (interface / "usbmisc").glob("lp*"))
            interfaces.append(
                {
                    "interface": interface.name,
                    "class": _read(interface / "bInterfaceClass"),
                    "driver": os.path.basename(os.readlink(driver)) if driver.is_symlink() else None,
                    "nodes": [_node(Path(dev, "usb", name)) for name in nodes],
                }
            )
        devices.append(
            {
                "port": device.name,
                "product": _read(device / "product"),
                "serial": _read(device / "serial"),
                "product_id": _read(device / "idProduct"),
                "interfaces": interfaces,
            }
        )
    return devices


def _node(path: Path) -> dict:
    node = {"path": str(path), "exists": path.exists()}
    if node["exists"]:
        info = path.stat()
        try:
            group = grp.getgrgid(info.st_gid).gr_name
        except KeyError:
            group = str(info.st_gid)
        node.update(
            mode=stat.filemode(info.st_mode),
            group=group,
            readable=os.access(path, os.R_OK),
            writable=os.access(path, os.W_OK),
        )
    return node


def usblp_loaded(sysfs: str = "/sys") -> bool:
    return Path(sysfs, "module", "usblp").exists()


def cups_zebra_queues() -> list:
    """CUPS queues pointing at a USB Zebra: the CUPS usb backend can take the printer from usblp."""
    if not shutil.which("lpstat"):
        return []
    try:
        result = subprocess.run(["lpstat", "-v"], capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return []
    return [line.strip() for line in result.stdout.splitlines() if "usb://zebra" in line.lower()]


def tcp_check(host: str, port: int, timeout: float) -> dict:
    start = time.monotonic()
    try:
        with socket.create_connection((host, port), timeout=timeout):
            pass
    except OSError as exc:
        return {"ok": False, "error": str(exc) or exc.__class__.__name__}
    return {"ok": True, "ms": round((time.monotonic() - start) * 1000)}


# --- printers ----------------------------------------------------------------------------


def _service_post(control: Control, path: str, body: dict, timeout: float = 30) -> dict:
    request = urllib.request.Request(
        f"{control.base}{path}",
        data=json.dumps(body).encode(),
        method="POST",
        headers={"Content-Type": "application/json", "Origin": control.base},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read())


def query_printers(config: dict, control: Control, running: bool) -> list:
    """Status and SGD details of every printer, through the service when it runs."""
    if running:
        state = control.state(timeout=5) or {}
        printers = [(p["uid"], p["name"], p["connection"], p.get("virtual", False)) for p in state.get("printers", [])]
        start = time.monotonic()
        statuses = _service_post(control, "/ui/api/printers/status", {})
        status_ms = round((time.monotonic() - start) * 1000)

        def details(uid):
            return _service_post(control, "/ui/api/printers/details", {"uid": uid})
    else:
        registry = Registry(config)
        monitor = StatusMonitor(registry, config["encoding"])
        printers = [
            (p.uid, p.name, p.connection, p.uid in {v["uid"] for v in registry.virtual.list()})
            for p in registry.available()
        ]
        start = time.monotonic()
        statuses = monitor.refresh()
        status_ms = round((time.monotonic() - start) * 1000)
        details = monitor.details
    results = []
    for uid, name, connection, virtual in printers:
        status = {k: v for k, v in (statuses.get(uid) or {}).items() if k not in ("checked", "details")}
        start = time.monotonic()
        try:
            sgd = details(uid)
        except Exception as exc:  # a diagnosis reports failures instead of stopping at the first one
            sgd = {"supported": False, "error": str(exc) or exc.__class__.__name__}
        sgd = {k: v for k, v in sgd.items() if k != "checked"}
        results.append(
            {
                "uid": uid,
                "name": name,
                "connection": connection,
                "virtual": virtual,
                "status": status,
                "details": sgd,
                "status_ms": status_ms,
                "details_ms": round((time.monotonic() - start) * 1000),
            }
        )
    if not running:
        registry.close()
    return results


# --- report ------------------------------------------------------------------------------


def run(config_path: str = None, port: int = None, query: bool = True, sysfs: str = "/sys", dev: str = "/dev") -> dict:
    report = {"time": time.strftime("%Y-%m-%d %H:%M:%S %z"), "system": system_info(), "findings": []}
    findings = report["findings"]

    try:
        config = load_config(config_path)
        report["config"] = {
            "file": config["_config_file"],
            "exists": Path(config["_config_file"]).exists(),
            "files": config["_config_files"],
            "warnings": config["_warnings"],
        }
        findings += [(WARN, warning) for warning in config["_warnings"]]
    except ConfigError as exc:
        config = load_config("/nonexistent/config.json", system_path="/nonexistent/config.json")
        report["config"] = {"error": str(exc)}
        findings.append((FAIL, f"config.json cannot be used, the service will not start: {exc}"))

    control = Control(port, config=config)
    running = control.running()
    report["service"] = {
        "url": control.viewer_url,
        "running": running,
        "systemd": running and control.managed_by_systemd(),
        "autostart": autostart_state(),
    }
    if not running:
        findings.append(
            (
                INFO,
                "The service is not running"
                + (": printers were queried directly" if query else "")
                + " (start it with: zebra-print-bridge-ctl start).",
            )
        )

    usb = usb_devices(sysfs, dev)
    report["usb"] = {
        "usblp_loaded": usblp_loaded(sysfs),
        "udev_rule": [str(p) for p in UDEV_RULES if p.exists()],
        "devices": usb,
        "cups_queues": cups_zebra_queues(),
    }
    findings += _usb_findings(report["usb"], config)

    settings = SettingsStore(config.get("settings_file"))
    network = [*config["network_printers"], *settings.network_printers()]
    report["network"] = [
        dict(
            host=p["host"],
            port=int(p.get("port", 9100)),
            **tcp_check(p["host"], int(p.get("port", 9100)), config["connect_timeout"]),
        )
        for p in network
    ]
    for printer in report["network"]:
        if not printer["ok"]:
            findings.append(
                (
                    FAIL,
                    f"Network printer {printer['host']}:{printer['port']} does not accept "
                    f"connections ({printer['error']}). Check its IP address, that port "
                    f"9100 (raw printing) is on and that no firewall blocks it.",
                )
            )

    if query:
        try:
            report["printers"] = query_printers(config, control, running)
        except Exception as exc:  # reported: the rest of the report is still useful
            report["printers"] = []
            findings.append((FAIL, f"Could not query the printers: {exc}"))
        findings += _printer_findings(report["printers"])
    if not usb and not network and not any(not p["virtual"] for p in report.get("printers", [])):
        findings.append((INFO, "No real printer found: connect a Zebra by USB or add a network printer."))
    report["findings"] = [{"level": level, "message": message} for level, message in findings]
    return report


def _usb_findings(usb: dict, config: dict) -> list:
    findings = []
    if not config["usb"].get("enabled", True):
        findings.append((INFO, "USB discovery is off (usb.enabled is false in config.json)."))
    for device in usb["devices"]:
        name = device["product"] or f"Zebra {device['product_id']}"
        printer_ifaces = [i for i in device["interfaces"] if i["class"] == PRINTER_CLASS]
        bound = [i for i in printer_ifaces if i["driver"] == "usblp"]
        if not bound:
            drivers = sorted({i["driver"] or "none" for i in printer_ifaces}) or ["no printer interface"]
            hint = (
                "a program using libusb (CUPS usb backend, ipp-usb) has it"
                if "usbfs" in drivers
                else "load the driver with: sudo modprobe usblp"
                if not usb["usblp_loaded"]
                else "unplug and plug it again"
            )
            findings.append(
                (
                    FAIL,
                    f"{name} (USB {device['port']}) is not on the usblp driver (driver: {', '.join(drivers)}): {hint}.",
                )
            )
            continue
        for node in [n for i in bound for n in i["nodes"]]:
            if not node["exists"]:
                findings.append((FAIL, f"{name}: {node['path']} does not exist (check udev)."))
            elif not (node["readable"] and node["writable"]):
                rule = (
                    "the udev rule is installed: unplug and plug the printer, or log out and in"
                    if usb["udev_rule"]
                    else "the udev rule is missing: reinstall the package"
                )
                findings.append(
                    (
                        FAIL,
                        f"{name}: no read/write access to {node['path']} ({node['mode']}, "
                        f"group {node['group']}); {rule}.",
                    )
                )
            else:
                findings.append((OK, f"{name}: {node['path']} is on usblp and accessible."))
    if usb["devices"] and usb["cups_queues"]:
        findings.append(
            (
                WARN,
                "CUPS has a queue on a USB Zebra; while CUPS prints to it the bridge cannot "
                "open the printer: " + "; ".join(usb["cups_queues"]),
            )
        )
    return findings


def _printer_findings(printers: list) -> list:
    findings = []
    for printer in printers:
        if printer["virtual"]:
            continue
        name, state = f"{printer['name']} ({printer['uid']})", printer["status"].get("state")
        transport = "USB" if printer["connection"] == "usb" else printer["connection"]
        if state in ("ready", "warning", "error"):
            findings.append(
                (
                    OK,
                    f"{name}: status reads over {transport} work ({state}"
                    + (f": {_labels(printer['status'].get('errors'), zpl.ERROR_FLAGS)}" if state == "error" else "")
                    + ").",
                )
            )
        elif state == "no_response":
            more = " The libusb backend (ROADMAP) is needed for this model." if printer["connection"] == "usb" else ""
            findings.append(
                (
                    FAIL,
                    f"{name}: accepts data but does not answer ~HQES; printing works, the web "
                    f"app will show no status.{more}",
                )
            )
        elif state == "offline":
            findings.append((FAIL, f"{name}: cannot be opened: {printer['status'].get('error')}."))
        elif state == "unsupported":
            findings.append((INFO, f"{name}: CUPS queue, print only (no status)."))
        if state in ("ready", "warning", "error"):
            supported = printer["details"].get("supported")
            findings.append(
                (
                    OK if supported else WARN,
                    f"{name}: SGD details "
                    + ("readable." if supported else "not available (older firmware or not answering)."),
                )
            )
    return findings


MARKS = {OK: "✓", WARN: "!", FAIL: "✗", INFO: "·"}


def _labels(keys, table) -> str:
    """ "media_out, head_open" -> "Media Out, Head Open"."""
    return ", ".join(table[k][0] if k in table else k for k in keys or [])


def format_text(report: dict) -> str:
    system, service, usb = report["system"], report["service"], report["usb"]
    lines = [
        f"Zebra Print Bridge {system['bridge']} — diagnosis {report['time']}",
        f"System: {system['os']}, kernel {system['kernel']}, Python {system['python']}",
        f"User: {system['user']} (groups: {', '.join(system['groups'])})",
        "",
    ]

    config = report["config"]
    lines.append(
        "Configuration: "
        + (
            f"INVALID — {config['error']}"
            if "error" in config
            else " + ".join(config["files"]) or f"defaults ({config['file']} not found)"
        )
    )
    autostart = service["autostart"]
    lines.append(
        f"Service: {'running' if service['running'] else 'not running'} at {service['url']}"
        + (" (systemd)" if service["systemd"] else "")
        + f"; start at login: {'on' if autostart.get('enabled') else 'off'}"
    )
    lines.append(
        f"USB: usblp {'loaded' if usb['usblp_loaded'] else 'NOT loaded'}; udev rule: "
        + (", ".join(usb["udev_rule"]) or "not installed")
    )
    for device in usb["devices"]:
        lines.append(
            f"  {device['product'] or 'Zebra'} [0a5f:{device['product_id']}] port {device['port']}"
            + (f" serial {device['serial']}" if device["serial"] else "")
        )
        for iface in device["interfaces"]:
            nodes = ", ".join(
                f"{n['path']} {n.get('mode', 'missing')} {n.get('group', '')}".strip() for n in iface["nodes"]
            )
            lines.append(
                f"    interface {iface['interface']} class {iface['class']} driver {iface['driver']}"
                + (f" -> {nodes}" if nodes else "")
            )
    if not usb["devices"]:
        lines.append("  no Zebra on the USB bus")
    for queue in usb["cups_queues"]:
        lines.append(f"  CUPS: {queue}")
    if report["network"]:
        lines.append("Network printers:")
        for printer in report["network"]:
            lines.append(
                f"  {printer['host']}:{printer['port']} "
                + (f"connects in {printer['ms']} ms" if printer["ok"] else f"FAILS: {printer['error']}")
            )

    if "printers" in report:
        lines += ["", "Printers:"]
        for printer in report["printers"]:
            status = printer["status"]
            lines.append(
                f"  {printer['name']} [{printer['uid']}, {printer['connection']}"
                + (", virtual" if printer["virtual"] else "")
                + "]"
            )
            model = " ".join(str(status[k]) for k in ("model", "firmware") if status.get(k))
            lines.append(
                f"    status: {status.get('state', '?')}"
                + (f" — {model}" if model else "")
                + (f", {status['dpi']} dpi" if status.get("dpi") else "")
                + (f"; errors: {_labels(status['errors'], zpl.ERROR_FLAGS)}" if status.get("errors") else "")
                + (f"; warnings: {_labels(status['warnings'], zpl.WARNING_FLAGS)}" if status.get("warnings") else "")
                + (f"; {status['error']}" if status.get("error") else "")
            )
            values = {k: v for k, v in (printer["details"].get("values") or {}).items() if v not in (None, "")}
            lines.append(
                "    SGD: "
                + (
                    ", ".join(f"{k}={v}" for k, v in values.items())
                    if values
                    else "no answer"
                    if printer["details"].get("supported") is False
                    else "—"
                )
            )

    lines += ["", "Findings:"]
    lines += [f"  {MARKS[f['level']]} {f['message']}" for f in report["findings"]] or ["  ✓ nothing to report"]
    return "\n".join(lines)


def main(config_path=None, port=None, as_json=False, query=True) -> int:
    report = run(config_path, port, query=query)
    print(json.dumps(report, indent=2, ensure_ascii=False) if as_json else format_text(report))
    return 1 if any(f["level"] == FAIL for f in report["findings"]) else 0


if __name__ == "__main__":
    sys.exit(main())
