// Settings view, website approval banner and the stop button. Loaded before app.js; it only
// defines functions and uses app.js helpers (api, t, esc, toast, state) when they are called.

let view = 'jobs';          // 'jobs' | 'settings'
let settingsData = null;
let networkTest = null;     // {key, result} of the last connection test

function setView(next) {
  view = next;
  document.querySelectorAll('[data-view]').forEach((tab) => tab.classList.toggle('active', tab.dataset.view === view));
  $('#jobs-view').hidden = view !== 'jobs';
  $('#settings-view').hidden = view !== 'settings';
  if (view === 'settings') loadSettings();
}

async function loadSettings() {
  try { settingsData = await api('/ui/api/settings'); } catch (err) { toast(err.message); return; }
  renderSettings();
}

// --- website approval ------------------------------------------------------------

function renderOriginBanner() {
  const pending = state?.pending_origins || [];
  const badge = $('#settings-badge');
  badge.hidden = !pending.length;
  badge.textContent = pending.length;
  $('#origin-banner').innerHTML = pending.map((p) => `
    <div class="banner">
      <span>🔒 ${t('origins.request', { origin: `<b>${esc(p.origin)}</b>` })}</span>
      <span class="spacer"></span>
      <button class="primary" data-origin="${esc(p.origin)}" data-action="allow">${t('origins.allow')}</button>
      <button class="danger" data-origin="${esc(p.origin)}" data-action="deny">${t('origins.deny')}</button>
    </div>`).join('');
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

function renderSettings() {
  if (!settingsData || !state) return;
  const s = settingsData, o = s.origins;
  const list = (items, action, label) => items.length ? items.map((origin) => `
    <div class="row-item"><span class="mono">${esc(origin)}</span><span class="spacer"></span>
      <button data-origin="${esc(origin)}" data-action="${action}">${label}</button></div>`).join('')
    : `<div class="muted">${t('settings.none')}</div>`;

  const networkRows = [
    ...s.network_printers.config.map((p) => ({ ...p, source: 'config' })),
    ...s.network_printers.user.map((p) => ({ ...p, source: 'user' })),
  ];
  const testResult = (key) => {
    if (!networkTest || networkTest.key !== key) return '';
    const r = networkTest.result;
    if (r === null) return `<div class="muted">${t('settings.testing')}</div>`;
    return r.ok
      ? `<div class="ok-text">✓ ${t('settings.test_ok', { ms: r.ms })}${r.response ? `<pre class="mono">${showControl(r.response)}</pre>` : ` <span class="muted">${t('settings.test_no_status')}</span>`}</div>`
      : `<div class="error-text">✗ ${esc(r.error)}</div>`;
  };

  const uptime = Math.round((Date.now() / 1000 - s.service.started) / 60);
  $('#settings-view').innerHTML = `<div class="settings">
    <section class="card">
      <h2>${t('settings.origins')}</h2>
      <div class="card-body">
        <p class="muted">${o.approval ? t('settings.origins_help') : t('settings.origins_no_approval')}</p>
        ${o.pending.length ? `<h3>${t('settings.pending')}</h3>${o.pending.map((p) => `
          <div class="row-item"><span class="mono">${esc(p.origin)}</span>
            <span class="muted">${t('settings.requests', { count: p.requests })}</span><span class="spacer"></span>
            <button class="primary" data-origin="${esc(p.origin)}" data-action="allow">${t('origins.allow')}</button>
            <button class="danger" data-origin="${esc(p.origin)}" data-action="deny">${t('origins.deny')}</button></div>`).join('')}` : ''}
        <h3>${t('settings.allowed')}</h3>${list(o.allowed, 'forget', t('settings.remove'))}
        <h3>${t('settings.denied')}</h3>${list(o.denied, 'forget', t('settings.unblock'))}
        <h3>${t('settings.preapproved')}</h3>
        <div class="muted">${t('settings.preapproved_help', { file: `<span class="mono">${esc(s.service.config_file)}</span>` })}</div>
        ${o.patterns.map((p) => `<div class="row-item"><span class="mono">${esc(p)}</span></div>`).join('') || `<div class="muted">${t('settings.none')}</div>`}
      </div>
    </section>

    <section class="card">
      <h2>${t('settings.network')}</h2>
      <div class="card-body">
        <p class="muted">${t('settings.network_help')}</p>
        ${networkRows.length ? `<table><thead><tr><th>${t('settings.name')}</th><th>${t('settings.address')}</th><th>uid</th><th></th></tr></thead><tbody>
          ${networkRows.map((p) => `<tr>
            <td>${esc(p.name || '')}</td><td class="mono">${esc(p.host)}:${p.port || 9100}</td><td class="mono">${esc(p.uid)}</td>
            <td class="actions">${p.source === 'config' ? `<span class="badge">${t('settings.from_config')}</span>` : ''}
              <button data-test-host="${esc(p.host)}" data-test-port="${p.port || 9100}">${t('settings.test')}</button>
              ${p.source === 'user' ? `<button class="danger" data-remove-printer="${esc(p.uid)}">${t('settings.remove')}</button>` : ''}</td></tr>
            <tr><td colspan="4" class="test-cell">${testResult(`${p.host}:${p.port || 9100}`)}</td></tr>`).join('')}
        </tbody></table>` : `<div class="muted">${t('settings.none')}</div>`}
        <form id="network-form" class="inline-form">
          <input name="host" placeholder="${esc(t('settings.host'))}" required>
          <input name="port" class="num" type="number" min="1" max="65535" value="9100">
          <input name="name" placeholder="${esc(t('settings.name_optional'))}">
          <input name="uid" placeholder="${esc(t('settings.uid_optional'))}">
          <button type="button" id="network-test">${t('settings.test')}</button>
          <button class="primary" type="submit">${t('settings.add')}</button>
        </form>
        <div id="network-form-test">${testResult('form')}</div>
      </div>
    </section>

    <section class="card">
      <h2>${t('settings.default')}</h2>
      <div class="card-body">
        <p class="muted">${t('settings.default_help')}</p>
        <select id="default-printer">
          <option value="">${t('settings.default_auto')}</option>
          ${state.printers.map((p) => `<option value="${esc(p.uid)}" ${p.uid === s.default_printer.user ? 'selected' : ''}>${esc(p.name)} (${esc(p.uid)})</option>`).join('')}
        </select>
        <div class="muted">${t('settings.default_effective', { name: esc(state.printers.find((p) => p.uid === s.default_printer.effective)?.name || t('settings.none')) })}</div>
      </div>
    </section>

    <section class="card">
      <h2>${t('settings.service')}</h2>
      <div class="card-body">
        <dl class="facts">
          <dt>${t('settings.version')}</dt><dd class="mono">${esc(s.service.version)}</dd>
          <dt>${t('settings.address')}</dt><dd class="mono">${esc(s.service.address)}</dd>
          <dt>${t('settings.uptime')}</dt><dd>${t('settings.minutes', { count: uptime })} · PID <span class="mono">${s.service.pid}</span>${s.service.systemd ? ' · systemd' : ''}</dd>
          <dt>${t('settings.config_file')}</dt><dd class="mono">${esc(s.service.config_file)}</dd>
          <dt>${t('settings.settings_file')}</dt><dd class="mono">${esc(s.service.settings_file)}</dd>
          <dt>${t('settings.virtual_file')}</dt><dd class="mono">${esc(s.service.virtual_printers_file)}</dd>
        </dl>
        <div class="stop-box">
          <div><b>${t('settings.stop_title')}</b><div class="muted">${t('settings.stop_help')}</div></div>
          <button class="danger" id="stop-service">${t('settings.stop')}</button>
        </div>
      </div>
    </section>
  </div>`;
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

function setupSettings() {
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
    if (e.target.id === 'stop-service') stopService();
  });
  root.addEventListener('submit', async (e) => {
    if (e.target.id !== 'network-form') return;
    e.preventDefault();
    const f = e.target;
    try {
      settingsData = await api('/ui/api/settings/network/add', {
        host: f.host.value, port: +f.port.value || 9100, name: f.name.value, uid: f.uid.value });
      networkTest = null;
      toast(t('settings.added'));
      renderSettings();
      refresh(true);
    } catch (err) { toast(err.message); }
  });
  root.addEventListener('change', async (e) => {
    if (e.target.id !== 'default-printer') return;
    try { settingsData = await api('/ui/api/settings/default', { uid: e.target.value || null }); renderSettings(); }
    catch (err) { toast(err.message); }
  });
}

// Called by app.js after every state poll.
function onStateUpdate(online) {
  if (online && !$('#stopped').hidden) $('#stopped').hidden = true;  // it was started again
  if (!online) return;
  renderOriginBanner();
}
