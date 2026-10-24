import os
import pty
import tempfile
import tty
import unittest
from pathlib import Path

from zebra_print_bridge.config import load_config
from zebra_print_bridge.devices import Registry, discover_usb
from zebra_print_bridge.transports import UsbTransport


def make_usb(sysfs: Path, lp: str, port: str, vendor: str, serial: str = "", product: str = ""):
    usb_device = sysfs / "devices" / "pci0000:00" / "usb1" / port
    interface = usb_device / f"{port}:1.0"
    interface.mkdir(parents=True)
    (usb_device / "idVendor").write_text(vendor + "\n")
    if serial:
        (usb_device / "serial").write_text(serial + "\n")
    if product:
        (usb_device / "product").write_text(product + "\n")
    entry = sysfs / "class" / "usbmisc" / lp
    entry.mkdir(parents=True)
    (entry / "device").symlink_to(interface)


class DiscoverUsbTest(unittest.TestCase):
    def test_finds_only_zebra_printers(self):
        with tempfile.TemporaryDirectory() as tmp:
            sysfs = Path(tmp)
            make_usb(sysfs, "lp0", "1-1", "04b8", serial="EPSON1", product="Epson")
            make_usb(sysfs, "lp1", "1-2", "0a5f", serial="D4J123", product="ZTC ZD421-203dpi ZPL")
            make_usb(sysfs, "lp2", "1-3", "0A5F")
            printers = discover_usb(sysfs_root=tmp, dev_root="/dev")

        self.assertEqual([p.uid for p in printers], ["usb:D4J123", "usb:1-3"])
        first = printers[0]
        self.assertEqual(first.name, "ZTC ZD421-203dpi ZPL")
        self.assertEqual(first.key, ("usb", "/dev/usb/lp1"))
        self.assertEqual(first.to_device()["connection"], "usb")
        self.assertEqual(printers[1].name, "Zebra USB lp2")

    def test_no_usbmisc_class(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(discover_usb(sysfs_root=tmp), [])


class RegistryTest(unittest.TestCase):
    def config(self, **overrides):
        base = {"usb": {"enabled": False}, "virtual_printers": False}
        return load_config("/nonexistent", dict(base, **overrides))

    def test_overrides_and_default(self):
        config = self.config(
            network_printers=[{"host": "10.0.0.5"}, {"host": "10.0.0.6", "port": 6101}],
            cups_queues=["zebra_raw"],
            overrides={"10.0.0.5": {"name": "Dock 1", "uid": "dock-1"}},
            default_printer="10.0.0.6:6101",
        )
        registry = Registry(config, discover_usb_fn=lambda: [])
        printers = registry.available()
        self.assertEqual([p.uid for p in printers], ["dock-1", "10.0.0.6:6101", "cups:zebra_raw"])
        self.assertEqual(printers[0].name, "Dock 1")
        self.assertEqual(printers[2].connection, "driver")
        self.assertEqual(registry.default().uid, "10.0.0.6:6101")

    def test_channel_survives_refresh(self):
        registry = Registry(self.config(network_printers=[{"host": "10.0.0.5"}]),
                            discover_usb_fn=lambda: [])
        registry.refresh()
        channel = registry.channel("10.0.0.5")
        registry.refresh()
        self.assertIs(registry.channel("10.0.0.5"), channel)


class UsbTransportTest(unittest.TestCase):
    """Simulates /dev/usb/lpX with a pty in raw mode."""

    def setUp(self):
        self.master, slave = pty.openpty()
        tty.setraw(slave)
        self.path = os.ttyname(slave)
        self.slave = slave
        self.transport = UsbTransport(self.path)

    def tearDown(self):
        self.transport.close()
        os.close(self.slave)
        os.close(self.master)

    def test_write_and_read(self):
        self.transport.write(b"~HQES", timeout=1)
        self.assertEqual(os.read(self.master, 100), b"~HQES")
        os.write(self.master, b"\x02STATUS\x03")
        self.assertEqual(self.transport.read(1), b"\x02STATUS\x03")
        self.assertEqual(self.transport.read(0.05), b"")

    def test_read_before_open_is_empty(self):
        self.assertEqual(self.transport.read(0.05), b"")


if __name__ == "__main__":
    unittest.main()
