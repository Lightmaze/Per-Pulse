import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import ts from 'typescript';

// Use the project's compiler so these tests also run on the supported Node 20.
async function loadSource(name) {
  const source = await readFile(new URL(`../src/${name}.ts`, import.meta.url), 'utf8');
  const { outputText } = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext } });
  return import(`data:text/javascript;base64,${Buffer.from(outputText).toString('base64')}`);
}
const storage = await loadSource('storage');
const { getPageActivity, getMessageActivity } = await loadSource('status');

test('source URLs retain page and message identity across a direct load', () => {
  for (const [page, message] of [['home', 'event 1'], ['research / notes', 'event/#2']]) {
    const url = new URL(storage.pageHref(page, message), 'https://workspace.example');
    assert.equal(storage.pageFromLocation(url), page);
    assert.equal(storage.messageFromLocation(url), message);
  }
});

test('malformed or unrelated fragments never break navigation', () => {
  assert.equal(storage.pageFromLocation({ pathname: '/page/%ZZ' }), 'home');
  assert.equal(storage.messageFromLocation({ hash: '#message-%ZZ' }), undefined);
  assert.equal(storage.messageFromLocation({ hash: '#unrelated' }), undefined);
});

test('return history is scoped to the current workspace and page', () => {
  const state = { workspaceId: 'one', pageId: 'work', origin: { pageId: 'home', messageId: 'source' }, canGoBack: true };
  assert.equal(storage.readNavigationState(state, 'two', 'work'), undefined);
  assert.equal(storage.readNavigationState(state, 'one', 'different'), undefined);
  assert.equal(storage.readNavigationState(state, 'one', 'work').canGoBack, true);
  assert.equal(storage.readNavigationState({ ...state, origin: null }, 'one', 'work').canGoBack, false);
  assert.equal(storage.readNavigationState({ ...state, canGoBack: 'true' }, 'one', 'work').canGoBack, false);
});

test('return focus only targets a reference in the destination page', () => {
  const state = { workspaceId: 'one', pageId: 'home', restoreFocus: { pageId: 'home', referenceId: 'link-1' } };
  assert.equal(storage.readNavigationState(state, 'one', 'home').restoreFocus.referenceId, 'link-1');
  assert.equal(storage.readNavigationState({ ...state, restoreFocus: { pageId: 'elsewhere' } }, 'one', 'home').restoreFocus, undefined);
});

test('disconnected saved messages never claim active work', () => {
  for (const status of ['queued', 'working']) {
    assert.equal(getPageActivity(status, false).tone, 'neutral');
    assert.equal(getPageActivity(status, false).label, 'Saved · AI disconnected');
  }
  assert.equal(getPageActivity('working', true).tone, 'active');
  assert.equal(getPageActivity('idle', true).label, 'Ready');
});

test('stale activity stops asserting that work is running', () => {
  assert.equal(getMessageActivity('processing', true, true).label, 'Updates paused');
  assert.notEqual(getMessageActivity('running', true, true).tone, 'active');
});

test('waiting, failure, and uncertain outcomes retain distinct meaning', () => {
  assert.equal(getMessageActivity('blocked', true).label, 'Needs attention');
  assert.match(getMessageActivity('awaiting_approval', true).detail, /Approval is needed/);
  assert.equal(getMessageActivity('failed', true).tone, 'error');
  assert.equal(getMessageActivity('uncertain', true).label, 'Needs review');
  assert.equal(getMessageActivity('succeeded', true), null);
});

test('drafts remain scoped and restricted storage does not break editing', () => {
  const original = Object.getOwnPropertyDescriptor(globalThis, 'localStorage');
  const values = new Map();
  try {
    Object.defineProperty(globalThis, 'localStorage', { configurable: true, value: { getItem: key => values.get(key) ?? null, setItem: (key, value) => values.set(key, value), removeItem: key => values.delete(key) } });
    storage.saveDraft('first', 'home', { text: 'Unsent thought', references: [] });
    assert.equal(storage.readDraft('first', 'home').text, 'Unsent thought');
    assert.equal(storage.readDraft('second', 'home').text, '');
    storage.savePosition('first', 'home', 210);
    assert.equal(storage.readPosition('first', 'home'), 210);
    Object.defineProperty(globalThis, 'localStorage', { configurable: true, get() { throw new Error('Storage unavailable'); } });
    assert.doesNotThrow(() => storage.saveDraft('first', 'home', { text: 'Still editable', references: [] }));
    assert.equal(storage.readDraft('first', 'home').text, '');
  } finally {
    if (original) Object.defineProperty(globalThis, 'localStorage', original);
    else delete globalThis.localStorage;
  }
});
