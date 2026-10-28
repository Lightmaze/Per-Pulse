import Markdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { useLanguage } from '../i18n';
import { externalDocumentLink } from '../documentLinks';

/** Raw HTML stays escaped. Images never issue background network requests. */
export default function MarkdownReading({ content }: { content: string }) {
  const { t } = useLanguage();
  return <div className="markdown-document" tabIndex={0}>
    <Markdown remarkPlugins={[remarkGfm]} components={{
      h1: 'h3', h2: 'h4', h3: 'h5', h4: 'h6', h5: 'h6', h6: 'h6',
      a: ({ href, title, children }) => {
        const target = externalDocumentLink(href);
        return target ? <a href={target} title={title} target="_blank" rel="noopener noreferrer">{children}<span className="visually-hidden"> {t('document.newTab')}</span></a> : <span title={href}>{children}</span>;
      },
      img: ({ alt }) => <span className="markdown-image-description">{t('document.image', { label: alt || t('document.unnamedImage') })}</span>,
      table: ({ children }) => <div className="markdown-table" tabIndex={0} role="region" aria-label={t('document.table')}><table>{children}</table></div>,
    }}>{content}</Markdown>
  </div>;
}
