/** Preserve native page references without interpreting code as navigation.
 * Copyright 2026 Lightmaze Contributors. Licensed under Apache-2.0.
 */
import type { Element, ElementContent, Root } from 'hast';
import type { PageReference } from './types';

export type MessageReference = Pick<PageReference, 'id' | 'targetPageId' | 'label'>;

export function safeMessageUrl(value: string): string | undefined {
  // Only explicit web/mail links and document fragments are clickable.
  // Native page navigation is created separately from persisted references.
  if (/[\u0000-\u0020\u007f]/.test(value)) return undefined;
  if (/^https?:\/\//i.test(value) || /^mailto:/i.test(value) || value.startsWith('#')) return value;
  return undefined;
}

function referenceNode(reference: MessageReference): Element {
  return {
    type: 'element', tagName: 'a',
    properties: { href: '#', 'data-page-reference': reference.id },
    children: [{ type: 'text', value: reference.label }],
  };
}

export function messageReferencePlugin({ references, imageLabel }: { references: MessageReference[]; imageLabel: string }) {
  return (tree: Root) => {
    const sorted = [...references].filter(reference => reference.label).sort((a, b) => b.label.length - a.label.length);
    const used = new Set<string>();

    const visit = (parent: Root | Element, inLink = false) => {
      if (parent.type === 'element' && ['code', 'pre'].includes(parent.tagName)) return;
      for (let index = 0; index < parent.children.length; index++) {
        const child = parent.children[index];
        if (child.type === 'element') {
          if (child.tagName === 'img') {
            const alt = String(child.properties.alt || imageLabel);
            // A rendered message must not fetch remote images on the user's
            // behalf. Keep an explicit link, or the enclosing link's alt text.
            const replacement: ElementContent = inLink ? { type: 'text', value: alt } : {
              type: 'element', tagName: 'a',
              properties: { href: child.properties.src, className: ['message-image-link'] },
              children: [{ type: 'text', value: `${imageLabel}: ${alt}` }],
            };
            parent.children.splice(index, 1, replacement);
          } else visit(child, inLink || child.tagName === 'a');
          continue;
        }
        if (child.type !== 'text' || inLink || !sorted.length) continue;
        const nodes: ElementContent[] = [];
        let position = 0;
        while (position < child.value.length) {
          let found: MessageReference | undefined;
          let next = child.value.length;
          for (const reference of sorted) {
            const offset = child.value.indexOf(reference.label, position);
            if (offset !== -1 && offset < next) { next = offset; found = reference; }
          }
          if (!found) { nodes.push({ type: 'text', value: child.value.slice(position) }); break; }
          if (next > position) nodes.push({ type: 'text', value: child.value.slice(position, next) });
          nodes.push(referenceNode(found));
          used.add(found.id);
          position = next + found.label.length;
        }
        parent.children.splice(index, 1, ...nodes);
        index += nodes.length - 1;
      }
    };
    visit(tree);
    const remaining = references.filter(reference => !used.has(reference.id));
    if (remaining.length) tree.children.push({
      type: 'element', tagName: 'p', properties: { className: ['message-references'] },
      children: remaining.map(referenceNode),
    });
  };
}
