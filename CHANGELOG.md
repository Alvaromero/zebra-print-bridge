# Changelog

## 0.4.0 — first public release

- **Browser Print protocol:** `/default`, `/available`, `/config`, `/write` (`send`, `sendFile`, `sendUrl`),
  `/read`, `/convert` (images and PDF to ZPL: print, return, store), CORS and Chrome's private network preflight.
- **Printers:** USB (`usblp`), network (TCP 9100, mDNS discovery), raw CUPS queues and virtual printers
  that answer `~HQES`, `~HI` and SGD `getvar` with the status you choose.
- **Website approval** like Browser Print, with pre-approved `allowed_origins`.
- **Viewer** at `http://127.0.0.1:9100/ui` in English, Spanish, Brazilian Portuguese, French and German:
  live printer status and SGD details, every job with the printer's answer, encoding warnings, ZPL editor,
  optional Labelary preview, test label, settings page.
- **Configuration** in `/etc/zebra-print-bridge/config.json` (organization) and
  `~/.config/zebra-print-bridge/config.json` (user), checked at startup with readable errors.
- **Desktop:** `zebra-print-bridge-ctl open|start|stop|status|autostart|diagnose`, top-bar icon, application
  menu entry, per-user start at login.
- **`zebra-print-bridge-ctl diagnose`:** what the bridge sees and why a printer may not work.
- **Packaging:** `.deb` (systemd user service, udev rule) and wheel; `install.sh` for source installs.
