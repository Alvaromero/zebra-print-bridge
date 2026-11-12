"""Runs the viewer's JavaScript unit tests (tests/js, node:test). Skipped when node is missing."""

import shutil
import subprocess
import unittest
from pathlib import Path

JS_TESTS = Path(__file__).parent / "js"


@unittest.skipUnless(shutil.which("node"), "node is not installed")
class ViewerJavaScriptTest(unittest.TestCase):
    def test_node_unit_tests(self):
        files = sorted(str(p) for p in JS_TESTS.glob("*.test.mjs"))
        self.assertTrue(files)
        result = subprocess.run(["node", "--test", *files], capture_output=True, text=True, timeout=120)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
