#!/usr/bin/env bash
# Uninstall Zebra Print Bridge. ~/.config/zebra-print-bridge and the virtual printers are kept.
set -euo pipefail

# This script removes what install.sh installed. With the .deb, those same files belong to the
# package: deleting them by hand leaves dpkg believing it is installed while nothing works.
if dpkg-query -W -f='${Status}' zebra-print-bridge 2>/dev/null | grep -q "install ok installed"; then
  echo "Zebra Print Bridge is installed from the .deb package: remove it with" >&2
  echo "  sudo apt remove zebra-print-bridge" >&2
  exit 1
fi

systemctl --user disable --now zebra-print-bridge.service 2>/dev/null || true
# Older versions enabled it for every user.
sudo systemctl --global disable zebra-print-bridge.service 2>/dev/null || true
sudo rm -f /usr/lib/systemd/user/zebra-print-bridge.service
systemctl --user daemon-reload

sudo rm -f /etc/udev/rules.d/70-zebra-print-bridge.rules /etc/modules-load.d/zebra-print-bridge.conf
sudo udevadm control --reload-rules
pkill -u "$USER" -f "zebra_print_bridge.tray" 2>/dev/null || true
sudo rm -f /usr/local/bin/zebra-print-bridge /usr/local/bin/zebra-print-bridge-ctl /usr/local/bin/zebra-print-bridge-tray
sudo rm -f /usr/share/applications/zebra-print-bridge.desktop /etc/xdg/autostart/zebra-print-bridge-tray.desktop \
  /usr/share/icons/hicolor/scalable/apps/zebra-print-bridge.svg
sudo gtk-update-icon-cache -q /usr/share/icons/hicolor 2>/dev/null || true
sudo rm -rf /opt/zebra-print-bridge

echo "Uninstalled. ~/.config/zebra-print-bridge and ~/.local/state/zebra-print-bridge were not removed."
