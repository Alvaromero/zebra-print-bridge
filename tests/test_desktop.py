import os
import socket
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests.helpers import BridgeFixture
from zebra_print_bridge import control, tray
from zebra_print_bridge.control import Control


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class DescribeTest(unittest.TestCase):
    def test_stopped(self):
        view = tray.describe(None, "es")
        self.assertEqual(
            (view["icon"], view["running"], view["lines"]), ("tray-stopped-symbolic", False, ["Servicio detenido"])
        )

    def test_ready(self):
        state = {
            "printers": [{"uid": "a", "name": "ZD421"}],
            "printer_status": {"a": {"state": "ready"}},
            "pending_origins": [],
        }
        view = tray.describe(state)
        self.assertEqual(view["icon"], "tray-ready-symbolic")
        self.assertEqual(view["lines"], ["Running · 1 printer(s)"])
        self.assertEqual(view["printers"], ["ZD421 — ready"])

    def test_attention(self):
        state = {
            "printers": [{"uid": "a", "name": "ZD421"}, {"uid": "b", "name": "Dock"}],
            "printer_status": {"a": {"state": "error", "errors": ["media_out"]}},
            "pending_origins": [{"origin": "https://shop.example.com"}],
        }
        view = tray.describe(state)
        self.assertTrue(view["attention"])
        self.assertEqual(view["icon"], "tray-attention-symbolic")
        self.assertEqual(view["lines"][1], "1 website(s) waiting for approval")
        self.assertEqual(view["printers"], ["ZD421 — media_out", "Dock"])

    def test_language(self):
        with mock.patch.dict(os.environ, {"LANGUAGE": "", "LC_ALL": "", "LC_MESSAGES": "", "LANG": "es_CO.UTF-8"}):
            self.assertEqual(tray.language(), "es")
        with mock.patch.dict(os.environ, {"LANGUAGE": "ja_JP", "LANG": "es_CO.UTF-8"}):
            self.assertEqual(tray.language(), "en")  # the first preference wins, even if unsupported
        for lang, expected in (
            ("pt_BR.UTF-8", "pt-BR"),
            ("pt_PT.UTF-8", "pt-BR"),
            ("fr_CA.UTF-8", "fr"),
            ("de_AT.UTF-8", "de"),
            ("ja_JP.UTF-8", "en"),
        ):
            with mock.patch.dict(os.environ, {"LANGUAGE": "", "LC_ALL": "", "LC_MESSAGES": "", "LANG": lang}):
                self.assertEqual(tray.language(), expected, lang)

    def test_translations_complete(self):
        for lang, text in tray.TEXT.items():
            self.assertEqual(text.keys(), tray.TEXT["en"].keys(), lang)
            self.assertIn("{printers}", text["running"], lang)
            self.assertIn("{count}", text["pending"], lang)
            view = tray.describe({"printers": [{"uid": "u", "name": "P"}], "pending_origins": ["https://a"]}, lang)
            self.assertEqual(len(view["lines"]), 2, lang)


class AutostartTest(unittest.TestCase):
    def test_user_override_of_the_system_entry(self):
        with tempfile.TemporaryDirectory() as home, tempfile.TemporaryDirectory() as system:
            (Path(system) / "autostart").mkdir()
            (Path(system) / "autostart" / tray.AUTOSTART_NAME).write_text("[Desktop Entry]\n")
            with mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": home, "XDG_CONFIG_DIRS": system}):
                self.assertTrue(tray.autostart_enabled())
                tray.set_autostart(False)
                self.assertFalse(tray.autostart_enabled())
                self.assertIn("Hidden=true", tray.autostart_path().read_text())
                tray.set_autostart(True)
                self.assertTrue(tray.autostart_enabled())

    def test_desktop_files_are_valid(self):
        import shutil
        import subprocess

        if not shutil.which("desktop-file-validate"):
            self.skipTest("desktop-file-validate is not installed")
        files = sorted(str(p) for p in (Path(__file__).parent.parent / "packaging").glob("*.desktop"))
        self.assertEqual(len(files), 2)
        result = subprocess.run(["desktop-file-validate", *files], capture_output=True, text=True)
        self.assertEqual((result.returncode, result.stdout), (0, ""))


class ControlTest(unittest.TestCase):
    def test_status_and_stop_through_the_api(self):
        bridge = BridgeFixture()
        try:
            ctl = Control(port=bridge.port, config_path="/nonexistent/config.json")
            # Another port than config.json's: never the systemd service, even if it runs.
            self.assertFalse(ctl.systemd_installed())
            self.assertFalse(ctl.managed_by_systemd())
            status = ctl.status()
            self.assertEqual((status["running"], status["printers"]), (True, 1))
            self.assertEqual(ctl.stop(), "stopped")
            self.assertFalse(ctl.running())
            self.assertTrue(bridge.server.stopped_from_viewer)
        finally:
            bridge.server.server_close()
            bridge.printer.stop()
            bridge.tmp.cleanup()

    def test_start_and_stop_without_systemd(self):
        port = free_port()
        with tempfile.TemporaryDirectory() as state:
            env = {"XDG_STATE_HOME": state, "XDG_CONFIG_HOME": state}
            with mock.patch.dict(os.environ, env):
                ctl = Control(port=port)
                self.assertEqual(ctl.status(), {"running": False, "url": f"http://127.0.0.1:{port}/ui"})
                self.assertEqual(ctl.start(), "started")
                try:
                    self.assertEqual(ctl.start(), "already running")
                    self.assertTrue(ctl.status()["running"])
                finally:
                    self.assertEqual(ctl.stop(), "stopped")
                self.assertEqual(ctl.stop(), "not running")

    def test_cli(self):
        port = free_port()
        with mock.patch("builtins.print") as printed:
            self.assertEqual(control.main(["status", "--port", str(port)]), 0)
        self.assertIn('"running": false', printed.call_args[0][0])


if __name__ == "__main__":
    unittest.main()
