/** Actual upload, native passage reading, approval and report download.
 * Provider choices are scripted; this does not establish model competence.
 * Copyright 2026 Lightmaze Contributors. Licensed under Apache-2.0.
 */
import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { createHash } from 'node:crypto';
import { mkdtemp, mkdir, readFile, writeFile, readdir, rm } from 'node:fs/promises';
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
if (screenshots) await mkdir(screenshots, { recursive: true });
const socket = createServer();
await new Promise(resolve => socket.listen(0, '127.0.0.1', resolve));
const port = socket.address().port;
await new Promise(resolve => socket.close(resolve));
const base = 'http://127.0.0.1:' + port;
const filename = 'Café 库存.csv';
const bytes = Buffer.from('\ufeffname,quantity,unit_price\r\n' + 'Café supplies,3,2.50\r\n'.repeat(300));
const checks = [], errors = [];
let serverLog = '', browser, page;
const start = (offline = false) => {
  const child = spawn(process.env.PER_PULSE_PYTHON || join(root, '.venv/bin/python'), [
    join(root, 'tests/browser/material_server.py'), '--data-dir', directory, '--port', String(port),
    ...(offline ? ['--offline'] : []),
  ], { cwd: root, stdio: ['ignore', 'pipe', 'pipe'] });
  child.stdout.on('data', data => { serverLog += data; });
  child.stderr.on('data', data => { serverLog += data; });
  return child;
};
const stop = async child => {
  if (child.exitCode !== null || child.signalCode !== null) return;
  const closed = new Promise(resolve => child.once('exit', resolve));
  const force = setTimeout(() => child.kill('SIGKILL'), 10000);
  child.kill('SIGTERM'); await closed; clearTimeout(force);
};
const wait = async (fn, label) => {
  const end = Date.now() + 20000;
  while (Date.now() < end) {
    try { if (await fn()) return; } catch {}
    await new Promise(resolve => setTimeout(resolve, 80));
  }
  throw new Error('Timed out: ' + label + '\n' + serverLog);
};
const data = path => fetch(base + path).then(r => { assert.ok(r.ok, path); return r.json(); });
const view = id => data('/api/pages/' + id);
let server = start();
try {
  await wait(async () => (await fetch(base + '/api/health')).ok, 'service ready');
  browser = await chromium.launch({ executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE || undefined,
    args: process.env.PLAYWRIGHT_CHROMIUM_ARGS ? JSON.parse(process.env.PLAYWRIGHT_CHROMIUM_ARGS) : undefined });
  const context = await browser.newContext({ viewport: { width: 1280, height: 900 }, acceptDownloads: true,
    permissions: ['clipboard-read', 'clipboard-write'] });
  page = await context.newPage();
  page.on('pageerror', error => errors.push(error.message));
  await page.goto(base);
  const close = () => page.getByRole('button', { name: /^(Close dialog|关闭对话框)$/ }).click();
  const dialog = () => page.getByRole('dialog', { name: /^(Materials|Source files)$/ });
  const input = () => page.getByRole('textbox', { name: 'Message Inventory review', exact: true });
  const attach = () => page.getByRole('button', { name: 'Materials', exact: true }).click();
  const choose = (name, buffer, mimeType = 'text/plain') => dialog().getByLabel('Choose a text file', { exact: true }).setInputFiles({ name, mimeType, buffer });
  await page.getByRole('button', { name: 'Create a page', exact: true }).click();
  await page.getByRole('textbox', { name: 'Page name', exact: true }).fill('Inventory review');
  await page.getByRole('textbox', { name: 'Where should we begin?', exact: true }).fill('Prepare to inspect a supplied CSV. [Explicit synthetic fixture]');
  await page.getByRole('button', { name: 'Create page', exact: true }).click();
  await page.getByRole('heading', { name: 'Inventory review', exact: true }).waitFor();
  const origin = new URL(page.url()).pathname.split('/').pop();
  await wait(async () => (await view(origin)).messages.some(m => m.role === 'assistant'), 'first native reply');
  const before = await view(origin);
  const requestsBefore = (await data('/__materials/evidence')).requests;
  const draft = 'PROCESS_ATTACHED_CSV — inspect the original bytes 🌍';
  await input().fill(draft);
  await attach(); await choose(filename, bytes, 'text/csv');
  await dialog().getByRole('button', { name: 'Upload file', exact: true }).click();
  await dialog().getByRole('button', { name: 'Add to draft', exact: true }).click();
  await page.getByRole('button', { name: `Remove ${filename} from this message`, exact: true }).waitFor();
  const material = (await data('/api/pages/' + origin + '/materials')).materials[0];
  assert.equal(material.name, filename);
  assert.equal(material.sha256, createHash('sha256').update(bytes).digest('hex'));
  assert.deepEqual(await readFile(join(directory, 'workspace', material.path)), bytes);
  assert.equal((await view(origin)).messages.length, before.messages.length);
  assert.equal((await data('/__materials/evidence')).requests, requestsBefore);
  await page.reload();
  await page.getByRole('button', { name: `Remove ${filename} from this message`, exact: true }).waitFor();
  assert.equal(await input().inputValue(), draft);
  checks.push('the browser uploads original BOM/CRLF/Unicode bytes without submitting or calling a model; the file and text draft survive refresh');

  await page.getByRole('button', { name: 'Link a page', exact: true }).click();
  const picker = page.getByRole('dialog', { name: 'Link a page', exact: true });
  await picker.getByRole('option', { name: /Home/ }).click();
  await page.getByRole('button', { name: `Remove ${filename} from this message`, exact: true }).waitFor();
  assert.ok((await input().inputValue()).includes(draft));
  if (screenshots) await page.screenshot({ path: join(screenshots, 'materials-draft.png') });
  await page.getByRole('button', { name: 'Send message', exact: true }).click();
  await page.getByRole('button', { name: 'Allow once', exact: true }).waitFor();
  const evidence = await data('/__materials/evidence');
  assert.ok(evidence.reads[0].passages > 1);
  assert.equal(evidence.reads[0].content, bytes.subarray(3).toString('utf8'));
  await assert.rejects(readFile(join(directory, 'workspace', evidence.reads[0].path)));
  await page.getByRole('button', { name: 'Allow once', exact: true }).click();
  await page.getByText('Inventory total: 2250.00.', { exact: true }).waitFor();
  const completed = await view(origin);
  const original = completed.messages.find(m => m.materials?.some(item => item.id === material.id));
  const article = page.locator('[data-message-id="' + original.id + '"]');
  assert.ok(!(await article.locator('.message-content').textContent()).includes('[Attached workspace materials]'));
  assert.ok((await article.locator('.message-content').textContent()).includes('🌍'));
  await article.getByRole('button', { name: 'Copy original message', exact: true }).click();
  await wait(async () => await page.evaluate(() => navigator.clipboard.readText()) === original.content, 'raw input copy');
  const artifacts = page.getByRole('region', { name: 'Files from this page', exact: true });
  await artifacts.getByRole('button', { name: new RegExp(evidence.reads[0].path.replace('.', '\\.')) }).click();
  const downloaded = page.waitForEvent('download');
  await artifacts.getByRole('button', { name: 'Download file', exact: true }).click();
  const download = await downloaded;
  await download.saveAs(join(directory, 'downloaded.md'));
  assert.equal(await readFile(join(directory, 'downloaded.md'), 'utf8'), evidence.reads[0].report);
  assert.deepEqual(await readFile(join(directory, 'workspace', material.path)), bytes);
  if (screenshots) await page.screenshot({ path: join(screenshots, 'materials-report.png') });
  checks.push('native reading consumes the supplied version across actual passages; a report is written only after approval and downloads exactly');

  const retainedDraft = 'Keep this draft while inspecting a saved source.';
  await input().fill(retainedDraft);
  const report = completed.messages.at(-1);
  await page.locator('[data-message-id="' + report.id + '"]').getByRole('button', { name: 'Save example', exact: true }).click();
  const exampleEditor = page.getByRole('dialog', { name: 'Save example', exact: true });
  await exampleEditor.getByRole('textbox', { name: 'Example name', exact: true }).fill('Inventory source review');
  await exampleEditor.getByRole('textbox', { name: 'Original passage', exact: true }).fill('Inventory total: 2250.00.');
  await exampleEditor.getByRole('button', { name: 'Save example', exact: true }).click();
  const learning = page.getByRole('dialog', { name: 'Lessons and examples', exact: true });
  await learning.getByRole('heading', { name: 'Inventory source review', exact: true }).waitFor();
  await learning.getByText('Source experience', { exact: true }).click();
  assert.ok(!(await learning.textContent()).includes('[Attached workspace materials]'));
  assert.ok((await learning.textContent()).includes(filename));
  await learning.getByRole('button', { name: 'Open source message', exact: true }).click();
  await wait(() => page.url().endsWith('#message-' + original.id), 'experience returns to its actual file input');
  assert.equal(await input().inputValue(), retainedDraft);
  checks.push('source experience shows readable text and original file-message access without rewriting the raw encounter or losing the draft');

  await article.getByRole('button', { name: filename, exact: true }).click();
  await dialog().getByRole('region', { name: 'Source file preview', exact: true }).waitFor();
  await dialog().getByRole('button', { name: 'Source', exact: true }).click();
  assert.equal(await dialog().locator('.document-source').textContent(), bytes.toString('utf8'));
  await close();
  await article.getByRole('button', { name: 'New page', exact: true }).click();
  const newPage = page.getByRole('dialog', { name: 'Give this a little room.', exact: true });
  await newPage.getByText('This starts with the message text. Its files remain available from the linked source message.', { exact: true }).waitFor();
  assert.ok(!(await newPage.getByRole('textbox', { name: 'Where should we begin?', exact: true }).inputValue()).includes('[Attached workspace materials]'));
  await close();
  await article.getByRole('button', { name: 'Bring to Home', exact: true }).click();
  const bring = page.getByRole('dialog', { name: 'Bring this back to Home.', exact: true });
  assert.ok(await bring.getByLabel('Include the attached source files', { exact: true }).isChecked());
  const editor = bring.getByRole('textbox', { name: 'What would you like to bring back?', exact: true });
  assert.ok(!(await editor.inputValue()).includes('[Attached workspace materials]'));
  await editor.fill('Review this inventory source with me.');
  await bring.getByRole('button', { name: 'Bring to Home', exact: true }).click();
  await page.getByRole('heading', { name: 'A place to keep going.', exact: true }).waitFor();
  const home = (await view('home')).messages.find(m => m.sourceMessageId === original.id);
  assert.deepEqual(home.materials, [material]);
  await page.locator('[data-message-id="' + home.id + '"]').getByRole('button', { name: filename, exact: true }).click();
  await dialog().getByText('Inspect the original file from the source page. Its upload and message stay in that page.', { exact: true }).waitFor();
  assert.equal(await dialog().getByLabel('Choose a text file', { exact: true }).count(), 0);
  assert.equal(await dialog().getByRole('button', { name: 'Add to draft', exact: true }).count(), 0);
  await dialog().getByRole('button', { name: 'Source', exact: true }).click();
  assert.equal(await dialog().locator('.document-source').textContent(), bytes.toString('utf8'));
  await close();
  checks.push('bringing source files Home is explicit and keeps previews attached to their original page without silently transferring upload ownership');

  await page.goto(base + '/page/' + origin);
  await attach(); await choose('Unconfirmed.txt', Buffer.from('One upload, one file.\n'));
  let interrupted = false;
  const uploadUrl = '**/api/pages/' + origin + '/materials?*';
  await page.route(uploadUrl, async route => {
    if (route.request().method() !== 'POST' || interrupted) return route.continue();
    interrupted = true;
    const actual = await route.fetch();
    assert.equal(actual.status(), 201);
    await route.abort('failed');
  });
  await dialog().getByRole('button', { name: 'Upload file', exact: true }).click();
  await dialog().getByRole('alert').getByText('The upload could not be confirmed. Open the uploads folder and check before trying again.', { exact: true }).waitFor();
  await page.unroute(uploadUrl);
  await dialog().locator('.material-list').getByRole('button', { name: 'uploads/', exact: true }).click();
  await dialog().locator('.material-list button').filter({ hasText: 'Unconfirmed.txt' }).click();
  await dialog().getByRole('button', { name: 'Add to draft', exact: true }).click();
  assert.equal((await data('/api/pages/' + origin + '/materials')).materials.length, 2);
  assert.equal((await readdir(join(directory, 'workspace', 'uploads'))).length, 2);
  checks.push('a dropped successful upload response remains uncertain and can be recovered from the actual saved file without automatic duplication');

  await page.getByRole('button', { name: 'Remove Unconfirmed.txt from this message', exact: true }).click();
  await attach(); await choose('binary.bin', Buffer.from([0xff, 0xfe, 0x00]), 'application/octet-stream');
  await dialog().getByRole('button', { name: 'Upload file', exact: true }).click();
  await dialog().getByRole('alert').waitFor();
  assert.equal((await data('/api/pages/' + origin + '/materials')).materials.length, 2);
  await choose('too-large.txt', Buffer.alloc(1024 * 1024 + 1, 97));
  await dialog().getByRole('button', { name: 'Upload file', exact: true }).click();
  await dialog().getByText('Choose a text file no larger than 1 MiB.', { exact: true }).waitFor();
  await close();
  await attach();
  await dialog().locator('.material-list').getByRole('button', { name: 'uploads/', exact: true }).click();
  await dialog().locator('.material-list button').filter({ hasText: 'Unconfirmed.txt' }).click();
  await dialog().getByRole('button', { name: 'Add to draft', exact: true }).click();
  await dialog().waitFor({ state: 'hidden' });
  await page.getByRole('button', { name: 'Remove Unconfirmed.txt from this message', exact: true }).waitFor();
  await input().fill('Keep this draft if its file has changed.');
  const other = (await data('/api/pages/' + origin + '/materials')).materials.find(item => item.name === 'Unconfirmed.txt');
  await writeFile(join(directory, 'workspace', other.path), 'REPLACEMENT_BYTES_NOT_SELECTED');
  const beforeRejected = (await view(origin)).messages.length;
  await page.getByRole('button', { name: 'Send message', exact: true }).click();
  await page.getByText('This source file changed. Upload the version you want to use.', { exact: true }).waitFor();
  assert.equal((await view(origin)).messages.length, beforeRejected);
  assert.equal(await input().inputValue(), 'Keep this draft if its file has changed.');
  await page.getByRole('button', { name: 'Remove Unconfirmed.txt from this message', exact: true }).waitFor();
  checks.push('unsupported and oversized files do not create receipts; a changed selected version cannot be submitted and the draft remains editable');

  await page.getByRole('button', { name: 'Remove Unconfirmed.txt from this message', exact: true }).click();
  await input().fill('');
  await attach(); await choose('Only a source.md', Buffer.from('# Source only\n\nA durable file-only draft.\n'));
  await dialog().getByRole('button', { name: 'Upload file', exact: true }).click();
  await dialog().getByRole('button', { name: 'Add to draft', exact: true }).click();
  await page.getByRole('button', { name: 'Remove Only a source.md from this message', exact: true }).waitFor();
  await stop(server); server = start(true);
  await wait(async () => (await fetch(base + '/api/health')).ok, 'offline restart');
  await page.reload();
  await page.getByRole('button', { name: 'Remove Only a source.md from this message', exact: true }).waitFor();
  assert.equal(await input().inputValue(), '');
  assert.ok(await page.getByRole('button', { name: 'Send message', exact: true }).isEnabled());
  await page.getByRole('button', { name: 'Send message', exact: true }).click();
  await wait(async () => (await view(origin)).messages.at(-1).materials?.[0]?.name === 'Only a source.md', 'file-only message');
  const fileOnly = (await view(origin)).messages.at(-1);
  await page.locator('[data-message-id="' + fileOnly.id + '"]').getByRole('button', { name: 'Bring to Home', exact: true }).click();
  assert.equal(await editor.inputValue(), '');
  assert.ok(await bring.getByRole('button', { name: 'Bring to Home', exact: true }).isEnabled());
  await bring.getByRole('button', { name: 'Bring to Home', exact: true }).click();
  await wait(async () => (await view('home')).messages.at(-1).sourceMessageId === fileOnly.id, 'file-only return');
  checks.push('restart without a provider preserves a file-only draft; it can be sent and brought Home without inventing user text');

  await page.setViewportSize({ width: 390, height: 844 });
  await page.getByRole('button', { name: 'Toggle navigation', exact: true }).click();
  await page.getByRole('button', { name: 'Settings', exact: true }).click();
  await page.locator('#interface-language').selectOption('zh-CN');
  await close();
  await page.getByRole('button', { name: 'Only a source.md', exact: true }).click();
  await page.getByRole('dialog', { name: '来源文件', exact: true }).waitFor();
  await page.getByLabel('来源文件文本', { exact: true }).waitFor();
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
  await page.getByRole('button', { name: '原文', exact: true }).click();
  assert.equal(await page.locator('.document-source').textContent(), '# Source only\n\nA durable file-only draft.\n');
  if (screenshots) await page.screenshot({ path: join(screenshots, 'materials-mobile-chinese.png') });
  checks.push('the Chinese phone view preserves original source text and fits within the viewport');
  assert.deepEqual(errors, []);
  console.log(JSON.stringify({ count: checks.length, checks, errors, modelEvidence: 'explicit_synthetic_transport_only' }, null, 2));
} catch (error) {
  if (screenshots && page) await page.screenshot({ path: join(screenshots, 'materials-failure.png') }).catch(() => {});
  throw error;
} finally {
  await browser?.close(); await stop(server);
  await rm(directory, { recursive: true, force: true });
}
