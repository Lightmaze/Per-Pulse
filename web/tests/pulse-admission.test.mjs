import assert from 'node:assert/strict';
import test from 'node:test';
import { pulseAdmissionHold } from '../src/pulseAdmission.ts';
import { continuityEnglish, continuityChinese } from '../src/locales/continuity.ts';

function state(changes = {}) {
  return {
    enabled: true,
    paused: false,
    running: true,
    providerConfigured: true,
    budget: { limit: 2, used: 0, remaining: 2, windowSeconds: 3600, unit: 'admissions' },
    providerTokenBudget: { limit: 90, remaining: 90, reservationPerAdmission: 60 },
    queue: { queued: 1, running: 1, oldestQueuedAt: '2026-10-27T12:00:00Z' },
    ...changes,
  };
}

test('hourly count exhaustion is visible even while earlier local work is running', () => {
  const pulse = state({ budget: { limit: 2, used: 2, remaining: 0, windowSeconds: 3600 } });
  assert.equal(pulseAdmissionHold(pulse), 'hourly_count');
  assert.equal(pulse.running, true);
  assert.equal(pulse.queue.queued, 1);
});

test('insufficient provider token reservation is distinct from a hard spending cap', () => {
  assert.equal(pulseAdmissionHold(state({
    providerTokenBudget: { limit: 90, remaining: 59, reservationPerAdmission: 60 },
  })), 'token_reservation');
  assert.equal(pulseAdmissionHold(state({
    providerTokenBudget: { limit: 90, remaining: 60, reservationPerAdmission: 60 },
  })), null);
  assert.equal(pulseAdmissionHold(state({ providerTokenBudget: null })), null);
});

test('explicit pause, disconnected model or disabled profile take precedence', () => {
  const exhausted = {
    budget: { limit: 0, used: 0, remaining: 0, windowSeconds: 3600 },
    providerTokenBudget: { limit: 60, remaining: 0, reservationPerAdmission: 60 },
  };
  assert.equal(pulseAdmissionHold(state({ ...exhausted, paused: true })), null);
  assert.equal(pulseAdmissionHold(state({ ...exhausted, providerConfigured: false })), null);
  assert.equal(pulseAdmissionHold(state({ ...exhausted, enabled: false })), null);
  assert.equal(pulseAdmissionHold(state(exhausted)), 'hourly_count');
});

test('the English and Chinese notices preserve foreground and queued-work distinctions', () => {
  assert.match(continuityEnglish['pulse.admissionCountHeld'], /queued work may continue/);
  assert.match(continuityEnglish['pulse.admissionTokensHeld'], /foreground writing is unaffected/);
  assert.match(continuityChinese['pulse.admissionCountHeld'], /已排队的工作仍可能继续/);
  assert.match(continuityChinese['pulse.admissionTokensHeld'], /前台输入不受影响/);
});
