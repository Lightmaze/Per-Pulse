import { test } from 'node:test';
import assert from 'node:assert/strict';
import { otherPendingPulseContexts, pulseQueueForPage } from '../src/pulseQueue.ts';

test('native queue counts remain local even when global recent events omit that Work', () => {
  const source = { queueByContext: [
    { center_id: 'b-center', pageId: 'b', queued: 3, running: 0, oldestQueuedAt: '2026-10-28T12:00:00Z' },
    { center_id: 'a-center', pageId: 'a', queued: 0, running: 2, oldestQueuedAt: null },
    { center_id: 'old-center', pageId: null, queued: 1, running: 0, oldestQueuedAt: '2026-10-28T11:00:00Z' },
  ], recentActivations: [] };
  assert.equal(pulseQueueForPage(source, 'b').queued, 3);
  assert.equal(pulseQueueForPage(source, 'a').running, 2);
  assert.equal(pulseQueueForPage(source, 'missing'), null);
  assert.deepEqual(otherPendingPulseContexts(source, 'b').map(entry => entry.center_id),
    ['a-center', 'old-center']);
});

test('older backend response and no queue do not invent local work', () => {
  assert.equal(pulseQueueForPage(null, 'home'), null);
  assert.deepEqual(otherPendingPulseContexts({}, 'home'), []);
  assert.deepEqual(otherPendingPulseContexts({
    queueByContext: [{ pageId: 'home', center_id: null, queued: 0, running: 0, oldestQueuedAt: null }],
  }, 'other'), []);
});
