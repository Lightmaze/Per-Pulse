// Copyright 2026 Lightmaze Contributors. Licensed under Apache-2.0.
import type { Message } from './types';
import { messageBody } from './messageBody.ts';

/** A selected message supplies evidence, never silently becomes the new request. */
export function initialPageSource(message?: Message): { excerpt: string; instruction: string } {
  return message ? { excerpt: messageBody(message), instruction: 'Continue from this source.' }
    : { excerpt: '', instruction: '' };
}
