import assert from 'node:assert/strict';
import test from 'node:test';
import { externalDocumentLink } from '../src/documentLinks.ts';

test('authored documents can link to explicit HTTP sources', () => {
  assert.equal(externalDocumentLink('https://example.org/paper?q=source#results'), 'https://example.org/paper?q=source#results');
  assert.equal(externalDocumentLink('http://example.org/'), 'http://example.org/');
});

test('document source cannot turn app routes, executable URLs or credentials into navigation', () => {
  for (const value of [undefined, '', '/api/pages/home', '../result.md', '#source', '//remote.example/pixel',
    'javascript:alert(1)', ' JaVaScRiPt:alert(1) ', 'data:text/html,test', 'file:///etc/passwd',
    'https://name:credential@example.org/', 'mailto:person@example.org']) {
    assert.equal(externalDocumentLink(value), null, String(value));
  }
});
