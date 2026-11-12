"""Top-bar icon (AppIndicator): service state, printers, websites waiting, open/start/stop.

Needs PyGObject and the Ayatana AppIndicator typelib:
    sudo apt install gir1.2-ayatanaappindicator3-0.1
On GNOME the "AppIndicator and KStatusNotifierItem Support" extension shows it (Ubuntu ships it on).
"""

import locale
import os
import shutil
import sys
import threading
from pathlib import Path

from .config import ConfigError
from .control import Control, autostart_state
from .control import set_autostart as set_service_autostart

STATIC = Path(__file__).parent / "static"
POLL_SECONDS = 5
AUTOSTART_NAME = "zebra-print-bridge-tray.desktop"

TEXT = {
    "en": {
        "running": "Running · {printers} printer(s)",
        "stopped": "Service stopped",
        "pending": "{count} website(s) waiting for approval",
        "open": "Open viewer",
        "start": "Start service",
        "stop": "Stop service",
        "autostart": "Show this icon at login",
        "service_autostart": "Start the service at login",
        "quit": "Hide icon",
        "busy": "Working…",
        "ready": "ready",
        "error": "error",
        "warning": "warning",
        "offline": "offline",
    },
    "es": {
        "running": "En marcha · {printers} impresora(s)",
        "stopped": "Servicio detenido",
        "pending": "{count} sitio(s) esperando aprobación",
        "open": "Abrir visor",
        "start": "Iniciar servicio",
        "stop": "Detener servicio",
        "autostart": "Mostrar este icono al iniciar sesión",
        "service_autostart": "Iniciar el servicio al iniciar sesión",
        "quit": "Ocultar icono",
        "busy": "Trabajando…",
        "ready": "lista",
        "error": "error",
        "warning": "aviso",
        "offline": "sin conexión",
    },
    "pt-BR": {
        "running": "Em execução · {printers} impressora(s)",
        "stopped": "Serviço parado",
        "pending": "{count} site(s) aguardando aprovação",
        "open": "Abrir visualizador",
        "start": "Iniciar serviço",
        "stop": "Parar serviço",
        "autostart": "Mostrar este ícone ao fazer login",
        "service_autostart": "Iniciar o serviço ao fazer login",
        "quit": "Ocultar ícone",
        "busy": "Processando…",
        "ready": "pronta",
        "error": "erro",
        "warning": "aviso",
        "offline": "desconectada",
    },
    "fr": {
        "running": "En marche · {printers} imprimante(s)",
        "stopped": "Service arrêté",
        "pending": "{count} site(s) en attente d’approbation",
        "open": "Ouvrir le visualiseur",
        "start": "Démarrer le service",
        "stop": "Arrêter le service",
        "autostart": "Afficher cette icône à l’ouverture de session",
        "service_autostart": "Démarrer le service à l’ouverture de session",
        "quit": "Masquer l’icône",
        "busy": "En cours…",
        "ready": "prête",
        "error": "erreur",
        "warning": "avertissement",
        "offline": "hors ligne",
    },
    "de": {
        "running": "Läuft · {printers} Drucker",
        "stopped": "Dienst gestoppt",
        "pending": "{count} Website(s) warten auf Genehmigung",
        "open": "Viewer öffnen",
        "start": "Dienst starten",
        "stop": "Dienst stoppen",
        "autostart": "Dieses Symbol bei der Anmeldung anzeigen",
        "service_autostart": "Dienst bei der Anmeldung starten",
        "quit": "Symbol ausblenden",
        "busy": "Wird ausgeführt…",
        "ready": "bereit",
        "error": "Fehler",
        "warning": "Warnung",
        "offline": "offline",
    },
}


def language() -> str:
    """The first locale preference, matched like the viewer: any Portuguese gets pt-BR, unknown gets English."""
    for value in (
        os.environ.get("LANGUAGE", ""),
        os.environ.get("LC_ALL", ""),
        os.environ.get("LC_MESSAGES", ""),
        os.environ.get("LANG", ""),
        (locale.getlocale()[0] or ""),
    ):
        code = value.split(":")[0][:2].lower()
        if code == "pt":
            return "pt-BR"
        if code in TEXT:
            return code
        if code:
            break
    return "en"


def describe(state, lang: str = "en") -> dict:
    """What the icon shows for a /ui/api/state (None = service not answering). No GTK here."""
    t = TEXT.get(lang, TEXT["en"])
    if state is None:
        return {
            "icon": "tray-stopped-symbolic",
            "attention": False,
            "running": False,
            "lines": [t["stopped"]],
            "printers": [],
        }
    pending = state.get("pending_origins") or []
    statuses = state.get("printer_status") or {}
    printers = []
    for printer in state.get("printers", []):
        status = statuses.get(printer["uid"], {})
        label = {
            "ready": t["ready"],
            "error": ", ".join(status.get("errors") or []) or t["error"],
            "warning": ", ".join(status.get("warnings") or []) or t["warning"],
            "offline": t["offline"],
        }.get(status.get("state"), "")
        printers.append(f"{printer['name']}" + (f" — {label}" if label else ""))
    problem = any(s.get("state") in ("error", "offline") for s in statuses.values())
    lines = [t["running"].format(printers=len(printers))]
    if pending:
        lines.append(t["pending"].format(count=len(pending)))
    attention = bool(pending) or problem
    return {
        "icon": "tray-attention-symbolic" if attention else "tray-ready-symbolic",
        "attention": attention,
        "running": True,
        "lines": lines,
        "printers": printers,
    }


def autostart_path() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return Path(base) / "autostart" / AUTOSTART_NAME


def autostart_enabled() -> bool:
    """The system entry (/etc/xdg/autostart) is on unless the user's copy says Hidden=true."""
    user = autostart_path()
    if user.exists():
        text = user.read_text(errors="replace")
        return "Hidden=true" not in text and "X-GNOME-Autostart-enabled=false" not in text
    return any(
        (Path(d) / "autostart" / AUTOSTART_NAME).exists()
        for d in (os.environ.get("XDG_CONFIG_DIRS") or "/etc/xdg").split(":")
    )


def set_autostart(enabled: bool):
    """Per-user override of the system autostart entry (the freedesktop way)."""
    path = autostart_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    command = shutil.which("zebra-print-bridge-tray") or f"{sys.executable} -m zebra_print_bridge.tray"
    path.write_text(
        "[Desktop Entry]\nType=Application\nName=Zebra Print Bridge (top-bar icon)\n"
        f"Exec={command}\nIcon=zebra-print-bridge\nNoDisplay=true\n"
        f"Hidden={'false' if enabled else 'true'}\n"
        f"X-GNOME-Autostart-enabled={'true' if enabled else 'false'}\n"
    )


class Tray:  # pragma: no cover - needs a graphical session
    def __init__(self):
        import gi

        gi.require_version("Gtk", "3.0")
        gi.require_version("AyatanaAppIndicator3", "0.1")
        from gi.repository import AyatanaAppIndicator3 as AppIndicator
        from gi.repository import GLib, Gtk

        self.Gtk, self.GLib, self.AppIndicator = Gtk, GLib, AppIndicator
        self.control = Control()
        self.t = TEXT[language()]
        self.view = None
        self.busy = False
        self.indicator = AppIndicator.Indicator.new(
            "zebra-print-bridge", "tray-stopped-symbolic", AppIndicator.IndicatorCategory.APPLICATION_STATUS
        )
        self.indicator.set_icon_theme_path(str(STATIC))
        self.indicator.set_attention_icon_full("tray-attention-symbolic", "attention")
        self.indicator.set_title("Zebra Print Bridge")
        self.indicator.set_status(AppIndicator.IndicatorStatus.ACTIVE)
        self.render(describe(None, language()))
        self.poll()
        GLib.timeout_add_seconds(POLL_SECONDS, self.poll)

    def poll(self):
        def work():
            view = describe(self.control.state(), language())
            self.GLib.idle_add(self.render, view)

        threading.Thread(target=work, daemon=True).start()
        return True  # keep the GLib timer

    def item(self, menu, label, callback=None, sensitive=True):
        widget = self.Gtk.MenuItem(label=label)
        widget.set_sensitive(sensitive and not self.busy)
        if callback:
            widget.connect("activate", lambda _w: callback())
        menu.append(widget)
        return widget

    def render(self, view):
        if view == self.view:
            return False
        self.view = view
        Gtk, t = self.Gtk, self.t
        self.indicator.set_icon_full(view["icon"], view["lines"][0])
        status = self.AppIndicator.IndicatorStatus
        self.indicator.set_status(status.ATTENTION if view["attention"] else status.ACTIVE)

        menu = Gtk.Menu()
        for line in [t["busy"]] if self.busy else view["lines"]:
            self.item(menu, line, sensitive=False)
        for printer in view["printers"][:10]:
            self.item(menu, f"   {printer}", sensitive=False)
        menu.append(Gtk.SeparatorMenuItem())
        self.item(menu, t["open"], lambda: self.run(self.control.open))
        if view["running"]:
            self.item(menu, t["stop"], lambda: self.run(self.control.stop))
        else:
            self.item(menu, t["start"], lambda: self.run(self.control.start))
        menu.append(Gtk.SeparatorMenuItem())
        service = autostart_state()
        if service["available"]:
            service_check = Gtk.CheckMenuItem(label=t["service_autostart"])
            service_check.set_active(service["enabled"])
            service_check.set_sensitive(not self.busy)
            service_check.connect("toggled", lambda w: self.run(lambda: set_service_autostart(w.get_active())))
            menu.append(service_check)
        check = Gtk.CheckMenuItem(label=t["autostart"])
        check.set_active(autostart_enabled())
        check.connect("toggled", lambda w: set_autostart(w.get_active()))
        menu.append(check)
        self.item(menu, t["quit"], Gtk.main_quit)
        menu.show_all()
        self.indicator.set_menu(menu)
        return False

    def run(self, action):
        """Start/stop/open take a few seconds: run them off the GTK thread."""

        def work():
            try:
                action()
            except RuntimeError as exc:
                from .control import notify

                notify("Zebra Print Bridge", str(exc))
            self.busy = False
            self.view = None
            self.poll()

        self.busy = True
        last, self.view = self.view, None
        self.render(last)  # same state, with "Working…" and the actions disabled
        threading.Thread(target=work, daemon=True).start()


def main():  # pragma: no cover - needs a graphical session
    try:
        tray = Tray()
    except ConfigError as exc:
        print(f"Invalid configuration: {exc}", file=sys.stderr)
        return 1
    except (ImportError, ValueError) as exc:
        print(
            "The top-bar icon needs PyGObject and AppIndicator:\n"
            "    sudo apt install gir1.2-ayatanaappindicator3-0.1\n"
            f"({exc})",
            file=sys.stderr,
        )
        return 1
    import signal

    signal.signal(signal.SIGINT, signal.SIG_DFL)
    tray.Gtk.main()
    return 0


if __name__ == "__main__":
    sys.exit(main())
