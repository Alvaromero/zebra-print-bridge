"""Configuration: DEFAULTS, then /etc/zebra-print-bridge/config.json (the organization's, for every
user of the machine), then ~/.config/zebra-print-bridge/config.json (the user's)."""

import codecs
import copy
import difflib
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
    # Jobs kept by the viewer.
    "max_jobs": 300,
    # Keep the job history on disk so it survives a restart (labels may carry personal data:
    # the file is only readable by the user). null = ~/.local/state/zebra-print-bridge/history.jsonl
    "persist_history": True,
    "history_file": None,
    # Size limit of the history file; the oldest jobs go first.
    "history_max_mb": 50,
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


def state_dir() -> Path:
    base = os.environ.get("XDG_STATE_HOME") or os.path.expanduser("~/.local/state")
    return Path(base) / "zebra-print-bridge"


SYSTEM_CONFIG = Path("/etc/zebra-print-bridge/config.json")


def system_config_path() -> Path:
    """The organization's file. ZEBRA_PRINT_BRIDGE_SYSTEM_CONFIG overrides it (tests, other layouts)."""
    return Path(os.environ.get("ZEBRA_PRINT_BRIDGE_SYSTEM_CONFIG") or SYSTEM_CONFIG)


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


class ConfigError(Exception):
    """config.json cannot be used: invalid JSON or a setting of the wrong type."""


def _read_json(path: Path):
    """The file's JSON object, or None when the file does not exist."""
    if not path.exists():
        return None
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except json.JSONDecodeError as exc:
        raise ConfigError(f"{path}: invalid JSON at line {exc.lineno}, column {exc.colno}: {exc.msg}") from None
    except (OSError, UnicodeDecodeError) as exc:
        raise ConfigError(f"{path}: cannot read it: {exc}") from None
    if not isinstance(data, dict):
        raise ConfigError(f"{path}: must be a JSON object ({{...}})")
    return data


def load_config(path=None, overrides=None, system_path=None) -> dict:
    """DEFAULTS, then the organization's file, then the user's file, then `overrides`; then checked.

    The user's file wins key by key, except allowed_origins: when both files set it, the user's
    websites are added to the organization's instead of replacing them.

    Raises ConfigError for problems that would break the service later in a confusing way.
    Unknown keys (usually typos) only produce warnings, kept in config["_warnings"] for the
    caller to log once logging is set up.
    """
    config = copy.deepcopy(DEFAULTS)
    path = Path(path) if path else default_config_path()
    system_path = Path(system_path) if system_path else system_config_path()
    loaded = []
    origins = []  # allowed_origins of every file that sets it, in order
    for file in (system_path, path):
        if loaded and file.resolve() == Path(loaded[0]).resolve():
            continue  # --config pointing at the organization's file
        data = _read_json(file)
        if data is None:
            continue
        loaded.append(str(file))
        if isinstance(data.get("allowed_origins"), list):
            origins += [o for o in data["allowed_origins"] if o not in origins]
        config = _merge(config, data)
    if len(loaded) == 2 and isinstance(config["allowed_origins"], list):
        config["allowed_origins"] = origins
    if overrides:
        config = _merge(config, overrides)
    config["_config_file"] = str(path)  # the one a user edits
    config["_config_files"] = loaded  # the ones actually read, organization's first
    source = " + ".join(loaded) or str(path)
    errors, warnings = check(config)
    if errors:
        raise ConfigError(f"{source}:\n  " + "\n  ".join(errors + warnings))
    config["_warnings"] = [f"{source}: {warning}" for warning in warnings]
    return config


# --- checks ------------------------------------------------------------------------------

NULLABLE_TEXT = {"default_printer", "virtual_printers_file", "settings_file", "history_file", "labelary_url"}
INTEGERS = {"port", "max_jobs"}
NETWORK_PRINTER_KEYS = {"host", "port", "name", "uid"}
OVERRIDE_KEYS = {"uid", "name"}
USB_KEYS = {"enabled"}
LOG_LEVELS = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}


def _is_number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _type_error(key: str, value) -> str | None:
    """Why `value` cannot be used for `key`, judged by its default; None when it can."""
    default = DEFAULTS[key]
    if key in NULLABLE_TEXT:
        ok, expected = value is None or isinstance(value, str), "text or null"
    elif isinstance(default, bool):
        ok, expected = isinstance(value, bool), "true or false"
    elif key in INTEGERS:
        ok, expected = isinstance(value, int) and not isinstance(value, bool) and value >= 0, "a whole number"
    elif _is_number(default):
        ok, expected = _is_number(value) and value >= 0, "a number"
    elif isinstance(default, str):
        ok, expected = isinstance(value, str), "text"
    elif isinstance(default, list):
        ok, expected = isinstance(value, list), "a list ([...])"
    else:
        ok, expected = isinstance(value, dict), "an object ({...})"
    return None if ok else f"{key} must be {expected}, not {json.dumps(value)}"


def _unknown(keys, known, where: str) -> list:
    warnings = []
    for key in sorted(set(keys) - set(known)):
        hint = difflib.get_close_matches(key, known, n=1)
        suggestion = f" (did you mean {hint[0]!r}?)" if hint else ""
        warnings.append(f"unknown setting {where}{key!r} is ignored{suggestion}")
    return warnings


def _valid_port(value) -> bool:
    try:
        return 0 < int(value) < 65536 and not isinstance(value, bool)
    except (TypeError, ValueError):
        return False


def check(config: dict) -> tuple:
    """(errors, warnings) for a merged configuration. Keys starting with "_" are internal.

    Every problem is reported at once; the detailed checks of a key run only when its type is right.
    """
    type_errors = {key: e for key in DEFAULTS if (e := _type_error(key, config[key]))}
    errors = list(type_errors.values())
    warnings = _unknown([k for k in config if not k.startswith("_")], list(DEFAULTS), "")

    def valid(key):
        return key not in type_errors

    if valid("port") and not 0 <= config["port"] < 65536:
        errors.append(f"port must be between 1 and 65535, not {config['port']}")
    if valid("encoding"):
        try:
            codecs.lookup(config["encoding"])
        except LookupError:
            errors.append(f"encoding {config['encoding']!r} is not a known text encoding")
    if valid("log_level") and config["log_level"].upper() not in LOG_LEVELS:
        warnings.append(f"log_level {config['log_level']!r} is not one of {sorted(LOG_LEVELS)}; using INFO")
    for key in ("allowed_origins", "cups_queues"):
        if valid(key) and not all(isinstance(item, str) and item for item in config[key]):
            errors.append(f"{key} must be a list of texts")
    if valid("usb"):
        if not isinstance(config["usb"].get("enabled", True), bool):
            errors.append("usb.enabled must be true or false")
        warnings += _unknown(config["usb"], USB_KEYS, "usb.")

    for i, printer in enumerate(config["network_printers"] if valid("network_printers") else []):
        where = f"network_printers[{i}]"
        if not isinstance(printer, dict):
            errors.append(f'{where} must be an object like {{"host": "10.0.0.50"}}')
            continue
        if not isinstance(printer.get("host"), str) or not printer["host"].strip():
            errors.append(f"{where}.host is required (an IP address or a host name)")
        if "port" in printer and not _valid_port(printer["port"]):
            errors.append(f"{where}.port must be between 1 and 65535, not {json.dumps(printer['port'])}")
        errors += [
            f"{where}.{k} must be text" for k in ("name", "uid") if k in printer and not isinstance(printer[k], str)
        ]
        warnings += _unknown(printer, NETWORK_PRINTER_KEYS, f"{where}.")

    for uid, override in config["overrides"].items() if valid("overrides") else []:
        where = f"overrides[{uid!r}]"
        if not isinstance(override, dict):
            errors.append(f'{where} must be an object like {{"name": "Dock 1"}}')
            continue
        errors += [
            f"{where}.{k} must be text" for k in OVERRIDE_KEYS if k in override and not isinstance(override[k], str)
        ]
        warnings += _unknown(override, OVERRIDE_KEYS, f"{where}.")
    return errors, warnings
