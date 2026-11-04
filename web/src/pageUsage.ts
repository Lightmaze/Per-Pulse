import type { Locale } from './language';

/** Tokens the model service reported, never an estimate. */
export type TokenUsage = { inputTokens: number; outputTokens: number; cachedTokens: number; unreportedRounds: number };
export type PageUsage = TokenUsage & { lastTurn: TokenUsage | null };

export function totalTokens(usage: TokenUsage): number {
  return usage.inputTokens + usage.outputTokens;
}

/** Show usage only once the model service has reported some for this page. */
export function hasUsage(usage: PageUsage | null | undefined): usage is PageUsage {
  return Boolean(usage && totalTokens(usage) > 0);
}

export function compactTokens(value: number, locale: Locale): string {
  return new Intl.NumberFormat(locale, { notation: 'compact', maximumFractionDigits: 1 }).format(value);
}

export function exactTokens(value: number, locale: Locale): string {
  return new Intl.NumberFormat(locale).format(value);
}
