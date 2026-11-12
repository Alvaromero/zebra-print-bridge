// node --test tests/js
import assert from 'node:assert/strict';
import { test } from 'node:test';

import { html } from '../../zebra_print_bridge/static/html.js';
import { detectLanguage, I18N, LANGUAGES, setLanguage, t, tHtml } from '../../zebra_print_bridge/static/i18n.js';

test('language detection', () => {
  const cases = [
    [null, ['pt-BR'], 'pt-BR'], [null, ['pt-PT', 'en'], 'pt-BR'], [null, ['pt'], 'pt-BR'],
    [null, ['fr-CA'], 'fr'], [null, ['de-AT', 'en'], 'de'], [null, ['es-MX'], 'es'],
    [null, ['ja', 'pt-BR'], 'pt-BR'], [null, ['ja', 'zh'], 'en'], [null, [], 'en'],
    ['de', ['es'], 'de'],            // the user's choice wins
    ['xx', ['es'], 'es'],            // an unknown saved value is ignored
    ['__proto__', [], 'en'],         // not an inherited property
  ];
  for (const [saved, browser, expected] of cases) {
    assert.equal(detectLanguage(saved, browser), expected, `${saved} ${browser}`);
  }
});

test('every language has every key, with the same placeholders and tags', () => {
  const marks = (s) => [...s.matchAll(/\{\w+\}|<\/?\w+/g)].map((m) => m[0]).sort().join(',');
  for (const lang of Object.keys(LANGUAGES)) {
    assert.ok(I18N[lang], `missing block ${lang}`);
    assert.deepEqual(Object.keys(I18N[lang]).sort(), Object.keys(I18N.en).sort(), lang);
    for (const [key, text] of Object.entries(I18N.en)) {
      assert.equal(marks(I18N[lang][key]), marks(text), `${lang} ${key}`);
    }
  }
});

test('only tHtml keeps markup, and it escapes its parameters', () => {
  setLanguage('en');
  assert.match(t('preview.off', { count: 2 }), /<b>/);  // plain text: html`` would escape it
  const markup = String(tHtml('origins.request', { origin: '<script>' }));
  assert.ok(markup.includes('&lt;script&gt;'));
  assert.ok(String(tHtml('origins.request', { origin: html`<b>x</b>` })).includes('<b>x</b>'));
});

test('missing keys fall back to English, then to the key', () => {
  setLanguage('de');
  const saved = I18N.de['jobs.title'];
  delete I18N.de['jobs.title'];
  try {
    assert.equal(t('jobs.title'), I18N.en['jobs.title']);
  } finally {
    I18N.de['jobs.title'] = saved;
  }
  assert.equal(t('no.such.key'), 'no.such.key');
  setLanguage('en');
});
