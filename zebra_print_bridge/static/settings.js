// Settings view, website approval banner and the stop button.
import { refresh, state } from './app.js';
import { $, api, showControl, toast } from './common.js';
import { html, setHtml } from './html.js';
import { t, tHtml } from './i18n.js';

export let view = 'jobs';   // 'jobs' | 'settings'
let settingsData = null;
let networkTest = null;     // {key, result} of the last connection test
let discovered = null;      // null: not searched; 'searching'; or the /discover response

export function setView(next) {
  view = next;
  document.querySelectorAll('[data-view]').forEach((tab) => tab.classList.toggle('active', tab.dataset.view === view));
  $('#jobs-view').hidden = view !== 'jobs';
  $('#settings-view').hidden = view !== 'settings';
  if (view === 'settings') loadSettings();
}

export async function loadSettings() {
  try { settingsData = await api('/ui/api/settings'); } catch (err) { toast(err.message); return; }
  renderSettings();
}

// --- website approval ------------------------------------------------------------

const decisionButtons = (origin) => html`
  <button class="primary" data-origin="${origin}" data-action="allow">${t('origins.allow')}</button>
  <button class="danger" data-origin="${origin}" data-action="deny">${t('origins.deny')}</button>`;

export function renderOriginBanner() {
  const pending = state?.pending_origins || [];
  const badge = $('#settings-badge');
  badge.hidden = !pending.length;
  badge.textContent = pending.length;
  setHtml($('#origin-banner'), pending.map((p) => html`
    <div class="banner">
      <span>🔒 ${tHtml('origins.request', { origin: html`<b>${p.origin}</b>` })}</span>
      <span class="spacer"></span>
      ${decisionButtons(p.origin)}
    </div>`));
}

async function decideOrigin(origin, action) {
  try {
    settingsData = await api('/ui/api/settings/origin', { origin, action });
    toast(t(`origins.done_${action}`, { origin }));
    if (view === 'settings') renderSettings();
    refresh(true);
  } catch (err) { toast(err.message); }
}

// --- settings view -----------------------------------------------------------------

// Result of the last connection test, shown under the row (or form) it belongs to.
function testResult(key) {
  if (!networkTest || networkTest.key !== key) return '';
  const r = networkTest.result;
  if (r === null) return html`<div class="muted">${t('settings.testing')}</div>`;
  if (!r.ok) return html`<div class="error-text">✗ ${r.error}</div>`;
  return html`<div class="ok-text">✓ ${t('settings.test_ok', { ms: r.ms })}${r.response
    ? html`<pre class="mono">${showControl(r.response)}</pre>`
    : html` <span class="muted">${t('settings.test_no_status')}</span>`}</div>`;
}

const none = () => html`<div class="muted">${t('settings.none')}</div>`;

function originList(items, label) {
  if (!items.length) return none();
  return items.map((origin) => html`
    <div class="row-item"><span class="mono">${origin}</span><span class="spacer"></span>
      <button data-origin="${origin}" data-action="forget">${label}</button></div>`);
}

function originsCard(s) {
  const o = s.origins;
  return html`<section class="card">
    <h2>${t('settings.origins')}</h2>
    <div class="card-body">
      <p class="muted">${o.approval ? t('settings.origins_help') : t('settings.origins_no_approval')}</p>
      ${o.pending.length > 0 && html`<h3>${t('settings.pending')}</h3>${o.pending.map((p) => html`
        <div class="row-item"><span class="mono">${p.origin}</span>
          <span class="muted">${t('settings.requests', { count: p.requests })}</span><span class="spacer"></span>
          ${decisionButtons(p.origin)}</div>`)}`}
      <h3>${t('settings.allowed')}</h3>${originList(o.allowed, t('settings.remove'))}
      <h3>${t('settings.denied')}</h3>${originList(o.denied, t('settings.unblock'))}
      <h3>${t('settings.preapproved')}</h3>
      <div class="muted">${tHtml('settings.preapproved_help', { file: html`<span class="mono">${s.service.config_file}</span>` })}</div>
      ${o.patterns.length ? o.patterns.map((p) => html`<div class="row-item"><span class="mono">${p}</span></div>`) : none()}
    </div>
  </section>`;
}

function networkCard(s) {
  const rows = [
    ...s.network_printers.config.map((p) => ({ ...p, source: 'config' })),
    ...s.network_printers.user.map((p) => ({ ...p, source: 'user' })),
  ];
  const table = html`<table><thead><tr><th>${t('settings.name')}</th><th>${t('settings.address')}</th><th>uid</th><th></th></tr></thead><tbody>
    ${rows.map((p) => {
      const port = p.port || 9100;
      return html`<tr>
        <td>${p.name || ''}</td><td class="mono">${p.host}:${port}</td><td class="mono">${p.uid}</td>
        <td class="actions">${p.source === 'config' && html`<span class="badge">${t('settings.from_config')}</span>`}
          <button data-test-host="${p.host}" data-test-port="${port}">${t('settings.test')}</button>
          ${p.source === 'user' && html`<button class="danger" data-remove-printer="${p.uid}">${t('settings.remove')}</button>`}</td></tr>
        <tr><td colspan="4" class="test-cell">${testResult(`${p.host}:${port}`)}</td></tr>`;
    })}
  </tbody></table>`;
  return html`<section class="card">
    <h2>${t('settings.network')}</h2>
    <div class="card-body">
      <p class="muted">${t('settings.network_help')}</p>
      ${rows.length ? table : none()}
      <form id="network-form" class="inline-form">
        <input name="host" placeholder="${t('settings.host')}" required>
        <input name="port" class="num" type="number" min="1" max="65535" value="9100">
        <input name="name" placeholder="${t('settings.name_optional')}">
        <input name="uid" placeholder="${t('settings.uid_optional')}">
        <button type="button" id="network-test">${t('settings.test')}</button>
        <button class="primary" type="submit">${t('settings.add')}</button>
      </form>
      <div id="network-form-test">${testResult('form')}</div>
      <div class="discover">
        <button id="network-discover" ${discovered === 'searching' ? 'disabled' : ''}>🔍 ${t('settings.discover')}</button>
        <span class="muted">${t('settings.discover_help')}</span>
        ${discoverResults()}
      </div>
    </div>
  </section>`;
}

function defaultPrinterCard(s) {
  const effective = state.printers.find((p) => p.uid === s.default_printer.effective)?.name || t('settings.none');
  return html`<section class="card">
    <h2>${t('settings.default')}</h2>
    <div class="card-body">
      <p class="muted">${t('settings.default_help')}</p>
      <select id="default-printer">
        <option value="">${t('settings.default_auto')}</option>
        ${state.printers.map((p) => html`<option value="${p.uid}" ${p.uid === s.default_printer.user ? 'selected' : ''}>${p.name} (${p.uid})</option>`)}
      </select>
      <div class="muted">${t('settings.default_effective', { name: effective })}</div>
    </div>
  </section>`;
}

function serviceCard(s) {
  const service = s.service;
  const uptime = Math.round((Date.now() / 1000 - service.started) / 60);
  return html`<section class="card">
    <h2>${t('settings.service')}</h2>
    <div class="card-body">
      <dl class="facts">
        <dt>${t('settings.version')}</dt><dd class="mono">${service.version}</dd>
        <dt>${t('settings.address')}</dt><dd class="mono">${service.address}</dd>
        <dt>${t('settings.uptime')}</dt><dd>${t('settings.minutes', { count: uptime })} · PID <span class="mono">${service.pid}</span>${service.systemd && ' · systemd'}</dd>
        <dt>${t('settings.config_file')}</dt><dd class="mono">${service.config_file}</dd>
        <dt>${t('settings.settings_file')}</dt><dd class="mono">${service.settings_file}</dd>
        <dt>${t('settings.virtual_file')}</dt><dd class="mono">${service.virtual_printers_file}</dd>
        <dt>${t('settings.history_file')}</dt><dd>${service.history_file
          ? html`<span class="mono">${service.history_file}</span>` : html`<span class="muted">${t('settings.history_off')}</span>`}</dd>
      </dl>
      ${service.autostart.available && html`<label class="check autostart">
        <input type="checkbox" id="service-autostart" ${service.autostart.enabled ? 'checked' : ''}>
        <span><b>${t('settings.autostart')}</b><div class="muted">${t(service.autostart.global ? 'settings.autostart_global' : 'settings.autostart_help')}</div></span>
      </label>`}
      <div class="stop-box">
        <div><b>${t('settings.stop_title')}</b><div class="muted">${t('settings.stop_help')}</div></div>
        <button class="danger" id="stop-service">${t('settings.stop')}</button>
      </div>
    </div>
  </section>`;
}

export function renderSettings() {
  if (!settingsData || !state) return;
  const s = settingsData;
  setHtml($('#settings-view'), html`<div class="settings">
    ${originsCard(s)}${networkCard(s)}${defaultPrinterCard(s)}${serviceCard(s)}
  </div>`);
}

function discoverResults() {
  if (discovered === null) return '';
  if (discovered === 'searching') return html`<div class="muted">${t('settings.discovering')}</div>`;
  if (!discovered.ok) return html`<div class="error-text">✗ ${discovered.error}</div>`;
  if (!discovered.printers.length) return html`<div class="muted">${t('settings.discover_none')}</div>`;
  return html`<table><thead><tr><th>${t('settings.name')}</th><th>${t('settings.model')}</th><th>${t('settings.address')}</th><th></th></tr></thead><tbody>
    ${discovered.printers.map((p, i) => html`<tr class="${p.zebra ? '' : 'dim'}">
      <td>${p.name}${!p.zebra && html` <span class="badge" title="${t('settings.not_zebra_help')}">${t('settings.not_zebra')}</span>`}</td>
      <td>${p.model || ''}</td>
      <td class="mono">${p.host}:${p.port}${p.hostname && html`<div class="muted">${p.hostname}</div>`}</td>
      <td class="actions"><button data-test-host="${p.host}" data-test-port="${p.port}">${t('settings.test')}</button>
        ${p.added ? html`<span class="badge">${t('settings.already_added')}</span>` : html`<button class="primary" data-add-found="${i}">${t('settings.add')}</button>`}</td></tr>
      <tr><td colspan="4" class="test-cell">${testResult(`${p.host}:${p.port}`)}</td></tr>`)}
  </tbody></table>`;
}

async function discoverPrinters() {
  discovered = 'searching';
  renderSettings();
  try { discovered = await api('/ui/api/settings/network/discover', { timeout: 3 }); }
  catch (err) { discovered = { ok: false, error: err.message, printers: [] }; }
  renderSettings();
}

async function addNetworkPrinter(printer) {
  try {
    settingsData = await api('/ui/api/settings/network/add', printer);
    networkTest = null;
    if (discovered?.printers) {
      discovered.printers.forEach((p) => { if (p.host === printer.host && p.port === printer.port) p.added = true; });
    }
    toast(t('settings.added'));
    renderSettings();
    refresh(true);
  } catch (err) { toast(err.message); }
}

function startCommand() {
  return settingsData?.service.systemd === false ? 'python3 -m zebra_print_bridge' : 'systemctl --user start zebra-print-bridge';
}

async function stopService() {
  if (!settingsData) await loadSettings();
  if (!confirm(t('settings.stop_confirm', { command: startCommand() }))) return;
  try {
    await api('/ui/api/shutdown', {});
  } catch (err) { toast(err.message); return; }
  $('#stopped-command').textContent = startCommand();
  // Only the systemd service starts again at login.
  $('#stopped [data-i18n="stopped.login"]').hidden = settingsData?.service.systemd === false;
  $('#stopped').hidden = false;
}

async function testNetwork(key, host, port) {
  networkTest = { key, result: null };
  renderSettings();
  try { networkTest = { key, result: await api('/ui/api/settings/network/test', { host, port: +port }) }; }
  catch (err) { networkTest = { key, result: { ok: false, error: err.message } }; }
  renderSettings();
}

export function setupSettings() {
  document.querySelectorAll('[data-view]').forEach((tab) => tab.addEventListener('click', () => setView(tab.dataset.view)));
  $('#origin-banner').addEventListener('click', (e) => {
    const { origin, action } = e.target.dataset;
    if (origin) decideOrigin(origin, action);
  });
  $('#stopped-copy').addEventListener('click', () => navigator.clipboard.writeText($('#stopped-command').textContent)
    .then(() => toast(t('settings.command_copied')), () => {}));

  const root = $('#settings-view');
  root.addEventListener('click', async (e) => {
    const d = e.target.dataset;
    if (d.origin) return decideOrigin(d.origin, d.action);
    if (d.testHost) return testNetwork(`${d.testHost}:${d.testPort}`, d.testHost, d.testPort);
    if (d.removePrinter) {
      try { settingsData = await api('/ui/api/settings/network/remove', { uid: d.removePrinter }); renderSettings(); refresh(true); }
      catch (err) { toast(err.message); }
      return;
    }
    if (e.target.id === 'network-test') {
      const f = $('#network-form');
      if (!f.host.value.trim()) return f.host.reportValidity();
      return testNetwork('form', f.host.value.trim(), f.port.value || 9100);
    }
    if (d.addFound) {
      const p = discovered.printers[+d.addFound];
      return addNetworkPrinter({ host: p.host, port: p.port, name: p.name });
    }
    if (e.target.id === 'network-discover') return discoverPrinters();
    if (e.target.id === 'stop-service') stopService();
  });
  root.addEventListener('submit', async (e) => {
    if (e.target.id !== 'network-form') return;
    e.preventDefault();
    const f = e.target;
    addNetworkPrinter({ host: f.host.value, port: +f.port.value || 9100, name: f.name.value, uid: f.uid.value });
  });
  root.addEventListener('change', async (e) => {
    if (e.target.id === 'service-autostart') {
      try {
        settingsData = await api('/ui/api/settings/autostart', { enabled: e.target.checked });
        toast(t(e.target.checked ? 'settings.autostart_on' : 'settings.autostart_off'));
      } catch (err) { toast(err.message); await loadSettings(); return; }
      renderSettings();
      return;
    }
    if (e.target.id !== 'default-printer') return;
    try { settingsData = await api('/ui/api/settings/default', { uid: e.target.value || null }); renderSettings(); }
    catch (err) { toast(err.message); }
  });
}

/** Called by app.js after every state poll. */
export function onStateUpdate(online) {
  if (online && !$('#stopped').hidden) $('#stopped').hidden = true;  // it was started again
  if (!online) return;
  renderOriginBanner();
}
