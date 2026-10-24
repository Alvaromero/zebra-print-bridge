import json
import os
import shutil
import subprocess
import unittest
from pathlib import Path

from tests.helpers import BridgeFixture

SCRIPT = Path(__file__).parent / "e2e" / "run_browserprint.mjs"


@unittest.skipUnless(shutil.which("node"), "node is not installed")
class BrowserPrintLibraryTest(unittest.TestCase):
    """The official, unmodified Zebra library against the bridge and the simulated printer."""

    def test_library_flow(self):
        bridge = BridgeFixture()
        try:
            bridge.printer.errors = ["head_open", "media_out"]
            proc = subprocess.run(
                ["node", str(SCRIPT)],
                env=dict(os.environ, BRIDGE_URL=bridge.base),
                capture_output=True, text=True, timeout=30,
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)
            result = json.loads(proc.stdout.strip().splitlines()[-1])
            self.assertTrue(bridge.printer.wait_for(b"^XA^FO50,50^FDe2e^FS^XZ"))
        finally:
            bridge.close()

        self.assertEqual(result["default"], {"uid": "mock-1", "name": "Mock ZD421", "connection": "network"})
        self.assertEqual(result["available"], ["mock-1"])
        self.assertEqual(result["config"]["application"]["platform"], "linux")
        self.assertEqual(result["send"], "")  # the web app treats "" or "{}" as success
        self.assertIn("ERRORS:         1 00000000 00000005", result["status"])
        self.assertEqual(result["ghostError"], "Printer not found: missing")


if __name__ == "__main__":
    unittest.main()
