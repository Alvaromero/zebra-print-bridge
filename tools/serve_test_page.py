#!/usr/bin/env python3
"""Serve tests/e2e/browser.html over HTTPS (self-signed certificate) to test in Chrome/Firefox.

Usage: python3 tools/serve_test_page.py [--port 8443]  ->  https://localhost:8443/browser.html
"""

import argparse
import functools
import http.server
import ssl
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent / "tests" / "e2e"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8443)
    args = parser.parse_args()

    with tempfile.TemporaryDirectory() as tmp:
        cert, key = Path(tmp, "cert.pem"), Path(tmp, "key.pem")
        subprocess.run(
            ["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1",
             "-subj", "/CN=localhost", "-keyout", str(key), "-out", str(cert)],
            check=True, capture_output=True,
        )
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(cert, key)
        handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(ROOT))
        server = http.server.ThreadingHTTPServer(("127.0.0.1", args.port), handler)
        server.socket = context.wrap_socket(server.socket, server_side=True)
        print(f"Open https://localhost:{args.port}/browser.html (accept the self-signed certificate)")
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
