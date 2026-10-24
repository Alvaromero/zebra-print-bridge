# Zebra Print Bridge

A replacement for **Zebra Browser Print** on Ubuntu (22.04 or newer), aimed at **development**. It is a local service that speaks the same HTTP protocol on `http://127.0.0.1:9100/`, so the web app (my.recommercebrands.com) prints **without frontend changes**, using the official, unmodified `BrowserPrint-3.1.250.min.js` library.

- **Virtual printers** show up in the web app like real ones and answer `~HQES` with the status you pick (ready, media out, head open, paused…). No hardware needed.
- **Website approval** like Browser Print: a new website has to be allowed before it can print (desktop notification + Allow/Block in the viewer).
- **Viewer** at `http://127.0.0.1:9100/ui`, in English and Spanish, with a **Settings** page (network printers, default printer, authorized websites, stop the service): every job the web app sends (to virtual and real printers), the printer's response, **encoding warnings**, a **ZPL editor** and a **label preview** with the same controls as the [Labelary viewer](https://labelary.com/viewer.html) (density, size in in/cm/mm, rotation, quality, PNG/PDF export).
- Real printers too: **USB** (`usblp` driver, `/dev/usb/lpX`), **network** (TCP 9100) and, optionally, raw **CUPS** queues (write only).
- Python 3.10+ standard library only: nothing to `pip install`.

## Install

```bash
git clone <repo> zebra-print-bridge && cd zebra-print-bridge
./install.sh          # as your user; it asks for sudo for the system parts
```

The installer:

1. Copies the program to `/opt/zebra-print-bridge` and creates the `zebra-print-bridge` command.
2. Installs the udev rule `70-zebra-print-bridge.rules`, which gives the user with the active session access to Zebra USB printers (vendor `0a5f`).
3. Loads the `usblp` driver at boot.
4. Installs the `systemd --user` service and enables it for **every user** on the machine (`systemctl --global enable`): it starts at login.
5. Creates `~/.config/zebra-print-bridge/config.json` if it does not exist.

Run `./install.sh` again to upgrade. If a USB printer was already plugged in, unplug it and plug it back in after installing.

Uninstall: `./uninstall.sh` (keeps the configuration and the virtual printers).

## Daily use

```bash
zebra-print-bridge --list                          # detected printers (* = default)
systemctl --user status zebra-print-bridge         # service status
systemctl --user restart zebra-print-bridge        # after changing config.json
journalctl --user -u zebra-print-bridge -f         # logs
```

### Stopping and starting

- **From the viewer:** Settings → Service → **Stop service**. It frees the port and the printers and stays stopped until you start it again or log in again (systemd only restarts it after a crash). The page then shows the command to start it and reconnects by itself.
- **From a terminal:**

  ```bash
  systemctl --user stop zebra-print-bridge     # until the next login
  systemctl --user start zebra-print-bridge
  systemctl --user mask zebra-print-bridge     # never start it at login (unmask to undo)
  ```

  `systemctl --user disable` is not enough, because the installer enables the service for every user (`--global`).

The first time the web app calls the service, **Chrome** may ask for permission to "access devices on your local network". **Allow** it; Chrome remembers it for that site. On managed machines it can be granted by Chrome policy.

In the web app, each user has to **pick their printer once**: Linux uids do not match the ones Browser Print stored on Windows.

## Authorized websites

Like Zebra Browser Print, a website must be approved before it can use the printers:

1. The first time a new website calls the service, its request is held as **pending**. The web app receives a readable error (`This website (...) is not authorized to use the printers yet. Approve it in Zebra Print Bridge: http://127.0.0.1:9100/ui`), and a **desktop notification** appears.
2. The viewer shows a banner with **Allow** / **Block**. After allowing it, reload the website.
3. Settings → Authorized websites lists pending, allowed and blocked websites; they can be removed or unblocked there.

Websites matching `allowed_origins` in `config.json` are pre-approved (by default the organization's domains and `localhost`). Requests without an `Origin` header (curl, local scripts) are always accepted. Set `"origin_approval": false` to turn the prompt off and accept only `allowed_origins`.

## Viewer

Open `http://127.0.0.1:9100/ui` (`/` redirects there). It has two tabs, **Jobs** and **Settings** (`#settings` in the URL). The language (English/Spanish) follows the browser and can be changed at the top, as can the units (in, cm, mm).

### Printers and virtual printers

- Real and virtual printers are listed together. There is one virtual printer by default, `Virtual ZD421-203dpi` (`virtual:zd421-203`). The form adds more (name, density, label size with presets such as 4 × 6 in or 100 × 150 mm). They are stored in `~/.local/state/zebra-print-bridge/virtual-printers.json`. After adding one, reload the web app to see it in its printer list.
- **Simulated status:** the buttons (Ready, Media out, Ribbon out, Head open, Paused, Calibrate) and the panel with every flag change what the virtual printer answers to `~HQES`, using the same flags the web app decodes (`zplHQES` in `resources/js/utils/constants.js`). A virtual printer answers instantly; a real one takes up to `read_timeout` when it does not reply.
- Virtual printers are listed **after** the real ones, so a connected real printer stays the default.

### Jobs

- Kept in memory (the latest `max_jobs`, lost on restart). The `~HQES` status queries the web app sends every 5 minutes and after each print are hidden by default.
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
- Graphics sent earlier with `~DG` (the web app's "Sync Icons") are prepended so `^XG` renders.
- Labelary's own **linter** warnings (`X-Warnings`) are shown next to the local ones, with byte positions relative to your ZPL.
- Labelary's free plan allows about 3 requests per second and 5,000 per day; images are cached and the editor waits until you stop typing.

The viewer only answers on `127.0.0.1`/`localhost` and rejects other origins: the web app and any other site can print, but cannot read the job history or change printers.

### Settings page

- **Authorized websites:** see above.
- **Network printers:** add (IP/host, port, optional name and uid), remove and **test** them. The test connects and sends `~HQES` (nothing is printed) and shows the response time and status. Printers from `config.json` are listed but can only be changed in that file.
- **Default printer:** the one the web app gets from `getDefaultDevice`; "Automatic" uses `default_printer` from `config.json` or the first printer detected.
- **Service:** version, address, uptime, the files in use and the **Stop service** button.

## Configuration

There are two layers:

- `~/.config/zebra-print-bridge/config.json`: the base configuration, edited by hand (and, later, distributable by an organization). The service never rewrites it.
- `~/.local/state/zebra-print-bridge/settings.json`: what each user changes in the viewer (network printers, default printer, allowed/blocked websites), applied on top of `config.json`. Virtual printers live next to it in `virtual-printers.json`.

`config.json` keys (only what you change; see `config.example.json`):

| Key | Default | Purpose |
|---|---|---|
| `network_printers` | `[]` | `[{"host": "192.168.1.50", "port": 9100, "name": "Zebra Dock 1", "uid": "optional"}]`; the viewer can add more |
| `default_printer` | `null` | uid of the default printer; `null` = the first one detected. The viewer's choice wins |
| `allowed_origins` | `*.recommercebrands.com` and `localhost` | Pre-approved websites (`fnmatch` patterns for the `Origin` header) |
| `origin_approval` | `true` | Ask before a new website can use the printers. `false` = only `allowed_origins` (and `[]` allows any website) |
| `desktop_notifications` | `true` | Notify (`notify-send`) when a website asks for approval |
| `settings_file` | `null` | Where the viewer's settings are stored (default `~/.local/state/zebra-print-bridge/settings.json`) |
| `overrides` | `{}` | Rename a detected printer or pin its uid: `{"usb:D4J123": {"name": "Zebra Desk 3", "uid": "desk-3"}}` |
| `cups_queues` | `[]` | Raw CUPS queues. Print only: no status |
| `usb.enabled` | `true` | Discover USB printers |
| `virtual_printers` | `true` | Offer the viewer's virtual printers |
| `virtual_printers_file` | `null` | Where they are stored (default `~/.local/state/zebra-print-bridge/virtual-printers.json`) |
| `max_jobs` | `300` | Jobs kept by the viewer |
| `labelary_url` | `https://api.labelary.com` | Preview service; `null` disables it |
| `encoding` | `utf-8` | Encoding of the data sent to the printers (pair it with `^CI28`) |
| `read_timeout` | `2.5` | Seconds `/read` waits for the printer's response |
| `read_idle` | `0.15` | Silence that marks the response as complete |
| `connection_linger` | `5.0` | Seconds the printer connection stays open after use |
| `port` / `host` | `9100` / `127.0.0.1` | Do not change for the web app: the library hardcodes `127.0.0.1:9100` |

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
| `GET /config` | `{"application": {..., "platform": "linux"}}` |
| `POST /write` `{"device", "data"}` | `""`; sends `data` raw |
| `POST /read` `{"device"}` | what the printer answered after the last write; waits up to `read_timeout`, cuts at ETX or after `read_idle` of silence; `""` when it does not answer |
| `sendUrl`, `sendFile`, `/convert` | 400 with a clear message (the web app does not use them) |

Errors are readable plain text (e.g. `Printer not found: <uid>` or `Error writing to printer ...`) with the CORS headers, so the web app can show them.

## Development and tests

```bash
python3 -m unittest -v
```

- `tests/test_server.py`: Browser Print endpoints, CORS/PNA, timeouts and errors against a simulated printer.
- `tests/test_settings.py`: website approval, network printers and default printer from the viewer, connection test, stopping the service.
- `tests/test_viewer.py`: virtual printers, job log, resending, encoding warnings, preview options and Labelary linter (against a fake Labelary) and viewer security.
- `tests/test_zpl.py`: `~HQES` flags, label and graphics parsing, encoding lint.
- `tests/test_devices.py`: USB discovery with a fake sysfs and the USB transport over a pty.
- `tests/test_e2e_browserprint.py`: runs the **official** `BrowserPrint-3.1.250.min.js` in Node (skipped when `node` is missing).

To work on the code without stopping the installed service, start another instance on another port (its viewer is at `http://127.0.0.1:9200/ui`; the web app only uses 9100):

```bash
python3 -m zebra_print_bridge --port 9200
python3 tools/serve_test_page.py        # https://localhost:8443/browser.html, the official library against port 9100
```

`zebra_print_bridge.mock_printer` simulates a **network** printer (TCP), useful to test that transport: `python3 -m zebra_print_bridge.mock_printer --host 127.0.0.2 --port 9100 [--error media_out]` and `{"network_printers":[{"host":"127.0.0.2"}]}` in the configuration. It uses `127.0.0.2` because the bridge takes `127.0.0.1:9100`. With `?auto`, the test page prints and queries the status on its own.

### Adding a viewer language

Translations live in `zebra_print_bridge/static/i18n.js`: copy the `en` block, translate the values and add the language code to `LANGUAGES`. Missing keys fall back to English.

## Known issues

- **USB reads:** `usblp` reads the response of most Zebras, but if a model does not return its status, printing still works and the web app shows an empty status. Plan B (not implemented): talk to the bulk endpoints through `libusb`.
- **One user at a time:** `usblp` allows a single open per printer and many network Zebras accept a single connection on port 9100. That is why the bridge closes the connection after `connection_linger` idle seconds.
- **`sendThenRead` from two tabs at once:** each operation is serialized per printer, but another tab's write can slip in between the first tab's write and read.
- **Web app status decoder:** `decodePrinterStatus` in `print-component.vue` compares each hex digit with a single value, so two errors in the same digit (head open + media out = `5`) mark the printer as not ready without a reason. The viewer's presets simulate one error at a time; use the flags panel to reproduce the combined case.

See [ROADMAP.md](ROADMAP.md) for what is planned next. `docs/PROMPT.es.md` is the original design brief (in Spanish).
