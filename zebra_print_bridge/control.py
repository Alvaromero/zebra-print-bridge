"""Start, stop and open the service from the desktop: `zebra-print-bridge-ctl open|start|stop|status|autostart`
(and `diagnose`, see diagnose.py).

Used by the application menu entries and by the top-bar icon. When the systemd user service
is installed it goes through systemctl; otherwise (development) it starts the bridge as a
background process and stops it through the viewer's API.

Starting at login is a per-user choice: the usual `systemctl --user enable/disable` link.
Versions before 0.3.0 enabled the service for every user (`systemctl --global enable`); that
link can only be removed with sudo, and the installers remove it when upgrading.
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from .config import ConfigError, load_config

UNIT = "zebra-print-bridge.service"
GLOBAL_WANTS = Path("/etc/systemd/user/default.target.wants") / UNIT
PACKAGE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class Control:
    def __init__(self, port: int = None, config_path: str = None, config: dict = None):
        config = config or load_config(config_path)
        self.config_port = config["port"]
        self.port = port or config["port"]
        self.base = f"http://127.0.0.1:{self.port}"
        self.viewer_url = f"{self.base}/ui"

    # --- state ------------------------------------------------------------------------

    def state(self, timeout: float = 1.0):
        """The viewer's state, or None when the service does not answer."""
        try:
            with urllib.request.urlopen(f"{self.base}/ui/api/state", timeout=timeout) as response:
                return json.loads(response.read())
        except (OSError, ValueError):
            return None

    def running(self) -> bool:
        return self.state() is not None

    @staticmethod
    def _systemctl(*args, check=False):
        if not shutil.which("systemctl"):
            return None
        return subprocess.run(["systemctl", "--user", *args], capture_output=True, text=True, check=check)

    def systemd_installed(self) -> bool:
        """The user unit exists and serves the port we control (it uses config.json's port)."""
        if self.port != self.config_port:
            return False
        result = self._systemctl("cat", UNIT)
        return bool(result and result.returncode == 0)

    def _service_pid(self):
        try:
            with urllib.request.urlopen(f"{self.base}/ui/api/settings", timeout=1) as response:
                return json.loads(response.read())["service"]["pid"]
        except (OSError, ValueError, KeyError):
            return None

    def _systemd_pid(self):
        result = self._systemctl("show", "--property=MainPID", "--value", UNIT)
        try:
            return int(result.stdout.strip()) if result and result.returncode == 0 else None
        except ValueError:
            return None

    def managed_by_systemd(self) -> bool:
        """True only when the process answering on our port is the systemd service's."""
        pid = self._service_pid()
        return pid is not None and pid == self._systemd_pid()

    def wait(self, running: bool, timeout: float = 10.0) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.running() == running:
                return True
            time.sleep(0.2)
        return self.running() == running

    # --- actions ----------------------------------------------------------------------

    def start(self) -> str:
        if self.running():
            return "already running"
        if self.systemd_installed():
            result = self._systemctl("start", UNIT)
            if result.returncode != 0:
                raise RuntimeError(result.stderr.strip() or "systemctl start failed")
        else:
            # Development: a detached process that outlives this command.
            subprocess.Popen(
                [sys.executable, "-m", "zebra_print_bridge", "--port", str(self.port)],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
                env=dict(os.environ, PYTHONPATH=PACKAGE_ROOT),
            )
        if not self.wait(True):
            raise RuntimeError("The service did not start. Check: journalctl --user -u zebra-print-bridge -n 50")
        return "started"

    def stop(self) -> str:
        if not self.running():
            return "not running"
        if self.managed_by_systemd():
            result = self._systemctl("stop", UNIT)
            if result.returncode != 0:
                raise RuntimeError(result.stderr.strip() or "systemctl stop failed")
        else:
            request = urllib.request.Request(
                f"{self.base}/ui/api/shutdown",
                data=b"{}",
                method="POST",
                headers={"Content-Type": "application/json", "Origin": self.base},
            )
            try:
                urllib.request.urlopen(request, timeout=3).close()
            except urllib.error.URLError as exc:
                raise RuntimeError(f"Could not stop the service: {exc.reason}") from exc
        if not self.wait(False):
            raise RuntimeError("The service is still running")
        return "stopped"

    def open(self) -> str:
        message = self.start() if not self.running() else "running"
        opener = shutil.which("xdg-open")
        if not opener:
            raise RuntimeError(f"xdg-open not found; open {self.viewer_url}")
        subprocess.Popen(
            [opener, self.viewer_url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True
        )
        return message

    def status(self) -> dict:
        state = self.state()
        if state is None:
            return {"running": False, "url": self.viewer_url}
        return {
            "running": True,
            "url": self.viewer_url,
            "version": state.get("version"),
            "printers": len(state.get("printers", [])),
            "pending_origins": [p["origin"] for p in state.get("pending_origins", [])],
        }


# --- start at login ---------------------------------------------------------------------


def _systemctl(*args):
    if not shutil.which("systemctl"):
        return None
    return subprocess.run(["systemctl", "--user", *args], capture_output=True, text=True, check=False)


def user_wants_link() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return Path(base) / "systemd" / "user" / "default.target.wants" / UNIT


def autostart_state() -> dict:
    """{"available": the user unit is installed, "enabled": it starts at login for this user,
    "global": an older installer enabled it for every user (the user cannot turn that off)}."""
    result = _systemctl("is-enabled", UNIT)
    value = result.stdout.strip() if result else ""
    available = value not in ("", "not-found")
    is_global = GLOBAL_WANTS.is_symlink() or GLOBAL_WANTS.exists()
    return {
        "available": available,
        "enabled": available and (user_wants_link().exists() or is_global),
        "global": is_global,
        "masked": value.startswith("masked"),
    }


def set_autostart(enabled: bool) -> dict:
    state = autostart_state()
    if not state["available"]:
        raise RuntimeError("The systemd user service is not installed (install.sh or the .deb install it)")
    if state["masked"]:
        _systemctl("unmask", UNIT)
    result = _systemctl("enable" if enabled else "disable", UNIT)
    if result is None or result.returncode != 0:
        raise RuntimeError((result.stderr.strip() if result else "") or "systemctl failed")
    state = autostart_state()
    if not enabled and state["global"]:
        raise RuntimeError(
            "An older installer enabled the service for every user. Turn that off with: "
            f"sudo systemctl --global disable {UNIT}"
        )
    return state


def notify(title: str, body: str = ""):
    if shutil.which("notify-send"):
        subprocess.run(
            ["notify-send", "--app-name=Zebra Print Bridge", "--icon=zebra-print-bridge", title, body],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )


def main(argv=None):
    parser = argparse.ArgumentParser(prog="zebra-print-bridge-ctl", description=__doc__.splitlines()[0])
    parser.add_argument("action", choices=["open", "start", "stop", "status", "autostart", "diagnose"])
    parser.add_argument(
        "value",
        nargs="?",
        choices=["on", "off", "status"],
        help="for autostart: start the service at login (on/off) or show it (status)",
    )
    parser.add_argument("--port", type=int, help="port of the service (default: from config.json)")
    parser.add_argument("--config", help="path of config.json")
    parser.add_argument("--notify", action="store_true", help="report errors with a desktop notification")
    parser.add_argument("--json", action="store_true", help="for diagnose: print the report as JSON")
    parser.add_argument("--no-query", action="store_true", help="for diagnose: do not query the printers")
    args = parser.parse_args(argv)
    if args.action == "diagnose":  # works even with an invalid config.json: it reports it
        from . import diagnose

        return diagnose.main(args.config, args.port, as_json=args.json, query=not args.no_query)
    try:
        control = Control(args.port, args.config)
    except ConfigError as exc:
        message = f"Invalid configuration: {exc}"
        if args.notify:
            notify("Zebra Print Bridge", message)
        print(message, file=sys.stderr)
        return 1
    try:
        if args.action == "autostart":
            result = autostart_state() if args.value in (None, "status") else set_autostart(args.value == "on")
        else:
            result = getattr(control, args.action)()
    except RuntimeError as exc:
        if args.notify:
            notify("Zebra Print Bridge", str(exc))
        print(exc, file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2) if isinstance(result, dict) else result)
    return 0


if __name__ == "__main__":
    sys.exit(main())
