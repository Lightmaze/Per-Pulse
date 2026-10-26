import assert from 'node:assert/strict';
import test from 'node:test';
import { attentionSourceTarget, localRetainedAttention } from '../src/pulseAttention.ts';

const entry = (overrides = {}) => ({
  center_id: 'origin-center', pageId: 'work-source', note: 'Check the actual interaction',
  drive: 0.8, revision: 1, sourceTurnId: 'turn-a', sourceActionId: 'action-a',
  sourceEventId: 'source-message-a', sourcePageId: 'work-source',
  sourceMessageId: 'source-message-a', operatorEditedAt: null, ...overrides,
});

test('retained notes remain scoped to the opened page, not visible in sibling Works or Home', () => {
  const pulse = { retainedAttention: [
    entry(), entry({ center_id: null, pageId: 'home', note: 'Home-only' }),
    entry({ center_id: 'missing-work', pageId: null, note: 'Hidden older Work' }),
  ] };
  assert.deepEqual(localRetainedAttention(pulse, 'work-source').map(x => x.note),
    ['Check the actual interaction']);
  assert.deepEqual(localRetainedAttention(pulse, 'home').map(x => x.note), ['Home-only']);
  assert.deepEqual(localRetainedAttention(pulse, 'other-work'), []);
  assert.deepEqual(localRetainedAttention(null, 'home'), []);
});

test('only the original validated event can be a source message deep link', () => {
  const available = new Set(['home', 'work-source']);
  assert.deepEqual(attentionSourceTarget(entry(), available),
    { pageId: 'work-source', messageId: 'source-message-a' });
  assert.deepEqual(attentionSourceTarget(entry({
    sourceMessageId: 'different-event',
  }), available), { pageId: 'work-source' });
  assert.deepEqual(attentionSourceTarget(entry({
    sourceMessageId: null,
  }), available), { pageId: 'work-source' });
  assert.equal(attentionSourceTarget(entry({
    sourcePageId: null, sourceMessageId: null,
  }), available), null);
  assert.equal(attentionSourceTarget(entry({
    sourcePageId: 'missing-work',
  }), available), null);
  assert.equal(attentionSourceTarget(entry({
    sourcePageId: 'home', sourceMessageId: null,
  }), new Set(['work-source'])), null);
});
