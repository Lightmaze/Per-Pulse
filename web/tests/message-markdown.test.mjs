import assert from 'node:assert/strict';
import test from 'node:test';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import Markdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { messageReferencePlugin, safeMessageUrl } from '../src/messageMarkdown.ts';

const reference = { id: 'reference-1', targetPageId: 'reading-room', label: 'Reading room' };
const render = (content, references = [reference]) => renderToStaticMarkup(createElement(Markdown, {
  remarkPlugins: [remarkGfm],
  rehypePlugins: [[messageReferencePlugin, { references, imageLabel: 'Open image' }]],
  urlTransform: safeMessageUrl,
  children: content,
}));

test('structured results keep code literal and native references in prose', () => {
  const html = render('# Result\n\n**Reading room** has the original.\n\n`Reading room`\n\n```python\n# Reading room\nprint("two  spaces")\n```\n\n| Case | Result |\n| --- | --- |\n| A | Kept |');
  assert.match(html, /<h1>Result<\/h1>/);
  assert.match(html, /<strong><a href="#" data-page-reference="reference-1">Reading room<\/a><\/strong>/);
  assert.match(html, /<code>Reading room<\/code>/);
  assert.match(html, /<pre><code class="language-python"># Reading room\nprint\(&quot;two  spaces&quot;\)\n<\/code><\/pre>/);
  assert.match(html, /<table>/);
  assert.equal(html.match(/data-page-reference=/g).length, 1);
});

test('references inside code, split formatting or existing links retain a separate native entry', () => {
  for (const content of ['`Reading room`', '**Reading** room', '[Reading room](https://example.com/reference)']) {
    const html = render(content);
    assert.match(html, /class="message-references"><a href="#" data-page-reference="reference-1">Reading room<\/a>/);
    assert.equal(html.match(/data-page-reference=/g).length, 1);
    assert.doesNotMatch(html, /<a[^>]*><a/);
  }
});

test('only exact persisted reference labels create native links; overlapping labels prefer the longest', () => {
  const html = render('Reading room and Reading. Not a real page.', [reference, { id: 'short', targetPageId: 'short-page', label: 'Reading' }]);
  assert.match(html, /data-page-reference="reference-1">Reading room/);
  assert.match(html, /data-page-reference="short">Reading/);
  assert.doesNotMatch(render('[Forged page](/page/elsewhere)', []), /href=/);
});

test('remote images become explicit links, including images nested inside links', () => {
  const html = render('![Diagram](https://example.com/diagram.png)\n\n[![Chart](https://example.com/chart.png)](https://example.com/source)', []);
  assert.doesNotMatch(html, /<img|<link/);
  assert.match(html, /href="https:\/\/example.com\/diagram.png"[^>]*>Open image: Diagram<\/a>/);
  assert.match(html, /href="https:\/\/example.com\/source">Chart<\/a>/);
});

test('raw HTML and active URL schemes cannot become executable result content', () => {
  const html = render('<script>alert(1)</script>\n\n[unsafe](javascript:alert%281%29)\n\n![unsafe](data:text/html,hello)', []);
  assert.doesNotMatch(html, /<script|<img|href="(?:javascript|data):/);
  assert.match(html, /&lt;script&gt;/);
  for (const value of ['javascript:alert(1)', 'data:text/html,hello', '/api/settings', '//example.com', 'file:///tmp/example', 'https:\n//example.com']) {
    assert.equal(safeMessageUrl(value), undefined);
  }
  for (const value of ['https://example.com', 'http://example.com', 'mailto:person@example.com', '#footnote']) assert.equal(safeMessageUrl(value), value);
});
