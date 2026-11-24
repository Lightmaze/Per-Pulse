import { memo, useMemo } from 'react';
import Markdown, { type Components } from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { useLanguage } from '../i18n';
import { messageReferencePlugin, safeMessageUrl, type MessageReference } from '../messageMarkdown';
import { CopyButton } from './CopyButton';
import { SessionLink, type Navigate } from './SessionLink';

// Polls return new message objects. Primitive content/reference props let
// unchanged messages retain their parsed output and code-copy feedback.
export const MessageContent = memo(function MessageContent({ content, referenceKey, pageId, messageId, navigate }: {
  content: string; referenceKey: string; pageId: string; messageId: string; navigate: Navigate;
}) {
  const { t } = useLanguage();
  const references = useMemo(() => JSON.parse(referenceKey) as MessageReference[], [referenceKey]);
  const components = useMemo<Components>(() => ({
    h1: 'h2', h2: 'h3', h3: 'h4', h4: 'h5', h5: 'h6',
    a: ({ node, href, children, ...rest }) => {
      const reference = references.find(item => item.id === node?.properties['data-page-reference']);
      if (reference) return <SessionLink pageId={reference.targetPageId} label={reference.label} referenceId={reference.id} origin={{ pageId, messageId, referenceId: reference.id }} navigate={navigate} />;
      if (!href) return <span>{children}</span>;
      return <a {...rest} href={href} target={href.startsWith('#') ? undefined : '_blank'} rel="noopener noreferrer">{children}</a>;
    },
    pre: ({ node, children }) => {
      const code = node?.children.find(child => child.type === 'element' && child.tagName === 'code');
      const text = code?.type === 'element' ? code.children.map(child => child.type === 'text' ? child.value : '').join('') : '';
      const classes = code?.type === 'element' ? code.properties.className : [];
      const language = Array.isArray(classes) ? String(classes.find(name => String(name).startsWith('language-')) || '').slice(9) : '';
      return <div className="message-code">
        <div className="message-code-toolbar"><span>{language || t('conversation.code')}</span><CopyButton text={text} label={t('conversation.copyCode')} /></div>
        <pre tabIndex={0}>{children}</pre>
      </div>;
    },
    table: ({ children }) => <div className="message-table" tabIndex={0} role="region" aria-label={t('conversation.table')}><table>{children}</table></div>,
  }), [references, pageId, messageId, navigate, t]);

  return <div className="message-content"><Markdown
    remarkPlugins={[remarkGfm]}
    remarkRehypeOptions={{ clobberPrefix: `message-${messageId}-` }}
    rehypePlugins={[[messageReferencePlugin, { references, imageLabel: t('conversation.openImage') }]]}
    urlTransform={safeMessageUrl}
    components={components}
  >{content}</Markdown></div>;
});
