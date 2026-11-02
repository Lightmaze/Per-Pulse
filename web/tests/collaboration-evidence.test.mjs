// Copyright 2026 Lightmaze Contributors. Licensed under Apache-2.0.
import test from 'node:test';
import assert from 'node:assert/strict';
import { historicSourceRating } from '../src/collaborationEvidence.ts';

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
