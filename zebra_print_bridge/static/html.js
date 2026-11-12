// HTML templates that escape by default.
//
//   html`<b>${name}</b>`   -> name is escaped; nested html`` fragments, arrays of them and raw() are not.
//   setHtml(element, html`...`)
//
// Anything that is not an Html fragment is treated as text, so forgetting to escape is not possible.

export class Html {
  constructor(value) { this.value = value; }
  toString() { return this.value; }
}

const ENTITIES = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' };

export function escapeHtml(value) {
  return String(value).replace(/[&<>"']/g, (c) => ENTITIES[c]);
}

/** Markup we wrote ourselves (or text already escaped): inserted as it is. */
export function raw(markup) {
  return new Html(String(markup));
}

/** null, undefined and false render nothing, so `${cond && html`...`}` works. */
export function render(value) {
  if (value === null || value === undefined || value === false) return '';
  if (value instanceof Html) return value.value;
  if (Array.isArray(value)) return value.map(render).join('');
  return escapeHtml(value);
}

export function html(strings, ...values) {
  let out = strings[0];
  values.forEach((value, i) => { out += render(value) + strings[i + 1]; });
  return new Html(out);
}

/** Like items.join(separator), escaping what is not already Html. */
export function join(items, separator) {
  return raw(items.map(render).join(render(separator)));
}

export function setHtml(element, content) {
  element.innerHTML = render(content);
}
