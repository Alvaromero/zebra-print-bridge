# Generar y repartir el instalador

[English version](BUILDING.md) (la versión de referencia: si difieren, manda la inglesa)

Hay dos paquetes. Los dos se generan en `dist/`, que no se sube a git:

| Paquete | Para | Qué instala |
|---|---|---|
| **`.deb`** (recomendado) | El equipo, en Ubuntu 22.04 o posterior | Todo: servicio, regla udev para USB, entrada en el menú de aplicaciones, icono en la barra superior, y trae las dependencias opcionales |
| **Wheel** (`.whl`) | Desarrollo, con `pipx` | Solo los comandos: sin servicio, sin regla udev, sin entrada en el menú |

## 1. Requisitos (en la máquina donde se construye)

- Ubuntu o Debian con `python3` (3.10+) y `dpkg-deb`, que vienen instalados.
- `python3-setuptools`, solo para el wheel: `sudo apt install python3-setuptools`.
- No hace falta root, pip, `build` ni `fakeroot`.

## 2. Preparar la versión

1. **Subir la versión.** Está en un solo sitio, `zebra_print_bridge/__init__.py`:

   ```python
   __version__ = "0.4.1"
   ```

   El `.deb` y el wheel la leen de ahí. Súbela siempre antes de repartir: `apt` no trata un paquete con la misma versión como una actualización.

2. **Pasar los tests:**

   ```bash
   python3 -m unittest
   ```

   `tests/test_autostart.py` construye el `.deb` y ejecuta sus scripts de instalación con `systemctl`/`loginctl` simulados, así que los problemas de empaquetado salen aquí.

3. **Hacer commit.** El paquete se construye con los archivos tal como están, incluidos los cambios sin commit. Construir desde un commit te dice exactamente qué lleva cada versión.

4. **Publicar una release (lo recomendado para todo lo que repartas).** Añade la sección de la versión a `CHANGELOG.md`, fusiona el cambio de versión con un pull request y etiqueta `main`:

   ```bash
   git switch main && git pull
   git tag v0.4.1 && git push origin v0.4.1
   ```

   El [workflow de release](../.github/workflows/release.yml) comprueba que la etiqueta coincide con `__version__`, ejecuta los tests, construye el `.deb` y el wheel en GitHub y los publica con `SHA256SUMS` y una atestación de procedencia firmada. Manda a tu equipo a la página de la release en vez de pasar archivos: así pueden comprobar que lo que instalan se construyó desde el código público (README, "Verifying a download"). Las secciones 3 y 4 de abajo son para construir en local.

## 3. Construir el `.deb`

```bash
tools/build_deb.sh
# -> dist/zebra-print-bridge_<versión>_all.deb
```

Revisarlo antes de repartirlo:

```bash
dpkg-deb -I dist/zebra-print-bridge_*_all.deb   # versión, dependencias, descripción
dpkg-deb -c dist/zebra-print-bridge_*_all.deb   # lista de archivos
```

Qué lleva dentro:

| Ruta | Qué |
|---|---|
| `/opt/zebra-print-bridge/` | El programa (misma estructura que `install.sh`) |
| `/usr/bin/zebra-print-bridge`, `-ctl`, `-tray` | Comandos |
| `/usr/lib/systemd/user/zebra-print-bridge.service` | Servicio de usuario |
| `/usr/lib/udev/rules.d/70-zebra-print-bridge.rules` | Acceso a las Zebra USB para el usuario con la sesión abierta |
| `/usr/lib/modules-load.d/zebra-print-bridge.conf` | Carga `usblp` al arrancar |
| `/usr/share/applications/zebra-print-bridge.desktop` | Entrada en el menú de aplicaciones |
| `/etc/xdg/autostart/zebra-print-bridge-tray.desktop` | Icono en la barra superior al iniciar sesión (archivo de configuración) |
| `/usr/share/icons/hicolor/scalable/apps/zebra-print-bridge.svg` | Icono de la aplicación |

Qué hacen los scripts del paquete:

- **Instalación nueva:** activa "iniciar al iniciar sesión" para los usuarios con la sesión abierta en ese momento y arranca su servicio.
- **Actualización:** respeta lo que eligió cada usuario y reinicia los servicios que estén en marcha.
- **Sobre una instalación de `install.sh`:** la trata como actualización (respeta la elección) y muestra los archivos que dejó `install.sh`, con el comando `sudo rm` para borrarlos.
- **Desde una versión anterior a la 0.3.0:** quita la activación antigua "para todos los usuarios" (`systemctl --global`) y la mantiene para los usuarios con la sesión abierta.
- **Desinstalación:** detiene y desactiva el servicio de los usuarios con la sesión abierta.

## 4. Construir el wheel (opcional)

```bash
python3 tools/build_wheel.py
# -> dist/zebra_print_bridge-<versión>-py3-none-any.whl
```

## 5. Instalar el `.deb` (cada persona del equipo)

1. **¿Instalaste antes con `./install.sh`?** Ejecuta `./uninstall.sh` primero, **antes** de instalar el `.deb`. Nunca ejecutes `uninstall.sh` después de instalar el `.deb`: borraría los archivos del paquete (ahora se niega a hacerlo).

2. Copia el paquete fuera de tu carpeta personal e instálalo:

   ```bash
   cp zebra-print-bridge_<versión>_all.deb /tmp/
   sudo apt install /tmp/zebra-print-bridge_<versión>_all.deb
   ```

   - Pon una ruta (`/tmp/…` o `./…`): sin ella, `apt` busca en sus repositorios.
   - Usa `apt`, no `dpkg -i`: `apt` instala también los paquetes recomendados (AppIndicator para el icono, Pillow para JPG/GIF/TIFF/WebP, `poppler-utils` para PDF, `libnotify-bin` para las notificaciones).
   - Desde `/tmp` no aparece el aviso de `_apt` "Permiso denegado". Ese aviso es inofensivo: solo indica que `apt` leyó el archivo como root porque su usuario aislado no puede leer tu carpeta personal.

3. Después de instalar:

   - El servicio de quien instaló está en marcha y arranca al iniciar sesión. Cada usuario lo cambia en el visor (Ajustes → Servicio), en el icono de la barra superior o con `zebra-print-bridge-ctl autostart on|off`.
   - El icono de la barra superior aparece al volver a iniciar sesión. Para verlo ya: `zebra-print-bridge-tray &`.
   - ¿Había una impresora USB conectada? Desconéctala y vuelve a conectarla.

4. Comprobar:

   ```bash
   zebra-print-bridge --list          # impresoras detectadas
   zebra-print-bridge-ctl status      # el servicio responde
   dpkg -V zebra-print-bridge         # sin salida = no falta ni cambió ningún archivo del paquete
   ```

   Visor: <http://127.0.0.1:9100/ui>

## 6. Actualizar y desinstalar

- **Actualizar:** construye el `.deb` nuevo (con la versión subida) e instálalo igual. Los servicios en marcha se reinician y se respeta la elección de cada usuario.
- **Desinstalar:** `sudo apt remove zebra-print-bridge`. No hace falta el repositorio: el paquete detiene el servicio de cada usuario y borra sus propios archivos. Se conservan `~/.config/zebra-print-bridge` y `~/.local/state/zebra-print-bridge` (configuración, sitios aprobados, impresoras virtuales, historial), y también `/etc/zebra-print-bridge`, que no pertenece al paquete. Para borrarlo todo: `rm -rf ~/.config/zebra-print-bridge ~/.local/state/zebra-print-bridge` en cada usuario y `sudo rm -rf /etc/zebra-print-bridge`. El historial puede contener nombres y direcciones de las etiquetas.

## 7. Instalar el wheel (desarrollo)

```bash
pipx install dist/zebra_print_bridge-<versión>-py3-none-any.whl
zebra-print-bridge --port 9200       # otro puerto, para no chocar con el servicio instalado
pipx inject zebra-print-bridge Pillow   # opcional: JPG/GIF/TIFF/WebP
```

El icono de la barra superior no funciona desde `pipx`: su entorno aislado no ve `python3-gi` del sistema.

## Problemas frecuentes

| Síntoma | Causa y solución |
|---|---|
| `dpkg -V zebra-print-bridge` muestra archivos `missing`; `systemctl --user status zebra-print-bridge` dice "could not be found" | Se borraron los archivos del paquete (por ejemplo, ejecutando `uninstall.sh` sobre el `.deb`). Reinstala reponiendo todo, incluido el archivo de configuración: `sudo apt install --reinstall -o Dpkg::Options::=--force-confmiss /tmp/zebra-print-bridge_<versión>_all.deb` y luego `zebra-print-bridge-ctl start` |
| No aparece el icono en la barra superior | Falta `gir1.2-ayatanaappindicator3-0.1` (`sudo apt install gir1.2-ayatanaappindicator3-0.1`) o, en GNOME, está apagada la extensión "AppIndicator and KStatusNotifierItem Support". Ejecuta `zebra-print-bridge-tray` en una terminal para ver el error |
| `install.sh` dice que el `.deb` está instalado | Es lo esperado: no hay que mezclar las dos formas. Actualiza con el `.deb` nuevo, o `sudo apt remove zebra-print-bridge` antes de usar `install.sh` |
| Al instalar sale el aviso "files left by a previous install.sh" | Ejecuta el `sudo rm -f …` que muestra: esas copias tapan los comandos del paquete y se quedarían al desinstalarlo |
| El servicio arranca al iniciar sesión aunque lo apagaste | Una instalación anterior a la 0.3.0 lo activó para todos los usuarios: instala el `.deb` actual (migra solo) o `sudo systemctl --global disable zebra-print-bridge.service` |

## Preaprobar vuestra web

El paquete no trae ningún sitio preaprobado salvo `localhost`: la primera vez que una web llama al servicio, el usuario la aprueba en el visor (igual que Browser Print). Para ahorrar ese paso al equipo, repartid junto al `.deb` un `config.json` con vuestros dominios:

```json
{
  "allowed_origins": ["https://app.example.com", "https://*.app.example.com"]
}
```

e instaladlo una vez por máquina, para todos sus usuarios:

```bash
sudo install -D -m 644 config.json /etc/zebra-print-bridge/config.json
zebra-print-bridge-ctl stop && zebra-print-bridge-ctl start   # o cerrar sesión y volver a entrar
```

El `~/.config/zebra-print-bridge/config.json` de cada usuario sigue funcionando por encima: sus `allowed_origins` se suman a los de la organización y cualquier otra clave reemplaza la de la organización. Los patrones son `fnmatch` contra la cabecera `Origin` (esquema, host y puerto). Todas las opciones están en `config.example.json`. `zebra-print-bridge-ctl diagnose` muestra qué archivos se leyeron.

## Limitaciones del reparto

- No hay repositorio `apt`: cada actualización se reparte a mano (el `.deb` nuevo, con la versión subida). Un repositorio `apt` y un flujo que publique los paquetes en cada versión están en [ROADMAP.md](../ROADMAP.md).
