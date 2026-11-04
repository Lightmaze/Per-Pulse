// Copyright 2026 Lightmaze Contributors. Licensed under Apache-2.0.
import type { DraftReference, Message } from './types';
import { messageBody } from './messageBody.ts';

export type RetryInput = { content: string; references: DraftReference[]; materials: string[] };

/** Only resend an ordinary user input. A native Work transfer/return has a
 * causal source anchor that plain /messages cannot recreate; offering Send
 * again for it would quietly turn sourced work into anonymous copied text.
 */
export function retryMessageInput(message: Message): RetryInput | null {
  if (message.role !== 'user' || message.sourcePageId || message.replay) return null;
  // The native message carries a verified material envelope appended by the
  // server. Resending it raw would append the same envelope a second time.
  const content = messageBody(message);
  const materials = message.materials?.map(material => material.id) ?? [];
  if (!content.trim() && !materials.length) return null;
  return {
    content,
    references: message.references
      .filter(ref => ref.kind === 'reference' && ref.sourceMessageId === message.id && content.includes(ref.label))
      .map(ref => ({ targetPageId: ref.targetPageId, label: ref.label })),
    materials,
  };
}
