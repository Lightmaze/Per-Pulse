// Copyright 2026 Lightmaze Contributors. Licensed under Apache-2.0.
import test from 'node:test';
import assert from 'node:assert/strict';
import { conditionIssue, replaceCondition } from '../src/lessonConditions.ts';

test('editing a nested exception preserves alternative branches and the original draft', () => {
  const before = { all: ['Interactive practice', { any: ['First visit', { not: 'Booking required' }] }] };
  const snapshot = structuredClone(before);
  const after = replaceCondition(before, [1, 1, 0], 'Booking is not yet confirmed');
  assert.deepEqual(before, snapshot);
  assert.deepEqual(after, { all: ['Interactive practice', { any: ['First visit', { not: 'Booking is not yet confirmed' }] }] });
  assert.equal(conditionIssue(after), null);
});

test('blank and multibyte conditions cannot silently pass the native text budget', () => {
  assert.equal(conditionIssue({ all: ['Valid', '  '] }), 'empty');
  assert.equal(conditionIssue('界'.repeat(333)), null);
  assert.equal(conditionIssue('界'.repeat(334)), 'long');
  assert.equal(conditionIssue({ all: Array(10).fill('a'.repeat(900)) }), 'long');
});

test('native tree bounds apply to authored groups without flattening them', () => {
  let allowed = 'A';
  for (let i = 0; i < 6; i++) allowed = { not: allowed };
  assert.equal(conditionIssue(allowed), null);
  assert.equal(conditionIssue({ not: allowed }), 'complex');
  assert.equal(conditionIssue({ any: Array(13).fill('A') }), 'complex');
  assert.equal(conditionIssue({ all: Array(3).fill({ any: Array(10).fill('A') }) }), 'complex');
  assert.equal(conditionIssue({ all: [] }), 'complex');
});
