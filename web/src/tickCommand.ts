/** `/tick [N]` and `/tick stop` typed into the composer. */
export const TICK_LIMIT = 256;

export type TickCommand =
  | { kind: 'start'; count: number }
  | { kind: 'stop' }
  | { kind: 'invalid' };

/** Return a command only when the whole message is a /tick command. */
export function parseTickCommand(text: string): TickCommand | null {
  const match = /^\/tick(?:\s+(\S+))?$/i.exec(text.trim());
  if (!match) return null;
  const argument = match[1];
  if (argument === undefined) return { kind: 'start', count: 1 };
  if (argument.toLowerCase() === 'stop') return { kind: 'stop' };
  if (!/^\d{1,3}$/.test(argument)) return { kind: 'invalid' };
  const count = Number(argument);
  return count >= 1 && count <= TICK_LIMIT ? { kind: 'start', count } : { kind: 'invalid' };
}
