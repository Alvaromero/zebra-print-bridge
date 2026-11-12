# Contributing

Thanks for your interest. Bug reports, compatibility reports for printer models and pull requests are welcome.

## Reporting a problem

Open an issue with:

- Your Ubuntu version, the bridge version (`zebra-print-bridge --version`) and the printer model.
- The output of `zebra-print-bridge-ctl diagnose`. It contains serial numbers and IP addresses: remove
  what you do not want to make public.
- What the web app did and what you expected. If a label is involved, the ZPL from the viewer
  (`http://127.0.0.1:9100/ui`), with any personal data removed.

Security problems go through [SECURITY.md](SECURITY.md), not public issues.

## Development

Python 3.10+ standard library only; no build step for the viewer (plain ES modules).

```bash
python3 -m zebra_print_bridge --port 9200   # a development instance; the installed service keeps 9100
python3 -m unittest                         # every test (Node runs the viewer's JS tests when present)
pipx run ruff check                         # lint
pipx run ruff format                        # format
```

The end-to-end test runs Zebra's own `BrowserPrint-3.1.250.min.js`, which is not redistributed here:
see [tests/e2e/vendor/README.md](tests/e2e/vendor/README.md). Without it that test is skipped.

The README's "Development and tests" and "Code layout" sections describe every module and test file.

## Pull requests

- One topic per pull request, with tests for new behavior and for fixed bugs.
- `python3 -m unittest`, `ruff check` and `ruff format --check` must pass; CI runs them on Python 3.10 to 3.13.
- Code, comments and documentation in English. Viewer texts go through `zebra_print_bridge/static/i18n.js`
  (every language block must have every key; `tests/js/i18n.test.mjs` checks it).
- No new runtime dependencies: the bridge must keep working with a stock Ubuntu Python.
- Do not add Zebra's files (SDK, libraries, fonts) or real customer labels to the repository.

By contributing you agree that your contributions are released under the [MIT License](LICENSE).
