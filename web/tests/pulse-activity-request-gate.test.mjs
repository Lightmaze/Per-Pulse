import assert from 'node:assert/strict';
import test from 'node:test';
import { PulseActivityRequestGate } from '../src/pulseActivityRequestGate.ts';

test('an old poll cannot overwrite a successful operator pause/hold', () => {
  const gate = new PulseActivityRequestGate();
  const beforePatch = gate.beginRead();
  assert.ok(beforePatch);
  assert.equal(gate.beginWrite(), true);
  assert.equal(gate.acceptsRead(beforePatch), false);
  assert.equal(gate.beginRead(), null, 'do not poll during the PATCH');
  gate.finishWrite();
  assert.equal(gate.acceptsRead(beforePatch), false, 'late pre-PATCH GET stays stale');
  const confirmed = gate.beginRead();
  assert.ok(confirmed);
  assert.equal(gate.acceptsRead(confirmed), true);
});

test('latest GET wins when out-of-order polling results arrive', () => {
  const gate = new PulseActivityRequestGate();
  const slow = gate.beginRead();
  const fast = gate.beginRead();
  assert.ok(slow && fast);
  assert.equal(gate.acceptsRead(slow), false);
  assert.equal(gate.acceptsRead(fast), true);
});

test('write gate blocks duplicate commands and releases after an uncertain failure', () => {
  const gate = new PulseActivityRequestGate();
  assert.equal(gate.beginWrite(), true);
  assert.equal(gate.beginWrite(), false, 'do not submit a second hold/release');
  gate.finishWrite(); // finally runs on success or failure
  assert.equal(gate.beginWrite(), true);
  gate.finishWrite();
  const reread = gate.beginRead();
  assert.ok(reread);
  assert.equal(gate.acceptsRead(reread), true, 'read back authoritative state');
});

test('finishing an inactive write does not invalidate a valid read', () => {
  const gate = new PulseActivityRequestGate();
  const read = gate.beginRead();
  assert.ok(read);
  gate.finishWrite();
  assert.equal(gate.acceptsRead(read), true);
});
