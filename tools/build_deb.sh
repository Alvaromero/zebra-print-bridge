#!/usr/bin/env bash
# Build dist/zebra-print-bridge_<version>_all.deb (no root or fakeroot needed).
# Same layout as install.sh: the program in /opt/zebra-print-bridge, commands in /usr/bin.
# Install it with:  sudo apt install ./dist/zebra-print-bridge_<version>_all.deb
set -euo pipefail
umask 022

SRC="$(cd "$(dirname "$0")/.." && pwd)"
VERSION="$(PYTHONPATH="$SRC" python3 -c 'import zebra_print_bridge as z; print(z.__version__)')"
NAME=zebra-print-bridge
BUILD="$(mktemp -d)"
trap 'rm -rf "$BUILD"' EXIT
PKG="$BUILD/${NAME}_${VERSION}_all"
OPT="$PKG/opt/$NAME"

install -d "$OPT/zebra_print_bridge/static" "$PKG/usr/bin" "$PKG/DEBIAN"
install -m 644 "$SRC"/zebra_print_bridge/*.py "$OPT/zebra_print_bridge/"
install -m 644 "$SRC"/zebra_print_bridge/static/* "$OPT/zebra_print_bridge/static/"
install -m 644 "$SRC/README.md" "$SRC/ROADMAP.md" "$SRC/config.example.json" "$OPT/"

for cmd in zebra-print-bridge:zebra_print_bridge zebra-print-bridge-ctl:zebra_print_bridge.control \
           zebra-print-bridge-tray:zebra_print_bridge.tray; do
  cat >"$PKG/usr/bin/${cmd%%:*}" <<EOF
#!/bin/sh
PYTHONPATH=/opt/$NAME exec /usr/bin/python3 -m ${cmd#*:} "\$@"
EOF
  chmod 755 "$PKG/usr/bin/${cmd%%:*}"
done

install -D -m 644 "$SRC/packaging/zebra-print-bridge.service" "$PKG/usr/lib/systemd/user/zebra-print-bridge.service"
install -D -m 644 "$SRC/packaging/70-zebra-print-bridge.rules" "$PKG/usr/lib/udev/rules.d/70-zebra-print-bridge.rules"
install -d "$PKG/usr/lib/modules-load.d"
echo usblp >"$PKG/usr/lib/modules-load.d/zebra-print-bridge.conf"
chmod 644 "$PKG/usr/lib/modules-load.d/zebra-print-bridge.conf"
install -D -m 644 "$SRC/packaging/zebra-print-bridge.desktop" "$PKG/usr/share/applications/zebra-print-bridge.desktop"
install -D -m 644 "$SRC/packaging/zebra-print-bridge-tray.desktop" "$PKG/etc/xdg/autostart/zebra-print-bridge-tray.desktop"
install -D -m 644 "$SRC/zebra_print_bridge/static/icon.svg" "$PKG/usr/share/icons/hicolor/scalable/apps/zebra-print-bridge.svg"
install -d "$PKG/usr/share/doc/$NAME"
gzip -9n <"$SRC/README.md" >"$PKG/usr/share/doc/$NAME/README.md.gz"
chmod 644 "$PKG/usr/share/doc/$NAME/README.md.gz"
# Debian keeps each package's license in /usr/share/doc/<package>/copyright.
install -m 644 "$SRC/LICENSE" "$PKG/usr/share/doc/$NAME/copyright"

SIZE="$(du -sk --exclude=DEBIAN "$PKG" | cut -f1)"
cat >"$PKG/DEBIAN/control" <<EOF
Package: $NAME
Version: $VERSION
Architecture: all
Maintainer: Alvaro Romero <10608006+Alvaromero@users.noreply.github.com>
Homepage: https://github.com/Alvaromero/zebra-print-bridge
Installed-Size: $SIZE
Depends: python3 (>= 3.10), systemd, udev
Recommends: gir1.2-ayatanaappindicator3-0.1, python3-gi, libnotify-bin, python3-pil, poppler-utils
Section: utils
Priority: optional
Description: Local replacement for Zebra Browser Print on Linux
 Answers the Browser Print protocol on http://127.0.0.1:9100 so web apps can
 print to Zebra printers (USB, network, CUPS) on Ubuntu. Includes virtual
 printers, a viewer of every job with a ZPL editor and preview, an
 application menu entry and a top-bar icon.
EOF

echo "/etc/xdg/autostart/zebra-print-bridge-tray.desktop" >"$PKG/DEBIAN/conffiles"

# preinst: remember whether install.sh had already installed it (the unit file exists although
# dpkg has never installed the package), so postinst treats it as an upgrade, not a fresh install.
cat >"$PKG/DEBIAN/preinst" <<'EOF'
#!/bin/sh
set -e
if [ "$1" = "install" ] && [ -f "${ZPB_ROOT:-}/usr/lib/systemd/user/zebra-print-bridge.service" ]; then
  touch "${ZPB_FROM_SOURCE_MARK:-/run/zebra-print-bridge.from-source}" 2>/dev/null || true
fi
exit 0
EOF

cat >"$PKG/DEBIAN/postinst" <<'EOF'
#!/bin/sh
set -e
UNIT=zebra-print-bridge.service
MARK="${ZPB_FROM_SOURCE_MARK:-/run/zebra-print-bridge.from-source}"
# Users with a running systemd user manager (logged in), without root and system accounts.
session_users() {
  loginctl list-users --no-legend 2>/dev/null | awk '$1 >= 1000 && $1 < 60000 {print $2}'
}
if [ "$1" = "configure" ]; then
  # Starting at login is each user's choice (systemctl --user enable/disable). A fresh install
  # turns it on for the users logged in now; others turn it on from the top-bar icon, the
  # viewer or `zebra-print-bridge-ctl autostart on`. Upgrades keep every user's choice, except
  # from versions before 0.3.0, which enabled it for every user (--global): that link goes and
  # the users logged in now keep starting at login.
  # Fresh = first install of the program on this machine (not an upgrade, not over install.sh).
  FRESH=no
  [ -z "$2" ] && FRESH=yes
  if [ -e "$MARK" ]; then
    FRESH=no
    rm -f "$MARK"
  fi
  WAS_GLOBAL=no
  [ -e "${ZPB_GLOBAL_WANTS:-/etc/systemd/user/default.target.wants/$UNIT}" ] && WAS_GLOBAL=yes
  if [ $WAS_GLOBAL = yes ]; then
    systemctl --global disable $UNIT >/dev/null 2>&1 || true
  fi
  for unit_user in $(session_users); do
    systemctl --user -M "$unit_user@" daemon-reload >/dev/null 2>&1 || true
    if [ $FRESH = yes ] || [ $WAS_GLOBAL = yes ]; then
      systemctl --user -M "$unit_user@" enable $UNIT >/dev/null 2>&1 || true
    fi
  done
  if command -v udevadm >/dev/null; then
    udevadm control --reload-rules || true
    udevadm trigger --subsystem-match=usbmisc --action=add || true
  fi
  modprobe usblp 2>/dev/null || true
  gtk-update-icon-cache -q /usr/share/icons/hicolor 2>/dev/null || true
  update-desktop-database -q /usr/share/applications 2>/dev/null || true
  # Restart the service of the users that have it running, so they get the new version.
  for unit_user in $(session_users); do
    if systemctl --user -M "$unit_user@" is-active --quiet $UNIT 2>/dev/null; then
      systemctl --user -M "$unit_user@" restart $UNIT || true
    elif [ $FRESH = yes ]; then
      systemctl --user -M "$unit_user@" start $UNIT >/dev/null 2>&1 || true
    fi
  done
  echo "Zebra Print Bridge: open it from the application menu. Start at login is set per user"
  echo "(top-bar icon, viewer Settings, or: zebra-print-bridge-ctl autostart on|off)."
  # Files that install.sh creates and this package does not own: the /usr/local/bin commands
  # shadow the package's, and nothing removes them with the package. Not deleted automatically.
  LEFTOVERS=""
  for f in /usr/local/bin/zebra-print-bridge /usr/local/bin/zebra-print-bridge-ctl \
           /usr/local/bin/zebra-print-bridge-tray /etc/udev/rules.d/70-zebra-print-bridge.rules \
           /etc/modules-load.d/zebra-print-bridge.conf; do
    if [ -e "${ZPB_ROOT:-}$f" ]; then LEFTOVERS="$LEFTOVERS $f"; fi
  done
  if [ -n "$LEFTOVERS" ]; then
    echo
    echo "WARNING: files left by a previous install.sh (this package replaces them). Remove them with:"
    echo "  sudo rm -f$LEFTOVERS"
  fi
fi
exit 0
EOF

cat >"$PKG/DEBIAN/prerm" <<'EOF'
#!/bin/sh
set -e
if [ "$1" = "remove" ]; then
  systemctl --global disable zebra-print-bridge.service >/dev/null 2>&1 || true
  for unit_user in $(loginctl list-users --no-legend 2>/dev/null | awk '$1 >= 1000 && $1 < 60000 {print $2}'); do
    systemctl --user -M "$unit_user@" disable --now zebra-print-bridge.service >/dev/null 2>&1 || true
  done
  pkill -f "zebra_print_bridge.tray" 2>/dev/null || true
fi
exit 0
EOF

cat >"$PKG/DEBIAN/postrm" <<'EOF'
#!/bin/sh
set -e
if [ "$1" = "remove" ] || [ "$1" = "purge" ]; then
  udevadm control --reload-rules 2>/dev/null || true
  gtk-update-icon-cache -q /usr/share/icons/hicolor 2>/dev/null || true
fi
exit 0
EOF
chmod 755 "$PKG/DEBIAN/preinst" "$PKG/DEBIAN/postinst" "$PKG/DEBIAN/prerm" "$PKG/DEBIAN/postrm"

mkdir -p "$SRC/dist"
dpkg-deb --root-owner-group -Zxz --build "$PKG" "$SRC/dist/" >/dev/null
echo "$SRC/dist/${NAME}_${VERSION}_all.deb"
