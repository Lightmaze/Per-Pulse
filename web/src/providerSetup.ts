/** Local terminal instructions only; never read or transport provider credentials.
 * Copyright 2026 Lightmaze Contributors. Licensed under Apache-2.0.
 */
export type ProviderShell = 'posix' | 'powershell';
export type ProviderMode = 'keyed' | 'loopback';

const exampleValues = [
  ['P_PULSE_CHAT_ENDPOINT', 'https://your-provider.example/v1/chat/completions'],
  ['P_PULSE_CHAT_MODEL', 'your-model-id'],
  ['P_PULSE_API_KEY', 'your-api-key'],
] as const;

const loopbackValues = [
  ['P_PULSE_CHAT_ENDPOINT', 'http://127.0.0.1:11434/v1/chat/completions'],
  ['P_PULSE_CHAT_MODEL', 'your-local-model-id'],
  ['P_PULSE_ALLOW_LOCAL_HTTP', '1'],
  ['P_PULSE_LOCAL_NO_AUTH', '1'],
] as const;

/** A user-editable terminal template, not a request to send secrets to the UI. */
export function providerSetupCommand(shell: ProviderShell, mode: ProviderMode = 'keyed'): string {
  const values: readonly (readonly [string, string])[] =
    mode === 'loopback' ? loopbackValues : exampleValues;
  const lines = values.map(([name, value]) => shell === 'powershell'
    ? `$env:${name} = '${value}'`
    : `export ${name}='${value}'`);
  // doctor is strictly a local configuration preflight, not a remote provider test.
  return [...lines, 'per-pulse doctor', 'per-pulse'].join('\n');
}
