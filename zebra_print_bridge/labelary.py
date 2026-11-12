"""Label preview through the Labelary API (https://labelary.com/service.html).

It sends the ZPL off the machine, so the viewer only calls it after the user turns preview on.
Free plan limits: 3 requests per second and 5000 per day; answers are cached.
"""

import hashlib
import json
import threading
import urllib.error
import urllib.request
from collections import OrderedDict
from dataclasses import dataclass

from .web import HttpError

MAX_BODY = 1024 * 1024
MAX_LABELS = 50
TIMEOUT = 20
DPMM = {152: 6, 203: 8, 300: 12, 600: 24}


@dataclass(frozen=True)
class Preview:
    content: bytes
    content_type: str
    total: str  # X-Total-Count: labels in the ZPL
    warnings: list  # linter warnings, byte positions relative to the label (see parse_warnings)


class PreviewCache:
    """Small LRU, shared by every request."""

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


def parse_warnings(header: str, offset: int) -> list:
    """X-Warnings: byte|size|command|parameter|message, repeated, pipe-delimited.

    `offset` is the length of what was prepended to the label (stored ~DG graphics): warnings
    inside it are dropped and the rest are shifted so they point into the label itself.
    """
    parts = header.split("|") if header else []
    warnings = []
    for i in range(0, len(parts) - 4, 5):
        byte_index, size, command, parameter, message = parts[i : i + 5]
        try:
            position = int(byte_index) - offset
        except ValueError:
            continue
        if position < 0:
            continue
        warnings.append(
            {
                "byte": position,
                "size": int(size or 0),
                "command": command,
                "parameter": int(parameter) if parameter else None,
                "message": message,
            }
        )
    return warnings


class LabelaryClient:
    def __init__(self, base_url: str):
        self.base_url = base_url.rstrip("/")
        self.cache = PreviewCache()

    def render(self, payload: bytes, index, options: dict, prefix_bytes: int = 0) -> Preview:
        """Render `payload` (ZPL). `index` picks one label (None = every label, PDF only).

        `options`: dpi, width and height (inches), rotation, quality, format (png/pdf), lint.
        `prefix_bytes`: length of the graphics prepended to the label, for parse_warnings.
        """
        if len(payload) > MAX_BODY:
            raise HttpError(413, "Too large for Labelary (1 MB maximum)")
        key = hashlib.sha256(payload + json.dumps([index, options], sort_keys=True).encode()).hexdigest()
        cached = self.cache.get(key)
        if cached is None:
            cached = self._request(payload, index, options, prefix_bytes)
            self.cache.put(key, cached)
        return cached

    def _request(self, payload: bytes, index, options: dict, prefix_bytes: int) -> Preview:
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
        request = urllib.request.Request(self.base_url + path, data=payload, method="POST", headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
                return Preview(
                    content=response.read(),
                    content_type=response.headers.get("Content-Type", headers["Accept"]),
                    total=response.headers.get("X-Total-Count", ""),
                    warnings=parse_warnings(response.headers.get("X-Warnings", ""), prefix_bytes),
                )
        except urllib.error.HTTPError as exc:
            with exc:
                detail = exc.read().decode(errors="replace").strip()
            if exc.code == 429:
                raise HttpError(
                    429, "Labelary rate limit reached (3 requests per second on the free plan). Try again in a moment."
                ) from None
            raise HttpError(502, f"Labelary {exc.code}: {detail[:300]}") from None
        except OSError as exc:
            raise HttpError(502, f"Labelary unreachable: {exc}") from None
