// Helpers shared by the viewer's modules.
import { escapeHtml, raw } from './html.js';

export const $ = (selector, root = document) => root.querySelector(selector);

/** localStorage with JSON values; it may be unavailable (private windows, blocked storage). */
export const store = {
  get(key, fallback) { try { return JSON.parse(localStorage.getItem(key)) ?? fallback; } catch { return fallback; } },
  set(key, value) { try { localStorage.setItem(key, JSON.stringify(value)); } catch {} },
};

export const debounce = (fn, ms) => {
  let id;
  return (...args) => { clearTimeout(id); id = setTimeout(() => fn(...args), ms); };
};

export function toast(message) {
  const el = $('#toast');
  el.textContent = message;
  el.classList.add('show');
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => el.classList.remove('show'), 3000);
}

/** GET (no body) or POST JSON to the viewer's API; non-2xx answers throw with the server's message. */
export async function api(path, body) {
  const res = await fetch(path, body === undefined ? {} : {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
  const text = await res.text();
  if (!res.ok) throw new Error(text || res.statusText);
  return text ? JSON.parse(text) : null;
}

/** A printer answer with its STX/ETX framing made visible. */
export function showControl(text) {
  return raw(escapeHtml(text).replace(/\x02/g, '<span class="ctl">␂</span>').replace(/\x03/g, '<span class="ctl">␃</span>'));
}
