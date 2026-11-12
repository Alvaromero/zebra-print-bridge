# Zebra Print Bridge

A replacement for **Zebra Browser Print** on Ubuntu (22.04 or newer), aimed at **development**. It is a local service that speaks the same HTTP protocol on `http://127.0.0.1:9100/`, so web apps built on it print **without frontend changes**, using the official, unmodified `BrowserPrint-3.1.250.min.js` library.

> **Not affiliated with Zebra Technologies.** This is an independent project. "Zebra", "Browser Print" and "ZPL" are trademarks of Zebra Technologies Corporation, used here only to say what the project is compatible with. It does not include any Zebra software.

- **Virtual printers** show up in the web app like real ones and answer `~HQES` (and `~HI`) with the status you pick (ready, media out, head open, paused…). No hardware needed.
- **Website approval** like Browser Print: a new website has to be allowed before it can print (desktop notification + Allow/Block in the viewer).
- **Viewer** at `http://127.0.0.1:9100/ui`, in English, Spanish, Brazilian Portuguese, French and German, with a **Settings** page (network printers, default printer, authorized websites, stop the service): every job the web app sends (to virtual and real printers), the printer's response, **encoding warnings**, a **ZPL editor** and a **label preview** with the same controls as the [Labelary viewer](https://labelary.com/viewer.html) (density, size in in/cm/mm, rotation, quality, PNG/PDF export).
- Real printers too: **USB** (`usblp` driver, `/dev/usb/lpX`), **network** (TCP 9100) and, optionally, raw **CUPS** queues (write only).
- **Desktop:** an application menu entry (opens the viewer, starting the service if needed) and a **top-bar icon** with the service state, the printers and the websites waiting for approval.
- Python 3.10+ standard library only: nothing to `pip install`. Optional: Pillow (more image formats), `pdftoppm` (PDF), AppIndicator (top-bar icon).

## Install

Download the `.deb` from the [latest release](https://github.com/Alvaromero/zebra-print-bridge/releases/latest) and install it with `sudo apt install ./zebra-print-bridge_<version>_all.deb`. Releases are built by GitHub Actions from the tagged code ([`release.yml`](.github/workflows/release.yml)), not on anyone's machine; see [Verifying a download](#verifying-a-download) and [What the package changes on your system](#what-the-package-changes-on-your-system).

Three ways, from most to least complete:

| | Command | What you get |
|---|---|---|
| **`.deb` package** (recommended for the team) | `sudo apt install ./zebra-print-bridge_<version>_all.deb` | Everything below, upgrades with the next `.deb`, removal with `sudo apt remove zebra-print-bridge` |
| **From source** | `./install.sh` (as your user) | The same, installed by a script |
| **Wheel** (`pipx`) | `pipx install zebra_print_bridge-<version>-py3-none-any.whl` | Only the commands, for development: no udev rule, no service, no menu entry |

Build the packages with `tools/build_deb.sh` and `python3 tools/build_wheel.py` (output in `dist/`; no pip, `build` or root needed). The `.deb` recommends the optional dependencies (AppIndicator, Pillow, poppler-utils, libnotify-bin), so `apt` installs them too. Do not mix the `.deb` with `install.sh`: run `./uninstall.sh` before installing the `.deb`, never after (both scripts refuse to run while the `.deb` is installed). Step by step, with troubleshooting: [docs/BUILDING.md](docs/BUILDING.md) ([español](docs/BUILDING.es.md)).

The `.deb` and `install.sh`:

1. Copy the program to `/opt/zebra-print-bridge` and create the `zebra-print-bridge`, `zebra-print-bridge-ctl` and `zebra-print-bridge-tray` commands (`/usr/bin` for the `.deb`, `/usr/local/bin` for `install.sh`).
2. Installs the udev rule `70-zebra-print-bridge.rules`, which gives the user with the active session access to Zebra USB printers (vendor `0a5f`).
3. Loads the `usblp` driver at boot.
4. Install the `systemd --user` service. **Starting at login is each user's choice**: a fresh install turns it on for the user who installs (`install.sh`) or for the users logged in at that moment (`.deb`); upgrades keep what each user chose. Versions before 0.3.0 enabled it for every user (`systemctl --global`); upgrading removes that and keeps starting at login for you.
5. Add the application menu entry, the top-bar icon (started at login from `/etc/xdg/autostart`) and the application icon.
6. `install.sh` also creates `~/.config/zebra-print-bridge/config.json` if it does not exist (without it, the defaults apply).

After installing the `.deb`, the service of the users logged in is already running. Other users open it from the application menu and turn on "Start the service at login" if they want it. Upgrading the `.deb` restarts the services that are running.

Run `./install.sh` again to upgrade. If a USB printer was already plugged in, unplug it and plug it back in after installing.

Uninstall: `sudo apt remove zebra-print-bridge` or `./uninstall.sh`. Both keep the configuration, the virtual printers and the job history.

### Verifying a download

Each release has a `SHA256SUMS` file and a signed [build provenance attestation](https://docs.github.com/actions/security-for-github-actions/using-artifact-attestations) that ties every file to the commit and workflow run that built it:

```bash
sha256sum -c SHA256SUMS --ignore-missing     # the file is the one published
gh attestation verify zebra-print-bridge_<version>_all.deb --repo Alvaromero/zebra-print-bridge   # it was built here, from the tag
```

To look inside the package before installing it:

```bash
dpkg-deb -c zebra-print-bridge_<version>_all.deb              # every file it installs
dpkg-deb -e zebra-print-bridge_<version>_all.deb control/     # its maintainer scripts, to read them
```

You can also build it yourself from the tag with `tools/build_deb.sh`.

### What the package changes on your system

Files (`dpkg -L zebra-print-bridge` lists them once installed):

- `/opt/zebra-print-bridge/`: the program (Python, readable) and its documentation.
- `/usr/bin/zebra-print-bridge`, `zebra-print-bridge-ctl`, `zebra-print-bridge-tray`: small launchers.
- `/usr/lib/systemd/user/zebra-print-bridge.service`: a **user** service (it runs as you, not as root).
- `/usr/lib/udev/rules.d/70-zebra-print-bridge.rules`: gives the user at the screen access to Zebra USB printers (vendor `0a5f`) only.
- `/usr/lib/modules-load.d/zebra-print-bridge.conf`: loads the kernel's own `usblp` printer driver at boot.
- The application menu entry, the top-bar icon's autostart entry (`/etc/xdg/autostart`), the icon and `/usr/share/doc/zebra-print-bridge/`.

What its maintainer scripts do as root: reload udev rules and load `usblp`; refresh the icon and menu caches; for the users logged in, reload their systemd user manager and, on a first install, enable and start their user service; on upgrade, restart the services that are running and warn about leftovers of a previous `install.sh`; on removal, stop and disable the services and close the top-bar icon. They do not download anything, add users, open ports or touch other packages.

What the service does: it listens only on `127.0.0.1:9100`. It connects out only to the printers you configure or search for (TCP 9100, mDNS), to Labelary when you turn the preview on, and to a URL a web app asks it to print (`sendUrl`). No telemetry. Its files live in `~/.config/zebra-print-bridge` and `~/.local/state/zebra-print-bridge`.

## Daily use

```bash
zebra-print-bridge --list                          # detected printers (* = default)
systemctl --user status zebra-print-bridge         # service status
systemctl --user restart zebra-print-bridge        # after changing config.json
journalctl --user -u zebra-print-bridge -f         # logs
```

### Desktop

- **Application menu:** "Zebra Print Bridge" opens the viewer and starts the service first if it is stopped. Right-click (or the dock) offers **Start service**, **Stop service** and **Show top-bar icon**.
- **Top-bar icon:** shows whether the service is running, each printer with its state, and how many websites are waiting for approval (the icon gets an orange dot when a website waits or a printer has an error or is offline). Its menu opens the viewer, starts or stops the service and has two switches for your user: **Start the service at login** and "Show this icon at login". The latter turns the icon's own autostart on or off; "Hide icon" closes it until the next login. It needs `gir1.2-ayatanaappindicator3-0.1` (the installers add it) and, on GNOME, the AppIndicator extension that Ubuntu ships enabled.
- **`zebra-print-bridge-ctl open|start|stop|status`** and **`zebra-print-bridge-ctl autostart on|off|status`:** the same actions from a terminal. It uses systemd when the installed service serves the configured port; otherwise (development) it starts the bridge as a background process and stops it through the viewer.
- **`zebra-print-bridge-ctl diagnose`:** a report of what the bridge sees and why a printer may not work: the `usblp` driver, the udev rule and the permissions of `/dev/usb/lpX`, Zebras on the USB bus that another driver took (CUPS, `ipp-usb`), network printers that do not connect, `config.json` problems, and each printer's answer to `~HI`, `~HQES` and SGD (nothing is printed). While the service runs it asks through it. `--json` for the raw data, `--no-query` to skip the printers. It exits with 1 when it finds an error. The report includes serial numbers and addresses.

### Stopping and starting

- **Start at login:** Settings → Service → **Start the service at login**, the same switch in the top-bar icon, or `zebra-print-bridge-ctl autostart on|off` (`systemctl --user enable|disable zebra-print-bridge` underneath). It only affects your user. When it is off, the application menu entry and the top-bar icon start the service when you need it.
- **From the viewer:** Settings → Service → **Stop service**. It frees the port and the printers and stays stopped until you start it again or log in again (systemd only restarts it after a crash). The page then shows the command to start it and reconnects by itself.
- **From a terminal:**

  ```bash
  systemctl --user stop zebra-print-bridge     # until the next login (or until you start it)
  systemctl --user start zebra-print-bridge
  zebra-print-bridge-ctl autostart off         # do not start it at login (on to undo)
  ```

  Installed with a version before 0.3.0? It was enabled for every user and the switch cannot undo that: install the new version (it migrates) or run `sudo systemctl --global disable zebra-print-bridge.service`.

The first time the web app calls the service, **Chrome** may ask for permission to "access devices on your local network". **Allow** it; Chrome remembers it for that site. On managed machines it can be granted by Chrome policy.

In the web app, each user has to **pick their printer once**: Linux uids do not match the ones Browser Print stored on Windows.

## Authorized websites

Like Zebra Browser Print, a website must be approved before it can use the printers:

1. The first time a new website calls the service, its request is held as **pending**. The web app receives a readable error (`This website (...) is not authorized to use the printers yet. Approve it in Zebra Print Bridge: http://127.0.0.1:9100/ui`), and a **desktop notification** appears.
2. The viewer shows a banner with **Allow** / **Block**. After allowing it, reload the website.
3. Settings → Authorized websites lists pending, allowed and blocked websites; they can be removed or unblocked there.

Websites matching `allowed_origins` in `config.json` are pre-approved (by default the organization's domains and `localhost`). Requests without an `Origin` header (curl, local scripts) are always accepted. Set `"origin_approval": false` to turn the prompt off and accept only `allowed_origins`.

## Viewer

Open `http://127.0.0.1:9100/ui` (`/` redirects there). It has two tabs, **Jobs** and **Settings** (`#settings` in the URL). The language (English, Spanish, Brazilian Portuguese, French or German) follows the browser and can be changed at the top, as can the units (in, cm, mm). Any Portuguese browser locale gets Brazilian Portuguese; an unsupported one gets English. The top-bar icon uses the same languages, chosen from the session locale.

### Printers and virtual printers

- **Live status:** each printer card shows its state (ready, the active errors and warnings, offline with the reason, or "no status" for CUPS queues), model, firmware and density. The viewer asks with `~HI` and `~HQES` when it opens, every 60 seconds while the tab is visible, and with the ↻ button. These queries are not recorded as jobs, and they wait (up to 3 s) while the web app has a `sendThenRead` in progress, so they never take its answer.
- **Printer details:** opening "Printer details" in a card reads, with SGD `! U1 getvar` commands, the name, serial number, firmware, Link-OS version, print method, media type, print mode, print width and label length (in dots and in the viewer's units) and the odometer. They are read only when the panel is opened (or with "read again"), one variable at a time, and are not recorded as jobs. Values the printer does not know (`"?"`) are hidden; a printer that does not answer the first variable is shown as not supporting SGD, without waiting for the rest. Virtual printers answer with their own settings.
- **Test label:** prints a small label (it fits 2.25 × 1.25 in, scaled to the printer's density) with the printer name, uid, date and a barcode, plus `ÁÉÍÓÚ ñ ü ç €` to check UTF-8 on paper. It is recorded as a job, so it can be previewed.
- Real and virtual printers are listed together. There is one virtual printer by default, `Virtual ZD421-203dpi` (`virtual:zd421-203`). The form adds more (name, density, label size with presets such as 4 × 6 in or 100 × 150 mm). They are stored in `~/.local/state/zebra-print-bridge/virtual-printers.json`. After adding one, reload the web app to see it in its printer list.
- **Simulated status:** the buttons (Ready, Media out, Ribbon out, Head open, Paused, Calibrate) and the panel with every flag change what the virtual printer answers to `~HQES`, using the flag layout documented by Zebra, which is what web apps decode. A virtual printer answers instantly; a real one takes up to `read_timeout` when it does not reply.
- Virtual printers are listed **after** the real ones, so a connected real printer stays the default.

### Jobs

- The latest `max_jobs` are kept and **survive a restart**: they are saved in `~/.local/state/zebra-print-bridge/history.jsonl` (readable only by your user, because labels can carry names and addresses; at most `history_max_mb`, the oldest go first). **Clear** empties it. The `~DG` graphics and the `^CI` charset each printer received are kept too, because they stay on the printer: previews of `^XG` and the encoding warnings still work after a restart. `persist_history: false` keeps the history in memory only. The `~HQES` status queries the web app sends every 5 minutes and after each print are hidden by default.
- Each job shows the ZPL, the printer response, the time in local time and in UTC (to match backend logs), and lets you download the `.zpl`, copy it, **resend** it to another printer (e.g. from a virtual printer to a real one) or open it in the editor.
- `#job=<id>` in the URL opens a job directly; `#editor` opens the editor.

### Encoding warnings

Every job (and the editor while you type) is checked for the problems that make a Zebra print wrong characters or blanks. They do not depend on Labelary:

| Warning | When | Fix |
|---|---|---|
| Not UTF-8 | Text with non-ASCII characters (`ã`, `ñ`, `ü`, `é`…) while `^CI28` is not active | Add `^CI28` at the start of the label |
| Font coverage | Characters outside the Latin script (Chinese, Cyrillic, Arabic, Greek…) in a label without a downloaded font | Download a font that includes them (`^CW` / `^A@`) |

`^CI` persists on the printer, so the bridge remembers the last `^CI` sent to each printer, like the printer does. The web app's ZPL templates already use `^CI28`; the font warning is the one that matters for product names in non-Latin scripts.

### ZPL editor and preview

- **Editor:** "New ZPL" (or "Open in editor" from a job) opens a ZPL editor with live warnings and preview, and a button to send the text to any printer. The text is kept in the browser between visits.
- **Preview** is rendered by [Labelary](https://labelary.com/service.html), an external service, so it stays **off** until you tick "Preview with Labelary". From then on, the ZPL of each label you open is sent to `api.labelary.com`: do not turn it on with real customer data. To remove it completely: `"labelary_url": null`.
- Controls, as in the Labelary viewer: **print density** (6, 8, 12 or 24 dpmm = 152, 203, 300 or 600 dpi), **label size** in the chosen unit with presets (taken from `^PW`/`^LL` when the label declares them, otherwise from the printer), **rotation**, **quality** (grayscale/bitonal), **label N of M**, and **download** as PNG, PDF or a PDF with every label.
- Graphics sent earlier with `~DG` (for example logos a web app sends to every printer once) are prepended so `^XG` renders.
- Labelary's own **linter** warnings (`X-Warnings`) are shown next to the local ones, with byte positions relative to your ZPL.
- Labelary's free plan allows about 3 requests per second and 5,000 per day; images are cached and the editor waits until you stop typing. Labelary is a third-party service with its own [terms of use](https://labelary.com/service.html): check them before relying on it, especially for commercial use.

The viewer only answers on `127.0.0.1`/`localhost` and rejects other origins: the web app and any other site can print, but cannot read the job history or change printers.

### Settings page

- **Authorized websites:** see above.
- **Network printers:** add (IP/host, port, optional name and uid), remove and **test** them. The test connects and sends `~HQES` (nothing is printed) and shows the response time and status. Printers from `config.json` are listed but can only be changed in that file.
- **Search the network:** finds printers that announce their raw port with mDNS (`_pdl-datastream._tcp`, which Zebra Link-OS models do) and adds them with one click. Nothing is sent to the printers; results that do not look like a Zebra are marked, because another brand prints ZPL as text. mDNS does not cross VLANs or VPNs: add those printers by IP. Zebra's own UDP discovery (port 4201) is not supported.
- **Default printer:** the one the web app gets from `getDefaultDevice`; "Automatic" uses `default_printer` from `config.json` or the first printer detected.
- **Service:** version, address, uptime, the files in use and the **Stop service** button.

## Configuration

There are three layers, each on top of the previous one:

- `/etc/zebra-print-bridge/config.json` (optional): the organization's configuration, for every user of the machine. The package does not create it; an administrator does (see `docs/BUILDING.md`).
- `~/.config/zebra-print-bridge/config.json`: each user's configuration, edited by hand. It wins key by key, except `allowed_origins`, which adds to the organization's list instead of replacing it. The service never rewrites either file.
- `~/.local/state/zebra-print-bridge/settings.json`: what each user changes in the viewer (network printers, default printer, allowed/blocked websites), applied on top of `config.json`. Virtual printers live next to it in `virtual-printers.json`.

`config.json` keys (only what you change; see `config.example.json`):

| Key | Default | Purpose |
|---|---|---|
| `network_printers` | `[]` | `[{"host": "192.168.1.50", "port": 9100, "name": "Zebra Dock 1", "uid": "optional"}]`; the viewer can add more |
| `default_printer` | `null` | uid of the default printer; `null` = the first one detected. The viewer's choice wins |
| `allowed_origins` | `localhost` (any port, http and https) | Pre-approved websites (`fnmatch` patterns for the `Origin` header) |
| `origin_approval` | `true` | Ask before a new website can use the printers. `false` = only `allowed_origins` (and `[]` allows any website) |
| `desktop_notifications` | `true` | Notify (`notify-send`) when a website asks for approval |
| `settings_file` | `null` | Where the viewer's settings are stored (default `~/.local/state/zebra-print-bridge/settings.json`) |
| `overrides` | `{}` | Rename a detected printer or pin its uid: `{"usb:D4J123": {"name": "Zebra Desk 3", "uid": "desk-3"}}` |
| `cups_queues` | `[]` | Raw CUPS queues. Print only: no status |
| `usb.enabled` | `true` | Discover USB printers |
| `virtual_printers` | `true` | Offer the viewer's virtual printers |
| `virtual_printers_file` | `null` | Where they are stored (default `~/.local/state/zebra-print-bridge/virtual-printers.json`) |
| `max_jobs` | `300` | Jobs kept by the viewer |
| `persist_history` | `true` | Save the job history to disk so it survives a restart |
| `history_file` | `null` | Where (default `~/.local/state/zebra-print-bridge/history.jsonl`, mode 0600) |
| `history_max_mb` | `50` | Size limit of the history file; the oldest jobs go first |
| `labelary_url` | `https://api.labelary.com` | Preview service; `null` disables it |
| `encoding` | `utf-8` | Encoding of the data sent to the printers (pair it with `^CI28`) |
| `read_timeout` | `2.5` | Seconds `/read` waits for the printer's response |
| `read_idle` | `0.15` | Silence that marks the response as complete |
| `connection_linger` | `5.0` | Seconds the printer connection stays open after use |
| `port` / `host` | `9100` / `127.0.0.1` | Do not change for the web app: the library hardcodes `127.0.0.1:9100` |

The file is checked at startup. Invalid JSON or a value of the wrong type (`"port": "9100"`, a network printer without `host`…) stops the service with a message naming the file and the key; see it with `journalctl --user -u zebra-print-bridge` (systemd does not retry: fix the file and start it again). Unknown keys only log a warning, with a suggestion when they look like a typo (`allowed_orgins` → `allowed_origins`).

### Printer uids

| Type | uid |
|---|---|
| USB | `usb:<serial number>`; without a serial, `usb:<physical port>` (e.g. `usb:1-2`) |
| Network | the IP (`192.168.1.50`), or `ip:port` when the port is not 9100 |
| CUPS | `cups:<queue>` |
| Virtual | `virtual:<name>` |

The uid is what the web app stores (in `localStorage` and in the backend), so it must be stable. `overrides` can pin one by hand.

## Implemented protocol

| Route | Response |
|---|---|
| `GET /default?type=printer` | device as JSON, or `""` when there are no printers. Always 200 |
| `GET /available` | `{"printer": [device, ...]}` |
| `GET /config` | `{"application": {..., "platform": "linux", "supportedConversions": {"png": ["zpl"], ...}}}` |
| `POST /write` `{"device", "data"}` | `""`; sends `data` raw |
| `POST /read` `{"device"}` | what the printer answered after the last write; waits up to `read_timeout`, cuts at ETX or after `read_idle` of silence; `""` when it does not answer |
| `POST /write` multipart `json` + `blob` (`sendFile`) | `""`; sends the file as it is (bytes) |
| `POST /write` `{"device", "url", "options"?}` (`sendUrl`) | `""`; downloads the URL (http/https only, up to 20 MB, 15 s). Images and PDFs are converted like `convertAndSendFile`; anything else is sent as it is |
| `POST /convert` multipart `json` (`options`, `device`) + `blob` | JSON: `""` for `action: "print"`, the ZPL for `"return"`, the printer path (`"E:LOGO.GRF"`) for `"store"` |
| `POST /convert/scan` (`scanImage`) | 400 with a clear message |

### Image and PDF conversion

`convertAndSendFile`, `printImageAsLabel`, `getConvertedResource`, `storeConvertedResource` and `sendUrl` with an image turn the image into a 1-bit `^GF` graphic: transparency becomes white and each pixel is black when its luminance is below 50 %. Options:

- `action`: `print` (a label with the image at `^FO0,0`, one per PDF page), `return` (that ZPL, without printing) or `store` (`~DG` to `storageName`, by default on `E:`, up to 8 characters, `.GRF`; reuse it with `^XG`).
- `fitTo` (keeps the aspect ratio), `resize` (exact; with one side, the other keeps the ratio) or `scale`, in dots. Shrinking averages pixels, so thin lines survive.
- `toFormat`: `zpl`, or `none` to send the file without converting it. `cpcl` and `kpl` are not supported.

PNG and BMP are converted with the standard library alone. JPG, GIF, TIFF and WebP need Pillow (`sudo apt install python3-pil`), which also makes conversion several times faster. PDF needs `pdftoppm` (`poppler-utils`) and is rendered at the printer's density; Zebra's `featureKey` license is not needed. `/config` only lists the formats available on the machine. Converted labels are recorded as jobs, so the viewer can preview them.

The response bodies of `/convert` (JSON strings) follow what the library expects (it parses them as JSON); they have not been compared with Zebra's application.

Errors are readable plain text (e.g. `Printer not found: <uid>` or `Error writing to printer ...`) with the CORS headers, so the web app can show them.

## Development and tests

```bash
python3 -m unittest -v
pipx run ruff check        # lint (or: uvx ruff check; settings in pyproject.toml)
pipx run ruff format       # format the Python code (--check to only verify)
```

- `tests/test_server.py`: Browser Print endpoints, CORS/PNA, timeouts and errors against a simulated printer.
- `tests/test_status.py`: live status of every kind of printer, coexistence with the web app's `sendThenRead`, test label.
- `tests/test_settings.py`: website approval, network printers and default printer from the viewer, connection test, stopping the service.
- `tests/test_viewer.py`: virtual printers, job log, resending, encoding warnings, preview options and Labelary linter (against a fake Labelary) and viewer security.
- `tests/test_convert.py`: PNG/BMP decoders (compared with Pillow), sizing, `^GF`/`~DG`, PDF pages, and `sendFile`/`sendUrl`/`/convert` over HTTP.
- `tests/test_history.py`: history on disk (restart, compaction, size limit, file permissions).
- `tests/test_autostart.py`: the start-at-login switch (ctl, viewer, old global activation) and the `.deb`'s `postinst` run with fake `systemctl`/`loginctl` (fresh install, upgrade, migration from `--global`).
- `tests/test_desktop.py`: `zebra-print-bridge-ctl` (start/stop without systemd, never touching the installed service), the top-bar icon's states and autostart, and the `.desktop` files.
- `tests/test_discovery.py`: mDNS packets and network search against a fake responder.
- `tests/test_zpl.py`: `~HQES` flags, label and graphics parsing, encoding lint.
- `tests/test_diagnose.py`: the diagnosis with a fake sysfs (driver, permissions, CUPS), a simulated network printer (with and without the service running) and an invalid configuration.
- `tests/test_js.py`: runs the viewer's JavaScript unit tests (`tests/js`, `node --test`; skipped without `node`): HTML escaping, language detection and the completeness of every translation.
- `tests/test_config.py`: `config.json` checks (wrong types, typos, invalid JSON) and that `config.example.json` stays valid.
- `tests/test_devices.py`: USB discovery with a fake sysfs and the USB transport over a pty.
- `tests/test_e2e_browserprint.py`: runs the **official** `BrowserPrint-3.1.250.min.js` in Node (skipped when `node` or the library is missing; Zebra's library is not redistributed here, see [tests/e2e/vendor/README.md](tests/e2e/vendor/README.md)): devices, `send`, `sendThenRead`, `sendFile`, `convert`, `convertAndSendFile` and `sendUrl`.

To work on the code without stopping the installed service, start another instance on another port (its viewer is at `http://127.0.0.1:9200/ui`; the web app only uses 9100):

```bash
python3 -m zebra_print_bridge --port 9200
python3 tools/serve_test_page.py        # https://localhost:8443/browser.html, the official library against port 9100
```

`zebra_print_bridge.mock_printer` simulates a **network** printer (TCP), useful to test that transport: `python3 -m zebra_print_bridge.mock_printer --host 127.0.0.2 --port 9100 [--error media_out]` and `{"network_printers":[{"host":"127.0.0.2"}]}` in the configuration. It uses `127.0.0.2` because the bridge takes `127.0.0.1:9100`. With `?auto`, the test page prints and queries the status on its own.

### Code layout

| Module | What it does |
|---|---|
| `server.py` | HTTP server and the Browser Print endpoints (`/default`, `/available`, `/write`, `/read`, `/convert`), CORS, website approval |
| `ui.py` | The viewer's API (`/ui/api/*`), one function per endpoint registered with `@get`/`@post`, and its static files |
| `web.py` | `HttpError` and the request-field parsers shared by both |
| `labelary.py` | Preview client for Labelary, with its cache |
| `devices.py` | Printer discovery, the `Registry` and one serialized `Channel` per printer |
| `transports.py`, `virtual.py` | USB, network and CUPS transports; virtual printers |
| `jobs.py` | Job history (memory + JSON lines) and `PrinterMemory`: the `~DG` graphics and `^CI` each printer keeps |
| `printer_status.py`, `zpl.py` | Live status and SGD details; ZPL parsing, `~HQES`, encoding lint, test label |
| `convert.py`, `discovery.py` | Image/PDF to ZPL; mDNS search |
| `config.py`, `settings.py` | `config.json` defaults and checks; the viewer's settings and website approval |
| `control.py`, `tray.py` | `zebra-print-bridge-ctl` and start at login; the top-bar icon |
| `diagnose.py` | `zebra-print-bridge-ctl diagnose` |
| `static/` | The viewer, plain ES modules with no build step: `app.js` (printers, jobs, editor, preview), `settings.js`, `i18n.js`, `common.js` (fetch, toast, storage), `html.js` and `style.css` |

The viewer builds its markup with the `` html`...` `` template from `html.js`: every interpolated value is escaped unless it is itself an `html` fragment, so printer names, websites or ZPL cannot inject markup. Use `tHtml()` only for translations that contain tags.

### Adding a viewer language

Translations live in `zebra_print_bridge/static/i18n.js`: copy the `en` block, translate the values and add the language code to `LANGUAGES`. Use a base code (`fr`) unless the regional variant matters (`pt-BR`): browser locales such as `fr-CA` fall back to the base code. Keep the `{name}` placeholders and HTML tags as they are. Missing keys fall back to English. The top-bar icon has its own short `TEXT` dictionary in `zebra_print_bridge/tray.py`.

## Known issues

- **USB reads:** `usblp` reads the response of most Zebras, but if a model does not return its status, printing still works and the web app shows an empty status. `zebra-print-bridge-ctl diagnose` tells which case a printer is in. Plan B (not implemented): talk to the bulk endpoints through `libusb`.
- **One user at a time:** `usblp` allows a single open per printer and many network Zebras accept a single connection on port 9100. That is why the bridge closes the connection after `connection_linger` idle seconds.
- **`sendThenRead` from two tabs at once:** each operation is serialized per printer, but another tab's write can slip in between the first tab's write and read.
- **Web app status decoders:** `~HQES` packs several flags into each hex digit (head open + media out = `5`). A web app that compares whole digits with single values marks such a printer as not ready without a reason. The viewer's presets simulate one error at a time; use the flags panel to reproduce combined errors and check how your web app handles them.

See [ROADMAP.md](ROADMAP.md) for what is planned next and [CHANGELOG.md](CHANGELOG.md) for what changed in each version.

## Contributing, security and license

- Contributions are welcome: see [CONTRIBUTING.md](CONTRIBUTING.md).
- To report a vulnerability, follow [SECURITY.md](SECURITY.md) instead of opening a public issue.
- Released under the [MIT License](LICENSE).
