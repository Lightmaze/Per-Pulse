/** Real process death after an approved native file effect, before its receipt.
 * Synthetic model only. No external provider or billing/learning claim.
 * Copyright 2026 Lightmaze Contributors. Licensed under Apache-2.0.
 */
import assert from 'node:assert/strict';
import { spawn, execFile } from 'node:child_process';
import { once } from 'node:events';
import { mkdtemp, mkdir, readFile, stat, rm } from 'node:fs/promises';
import { createServer } from 'node:net';
import { tmpdir } from 'node:os';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { createRequire } from 'node:module';
import { promisify } from 'node:util';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '../..');
const require = createRequire(import.meta.url);
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const python = process.env.PER_PULSE_PYTHON || join(root, '.venv/bin/python');
const directory = await mkdtemp(join(tmpdir(), 'per-pulse-crash-'));
const home = join(directory, 'home');
const evidence = join(directory, 'evidence');
const screenshots = process.env.PER_PULSE_CRASH_SCREENSHOTS;
const socket = createServer();
await new Promise(resolve => socket.listen(0, '127.0.0.1', resolve));
const port = socket.address().port;
await new Promise(resolve => socket.close(resolve));
const base = `http://127.0.0.1:${port}`;
let server, browser, serverLog = '';
const checks = [];
const delay = ms => new Promise(resolve => setTimeout(resolve, ms));
const wait = async (read, label, timeout = 15000) => {
  const end = Date.now() + timeout;
  while (Date.now() < end) {
    try { if (await read()) return; } catch { /* Startup or state has not settled. */ }
    await delay(80);
  }
  throw new Error(`Timed out: ${label}\n${serverLog}`);
};
const start = mode => {
  const child = spawn(python, [join(root, 'tests/browser/crash_recovery_server.py'),
    '--data-dir', home, '--evidence-dir', evidence, '--port', String(port), '--mode', mode],
  { cwd: root, stdio: ['ignore', 'pipe', 'pipe'] });
  child.stderr.on('data', data => { serverLog += data; });
  return child;
};
const stop = async (child, signal = 'SIGTERM') => {
  if (!child || child.exitCode !== null || child.signalCode !== null) return;
  const exited = once(child, 'exit');
  child.kill(signal);
  const force = setTimeout(() => child.kill('SIGKILL'), 10000);
  try { await exited; } finally { clearTimeout(force); }
};
const journal = async () => (await readFile(join(evidence, 'events.jsonl'), 'utf8')).trim().split('\n').map(JSON.parse);

try {
  server = start('effect');
  await wait(async () => (await fetch(base + '/api/health')).ok, 'service ready');
  browser = await chromium.launch({
    executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE || undefined,
    args: process.env.PLAYWRIGHT_CHROMIUM_ARGS ? JSON.parse(process.env.PLAYWRIGHT_CHROMIUM_ARGS) : undefined,
  });
  const context = await browser.newContext({ viewport: { width: 1280, height: 900 } });
  const page = await context.newPage();
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.goto(base);
  assert.equal(await page.title(), 'Per Pulse');
  await page.getByRole('button', { name: 'Create a page', exact: true }).click();
  await page.getByRole('textbox', { name: 'Page name', exact: true }).fill('Interrupted file');
  await page.getByRole('textbox', { name: 'Where should we begin?', exact: true }).fill('WRITE_BEFORE_CRASH');
  await page.getByRole('button', { name: 'Create page', exact: true }).click();
  await page.getByText('Your approval is needed', { exact: true }).waitFor();
  const work = new URL(page.url()).pathname.split('/').pop();
  const originalWorld = (await (await fetch(base + '/api/bootstrap')).json()).workspaceId;
  const draft = 'Keep this original Work draft after the crash.';
  await page.locator('.composer textarea').fill(draft);
  await page.getByRole('button', { name: 'Allow once', exact: true }).click();
  await wait(async () => (await journal()).some(event => event.kind === 'effect'), 'real write before receipt');
  const file = join(home, 'workspace/interrupted.txt');
  assert.equal(await readFile(file, 'utf8'), 'Written once before the process died.\n');
  const fileStat = await stat(file);
  await stop(server, 'SIGKILL');
  checks.push('an approved native file write precedes physical process death without a receipt');

  // Read the durable lease only after the old OS process is confirmed dead.
  // Never delete, shorten, or take over a live lease to speed up this test.
  const { stdout } = await promisify(execFile)(python, ['-c',
    'import sqlite3, sys; db = sqlite3.connect("file:" + sys.argv[1] + "?mode=ro", uri=True); print(db.execute("SELECT expires_at FROM runtime_leases WHERE scope = ?", ("pulse_world",)).fetchone()[0]); db.close()',
    join(home, 'world.db')]);
  const leaseDelay = Math.max(0, Date.parse(stdout.trim()) - Date.now() + 250);
  assert.ok(Number.isFinite(leaseDelay) && leaseDelay < 35000);
  await delay(leaseDelay);
  server = start('reply');
  await wait(async () => (await fetch(base + '/api/health')).ok, 'new fenced owner of same World');
  assert.equal((await (await fetch(base + '/api/bootstrap')).json()).workspaceId, originalWorld);
  assert.deepEqual((await journal()).map(event => event.kind), ['request', 'effect']);
  const consoleErrors = [];
  page.on('console', message => { if (message.type() === 'error') consoleErrors.push(message.text()); });
  await page.reload();
  await page.getByRole('heading', { name: 'Interrupted file', exact: true }).waitFor();
  assert.equal(await page.locator('.composer textarea').inputValue(), draft);
  const outcomes = page.locator('details.execution-uncertain');
  await outcomes.locator('summary').click();
  await outcomes.getByText(/Stopping a turn does not undo its actions/).waitFor();
  assert.match(await outcomes.innerText(), /interrupted\.txt/);
  assert.equal(await outcomes.getByRole('button').count(), 0);
  assert.equal(await page.getByRole('button', { name: 'Allow once', exact: true }).count(), 0);
  assert.equal((await (await fetch(base + `/api/pages/${work}/artifacts`)).json()).artifacts.length, 0);
  checks.push('same Work exposes the lost action without replay, fabricated artifact or renewed approval');
  if (screenshots) {
    await mkdir(screenshots, { recursive: true });
    await page.screenshot({ path: join(screenshots, 'crash-unconfirmed-desktop.png') });
  }
  await page.getByRole('button', { name: 'Stop this turn', exact: true }).click();
  await page.getByText('This turn was stopped', { exact: true }).waitFor();
  await outcomes.getByText(/Stopping a turn does not undo its actions/).waitFor();
  assert.equal(await readFile(file, 'utf8'), 'Written once before the process died.\n');

  await page.getByRole('button', { name: 'Settings', exact: true }).click();
  await page.locator('#interface-language').selectOption('zh-CN');
  await page.getByRole('button', { name: '关闭对话框', exact: true }).click();
  await page.getByText('结果尚未确认的操作', { exact: true }).waitFor();
  assert.equal(await page.locator('.composer textarea').inputValue(), draft);
  await page.getByRole('button', { name: '设置', exact: true }).click();
  await page.locator('#interface-language').selectOption('en');
  await page.getByRole('button', { name: 'Close dialog', exact: true }).click();
  checks.push('Stop and language changes preserve the unconfirmed effect and draft');

  await page.locator('aside.sidebar').getByRole('link', { name: 'Home', exact: true }).click();
  await page.getByRole('heading', { name: 'A place to keep going.', exact: true }).waitFor();
  assert.equal(await page.locator('details.execution-uncertain').count(), 0);
  assert.equal((await page.locator('body').innerText()).includes('interrupted.txt'), false);
  await page.goto(base + `/page/${work}`);
  await page.getByRole('heading', { name: 'Interrupted file', exact: true }).waitFor();
  assert.equal(await page.locator('.composer textarea').inputValue(), draft);
  await page.locator('.composer textarea').fill('CONTINUE_AFTER_REVIEW');
  await page.getByRole('button', { name: 'Send message', exact: true }).click();
  await page.getByText('Continued the same Work after review. [Synthetic crash fixture]', { exact: true }).waitFor();
  await outcomes.locator('summary').click();
  assert.match(await outcomes.innerText(), /interrupted\.txt/);
  assert.deepEqual((await journal()).map(event => event.kind), ['request', 'effect', 'request']);
  assert.equal((await stat(file)).mtimeMs, fileStat.mtimeMs);
  checks.push('Home does not expose Work action details; new input continues the original Work without a repeat effect');

  const retainedDraft = 'Keep this draft while the original Work continues across a native generation.';
  await page.locator('.composer textarea').fill(retainedDraft);
  const rotation = await fetch(base + `/api/__crash/succession/${work}`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}',
  });
  assert.equal(rotation.status, 200, await rotation.clone().text());
  const generation = await rotation.json();
  assert.deepEqual(generation.lineage, [generation.successor, generation.previous]);
  assert.notEqual(generation.successor, generation.previous);
  await page.reload();
  await page.getByRole('heading', { name: 'Interrupted file', exact: true }).waitFor();
  await outcomes.locator('summary').click();
  assert.match(await outcomes.innerText(), /interrupted\.txt/);
  assert.equal(await page.locator('.composer textarea').inputValue(), retainedDraft);
  assert.equal(await outcomes.getByRole('button').count(), 0);
  assert.equal((await (await fetch(base + `/api/pages/${work}/artifacts`)).json()).artifacts.length, 0);
  assert.deepEqual((await journal()).map(event => event.kind), ['request', 'effect', 'request', 'summary_request']);
  assert.equal((await stat(file)).mtimeMs, fileStat.mtimeMs);
  if (screenshots) await page.screenshot({ path: join(screenshots, 'crash-unconfirmed-after-succession.png') });
  checks.push('committed native succession retains the same Work uncertainty and draft without a new approval or repeated effect');

  await page.setViewportSize({ width: 390, height: 844 });
  await outcomes.scrollIntoViewIfNeeded();
  assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth));
  if (screenshots) await page.screenshot({ path: join(screenshots, 'crash-unconfirmed-mobile.png') });
  assert.deepEqual(errors, []);
  assert.deepEqual(consoleErrors, []);
  checks.push('desktop/mobile recovery has no page errors, post-recovery console errors or horizontal overflow');
  console.log(JSON.stringify({ passed: checks.length, checks, provider: 'synthetic; real native effect and process kill', leaseWaitMs: leaseDelay }));
} catch (error) {
  console.error(error.stack, '\nSERVER:\n', serverLog);
  process.exitCode = 1;
} finally {
  await browser?.close();
  await stop(server);
  await rm(directory, { recursive: true, force: true });
}
