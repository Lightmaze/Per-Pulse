import { useEffect, useRef, useState } from 'react';
import { useLanguage } from '../i18n';
import { Icon } from './Icon';

export function CopyButton({ text, label }: { text: string; label: string }) {
  const { t } = useLanguage();
  const [state, setState] = useState<'ready' | 'copying' | 'copied' | 'failed'>('ready');
  const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const attempt = useRef(0);
  useEffect(() => () => { attempt.current++; clearTimeout(timer.current); }, []);
  const copy = async () => {
    const current = ++attempt.current;
    clearTimeout(timer.current);
    setState('copying');
    try {
      await navigator.clipboard.writeText(text);
      if (current !== attempt.current) return;
      setState('copied');
      timer.current = setTimeout(() => setState('ready'), 2200);
    } catch { if (current === attempt.current) setState('failed'); }
  };
  return <span className="copy-control">
    <button type="button" onClick={copy} disabled={state === 'copying'} aria-label={label} title={label}>
      <Icon name={state === 'copied' ? 'check' : 'copy'} size={14} />{t(state === 'copied' ? 'conversation.copied' : 'conversation.copy')}
    </button>
    <span className={state === 'failed' ? 'copy-error' : 'sr-only'} role="status">{state === 'failed' ? t('conversation.copyFailed') : state === 'copied' ? t('conversation.copied') : ''}</span>
  </span>;
}
