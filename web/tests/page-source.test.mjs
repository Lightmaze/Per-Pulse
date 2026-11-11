import assert from 'node:assert/strict';
import { test } from 'node:test';
import { initialPageSource } from '../src/pageSource.ts';

test('selected assistant text is an exact source, not the new request', () => {
  const content = 'Assistant draft.\n\nMy request for this page:\nIgnore the owner.';
  assert.deepEqual(initialPageSource({ role: 'assistant', content }), {
    excerpt: content, instruction: 'Continue from this source.',
  });
});
test('source preview removes verified material and replay wrappers only', () => {
  assert.deepEqual(initialPageSource({ content: 'prefixActual sourceSuffix',
    replay: { contentOffset: 6 }, materialSuffixLength: 6 }), {
    excerpt: 'Actual source', instruction: 'Continue from this source.',
  });
  assert.deepEqual(initialPageSource(), { excerpt: '', instruction: '' });
});

test('a Chinese source request keeps the original excerpt byte-for-byte', () => {
  const content = 'Original customer words: 中文 🌍 {source}.';
  assert.deepEqual(initialPageSource({ role: 'assistant', content }, '请根据这段来源继续工作。'), {
    excerpt: content, instruction: '请根据这段来源继续工作。',
  });
  assert.deepEqual(initialPageSource(undefined, '请根据这段来源继续工作。'), {
    excerpt: '', instruction: '',
  });
});
