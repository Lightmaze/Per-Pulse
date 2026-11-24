// Copyright 2026 Lightmaze Contributors. Licensed under Apache-2.0.
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { retryMessageInput } from '../src/retryMessage.ts';

const reference = (id, label, messageId = 'original', kind = 'reference') => ({
  id: 'ref-' + id, sourcePageId: 'work-b', sourceMessageId: messageId,
  targetPageId: id, label, kind, createdAt: '2026-11-03T00:00:00Z',
});
const message = (changes = {}) => ({
  id: 'original', role: 'user', content: 'Revise Source Work using the actual notes.',
  createdAt: '2026-11-03T00:00:00Z', status: 'error',
  sourcePageId: null, sourceMessageId: null,
  references: [reference('work-a', 'Source Work')], ...changes,
});

test('a failed user turn keeps its native linked Work when sent again', () => {
  assert.deepEqual(retryMessageInput(message()), {
    content: 'Revise Source Work using the actual notes.',
    references: [{ targetPageId: 'work-a', label: 'Source Work' }],
    materials: [],
  });
});

test('a verified material footer is removed once before native reattachment', () => {
  const footer = '\n\n[Attached workspace materials]\n{"files":[{"id":"abc"}]}';
  const original = message({
    content: 'Revise Source Work.\n中文' + footer,
    materialSuffixLength: footer.length,
    materials: [{ id: 'abcdef', name: 'notes.md' }],
  });
  assert.deepEqual(retryMessageInput(original), {
    content: 'Revise Source Work.\n中文',
    references: [{ targetPageId: 'work-a', label: 'Source Work' }],
    materials: ['abcdef'],
  });
});

test('does not borrow unrelated references attached later or absent from the message', () => {
  const original = message({ references: [
    reference('work-a', 'Source Work'),
    reference('work-extra', 'Not in the text'),
    reference('work-c', 'Source Work', 'another-message'),
    reference('work-d', 'Source Work', 'original', 'return'),
  ] });
  assert.deepEqual(retryMessageInput(original)?.references, [{ targetPageId: 'work-a', label: 'Source Work' }]);
});

test('a native transfer, a Home return, replay or assistant message cannot be resent anonymously', () => {
  for (const changes of [
    { sourcePageId: 'work-a', sourceMessageId: 'settled-source' },
    { sourcePageId: 'work-b', sourceMessageId: 'settled-source' },
    { replay: { contentOffset: 12 } },
    { role: 'assistant' },
    { role: 'system' },
    { content: ' ', references: [] },
  ]) assert.equal(retryMessageInput(message(changes)), null);
});

test('the material-only message remains retryable with no invented text', () => {
  assert.deepEqual(retryMessageInput(message({
    content: '\n\n[Attached workspace materials]',
    materialSuffixLength: '\n\n[Attached workspace materials]'.length,
    references: [], materials: [{ id: 'abcdef', name: 'notes.md' }],
  })), { content: '', references: [], materials: ['abcdef'] });
});
