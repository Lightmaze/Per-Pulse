/** Downloads on the real native file/approval path; provider choices are fixtures.
 * Copyright 2026 Lightmaze Contributors. Licensed under Apache-2.0.
 */
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { spawn } from 'node:child_process';
import { mkdtemp, mkdir, readFile, rm } from 'node:fs/promises';
import { createServer } from 'node:net';
import { tmpdir } from 'node:os';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { createRequire } from 'node:module';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '../..');
const require = createRequire(import.meta.url);
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const directory = await mkdtemp(join(tmpdir(), 'per-pulse-download-'));
const screenshots = process.env.PER_PULSE_DOWNLOAD_SCREENSHOTS;
if (screenshots) await mkdir(screenshots, { recursive: true });
const socket = createServer();
await new Promise(resolve => socket.listen(0, '127.0.0.1', resolve));
const port = socket.address().port;
await new Promise(resolve => socket.close(resolve));
const base = 'http://127.0.0.1:' + port;
const filename = 'Café 100% 文本.txt';
const expected = Buffer.from('\ufeffFINAL\r\n' + 'A fixture line: café 文本.\r\n'.repeat(1400));
let log = '', browser, page;
const server = spawn(process.env.PER_PULSE_PYTHON || join(root, '.venv/bin/python'), [
  join(root, 'tests/browser/fixture_server.py'),
], { cwd: root, env: { ...process.env, PER_PULSE_QA_PORT: String(port) }, stdio: ['ignore', 'pipe', 'pipe'] });
server.stdout.on('data', value => { log += value; });
server.stderr.on('data', value => { log += value; });
const checks = [], errors = [];
const wait = async (fn, label) => {
  const end = Date.now() + 20000;
  while (Date.now() < end) {
    try { if (await fn()) return; } catch {}
    await new Promise(resolve => setTimeout(resolve, 80));
  }
  throw new Error('Timed out: ' + label + '\n' + log);
};
try {
  await wait(async () => (await fetch(base + '/api/health')).ok, 'service ready');
  browser = await chromium.launch({ executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE || undefined,
    args: process.env.PLAYWRIGHT_CHROMIUM_ARGS ? JSON.parse(process.env.PLAYWRIGHT_CHROMIUM_ARGS) : undefined });
  const context = await browser.newContext({ viewport: { width: 1280, height: 900 }, acceptDownloads: true });
  page = await context.newPage();
  page.on('pageerror', error => errors.push(error.message));
  let downloadCount = 0, executedHtml = 0;
  page.on('download', () => { downloadCount++; });
  page.on('request', request => { if (request.url().endsWith('/__fixture/should-not-run')) executedHtml++; });
  await page.goto(base);
  const create = async (title, instruction) => {
    await page.getByRole('button', { name: 'Create a page', exact: true }).click();
    await page.getByRole('textbox', { name: 'Page name', exact: true }).fill(title);
    await page.getByRole('textbox', { name: 'Where should we begin?', exact: true }).fill(instruction);
    await page.getByRole('button', { name: 'Create page', exact: true }).click();
    await page.getByRole('heading', { name: title, exact: true }).waitFor();
    await page.getByRole('button', { name: 'Allow once', exact: true }).click();
    await page.getByRole('region', { name: 'Files from this page', exact: true }).waitFor();
    return new URL(page.url()).pathname.split('/').pop();
  };
  const origin = await create('Report to take away', 'WRITE_DOWNLOAD');
  await page.getByRole('button', { name: new RegExp(filename.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')) }).click();
  const artifacts = page.getByRole('region', { name: 'Files from this page', exact: true });
  await artifacts.getByRole('button', { name: 'Download file', exact: true }).waitFor();
  await page.getByRole('textbox', { name: 'Message Report to take away', exact: true }).fill('Keep my draft while taking this file away.');
  const completed = page.waitForEvent('download');
  await artifacts.getByRole('button', { name: 'Download file', exact: true }).click();
  const download = await completed;
  assert.equal(download.suggestedFilename(), filename);
  const destination = join(directory, 'downloaded.txt');
  await download.saveAs(destination);
  assert.deepEqual(await readFile(destination), expected);
  const originalView = await fetch(base + '/api/pages/' + origin).then(r => r.json());
  const artifact = originalView.artifacts[0];
  assert.equal(createHash('sha256').update(await readFile(destination)).digest('hex'), artifact.sha256);
  await artifacts.getByText('Download started', { exact: true }).waitFor();
  assert.equal(await page.getByRole('textbox', { name: 'Message Report to take away', exact: true }).inputValue(), 'Keep my draft while taking this file away.');
  if (screenshots) await page.screenshot({ path: join(screenshots, 'download-desktop.png') });
  checks.push('native file download preserves complete UTF-8/BOM/CRLF bytes, original filename and unsent draft');

  // Simulate a damaged response while retaining the valid expected version
  // header: the client must verify the received bytes before initiating save.
  const url = '**/api/pages/' + origin + '/artifacts/' + encodeURIComponent(artifact.id) + '/download';
  let corruptedResponse = false;
  await page.route(url, async route => {
    const response = await route.fetch();
    corruptedResponse = true;
    await route.fulfill({ response, body: 'CORRUPTED_SYNTHETIC_RESPONSE' });
  });
  await artifacts.getByRole('button', { name: 'Download file', exact: true }).click();
  await wait(() => corruptedResponse, 'deliberately corrupted download response');
  await artifacts.getByRole('alert').getByText('The downloaded file did not match this recorded version. Refresh the preview and try again.', { exact: true }).waitFor();
  assert.equal(downloadCount, 1);
  await page.unroute(url);
  checks.push('a response with mismatched bytes never starts a browser download');

  // A pending browser request must not create a late download after leaving
  // the source Work. The native read is harmless and can still finish.
  let held = false, release = () => {};
  const blocked = new Promise(resolve => { release = resolve; });
  await page.route(url, async route => {
    const response = await route.fetch(); held = true;
    await blocked;
    try { await route.fulfill({ response }); } catch { /* Navigation cancelled the old read. */ }
  });
  await artifacts.getByRole('button', { name: 'Download file', exact: true }).click();
  await wait(() => held, 'held download response');
  await page.locator('aside.sidebar').getByRole('link', { name: 'Home', exact: true }).click();
  await page.getByRole('textbox', { name: 'Message Home', exact: true }).waitFor();
  release(); await page.unrouteAll({ behavior: 'wait' });
  // Returning immediately exercises an old completion racing a fresh view.
  await page.locator('aside.sidebar').getByRole('link', { name: 'Report to take away', exact: true }).click();
  await page.getByRole('textbox', { name: 'Message Report to take away', exact: true }).waitFor();
  assert.equal(downloadCount, 1);
  checks.push('navigation cancels a pending file save without sending it into another Work');

  await artifacts.getByRole('button', { name: new RegExp('Café 100%') }).click();
  await artifacts.getByRole('button', { name: 'Download file', exact: true }).waitFor();
  assert.equal((await fetch(base + '/__fixture/replace-download', { method: 'POST' })).status, 200);
  await artifacts.getByRole('button', { name: 'Download file', exact: true }).click();
  await artifacts.getByRole('alert').getByText('This file has changed since this page wrote it. Its current contents are not the recorded version.', { exact: true }).waitFor();
  assert.equal(downloadCount, 1);
  assert.equal(await page.getByText('PRIVATE_LATER_WRITER', { exact: false }).count(), 0);
  await artifacts.getByRole('button', { name: 'Refresh file preview', exact: true }).click();
  await artifacts.getByRole('status').getByText('This file has changed since this page wrote it. Its current contents are not the recorded version.', { exact: true }).waitFor();
  assert.equal(await artifacts.getByRole('button', { name: 'Download file', exact: true }).count(), 0);
  checks.push('changing the file after preview refuses the old-version download and exposes no replacement content');

  await create('HTML as a file', 'WRITE_HTML');
  await page.getByRole('button', { name: /preview\.html/ }).click();
  const htmlDownload = page.waitForEvent('download');
  await artifacts.getByRole('button', { name: 'Download file', exact: true }).click();
  assert.equal((await htmlDownload).suggestedFilename(), 'preview.html');
  assert.equal(executedHtml, 0);
  checks.push('HTML remains inert in the preview and is delivered only as an attachment');

  await create('Sensitive report', 'WRITE_REDACTED');
  await page.getByRole('button', { name: /redacted\.txt/ }).click();
  await artifacts.getByText('Sensitive values have been hidden.', { exact: true }).waitFor();
  assert.equal(await artifacts.getByRole('button', { name: 'Download file', exact: true }).count(), 0);
  await page.setViewportSize({ width: 390, height: 844 });
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
  if (screenshots) await page.screenshot({ path: join(screenshots, 'download-mobile.png') });
  checks.push('redacted previews offer no original download and phone layout stays within the viewport');
  assert.deepEqual(errors, []);
  console.log(JSON.stringify({ checks, count: checks.length, errors, modelEvidence: 'explicit_synthetic_transport_only' }, null, 2));
} catch (error) {
  if (screenshots && page) await page.screenshot({ path: join(screenshots, 'download-failure.png') }).catch(() => {});
  throw error;
} finally {
  await browser?.close();
  const stopped = new Promise(resolve => server.once('exit', resolve));
  server.kill('SIGTERM');
  const force = setTimeout(() => server.kill('SIGKILL'), 10000);
  if (server.exitCode === null && server.signalCode === null) await stopped;
  clearTimeout(force);
  await rm(directory, { recursive: true, force: true });
}
