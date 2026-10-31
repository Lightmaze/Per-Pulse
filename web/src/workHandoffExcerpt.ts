// Copyright 2026 Lightmaze Contributors. Licensed under Apache-2.0.

/** Never expand an attempted text selection into an entire private Work result.
 * Rendered Markdown may join or reformat characters that are not contiguous in
 * the original source. The user must inspect a verbatim passage instead.
 */
export function initialWorkHandoffExcerpt(sourceText: string, renderedSelection: string): {
  excerpt: string; needsExactExcerpt: boolean;
} {
  const selected = renderedSelection.trim();
  if (!selected) return { excerpt: sourceText, needsExactExcerpt: false };
  if (sourceText.includes(selected)) return { excerpt: selected, needsExactExcerpt: false };
  return { excerpt: '', needsExactExcerpt: true };
}

export function isExactWorkHandoffExcerpt(sourceText: string, excerpt: string): boolean {
  return Boolean(excerpt.trim()) && sourceText.includes(excerpt);
}
