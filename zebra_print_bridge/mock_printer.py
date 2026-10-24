"""Simulated Zebra network printer (TCP), to test the network transport without hardware.

Usage: python3 -m zebra_print_bridge.mock_printer --host 127.0.0.2 --port 9100
(the bridge already uses 127.0.0.1:9100, hence another loopback address or port).
"""

import argparse
import logging
import socketserver
import threading

from .zpl import ERROR_FLAGS, hqes_response

log = logging.getLogger(__name__)


class MockPrinter(socketserver.ThreadingTCPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, host="127.0.0.1", port=0):
        self.received = bytearray()
        self.received_event = threading.Condition()
        self.errors = []  # ERROR_FLAGS keys reported by ~HQES
        super().__init__((host, port), _Handler)

    @property
    def address(self):
        return self.server_address[:2]

    def wait_for(self, needle: bytes, timeout=2.0) -> bool:
        with self.received_event:
            return self.received_event.wait_for(lambda: needle in self.received, timeout)

    def start(self):
        thread = threading.Thread(target=self.serve_forever, daemon=True)
        thread.start()
        return self

    def stop(self):
        self.shutdown()
        self.server_close()


class _Handler(socketserver.BaseRequestHandler):
    def handle(self):
        pending = b""
        while True:
            chunk = self.request.recv(4096)
            if not chunk:
                return
            with self.server.received_event:
                self.server.received += chunk
                self.server.received_event.notify_all()
            log.info("received %d bytes: %r", len(chunk), chunk[:80])
            pending += chunk
            while b"~HQES" in pending:
                pending = pending.split(b"~HQES", 1)[1]
                self.request.sendall(hqes_response(self.server.errors))
            pending = pending[-8:]


def main():
    parser = argparse.ArgumentParser(description="Simulated Zebra network printer (TCP)")
    parser.add_argument("--host", default="127.0.0.2")
    parser.add_argument("--port", type=int, default=9100)
    parser.add_argument("--error", action="append", default=[], choices=sorted(ERROR_FLAGS),
                        help="error reported by ~HQES (repeatable)")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    printer = MockPrinter(args.host, args.port)
    printer.errors = args.error
    log.info("Simulated printer on %s:%s", *printer.address)
    try:
        printer.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
