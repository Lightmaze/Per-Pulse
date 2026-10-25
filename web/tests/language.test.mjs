import assert from 'node:assert/strict';
import test from 'node:test';
import { interpolate, languagePreference, LANGUAGE_STORAGE_KEY, readLanguagePreference, resolveLocale, saveLanguagePreference } from '../src/language.ts';
import { commonEnglish, commonChinese } from '../src/locales/common.ts';
import { workspaceEnglish, workspaceChinese } from '../src/locales/workspace.ts';

test('an explicit interface choice overrides browser language order', () => {
  assert.equal(resolveLocale('en', ['zh-CN', 'en-US']), 'en');
  assert.equal(resolveLocale('zh-CN', ['en-US']), 'zh-CN');
});

test('system choice follows the first supported browser language', () => {
  assert.equal(resolveLocale('system', ['en-GB', 'zh-CN']), 'en');
  assert.equal(resolveLocale('system', ['fr-FR', 'zh-TW', 'en-US']), 'zh-CN');
  assert.equal(resolveLocale('system', ['ZH-hans']), 'zh-CN');
  assert.equal(resolveLocale('system', ['enochian', 'zho', 'fr-FR']), 'en');
  assert.equal(resolveLocale('system', []), 'en');
});

test('invalid or stale persisted choices safely follow the system', () => {
  for (const value of [null, undefined, '', 'zh', 'fr', '{}', '<script>', 2]) assert.equal(languagePreference(value), 'system');
  assert.equal(languagePreference('en'), 'en');
  assert.equal(languagePreference('zh-CN'), 'zh-CN');
});

test('browser preference persistence never edits drafts or positions', () => {
  const draftKey = 'per-pulse.ui.v2.world.draft.home';
  const values = new Map([[draftKey, '未发送的 draft']]);
  Object.defineProperty(globalThis, 'localStorage', { configurable: true, value: {
    getItem: key => values.get(key) ?? null,
    setItem: (key, value) => values.set(key, value),
    removeItem: key => values.delete(key),
  } });
  try {
    assert.equal(saveLanguagePreference('zh-CN'), true);
    assert.equal(readLanguagePreference(), 'zh-CN');
    assert.equal(values.get(draftKey), '未发送的 draft');
    assert.equal(saveLanguagePreference('system'), true);
    assert.equal(readLanguagePreference(), 'system');
    assert.equal(values.has(LANGUAGE_STORAGE_KEY), false);
    assert.equal(values.size, 1);
  } finally { delete globalThis.localStorage; }
});

test('restricted browser storage does not prevent an in-memory switch', () => {
  Object.defineProperty(globalThis, 'localStorage', { configurable: true, get() { throw new Error('Storage blocked'); } });
  try {
    assert.equal(readLanguagePreference(), 'system');
    assert.equal(saveLanguagePreference('zh-CN'), false);
    assert.equal(resolveLocale('zh-CN', ['en-US']), 'zh-CN');
  } finally { delete globalThis.localStorage; }
});

test('both catalogues have identical keys and interpolation placeholders', () => {
  const en = { ...commonEnglish, ...workspaceEnglish };
  const zh = { ...commonChinese, ...workspaceChinese };
  const placeholders = text => [...text.matchAll(/\{(\w+)\}/g)].map(match => match[1]).sort();
  assert.equal(Object.keys(en).length, Object.keys(commonEnglish).length + Object.keys(workspaceEnglish).length, 'catalogue keys must not overlap');
  assert.deepEqual(Object.keys(en).sort(), Object.keys(zh).sort());
  for (const key of Object.keys(en)) {
    assert.ok(en[key].trim() && zh[key].trim(), `Missing copy: ${key}`);
    assert.deepEqual(placeholders(en[key]), placeholders(zh[key]), `Placeholder mismatch: ${key}`);
  }
});

test('interpolation preserves literal page names, dollar sequences and braces', () => {
  const title = '$& {model} 中文 <b>Home</b>';
  assert.equal(interpolate('Linked to {title}.', { title }), `Linked to ${title}.`);
  assert.equal(interpolate('{count} · {unknown}', { count: 0 }), '0 · {unknown}');
  assert.equal(interpolate('{toString}', {}), '{toString}');
});
