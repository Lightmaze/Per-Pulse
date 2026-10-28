import { createContext, useCallback, useContext, useEffect, useLayoutEffect, useMemo, useState, type ReactNode } from 'react';
import { ApiError } from './api';
import { commonEnglish, commonChinese } from './locales/common';
import { workspaceEnglish, workspaceChinese } from './locales/workspace';
import { continuityEnglish, continuityChinese } from './locales/continuity';
import { learningEnglish, learningChinese } from './locales/learning';
import { interpolate, LANGUAGE_STORAGE_KEY, languagePreference, readLanguagePreference, resolveLocale, saveLanguagePreference, type LanguagePreference, type Locale } from './language';

const english = { ...commonEnglish, ...workspaceEnglish, ...continuityEnglish, ...learningEnglish };
const chinese: Record<keyof typeof english, string> = { ...commonChinese, ...workspaceChinese, ...continuityChinese, ...learningChinese };
export type MessageKey = keyof typeof english;
export type Translate = (key: MessageKey, values?: Record<string, string | number>) => string;

const errorKeys: Record<string, MessageKey> = {
  request_failed: 'error.generic',
  network_error: 'error.network', invalid_response: 'error.invalidResponse',
  workspace_load_failed: 'error.workspaceLoad', page_load_failed: 'error.pageLoad',
  page_not_found: 'error.pageNotFound', message_not_found: 'error.messageNotFound',
  invalid_request: 'error.invalidRequest', invalid_response_language: 'error.invalidLanguage',
  origin_not_allowed: 'error.localAddress', json_required: 'error.jsonRequired',
  request_too_large: 'error.requestTooLarge', workspace_mode_mismatch: 'error.workspaceMode',
  home_title_fixed: 'error.homeRename', already_home: 'error.homeReturn',
  not_found: 'error.notFound', frontend_not_built: 'error.frontendBuild',
  experience_revision_conflict: 'learning.revisionConflict',
  experience_not_found: 'learning.notAvailable', posture_disabled: 'learning.disabled',
  artifact_version_changed: 'artifacts.changed', artifact_download_redacted: 'artifacts.downloadRedacted',
  artifact_download_unverified: 'artifacts.downloadUnverified',
  file_too_large: 'artifacts.tooLarge', text_files_only: 'artifacts.nonText',
  credential_content_blocked: 'artifacts.sensitive', file_changed_retry: 'artifacts.retry',
  file_unavailable: 'artifacts.missing', path_unavailable: 'artifacts.missing',
  not_a_single_regular_file: 'artifacts.missing', artifact_not_found: 'artifacts.missing',
};

type LanguageContext = {
  locale: Locale;
  preference: LanguagePreference;
  preferenceSaved: boolean;
  setPreference: (value: LanguagePreference) => void;
  t: Translate;
  pageTitle: (page: { id: string; title: string }) => string;
  errorText: (error: unknown) => string;
};

const Context = createContext<LanguageContext | null>(null);
const browserLanguages = () => navigator.languages?.length ? navigator.languages : [navigator.language];

export function LanguageProvider({ children }: { children: ReactNode }) {
  const [preference, updatePreference] = useState(readLanguagePreference);
  const [preferenceSaved, setPreferenceSaved] = useState(true);
  const [languages, setLanguages] = useState(browserLanguages);
  const locale = resolveLocale(preference, languages);
  const setPreference = useCallback((next: LanguagePreference) => {
    const checked = languagePreference(next);
    setPreferenceSaved(saveLanguagePreference(checked));
    updatePreference(checked);
  }, []);

  useEffect(() => {
    const onStorage = (event: StorageEvent) => {
      if (event.key === LANGUAGE_STORAGE_KEY || event.key === null) {
        updatePreference(event.key === null ? readLanguagePreference() : languagePreference(event.newValue));
        setPreferenceSaved(true);
      }
    };
    const onLanguage = () => setLanguages(browserLanguages());
    window.addEventListener('storage', onStorage);
    window.addEventListener('languagechange', onLanguage);
    return () => { window.removeEventListener('storage', onStorage); window.removeEventListener('languagechange', onLanguage); };
  }, []);

  useLayoutEffect(() => { document.documentElement.lang = locale; }, [locale]);

  const t: Translate = useCallback((key, values) => interpolate((locale === 'zh-CN' ? chinese[key] : english[key]) ?? english[key] ?? key, values), [locale]);
  const pageTitle = useCallback((page: { id: string; title: string }) => page.id === 'home' ? t('app.home') : page.title, [t]);
  const errorText = useCallback((error: unknown) => {
    if (error instanceof ApiError && errorKeys[error.code]) return t(errorKeys[error.code]);
    // Unknown provider/runtime diagnostics are evidence, not translation keys.
    if (error instanceof Error && error.message) return error.message;
    if (typeof error === 'string' && error) return error;
    return t('error.generic');
  }, [t]);
  const value = useMemo(() => ({ locale, preference, preferenceSaved, setPreference, t, pageTitle, errorText }), [locale, preference, preferenceSaved, setPreference, t, pageTitle, errorText]);
  return <Context.Provider value={value}>{children}</Context.Provider>;
}

export function useLanguage() {
  const value = useContext(Context);
  if (!value) throw new Error('LanguageProvider is required.');
  return value;
}
