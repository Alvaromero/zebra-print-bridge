"""Virtual printers: accept any ZPL and answer ~HQES with a configurable status."""

import json
import os
import re
import threading
from pathlib import Path

from . import zpl
from .transports import Transport

DPI_VALUES = (152, 203, 300, 600)  # 6, 8, 12 and 24 dots/mm

DEFAULT_PRINTERS = [
    {"uid": "virtual:zd421-203", "name": "Virtual ZD421-203dpi", "dpi": 203, "width": 4, "height": 6},
]


def default_state_path() -> Path:
    base = os.environ.get("XDG_STATE_HOME") or os.path.expanduser("~/.local/state")
    return Path(base) / "zebra-print-bridge" / "virtual-printers.json"


class VirtualPrinterStore:
    """Virtual printer list, persisted in a JSON file managed from the viewer."""

    def __init__(self, path=None):
        self.path = Path(path) if path else default_state_path()
        self._lock = threading.Lock()
        self._printers = self._load()

    def _load(self) -> list:
        try:
            with open(self.path, encoding="utf-8") as fh:
                printers = json.load(fh)
        except (OSError, ValueError):
            printers = DEFAULT_PRINTERS
        return [self._normalize(p) for p in printers]

    @staticmethod
    def _normalize(printer: dict) -> dict:
        dpi = int(printer.get("dpi", 203))
        return {
            "uid": str(printer["uid"]),
            "name": str(printer.get("name") or printer["uid"]),
            "dpi": dpi if dpi in DPI_VALUES else 203,
            # Stored in inches, like Labelary; the viewer converts to mm/cm.
            "width": min(max(float(printer.get("width", 4)), 0.1), 15.0),
            "height": min(max(float(printer.get("height", 6)), 0.1), 15.0),
            "errors": [f for f in printer.get("errors", []) if f in zpl.ERROR_FLAGS],
            "warnings": [f for f in printer.get("warnings", []) if f in zpl.WARNING_FLAGS],
        }

    def _save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._printers, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(self.path)

    def list(self) -> list:
        with self._lock:
            return [dict(p) for p in self._printers]

    def get(self, uid: str):
        with self._lock:
            return next((dict(p) for p in self._printers if p["uid"] == uid), None)

    def add(self, name: str, dpi: int = 203, width: float = 4, height: float = 6) -> dict:
        slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "printer"
        with self._lock:
            uids = {p["uid"] for p in self._printers}
            uid, n = f"virtual:{slug}", 2
            while uid in uids:
                uid, n = f"virtual:{slug}-{n}", n + 1
            printer = self._normalize({"uid": uid, "name": name, "dpi": dpi,
                                       "width": width, "height": height})
            self._printers.append(printer)
            self._save()
            return dict(printer)

    def update(self, uid: str, **changes) -> dict:
        with self._lock:
            for index, printer in enumerate(self._printers):
                if printer["uid"] == uid:
                    self._printers[index] = self._normalize(dict(printer, **changes, uid=uid))
                    self._save()
                    return dict(self._printers[index])
        raise KeyError(uid)

    def remove(self, uid: str):
        with self._lock:
            before = len(self._printers)
            self._printers = [p for p in self._printers if p["uid"] != uid]
            if len(self._printers) == before:
                raise KeyError(uid)
            self._save()


class VirtualTransport(Transport):
    # The response is ready as soon as the write returns: /read does not wait for the timeout.
    responds_later = False

    def __init__(self, store: VirtualPrinterStore, uid: str):
        self.store = store
        self.uid = uid
        self.pending = bytearray()

    def write(self, data, timeout):
        printer = self.store.get(self.uid)
        if printer is None:
            raise OSError("virtual printer was removed")
        self.pending = bytearray()  # like drain() on the real transports
        upper = data.upper()
        if b"~HI" in upper:
            dpmm = {152: 6, 203: 8, 300: 12, 600: 24}[printer["dpi"]]
            self.pending += f"\x02{printer['name']},V-virtual,{dpmm},8192KB\x03\r\n".encode()
        for _ in range(upper.count(b"~HQES")):
            self.pending += zpl.hqes_response(printer["errors"], printer["warnings"])

    def read(self, timeout):
        data, self.pending = bytes(self.pending), bytearray()
        return data
