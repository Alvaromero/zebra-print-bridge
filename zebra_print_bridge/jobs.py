"""In-memory log of everything sent to the printers, for the viewer."""

import itertools
import threading
import time
from collections import deque

from . import zpl

MAX_STORED_BYTES = 4 * 1024 * 1024


class JobLog:
    def __init__(self, max_jobs: int = 300, encoding: str = "utf-8"):
        self._jobs = deque(maxlen=max_jobs)
        self._by_id = {}
        self._seq = itertools.count(1)
        self._lock = threading.Lock()
        self._encoding = encoding
        self._graphics = {}  # printer uid -> {name: ~DG command}
        self._charset = {}  # printer uid -> last ^CI seen (it persists on the printer)
        self.rev = 0  # changes on every update; the viewer only redraws when it changes

    def record(self, printer, data: str, error: str = None) -> dict:
        with self._lock:
            charset = self._charset.get(printer.uid)
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
            if len(self._jobs) == self._jobs.maxlen:
                self._by_id.pop(self._jobs[0]["id"], None)
            self._jobs.append(job)
            self._by_id[job["id"]] = job
            if error is None:
                self._graphics.setdefault(printer.uid, {}).update(zpl.extract_graphics(data))
                new_charset = zpl.last_charset(data)
                if new_charset is not None:
                    self._charset[printer.uid] = new_charset
            self.rev += 1
        return job

    def attach_response(self, uid: str, response: str):
        """Attach what was read to that printer's latest job (the write of sendThenRead)."""
        with self._lock:
            for job in reversed(self._jobs):
                if job["uid"] == uid:
                    job["response"] = response
                    self.rev += 1
                    return

    def summaries(self) -> list:
        with self._lock:
            return [{k: v for k, v in job.items() if k != "data"} for job in self._jobs]

    def get(self, job_id: int):
        with self._lock:
            return self._by_id.get(job_id)

    def charset(self, uid: str):
        with self._lock:
            return self._charset.get(uid)

    def graphics_for(self, uid: str, text: str) -> str:
        """~DG commands stored on that printer that the text uses with ^XG."""
        with self._lock:
            stored = self._graphics.get(uid, {})
            return "".join(stored[name] for name in sorted(zpl.referenced_graphics(text)) if name in stored)

    def bump(self):
        """Flag a change outside the jobs (e.g. printers) so the viewer redraws."""
        with self._lock:
            self.rev += 1

    def clear(self):
        with self._lock:
            self._jobs.clear()
            self._by_id.clear()
            self.rev += 1
