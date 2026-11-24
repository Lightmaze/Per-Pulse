// Copyright 2026 Lightmaze Contributors. Licensed under Apache-2.0.
import type { Message } from './types';

/** Display only prefixes/suffixes whose native provenance the server verified.
 * Original raw copying and stored history retain both source envelopes.
 */
export function messageBody(message: Pick<Message, 'content' | 'replay' | 'materialSuffixLength'>): string {
  const start = message.replay?.contentOffset ?? 0;
  const suffix = message.materialSuffixLength ?? 0;
  return message.content.slice(start, suffix > 0 ? -suffix : undefined);
}
