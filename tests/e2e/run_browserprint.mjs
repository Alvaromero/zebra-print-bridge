// Runs the real BrowserPrint-3.1.250.min.js library against the bridge, like the web app does.
// Usage: BRIDGE_URL=http://127.0.0.1:9100/ node tests/e2e/run_browserprint.mjs
import { readFileSync } from 'node:fs';
import vm from 'node:vm';

const BRIDGE_URL = process.env.BRIDGE_URL || 'http://127.0.0.1:9100/';
const ORIGIN = process.env.ORIGIN || 'https://my.recommercebrands.com';
const LIB = new URL('./vendor/BrowserPrint-3.1.250.min.js', import.meta.url);

// Minimal XMLHttpRequest on top of fetch. The library hardcodes 127.0.0.1:9100,
// so the URL is rewritten to the test bridge's port.
class XMLHttpRequest {
  static DONE = 4;
  withCredentials = false;
  readyState = 0;
  status = 0;
  responseType = '';
  responseText = '';
  response = '';
  open(method, url) {
    this.method = method;
    this.url = url.replace('http://127.0.0.1:9100/', BRIDGE_URL);
  }
  send(body) {
    // Like the browser: string -> text/plain;charset=UTF-8, no explicit header.
    fetch(this.url, { method: this.method, body: body ?? undefined, headers: { Origin: ORIGIN } })
      .then(async (res) => {
        this.status = res.status;
        this.responseText = this.response = await res.text();
      })
      .catch(() => { this.status = 0; })
      .finally(() => {
        this.readyState = 4;
        this.onreadystatechange?.();
      });
  }
}

const context = vm.createContext({
  XMLHttpRequest,
  navigator: { userAgent: 'Mozilla/5.0 (X11; Linux x86_64) Chrome/140.0 Safari/537.36' },
  location: { protocol: 'https:' },
  console,
  setTimeout,
  clearTimeout,
  FormData,
  Blob,
});
context.window = context;
vm.runInContext(readFileSync(LIB, 'utf8'), context);
const BrowserPrint = context.BrowserPrint;

const call = (fn) => new Promise((resolve, reject) => fn(resolve, reject));
const result = {};

const printer = await call((ok, err) => BrowserPrint.getDefaultDevice('printer', ok, err));
result.default = printer && { uid: printer.uid, name: printer.name, connection: printer.connection };

const devices = await call((ok, err) => BrowserPrint.getLocalDevices(ok, err, 'printer'));
result.available = devices.map((d) => d.uid);

result.config = await call((ok, err) => BrowserPrint.getApplicationConfiguration(ok, err));

result.send = await call((ok, err) => devices[0].send('^XA^FO50,50^FDe2e^FS^XZ', ok, err));

result.status = await call((ok, err) => devices[0].sendThenRead('~HQES', ok, err));

const ghost = new BrowserPrint.Device({ ...devices[0], uid: 'missing' });
result.ghostError = await call((ok, err) => ghost.send('x', () => err('unexpected success'), ok));

console.log(JSON.stringify(result));
