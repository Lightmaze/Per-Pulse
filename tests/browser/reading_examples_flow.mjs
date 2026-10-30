/** Native reading -> retained example -> explicit share -> approved artifact.
 * Synthetic model choices; actual World/Kernel, files, browser and restart.
 * Copyright 2026 Lightmaze Contributors. Licensed under Apache-2.0.
 */
import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { mkdtemp, mkdir, readFile, writeFile, rm } from 'node:fs/promises';
import { createServer } from 'node:net';
import { tmpdir } from 'node:os';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { createRequire } from 'node:module';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '../..');
const require = createRequire(import.meta.url);
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const directory = await mkdtemp(join(tmpdir(), 'per-pulse-reading-examples-'));
const screenshots = process.env.PER_PULSE_READING_SCREENSHOTS;
if (screenshots) await mkdir(screenshots, { recursive: true });
const socket = createServer();
await new Promise(resolve => socket.listen(0, '127.0.0.1', resolve));
const port = socket.address().port;
await new Promise(resolve => socket.close(resolve));
const base = `http://127.0.0.1:${port}`;
const checks = [], errors = [];
let logs = '', browser, page;
const start = (offline = false) => {
  const child = spawn(process.env.PER_PULSE_PYTHON || join(root, '.venv/bin/python'), [
    join(root, 'tests/browser/reading_example_server.py'), '--data-dir', directory, '--port', String(port),
    ...(offline ? ['--offline'] : []),
  ], { cwd: root, env: { ...process.env, PYTHONPATH: join(root, 'src') }, stdio: ['ignore', 'pipe', 'pipe'] });
  child.stdout.on('data', data => { logs += data; });
  child.stderr.on('data', data => { logs += data; });
  return child;
};
const stop = async child => {
  if (child.exitCode !== null || child.signalCode !== null) return;
  const exited = new Promise(resolve => child.once('exit', resolve));
  const force = setTimeout(() => child.kill('SIGKILL'), 10000);
  child.kill('SIGTERM'); await exited; clearTimeout(force);
};
const wait = async (fn, label) => {
  const deadline = Date.now() + 20000;
  while (Date.now() < deadline) {
    if (await fn().catch(() => false)) return;
    await new Promise(resolve => setTimeout(resolve, 80));
  }
  throw new Error(`Timed out: ${label}\n${logs}`);
};
const api = async (path, body) => {
  const result = await fetch(base + path, body === undefined ? undefined : {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
  });
  assert.ok(result.ok, `${path}: ${await result.clone().text()}`);
  return result.json();
};
const view = id => api(`/api/pages/${id}`);
const experiences = id => api(`/api/pages/${id}/posture`);
const create = async title => {
  const result = await api('/api/pages', { title, instruction: 'Prepare a source comparison. [Explicit synthetic fixture]' });
  const id = result.page.id;
  await wait(async () => (await view(id)).messages.some(m => m.role === 'assistant'), 'initial reply');
  return id;
};
const send = async (id, content) => {
  const sent = await api(`/api/pages/${id}/messages`, { content });
  await wait(async () => (await view(id)).messages.some(m => m.id === sent.messageId && m.status === 'settled'), 'native reply');
};
let server = start();
try {
  await wait(async () => (await fetch(base + '/api/health')).ok, 'service');
  const source = await create('Reading encounter');
  const receiver = await create('New application');
  const passage = 'The start character is thus included in the list, but the end character is not.';
  // A short W3C quotation (<25 words); full document is linked, not copied.
  await writeFile(join(directory, 'workspace', 'selection-source.txt'),
    'Source: https://www.w3.org/TR/annotation-model/#text-position-selector\n' + passage + '\nPRIVATE_SURROUNDING_FIXTURE\n');
  await send(source, 'READING_EXAMPLE_FIXTURE:' + JSON.stringify({ op: 'open', path: 'selection-source.txt',
    retain: { title: 'Inclusive start, exclusive end', excerpt: passage, note: 'Use this boundary when comparing spans; a position needs its source version.' } }));
  await wait(async () => (await experiences(source)).experiences.length === 1, 'native experience admission');
  const saved = (await experiences(source)).experiences[0];
  assert.equal(saved.sourceKind, 'reading');
  assert.deepEqual((await experiences(receiver)).experiences, []);
  await send(receiver, 'APPLY_READING_FIXTURE:' + JSON.stringify({ experience_id: saved.id, path: 'comparison.md' }));
  assert.ok((await view(receiver)).messages.at(-1).content.includes('not available'));
  checks.push('an actual observed passage is retained locally; the receiving Work cannot get it before sharing');

  browser = await chromium.launch({ executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE || undefined,
    args: process.env.PLAYWRIGHT_CHROMIUM_ARGS ? JSON.parse(process.env.PLAYWRIGHT_CHROMIUM_ARGS) : undefined });
  const context = await browser.newContext({ viewport: { width: 1280, height: 900 }, acceptDownloads: true });
  page = await context.newPage();
  page.on('pageerror', error => errors.push(error.message));
  const openSettings = async () => {
    if (!(await page.getByRole('button', { name: /^(Settings|设置)$/ }).isVisible())) {
      await page.getByRole('button', { name: /^(Toggle navigation|展开或收起导航)$/ }).click();
    }
    await page.getByRole('button', { name: /^(Settings|设置)$/ }).click();
  };
  const openExperience = async () => {
    await openSettings();
    await page.getByRole('button', { name: /^(View this page’s experience|查看此页面的经验)$/ }).click();
  };
  const dialog = () => page.getByRole('dialog');
  const close = () => page.getByRole('button', { name: /^(Close dialog|关闭对话框)$/ }).click();
  await page.goto(`${base}/page/${source}`);
  assert.equal(await page.title(), 'Per Pulse');
  await page.getByRole('heading', { name: 'Reading encounter', exact: true }).waitFor();
  const sourceDraft = 'Keep my place in the original reading.';
  await page.getByRole('textbox', { name: 'Message Reading encounter', exact: true }).fill(sourceDraft);
  await openExperience();
  await dialog().getByRole('heading', { name: saved.title, exact: true }).waitFor();
  assert.equal(await dialog().locator('.saved-example code').textContent(), passage);
  await dialog().getByText('Source experience', { exact: true }).click();
  await dialog().getByText('Source read: selection-source.txt', { exact: true }).waitFor();
  await dialog().getByText('Recorded version', { exact: true }).click();
  await dialog().getByText('SHA-256: ' + saved.readingSource.digest, { exact: true }).waitFor();
  if (screenshots) await page.screenshot({ path: join(screenshots, 'reading-source-desktop.png') });
  await dialog().getByRole('button', { name: 'Use in other pages', exact: true }).click();
  await wait(async () => (await experiences(receiver)).experiences.length === 1, 'explicit share');
  await close();
  checks.push('the existing source editor shows the observed version and shares only the selected passage and note');

  await page.goto(`${base}/page/${receiver}`);
  const receiverDraft = 'Use the source in this separate work.';
  await page.getByRole('textbox', { name: 'Message New application', exact: true }).fill(receiverDraft);
  await openExperience();
  const shared = (await experiences(receiver)).experiences[0];
  assert.equal(shared.readingSource, undefined);
  assert.equal(shared.encounter, undefined);
  assert.ok(!(await dialog().textContent()).includes('PRIVATE_SURROUNDING_FIXTURE'));
  await dialog().getByText('Source experience', { exact: true }).click();
  await dialog().getByRole('button', { name: 'Open reading conversation', exact: true }).click();
  await page.getByRole('heading', { name: 'Reading encounter', exact: true }).waitFor();
  assert.equal(await page.getByRole('textbox', { name: 'Message Reading encounter', exact: true }).inputValue(), sourceDraft);
  await page.getByRole('button', { name: 'Back to conversation', exact: true }).click();
  assert.equal(await page.getByRole('textbox', { name: 'Message New application', exact: true }).inputValue(), receiverDraft);
  checks.push('source navigation returns to the original reading turn and preserves both Work drafts');

  await page.getByRole('textbox', { name: 'Message New application', exact: true }).fill(
    'APPLY_READING_FIXTURE:' + JSON.stringify({ experience_id: saved.id, path: 'comparison.md' }));
  await page.getByRole('button', { name: 'Send message', exact: true }).click();
  await page.getByRole('button', { name: 'Allow once', exact: true }).click();
  await wait(async () => (await view(receiver)).messages.some(m => m.role === 'assistant'
    && m.status === 'settled' && m.content.includes('The source comparison is saved in comparison.md')), 'approved comparison');
  const bytes = await readFile(join(directory, 'workspace', 'comparison.md'), 'utf8');
  assert.ok(bytes.includes(passage) && bytes.includes(saved.note));
  assert.ok(!bytes.includes('PRIVATE_SURROUNDING_FIXTURE'));
  checks.push('the shared example reaches a real native file action, with the existing explicit approval');

  // Consume the product's recorded artifact, not just its workspace bytes.
  await page.getByRole('button', { name: /comparison\.md/ }).click();
  const downloading = page.waitForEvent('download');
  await page.getByRole('button', { name: 'Download file', exact: true }).click();
  const downloaded = await downloading;
  assert.equal(downloaded.suggestedFilename(), 'comparison.md');
  assert.equal(await readFile(await downloaded.path(), 'utf8'), bytes);
  const result = (await view(receiver)).messages.find(m => m.role === 'assistant'
    && m.status === 'settled' && m.content.includes('The source comparison is saved in comparison.md'));
  const deliveryDraft = 'Continue comparing the original source after delivery.';
  await page.locator('.composer textarea').fill(deliveryDraft);
  await page.locator('#message-' + result.id).getByRole('button', { name: 'Bring to Home', exact: true }).click();
  const returning = page.getByRole('dialog', { name: 'Bring this back to Home.', exact: true });
  await returning.getByRole('button', { name: 'Bring to Home', exact: true }).click();
  await wait(async () => new URL(page.url()).pathname === '/', 'selected result at Home');
  let adopted;
  await wait(async () => {
    const matches = (await view('home')).messages.filter(m =>
      m.sourcePageId === receiver && m.sourceMessageId === result.id);
    adopted = matches.length === 1 ? matches[0] : null;
    return Boolean(adopted);
  }, 'persisted exact result attribution');
  assert.ok(adopted.content.includes(result.content));
  await page.locator('#message-' + adopted.id + ' .source-line a.session-link').click();
  await wait(async () => decodeURIComponent(new URL(page.url()).hash) === '#message-' + result.id,
    'Home source opens the exact delivery result');
  assert.equal(await page.locator('.composer textarea').inputValue(), deliveryDraft);
  await page.getByRole('button', { name: 'Back to conversation', exact: true }).click();
  await wait(async () => new URL(page.url()).pathname === '/', 'return to original Home conversation');
  if (screenshots) await page.screenshot({ path: join(screenshots, 'reading-result-home.png') });
  checks.push('the exact approved file downloads; the selected result returns to Home with its source and restores the delivery draft');

  await stop(server); server = start(true);
  await wait(async () => (await fetch(base + '/api/health')).ok, 'restarted World');
  const retainedReturn = (await view('home')).messages.find(m => m.id === adopted.id);
  assert.equal(retainedReturn.sourcePageId, receiver);
  assert.equal(retainedReturn.sourceMessageId, result.id);
  await page.goto(`${base}/page/${receiver}#message-${result.id}`);
  await page.getByRole('heading', { name: 'New application', exact: true }).waitFor();
  assert.equal(await page.locator('.composer textarea').inputValue(), deliveryDraft);
  await page.goto(`${base}/page/${source}`);
  await openSettings();
  await page.locator('#interface-language').selectOption('zh-CN');
  await close();
  await page.setViewportSize({ width: 390, height: 844 });
  await openExperience();
  await dialog().getByText('来自实际读到的材料片段。备注是解释，片段尚未被评为成功结果。', { exact: true }).waitFor();
  assert.equal(await dialog().locator('.saved-example code').textContent(), passage);
  assert.ok(await dialog().evaluate(el => el.scrollWidth <= el.clientWidth + 1));
  if (screenshots) await page.screenshot({ path: join(screenshots, 'reading-example-mobile.png') });
  assert.equal(await readFile(join(directory, 'workspace', 'comparison.md'), 'utf8'), bytes);
  assert.deepEqual(errors, []);
  checks.push('restart retains the source and approved file; the Chinese phone view keeps literal source text without horizontal overflow or page errors');
  console.log(JSON.stringify({ checks, pageErrors: errors, viewports: ['1280x900', '390x844'], provider: 'explicit synthetic transport' }, null, 2));
} finally {
  await browser?.close(); await stop(server); await rm(directory, { recursive: true, force: true });
}
