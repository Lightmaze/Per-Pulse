/** Read and reuse structured results through the native page/source journey.
 * Copyright 2026 Lightmaze Contributors. Licensed under Apache-2.0.
 * Explicit authored example input only; this does not simulate model quality.
 */
import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { mkdtemp, mkdir, rm } from 'node:fs/promises';
import { createServer } from 'node:net';
import { tmpdir } from 'node:os';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { createRequire } from 'node:module';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '../..');
const require = createRequire(import.meta.url);
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const directory = await mkdtemp(join(tmpdir(), 'per-pulse-result-reading-'));
const screenshots = process.env.PER_PULSE_RESULT_SCREENSHOTS;
if (screenshots) await mkdir(screenshots, { recursive: true });
const socket = createServer();
await new Promise(resolve => socket.listen(0, '127.0.0.1', resolve));
const port = socket.address().port;
await new Promise(resolve => socket.close(resolve));
const base = 'http://127.0.0.1:' + port;
const errors = [];
const externalRequests = [];
const checks = [];
let serverLog = '';
let browser;
let page;
const server = spawn(process.env.PER_PULSE_PYTHON || join(root, '.venv/bin/python'), [
  '-m', 'per_pulse', '--example', '--data-dir', directory, '--port', String(port),
], { cwd: root, stdio: ['ignore', 'pipe', 'pipe'] });
server.stderr.on('data', data => { serverLog += data; });
server.stdout.on('data', data => { serverLog += data; });
const wait = async (fn, label, timeout = 20000) => {
  const deadline = Date.now() + timeout;
  while (Date.now() < deadline) {
    try { if (await fn()) return; } catch {}
    await new Promise(resolve => setTimeout(resolve, 80));
  }
  throw new Error('Timed out: ' + label + '\n' + serverLog);
};
const post = async (path, body) => {
  const response = await fetch(base + path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
  assert.ok(response.ok, await response.clone().text());
  return response.json();
};
try {
  await wait(async () => (await fetch(base + '/api/health')).ok, 'native example service', 40000);
  const source = await post('/api/pages/home/messages', { content: 'Explore a result and bring one concrete observation back.' });
  const parent = await post('/api/pages', { title: 'Field method', instruction: 'Keep the parent distinct from the actual return location.', sourcePageId: 'home', sourceMessageId: source.messageId });
  const work = await post('/api/pages', { title: 'Field result', instruction: 'Authored interface specimen, not model output.', sourcePageId: parent.page.id });
  const reference = await post('/api/pages', { title: 'Reference notebook', instruction: 'Keep the original observation here.', sourcePageId: work.page.id });
  const code = '# Reference notebook is literal code.\nmessage = "Keep  two  spaces"\nprint(message)\n';
  const quote = 'A finished local result can stand while the wider question remains open.';
  const content = '# A usable field result\n\nWe tested the **first interaction**. Read **Reference notebook** for the original.\n\n## What happened\n\n1. Open the practice.\n2. Make a real attempt.\n3. Compare the result.\n\n| Case | Observed result | Next decision |\n| --- | --- | --- |\n| First attempt | A complete local result | Keep it |\n| Transfer | A different constraint | Adapt the example |\n\n```python\n' + code + '```\n\n> ' + quote + '\n\n[CommonMark reference](https://commonmark.org/help/)';
  const result = await post(`/api/pages/${work.page.id}/messages`, { content, references: [{ targetPageId: reference.page.id, label: 'Reference notebook' }] });
  const literal = await post(`/api/pages/${work.page.id}/messages`, {
    content: '`Reference notebook`\n\n![An external diagram](https://example.com/not-requested.png)\n\n[![Nested image](https://example.com/not-requested-either.png)](https://example.com/source)\n\n<script>window.untrustedResult = true</script>\n\n[Unsafe link](javascript:alert%281%29)',
    references: [{ targetPageId: reference.page.id, label: 'Reference notebook' }],
  });
  browser = await chromium.launch({
    executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE || undefined,
    args: process.env.PLAYWRIGHT_CHROMIUM_ARGS ? JSON.parse(process.env.PLAYWRIGHT_CHROMIUM_ARGS) : undefined,
  });
  const context = await browser.newContext({ viewport: { width: 1280, height: 900 }, permissions: ['clipboard-read', 'clipboard-write'] });
  page = await context.newPage();
  page.on('pageerror', error => errors.push(error.message));
  page.on('console', message => { if (message.type() === 'error' || message.type() === 'warning') errors.push(message.text()); });
  page.on('request', request => { if (!request.url().startsWith(base)) externalRequests.push(request.url()); });
  await page.goto(base + '/page/' + work.page.id + '#message-' + result.messageId);
  assert.equal(await page.title(), 'Per Pulse');
  await page.getByRole('heading', { name: 'Field result', exact: true }).waitFor();
  const article = page.locator(`[data-message-id="${result.messageId}"]`);
  await article.getByRole('heading', { name: 'A usable field result', exact: true }).waitFor();
  assert.equal(await article.locator('ol > li').count(), 3);
  assert.equal(await article.locator('table tbody tr').count(), 2);
  assert.equal(await article.locator('pre').innerText(), code);
  assert.equal(await article.locator('pre a, code a').count(), 0);
  await article.getByRole('button', { name: 'Copy code', exact: true }).click();
  assert.equal(await page.evaluate(() => navigator.clipboard.readText()), code);
  await article.getByRole('button', { name: 'Copy original message', exact: true }).click();
  assert.equal(await page.evaluate(() => navigator.clipboard.readText()), content);
  checks.push('headings, lists and tables render; code and original Markdown copy exactly');

  const literalArticle = page.locator(`[data-message-id="${literal.messageId}"]`);
  assert.equal(await literalArticle.locator('code a, img, script').count(), 0);
  assert.equal(await literalArticle.locator('.message-references a').count(), 1);
  assert.equal(await literalArticle.getByRole('link', { name: 'Open image: An external diagram', exact: true }).getAttribute('href'), 'https://example.com/not-requested.png');
  assert.equal(await literalArticle.getByRole('link', { name: 'Nested image', exact: true }).getAttribute('href'), 'https://example.com/source');
  assert.equal(await literalArticle.getByRole('link', { name: 'Unsafe link', exact: true }).count(), 0);
  assert.equal(await page.evaluate(() => window.untrustedResult), undefined);
  assert.deepEqual(externalRequests, []);
  checks.push('code-only native references remain reachable; raw HTML and images trigger no external effect');

  const composer = page.getByRole('textbox', { name: 'Message Field result', exact: true });
  const draft = 'Keep this unsent follow-up while I inspect the source.';
  await composer.fill(draft);
  await article.getByRole('link', { name: 'Reference notebook', exact: true }).click();
  await page.getByRole('heading', { name: 'Reference notebook', exact: true }).waitFor();
  await page.getByRole('button', { name: 'Back to conversation', exact: true }).click();
  await article.waitFor();
  assert.equal(await composer.inputValue(), draft);
  assert.equal(new URL(page.url()).hash, '#message-' + result.messageId);
  // A rendered paragraph joins the bold span to plain text, but those
  // characters are not one contiguous excerpt in the authored Markdown.
  // Never silently export the entire original when the selected scope differs.
  await article.locator('.message-content p').first().evaluate(node => {
    const range = document.createRange(); range.selectNodeContents(node);
    const selection = getSelection(); selection.removeAllRanges(); selection.addRange(range);
  });
  await article.getByRole('button', { name: 'Bring to Home', exact: true }).click();
  const crossFormatDialog = page.getByRole('dialog', { name: 'Bring this back to Home.', exact: true });
  const crossFormatInput = crossFormatDialog.getByRole('textbox', { name: 'What would you like to bring back?', exact: true });
  assert.equal(await crossFormatInput.inputValue(), '');
  assert.equal(await crossFormatDialog.getByRole('button', { name: 'Bring to Home', exact: true }).isDisabled(), true);
  await crossFormatDialog.getByText('The highlighted text does not match a contiguous passage', { exact: false }).waitFor();
  await crossFormatDialog.getByText('Show original text for copying', { exact: true }).click();
  assert.equal(await crossFormatDialog.getByRole('textbox', { name: 'Show original text for copying', exact: true }).inputValue(), content);
  await crossFormatDialog.getByRole('button', { name: 'Use entire source result', exact: true }).click();
  assert.equal(await crossFormatInput.inputValue(), content);
  assert.equal(await crossFormatDialog.getByRole('button', { name: 'Bring to Home', exact: true }).isEnabled(), true);
  await page.keyboard.press('Escape');
  await crossFormatDialog.waitFor({ state: 'hidden' });
  checks.push('a cross-format selection cannot disclose the full source without explicit consent');

  await article.locator('blockquote').evaluate(node => {
    const range = document.createRange(); range.selectNodeContents(node);
    const selection = getSelection(); selection.removeAllRanges(); selection.addRange(range);
  });
  await article.getByRole('button', { name: 'Bring to Home', exact: true }).click();
  const dialog = page.getByRole('dialog', { name: 'Bring this back to Home.', exact: true });
  assert.equal(await dialog.getByRole('textbox', { name: 'What would you like to bring back?', exact: true }).inputValue(), quote);
  await dialog.getByRole('button', { name: 'Bring to Home', exact: true }).click();
  await dialog.waitFor({ state: 'hidden' });
  await page.getByRole('textbox', { name: 'Message Home', exact: true }).waitFor();
  assert.equal(new URL(page.url()).pathname, '/');
  const returned = page.locator('.message').filter({ has: page.getByText(quote, { exact: true }) }).last();
  const homeComposer = page.getByRole('textbox', { name: 'Message Home', exact: true });
  const homeDraft = 'Keep the Home draft while I revisit the nested result.';
  await homeComposer.fill(homeDraft);
  const returnedId = await returned.getAttribute('data-message-id');
  await returned.locator('.source-line').getByRole('link', { name: 'Field result', exact: true }).click();
  await article.waitFor();
  assert.equal(new URL(page.url()).hash, '#message-' + result.messageId);
  assert.equal(await composer.inputValue(), draft);
  await page.getByRole('button', { name: 'Back to conversation', exact: true }).click();
  await homeComposer.waitFor();
  assert.equal(new URL(page.url()).pathname, '/');
  assert.equal(await homeComposer.inputValue(), homeDraft);
  await wait(() => page.evaluate(id => document.activeElement?.id === 'message-' + id, returnedId), 'return focus to the exact Home message');
  await returned.locator('.source-line').getByRole('link', { name: 'Field result', exact: true }).click();
  await article.waitFor();
  assert.equal(new URL(page.url()).hash, '#message-' + result.messageId);
  assert.equal(await composer.inputValue(), draft);
  checks.push('nested source return reaches the exact Home message, preserves both drafts and can reopen the original result');

  await article.scrollIntoViewIfNeeded();
  if (screenshots) await page.screenshot({ path: join(screenshots, 'results-desktop.png') });
  await page.setViewportSize({ width: 390, height: 844 });
  await article.locator('table').scrollIntoViewIfNeeded();
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
  const pre = article.locator('pre');
  assert.ok(await pre.evaluate(node => node.scrollWidth > node.clientWidth));
  await pre.focus();
  await page.keyboard.press('ArrowRight');
  await wait(async () => await pre.evaluate(node => node.scrollLeft > 0), 'keyboard scroll for long code');
  if (screenshots) await page.screenshot({ path: join(screenshots, 'results-mobile.png') });
  checks.push('phone layout fits without page overflow and long code remains keyboard-scrollable');

  await page.evaluate(() => { navigator.clipboard.writeText = async () => { throw new DOMException('Deliberate denied clipboard', 'NotAllowedError'); }; });
  await article.getByRole('button', { name: 'Copy code', exact: true }).click();
  await article.getByText('Could not copy. Select the text and copy it manually.', { exact: true }).waitFor();
  assert.equal(await composer.inputValue(), draft);
  checks.push('denied clipboard reports a usable fallback without changing the source or draft');
  assert.deepEqual(errors, []);
  assert.equal(await page.locator('vite-error-overlay').count(), 0);
  console.log(JSON.stringify({ checks, count: checks.length, errors, externalRequests, viewports: ['1280x900', '390x844'] }, null, 2));
} catch (error) {
  if (screenshots && page) await page.screenshot({ path: join(screenshots, 'results-failure.png') }).catch(() => {});
  throw error;
} finally {
  await browser?.close();
  server.kill('SIGTERM');
  await Promise.race([new Promise(resolve => server.once('exit', resolve)), new Promise(resolve => setTimeout(resolve, 8000))]);
  if (server.exitCode === null && server.signalCode === null) server.kill('SIGKILL');
  await rm(directory, { recursive: true, force: true });
}
