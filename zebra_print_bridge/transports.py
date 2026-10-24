"""Low-level transports: USB (usblp), network (TCP 9100) and CUPS (write only)."""

import os
import select
import socket
import subprocess
import time


class Transport:
    """Raw byte channel to a printer. Not thread-safe: Channel guards it."""

    # False when the response is already available once the write returns (no need to wait).
    responds_later = True
    # False when the printer's answers cannot be read at all (CUPS).
    readable = True

    def write(self, data: bytes, timeout: float) -> None:
        raise NotImplementedError

    def read(self, timeout: float) -> bytes:
        """Return the available bytes, waiting up to `timeout`; b"" if nothing arrives."""
        return b""

    def drain(self) -> None:
        """Discard pending input so /read only returns the response to the latest write."""
        while self.read(0):
            pass

    def close(self) -> None:
        pass


class NetworkTransport(Transport):
    def __init__(self, host: str, port: int, connect_timeout: float):
        self.host = host
        self.port = port
        self.connect_timeout = connect_timeout
        self.sock = None

    def _connect(self):
        if self.sock is None:
            sock = socket.create_connection((self.host, self.port), timeout=self.connect_timeout)
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            self.sock = sock

    def write(self, data, timeout):
        # drain() also notices when the printer closed the connection (recv == b"") and drops it.
        self.drain()
        for attempt in (1, 2):
            try:
                self._connect()
                self.sock.settimeout(timeout)
                self.sock.sendall(data)
                return
            except OSError:
                self.close()
                if attempt == 2:
                    raise

    def read(self, timeout):
        if self.sock is None:
            return b""
        try:
            ready, _, _ = select.select([self.sock], [], [], max(timeout, 0))
            if not ready:
                return b""
            chunk = self.sock.recv(4096)
        except OSError:
            self.close()
            return b""
        if not chunk:
            self.close()
        return chunk

    def close(self):
        if self.sock is not None:
            try:
                self.sock.close()
            finally:
                self.sock = None


class UsbTransport(Transport):
    """/dev/usb/lpX device from the usblp driver. usblp allows a single open at a time."""

    def __init__(self, path: str):
        self.path = path
        self.fd = None

    def _open(self):
        if self.fd is None:
            self.fd = os.open(self.path, os.O_RDWR | os.O_NONBLOCK | os.O_CLOEXEC)

    def write(self, data, timeout):
        self._open()
        deadline = time.monotonic() + timeout
        view = memoryview(data)
        try:
            while view:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError(f"timeout writing to {self.path}")
                _, writable, _ = select.select([], [self.fd], [], remaining)
                if not writable:
                    continue
                try:
                    written = os.write(self.fd, view)
                except BlockingIOError:
                    continue
                view = view[written:]
        except OSError:
            self.close()
            raise

    def read(self, timeout):
        if self.fd is None:
            return b""
        try:
            ready, _, _ = select.select([self.fd], [], [], max(timeout, 0))
            if not ready:
                return b""
            return os.read(self.fd, 4096)
        except BlockingIOError:
            return b""
        except OSError as exc:
            # EINVAL: the printer is not bidirectional. Any other error: it was unplugged.
            if exc.errno != 22:
                self.close()
            return b""

    def close(self):
        if self.fd is not None:
            try:
                os.close(self.fd)
            finally:
                self.fd = None


class CupsTransport(Transport):
    """Send to a CUPS queue in raw mode. CUPS does not return the printer's response."""

    readable = False

    def __init__(self, queue: str):
        self.queue = queue

    def write(self, data, timeout):
        result = subprocess.run(
            ["lp", "-d", self.queue, "-o", "raw"],
            input=data,
            capture_output=True,
            timeout=timeout,
        )
        if result.returncode != 0:
            raise OSError(result.stderr.decode(errors="replace").strip() or "lp failed")
