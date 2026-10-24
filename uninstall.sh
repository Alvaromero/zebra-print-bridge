#!/usr/bin/env bash
# Uninstall Zebra Print Bridge. ~/.config/zebra-print-bridge and the virtual printers are kept.
set -euo pipefail

systemctl --user disable --now zebra-print-bridge.service 2>/dev/null || true
sudo systemctl --global disable zebra-print-bridge.service 2>/dev/null || true
sudo rm -f /usr/lib/systemd/user/zebra-print-bridge.service
systemctl --user daemon-reload

sudo rm -f /etc/udev/rules.d/70-zebra-print-bridge.rules /etc/modules-load.d/zebra-print-bridge.conf
sudo udevadm control --reload-rules
sudo rm -f /usr/local/bin/zebra-print-bridge
sudo rm -rf /opt/zebra-print-bridge

echo "Uninstalled. ~/.config/zebra-print-bridge and ~/.local/state/zebra-print-bridge were not removed."
