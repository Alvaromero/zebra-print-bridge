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
sudo tee /usr/local/bin/zebra-print-bridge >/dev/null <<EOF
#!/bin/sh
PYTHONPATH=$PREFIX exec /usr/bin/python3 -m zebra_print_bridge "\$@"
EOF
sudo chmod 755 /usr/local/bin/zebra-print-bridge

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

echo "==> systemd user service (for every user on this machine)"
sudo install -m 644 "$SRC/packaging/zebra-print-bridge.service" /usr/lib/systemd/user/
sudo systemctl --global enable zebra-print-bridge.service

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
else
  echo "The service did not start. Check: journalctl --user -u zebra-print-bridge -n 50" >&2
  exit 1
fi
