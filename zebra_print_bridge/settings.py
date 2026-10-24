"""Settings changed from the viewer, layered on top of config.json (which is never rewritten).

config.json is the base (it can be distributed by an organization); settings.json holds what
each user changes in the viewer: network printers, default printer and authorized websites.
"""

import json
import logging
import os
import re
import shutil
import subprocess
import threading
import time
from fnmatch import fnmatchcase
from pathlib import Path

log = logging.getLogger(__name__)

EMPTY = {"network_printers": [], "default_printer": None, "origins": {"allowed": [], "denied": []}}
ORIGIN_RE = re.compile(r"^(https?)://[A-Za-z0-9.\-\[\]:]+$")
HOST_RE = re.compile(r"^[A-Za-z0-9.\-:\[\]]+$")


def default_settings_path() -> Path:
    base = os.environ.get("XDG_STATE_HOME") or os.path.expanduser("~/.local/state")
    return Path(base) / "zebra-print-bridge" / "settings.json"


class SettingsError(ValueError):
    pass


class SettingsStore:
    def __init__(self, path=None):
        self.path = Path(path) if path else default_settings_path()
        self._lock = threading.Lock()
        self._data = self._load()

    def _load(self) -> dict:
        try:
            with open(self.path, encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError):
            data = {}
        origins = data.get("origins") or {}
        return {
            "network_printers": [p for p in data.get("network_printers", []) if p.get("host")],
            "default_printer": data.get("default_printer"),
            "origins": {"allowed": list(origins.get("allowed", [])), "denied": list(origins.get("denied", []))},
        }

    def _save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._data, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(self.path)

    def snapshot(self) -> dict:
        with self._lock:
            return json.loads(json.dumps(self._data))

    # --- network printers -----------------------------------------------------------

    def network_printers(self) -> list:
        with self._lock:
            return [dict(p) for p in self._data["network_printers"]]

    def add_network_printer(self, host: str, port: int = 9100, name: str = "", uid: str = "") -> dict:
        host = host.strip()
        if not host or not HOST_RE.match(host):
            raise SettingsError("Invalid host")
        if not 1 <= int(port) <= 65535:
            raise SettingsError("Invalid port")
        printer = {"host": host, "port": int(port)}
        if name.strip():
            printer["name"] = name.strip()
        if uid.strip():
            printer["uid"] = uid.strip()
        with self._lock:
            self._data["network_printers"].append(printer)
            self._save()
        return printer

    def remove_network_printer(self, uid: str):
        with self._lock:
            before = len(self._data["network_printers"])
            self._data["network_printers"] = [p for p in self._data["network_printers"]
                                              if network_uid(p) != uid]
            if len(self._data["network_printers"]) == before:
                raise KeyError(uid)
            if self._data["default_printer"] == uid:
                self._data["default_printer"] = None
            self._save()

    # --- default printer ------------------------------------------------------------

    def default_printer(self):
        with self._lock:
            return self._data["default_printer"]

    def set_default_printer(self, uid):
        with self._lock:
            self._data["default_printer"] = uid or None
            self._save()

    # --- authorized websites --------------------------------------------------------

    def origin_decision(self, origin: str) -> str:
        """"allowed", "denied" or "unknown" according to what the user decided."""
        with self._lock:
            if origin in self._data["origins"]["allowed"]:
                return "allowed"
            if origin in self._data["origins"]["denied"]:
                return "denied"
        return "unknown"

    def set_origin(self, origin: str, action: str):
        if not ORIGIN_RE.match(origin or ""):
            raise SettingsError("Invalid origin")
        if action not in ("allow", "deny", "forget"):
            raise SettingsError("action must be allow, deny or forget")
        with self._lock:
            lists = self._data["origins"]
            for key in ("allowed", "denied"):
                if origin in lists[key]:
                    lists[key].remove(origin)
            if action == "allow":
                lists["allowed"].append(origin)
            elif action == "deny":
                lists["denied"].append(origin)
            self._save()


def network_uid(printer: dict) -> str:
    port = int(printer.get("port", 9100))
    return printer.get("uid") or (printer["host"] if port == 9100 else f"{printer['host']}:{port}")


class OriginPolicy:
    """Decides which websites may use the printers, like Browser Print's "accept host" prompt."""

    def __init__(self, config: dict, settings: SettingsStore, viewer_url: str):
        self.config = config
        self.settings = settings
        self.viewer_url = viewer_url
        self._pending = {}
        self._lock = threading.Lock()

    def decide(self, origin) -> str:
        """"allowed", "pending" or "denied". Requests without Origin (curl, scripts) are allowed."""
        if not origin:
            return "allowed"
        patterns = self.config["allowed_origins"]
        if not self.config["origin_approval"]:
            # Old behavior: only the config patterns, and an empty list allows everything.
            return "allowed" if not patterns or any(fnmatchcase(origin, p) for p in patterns) else "denied"
        if any(fnmatchcase(origin, p) for p in patterns):
            return "allowed"
        decision = self.settings.origin_decision(origin)
        if decision == "unknown":
            self._mark_pending(origin)
            return "pending"
        return decision

    def _mark_pending(self, origin: str):
        now = time.time()
        with self._lock:
            entry = self._pending.get(origin)
            first = entry is None
            if first:
                entry = self._pending[origin] = {"origin": origin, "first_seen": now, "requests": 0}
            entry["last_seen"] = now
            entry["requests"] += 1
        if first:
            log.warning("Website waiting for approval: %s (approve it at %s)", origin, self.viewer_url)
            self._notify(origin)

    def pending(self) -> list:
        with self._lock:
            entries = [dict(e) for e in self._pending.values()]
        return [e for e in entries if self.settings.origin_decision(e["origin"]) == "unknown"]

    def resolve(self, origin: str, action: str):
        self.settings.set_origin(origin, action)
        with self._lock:
            self._pending.pop(origin, None)

    def message(self, origin: str, decision: str) -> str:
        if decision == "denied":
            return (f"This website ({origin}) is blocked in Zebra Print Bridge. "
                    f"Unblock it in the settings at {self.viewer_url}")
        return (f"This website ({origin}) is not authorized to use the printers yet. "
                f"Approve it in Zebra Print Bridge: {self.viewer_url}")

    def _notify(self, origin: str):
        if not self.config["desktop_notifications"] or not shutil.which("notify-send"):
            return
        try:
            subprocess.Popen(
                ["notify-send", "--app-name=Zebra Print Bridge", "--icon=printer",
                 "Zebra Print Bridge", f"{origin} wants to use your printers.\nOpen {self.viewer_url} to allow it."],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
        except OSError as exc:
            log.debug("notify-send failed: %s", exc)
