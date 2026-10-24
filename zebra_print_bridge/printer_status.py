"""Live printer status for the viewer: model and firmware (~HI) and errors/warnings (~HQES).

These queries go straight to the channel and are not recorded as jobs.
"""

import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from . import zpl
from .devices import PrinterNotFound

log = logging.getLogger(__name__)


class StatusMonitor:
    def __init__(self, registry, encoding: str = "utf-8"):
        self.registry = registry
        self.encoding = encoding
        self._cache = {}
        self._lock = threading.Lock()

    def snapshot(self) -> dict:
        with self._lock:
            return {uid: dict(status) for uid, status in self._cache.items()}

    def refresh(self, uids=None) -> dict:
        """Query the given printers (all when None) in parallel and return the whole cache."""
        printers = self.registry.available()
        wanted = [p.uid for p in printers if uids is None or p.uid in uids]
        if wanted:
            with ThreadPoolExecutor(max_workers=min(8, len(wanted))) as pool:
                results = dict(zip(wanted, pool.map(self.query, wanted)))
            with self._lock:
                self._cache.update(results)
        with self._lock:
            present = {p.uid for p in printers}
            for uid in [uid for uid in self._cache if uid not in present]:
                del self._cache[uid]
        return self.snapshot()

    def query(self, uid: str) -> dict:
        status = {"checked": time.time()}
        try:
            channel = self.registry.channel(uid)
        except PrinterNotFound:
            return dict(status, state="offline", error="Printer not found")
        if not channel.transport.readable:
            return dict(status, state="unsupported")
        try:
            info = zpl.parse_hi(channel.transact(b"~HI").decode(self.encoding, errors="replace"))
            flags = zpl.parse_hqes(channel.transact(b"~HQES").decode(self.encoding, errors="replace"))
        except OSError as exc:
            log.info("Status of %s: offline (%s)", uid, exc)
            return dict(status, state="offline", error=str(exc) or exc.__class__.__name__)
        status.update(info or {})
        if flags is None:
            return dict(status, state="no_response")
        state = "error" if flags["errors"] else "warning" if flags["warnings"] else "ready"
        return dict(status, state=state, **flags)

    def dpi(self, uid: str):
        """Density reported by ~HI, when known."""
        with self._lock:
            return (self._cache.get(uid) or {}).get("dpi")
