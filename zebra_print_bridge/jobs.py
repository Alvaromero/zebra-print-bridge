"""Log of everything sent to the printers, for the viewer, and what each printer keeps (PrinterMemory).

In memory, and optionally on disk (JSON lines) so it survives a restart. Each job and each
response is appended as one line; the file is rewritten ("compacted") at startup and when it
grows past twice the limits. Compacting also keeps the ~DG graphics and the ^CI of every
printer, which stay on the printer even after the job that sent them leaves the history.
"""

import itertools
import json
import logging
import os
import threading
import time
from collections import deque
from pathlib import Path

from . import zpl

log = logging.getLogger(__name__)

MAX_STORED_BYTES = 4 * 1024 * 1024


class PrinterMemory:
    """What each printer keeps after a job: ~DG graphics (used later with ^XG) and the last ^CI."""

    def __init__(self):
        self._graphics = {}  # printer uid -> {name: ~DG command}
        self._charset = {}  # printer uid -> last ^CI seen
        self._lock = threading.Lock()

    def learn(self, uid: str, data: str):
        """Remember what a job sent successfully to that printer leaves on it."""
        graphics, charset = zpl.extract_graphics(data), zpl.last_charset(data)
        with self._lock:
            if graphics:
                self._graphics.setdefault(uid, {}).update(graphics)
            if charset is not None:
                self._charset[uid] = charset

    def charset(self, uid: str):
        with self._lock:
            return self._charset.get(uid)

    def graphics_for(self, uid: str, text: str) -> str:
        """~DG commands stored on that printer that the text uses with ^XG."""
        with self._lock:
            stored = self._graphics.get(uid, {})
            return "".join(stored[name] for name in sorted(zpl.referenced_graphics(text)) if name in stored)

    def snapshot(self) -> dict:
        """{uid: {"graphics": {...}, "charset": n}}, as stored in the history file."""
        with self._lock:
            return {
                uid: {"graphics": dict(self._graphics.get(uid, {})), "charset": self._charset.get(uid)}
                for uid in set(self._graphics) | set(self._charset)
            }

    def restore(self, printers: dict):
        with self._lock:
            for uid, state in printers.items():
                self._graphics.setdefault(uid, {}).update(state.get("graphics") or {})
                if state.get("charset") is not None:
                    self._charset[uid] = state["charset"]


class JobLog:
    def __init__(
        self, max_jobs: int = 300, encoding: str = "utf-8", path=None, max_bytes: int = 50 * 1024 * 1024, on_change=None
    ):
        """`on_change` is called after every change (the viewer redraws when its revision moves)."""
        self._jobs = deque(maxlen=max_jobs)
        self._by_id = {}
        self._seq = itertools.count(1)
        self._lock = threading.Lock()
        self._encoding = encoding
        self._on_change = on_change or (lambda: None)
        self.printers = PrinterMemory()  # survives clear(): the printers keep it
        self.path = Path(path) if path else None
        self.max_bytes = max_bytes
        self._file_lines = 0
        self._file_bytes = 0
        if self.path:
            self._load()

    def record(self, printer, data: str, error: str = None) -> dict:
        charset = self.printers.charset(printer.uid)
        job = {
            "id": next(self._seq),
            "time": time.time(),
            "uid": printer.uid,
            "printer": printer.name,
            "connection": printer.connection,
            "kind": zpl.classify(data),
            "bytes": len(data.encode("utf-8")),
            "labels": len(zpl.labels(data)),
            "warnings": zpl.lint(data, charset, self._encoding),
            "data": data[:MAX_STORED_BYTES],
            "truncated": len(data) > MAX_STORED_BYTES,
            "error": error,
            "response": None,
        }
        with self._lock:
            self._add(job)
            self._append({"job": job})
        self._on_change()
        return job

    def _add(self, job):
        """Add a job (lock held): history, and the graphics/charset it leaves on the printer."""
        if len(self._jobs) == self._jobs.maxlen:
            self._by_id.pop(self._jobs[0]["id"], None)
        self._jobs.append(job)
        self._by_id[job["id"]] = job
        if job["error"] is None:
            self.printers.learn(job["uid"], job["data"])

    def attach_response(self, uid: str, response: str):
        """Attach what was read to that printer's latest job (the write of sendThenRead)."""
        with self._lock:
            for job in reversed(self._jobs):
                if job["uid"] == uid:
                    job["response"] = response
                    self._append({"response": {"id": job["id"], "text": response}})
                    break
            else:
                return
        self._on_change()

    def summaries(self) -> list:
        with self._lock:
            return [{k: v for k, v in job.items() if k != "data"} for job in self._jobs]

    def get(self, job_id: int):
        with self._lock:
            return self._by_id.get(job_id)

    def clear(self):
        """Empty the history. The printers keep their graphics and charset, so those stay."""
        with self._lock:
            self._jobs.clear()
            self._by_id.clear()
            self._compact()
        self._on_change()

    # --- disk -----------------------------------------------------------------------

    def _load(self):
        try:
            lines = self.path.read_text(encoding="utf-8").splitlines()
        except FileNotFoundError:
            return
        except OSError as exc:
            log.warning("Cannot read the job history %s: %s", self.path, exc)
            return
        last_id, skipped = 0, 0
        for line in lines:
            try:
                entry = json.loads(line)
                if "job" in entry:
                    job = entry["job"]
                    self._add(job)
                    last_id = max(last_id, int(job["id"]))
                elif "response" in entry:
                    job = self._by_id.get(entry["response"]["id"])
                    if job is not None:
                        job["response"] = entry["response"]["text"]
                elif "printers" in entry:
                    self.printers.restore(entry["printers"])
            except (ValueError, KeyError, TypeError, AttributeError):
                skipped += 1
        if skipped:
            log.warning("Skipped %d unreadable lines in %s", skipped, self.path)
        self._seq = itertools.count(last_id + 1)
        log.info("Loaded %d jobs from %s", len(self._jobs), self.path)
        self._compact()

    def _append(self, entry):
        """Append one line (lock held); compact when the file is twice the limits."""
        if not self.path:
            return
        line = json.dumps(entry, ensure_ascii=False) + "\n"
        try:
            with self._open_private("a") as fh:
                fh.write(line)
        except OSError as exc:
            log.warning("Cannot write the job history %s: %s", self.path, exc)
            return
        self._file_lines += 1
        self._file_bytes += len(line.encode("utf-8"))
        if self._file_lines > 2 * self._jobs.maxlen or self._file_bytes > 2 * self.max_bytes:
            self._compact()

    def _compact(self):
        """Rewrite the file (lock held): printer state, then the newest jobs within max_bytes."""
        if not self.path:
            return
        header = json.dumps({"printers": self.printers.snapshot()}, ensure_ascii=False) + "\n"
        lines, kept, total = [], set(), len(header.encode("utf-8"))
        for job in reversed(self._jobs):
            line = json.dumps({"job": job}, ensure_ascii=False) + "\n"
            size = len(line.encode("utf-8"))
            if total + size > self.max_bytes:
                break
            lines.append(line)
            kept.add(job["id"])
            total += size
        lines.reverse()
        for job in [j for j in self._jobs if j["id"] not in kept]:  # too big for the file: forget them
            self._jobs.remove(job)
            self._by_id.pop(job["id"], None)
        tmp = self.path.with_name(self.path.name + ".tmp")
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self._open_private("w", tmp) as fh:
                fh.write(header)
                fh.writelines(lines)
            os.replace(tmp, self.path)
        except OSError as exc:
            log.warning("Cannot write the job history %s: %s", self.path, exc)
            return
        self._file_lines, self._file_bytes = len(lines) + 1, total

    def _open_private(self, mode, path=None):
        """Open with permissions 0600: labels can carry names and addresses."""
        path = path or self.path
        path.parent.mkdir(parents=True, exist_ok=True)
        flags = os.O_WRONLY | os.O_CREAT | (os.O_APPEND if mode == "a" else os.O_TRUNC)
        return os.fdopen(os.open(path, flags, 0o600), mode, encoding="utf-8")
