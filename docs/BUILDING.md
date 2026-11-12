# Building and distributing the installer

[Versión en español](BUILDING.es.md)

There are two packages. Both are written to `dist/`, which is not committed:

| Package | For | What it installs |
|---|---|---|
| **`.deb`** (recommended) | The team, on Ubuntu 22.04 or newer | Everything: service, USB udev rule, application menu entry, top-bar icon, and it pulls the optional dependencies |
| **Wheel** (`.whl`) | Development, with `pipx` | Only the commands: no service, no udev rule, no menu entry |

## 1. Requirements (on the build machine)

- Ubuntu or Debian with `python3` (3.10+) and `dpkg-deb`, both installed by default.
- `python3-setuptools`, only for the wheel: `sudo apt install python3-setuptools`.
- No root, pip, `build` or `fakeroot` needed.

## 2. Prepare the release

1. **Bump the version.** It lives in one place, `zebra_print_bridge/__init__.py`:

   ```python
   __version__ = "0.4.1"
   ```

   The `.deb` and the wheel read it from there. Always bump it before distributing: `apt` does not treat a package with the same version as an upgrade.

2. **Run the tests:**

   ```bash
   python3 -m unittest
   ```

   `tests/test_autostart.py` builds the `.deb` and runs its maintainer scripts with fake `systemctl`/`loginctl`, so packaging problems show up here.

3. **Commit.** The package is built from the working tree, uncommitted changes included. Building from a commit tells you exactly what each version contains.

## 3. Build the `.deb`

```bash
tools/build_deb.sh
# -> dist/zebra-print-bridge_<version>_all.deb
```

Check it before handing it out:

```bash
dpkg-deb -I dist/zebra-print-bridge_*_all.deb   # version, dependencies, description
dpkg-deb -c dist/zebra-print-bridge_*_all.deb   # file list
```

What is inside:

| Path | What |
|---|---|
| `/opt/zebra-print-bridge/` | The program (same layout as `install.sh`) |
| `/usr/bin/zebra-print-bridge`, `-ctl`, `-tray` | Commands |
| `/usr/lib/systemd/user/zebra-print-bridge.service` | User service |
| `/usr/lib/udev/rules.d/70-zebra-print-bridge.rules` | Access to Zebra USB printers for the logged-in user |
| `/usr/lib/modules-load.d/zebra-print-bridge.conf` | Loads `usblp` at boot |
| `/usr/share/applications/zebra-print-bridge.desktop` | Application menu entry |
| `/etc/xdg/autostart/zebra-print-bridge-tray.desktop` | Top-bar icon at login (a conffile) |
| `/usr/share/icons/hicolor/scalable/apps/zebra-print-bridge.svg` | Application icon |

What the maintainer scripts do:

- **Fresh install:** turns "start at login" on for the users logged in at that moment, and starts their service.
- **Upgrade:** keeps each user's choice and restarts the services that are running.
- **Over an `install.sh` installation:** treated as an upgrade (the choice is kept), and it prints the `install.sh` files left behind with the `sudo rm` command to remove them.
- **From a version before 0.3.0:** removes the old "every user" activation (`systemctl --global`) and keeps starting at login for the users logged in.
- **Removal:** stops and disables the service of the users logged in.

## 4. Build the wheel (optional)

```bash
python3 tools/build_wheel.py
# -> dist/zebra_print_bridge-<version>-py3-none-any.whl
```

## 5. Install the `.deb` (each team member)

1. **Installed before with `./install.sh`?** Run `./uninstall.sh` first, while the `.deb` is **not** installed yet. Never run `uninstall.sh` after installing the `.deb`: it would delete the package's files (it now refuses to run in that case).

2. Copy the package out of your home directory and install it:

   ```bash
   cp zebra-print-bridge_<version>_all.deb /tmp/
   sudo apt install /tmp/zebra-print-bridge_<version>_all.deb
   ```

   - Use a path (`/tmp/…` or `./…`): without one, `apt` searches its repositories.
   - Use `apt`, not `dpkg -i`: `apt` also installs the recommended packages (AppIndicator for the top-bar icon, Pillow for JPG/GIF/TIFF/WebP, `poppler-utils` for PDF, `libnotify-bin` for notifications).
   - From `/tmp` there is no `_apt` "Permission denied" notice. That notice is harmless: it only means `apt` read the file as root because its sandbox user cannot read your home directory.

3. After installing:

   - The service of the user who installed is running and starts at login. Each user changes that in the viewer (Settings → Service), in the top-bar icon, or with `zebra-print-bridge-ctl autostart on|off`.
   - The top-bar icon appears at the next login. To see it now: `zebra-print-bridge-tray &`.
   - USB printer already plugged in? Unplug it and plug it back in.

4. Check:

   ```bash
   zebra-print-bridge --list          # detected printers
   zebra-print-bridge-ctl status      # the service answers
   dpkg -V zebra-print-bridge         # no output = no package file missing or changed
   ```

   Viewer: <http://127.0.0.1:9100/ui>

## 6. Upgrade and remove

- **Upgrade:** build the new `.deb` (with a higher version) and install it the same way. Running services are restarted; start-at-login choices are kept.
- **Remove:** `sudo apt remove zebra-print-bridge`. No repository is needed: the package stops every user's service and removes its own files. `~/.config/zebra-print-bridge` and `~/.local/state/zebra-print-bridge` are kept (configuration, approved websites, virtual printers, job history), and so is `/etc/zebra-print-bridge`, which the package does not own. To remove everything: `rm -rf ~/.config/zebra-print-bridge ~/.local/state/zebra-print-bridge` for each user and `sudo rm -rf /etc/zebra-print-bridge`. The history may hold names and addresses from the labels.

## 7. Install the wheel (development)

```bash
pipx install dist/zebra_print_bridge-<version>-py3-none-any.whl
zebra-print-bridge --port 9200       # another port, so it does not clash with the installed service
pipx inject zebra-print-bridge Pillow   # optional: JPG/GIF/TIFF/WebP
```

The top-bar icon does not work from `pipx`: its isolated environment cannot see the system's `python3-gi`.

## Troubleshooting

| Symptom | Cause and fix |
|---|---|
| `dpkg -V zebra-print-bridge` lists `missing` files; `systemctl --user status zebra-print-bridge` says "could not be found" | The package's files were deleted (for example by running `uninstall.sh` over the `.deb`). Reinstall restoring everything, the conffile included: `sudo apt install --reinstall -o Dpkg::Options::=--force-confmiss /tmp/zebra-print-bridge_<version>_all.deb`, then `zebra-print-bridge-ctl start` |
| The top-bar icon does not show | `gir1.2-ayatanaappindicator3-0.1` missing (`sudo apt install gir1.2-ayatanaappindicator3-0.1`), or on GNOME the "AppIndicator and KStatusNotifierItem Support" extension is off. Run `zebra-print-bridge-tray` in a terminal to see the error |
| `install.sh` says the `.deb` is installed | Expected: do not mix both. Upgrade with the new `.deb`, or `sudo apt remove zebra-print-bridge` before using `install.sh` |
| After installing, the warning "files left by a previous install.sh" | Run the `sudo rm -f …` command it prints; those copies shadow the package's commands and would stay after removing the package |
| The service starts at login although you turned it off | An installation from before 0.3.0 enabled it for every user: install the current `.deb` (it migrates) or `sudo systemctl --global disable zebra-print-bridge.service` |

## Pre-approving your web app

The package ships no website pre-approved except `localhost`: the first time a web app calls the service, the user approves it in the viewer (Browser Print does the same). To skip that step for your team, hand out a `config.json` with your domains next to the `.deb`:

```json
{
  "allowed_origins": ["https://app.example.com", "https://*.app.example.com"]
}
```

and install it once per machine, for every user:

```bash
sudo install -D -m 644 config.json /etc/zebra-print-bridge/config.json
zebra-print-bridge-ctl stop && zebra-print-bridge-ctl start   # or log out and in
```

A user's own `~/.config/zebra-print-bridge/config.json` still works on top of it: its `allowed_origins` are added to the organization's, and any other key replaces the organization's value. Patterns are `fnmatch` against the `Origin` header (scheme, host and port). See `config.example.json` for every setting. `zebra-print-bridge-ctl diagnose` shows which files were read.

## Distribution limits

- There is no `apt` repository: every upgrade is handed out by hand (the new `.deb`, with the version bumped). An apt repository and a workflow that publishes the packages on each release are in [ROADMAP.md](../ROADMAP.md).
