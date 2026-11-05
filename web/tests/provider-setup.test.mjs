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
    const rendered = providerSetupCommand(shell);
    const lines = rendered.split('\n');
    assert.equal(lines.length, 7);
    assert.ok(rendered.includes(shell === 'powershell'
      ? "$env:P_PULSE_CHAT_AUTH_STYLE = 'bearer'"
      : "export P_PULSE_CHAT_AUTH_STYLE='bearer'"), 'remote mode resets a prior Azure header');
    assert.ok(command.includes('P_PULSE_LOCAL_NO_AUTH'), 'keyed mode disables local no-auth carryover');
    assert.ok(lines.every(line => !line.includes('sk-')), 'Templates never contain real credentials');
  }
});

test('explicit local-model mode offers both terminal types without a credential', () => {
  for (const shell of ['posix', 'powershell']) {
    const command = providerSetupCommand(shell, 'loopback');
    assert.ok(command.includes('127.0.0.1:11434/v1/chat/completions'));
    assert.ok(command.includes('P_PULSE_LOCAL_NO_AUTH'));
    assert.ok(command.includes('P_PULSE_ALLOW_LOCAL_HTTP'));
    assert.ok(!command.includes('P_PULSE_API_KEY'), 'local setup never asks for or embeds a credential');
    assert.ok(!command.includes('your-api-key'));
    assert.ok(command.endsWith('per-pulse doctor\nper-pulse'));
    const lines = command.split('\n');
    assert.equal(lines.length, 8);
    assert.ok(command.includes(shell === 'powershell'
      ? "$env:P_PULSE_CHAT_AUTH_STYLE = 'bearer'"
      : "export P_PULSE_CHAT_AUTH_STYLE='bearer'"), 'local mode resets a prior Azure header');
    assert.ok(command.includes('P_PULSE_PROVIDER_NAMESPACE'), 'local mode clears an inherited remote namespace');
    if (shell === 'powershell') {
      assert.match(command, /^\$env:P_PULSE_CHAT_ENDPOINT/);
      assert.ok(!command.includes('export '));
    } else {
      assert.match(command, /^export P_PULSE_CHAT_ENDPOINT/);
      assert.ok(!command.includes('$env:'));
    }
  }
});

test('Azure API-key setup is available in POSIX and PowerShell without a real credential', () => {
  for (const shell of ['posix', 'powershell']) {
    const command = providerSetupCommand(shell, 'azure');
    assert.ok(command.includes('your-resource.openai.azure.com/openai/deployments/your-deployment/chat/completions?api-version=2024-10-21'));
    assert.ok(command.includes('your-deployment'));
    assert.ok(command.includes('your-azure-api-key'));
    assert.ok(command.includes(shell === 'powershell'
      ? "$env:P_PULSE_CHAT_AUTH_STYLE = 'api-key'"
      : "export P_PULSE_CHAT_AUTH_STYLE='api-key'"));
    assert.ok(command.includes('P_PULSE_LOCAL_NO_AUTH'));
    assert.ok(command.includes('P_PULSE_PROVIDER_NAMESPACE'));
    assert.ok(command.endsWith('per-pulse doctor\nper-pulse'));
    assert.equal(command.split('\n').length, 8);
    assert.ok(!command.includes('sk-'), 'no real provider credentials in the template');
  }
});
