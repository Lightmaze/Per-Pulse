/** Local terminal instructions only; never read or transport provider credentials.
 * Copyright 2026 Lightmaze Contributors. Licensed under Apache-2.0.
 */
export type ProviderShell = 'posix' | 'powershell';

const exampleValues = [
  ['P_PULSE_CHAT_ENDPOINT', 'https://your-provider.example/v1/chat/completions'],
  ['P_PULSE_CHAT_MODEL', 'your-model-id'],
  ['P_PULSE_API_KEY', 'your-api-key'],
] as const;

/** A user-editable terminal template, not a request to send secrets to the UI. */
export function providerSetupCommand(shell: ProviderShell): string {
  const lines = exampleValues.map(([name, value]) => shell === 'powershell'
    ? `$env:${name} = '${value}'`
    : `export ${name}='${value}'`);
  // doctor is strictly a local configuration preflight, not a remote provider test.
  return [...lines, 'per-pulse doctor', 'per-pulse'].join('\n');
}
