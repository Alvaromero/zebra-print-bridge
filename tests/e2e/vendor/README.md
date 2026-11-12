# Zebra Browser Print library (not included)

The end-to-end test (`tests/test_e2e_browserprint.py`) and the test page (`tools/serve_test_page.py`)
run Zebra's own JavaScript library, `BrowserPrint-3.1.250.min.js`, against the bridge. That file is
distributed by Zebra Technologies under its own license, so it is not part of this repository.

To run those tests, download the Browser Print SDK from Zebra's developer site, then either copy the
library here as `BrowserPrint-3.1.250.min.js` (it is ignored by git) or point to it:

```bash
BROWSERPRINT_JS=/path/to/BrowserPrint-3.1.250.min.js python3 -m unittest tests.test_e2e_browserprint
```

Without it, the end-to-end test is skipped; every other test runs.
