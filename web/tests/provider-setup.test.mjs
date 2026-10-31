import assert from 'node:assert/strict';
import test from 'node:test';
import { providerSetupCommand } from '../src/providerSetup.ts';

test('Windows PowerShell setup stays in one terminal and checks config before launch', () => {
  const command = providerSetupCommand('powershell');
  assert.match(command, /^\$env:P_PULSE_CHAT_ENDPOINT = 'https:\/\/your-provider\.example\/v1\/chat\/completions'/);
  assert.match(command, /\$env:P_PULSE_CHAT_MODEL = 'your-model-id'/);
  assert.match(command, /\$env:P_PULSE_API_KEY = 'your-api-key'/);
  assert.ok(command.endsWith('per-pulse doctor\nper-pulse'));
  assert.ok(!command.includes('export P_PULSE_'), 'PowerShell must not receive POSIX exports');
});

test('POSIX terminal setup uses exports and never embeds a real secret', () => {
  const command = providerSetupCommand('posix');
  assert.match(command, /^export P_PULSE_CHAT_ENDPOINT='https:\/\/your-provider\.example\/v1\/chat\/completions'/);
  assert.match(command, /export P_PULSE_CHAT_MODEL='your-model-id'/);
  assert.match(command, /export P_PULSE_API_KEY='your-api-key'/);
  assert.ok(command.endsWith('per-pulse doctor\nper-pulse'));
  assert.ok(!command.includes('$env:'), 'POSIX must not receive PowerShell syntax');
  for (const shell of ['posix', 'powershell']) {
    const lines = providerSetupCommand(shell).split('\n');
    assert.equal(lines.length, 5);
    assert.ok(lines.every(line => !line.includes('sk-')), 'Templates never contain real credentials');
  }
});
