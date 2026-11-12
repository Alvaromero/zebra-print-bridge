// Zebra Print Bridge viewer: printers, jobs, job detail, ZPL editor and preview.
// Plain ES modules, no build step. Markup goes through html`` (html.js), which escapes by default.
import { $, api, debounce, showControl, store, toast } from './common.js';
import { html, join, setHtml } from './html.js';
import { applyStaticTranslations, currentLang, LANGUAGES, setLanguage, t, tHtml } from './i18n.js';
import { loadSettings, onStateUpdate, renderOriginBanner, renderSettings, setupSettings, setView, view } from './settings.js';

const CONNECTION = { usb: 'USB', network: 'printers.network', driver: 'CUPS', bluetooth: 'Bluetooth' };
const STATUS_PRESETS = [
  ['preset.ready', [], []], ['preset.media_out', ['media_out'], []], ['preset.ribbon_out', ['ribbon_out'], []],
  ['preset.head_open', ['head_open'], []], ['preset.paused', ['paused'], []],
  ['preset.calibrate', [], ['need_calibrate']],
];
const DENSITIES = [[152, '6 dpmm (152 dpi)'], [203, '8 dpmm (203 dpi)'], [300, '12 dpmm (300 dpi)'], [600, '24 dpmm (600 dpi)']];
// Common label sizes, in inches (what Labelary and the printers use internally).
const SIZE_PRESETS = [
  ['4 × 6 in', 4, 6], ['4 × 3 in', 4, 3], ['4 × 2 in', 4, 2], ['3 × 2 in', 3, 2],
  ['2.25 × 1.25 in', 2.25, 1.25], ['2 × 1 in', 2, 1],
  ['100 × 150 mm', 100 / 25.4, 150 / 25.4], ['100 × 100 mm', 100 / 25.4, 100 / 25.4],
  ['100 × 50 mm', 100 / 25.4, 50 / 25.4], ['50 × 25 mm', 50 / 25.4, 25 / 25.4],
];
const UNIT_FACTOR = { in: 1, cm: 2.54, mm: 25.4 };
const UNIT_DECIMALS = { in: 2, cm: 2, mm: 1 };

export let state = null;    // last /ui/api/state answer
let rev = -1;
let mode = null;            // 'job' | 'editor' | null
let selectedId = null, detail = null;
const editor = { text: store.get('editorText', ''), uid: null, lint: { warnings: [], label_list: [] } };
let units = store.get('units', (navigator.language || '').toLowerCase() === 'en-us' ? 'in' : 'mm');
const preview = {
  dpi: 203, width: 4, height: 6, index: 0,
  rotation: store.get('rotation', 0), quality: store.get('quality', 'grayscale'),
  url: null, seq: 0, labelaryWarnings: [], error: null, loading: false,
};

const toUnit = (inches) => +(inches * UNIT_FACTOR[units]).toFixed(UNIT_DECIMALS[units]);
const fromUnit = (value) => +value / UNIT_FACTOR[units];
const sizeText = (w, h) => `${toUnit(w)} × ${toUnit(h)} ${units}`;
const bytes = (n) => n < 1024 ? `${n} B` : n < 1048576 ? `${(n / 1024).toFixed(1)} KB` : `${(n / 1048576).toFixed(1)} MB`;
const timeShort = (time) => new Date(time * 1000).toLocaleTimeString(currentLang);
const labelaryOn = () => state?.labelary && $('#labelary').checked;
const printerOptions = (selected) => state.printers.map((p) =>
  html`<option value="${p.uid}" ${p.uid === selected ? 'selected' : ''}>${p.name}</option>`);
const options = (items, selected) => items.map(([value, label]) =>
  html`<option value="${value}" ${value === selected ? 'selected' : ''}>${label}</option>`);

// --- printers ------------------------------------------------------------------

function printerCard(p, open, openDetails) {
  const kind = p.virtual ? t('printers.virtual') : t(CONNECTION[p.connection] || p.connection);
  const live = liveStatus(p);
  const flagBoxes = (group, label) => Object.entries(state.flags[group]).map(([key, name]) => html`
    <label class="check"><input type="checkbox" data-flag="${group}" data-key="${key}" data-uid="${p.uid}" ${p[group].includes(key) ? 'checked' : ''}> ${label}: ${name}</label>`);
  const virtualControls = p.virtual && html`
    <div class="muted" style="font-size:12px">${p.dpi} dpi · ${sizeText(p.width, p.height)}</div>
    <div class="presets">${STATUS_PRESETS.map(([key], i) => html`<button data-preset="${i}" data-uid="${p.uid}">${t(key)}</button>`)}</div>
    <details data-uid="${p.uid}" ${open.has(p.uid) ? 'open' : ''}><summary>${t('printers.all_flags')}</summary>
      <div class="flags">${flagBoxes('errors', t('printers.error'))}${flagBoxes('warnings', t('printers.warning'))}</div>
      <button class="danger" data-delete="${p.uid}">${t('printers.delete')}</button>
    </details>`;
  const hasDetails = !(p.connection === 'driver' && !p.virtual);  // CUPS queues cannot answer
  return html`<div class="printer">
    <div class="name">${p.name} <span class="badge">${kind}</span> ${live.badge}</div>
    <div class="uid mono">${p.uid}</div>
    ${live.details && html`<div class="live muted">${live.details}</div>`}
    <div class="printer-actions">
      <button data-refresh="${p.uid}" title="${t('printers.refresh')}" ${statusChecking.has(p.uid) ? 'disabled' : ''}>↻</button>
      <button data-test-label="${p.uid}">${t('printers.test_label')}</button>
    </div>
    ${hasDetails && html`<details data-details="${p.uid}" ${openDetails.has(p.uid) ? 'open' : ''}><summary>${t('details.title')}</summary>${detailsBody(p)}</details>`}
    ${virtualControls}</div>`;
}

function renderPrinters() {
  // Redrawing must not close the panels the user has open.
  const openUids = (selector, key) => new Set([...document.querySelectorAll(selector)].map((d) => d.dataset[key]));
  const open = openUids('#printers details[open][data-uid]', 'uid');
  const openDetails = openUids('#printers details[open][data-details]', 'details');
  $('#printer-count').textContent = state.printers.length;
  setHtml($('#printers'), state.printers.length
    ? state.printers.map((p) => printerCard(p, open, openDetails))
    : html`<div class="empty">${t('printers.none')}</div>`);

  const filter = $('#filter-printer'), current = filter.value;
  setHtml(filter, html`<option value="">${t('jobs.all_printers')}</option>${printerOptions(current)}`);
  filter.value = current;
}

// --- printer details (SGD getvar, read when the panel is opened) -----------------------

const DETAIL_KEYS = ['friendly_name', 'serial', 'firmware', 'link_os', 'print_method', 'media_type', 'print_mode',
  'print_width', 'label_length', 'odometer'];
const detailsLoading = new Set();

function detailValue(p, key, value) {
  if (key !== 'print_width' && key !== 'label_length') return value;
  const dpi = state.printer_status?.[p.uid]?.dpi || p.dpi;
  const dots = parseInt(value, 10);
  return dpi && dots ? html`${dots} ${t('details.dots')} <span class="muted">(${toUnit(dots / dpi)} ${units})</span>` : value;
}

function detailsBody(p) {
  const d = state.printer_status?.[p.uid]?.details;
  if (detailsLoading.has(p.uid)) return html`<div class="muted">${t('status.checking')}</div>`;
  if (!d) return '';
  if (!d.supported) {
    return html`<div class="muted">${d.error ? html`<span class="error-text">${d.error}</span>` : t('details.unsupported')}</div>`;
  }
  const rows = DETAIL_KEYS.filter((k) => d.values[k] != null)
    .map((k) => html`<dt>${t(`details.${k}`)}</dt><dd>${detailValue(p, k, d.values[k])}</dd>`);
  return html`<dl class="facts small">${rows}</dl>
    <div class="muted small">${t('status.checked', { when: relativeTime(Date.now() / 1000 - d.checked) })} ·
      <a href="#" data-details-reload="${p.uid}">${t('details.reload')}</a></div>`;
}

async function loadDetails(uid) {
  detailsLoading.add(uid);
  renderPrinters();
  try {
    const details = await api('/ui/api/printers/details', { uid });
    state.printer_status = state.printer_status || {};
    state.printer_status[uid] = { ...(state.printer_status[uid] || {}), details };
  } catch (err) { toast(err.message); }
  detailsLoading.delete(uid);
  renderPrinters();
}

// The toggle event does not bubble: listen in the capture phase.
$('#printers').addEventListener('toggle', (e) => {
  const uid = e.target.dataset?.details;
  if (uid && e.target.open && !state.printer_status?.[uid]?.details && !detailsLoading.has(uid)) loadDetails(uid);
}, true);

// --- live printer status (~HI + ~HQES, queried by the viewer, not recorded as jobs) ---

const STATUS_INTERVAL = 60000;
const statusChecking = new Set();
let statusCheckedAt = 0;

function relativeTime(seconds) {
  if (!Number.isFinite(seconds)) return '';
  const rtf = new Intl.RelativeTimeFormat(currentLang, { numeric: 'auto' });
  return seconds < 60 ? rtf.format(-Math.round(seconds), 'second') : rtf.format(-Math.round(seconds / 60), 'minute');
}

function liveStatus(p) {
  const s = state.printer_status?.[p.uid];
  const names = (keys, group) => keys.map((k) => state.flags[group][k] || k).join(', ');
  // The entry may only hold SGD details (opened before the first status check): no status yet.
  const known = s?.state !== undefined;
  if (statusChecking.has(p.uid) && !known) return { badge: html`<span class="badge">${t('status.checking')}</span>`, details: '' };
  if (!known) return { badge: '', details: '' };
  const badge = {
    ready: () => html`<span class="badge b-ok">${t('printers.ready')}</span>`,
    error: () => html`<span class="badge b-err">${names(s.errors || [], 'errors')}</span>`,
    warning: () => html`<span class="badge b-warn">${names(s.warnings || [], 'warnings')}</span>`,
    offline: () => html`<span class="badge b-err" title="${s.error || ''}">${t('status.offline')}</span>`,
    unsupported: () => html`<span class="badge">${t('status.unsupported')}</span>`,
    no_response: () => html`<span class="badge">${t('status.no_response')}</span>`,
  }[s.state]?.() || '';
  const extra = s.state === 'error' && s.warnings?.length > 0 && html` <span class="badge b-warn">${names(s.warnings, 'warnings')}</span>`;
  const facts = [s.model, s.firmware, s.dpi && `${s.dpi} dpi`].filter(Boolean);
  if (s.state === 'offline' && s.error) facts.push(html`<span class="error-text">${s.error}</span>`);
  facts.push(statusChecking.has(p.uid) ? t('status.checking') : t('status.checked', { when: relativeTime(Date.now() / 1000 - s.checked) }));
  return { badge: html`${badge}${extra}`, details: join(facts, ' · ') };
}

async function refreshStatus(uids) {
  const targets = uids || state.printers.map((p) => p.uid);
  targets.forEach((uid) => statusChecking.add(uid));
  if (!uids) statusCheckedAt = Date.now();
  renderPrinters();
  try {
    state.printer_status = await api('/ui/api/printers/status', uids ? { uids } : {});
  } catch (err) { toast(err.message); }
  targets.forEach((uid) => statusChecking.delete(uid));
  renderPrinters();
}

function statusTick() {
  if (state && document.visibilityState === 'visible' && Date.now() - statusCheckedAt >= STATUS_INTERVAL) refreshStatus();
}

$('#printers').addEventListener('click', async (e) => {
  const { preset, delete: del, uid, refresh: refreshUid, testLabel, detailsReload } = e.target.dataset;
  if (refreshUid) return refreshStatus([refreshUid]);
  if (detailsReload) { e.preventDefault(); return loadDetails(detailsReload); }
  if (testLabel) {
    try {
      const { job_id: jobId } = await api('/ui/api/printers/test-label', { uid: testLabel });
      toast(t('printers.test_label_sent'));
      await refresh(true);
      selectJob(jobId);
    } catch (err) { toast(err.message); }
    return;
  }
  try {
    if (preset !== undefined) {
      const [, errors, warnings] = STATUS_PRESETS[preset];
      await api('/ui/api/virtual/update', { uid, errors, warnings });
      refreshStatus([uid]);
    } else if (del && confirm(t('printers.delete_confirm'))) {
      await api('/ui/api/virtual/delete', { uid: del });
    } else return;
    refresh(true);
  } catch (err) { toast(err.message); }
});
$('#printers').addEventListener('change', async (e) => {
  const { flag, uid, key } = e.target.dataset;
  if (!flag) return;
  const selected = new Set(state.printers.find((p) => p.uid === uid)[flag]);
  e.target.checked ? selected.add(key) : selected.delete(key);
  try { await api('/ui/api/virtual/update', { uid, [flag]: [...selected] }); refresh(true); refreshStatus([uid]); }
  catch (err) { toast(err.message); }
});

function setupAddForm() {
  const form = $('#add-form');
  setHtml($('#add-dpi'), options(DENSITIES, 203));
  setHtml($('#add-preset'), html`<option value="">${t('preview.presets')}…</option>${SIZE_PRESETS.map(([label], i) => html`<option value="${i}">${label}</option>`)}`);
  if (!form.width.value) { form.width.value = toUnit(4); form.height.value = toUnit(6); }
  document.querySelectorAll('.unit-label').forEach((el) => { el.textContent = units; });
}
$('#add-preset').addEventListener('change', (e) => {
  const preset = SIZE_PRESETS[e.target.value];
  if (!preset) return;
  $('#add-form').width.value = toUnit(preset[1]);
  $('#add-form').height.value = toUnit(preset[2]);
  e.target.value = '';
});
$('#add-form').addEventListener('submit', async (e) => {
  e.preventDefault();
  const f = e.target;
  try {
    const p = await api('/ui/api/virtual', {
      name: f.name.value, dpi: +f.dpi.value, width: fromUnit(f.width.value), height: fromUnit(f.height.value) });
    f.name.value = '';
    toast(t('printers.added', { name: p.name }));
    refresh(true);
  } catch (err) { toast(err.message); }
});

// --- jobs ----------------------------------------------------------------------

function visibleJobs() {
  const uid = $('#filter-printer').value, showStatus = $('#show-status').checked;
  return state.jobs.filter((j) => (!uid || j.uid === uid) && (showStatus || j.kind !== 'status')).reverse();
}

function renderJobs() {
  const jobs = visibleJobs();
  $('#job-count').textContent = jobs.length;
  $('#jobs-empty').hidden = jobs.length > 0;
  setHtml($('#jobs'), jobs.map((j) => {
    const kind = j.error ? html`<span class="badge b-err">${t('kind.error')}</span>`
      : html`<span class="badge b-${j.kind}">${t('kind.' + j.kind)}</span>`;
    return html`<tr class="job ${mode === 'job' && j.id === selectedId ? 'selected' : ''}" data-id="${j.id}">
      <td class="mono">${timeShort(j.time)}</td><td>${j.printer}</td>
      <td>${kind}${j.labels > 1 && html` <span class="muted">×${j.labels}</span>`}${j.warnings.length > 0 && html` <span class="badge b-warn" title="${t('lint.title')}">⚠</span>`}</td>
      <td class="mono">${bytes(j.bytes)}</td></tr>`;
  }));
}

$('#jobs').addEventListener('click', (e) => {
  const row = e.target.closest('tr.job');
  if (row) selectJob(+row.dataset.id);
});
$('#filter-printer').addEventListener('change', renderJobs);
$('#show-status').addEventListener('change', () => { store.set('showStatus', $('#show-status').checked); renderJobs(); });
$('#clear').addEventListener('click', async () => {
  await api('/ui/api/jobs/clear', {});
  if (mode === 'job') { mode = null; selectedId = null; detail = null; renderDetail(); }
  refresh(true);
});
$('#new-zpl').addEventListener('click', () => openEditor());

// --- warnings ------------------------------------------------------------------

function warningText(w) {
  const chars = w.chars.map((c) => `"${c}"`).join(' ');
  if (w.code === 'not_utf8') {
    return w.charset === null || w.charset === undefined
      ? t('lint.not_utf8', { chars }) : t('lint.not_utf8_ci', { chars, charset: w.charset });
  }
  return t('lint.' + w.code, { chars });
}

function warningsHtml(local, labelary = []) {
  const items = [
    ...local.map((w) => html`<div class="warning">⚠ ${warningText(w)}</div>`),
    ...labelary.map((w) => html`<div class="warning">${t('lint.labelary')} · ${w.command || ''}
      <span class="muted">${t('lint.at_byte', { byte: w.byte })}</span>: ${w.message}</div>`),
  ];
  return items.length > 0 && html`<div><h3>${t('lint.title')}</h3><div class="warnings">${items}</div></div>`;
}

// --- preview (Labelary-viewer-style controls) ----------------------------------

function previewSource() {
  return mode === 'job' ? { job_id: detail.id } : { zpl: editor.text, uid: editor.uid };
}
function previewLabels() {
  return mode === 'job' ? detail.label_list : editor.lint.label_list;
}
function previewDefaults() {
  const settings = mode === 'job' ? detail.printer_settings
    : state.printers.find((p) => p.uid === editor.uid) || { dpi: 203, width: 4, height: 6 };
  preview.dpi = settings.dpi;
  preview.index = 0;
  const size = previewLabels()[0]?.size;
  [preview.width, preview.height] = size || [settings.width, settings.height];
}

function previewStage() {
  if (preview.error) return html`<div class="error-text">${t('preview.failed', { message: preview.error })}</div>`;
  if (preview.url) return html`<img alt="" src="${preview.url}">`;
  return html`<span class="muted">${t('preview.loading')}</span>`;
}

function previewHtml() {
  const labels = previewLabels();
  if (!state.labelary) return html`<div class="notice">${t('preview.disabled')}</div>`;
  if (!labels.length) return mode === 'job' && html`<div class="notice">${t('preview.no_labels')}</div>`;
  if (!labelaryOn()) return html`<div class="notice">${tHtml('preview.off', { count: labels.length })}</div>`;
  const label = labels[Math.min(preview.index, labels.length - 1)];
  const qualities = ['grayscale', 'bitonal'].map((q) => [q, t('preview.' + q)]);
  return html`<div class="preview" id="preview">
    <div class="controls">
      <label class="field">${t('preview.density')}
        <select data-opt="dpi">${options(DENSITIES, preview.dpi)}</select></label>
      <label class="field">${t('preview.size')}
        <input class="num" type="number" step="any" min="0" data-opt="width" value="${toUnit(preview.width)}"> ×
        <input class="num" type="number" step="any" min="0" data-opt="height" value="${toUnit(preview.height)}"> ${units}</label>
      <select data-opt="preset"><option value="">${t('preview.presets')}…</option>${SIZE_PRESETS.map(([l], i) => html`<option value="${i}">${l}</option>`)}</select>
      <span class="muted">${label?.size ? t('preview.from_zpl') : t('preview.from_printer')}</span>
      <label class="field">${t('preview.rotation')}
        <select data-opt="rotation">${options([0, 90, 180, 270].map((r) => [r, `${r}°`]), preview.rotation)}</select></label>
      <label class="field">${t('preview.quality')}
        <select data-opt="quality">${options(qualities, preview.quality)}</select></label>
      <label class="field">${t('preview.label')}
        <button data-step="-1" ${preview.index <= 0 ? 'disabled' : ''}>‹</button>
        ${preview.index + 1} ${t('preview.of')} ${labels.length}
        <button data-step="1" ${preview.index >= labels.length - 1 ? 'disabled' : ''}>›</button></label>
      ${label?.graphics.length > 0 && html`<span class="muted">^XG ${label.graphics.join(', ')}</span>`}
    </div>
    <div class="stage" id="preview-stage">${previewStage()}</div>
    <div class="footer">${t('preview.download')}:
      <button data-download="png">PNG</button><button data-download="pdf">PDF</button>
      <button data-download="pdf-all">${t('preview.pdf_all')}</button>
    </div>
  </div>`;
}

function mountPreview() {
  const box = $('#preview-area');
  if (!box) return;
  setHtml(box, previewHtml());
  const root = $('#preview');
  if (!root) return;
  root.addEventListener('change', (e) => {
    const opt = e.target.dataset.opt;
    if (opt === 'preset') {
      const preset = SIZE_PRESETS[e.target.value];
      if (preset) [preview.width, preview.height] = [preset[1], preset[2]];
    } else if (opt === 'width' || opt === 'height') {
      const value = fromUnit(e.target.value);
      if (value > 0) preview[opt] = value;
    } else if (opt === 'rotation' || opt === 'dpi') {
      preview[opt] = +e.target.value;
      if (opt === 'rotation') store.set('rotation', preview.rotation);
    } else if (opt === 'quality') {
      preview.quality = e.target.value; store.set('quality', preview.quality);
    } else return;
    drawPreview();
  });
  root.addEventListener('click', (e) => {
    const step = e.target.dataset.step, download = e.target.dataset.download;
    if (step) {
      preview.index += +step;
      const size = previewLabels()[preview.index]?.size;
      if (size) [preview.width, preview.height] = size;
      drawPreview();
    } else if (download) {
      downloadPreview(download);
    }
  });
}

async function fetchPreview(extra) {
  const res = await fetch('/ui/api/preview', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ ...previewSource(), index: preview.index, dpi: preview.dpi, width: preview.width,
      height: preview.height, rotation: preview.rotation, quality: preview.quality, lint: true, ...extra }),
  });
  if (!res.ok) throw new Error(await res.text());
  return res;
}

async function drawPreview() {
  const labels = previewLabels();
  preview.index = Math.max(0, Math.min(preview.index, labels.length - 1));
  if (!labelaryOn() || !labels.length) { mountPreview(); renderWarnings(); return; }
  const seq = ++preview.seq;
  preview.error = null;
  mountPreview();
  try {
    const res = await fetchPreview({ format: 'png' });
    const blob = await res.blob();
    if (seq !== preview.seq) return;
    if (preview.url) URL.revokeObjectURL(preview.url);
    preview.url = URL.createObjectURL(blob);
    preview.labelaryWarnings = JSON.parse(res.headers.get('X-Labelary-Warnings') || '[]');
  } catch (err) {
    if (seq !== preview.seq) return;
    preview.error = err.message;
    preview.labelaryWarnings = [];
  }
  mountPreview();
  renderWarnings();
}

async function downloadPreview(kind) {
  try {
    const res = await fetchPreview({ format: kind === 'png' ? 'png' : 'pdf', all: kind === 'pdf-all' });
    const link = Object.assign(document.createElement('a'), {
      href: URL.createObjectURL(await res.blob()),
      download: `${mode === 'job' ? 'job-' + detail.id : 'zpl'}${kind === 'pdf-all' ? '' : '-' + (preview.index + 1)}.${kind === 'png' ? 'png' : 'pdf'}`,
    });
    link.click();
    setTimeout(() => URL.revokeObjectURL(link.href), 1000);
  } catch (err) { toast(err.message); }
}

function resetPreview() {
  preview.seq++;
  if (preview.url) URL.revokeObjectURL(preview.url);
  Object.assign(preview, { url: null, error: null, labelaryWarnings: [] });
}

function renderWarnings() {
  const box = $('#warnings-area');
  if (!box) return;
  const local = mode === 'job' ? detail.warnings : editor.lint.warnings;
  setHtml(box, warningsHtml(local, labelaryOn() ? preview.labelaryWarnings : []));
}

// --- job detail ----------------------------------------------------------------

async function selectJob(id) {
  mode = 'job';
  selectedId = id;
  history.replaceState(null, '', `#job=${id}`);
  renderJobs();
  try { detail = await api(`/ui/api/jobs/${id}`); } catch (err) { detail = null; toast(err.message); }
  resetPreview();
  if (detail) previewDefaults();
  renderDetail();
}

function responseBlock(response) {
  if (response === null) return '';
  return html`<div><h3>${t('detail.response')}</h3><pre class="mono">${response
    ? showControl(response) : html`<span class="muted">${t('detail.response_empty')}</span>`}</pre></div>`;
}

function renderDetail() {
  const el = $('#detail');
  $('#detail-title').textContent = t(mode === 'editor' ? 'editor.title' : 'detail.title');
  if (mode === 'editor') return renderEditor();
  if (mode !== 'job' || !detail) { setHtml(el, html`<div class="empty">${t('detail.select')}</div>`); return; }
  const d = detail;
  const when = new Date(d.time * 1000);
  const shown = d.data.length > 60000 ? d.data.slice(0, 60000) + '\n' + t('detail.truncated') : d.data;
  setHtml(el, html`<div class="detail">
    <div>
      <h3>#${d.id} · ${d.printer} <span class="muted mono">${d.uid}</span></h3>
      <div class="muted">${when.toLocaleString(currentLang)} · <span class="mono">${when.toISOString().replace('.000', '')}</span> ·
        ${t('kind.' + d.kind)} · ${bytes(d.bytes)}</div>
      ${d.error && html`<div class="error-text">${d.error}</div>`}
    </div>
    <div class="toolbar flat">
      <a class="button" href="/ui/api/jobs/${d.id}/zpl">${t('detail.download_zpl')}</a>
      <button id="copy">${t('detail.copy')}</button>
      <button id="to-editor">${t('detail.open_editor')}</button>
      <span class="spacer"></span>
      <select id="resend-to">${printerOptions(d.uid)}</select>
      <button id="resend">${t('detail.resend')}</button>
    </div>
    <div id="warnings-area"></div>
    <div id="preview-area"></div>
    ${responseBlock(d.response)}
    <div><h3>${t('detail.sent')}</h3><pre class="mono">${shown}</pre></div>
  </div>`);
  $('#copy').onclick = () => navigator.clipboard.writeText(d.data).then(() => toast(t('detail.copied')), () => toast(t('detail.copy_failed')));
  $('#to-editor').onclick = () => openEditor(d.data, d.uid);
  $('#resend').onclick = async () => {
    try { await api('/ui/api/print', { uid: $('#resend-to').value, job_id: d.id }); toast(t('detail.resent')); refresh(true); }
    catch (err) { toast(err.message); }
  };
  renderWarnings();
  drawPreview();
}

// --- ZPL editor (like the Labelary viewer, plus sending to a printer) ----------

function openEditor(text, uid) {
  mode = 'editor';
  selectedId = null;
  history.replaceState(null, '', '#editor');
  if (text !== undefined) editor.text = text;
  editor.uid = uid || editor.uid || state.printers[0]?.uid || null;
  resetPreview();
  renderJobs();
  lintEditor().then(() => { previewDefaults(); renderDetail(); });
}

async function lintEditor() {
  try { editor.lint = await api('/ui/api/lint', { zpl: editor.text, uid: editor.uid }); }
  catch { editor.lint = { warnings: [], label_list: [] }; }
}

const onEditorInput = debounce(async () => {
  store.set('editorText', editor.text);
  const before = editor.lint.label_list.length;
  await lintEditor();
  if (!before && editor.lint.label_list.length) previewDefaults();
  drawPreview();
}, 900);

function renderEditor() {
  const printerName = state.printers.find((p) => p.uid === editor.uid)?.name;
  setHtml($('#detail'), html`<div class="detail">
    <textarea class="zpl" id="zpl" spellcheck="false" placeholder="${t('editor.placeholder')}"></textarea>
    <div class="toolbar flat">
      <button id="redraw">${t('editor.redraw')}</button>
      <span class="spacer"></span>
      <label class="field">${t('editor.printer')} <select id="editor-printer">${printerOptions(editor.uid)}</select></label>
      <button class="primary" id="editor-send">${t('editor.send')}</button>
      <button id="editor-close">${t('editor.close')}</button>
    </div>
    <div class="muted" style="font-size:12px">${t('editor.graphics_from')} ${printerName || t('editor.no_printer')}</div>
    <div id="warnings-area"></div>
    <div id="preview-area"></div>
  </div>`);
  const area = $('#zpl');
  area.value = editor.text;
  area.addEventListener('input', () => { editor.text = area.value; onEditorInput(); });
  $('#redraw').onclick = async () => { await lintEditor(); drawPreview(); };
  $('#editor-printer').onchange = async (e) => { editor.uid = e.target.value; await lintEditor(); renderDetail(); };
  $('#editor-send').onclick = async () => {
    try {
      await api('/ui/api/print', { uid: editor.uid, data: editor.text });
      toast(t('editor.sent', { name: state.printers.find((p) => p.uid === editor.uid)?.name || editor.uid }));
      refresh(true);
    } catch (err) { toast(err.message); }
  };
  $('#editor-close').onclick = () => { mode = null; history.replaceState(null, '', '#'); renderDetail(); };
  renderWarnings();
  drawPreview();
}

// --- global state ----------------------------------------------------------------

export async function refresh(force = false) {
  try {
    const next = await api('/ui/api/state');
    const changed = force || !state || next.rev !== rev || JSON.stringify(next.printers) !== JSON.stringify(state.printers);
    state = next;
    $('#version').textContent = 'v' + state.version;
    $('#labelary-toggle').hidden = !state.labelary;
    onStateUpdate(true);
    if (!changed) return;
    rev = state.rev;
    renderPrinters();
    renderJobs();
    if (view === 'settings') loadSettings();
    if (mode === 'job' && detail && state.jobs.find((j) => j.id === selectedId)?.response !== detail.response) {
      selectJob(selectedId);
    }
  } catch {
    $('#version').textContent = t('app.offline');
    onStateUpdate(false);
  }
}

function renderAll() {
  applyStaticTranslations();
  setupAddForm();
  if (!state) return;
  renderPrinters();
  renderJobs();
  renderDetail();
  renderOriginBanner();
  if (view === 'settings') renderSettings();
}

function selectFromHash() {
  const job = /#job=(\d+)/.exec(location.hash);
  if (job && +job[1] !== selectedId) selectJob(+job[1]);
  else if (location.hash === '#editor' && mode !== 'editor') openEditor();
  else if (location.hash === '#settings') setView('settings');
}

setHtml($('#lang'), options(Object.entries(LANGUAGES), currentLang));
$('#lang').addEventListener('change', (e) => {
  setLanguage(e.target.value);
  renderAll();
});
$('#units').value = units;
$('#units').addEventListener('change', (e) => {
  const form = $('#add-form');
  const [w, h] = [fromUnit(form.width.value), fromUnit(form.height.value)];
  units = e.target.value; store.set('units', units);
  form.width.value = toUnit(w); form.height.value = toUnit(h);
  renderAll();
});
$('#show-status').checked = store.get('showStatus', false);
$('#labelary').checked = store.get('labelary', false);
$('#labelary').addEventListener('change', () => { store.set('labelary', $('#labelary').checked); renderDetail(); });
window.addEventListener('hashchange', selectFromHash);
setupSettings();

renderAll();
refresh(true).then(() => { renderDetail(); selectFromHash(); statusTick(); });
setInterval(refresh, 1500);
setInterval(statusTick, 5000);
document.addEventListener('visibilitychange', statusTick);
