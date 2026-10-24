"""Configuration loaded from ~/.config/zebra-print-bridge/config.json."""

import copy
import json
import os
from pathlib import Path

DEFAULTS = {
    # Listen on loopback only: anyone on the network could otherwise print or read the printers.
    "host": "127.0.0.1",
    "port": 9100,
    # Websites pre-approved to use the printers (fnmatch patterns against the Origin header).
    # Any other website has to be approved by the user in the viewer, like Browser Print's
    # "accept host" prompt.
    "allowed_origins": [
        "https://recommercebrands.com",
        "https://*.recommercebrands.com",
        "https://localhost",
        "https://localhost:*",
        "http://localhost",
        "http://localhost:*",
    ],
    # false = no approval prompt: only allowed_origins, and an empty list allows any website.
    "origin_approval": True,
    # Desktop notification (notify-send) when a new website asks to use the printers.
    "desktop_notifications": True,
    # uid of the default printer. null = the first one detected. The viewer's setting wins.
    "default_printer": None,
    "usb": {"enabled": True},
    # Virtual printers (managed from the viewer at http://127.0.0.1:9100/ui).
    "virtual_printers": True,
    # null = ~/.local/state/zebra-print-bridge/virtual-printers.json
    "virtual_printers_file": None,
    # What the user changes in the viewer. null = ~/.local/state/zebra-print-bridge/settings.json
    "settings_file": None,
    # Jobs kept in memory by the viewer.
    "max_jobs": 300,
    # ZPL preview service. The viewer only calls it after the user turns preview on,
    # because it sends the ZPL off the machine. null disables it completely.
    "labelary_url": "https://api.labelary.com",
    # [{"host": "10.0.0.50", "port": 9100, "name": "Zebra Dock 1", "uid": "optional"}]
    # The viewer can add more; they are stored in settings_file.
    "network_printers": [],
    # Raw CUPS queues (write only, no status reads).
    "cups_queues": [],
    # Rename detected printers or pin their uid: {"usb:XXXX": {"uid": "...", "name": "..."}}
    "overrides": {},
    "encoding": "utf-8",
    # Seconds /read waits for the printer's first bytes.
    "read_timeout": 2.5,
    # Seconds of silence, after data arrives, that mark the response as complete.
    "read_idle": 0.15,
    "connect_timeout": 3.0,
    "write_timeout": 30.0,
    # Seconds the printer connection stays open after the last operation.
    # Network Zebras usually accept a single connection at a time on port 9100.
    "connection_linger": 5.0,
    "log_level": "INFO",
}


def default_config_path() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return Path(base) / "zebra-print-bridge" / "config.json"


def _merge(base: dict, override: dict) -> dict:
    result = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _merge(result[key], value)
        else:
            result[key] = value
    return result


def load_config(path=None, overrides=None) -> dict:
    """DEFAULTS merged with the file (when it exists) and with `overrides`."""
    config = copy.deepcopy(DEFAULTS)
    path = Path(path) if path else default_config_path()
    if path.exists():
        with open(path, encoding="utf-8") as fh:
            config = _merge(config, json.load(fh))
    if overrides:
        config = _merge(config, overrides)
    config["_config_file"] = str(path)
    return config
