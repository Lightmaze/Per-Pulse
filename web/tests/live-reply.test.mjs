import assert from 'node:assert/strict';
import test from 'node:test';
import { applyLiveReplyEvent } from '../src/liveReply.ts';
import { workspaceEnglish, workspaceChinese } from '../src/locales/workspace.ts';

test('a reply grows by appended text within the same model round', () => {
  let reply = applyLiveReplyEvent(null, { turnId: 't1', roundId: 'r0', text: 'The first' });
  reply = applyLiveReplyEvent(reply, { turnId: 't1', roundId: 'r0', append: ' half' });
  reply = applyLiveReplyEvent(reply, { turnId: 't1', roundId: 'r0', append: ' and more.' });
  assert.deepEqual(reply, { turnId: 't1', roundId: 'r0', text: 'The first half and more.' });
});

test('a new round or a reconnect replaces the text; stray appends are ignored', () => {
  const first = applyLiveReplyEvent(null, { turnId: 't1', roundId: 'r0', text: 'Reading the notes.' });
  assert.equal(applyLiveReplyEvent(first, { turnId: 't1', roundId: 'r1', append: 'x' }), first);
  assert.equal(applyLiveReplyEvent(first, { turnId: 't2', roundId: 'r0', append: 'x' }), first);
  assert.equal(applyLiveReplyEvent(null, { turnId: 't1', roundId: 'r0', append: 'x' }), null);
  assert.deepEqual(applyLiveReplyEvent(first, { turnId: 't1', roundId: 'r1', text: '' }), { turnId: 't1', roundId: 'r1', text: '' });
});

test('the last text stays while the finished reply is being saved', () => {
  const reply = { turnId: 't1', roundId: 'r0', text: 'Almost done' };
  assert.equal(applyLiveReplyEvent(reply, { turnId: null }), reply);
});

test('the live reply has an English and Chinese label', () => {
  assert.equal(workspaceEnglish['conversation.replyInProgress'], 'Reply being written');
  assert.equal(workspaceChinese['conversation.replyInProgress'], '正在写的回复');
});
