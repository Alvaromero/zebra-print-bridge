# Prompt: emular Zebra Browser Print en Ubuntu

> Copiar todo lo que está debajo de la línea en una sesión nueva.

---

Necesito construir en **Ubuntu (22.04/24.04)** un reemplazo de **Zebra Browser Print**, la app de escritorio de Zebra que usamos en Windows. (Antes de empezar, comprueba si Zebra publica todavía una versión oficial para Linux que funcione en Ubuntu 22.04/24.04; si existe, dímelo antes de implementar nada.) Nuestra aplicación web (my.recommercebrands.com, Laravel + Vue 2, se sirve por HTTPS) imprime etiquetas ZPL en impresoras Zebra a través de ella. El objetivo es que la web funcione en Ubuntu **sin cambiar el frontend**: un servicio local que hable exactamente el mismo protocolo HTTP.

## Cómo funciona hoy (Windows)

- Zebra Browser Print es un servicio local que escucha en `http://127.0.0.1:9100/`. Si el navegador es Safari y la página es HTTPS, usa `https://127.0.0.1:9101/`; en Chrome y Firefox siempre usa HTTP 9100.
- La web carga la librería oficial de Zebra `BrowserPrint-3.1.250.min.js` (también existe la 2.0.0.75, con la misma API). Esa librería solo hace `XMLHttpRequest` a ese servicio local. No usa WebSocket ni extensiones.
- El servicio descubre las impresoras Zebra (USB, red o driver de Windows), les envía bytes crudos (ZPL) y lee su respuesta, que la usamos para el estado.

## Protocolo HTTP que hay que implementar (extraído de BrowserPrint-3.1.250.min.js)

Base: `http://127.0.0.1:9100/`. Las peticiones son XHR desde un origen HTTPS (`https://my.recommercebrands.com`, también dominios de QA). Los POST envían `JSON.stringify(...)` **sin Content-Type explícito**, así que llegan como `text/plain;charset=UTF-8`. Cualquier respuesta distinta de HTTP 200 se trata como error: la librería pasa `xhr.response` al callback de error y la web lo muestra al usuario, así que los errores deben ser texto plano legible. Si el servicio no responde (está apagado), el error llega vacío.

Objeto **device**, que el servicio entrega. La librería **no lo devuelve tal cual**: en cada POST arma uno nuevo con solo `name`, `uid`, `connection`, `deviceType`, `version` (siempre `2`), `provider` y `manufacturer`. El servicio debe identificar la impresora **solo por `uid`**; si el `uid` ya no existe, responde con un código distinto de 200 y un texto claro (por ejemplo "Printer not found: <uid>").
```json
{"name":"ZD421-203dpi","deviceType":"printer","connection":"usb|network|driver","uid":"<id estable>","provider":"com.zebra.ds.webdriver.desktop.provider.DefaultDeviceProvider","manufacturer":"Zebra Technologies","version":2}
```

| Método y ruta | Entrada | Respuesta 200 |
|---|---|---|
| `GET /default?type=printer` | — | JSON de un device, o cuerpo vacío `""` si no hay impresora por defecto. **Siempre 200**, aunque no haya impresoras: si falla, la web no llega a llamar a `/available` |
| `GET /available` | — | `{"printer":[device,...]}` (la librería convierte cada arreglo en objetos Device) |
| `GET /config` | — | JSON de configuración, por ejemplo `{"application":{"version":"1.3.x","build_number":..,"api_level":2,"platform":"linux","supportedConversions":{}}}` |
| `POST /write` | `{"device":{...},"data":"<ZPL>"}` | cuerpo vacío. Envía `data` en crudo a la impresora |
| `POST /write` (variantes) | `{"device":{...},"url":"..."}` o multipart (`json` + `blob`) | Lo usan `sendUrl`/`sendFile`; **nuestra web no los usa**, son opcionales |
| `POST /read` | `{"device":{...}}` | texto leído de la impresora desde el último write (puede ser vacío) |
| `POST /convert`, `/convert/scan` | multipart | Conversión de imágenes o PDF a ZPL; **no se usa**, puede responder error |

`sendThenRead(cmd)` = `POST /write` con el comando y, cuando responde, **un único** `POST /read`. La librería **no reintenta** (`readRetries` es 0 salvo para bluetooth), así que la espera es responsabilidad del servicio:
- Guardar lo que la impresora responda después de un write (buffer por device) y devolverlo en el `/read` siguiente.
- Si el buffer está vacío, el `/read` **se queda esperando** (long-poll) hasta recibir datos o hasta un timeout de unos 2–3 s (configurable). Al llegar al timeout responde `""` con 200, no con error: `~WC` y `^XA^JSO^MNY~JC^XZ` pueden no devolver nada.
- Cortar la espera en cuanto la respuesta esté completa. En `~HQES` eso es al recibir `\x03` (ETX), más un margen corto de silencio. Así cada impresión no queda bloqueada hasta el timeout, porque después de cada impresión la web consulta el estado.

### CORS y red privada (crítico)
- Los POST van como `text/plain`, así que son *simple requests*: no hay preflight CORS normal, solo el de Private Network Access.
- Responder a **todo, también a los errores (4xx/5xx)**, con `Access-Control-Allow-Origin: <origin>` (o `*`). Si un error no lleva esta cabecera, el navegador oculta el cuerpo y la web muestra un error vacío.
- Manejar `OPTIONS` con `Access-Control-Allow-Methods: GET, POST, OPTIONS`, `Access-Control-Allow-Headers: *` y **`Access-Control-Allow-Private-Network: true`**. Chrome hace preflight de *Private/Local Network Access* cuando una página pública HTTPS llama a `127.0.0.1`; en versiones recientes además muestra un permiso de "acceso a la red local" que el usuario debe aceptar una vez.
- Una página HTTPS que llama a `http://127.0.0.1` no se bloquea por mixed content en Chrome ni en Firefox, porque localhost se considera de confianza. Verificarlo de todos modos en ambos navegadores.
- Opcional: escuchar también en `https://127.0.0.1:9101` con un certificado autofirmado que el usuario confíe (solo hace falta para Safari, que no aplica en Ubuntu).
- Escuchar **solo en 127.0.0.1**, nunca en 0.0.0.0, porque cualquiera en la red podría imprimir o leer la impresora. Opcionalmente, una lista de orígenes permitidos configurable.

## Cómo lo usa nuestra web (para saber qué es imprescindible)

- Llaman a `BrowserPrint`: `resources/js/components/utils/print-component.vue` (principal), `warehouse-printer.vue` y `return-printer.vue`. `warehouse-transparency-labels-print-sticker.vue` usa la lista de impresoras (por `uid`), pero no llama al servicio. Unas 22 vistas de warehouse y returns cargan `/js/BrowserPrint.min.js`, que `webpack.mix.js` genera a partir de `resources/js/utils/BrowserPrint-3.1.250.min.js`.
- Al montar: `getDefaultDevice('printer')`. **Solo si responde bien** se llama a `getLocalDevices(..., 'printer')` y se habilita la impresión. Si `/default` falla, la impresión queda deshabilitada sin el toast. Si falla `/available`, se muestra "Error getting local devices, verify zebra browser print".
- La impresora elegida se guarda por **`uid`** en `localStorage.printer` y en el backend (`api.admin.users.printer`, con su DPI, 203 o 300). **El `uid` debe ser estable** entre reinicios y reconexiones (por ejemplo el número de serie del USB o `ip:puerto`), o el usuario tendrá que volver a elegirla cada vez. No hace falta reproducir los `uid` de Windows: al migrar, cada usuario elige su impresora una vez. Lo ideal es que en la config se pueda fijar el `uid` de una impresora a mano.
- Comandos que se envían:
  - Etiquetas: ZPL completo (`^XA ... ^XZ`), traído del backend (`api.admin.utils.ZPL-template`). También se envían íconos/gráficos (`~DG`/`^GF`) a **todas** las impresoras cuando cambia la lista ("Sync Icons").
  - Estado: `~HQES` con `sendThenRead`, cada 5 min y después de cada impresión. La web busca líneas que contengan `ERROR` o `WARNING` y decodifica los flags. La respuesta típica es:
    ```
    \x02
      PRINTER STATUS
         ERRORS:         1 00000000 00000005
         WARNINGS:       0 00000000 00000000
    \x03
    ```
  - Página de configuración: `~WC`. Calibración: `^XA^JSO^MNY~JC^XZ`.
- `send()` se considera exitoso con HTTP 200 y cuerpo `""` o `"{}"`.

## Requisitos del emulador en Ubuntu

1. **Descubrimiento de impresoras Zebra:**
   - **USB:** `/dev/usb/lp*` (driver `usblp`). Identificar las Zebra por vendor id `0a5f` (sysfs/udev) y obtener modelo y serie. Darle permisos al usuario (regla udev o grupo `lp`).
   - **Red:** lista configurable de IPs (TCP crudo, puerto 9100, bidireccional). Opcional: descubrimiento por mDNS o broadcast.
   - **Opcional:** colas CUPS raw (`lpstat`), solo escritura y sin lectura de estado.
2. **Escritura y lectura:**
   - **USB:** escribir en `/dev/usb/lpX` y leer del mismo dispositivo con timeout (no bloqueante). Si la lectura por `usblp` no es fiable con algún modelo, plan B: `libusb` (pyusb/gousb) contra los endpoints bulk IN/OUT, desconectando `usblp` para ese dispositivo.
   - **Red:** mantener o abrir un socket a `ip:9100`, escribir y leer con timeout.
   - Serializar el acceso por impresora (cola o lock), porque la web puede mandar `sendThenRead` mientras imprime.
3. **Impresora por defecto:** configurable. Si no está configurada, usar la primera Zebra detectada.
4. **Servicio de usuario:** `systemd --user` o un autostart de escritorio, que arranque al iniciar sesión; logs en journald o en un archivo.
5. **Instalación sencilla:** un `.deb`, un script de instalación o un binario único (Go, Node o Python). Debe incluir la regla udev y la unidad systemd.
6. **Configuración** (JSON o YAML en `~/.config/...`): puerto, impresoras de red, impresora por defecto y orígenes permitidos.
7. **Tests:**
   - Un mock de impresora (TCP que responda a `~HQES` y guarde lo recibido) para probar sin hardware. **Ojo:** el servicio ya ocupa `127.0.0.1:9100`, así que el mock debe escuchar en otra IP de loopback (por ejemplo `127.0.0.2:9100`) o en otro puerto, configurable.
   - Prueba end-to-end cargando la misma `BrowserPrint-3.1.250.min.js` en una página HTML local servida por HTTPS, que llame a `getDefaultDevice`, `getLocalDevices`, `send` y `sendThenRead("~HQES")`.

## Qué espero de la sesión

1. Primero, una propuesta breve: lenguaje, librería HTTP, cómo resolverás USB y red y cómo manejarás CORS y PNA. Señala riesgos (permisos USB, lectura bidireccional por usblp, cambios de Chrome sobre Local Network Access).
2. Después, la implementación con tests y un README de instalación y uso en Ubuntu.
3. No modificar la web ni la librería BrowserPrint: el emulador debe ser transparente para ella. Si algo es imposible sin tocar la web, explícalo y propón el cambio mínimo.
4. Responder en español.

Referencia: la librería exacta está en `resources/js/utils/BrowserPrint-3.1.250.min.js` del repo my.recommercebrands.com (7,7 KB, legible). Úsala como fuente de verdad del protocolo.
