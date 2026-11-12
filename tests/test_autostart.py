"""Start at login, per user: ctl/viewer switch and the .deb maintainer scripts."""

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from tests.helpers import BridgeFixture
from zebra_print_bridge import control

ROOT = Path(__file__).resolve().parent.parent


class FakeSystemctl:
    """`systemctl --user` on top of a fake user config dir and a fake global link."""

    def __init__(self, home: Path, global_link: Path, installed=True):
        self.home, self.global_link, self.installed = home, global_link, installed
        self.calls = []

    @property
    def link(self):
        return self.home / "systemd/user/default.target.wants" / control.UNIT

    def __call__(self, *args):
        self.calls.append(args)
        ok = SimpleNamespace(returncode=0, stdout="", stderr="")
        if not self.installed:
            return SimpleNamespace(returncode=1, stdout="", stderr="Unit not found")
        if args[0] == "is-enabled":
            return SimpleNamespace(
                returncode=0,
                stdout="enabled\n" if self.link.exists() or self.global_link.exists() else "disabled\n",
                stderr="",
            )
        if args[0] == "enable":
            self.link.parent.mkdir(parents=True, exist_ok=True)
            self.link.write_text("")
        elif args[0] == "disable":
            self.link.unlink(missing_ok=True)
        return ok


class AutostartSwitchTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.home = Path(tmp.name) / "config"
        self.global_link = Path(tmp.name) / "global" / control.UNIT
        self.systemctl = FakeSystemctl(self.home, self.global_link)
        for patcher in (
            mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": str(self.home)}),
            mock.patch.object(control, "_systemctl", self.systemctl),
            mock.patch.object(control, "GLOBAL_WANTS", self.global_link),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_on_and_off(self):
        self.assertEqual(
            control.autostart_state(), {"available": True, "enabled": False, "global": False, "masked": False}
        )
        self.assertTrue(control.set_autostart(True)["enabled"])
        self.assertTrue(self.systemctl.link.exists())
        self.assertFalse(control.set_autostart(False)["enabled"])
        self.assertIn(("disable", control.UNIT), self.systemctl.calls)

    def test_old_global_activation_cannot_be_turned_off_by_the_user(self):
        self.global_link.parent.mkdir(parents=True)
        self.global_link.write_text("")
        self.assertEqual(control.autostart_state()["global"], True)
        with self.assertRaisesRegex(RuntimeError, "sudo systemctl --global disable"):
            control.set_autostart(False)

    def test_service_not_installed(self):
        self.systemctl.installed = False
        self.assertFalse(control.autostart_state()["available"])
        with self.assertRaisesRegex(RuntimeError, "not installed"):
            control.set_autostart(True)

    def test_cli(self):
        with mock.patch("builtins.print"):
            self.assertEqual(control.main(["autostart", "on"]), 0)
            self.assertTrue(control.autostart_state()["enabled"])
            self.assertEqual(control.main(["autostart", "off"]), 0)
            self.assertEqual(control.main(["autostart"]), 0)
        self.assertFalse(control.autostart_state()["enabled"])

    def test_viewer_switch(self):
        bridge = BridgeFixture()
        try:
            _, settings = bridge.ui("ui/api/settings")
            self.assertEqual(settings["service"]["autostart"]["enabled"], False)
            status, settings = bridge.ui("ui/api/settings/autostart", {"enabled": True})
            self.assertEqual((status, settings["service"]["autostart"]["enabled"]), (200, True))
            self.global_link.parent.mkdir(parents=True)
            self.global_link.write_text("")
            status, body = bridge.ui("ui/api/settings/autostart", {"enabled": False})
            self.assertEqual(status, 409)
            self.assertIn("--global disable", body)
        finally:
            bridge.close()


@unittest.skipUnless(shutil.which("dpkg-deb"), "dpkg-deb is not installed")
class DebMaintainerScriptsTest(unittest.TestCase):
    """Runs the .deb's postinst with fake systemctl/loginctl and records what it asks for."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        dist = Path(cls.tmp.name) / "build"
        shutil.copytree(ROOT, dist, ignore=shutil.ignore_patterns(".git", "dist", "__pycache__"))
        deb = subprocess.run(["bash", str(dist / "tools/build_deb.sh")], capture_output=True, text=True, check=True)
        control_dir = Path(cls.tmp.name) / "control"
        subprocess.run(["dpkg-deb", "-e", deb.stdout.strip(), str(control_dir)], check=True)
        cls.postinst = control_dir / "postinst"
        cls.preinst = control_dir / "preinst"

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def run_postinst(self, *args, global_link=False, active=False, from_source=False, output=None):
        with tempfile.TemporaryDirectory() as tmp:
            bin_dir, log = Path(tmp) / "bin", Path(tmp) / "calls.log"
            bin_dir.mkdir()
            stubs = {
                "loginctl": 'echo "1000 alice no active"; echo "0 root no active"; echo "123 gdm no active"',
                "systemctl": f'echo "$*" >> {log}; case "$*" in *is-active*) exit {0 if active else 3};; esac',
            }
            for name in ("udevadm", "modprobe", "gtk-update-icon-cache", "update-desktop-database"):
                stubs[name] = "true"
            for name, body in stubs.items():
                (bin_dir / name).write_text(f"#!/bin/sh\n{body}\n")
                (bin_dir / name).chmod(0o755)
            link = Path(tmp) / "global-link"
            if global_link:
                link.write_text("")
            root = Path(tmp) / "root"
            env = {
                "PATH": f"{bin_dir}:/usr/bin:/bin",
                "ZPB_GLOBAL_WANTS": str(link),
                "ZPB_ROOT": str(root),
                "ZPB_FROM_SOURCE_MARK": str(Path(tmp) / "from-source"),
            }
            if from_source:  # what install.sh leaves behind
                for f in (
                    "usr/lib/systemd/user/zebra-print-bridge.service",
                    "usr/local/bin/zebra-print-bridge",
                    "etc/udev/rules.d/70-zebra-print-bridge.rules",
                ):
                    (root / f).parent.mkdir(parents=True, exist_ok=True)
                    (root / f).write_text("")
                subprocess.run(["sh", str(self.preinst), "install"], env=env, check=True)
            result = subprocess.run(
                ["sh", str(self.postinst), *args], env=env, check=True, capture_output=True, text=True
            )
            if output is not None:
                output.append(result.stdout)
            self.assertFalse(Path(env["ZPB_FROM_SOURCE_MARK"]).exists())  # postinst cleans the mark
            return log.read_text().splitlines() if log.exists() else []

    def test_fresh_install_enables_it_for_logged_in_users(self):
        calls = self.run_postinst("configure", "")
        self.assertIn(f"--user -M alice@ enable {control.UNIT}", calls)
        self.assertIn(f"--user -M alice@ start {control.UNIT}", calls)
        self.assertFalse([c for c in calls if "root@" in c or "gdm@" in c])  # no system accounts
        self.assertFalse([c for c in calls if "--global" in c])

    def test_upgrade_keeps_each_users_choice(self):
        calls = self.run_postinst("configure", "0.3.0", active=True)
        self.assertFalse([c for c in calls if " enable " in c or "--global" in c])
        self.assertIn(f"--user -M alice@ restart {control.UNIT}", calls)

    def test_first_deb_over_install_sh_keeps_the_users_choice(self):
        output = []
        calls = self.run_postinst("configure", "", from_source=True, output=output)
        self.assertFalse([c for c in calls if " enable " in c or " start " in c])
        self.assertIn(
            "sudo rm -f /usr/local/bin/zebra-print-bridge /etc/udev/rules.d/70-zebra-print-bridge.rules", output[0]
        )

    def test_no_leftovers_warning_on_a_clean_machine(self):
        output = []
        self.run_postinst("configure", "", output=output)
        self.assertNotIn("WARNING", output[0])

    def test_upgrade_from_global_activation(self):
        calls = self.run_postinst("configure", "0.2.0", global_link=True)
        self.assertIn(f"--global disable {control.UNIT}", calls)
        self.assertIn(f"--user -M alice@ enable {control.UNIT}", calls)
        self.assertFalse([c for c in calls if " start " in c])  # an upgrade does not start stopped services


if __name__ == "__main__":
    unittest.main()
