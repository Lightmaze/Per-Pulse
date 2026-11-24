import assert from 'node:assert/strict';
import test from 'node:test';
import { ExecutionResponseGate } from '../src/executionResponseGate.ts';

test('a late Allow response cannot overwrite a newer Stop result', () => {
  const gate = new ExecutionResponseGate('work-a');
  const allow = gate.begin('work-a');
  const stop = gate.begin('work-a');
  assert.ok(allow !== null && stop !== null);
  assert.equal(gate.accepts('work-a', stop), true);
  assert.equal(gate.accepts('work-a', allow), false);
});

test('navigation fences old-page action and recovery responses even on returning', () => {
  const gate = new ExecutionResponseGate('work-a');
  const old = gate.begin('work-a');
  assert.ok(old !== null);
  gate.forPage('work-b');
  assert.equal(gate.accepts('work-a', old), false);
  assert.equal(gate.begin('work-a'), null);
  const other = gate.begin('work-b');
  assert.ok(other !== null);
  gate.forPage('work-a');
  assert.equal(gate.accepts('work-b', other), false);
  assert.equal(gate.accepts('work-a', old), false);
});

test('uncertain action recovery must not apply after a newer decision', () => {
  const gate = new ExecutionResponseGate('home');
  const failedAction = gate.begin('home');
  assert.ok(failedAction !== null);
  // A GET started because the action failed, then a new command begins.
  const newerAction = gate.begin('home');
  assert.ok(newerAction !== null);
  assert.equal(gate.accepts('home', failedAction), false);
  assert.equal(gate.accepts('home', newerAction), true);
});

test('unmount or explicit invalidation fences late responses', () => {
  const gate = new ExecutionResponseGate('work');
  const ticket = gate.begin('work');
  assert.ok(ticket !== null);
  gate.invalidate();
  assert.equal(gate.accepts('work', ticket), false);
});
