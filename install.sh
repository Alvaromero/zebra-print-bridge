#!/usr/bin/env bash
# Install Zebra Print Bridge on Ubuntu. Run it as your normal user (it asks for sudo when needed).
set -euo pipefail

SRC="$(cd "$(dirname "$0")" && pwd)"
PREFIX=/opt/zebra-print-bridge
CONFIG_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/zebra-print-bridge"

if [[ $EUID -eq 0 ]]; then
  echo "Run install.sh as your user, not as root: the service runs in your session." >&2
  exit 1
fi

if dpkg-query -W -f='${Status}' zebra-print-bridge 2>/dev/null | grep -q "install ok installed"; then
  echo "The zebra-print-bridge .deb package is installed. Update it with the new .deb, or remove it" >&2
  echo "first (sudo apt remove zebra-print-bridge) to install from source." >&2
  exit 1
fi

python3 -c 'import sys; sys.exit(sys.version_info < (3, 10))' || {
  echo "Python 3.10 or newer is required." >&2
  exit 1
}

echo "==> Copying the program to $PREFIX"
sudo rm -rf "$PREFIX"
sudo install -d "$PREFIX/zebra_print_bridge/static"
sudo install -m 644 "$SRC"/zebra_print_bridge/*.py "$PREFIX/zebra_print_bridge/"
sudo install -m 644 "$SRC"/zebra_print_bridge/static/* "$PREFIX/zebra_print_bridge/static/"
sudo install -m 644 "$SRC/README.md" "$SRC/ROADMAP.md" "$SRC/config.example.json" "$PREFIX/"
for cmd in zebra-print-bridge:zebra_print_bridge zebra-print-bridge-ctl:zebra_print_bridge.control \
           zebra-print-bridge-tray:zebra_print_bridge.tray; do
  sudo tee "/usr/local/bin/${cmd%%:*}" >/dev/null <<EOF
#!/bin/sh
PYTHONPATH=$PREFIX exec /usr/bin/python3 -m ${cmd#*:} "\$@"
EOF
  sudo chmod 755 "/usr/local/bin/${cmd%%:*}"
done

echo "==> Application menu entry, top-bar icon and application icon"
sudo install -m 644 "$SRC/packaging/zebra-print-bridge.desktop" /usr/share/applications/
sudo install -m 644 "$SRC/packaging/zebra-print-bridge-tray.desktop" /etc/xdg/autostart/
sudo install -D -m 644 "$SRC/zebra_print_bridge/static/icon.svg" /usr/share/icons/hicolor/scalable/apps/zebra-print-bridge.svg
sudo gtk-update-icon-cache -q /usr/share/icons/hicolor 2>/dev/null || true
sudo update-desktop-database -q /usr/share/applications 2>/dev/null || true
if ! python3 -c 'import gi; gi.require_version("AyatanaAppIndicator3", "0.1")' 2>/dev/null; then
  echo "    Installing gir1.2-ayatanaappindicator3-0.1 for the top-bar icon"
  sudo apt-get install -y gir1.2-ayatanaappindicator3-0.1 >/dev/null \
    || echo "WARNING: could not install it; the top-bar icon will not show (everything else works)." >&2
fi

echo "==> udev rule for Zebra USB printers"
sudo install -m 644 "$SRC/packaging/70-zebra-print-bridge.rules" /etc/udev/rules.d/
sudo udevadm control --reload-rules
sudo udevadm trigger --subsystem-match=usbmisc --action=add || true

echo "==> usblp driver"
if grep -rqsE '^\s*blacklist\s+usblp' /etc/modprobe.d /lib/modprobe.d /usr/lib/modprobe.d; then
  echo "WARNING: usblp is blacklisted in modprobe; USB printers will not show up." >&2
  grep -rsE '^\s*blacklist\s+usblp' /etc/modprobe.d /lib/modprobe.d /usr/lib/modprobe.d >&2 || true
fi
echo usblp | sudo tee /etc/modules-load.d/zebra-print-bridge.conf >/dev/null
sudo modprobe usblp || echo "WARNING: could not load usblp" >&2

echo "==> systemd user service"
# Starting at login is each user's choice (systemctl --user enable). A fresh install turns it on
# for you; an upgrade keeps what you chose. Versions before 0.3.0 enabled it for every user
# (--global): that link is removed, and you keep starting at login as before.
FRESH=yes; [[ -f /usr/lib/systemd/user/zebra-print-bridge.service ]] && FRESH=no
WAS_GLOBAL=no; [[ -e /etc/systemd/user/default.target.wants/zebra-print-bridge.service ]] && WAS_GLOBAL=yes
sudo install -m 644 "$SRC/packaging/zebra-print-bridge.service" /usr/lib/systemd/user/
if [[ $WAS_GLOBAL == yes ]]; then
  sudo systemctl --global disable zebra-print-bridge.service
  echo "    Removed the old 'every user' activation. Other users turn it on with:"
  echo "    systemctl --user enable zebra-print-bridge"
fi

if [[ ! -f "$CONFIG_DIR/config.json" ]]; then
  echo "==> Initial configuration in $CONFIG_DIR/config.json"
  install -d "$CONFIG_DIR"
  cat >"$CONFIG_DIR/config.json" <<'EOF'
{
  "network_printers": []
}
EOF
fi

systemctl --user daemon-reload
if [[ $FRESH == yes || $WAS_GLOBAL == yes ]]; then
  systemctl --user enable zebra-print-bridge.service
fi
AUTOSTART="$(systemctl --user is-enabled zebra-print-bridge.service 2>/dev/null || true)"
systemctl --user restart zebra-print-bridge.service

sleep 1
if systemctl --user is-active --quiet zebra-print-bridge.service; then
  echo
  echo "Installed. Printers found:"
  zebra-print-bridge --list || true
  echo
  echo "Viewer:        http://127.0.0.1:9100/ui"
  echo "Logs:          journalctl --user -u zebra-print-bridge -f"
  echo "Configuration: $CONFIG_DIR/config.json (example in $PREFIX/config.example.json)"
  echo "If the USB printer was plugged in before installing, unplug it and plug it back in."
  echo "Top-bar icon:  starts at your next login (or now: zebra-print-bridge-tray &)"
  if [[ $AUTOSTART == enabled ]]; then
    echo "At login:      the service starts (turn it off: zebra-print-bridge-ctl autostart off)"
  else
    echo "At login:      the service does not start (turn it on: zebra-print-bridge-ctl autostart on)"
  fi
else
  echo "The service did not start. Check: journalctl --user -u zebra-print-bridge -n 50" >&2
  exit 1
fi
