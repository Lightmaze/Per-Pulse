import assert from 'node:assert/strict';
import test from 'node:test';
import { initialWorkHandoffExcerpt, isExactWorkHandoffExcerpt } from '../src/workHandoffExcerpt.ts';

const source = 'Private first paragraph.\n\n**Bold** finding and a short closing result.';

test('no selection explicitly offers the full settled source for review', () => {
  assert.deepEqual(initialWorkHandoffExcerpt(source, ''), {
    excerpt: source, needsExactExcerpt: false,
  });
});

test('an exact source substring remains the only selected transfer', () => {
  assert.deepEqual(initialWorkHandoffExcerpt(source, '  **Bold** finding  '), {
    excerpt: '**Bold** finding', needsExactExcerpt: false,
  });
});

test('a nonliteral rendered Markdown selection must never silently expand to the entire source', () => {
  const choice = initialWorkHandoffExcerpt(source, 'Bold finding and a short closing result.');
  assert.deepEqual(choice, { excerpt: '', needsExactExcerpt: true });
  assert.equal(isExactWorkHandoffExcerpt(source, choice.excerpt), false);
});

test('manual corrections are allowed only when literally present in the source', () => {
  assert.equal(isExactWorkHandoffExcerpt(source, 'Bold finding'), false);
  assert.equal(isExactWorkHandoffExcerpt(source, '**Bold** finding'), true);
  assert.equal(isExactWorkHandoffExcerpt(source, '  '), false);
  assert.equal(isExactWorkHandoffExcerpt(source, 'Other Work private content'), false);
});
