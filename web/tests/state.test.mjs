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
const statusModule = await loadSource('status');
const { getPageActivity, getMessageActivity } = statusModule;

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
    assert.equal(getPageActivity(status, false).label, 'Saved · No model configured');
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

test('failed draft writes report failure and keep current edits ahead of readable old storage', () => {
  const original = Object.getOwnPropertyDescriptor(globalThis, 'localStorage');
  const values = new Map();
  let fail = false;
  try {
    Object.defineProperty(globalThis, 'localStorage', { configurable: true, value: {
      getItem: key => values.get(key) ?? null,
      setItem: (key, value) => { if (fail) throw new Error('Quota exceeded'); values.set(key, value); },
      removeItem: key => { if (fail) throw new Error('Storage blocked'); values.delete(key); },
    } });
    const old = { text: 'Older stored draft', references: [] };
    const edited = { text: 'New unsaved draft', references: [{ targetPageId: 'work', label: 'Source' }],
      materials: [{ id: 'a'.repeat(32), name: 'Original.txt' }] };
    assert.equal(storage.saveDraft('write-failure', 'home', old), true);
    fail = true;
    assert.equal(storage.saveDraft('write-failure', 'home', edited), false);
    assert.equal(storage.readDraft('write-failure', 'home').text, old.text);
    assert.deepEqual(storage.readCurrentDraft('write-failure', 'home', edited), edited);
    assert.deepEqual(storage.readCurrentDraft('different-world', 'home', edited), storage.EMPTY_DRAFT);
    const empty = { text: '', references: [] };
    assert.equal(storage.saveDraft('write-failure', 'home', empty), false);
    assert.deepEqual(storage.readCurrentDraft('write-failure', 'home', empty), empty);
    fail = false;
    assert.equal(storage.saveDraft('write-failure', 'home', edited), true);
    assert.deepEqual(storage.readDraft('write-failure', 'home'), edited);
    assert.equal(storage.saveDraft('write-failure', 'home', empty), true);
    assert.deepEqual(storage.readDraft('write-failure', 'home'), empty);
  } finally {
    if (original) Object.defineProperty(globalThis, 'localStorage', original);
    else delete globalThis.localStorage;
  }
});

test('a failed turn explains its cause from the Kernel error code', () => {
  const { failureReason } = statusModule;
  assert.equal(failureReason(null), null);
  assert.equal(failureReason('provider_http_401').key, 'status.failureAuth');
  assert.equal(failureReason('provider_http_429').key, 'status.failureRateLimited');
  assert.equal(failureReason('provider_http_503').key, 'status.failureService');
  assert.equal(failureReason('provider_http_400').key, 'status.failureRejected');
  assert.equal(failureReason('provider_timeout').key, 'status.failureUnreachable');
  for (const code of ['web_url_invalid', 'web_address_blocked', 'web_dns_failed', 'web_read_timeout',
    'web_redirect_failed', 'web_content_unsupported', 'web_http_failed', 'web_connection_failed']) {
    assert.equal(failureReason(code).key, 'status.failureWeb');
    assert.match(failureReason(code).text, /public HTTPS text page/);
  }
  assert.equal(failureReason('provider_output_limit').key, 'status.failureOutputLimit');
  assert.equal(failureReason('payload_too_large').key, 'status.failurePayloadTooLarge');
  assert.match(failureReason('payload_too_large').text, /Review completed work/);
  assert.equal(getPageActivity('error', true, false, 'payload_too_large').detailKey, 'status.failurePayloadTooLarge');
  assert.equal(failureReason('provider_content_filtered').key, 'status.failureFiltered');
  assert.equal(failureReason('provider_stream_too_long').key, 'status.failureStreamTooLong');
  assert.equal(failureReason('provider_generation_incomplete').key, 'status.failureIncomplete');
  assert.deepEqual(failureReason('something_new').params, { code: 'something_new' });
  // A background request stopped by the weekly allowance says where to resume it.
  for (const code of ['autonomous_weekly_requests_exhausted', 'autonomous_weekly_requests_paused']) {
    assert.equal(failureReason(code).key, 'status.failureAllowance');
    assert.match(failureReason(code).text, /Open Activity/);
  }
  assert.equal(failureReason('autonomous_request_source_unverified').key, 'status.failureSourceUnverified');
  const page = getPageActivity('error', true, false, 'provider_http_401');
  assert.equal(page.labelKey, 'status.interrupted');
  assert.equal(page.detailKey, 'status.failureAuth');
  assert.equal(getPageActivity('error', true).detailKey, 'status.interruptedDetail');
});
