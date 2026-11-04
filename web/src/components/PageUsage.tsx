import { useLanguage } from '../i18n';
import { compactTokens, exactTokens, hasUsage, totalTokens, type PageUsage as Usage } from '../pageUsage';

/** Tokens used on this page, as the model service reported them. */
export function PageUsage({ usage }: { usage: Usage | null | undefined }) {
  const { t, locale } = useLanguage();
  if (!hasUsage(usage)) return null;
  const exact = (value: number) => exactTokens(value, locale);
  return <details className="page-usage">
    <summary>{t(usage.unreportedRounds ? 'usage.summaryAtLeast' : 'usage.summary', { total: compactTokens(totalTokens(usage), locale) })}</summary>
    <dl>
      <div><dt>{t('usage.input')}</dt><dd>{exact(usage.inputTokens)}{usage.cachedTokens ? ` · ${t('usage.cached', { count: exact(usage.cachedTokens) })}` : ''}</dd></div>
      <div><dt>{t('usage.output')}</dt><dd>{exact(usage.outputTokens)}</dd></div>
      {usage.lastTurn ? <div><dt>{t('usage.lastTurn')}</dt><dd>{exact(totalTokens(usage.lastTurn))}</dd></div> : null}
    </dl>
    <p>{t('usage.source')}{usage.unreportedRounds ? ' ' + t('usage.unreported', { count: exact(usage.unreportedRounds) }) : ''}</p>
  </details>;
}
