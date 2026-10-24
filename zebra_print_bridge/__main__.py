"""CLI: `python3 -m zebra_print_bridge [--config PATH] [--list]`."""

import argparse
import json
import logging
import signal
import sys

from . import __version__
from .config import default_config_path, load_config
from .devices import Registry
from .server import BridgeServer


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="zebra-print-bridge",
        description="Local replacement for Zebra Browser Print on Linux.")
    parser.add_argument("--config", help=f"JSON file (default {default_config_path()})")
    parser.add_argument("--host", help="listen address (default 127.0.0.1)")
    parser.add_argument("--port", type=int, help="listen port (default 9100)")
    parser.add_argument("--log-level", help="DEBUG, INFO, WARNING...")
    parser.add_argument("--list", action="store_true", help="list detected printers and exit")
    parser.add_argument("--version", action="version", version=__version__)
    args = parser.parse_args(argv)

    overrides = {k: v for k, v in {"host": args.host, "port": args.port,
                                   "log_level": args.log_level}.items() if v is not None}
    config = load_config(args.config, overrides)
    logging.basicConfig(
        level=getattr(logging, str(config["log_level"]).upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    if args.list:
        registry = Registry(config)
        default = registry.default()
        for info in registry.available():
            mark = "*" if default and info.uid == default.uid else " "
            print(f"{mark} {json.dumps(info.to_device(), ensure_ascii=False)}")
        return 0

    if config["host"] not in ("127.0.0.1", "localhost", "::1"):
        logging.warning("Listening on %s: anyone on the network could use the printers",
                        config["host"])

    server = BridgeServer(config)
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    logging.info("Zebra Print Bridge %s listening on http://%s:%s/ (viewer at /ui)",
                 __version__, config["host"], config["port"])
    for info in server.registry.available():
        logging.info("  %s (%s, uid=%s)", info.name, info.connection, info.uid)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    if server.stopped_from_viewer:
        # Exit code 0: systemd (Restart=on-failure) does not start it again.
        logging.info("Stopped from the viewer. Start it again with: "
                     "systemctl --user start zebra-print-bridge")
    return 0


if __name__ == "__main__":
    sys.exit(main())
