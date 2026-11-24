/** Browser preferences are presentation state, never conversation state. */
export type Locale = 'en' | 'zh-CN';
export type LanguagePreference = 'system' | Locale;
export const LANGUAGE_STORAGE_KEY = 'per-pulse.ui.language.v1';

export function languagePreference(value: unknown): LanguagePreference {
  return value === 'en' || value === 'zh-CN' ? value : 'system';
}

export function resolveLocale(preference: LanguagePreference, languages: readonly string[]): Locale {
  if (preference !== 'system') return preference;
  // Use the user's first supported language, not the presence of Chinese
  // anywhere in a browser's lower-priority fallback list.
  for (const language of languages) {
    if (/^zh(?:-|$)/i.test(language)) return 'zh-CN';
    if (/^en(?:-|$)/i.test(language)) return 'en';
  }
  return 'en';
}

export function readLanguagePreference(): LanguagePreference {
  try { return languagePreference(localStorage.getItem(LANGUAGE_STORAGE_KEY)); }
  catch { return 'system'; }
}

export function saveLanguagePreference(preference: LanguagePreference): boolean {
  try {
    if (preference === 'system') localStorage.removeItem(LANGUAGE_STORAGE_KEY);
    else localStorage.setItem(LANGUAGE_STORAGE_KEY, preference);
    return true;
  } catch { return false; }
}

export function interpolate(message: string, values: Record<string, string | number> = {}): string {
  // A callback keeps dollar sequences and user-provided braces literal.
  return message.replace(/\{(\w+)\}/g, (placeholder, name: string) => Object.prototype.hasOwnProperty.call(values, name) ? String(values[name]) : placeholder);
}
