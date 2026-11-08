// Exercise the operator-facing LAN template without storing or sending credentials.
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { providerSetupCommand } from '../src/providerSetup.ts';

test('LAN setup is explicit in POSIX and PowerShell and never copies a key into the browser', () => {
  const posix = providerSetupCommand('posix', 'lan');
  const powershell = providerSetupCommand('powershell', 'lan');
  assert.match(posix, /P_PULSE_ALLOW_PRIVATE_HTTP='1'/);
  assert.match(posix, /P_PULSE_LOCAL_NO_AUTH='1'/);
  assert.match(posix, /192\.168\.1\.24:11434\/v1\/chat\/completions/);
  assert.match(powershell, /\$env:P_PULSE_ALLOW_PRIVATE_HTTP = '1'/);
  assert.match(powershell, /\$env:P_PULSE_LOCAL_NO_AUTH = '1'/);
  assert.ok(!posix.includes('P_PULSE_API_KEY'));
  assert.ok(!powershell.includes('P_PULSE_API_KEY'));
  assert.equal(posix.split('\n').at(-2), 'per-pulse doctor');
  assert.equal(posix.split('\n').at(-1), 'per-pulse');
});

test('keyed, Azure and loopback examples explicitly turn LAN permission off', () => {
  for (const mode of ['keyed', 'azure', 'loopback']) {
    const command = providerSetupCommand('posix', mode);
    assert.match(command, /P_PULSE_ALLOW_PRIVATE_HTTP='0'/);
  }
  assert.match(providerSetupCommand('posix', 'loopback'), /P_PULSE_ALLOW_LOCAL_HTTP='1'/);
});
