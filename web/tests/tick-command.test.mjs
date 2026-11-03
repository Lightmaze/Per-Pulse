import assert from 'node:assert/strict';
import test from 'node:test';
import { parseTickCommand } from '../src/tickCommand.ts';

test('/tick alone gives one time step', () => {
  assert.deepEqual(parseTickCommand('/tick'), { kind: 'start', count: 1 });
  assert.deepEqual(parseTickCommand('  /tick  '), { kind: 'start', count: 1 });
});

test('/tick N gives exactly N time steps within the limit', () => {
  assert.deepEqual(parseTickCommand('/tick 3'), { kind: 'start', count: 3 });
  assert.deepEqual(parseTickCommand('/tick 256'), { kind: 'start', count: 256 });
  assert.deepEqual(parseTickCommand('/tick 0'), { kind: 'invalid' });
  assert.deepEqual(parseTickCommand('/tick 257'), { kind: 'invalid' });
  assert.deepEqual(parseTickCommand('/tick -1'), { kind: 'invalid' });
  assert.deepEqual(parseTickCommand('/tick 2.5'), { kind: 'invalid' });
});

test('/tick stop ends a run', () => {
  assert.deepEqual(parseTickCommand('/tick stop'), { kind: 'stop' });
  assert.deepEqual(parseTickCommand('/TICK Stop'), { kind: 'stop' });
});

test('ordinary messages are never commands', () => {
  assert.equal(parseTickCommand('tick'), null);
  assert.equal(parseTickCommand('Please /tick 3'), null);
  assert.equal(parseTickCommand('/tick 3 and summarize'), null);
  assert.equal(parseTickCommand('/ticket'), null);
});
