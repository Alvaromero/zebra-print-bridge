// node --test tests/js
import assert from 'node:assert/strict';
import { test } from 'node:test';

import { escapeHtml, html, join, raw, render } from '../../zebra_print_bridge/static/html.js';

test('values are escaped, attributes included', () => {
  const name = '<img src=x onerror=alert(1)>';
  assert.equal(String(html`<b title="${'"quoted" & \'single\''}">${name}</b>`),
    '<b title="&quot;quoted&quot; &amp; &#39;single&#39;">&lt;img src=x onerror=alert(1)&gt;</b>');
});

test('nested fragments, arrays and raw() are not escaped twice', () => {
  const items = ['a<', 'b&'].map((x) => html`<li>${x}</li>`);
  assert.equal(String(html`<ul>${items}</ul>`), '<ul><li>a&lt;</li><li>b&amp;</li></ul>');
  assert.equal(String(html`${raw('<br>')}`), '<br>');
});

test('null, undefined and false render nothing; 0 renders', () => {
  assert.equal(String(html`[${null}${undefined}${false}${0}${''}]`), '[0]');
  assert.equal(String(html`${1 > 2 && html`<i>no</i>`}`), '');
});

test('join escapes text items and keeps fragments', () => {
  assert.equal(String(join(['x<y', html`<em>z</em>`], ' · ')), 'x&lt;y · <em>z</em>');
});

test('escapeHtml and render on plain values', () => {
  assert.equal(escapeHtml(`<>&"'`), '&lt;&gt;&amp;&quot;&#39;');
  assert.equal(render(['a', ['<b>']]), 'a&lt;b&gt;');
});
