/** Actual disconnected material-to-Work journey; no model responses are invented.
 * Copyright 2026 Lightmaze Contributors. Licensed under Apache-2.0.
 */
import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { createHash } from 'node:crypto';
import { mkdtemp, mkdir, readdir, readFile, rm } from 'node:fs/promises';
import { createServer } from 'node:net';
import { tmpdir } from 'node:os';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { createRequire } from 'node:module';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '../..');
const require = createRequire(import.meta.url);
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const directory = await mkdtemp(join(tmpdir(), 'per-pulse-materials-'));
const screenshots = process.env.PER_PULSE_MATERIAL_SCREENSHOTS;
const socket = createServer();
await new Promise(done => socket.listen(0, '127.0.0.1', done));
const port = socket.address().port;
await new Promise(done => socket.close(done));
const base = 'http://127.0.0.1:' + port;
const checks = [];
const errors = [];
let serverLog = '';
let browser;
const env = { ...process.env };
for (const key of ['P_PULSE_CHAT_ENDPOINT', 'P_PULSE_CHAT_MODEL', 'P_PULSE_API_KEY', 'P_PULSE_CREDENTIAL_ENV']) delete env[key];
const start = () => {
  const child = spawn(process.env.PER_PULSE_PYTHON || join(root, '.venv/bin/python'), [
    '-m', 'per_pulse.cli', '--data-dir', directory, '--port', String(port),
  ], { cwd: root, env, stdio: ['ignore', 'pipe', 'pipe'] });
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
    try { if (await fn()) return; } catch { /* Wait for the actual state. */ }
    await new Promise(done => setTimeout(done, 80));
  }
  throw new Error('Timed out: ' + label + '\n' + serverLog);
};
const ready = () => wait(async () => (await fetch(base + '/api/health')).ok, 'service ready');
const files = () => readdir(join(directory, 'workspace/uploads')).catch(() => []);
let server = start();

try {
  await ready();
  assert.equal((await fetch(base + '/api/health').then(r => r.json())).providerConfigured, false);
  browser = await chromium.launch();
  const context = await browser.newContext({ viewport: { width: 1280, height: 900 }, locale: 'en-US' });
  const page = await context.newPage();
  page.setDefaultTimeout(12000);
  page.on('pageerror', error => errors.push(error.message));
  await page.goto(base);
  await page.locator('.composer textarea').waitFor();
  const create = async title => {
    await page.getByRole('button', { name: 'Create a page', exact: true }).click();
    await page.getByRole('textbox', { name: 'Page name', exact: true }).fill(title);
    await page.getByRole('textbox', { name: 'Where should we begin?', exact: true }).fill('Use a source, then keep the selected finding.');
    await page.getByRole('button', { name: 'Create page', exact: true }).click();
    await page.getByRole('heading', { name: title, exact: true }).waitFor();
    return new URL(page.url()).pathname.split('/').pop();
  };
  const open = async () => {
    await page.getByRole('button', { name: 'Materials', exact: true }).click();
    await page.getByRole('dialog', { name: 'Materials', exact: true }).waitFor();
    await wait(async () => !(await page.getByRole('button', { name: 'Refresh', exact: true }).isDisabled()), 'material listing');
  };
  const close = () => page.getByRole('button', { name: 'Close dialog', exact: true }).click();
  const shot = async name => {
    if (screenshots) { await mkdir(screenshots, { recursive: true }); await page.screenshot({ path: join(screenshots, name + '.png'), fullPage: true }); }
  };
  const firstId = await create('Field notes');
  await page.locator('.composer textarea').fill('Keep my question and its page link.');
  await page.getByRole('button', { name: 'Link a page', exact: true }).click();
  await page.getByRole('option', { name: /^Home\b/ }).click();
  const before = await page.locator('.composer textarea').inputValue();
  const source = '# Field notes\n\nA useful source stays available in the next Work.\n\n<script>window.materialExecuted = true</script>\n';
  const filename = 'A deliberately long field-note title about sources and continued work ' + 'long-name-'.repeat(8) + '.md';
  await open();
  await page.getByLabel('Choose a text file', { exact: true }).setInputFiles({ name: filename, mimeType: 'text/markdown', buffer: Buffer.from(source) });
  assert.equal((await files()).length, 0, 'Selecting a file must not upload it');
  await page.getByRole('button', { name: 'Upload file', exact: true }).click();
  await page.getByRole('button', { name: 'Add to draft', exact: true }).waitFor();
  await wait(async () => page.locator('.material-preview > h3').evaluate(el => document.activeElement === el), 'preview receives focus');
  await page.locator('.markdown-document').getByRole('heading', { name: 'Field notes', exact: true }).waitFor();
  await page.getByRole('button', { name: 'Source', exact: true }).click();
  assert.equal(await page.locator('.material-preview pre').innerText(), source);
  assert.equal(await page.evaluate(() => window.materialExecuted), undefined, 'Material text is never executed as HTML');
  const [storedName] = await files();
  const path = 'uploads/' + storedName;
  assert.equal(await readFile(join(directory, 'workspace', path), 'utf8'), source);
  await page.getByRole('button', { name: 'Read', exact: true }).click();
  await shot('materials-desktop');
  await page.getByRole('button', { name: 'Add to draft', exact: true }).click();
  assert.equal(await page.locator('.composer textarea').inputValue(), before);
  await page.getByRole('button', { name: `Remove ${filename} from this message`, exact: true }).waitFor();
  assert.equal(await page.locator('.reference-count').innerText(), '1');
  await wait(async () => page.locator('.composer textarea').evaluate(el => document.activeElement === el), 'composer receives focus after native attachment');
  await page.reload();
  await page.locator('.composer textarea').waitFor();
  await page.getByRole('button', { name: `Remove ${filename} from this message`, exact: true }).waitFor();
  await page.getByRole('button', { name: 'Send message', exact: true }).click();
  await wait(async () => (await fetch(base + '/api/pages/' + firstId).then(r => r.json())).messages.some(m => m.content.includes(path)), 'native saved source reference');
  const first = await fetch(base + '/api/pages/' + firstId).then(r => r.json());
  assert.equal(first.messages.at(-1).references[0].targetPageId, 'home');
  assert.equal(first.messages.at(-1).role, 'user');
  assert.equal(first.messages.at(-1).materials[0].sha256, createHash('sha256').update(source).digest('hex'));
  assert.equal(first.messages.at(-1).materials[0].path, path);
  assert.equal(first.provider.configured, false);
  checks.push('Explicit upload, escaped preview, native versioned attachment, existing draft and page link survive reload and send');

  await page.getByLabel('Workspace navigation').getByRole('link', { name: 'Home', exact: true }).click();
  const secondId = await create('Another reading');
  await page.locator('.composer textarea').fill('A different question about the same source.');
  await open();
  await page.locator('.material-list').getByRole('button', { name: 'uploads/', exact: true }).click();
  await page.locator('.material-list button').filter({ hasText: storedName }).click();
  await page.getByRole('button', { name: 'Add to draft', exact: true }).click();
  assert.ok((await page.locator('.composer textarea').inputValue()).startsWith('A different question about the same source.'));
  assert.equal((await files()).length, 1, 'The second Work reuses the file without uploading again');
  assert.equal(await page.getByText('Keep my question and its page link.', { exact: true }).count(), 0);
  checks.push('A second Work reuses the native file without another upload or copying the first conversation');

  await open();
  const picker = page.getByLabel('Choose a text file', { exact: true });
  await picker.setInputFiles({ name: 'empty.txt', mimeType: 'text/plain', buffer: Buffer.alloc(0) });
  await page.getByRole('button', { name: 'Upload file', exact: true }).click();
  await page.getByRole('alert').filter({ hasText: 'This file is empty.' }).waitFor();
  await picker.setInputFiles({ name: 'large.txt', mimeType: 'text/plain', buffer: Buffer.alloc(1024 * 1024 + 1, 97) });
  await page.getByRole('button', { name: 'Upload file', exact: true }).click();
  await page.getByRole('alert').filter({ hasText: 'no larger than 1 MiB' }).waitFor();
  await picker.setInputFiles({ name: 'binary.txt', mimeType: 'text/plain', buffer: Buffer.from([97, 0, 98]) });
  await page.getByRole('button', { name: 'Upload file', exact: true }).click();
  await page.getByRole('alert').filter({ hasText: 'UTF-8 text file' }).waitFor();
  assert.equal((await files()).length, 1);
  await close();
  checks.push('Empty, oversized and binary files show specific errors without creating files');

  // Leave a read pending, dismiss it and navigate before its response arrives.
  let release;
  let held;
  const gate = new Promise(done => { release = done; });
  const pending = new Promise(done => { held = done; });
  const pattern = '**/api/workspace/files/content?*';
  await page.route(pattern, async route => {
    const response = await route.fetch();
    held(); await gate;
    await route.fulfill({ response }).catch(() => {});
  });
  await open();
  await page.locator('.material-list').getByRole('button', { name: 'uploads/', exact: true }).click();
  await page.locator('.material-list button').filter({ hasText: storedName }).click();
  await pending;
  await page.keyboard.press('Escape');
  await page.getByLabel('Workspace navigation').getByRole('link', { name: 'Field notes', exact: true }).click();
  await page.locator('.composer textarea').fill('This draft must stay in the first Work.');
  release();
  await page.unroute(pattern);
  assert.equal(await page.getByRole('dialog').count(), 0);
  assert.equal(await page.locator('.composer textarea').inputValue(), 'This draft must stay in the first Work.');
  checks.push('A late file read cannot reopen the dialog or change another Work draft');

  const oldIdentity = await fetch(base + '/api/bootstrap').then(r => r.json());
  await stop(server);
  server = start();
  await ready();
  const stale = await fetch(base + '/api/workspace/files', { headers: {
    'X-Per-Pulse-World': oldIdentity.workspaceId, 'X-Per-Pulse-Service-Instance': oldIdentity.serviceInstanceId,
  } });
  assert.equal(stale.status, 409);
  await page.reload();
  await page.locator('.composer textarea').waitFor();
  assert.equal(await page.locator('.composer textarea').inputValue(), 'This draft must stay in the first Work.');
  await page.setViewportSize({ width: 375, height: 812 });
  await open();
  await page.locator('.material-list').getByRole('button', { name: 'uploads/', exact: true }).click();
  await page.locator('.material-list button').filter({ hasText: storedName }).click();
  await page.getByRole('button', { name: 'Add to draft', exact: true }).waitFor();
  await page.getByRole('button', { name: 'Source', exact: true }).click();
  assert.equal(await page.locator('.material-preview pre').innerText(), source);
  await page.getByRole('button', { name: 'Read', exact: true }).click();
  await page.locator('.markdown-document').getByRole('heading', { name: 'Field notes', exact: true }).waitFor();
  const bounds = await page.getByRole('dialog').boundingBox();
  assert.ok(bounds.x >= 0 && bounds.x + bounds.width <= 375);
  assert.equal(await page.getByRole('dialog').evaluate(el => el.scrollWidth <= el.clientWidth), true, 'Long file names must wrap on a phone');
  const visiblePreview = await page.locator('.markdown-document').boundingBox();
  const closeButton = await page.getByRole('button', { name: 'Close dialog', exact: true }).boundingBox();
  assert.ok(visiblePreview.y + Math.min(visiblePreview.height, 160) < bounds.y + bounds.height,
    'Opening a material must reveal its content, not just focus a heading near the bottom edge');
  assert.ok(closeButton.y >= bounds.y && closeButton.y + closeButton.height <= bounds.y + bounds.height,
    'The dialog close control remains visible while the material body scrolls');
  await shot('materials-mobile');
  await page.keyboard.press('Escape');
  assert.equal(await page.getByRole('button', { name: 'Materials', exact: true }).evaluate(el => document.activeElement === el), true);
  await page.setViewportSize({ width: 1280, height: 900 });
  checks.push('Native file and draft survive service restart; phone layout and Escape focus work');

  await page.locator(`[data-message-id="${first.messages.at(-1).id}"]`).getByRole('button', { name: filename, exact: true }).click();
  const originalDialog = page.getByRole('dialog', { name: 'Source files', exact: true });
  const savedSource = page.waitForEvent('download');
  await originalDialog.getByRole('button', { name: 'Download original', exact: true }).click();
  const downloadedSource = await savedSource;
  assert.equal(downloadedSource.suggestedFilename(), filename);
  await downloadedSource.saveAs(join(directory, 'exported-source.md'));
  assert.equal(await readFile(join(directory, 'exported-source.md'), 'utf8'), source);
  assert.equal(await page.evaluate(() => window.materialExecuted), undefined);
  await close();
  assert.equal(await page.locator('.composer textarea').inputValue(), 'This draft must stay in the first Work.');
  checks.push('The original source downloads after a disconnected restart with its original name, exact bytes and retained draft');

  await page.getByRole('button', { name: 'Settings', exact: true }).click();
  await page.locator('#interface-language').selectOption('zh-CN');
  await page.getByRole('button', { name: '关闭对话框', exact: true }).click();
  await page.getByRole('button', { name: '材料', exact: true }).click();
  await page.getByRole('dialog', { name: '材料', exact: true }).waitFor();
  await page.getByText('工作区文件', { exact: true }).waitFor();
  await page.keyboard.press('Escape');
  await page.getByRole('button', { name: '设置', exact: true }).click();
  await page.locator('#interface-language').selectOption('en');
  await close();
  checks.push('Chinese controls use the same material flow without changing authored drafts');

  const finding = 'A useful source stays available in the next Work.';
  await page.locator('.composer textarea').fill(finding + '\nSource: ' + path);
  await page.getByRole('button', { name: 'Send message', exact: true }).click();
  await page.locator('.message').filter({ hasText: finding }).last().getByRole('button', { name: 'Bring to Home', exact: true }).click();
  await page.locator('#return-content').fill(finding);
  await page.getByRole('dialog').getByRole('button', { name: 'Bring to Home', exact: true }).click();
  await page.getByText(finding, { exact: true }).waitFor();
  const home = await fetch(base + '/api/pages/home').then(r => r.json());
  assert.equal(home.messages.at(-1).content, finding);
  assert.equal(home.messages.at(-1).sourcePageId, firstId);
  assert.ok(home.messages.at(-1).sourceMessageId);
  assert.equal(home.messages.some(m => m.content.includes('A different question')), false);
  assert.equal((await fetch(base + '/api/pages/' + secondId).then(r => r.json())).messages.length, 1);
  checks.push('An authored selected finding returns Home with its exact original Work and message source');
  assert.deepEqual(errors, []);
  console.log(JSON.stringify({ ok: true, checks, browserErrors: errors, provider: 'disconnected', source: 'synthetic authored material' }, null, 2));
} catch (error) {
  console.error(serverLog.slice(-7000));
  throw error;
} finally {
  if (browser) await browser.close();
  await stop(server);
  await rm(directory, { recursive: true, force: true });
}
