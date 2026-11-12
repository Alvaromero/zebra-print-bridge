# Roadmap

What is planned next. Done items move to [CHANGELOG.md](CHANGELOG.md).

## Open questions

- [ ] **Project name.** "Zebra" is a Zebra Technologies trademark. The README says the project is not affiliated with Zebra and uses the name only to state compatibility; a neutral name (e.g. `browserprint-linux`, `zpl-bridge`) would remove any doubt.

## Next

- [ ] **`libusb` backend.** Plan B for models whose status cannot be read through `usblp`: talk to the bulk endpoints directly (detaching `usblp` for that device). `zebra-print-bridge-ctl diagnose` shows whether a printer needs it.
- [ ] **Offline preview.** A local ZPL renderer, even a basic one, so previews do not send data to a third party.

## Later

- [ ] More SGD details once checked on real printers: head temperature, ribbon and media sensors, network settings (`ip.*`, `wlan.*`). Their variable names vary by model.
- [ ] Zebra's UDP discovery (port 4201) for printers with mDNS off. Its packet layout is not public: needs a capture from a real printer.
- [ ] Conversion extras: dithering for photos, `cpcl`/`kpl` output, `scanImage` (`/convert/scan`), and checking the `/convert` response bodies against Zebra's application.
- [ ] Bluetooth printers (mobile ZQ series over RFCOMM) and other printer languages (CPCL, EPL).
- [ ] Single-file `.pyz` (needs `importlib.resources` for the viewer's static files) an apt repository for automatic upgrades, and a release workflow that attaches the `.deb` and the wheel to each GitHub release.
- [ ] More viewer languages, contributed by the community (see "Adding a viewer language" in the README). Now: English, Spanish, Brazilian Portuguese, French and German.
- [ ] Compatibility table of printer models tested by users.
