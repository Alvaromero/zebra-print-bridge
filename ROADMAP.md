# Roadmap

What is planned next. Done items move to `CHANGELOG.md` once releases start.

## Before a public release (do last)

- [ ] **Remove organization-specific defaults.** `allowed_origins` ships with `*.recommercebrands.com`, and the README mentions that web app's files (`print-component.vue`, `constants.js`). The public defaults should be generic; an organization distributes its own `config.json`.
- [ ] **Do not redistribute Zebra's library.** `tests/e2e/vendor/BrowserPrint-3.1.250.min.js` is under Zebra's license. The e2e test should take its path from an environment variable or download it from Zebra.
- [ ] **Name and trademark.** "Zebra" is a Zebra Technologies trademark. Add a "not affiliated with or endorsed by Zebra Technologies" notice and decide whether to rename the project (e.g. `browserprint-linux`, `zpl-bridge`) before the first public commit.
- [ ] **Labelary terms of use.** Review them before advertising the preview in a public project, especially for commercial use.
- [ ] **Open-source files:** `LICENSE` (MIT or Apache-2.0), `CONTRIBUTING.md`, `SECURITY.md`, `CHANGELOG.md`, issue templates.
- [ ] **CI (GitHub Actions):** tests on Python 3.10–3.13 and a release workflow that builds the `.deb` and the wheel.

## Next

- [ ] **Network printer discovery.** Find Zebra printers on the local network (mDNS / Zebra's UDP discovery on port 4201) and offer them in Settings instead of typing IPs.
- [ ] **`libusb` backend.** Plan B for models whose status cannot be read through `usblp`: talk to the bulk endpoints directly (detaching `usblp` for that device).
- [ ] **Offline preview.** A local ZPL renderer, even a basic one, so previews do not send data to a third party.
- [ ] **Complete the Browser Print protocol:** `sendFile` (multipart), `sendUrl` and `/convert` (image/PDF to ZPL with `^GF`). Our web app does not use them, other apps do.
- [ ] **Live printer status in the viewer:** model, firmware and media status from `~HQES` / `~HI` / SGD `getvar`, refreshed periodically.

## Later

- [ ] Bluetooth printers (mobile ZQ series over RFCOMM) and other printer languages (CPCL, EPL).
- [ ] Optionally persist the job history and the `~DG` graphics to disk so they survive a restart.
- [ ] More viewer languages, contributed by the community (see "Adding a viewer language" in the README).
- [ ] Top-bar indicator (AppIndicator) with status, open viewer, start/stop and autostart.
- [ ] Start/stop/open-viewer entries in the desktop application menu (`.desktop` files).
- [ ] Packaging: `.deb`, wheel/`pipx`, single-file `.pyz` (needs `importlib.resources` for the viewer's static files).
- [ ] Compatibility table of printer models tested by users.
