/** Read and save real native file effects from explicit synthetic model choices.
 * Copyright 2026 Lightmaze Contributors. Licensed under Apache-2.0.
 */
import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { createHash } from 'node:crypto';
import { mkdtemp, mkdir, readFile, rm } from 'node:fs/promises';
import { createServer } from 'node:net';
import { tmpdir } from 'node:os';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { createRequire } from 'node:module';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '../..');
const require = createRequire(import.meta.url);
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const directory = await mkdtemp(join(tmpdir(), 'per-pulse-documents-'));
const screenshots = process.env.PER_PULSE_DOCUMENT_SCREENSHOTS;
const socket = createServer();
await new Promise(done => socket.listen(0, '127.0.0.1', done));
const port = socket.address().port;
await new Promise(done => socket.close(done));
const base = 'http://127.0.0.1:' + port;
const checks = [];
const pageErrors = [];
const networkErrors = [];
const unexpectedImages = [];
let serverLog = '';
let browser;
const start = () => {
  const child = spawn(process.env.PER_PULSE_PYTHON || join(root, '.venv/bin/python'), [
    join(root, 'tests/browser/fixture_server.py'),
  ], { cwd: root, env: { ...process.env, PER_PULSE_QA_PORT: String(port), PER_PULSE_QA_DATA_DIR: directory }, stdio: ['ignore', 'pipe', 'pipe'] });
  child.stdout.on('data', data => { serverLog += data; });
  child.stderr.on('data', data => { serverLog += data; });
  return child;
};
const stop = async child => {
  if (!child || child.exitCode !== null || child.signalCode !== null) return;
  child.kill('SIGTERM');
  const force = setTimeout(() => child.kill('SIGKILL'), 9000);
  await new Promise(done => child.once('exit', done));
  clearTimeout(force);
};
const wait = async (fn, label, timeout = 20000) => {
  const deadline = Date.now() + timeout;
  while (Date.now() < deadline) {
    try { if (await fn()) return; } catch { /* Wait for the original native state. */ }
    await new Promise(done => setTimeout(done, 80));
  }
  throw new Error('Timed out: ' + label + '\n' + serverLog);
};
const ready = () => wait(async () => (await fetch(base + '/api/health')).ok, 'fixture ready');
const create = async (page, title, instruction) => {
  await page.getByRole('button', { name: 'Create a page', exact: true }).click();
  await page.getByLabel('Page name', { exact: true }).fill(title);
  await page.getByLabel('Where should we begin?', { exact: true }).fill(instruction);
  await page.getByRole('button', { name: 'Create page', exact: true }).click();
  await page.getByRole('heading', { name: title, exact: true }).waitFor();
  await page.getByRole('button', { name: 'Allow once', exact: true }).waitFor();
  return new URL(page.url()).pathname.split('/').pop();
};
const save = async (page, expected) => {
  const download = page.waitForEvent('download');
  await page.getByRole('button', { name: 'Download file', exact: true }).click();
  const file = await download;
  assert.equal(file.suggestedFilename(), expected.path);
  const bytes = await readFile(await file.path());
  assert.equal(bytes.toString('utf8'), expected.content);
  assert.equal(createHash('sha256').update(bytes).digest('hex'), expected.sha256);
};
let server = start();
try {
  await ready();
  const fixture = (await fetch(base + '/__fixture/status').then(r => r.json())).document;
  browser = await chromium.launch();
  const context = await browser.newContext({ viewport: { width: 1280, height: 900 }, locale: 'en-US' });
  await context.route('**/unexpected-source-image.png', route => { unexpectedImages.push(route.request().url()); return route.abort(); });
  const page = await context.newPage();
  page.setDefaultTimeout(12000);
  page.on('pageerror', error => pageErrors.push(error.message));
  page.on('response', response => { if (response.status() >= 400) networkErrors.push({ url: response.url(), status: response.status() }); });
  await page.goto(base);
  await page.locator('.composer textarea').waitFor();
  const firstId = await create(page, 'Readable result', 'WRITE_DOCUMENT');
  assert.equal(await page.getByRole('button', { name: 'Download file', exact: true }).count(), 0);
  await page.getByRole('button', { name: 'Allow once', exact: true }).click();
  await page.locator('.artifact-open').filter({ hasText: fixture.path }).click();
  await page.locator('.markdown-document').getByRole('heading', { name: 'A field note', exact: true }).waitFor();
  assert.equal(await page.locator('.markdown-document strong').first().innerText(), 'confirmed file');
  assert.equal(await page.getByRole('columnheader', { name: 'Interpretation', exact: true }).count(), 1);
  assert.equal(await page.locator('.markdown-document blockquote').count(), 1);
  assert.equal(await page.locator('.markdown-document pre code').innerText(), '<strong>This code stays literal.</strong>\n');
  assert.equal(await page.evaluate(() => window.rawDocumentRuns), undefined);
  assert.equal(await page.locator('.markdown-document img, .markdown-document script, .markdown-document iframe').count(), 0);
  assert.deepEqual(unexpectedImages, []);
  const link = page.locator('.markdown-document').getByRole('link', { name: /^A source/ });
  assert.equal(await link.getAttribute('href'), 'https://example.org/source');
  assert.equal(await link.getAttribute('rel'), 'noopener noreferrer');
  assert.equal(await page.locator('.markdown-document a').count(), 1);
  await page.getByRole('button', { name: 'Source', exact: true }).click();
  assert.equal(await page.locator('.document-source').textContent(), fixture.content);
  await page.getByRole('button', { name: 'Read', exact: true }).click();
  await page.locator('.composer textarea').fill('Keep this draft while I save and inspect the result.');
  await save(page, fixture);
  assert.equal(await page.locator('.composer textarea').inputValue(), 'Keep this draft while I save and inspect the result.');
  checks.push('Native approved Markdown renders headings, table, emphasis and literal code while source remains exact');
  checks.push('HTML and executable/relative links stay inert; images trigger no external request');
  checks.push('An actual browser download keeps the Unicode filename, complete bytes and original receipt digest');
  if (screenshots) {
    await mkdir(screenshots, { recursive: true });
    await page.locator('.artifact-open').scrollIntoViewIfNeeded();
    await page.screenshot({ path: join(screenshots, 'documents-desktop.png'), fullPage: true });
  }
  const original = await fetch(base + '/api/pages/' + firstId + '/artifacts').then(r => r.json());
  const artifactId = original.artifacts[0].id;
  await stop(server); server = start(); await ready();
  await page.reload();
  await page.locator('.artifact-open').filter({ hasText: fixture.path }).click();
  await page.locator('.markdown-document').getByRole('heading', { name: 'A field note', exact: true }).waitFor();
  await save(page, fixture);
  const reopened = await fetch(base + '/api/pages/' + firstId + '/artifacts').then(r => r.json());
  assert.equal(reopened.artifacts[0].id, artifactId);
  assert.equal((await fetch(base + '/__fixture/status').then(r => r.json())).requests, 0, 'Reading and saving do not replay a native effect');
  checks.push('Restart preserves the same artifact and file; preview and download cause no model calls');

  await page.setViewportSize({ width: 375, height: 812 });
  await page.locator('.markdown-document').scrollIntoViewIfNeeded();
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
  assert.equal(await page.locator('.markdown-table').evaluate(el => el.scrollWidth > el.clientWidth), true, 'Wide tables scroll inside their own region');
  await page.locator('.markdown-table').focus();
  await page.keyboard.press('ArrowRight');
  await wait(async () => page.locator('.markdown-table').evaluate(el => el.scrollLeft > 0), 'keyboard scrolls the wide table');
  if (screenshots) await page.screenshot({ path: join(screenshots, 'documents-mobile.png'), fullPage: true });
  await page.setViewportSize({ width: 1280, height: 900 });
  checks.push('The phone view retains readable content and a keyboard-reachable wide table without page clipping');

  await page.getByRole('button', { name: 'Settings', exact: true }).click();
  await page.locator('#interface-language').selectOption('zh-CN');
  await page.getByRole('button', { name: '关闭对话框', exact: true }).click();
  await page.getByRole('button', { name: '下载文件', exact: true }).waitFor();
  await page.getByRole('button', { name: '将完整文件加入主页草稿', exact: true }).waitFor();
  await page.getByRole('button', { name: '原文', exact: true }).click();
  assert.equal(await page.locator('.document-source').textContent(), fixture.content);
  await page.getByRole('button', { name: '设置', exact: true }).click();
  await page.locator('#interface-language').selectOption('en');
  await page.getByRole('button', { name: 'Close dialog', exact: true }).click();
  await page.getByRole('button', { name: 'Read', exact: true }).click();
  checks.push('Bilingual reading and saving controls preserve the authored file');

  // A confirmed native file is useful work, not just a download: prepare the
  // complete version in the existing Home draft without making a model call.
  await page.getByRole('button', { name: 'Add full file to Home draft', exact: true }).click();
  await page.waitForURL(base + '/');
  await page.locator('.composer-materials').getByText(fixture.path.split('/').at(-1)).waitFor();
  const homeText = await page.locator('.composer textarea').inputValue();
  assert.match(homeText, /Readable result/);
  assert.ok(homeText.includes(fixture.path));
  await page.locator('.composer-materials').getByText(fixture.path.split('/').at(-1)).waitFor();
  const homeFiles = await fetch(base + '/api/pages/home/materials').then(response => response.json());
  assert.equal(homeFiles.materials.length, 1);
  assert.equal(homeFiles.materials[0].sha256, fixture.sha256);
  assert.equal(homeFiles.materials[0].source, 'workspace_reference');
  assert.equal((await fetch(base + '/__fixture/status').then(r => r.json())).requests, 0,
    'Putting a verified file into an unsent Home draft cannot start another model round');
  await page.reload();
  await page.locator('.composer textarea').waitFor();
  assert.equal(await page.locator('.composer textarea').inputValue(), homeText);
  await page.goBack();
  await page.waitForURL('**/page/' + firstId);
  await page.getByRole('heading', { name: 'Readable result', exact: true }).waitFor();
  await wait(async () => await page.locator('.composer textarea').inputValue() ===
    'Keep this draft while I save and inspect the result.', 'source Work draft restored');
  await page.locator('.artifact-open').filter({ hasText: fixture.path }).click();
  await page.locator('.markdown-document').getByRole('heading', { name: 'A field note', exact: true }).waitFor();
  checks.push('Approved Work document becomes a version-bound, unsent Home attachment with source and both drafts preserved');
  // A different Work changes the real shared file while this preview remains open.
  const other = await context.newPage();
  await other.goto(base);
  await other.locator('.composer textarea').waitFor();
  const secondId = await create(other, 'A later result', 'WRITE_DOCUMENT_REVISION');
  await other.getByRole('button', { name: 'Allow once', exact: true }).click();
  await other.locator('.artifact-open').filter({ hasText: fixture.path }).click();
  await other.locator('.markdown-document').getByRole('heading', { name: 'A later version', exact: true }).waitFor();
  let downloadsAfterChange = 0;
  page.on('download', () => { ++downloadsAfterChange; });
  await page.getByRole('button', { name: 'Download file', exact: true }).click();
  await page.getByRole('alert').filter({ hasText: 'This file has changed' }).waitFor();
  assert.equal(downloadsAfterChange, 0);
  assert.equal(await page.getByText(/PRIVATE_LATER_WORK_RESULT/).count(), 0);
  const rejected = await fetch(base + '/api/pages/' + firstId + '/artifacts/' + encodeURIComponent(artifactId) + '/download');
  assert.equal(rejected.status, 409);
  assert.ok(!(await rejected.text()).includes('PRIVATE_LATER_WORK_RESULT'));
  await save(other, { path: fixture.path, content: fixture.revision, sha256: createHash('sha256').update(fixture.revision).digest('hex') });
  // The previously opened file is stale. Returning it to Home must not
  // create a new reference, navigate, or expose the later Work's private bytes.
  await page.getByRole('button', { name: 'Add full file to Home draft', exact: true }).click();
  await page.getByRole('alert').filter({ hasText: 'changed' }).waitFor();
  assert.equal(new URL(page.url()).pathname, '/page/' + firstId);
  assert.equal((await fetch(base + '/api/pages/home/materials').then(r => r.json())).materials.length, 1);
  checks.push('A replaced native file cannot be reattached or navigate Home from a stale preview');
  assert.notEqual(secondId, firstId);
  checks.push('Saving rechecks the native version after another Work changes the file; no later content leaks under the old result');
  assert.deepEqual(pageErrors, []);
  assert.deepEqual(unexpectedImages, []);
  assert.ok(networkErrors.every(error => error.status === 409 && (error.url.endsWith('/download') || error.url.endsWith('/materials/reference'))), JSON.stringify(networkErrors));
  console.log(JSON.stringify({ ok: true, checks, browserErrors: pageErrors, expectedVersionConflicts: networkErrors.length,
    provider: 'explicit synthetic HTTP choices; actual World/Kernel, approvals, file effects and browser downloads' }, null, 2));
} catch (error) {
  console.error(serverLog.slice(-7000));
  throw error;
} finally {
  if (browser) await browser.close();
  await stop(server);
  await rm(directory, { recursive: true, force: true });
}
