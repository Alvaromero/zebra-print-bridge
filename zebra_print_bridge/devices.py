"""Printer discovery and a serialized channel per printer."""

import logging
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from .settings import SettingsStore, network_uid
from .transports import CupsTransport, NetworkTransport, Transport, UsbTransport
from .virtual import VirtualPrinterStore, VirtualTransport

log = logging.getLogger(__name__)

ZEBRA_VENDOR_ID = "0a5f"
PROVIDER = "com.zebra.ds.webdriver.desktop.provider.DefaultDeviceProvider"
MANUFACTURER = "Zebra Technologies"


class PrinterNotFound(Exception):
    pass


@dataclass
class PrinterInfo:
    uid: str
    name: str
    connection: str  # "usb" | "network" | "driver"
    key: tuple  # identifies the physical transport; the channel is recreated when it changes
    make_transport: Callable[[], Transport] = field(repr=False, compare=False)

    def to_device(self) -> dict:
        """Device object as BrowserPrint.Device expects it."""
        return {
            "name": self.name,
            "deviceType": "printer",
            "connection": self.connection,
            "uid": self.uid,
            "provider": PROVIDER,
            "manufacturer": MANUFACTURER,
            "version": 2,
        }


def _read_attr(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return ""


def discover_usb(sysfs_root="/sys", dev_root="/dev") -> list:
    """Zebra printers bound to usblp, via /sys/class/usbmisc/lp*."""
    printers = []
    for entry in sorted(Path(sysfs_root, "class", "usbmisc").glob("lp*")):
        usb_device = (entry / "device").resolve().parent  # .../1-2:1.0 -> .../1-2
        if _read_attr(usb_device / "idVendor").lower() != ZEBRA_VENDOR_ID:
            continue
        serial = _read_attr(usb_device / "serial")
        product = _read_attr(usb_device / "product")
        node = str(Path(dev_root, "usb", entry.name))
        # Without a serial number, the physical port (e.g. "1-2") is the most stable option.
        uid = f"usb:{serial}" if serial else f"usb:{usb_device.name}"
        printers.append(PrinterInfo(
            uid=uid,
            name=product or f"Zebra USB {entry.name}",
            connection="usb",
            key=("usb", node),
            make_transport=lambda node=node: UsbTransport(node),
        ))
    return printers


def configured_network(config, extra=()) -> list:
    """Network printers from config.json plus the ones added in the viewer (`extra`)."""
    printers = []
    for item in [*config["network_printers"], *extra]:
        host = item["host"]
        port = int(item.get("port", 9100))
        printers.append(PrinterInfo(
            uid=network_uid(item),
            name=item.get("name") or f"Zebra {host}",
            connection="network",
            key=("network", host, port),
            make_transport=lambda host=host, port=port: NetworkTransport(
                host, port, config["connect_timeout"]),
        ))
    return printers


def configured_cups(config) -> list:
    return [
        PrinterInfo(
            uid=f"cups:{queue}",
            name=queue,
            connection="driver",
            key=("cups", queue),
            make_transport=lambda queue=queue: CupsTransport(queue),
        )
        for queue in config["cups_queues"]
    ]


class Channel:
    """Serializes access to one printer and closes the connection after it idles."""

    def __init__(self, info: PrinterInfo, config: dict):
        self.info = info
        self.config = config
        self.transport = info.make_transport()
        self.lock = threading.Lock()
        self._timer = None
        # The web app's sendThenRead is two requests (write, then read). Until the read
        # arrives, the viewer must not query the printer: its drain would discard the answer.
        self._web_write_at = None

    def write(self, data: bytes, expect_read: bool = True) -> None:
        with self.lock:
            self._cancel_timer()
            try:
                self.transport.write(data, self.config["write_timeout"])
            finally:
                self._web_write_at = time.monotonic() if expect_read else None
                self._arm_close()

    def read(self) -> bytes:
        """Long-poll: wait for the response up to read_timeout, cut it after read_idle of silence."""
        with self.lock:
            self._cancel_timer()
            self._web_write_at = None
            try:
                return self._read_response()
            finally:
                self._arm_close()

    def transact(self, data: bytes, grace: float = 3.0) -> bytes:
        """Write and read as one operation, for the viewer's own queries.

        Waits up to `grace` seconds while the web app has a write whose response it has not
        read yet, so the viewer never steals that response.
        """
        deadline = time.monotonic() + grace
        while True:
            with self.lock:
                pending = self._web_write_at is not None and time.monotonic() - self._web_write_at < grace
                if not pending or time.monotonic() >= deadline:
                    self._cancel_timer()
                    try:
                        self.transport.write(data, self.config["write_timeout"])
                        return self._read_response()
                    finally:
                        self._arm_close()
            time.sleep(0.1)

    def _read_response(self) -> bytes:
        deadline = time.monotonic() + self.config["read_timeout"]
        idle = self.config["read_idle"]
        buf = bytearray()
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            if not buf:
                wait = remaining
            elif buf.endswith(b"\x03"):  # ETX: end of a status block (~HQES)
                wait = min(remaining, 0.05)
            else:
                wait = min(remaining, idle)
            chunk = self.transport.read(wait)
            if chunk:
                buf += chunk
            elif buf or not self.transport.responds_later:
                break
        return bytes(buf)

    def close(self) -> None:
        with self.lock:
            self._cancel_timer()
            self.transport.close()

    def _arm_close(self):
        self._timer = threading.Timer(self.config["connection_linger"], self._idle_close)
        self._timer.daemon = True
        self._timer.start()

    def _cancel_timer(self):
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None

    def _idle_close(self):
        # If an operation is running it will re-arm the timer itself.
        if self.lock.acquire(blocking=False):
            try:
                self.transport.close()
            finally:
                self.lock.release()


class Registry:
    def __init__(self, config: dict, discover_usb_fn=discover_usb, virtual_store=None, settings=None):
        self.config = config
        self._discover_usb = discover_usb_fn
        self.virtual = virtual_store or VirtualPrinterStore(config.get("virtual_printers_file"))
        self.settings = settings or SettingsStore(config.get("settings_file"))
        self._channels = {}
        self._order = []
        self._lock = threading.Lock()

    def _discover(self) -> list:
        printers = []
        if self.config["usb"].get("enabled", True):
            try:
                printers += self._discover_usb()
            except OSError as exc:
                log.warning("Could not list USB printers: %s", exc)
        printers += configured_network(self.config, self.settings.network_printers())
        printers += configured_cups(self.config)
        # Last, so a real printer is the default whenever there is one.
        if self.config["virtual_printers"]:
            printers += [
                PrinterInfo(
                    uid=p["uid"],
                    name=p["name"],
                    connection="driver",
                    key=("virtual", p["uid"]),
                    make_transport=lambda uid=p["uid"]: VirtualTransport(self.virtual, uid),
                )
                for p in self.virtual.list()
            ]
        overrides = self.config["overrides"]
        for info in printers:
            override = overrides.get(info.uid) or {}
            info.uid = override.get("uid", info.uid)
            info.name = override.get("name", info.name)
        return printers

    def refresh(self) -> list:
        printers = self._discover()
        with self._lock:
            current = {}
            for info in printers:
                if info.uid in current:
                    log.warning("Duplicate uid %s, ignoring %s", info.uid, info.key)
                    continue
                channel = self._channels.get(info.uid)
                if channel is None or channel.info.key != info.key:
                    if channel is not None:
                        channel.close()
                    channel = Channel(info, self.config)
                    log.info("Printer found: %s (%s, %s)", info.name, info.connection, info.uid)
                else:
                    channel.info.name = info.name
                current[info.uid] = channel
            for uid, channel in self._channels.items():
                if uid not in current:
                    log.info("Printer gone: %s", uid)
                    channel.close()
            self._channels = current
            self._order = [info.uid for info in printers if info.uid in current]
            return [self._channels[uid].info for uid in self._order]

    def available(self) -> list:
        return self.refresh()

    def default(self):
        printers = self.refresh()
        for wanted in (self.settings.default_printer(), self.config["default_printer"]):
            for info in printers:
                if wanted and info.uid == wanted:
                    return info
        return printers[0] if printers else None

    def channel(self, uid: str) -> Channel:
        with self._lock:
            channel = self._channels.get(uid)
        if channel is None:
            self.refresh()
            with self._lock:
                channel = self._channels.get(uid)
        if channel is None:
            raise PrinterNotFound(uid)
        return channel

    def close(self):
        with self._lock:
            for channel in self._channels.values():
                channel.close()
            self._channels = {}
