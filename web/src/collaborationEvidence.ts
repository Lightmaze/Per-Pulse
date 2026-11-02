// Copyright 2026 Lightmaze Contributors. Licensed under Apache-2.0.
import type { CollaboratorOutcome } from './types';

/** Decode only the native persisted RIM adoption proxies (not a quality score). */
export function historicSourceRating(value: unknown): CollaboratorOutcome | null {
  if (typeof value !== 'number' || !Number.isFinite(value)) return null;
  if (value === 1) return 'adopted';
  if (value === 0) return 'revised';
  if (value === -1) return 'discarded';
  return null;
}
