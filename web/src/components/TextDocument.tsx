import { Component, lazy, Suspense, useState, type ReactNode } from 'react';
import { useLanguage } from '../i18n';

const MarkdownReading = lazy(() => import('./MarkdownReading'));

class ReadingBoundary extends Component<{ children: ReactNode; fallback: ReactNode }, { failed: boolean }> {
  state = { failed: false };
  static getDerivedStateFromError() { return { failed: true }; }
  render() { return this.state.failed ? this.props.fallback : this.props.children; }
}

export function TextDocument({ path, content }: { path: string; content: string }) {
  const { t } = useLanguage();
  const [source, setSource] = useState(false);
  const raw = <pre className="document-source" tabIndex={0}>{content}</pre>;
  if (!/\.(md|markdown)$/i.test(path)) return raw;
  return <div className="text-document">
    <div className="document-view-controls" role="group" aria-label={t('document.view')}>
      <button type="button" className="text-button" aria-pressed={!source} onClick={() => setSource(false)}>{t('document.read')}</button>
      <button type="button" className="text-button" aria-pressed={source} onClick={() => setSource(true)}>{t('document.source')}</button>
    </div>
    {source ? raw : <ReadingBoundary fallback={<><p role="status">{t('document.unavailable')}</p>{raw}</>}>
      <Suspense fallback={raw}><MarkdownReading content={content} /></Suspense>
    </ReadingBoundary>}
  </div>;
}
