import contextlib
import io
import json
import os
import socket
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests.helpers import BridgeFixture
from zebra_print_bridge import control, diagnose
from zebra_print_bridge.mock_printer import MockPrinter


def free_port() -> int:
    with socket.create_server(("127.0.0.1", 0)) as sock:
        return sock.getsockname()[1]


def make_device(sysfs: Path, dev: Path, port: str, vendor="0a5f", driver="usblp", lp="lp0", product="ZD421"):
    """A USB device under /sys/bus/usb/devices with one printer-class interface."""
    devices = sysfs / "bus" / "usb" / "devices"
    device = devices / port
    interface = devices / f"{port}:1.0"
    device.mkdir(parents=True)
    interface.mkdir()
    (device / "idVendor").write_text(vendor + "\n")
    (device / "idProduct").write_text("0185\n")
    (device / "product").write_text(product + "\n")
    (interface / "bInterfaceClass").write_text("07\n")
    if driver:
        target = sysfs / "bus" / "usb" / "drivers" / driver
        target.mkdir(parents=True, exist_ok=True)
        (interface / "driver").symlink_to(target)
    if driver == "usblp":
        (interface / "usbmisc" / lp).mkdir(parents=True)
        (dev / "usb").mkdir(parents=True, exist_ok=True)
        (dev / "usb" / lp).write_bytes(b"")


class UsbTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.sysfs, self.dev = Path(tmp.name, "sys"), Path(tmp.name, "dev")
        (self.sysfs / "module" / "usblp").mkdir(parents=True)

    def findings(self, cups=()):
        usb = {
            "usblp_loaded": diagnose.usblp_loaded(str(self.sysfs)),
            "udev_rule": ["/etc/udev/rules.d/x"],
            "devices": diagnose.usb_devices(str(self.sysfs), str(self.dev)),
            "cups_queues": list(cups),
        }
        return usb, diagnose._usb_findings(usb, {"usb": {"enabled": True}})

    def test_devices_drivers_and_nodes(self):
        make_device(self.sysfs, self.dev, "1-2")
        make_device(self.sysfs, self.dev, "1-3", driver="usbfs", product="ZT411")
        make_device(self.sysfs, self.dev, "1-4", vendor="046d")  # not a Zebra
        usb, findings = self.findings()
        self.assertEqual([d["port"] for d in usb["devices"]], ["1-2", "1-3"])
        node = usb["devices"][0]["interfaces"][0]["nodes"][0]
        self.assertEqual(
            (node["path"], node["readable"], node["writable"]), (str(self.dev / "usb" / "lp0"), True, True)
        )
        levels = {f[1].split(" ")[0]: f[0] for f in findings}
        self.assertEqual(levels, {"ZD421:": diagnose.OK, "ZT411": diagnose.FAIL})
        self.assertIn("libusb", next(f[1] for f in findings if f[1].startswith("ZT411")))

    def test_no_access_to_the_device_node(self):
        make_device(self.sysfs, self.dev, "1-2")
        (self.dev / "usb" / "lp0").chmod(0)
        if os.access(self.dev / "usb" / "lp0", os.W_OK):
            self.skipTest("running as root")
        _, findings = self.findings()
        self.assertEqual(findings[0][0], diagnose.FAIL)
        self.assertIn("no read/write access", findings[0][1])

    def test_unbound_without_usblp_and_cups_queue(self):
        make_device(self.sysfs, self.dev, "1-2", driver=None)
        (self.sysfs / "module" / "usblp").rmdir()
        _, findings = self.findings(cups=["device for zebra: usb://Zebra/ZD421"])
        self.assertIn("sudo modprobe usblp", findings[0][1])
        self.assertEqual(findings[1][0], diagnose.WARN)


class PrinterFindingsTest(unittest.TestCase):
    def finding(self, connection, status, details=None):
        printer = {
            "uid": "u",
            "name": "P",
            "connection": connection,
            "virtual": False,
            "status": status,
            "details": details or {},
        }
        return diagnose._printer_findings([printer])

    def test_usb_without_status_needs_libusb(self):
        [(level, message)] = self.finding("usb", {"state": "no_response"})
        self.assertEqual(level, diagnose.FAIL)
        self.assertIn("libusb", message)

    def test_status_and_sgd(self):
        findings = self.finding("usb", {"state": "error", "errors": ["Media Out"]}, {"supported": True})
        self.assertEqual([f[0] for f in findings], [diagnose.OK, diagnose.OK])
        self.assertIn("status reads over USB work (error: Media Out)", findings[0][1])


class RunTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        self.printer = MockPrinter().start()
        self.addCleanup(self.printer.stop)
        self.printer.errors = ["media_out"]
        host, port = self.printer.address
        self.config = {
            "usb": {"enabled": False},
            "virtual_printers": False,
            "read_timeout": 1.0,
            "network_printers": [{"host": host, "port": port, "name": "Mock", "uid": "mock-1"}],
            "settings_file": str(self.tmp / "settings.json"),
            "history_file": str(self.tmp / "history.jsonl"),
            "virtual_printers_file": str(self.tmp / "virtual.json"),
        }
        self.patch = mock.patch.object(control, "_systemctl", lambda *a: None)  # never the real service
        self.patch.start()
        self.addCleanup(self.patch.stop)

    def run_diagnose(self, port, config=None):
        path = self.tmp / "config.json"
        path.write_text(json.dumps(config or self.config))
        return diagnose.run(str(path), port, sysfs=str(self.tmp / "sys"), dev=str(self.tmp / "dev"))

    def check_mock_printer(self, report):
        [printer] = report["printers"]
        self.assertEqual(printer["status"]["state"], "error")
        self.assertEqual(printer["details"]["values"]["serial"], "D2J000000001")
        self.assertTrue(report["network"][0]["ok"])
        messages = [f["message"] for f in report["findings"]]
        self.assertTrue(any("status reads over network work (error: Media Out)" in m for m in messages), messages)
        self.assertIn("Mock", diagnose.format_text(report))

    def test_service_not_running_queries_directly(self):
        report = self.run_diagnose(free_port())
        self.assertFalse(report["service"]["running"])
        self.check_mock_printer(report)

    def test_through_the_running_service(self):
        bridge = BridgeFixture()
        self.addCleanup(bridge.close)
        self.config["network_printers"][0]["port"] = bridge.printer.address[1]
        bridge.printer.errors = ["media_out"]
        report = self.run_diagnose(bridge.port)
        self.assertTrue(report["service"]["running"])
        self.check_mock_printer(report)

    def test_invalid_config_is_reported_not_fatal(self):
        report = self.run_diagnose(free_port(), {"port": "x"})
        self.assertIn("error", report["config"])
        self.assertEqual(report["findings"][0]["level"], diagnose.FAIL)

    def test_unreachable_network_printer(self):
        self.config["network_printers"] = [{"host": "127.0.0.1", "port": free_port()}]
        report = self.run_diagnose(free_port())
        self.assertFalse(report["network"][0]["ok"])
        self.assertTrue(any("does not accept connections" in f["message"] for f in report["findings"]))

    def test_ctl_json_output(self):
        path = self.tmp / "config.json"
        path.write_text(json.dumps(self.config))
        out = io.StringIO()
        with contextlib.redirect_stdout(out), mock.patch.object(diagnose, "usb_devices", lambda *a: []):
            code = control.main(["diagnose", "--json", "--no-query", "--config", str(path), "--port", str(free_port())])
        self.assertEqual(code, 0)
        self.assertNotIn("printers", json.loads(out.getvalue()))


if __name__ == "__main__":
    unittest.main()
