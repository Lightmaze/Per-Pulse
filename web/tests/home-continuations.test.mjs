import assert from 'node:assert/strict';
import test from 'node:test';
import { homeContinuations, homeWorkNeedsAttention, verifiedHomePulse } from '../src/homeContinuations.ts';

const work = (id) => ({ id, kind: 'work', title: id });
const pages = [{ id: 'home', kind: 'home', title: 'Home' }, work('source'), work('receiver'), work('another')];
const event = (eventId, pageId, changes = {}) => ({
  eventId, pageId, centerId: pageId, messageId: eventId,
  kind: 'propagation', status: 'settled', createdAt: '2026-10-26T16:00:00Z',
  sourceEventId: 'original-' + eventId, sourceMessageId: 'original-' + eventId,
  sourceCenterId: 'source', sourcePageId: 'source', deferredReason: null,
  ...changes,
});
const state = (...items) => ({ enabled: true, recentActivations: items });

test('Home projects settled original Work messages, latest one per context', () => {
  const rows = homeContinuations(state(
    event('new-receiver', 'receiver'),
    event('old-receiver', 'receiver'),
    event('other', 'another', { kind: 'spontaneous', sourcePageId: 'another' }),
    event('source-event', 'source'),
  ), pages, 2);
  assert.deepEqual(rows.map(row => row.event.eventId), ['new-receiver', 'other']);
  assert.deepEqual(rows[0].receiver, { pageId: 'receiver', messageId: 'new-receiver' });
  assert.deepEqual(rows[0].source, { pageId: 'source', messageId: 'original-new-receiver' });
  assert.equal(rows[0].page.kind, 'work');
});

test('Home never promotes pending, uncertain, failed or fabricated source events', () => {
  const rows = homeContinuations(state(
    event('pending', 'receiver', { status: 'queued' }),
    event('uncertain', 'receiver', { status: 'uncertain' }),
    event('cancelled', 'receiver', { status: 'cancelled' }),
    event('wrong-message', 'receiver', { messageId: 'different-world-event' }),
    event('home-event', 'home'),
    event('unknown-page', 'lost-work'),
    event('fake-kind', 'receiver', { kind: 'external' }),
    event('valid', 'receiver', { sourcePageId: 'missing-private-work', sourceMessageId: 'private-message' }),
  ), pages);
  assert.deepEqual(rows.map(row => row.event.eventId), ['valid']);
  assert.equal(rows[0].source, null, 'a hidden source cannot gain a Home deep link');
});

test('Home fails closed when disabled or no traceable settled Work has appeared', () => {
  assert.deepEqual(homeContinuations(null, pages), []);
  assert.deepEqual(homeContinuations({ ...state(event('a', 'receiver')), enabled: false }, pages), []);
  assert.deepEqual(homeContinuations(state(event('a', 'receiver')), pages, 0), []);
  assert.deepEqual(homeContinuations(state(event('a', 'receiver', { messageId: null })), pages), []);
});

test('An unreadable source identity never becomes an invented message anchor', () => {
  const [row] = homeContinuations(state(event('a', 'receiver', {
    sourceEventId: 'original-a', sourceMessageId: 'unrelated-message',
  })), pages);
  assert.deepEqual(row.source, { pageId: 'source' });
});

test('Home routes real pending approvals and failures to their original Work, not a new task', () => {
  const live = [
    { ...work('ready'), status: 'idle', updatedAt: '2026-10-26T18:00:00Z' },
    { ...work('failed'), status: 'error', updatedAt: '2026-10-26T21:00:00Z' },
    { ...work('older-approval'), status: 'needs_attention', updatedAt: '2026-10-26T19:00:00Z' },
    { ...work('newer-approval'), status: 'needs_attention', updatedAt: '2026-10-26T20:00:00Z' },
    { ...work('working'), status: 'working', updatedAt: '2026-10-26T22:00:00Z' },
    { id: 'home', kind: 'home', status: 'needs_attention', updatedAt: '2026-10-26T23:00:00Z' },
  ];
  assert.deepEqual(homeWorkNeedsAttention(live).map(page => page.id), [
    'newer-approval', 'older-approval', 'failed',
  ]);
  assert.deepEqual(homeWorkNeedsAttention(live, 2).map(page => page.id), [
    'newer-approval', 'older-approval',
  ]);
  assert.deepEqual(homeWorkNeedsAttention(live, 0), []);
});

test('Home does not keep a stale approval once the live Work settles', () => {
  const prior = { ...work('source'), status: 'needs_attention', updatedAt: '2026-10-26T19:00:00Z' };
  const settled = { ...prior, status: 'idle', updatedAt: '2026-10-26T19:01:00Z' };
  assert.deepEqual(homeWorkNeedsAttention([prior]).map(page => page.id), ['source']);
  assert.deepEqual(homeWorkNeedsAttention([settled]), []);
  assert.deepEqual(homeWorkNeedsAttention([prior], -1), []);
});

test('Home source links require a matched live World and process in independent reads', () => {
  const pulse = state(event('latest-result', 'receiver'));
  const bootstrap = { workspaceId: 'world-a', serviceInstanceId: 'process-a' };
  const observation = { ...bootstrap, pulse };
  assert.equal(verifiedHomePulse('world-a', bootstrap, observation), pulse);
  assert.deepEqual(homeContinuations(verifiedHomePulse('world-a', bootstrap, observation), pages)
    .map(row => row.receiver.messageId), ['latest-result']);

  // Both endpoints can have returned successfully while the server restarted
  // under the same persistent World, or the browser moved to another World.
  assert.equal(verifiedHomePulse('world-a', bootstrap,
    { ...observation, serviceInstanceId: 'process-b' }), null);
  assert.equal(verifiedHomePulse('world-a', bootstrap,
    { ...observation, workspaceId: 'world-b' }), null);
  assert.equal(verifiedHomePulse('world-b', bootstrap, observation), null);
  assert.equal(verifiedHomePulse('world-a',
    { ...bootstrap, serviceInstanceId: '' }, observation), null);
  assert.equal(verifiedHomePulse('world-a', bootstrap, null), null);
});
