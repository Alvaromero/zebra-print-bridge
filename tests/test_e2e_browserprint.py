import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from tests.helpers import BridgeFixture
from tests.test_convert import CHECKER

SCRIPT = Path(__file__).parent / "e2e" / "run_browserprint.mjs"
# Zebra's library is not part of this repository (its license does not allow redistributing it).
LIBRARY = Path(os.environ.get("BROWSERPRINT_JS") or SCRIPT.parent / "vendor" / "BrowserPrint-3.1.250.min.js")


@unittest.skipUnless(shutil.which("node"), "node is not installed")
@unittest.skipUnless(LIBRARY.is_file(), "BrowserPrint-3.1.250.min.js not found: see tests/e2e/vendor/README.md")
class BrowserPrintLibraryTest(unittest.TestCase):
    """The official, unmodified Zebra library against the bridge and the simulated printer."""

    def test_library_flow(self):
        bridge = BridgeFixture()
        try:
            bridge.printer.errors = ["head_open", "media_out"]
            with tempfile.NamedTemporaryFile(suffix=".png") as image:
                image.write(CHECKER)
                image.flush()
                proc = subprocess.run(
                    ["node", str(SCRIPT)],
                    env=dict(os.environ, BRIDGE_URL=bridge.base, PNG_PATH=image.name),
                    capture_output=True,
                    text=True,
                    timeout=30,
                )
            self.assertEqual(proc.returncode, 0, proc.stderr)
            result = json.loads(proc.stdout.strip().splitlines()[-1])
            for sent in (
                b"^XA^FO50,50^FDe2e^FS^XZ",
                b"^XA^FDsendFile^FS^XZ",
                b"~DGE:E2E.GRF,4,2,FF0000FF",
                b"^GFA,16,16,4,",
                b"^XA^FDsendUrl^FS^XZ",
                b"^GFA,64,64,8,",
            ):
                self.assertTrue(bridge.printer.wait_for(sent), sent)
        finally:
            bridge.close()

        self.assertEqual(result["default"], {"uid": "mock-1", "name": "Mock ZD421", "connection": "network"})
        self.assertEqual(result["available"], ["mock-1"])
        self.assertEqual(result["config"]["application"]["platform"], "linux")
        self.assertEqual(result["send"], "")  # the web app treats "" or "{}" as success
        self.assertIn("ERRORS:         1 00000000 00000005", result["status"])
        self.assertEqual(result["ghostError"], "Printer not found: missing")
        self.assertEqual(result["sendFile"], "")
        self.assertEqual(result["convertReturn"], "^XA^FO0,0^GFA,4,4,2,FF0000FF^FS^XZ\n")
        self.assertEqual(result["convertStore"], "E:E2E.GRF")
        self.assertEqual(result["convertAndSend"], "")
        self.assertEqual(result["sendUrl"], "")
        self.assertEqual(result["sendUrlImage"], "")
        self.assertIn("png", result["config"]["application"]["supportedConversions"])


if __name__ == "__main__":
    unittest.main()
