// Copyright 2026 Lightmaze Contributors. Licensed under Apache-2.0.
import test from 'node:test';
import assert from 'node:assert/strict';
import { historicSourceRating, visibleRatedSourceWork } from '../src/collaborationEvidence.ts';

test('a later Work reads the three exact original native RIM outcome proxies', () => {
  assert.equal(historicSourceRating(1), 'adopted');
  assert.equal(historicSourceRating(0), 'revised');
  assert.equal(historicSourceRating(-1), 'discarded');
});

test('partial scores and untrusted values never invent an original operator rating', () => {
  for (const value of [undefined, null, '1', 0.5, -0.5, 2, NaN, Infinity, {}, true]) {
    assert.equal(historicSourceRating(value), null);
  }
});


test('historical source links require the exact original visible Work and message', () => {
  const pages = [{ id: 'home', kind: 'home' }, { id: 'work-a', kind: 'work' }, { id: 'work-b', kind: 'work' }];
  assert.equal(visibleRatedSourceWork(pages, 'work-a', 'result-1')?.id, 'work-a');
  for (const [workId, messageId] of [[null, 'result-1'], ['missing', 'result-1'], ['home', 'result-1'], ['work-a', null], ['', 'result-1'], ['work-a', '']]) {
    assert.equal(visibleRatedSourceWork(pages, workId, messageId), null);
  }
});
