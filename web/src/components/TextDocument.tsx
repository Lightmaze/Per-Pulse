import { Component, lazy, Suspense, useState, type ReactNode } from 'react';
import { useLanguage } from '../i18n';
import { DelimitedReading } from './DelimitedReading';

const MarkdownReading = lazy(() => import('./MarkdownReading'));

class ReadingBoundary extends Component<{ children: ReactNode; fallback: ReactNode }, { failed: boolean }> {
  state = { failed: false };
  static getDerivedStateFromError() { return { failed: true }; }
  render() { return this.state.failed ? this.props.fallback : this.props.children; }
}

export function TextDocument({ path, content, truncated = false }: { path: string; content: string; truncated?: boolean }) {
  const { t } = useLanguage();
  const [source, setSource] = useState(false);
  const [headings, setHeadings] = useState(true);
  const [hiddenColumns, setHiddenColumns] = useState<number[]>([]);
  const raw = <pre className="document-source" tabIndex={0}>{content}</pre>;
  const markdown = /\.(md|markdown)$/i.test(path);
  const delimited = /\.(csv|tsv)$/i.test(path);
  if (!markdown && !delimited) return raw;
  return <div className="text-document">
    <div className="document-view-controls" role="group" aria-label={t('document.view')}>
      <button type="button" className="text-button" aria-pressed={!source} onClick={() => setSource(false)}>{t('document.read')}</button>
      <button type="button" className="text-button" aria-pressed={source} onClick={() => setSource(true)}>{t('document.source')}</button>
    </div>
    {source ? raw : <ReadingBoundary fallback={<><p role="status">{t('document.unavailable')}</p>{raw}</>}>
      {delimited ? <DelimitedReading content={content} delimiter={/\.tsv$/i.test(path) ? '\t' : ','} truncated={truncated} fallback={raw} headings={headings} setHeadings={setHeadings} hiddenColumns={hiddenColumns} setHiddenColumns={setHiddenColumns} />
        : <Suspense fallback={raw}><MarkdownReading content={content} /></Suspense>}
    </ReadingBoundary>}
  </div>;
}
