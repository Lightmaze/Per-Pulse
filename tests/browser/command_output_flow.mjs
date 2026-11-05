/** Command -> saved pipe output -> passages/download -> later turn -> offline reopen.
 * World/Kernel/SQLite, pipe I/O, HTTP and browser actions are real. Model choices
 * and Docker control are explicit synthetic fixtures; no provider is contacted.
 * Copyright 2026 Lightmaze Contributors. Licensed under Apache-2.0.
 */
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { spawn } from 'node:child_process';
import { mkdtemp, mkdir, readFile, rm } from 'node:fs/promises';
import { createServer } from 'node:net';
import { tmpdir } from 'node:os';
import { delimiter, dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { createRequire } from 'node:module';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '../..');
const { chromium } = createRequire(import.meta.url)(process.env.PLAYWRIGHT_MODULE || 'playwright');
const directory = await mkdtemp(join(tmpdir(), 'per-pulse-command-browser-'));
const screenshots = process.env.PER_PULSE_COMMAND_SCREENSHOTS;
if (screenshots) await mkdir(screenshots, { recursive: true });
const socket = createServer();
await new Promise(done => socket.listen(0, '127.0.0.1', done));
const port = socket.address().port;
await new Promise(done => socket.close(done));
const base = `http://127.0.0.1:${port}`;
const checks = [], errors = [];
const check = description => { checks.push(description); console.log(`PASS: ${description}`); };
let serverLog = '', server, browser, page;
const start = (offline = false) => {
  const child = spawn(process.env.PER_PULSE_PYTHON || join(root, '.venv/bin/python'), [
    join(root, 'tests/browser/command_output_server.py'), '--data-dir', directory, '--port', String(port),
    ...(offline ? ['--offline'] : []),
  ], { cwd: root, env: { ...process.env, PYTHONPATH: [join(root, 'src'), process.env.PYTHONPATH].filter(Boolean).join(delimiter) }, stdio: ['ignore', 'pipe', 'pipe'] });
  child.stdout.on('data', data => { serverLog += data; });
  child.stderr.on('data', data => { serverLog += data; });
  return child;
};
const stop = async child => {
  if (!child || child.exitCode !== null || child.signalCode !== null) return;
  child.kill('SIGTERM');
  const force = setTimeout(() => child.kill('SIGKILL'), 10000);
  await new Promise(done => child.once('exit', done));
  clearTimeout(force);
};
const wait = async (fn, label) => {
  const until = Date.now() + 30000;
  while (Date.now() < until) {
    try { if (await fn()) return; } catch {}
    await new Promise(done => setTimeout(done, 80));
  }
  throw new Error(`Timed out: ${label}\n${serverLog}`);
};
const json = async path => {
  const response = await fetch(base + path);
  assert.equal(response.status, 200, `${path}: ${await response.clone().text()}`);
  return response.json();
};
try {
  server = start();
  await wait(async () => (await fetch(base + '/api/health')).ok, 'server ready');
  browser = await chromium.launch({ executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE || undefined,
    args: process.env.PLAYWRIGHT_CHROMIUM_ARGS ? JSON.parse(process.env.PLAYWRIGHT_CHROMIUM_ARGS) : undefined });
  const context = await browser.newContext({ viewport: { width: 1280, height: 900 }, acceptDownloads: true, locale: 'en-US' });
  page = await context.newPage();
  page.on('pageerror', error => errors.push(error.message));
  page.on('console', message => { if (message.type() === 'error' && !message.text().includes('Failed to load resource')) errors.push(message.text()); });
  let downloads = 0;
  page.on('download', () => { downloads++; });
  await page.goto(base);
  assert.match(await page.title(), /Per Pulse/i);
  await page.getByRole('textbox', { name: 'Message Home', exact: true }).waitFor();
  assert.equal(await page.locator('vite-error-overlay').count(), 0);
  await page.getByRole('button', { name: 'Create a page', exact: true }).click();
  await page.getByRole('textbox', { name: 'Page name', exact: true }).fill('Command investigation');
  await page.getByRole('textbox', { name: 'Where should we begin?', exact: true }).fill('RUN_OUTPUT_FIXTURE');
  await page.getByRole('button', { name: 'Create page', exact: true }).click();
  await page.getByRole('heading', { name: 'Command investigation', exact: true }).waitFor();
  await page.getByRole('button', { name: 'Allow once', exact: true }).click();
  await page.getByText('Both final diagnoses were read from the original command. [Explicit synthetic fixture]', { exact: true }).waitFor();
  const pageId = new URL(page.url()).pathname.split('/').pop();
  const view = await json(`/api/pages/${pageId}`);
  const output = view.execution.commandOutputs.items[0];
  assert.equal(output.exitCode, 7);
  assert.match(output.commandLabel, /explicit-pipe-fixture long/);
  assert.equal(view.artifacts.length, 0);
  assert.equal((await json('/__command/evidence')).commands, 1);
  check('one approved native command uses real OS pipes; synthetic model reads both tail diagnoses through read_command_output');

  const section = page.locator('details.command-outputs');
  const reader = page.getByRole('region', { name: 'Command output', exact: true });
  const open = async label => {
    if (!await section.getAttribute('open').then(value => value !== null)) await section.locator(':scope > summary').click();
    await section.getByRole('button', { name: new RegExp(label) }).click();
    await reader.locator('pre').waitFor();
  };
  await open('explicit-pipe-fixture long');
  await page.getByRole('textbox', { name: 'Message Command investigation', exact: true }).fill('Keep this unsent draft while reading.');
  assert.equal(await reader.locator('pre').textContent().then(text => text.includes('STDOUT_FINAL_DIAGNOSIS')), false);
  assert.equal(await reader.getByText(/Original action:/).isVisible(), false);
  let all = '', pages = 0;
  while (true) {
    all += await reader.locator('pre').textContent(); pages++;
    const next = reader.getByRole('button', { name: 'Next passage', exact: true });
    if (await next.isDisabled()) break;
    await next.click(); await reader.locator('pre').waitFor();
  }
  assert.ok(pages > 1 && all.endsWith('STDOUT_FINAL_DIAGNOSIS\n'));
  assert.equal(createHash('sha256').update(all).digest('hex'), output.streams.stdout.sha256);
  await reader.getByText('End of saved output.', { exact: true }).waitFor();
  await reader.getByRole('button', { name: 'Previous passage', exact: true }).click();
  await reader.locator('pre').waitFor();
  await reader.getByRole('button', { name: 'Standard error', exact: true }).click();
  await reader.locator('pre').waitFor();
  let stderr = '';
  while (true) {
    stderr += await reader.locator('pre').textContent();
    if (await reader.getByRole('button', { name: 'Next passage', exact: true }).isDisabled()) break;
    await reader.getByRole('button', { name: 'Next passage', exact: true }).click(); await reader.locator('pre').waitFor();
  }
  assert.ok(stderr.endsWith('STDERR_FINAL_DIAGNOSIS\n'));
  assert.equal(createHash('sha256').update(stderr).digest('hex'), output.streams.stderr.sha256);
  check('Unicode passages reconstruct both exact saved streams; previous/next and original exit code remain distinct from capture completeness');

  const downloaded = page.waitForEvent('download');
  await reader.getByRole('button', { name: 'Download saved bytes', exact: true }).click();
  const file = await downloaded;
  // Chromium replaces colon characters in an otherwise verified attachment name.
  assert.equal(file.suggestedFilename(), `command-${output.actionId}-stderr.bin`.replaceAll(':', '_'));
  const rawDownload = await fetch(`${base}/api/pages/${pageId}/execution/commands/${encodeURIComponent(output.actionId)}/stderr/download`);
  assert.equal(decodeURIComponent(rawDownload.headers.get('content-disposition').match(/filename\*=UTF-8''([^;]+)/)[1]), `command-${output.actionId}-stderr.bin`);
  const filename = join(directory, 'downloaded-stderr.bin'); await file.saveAs(filename);
  assert.equal(createHash('sha256').update(await readFile(filename)).digest('hex'), output.streams.stderr.sha256);
  const downloadUrl = `**/api/pages/${pageId}/execution/commands/${encodeURIComponent(output.actionId)}/stderr/download`;
  await page.route(downloadUrl, async route => {
    const response = await route.fetch(); await route.fulfill({ response, body: 'CORRUPTED_EXPLICIT_TEST_BYTES' });
  });
  await reader.getByRole('button', { name: 'Download saved bytes', exact: true }).click();
  await reader.getByRole('alert').getByText('The saved output did not match its recorded version. Open it again and retry.', { exact: true }).waitFor();
  assert.equal(downloads, 1);
  await page.unroute(downloadUrl);
  check('download keeps exact saved bytes and filename; a damaged response with the original ETag creates no download');

  let held = false, release;
  const blocked = new Promise(done => { release = done; });
  await page.route(downloadUrl, async route => {
    const response = await route.fetch(); held = true; await blocked;
    try { await route.fulfill({ response }); } catch { /* The source Work was left. */ }
  });
  await reader.getByRole('button', { name: 'Download saved bytes', exact: true }).click();
  await wait(() => held, 'held download');
  await page.locator('aside.sidebar').getByRole('link', { name: 'Home', exact: true }).click();
  await page.getByRole('textbox', { name: 'Message Home', exact: true }).waitFor();
  release(); await page.unrouteAll({ behavior: 'wait' });
  assert.equal(downloads, 1);
  assert.equal(await page.locator('details.command-outputs').count(), 0);
  assert.equal((await fetch(`${base}/api/pages/home/execution/commands/${encodeURIComponent(output.actionId)}/stdout`)).status, 404);
  await page.locator(`aside.sidebar a[href="/page/${pageId}"]`).click();
  const composer = page.getByRole('textbox', { name: 'Message Command investigation', exact: true });
  await composer.waitFor(); assert.equal(await composer.inputValue(), 'Keep this unsent draft while reading.');
  await composer.fill('Continue discussing the investigation.');
  await page.getByRole('button', { name: 'Send message', exact: true }).click();
  await page.getByText('A later ordinary turn. [Explicit synthetic fixture]', { exact: true }).waitFor();
  await open('explicit-pipe-fixture long');
  assert.equal((await json('/__command/evidence')).commands, 1);
  check('leaving the page cancels a pending save, Home cannot borrow the action, and a later turn still exposes the original command');

  // Read failures are recoverable and a delayed old stream cannot replace the selected stream.
  const readUrl = `**/api/pages/${pageId}/execution/commands/${encodeURIComponent(output.actionId)}/stderr?*`;
  await page.route(readUrl, route => route.fulfill({ status: 503, contentType: 'application/json', body: JSON.stringify({ error: { code: 'command_output_unavailable', message: 'Explicit read failure' } }) }));
  await reader.getByRole('button', { name: 'Standard error', exact: true }).click();
  await reader.getByRole('alert').getByText('This saved command output is unavailable.', { exact: true }).waitFor();
  await page.unroute(readUrl);
  await reader.getByRole('button', { name: 'Try reading again', exact: true }).click(); await reader.locator('pre').waitFor();
  await reader.getByRole('button', { name: 'Standard output', exact: true }).click(); await reader.locator('pre').waitFor();
  held = false; let releaseRead;
  const blockedRead = new Promise(done => { releaseRead = done; });
  await page.route(readUrl, async route => {
    const response = await route.fetch(); held = true; await blockedRead;
    try { await route.fulfill({ response }); } catch { /* A new selection won. */ }
  });
  await reader.getByRole('button', { name: 'Standard error', exact: true }).click(); await wait(() => held, 'held old stream');
  await reader.getByRole('button', { name: 'Standard output', exact: true }).click(); await reader.locator('pre').waitFor();
  releaseRead(); await page.unrouteAll({ behavior: 'wait' });
  assert.match(await reader.locator('pre').textContent(), /^ordinary progress/);
  check('failed reads retry without running commands; an obsolete stream response cannot replace the current selection');
  if (screenshots) { await section.scrollIntoViewIfNeeded(); await page.screenshot({ path: join(screenshots, 'command-output-desktop.png') }); }

  await composer.fill('RUN_CAPPED_FIXTURE'); await page.getByRole('button', { name: 'Send message', exact: true }).click();
  await page.getByRole('button', { name: 'Allow once', exact: true }).click();
  await page.getByText('The command exited with 7 and reached the capture limit. [Explicit synthetic fixture]', { exact: true }).waitFor();
  const cappedView = await json(`/api/pages/${pageId}`);
  assert.ok(cappedView.execution.commandOutputs.items.some(item => item.commandLabel === 'explicit-pipe-fixture capped'));
  await open('explicit-pipe-fixture capped');
  await reader.getByText('1048576 of 1048704 emitted bytes were saved. The rest exceeded the 1 MiB capture limit.', { exact: true }).waitFor();
  await reader.getByText('End of saved output.', { exact: true }).waitFor();
  assert.equal(await reader.locator('pre').textContent(), 'password=[REDACTED]');
  await reader.getByText('Some values are hidden in the preview. The download contains the original saved bytes.', { exact: true }).waitFor();
  await reader.getByRole('button', { name: 'Standard error', exact: true }).click();
  await reader.getByText('This stream is empty.', { exact: true }).waitFor();
  assert.equal((await json('/__command/evidence')).commands, 2);
  check('capture overflow is visibly incomplete while the other empty stream is correctly complete');

  await composer.fill('RUN_SAVE_FAILURE_FIXTURE'); await page.getByRole('button', { name: 'Send message', exact: true }).click();
  await page.getByRole('button', { name: 'Allow once', exact: true }).click();
  await page.getByText('The command exited with 7, but saving its output failed. [Explicit synthetic fixture]', { exact: true }).waitFor();
  await section.getByText('This command exited, but its output could not be saved.', { exact: true }).waitFor();
  assert.equal(await section.getByRole('button', { name: /explicit-pipe-fixture save-failure/ }).isDisabled(), true);
  assert.equal((await json('/__command/evidence')).commands, 3);
  check('a synthetic storage failure keeps the actual command exit visible without presenting saved output or inviting a rerun');

  const beforeRestart = await json('/__command/evidence');
  await composer.fill('Draft survives offline reopening.');
  await stop(server); server = start(true);
  await wait(async () => (await fetch(base + '/api/health')).ok, 'offline restart ready');
  await page.reload(); await composer.waitFor();
  assert.equal((await json(`/api/pages/${pageId}`)).provider.configured, false);
  await open('explicit-pipe-fixture long');
  assert.match(await reader.locator('pre').textContent(), /^ordinary progress/);
  const offlineDownload = page.waitForEvent('download');
  await reader.getByRole('button', { name: 'Download saved bytes', exact: true }).click();
  const offlineFile = await offlineDownload;
  const offlinePath = join(directory, 'offline-stdout.bin'); await offlineFile.saveAs(offlinePath);
  assert.equal(createHash('sha256').update(await readFile(offlinePath)).digest('hex'), output.streams.stdout.sha256);
  assert.equal(await composer.inputValue(), 'Draft survives offline reopening.');
  const afterRestart = await json('/__command/evidence');
  assert.equal(afterRestart.commands, beforeRestart.commands); assert.equal(afterRestart.requests, beforeRestart.requests);
  assert.equal(afterRestart.offline, true);
  check('offline restart without a model or command executor preserves discovery, original bytes, downloads and the unsent draft without rerunning');

  await page.setViewportSize({ width: 390, height: 844 });
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
  if (screenshots) { await reader.locator('.command-output-streams').scrollIntoViewIfNeeded(); await page.screenshot({ path: join(screenshots, 'command-output-mobile.png') }); }
  await page.evaluate(() => localStorage.setItem('per-pulse.ui.language.v1', 'zh-CN'));
  await page.reload(); await page.getByRole('textbox', { name: /Command investigation/ }).waitFor();
  await section.locator(':scope > summary').click();
  await section.getByText('此命令已经退出，但未能保存其输出。', { exact: true }).waitFor();
  await section.getByRole('button', { name: /explicit-pipe-fixture long/ }).click();
  await page.getByRole('region', { name: '命令输出', exact: true }).getByRole('button', { name: '下一段', exact: true }).waitFor();
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
  check('desktop and phone layouts fit; Chinese controls retain the same saved source');
  assert.deepEqual(errors, []);
  console.log(JSON.stringify({ checks, browserErrors: errors, sourceAction: output.actionId, pipeExecutions: afterRestart.commands, modelRequests: afterRestart.requests }, null, 2));
} catch (error) {
  if (screenshots && page) await page.screenshot({ path: join(screenshots, 'command-output-failure.png') }).catch(() => {});
  console.error(serverLog); throw error;
} finally {
  await browser?.close(); await stop(server);
  await rm(directory, { recursive: true, force: true });
}
