/** Native Work completion remains visible after navigating to idle Home.
 * Copyright 2026 Lightmaze Contributors. Licensed under Apache-2.0.
 * Provider replies are explicitly synthetic; approval and file effects are real.
 */
import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { createServer } from 'node:net';
import { mkdir } from 'node:fs/promises';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { createRequire } from 'node:module';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '../..');
const { chromium } = createRequire(import.meta.url)(process.env.PLAYWRIGHT_MODULE || 'playwright');
const screenshots = process.env.PER_PULSE_STATUS_SCREENSHOTS;
if (screenshots) await mkdir(screenshots, { recursive: true });
const socket = createServer();
await new Promise(resolve => socket.listen(0, '127.0.0.1', resolve));
const port = socket.address().port;
await new Promise(resolve => socket.close(resolve));
const base = 'http://127.0.0.1:' + port;
let browser, page, serverLog = '', releaseHome = () => {};
const server = spawn(process.env.PER_PULSE_PYTHON || join(root, '.venv/bin/python'), [join(root, 'tests/browser/fixture_server.py')], {
  cwd: root, env: { ...process.env, PER_PULSE_QA_PORT: String(port) }, stdio: ['ignore', 'pipe', 'pipe'],
});
server.stdout.on('data', data => { serverLog += data; });
server.stderr.on('data', data => { serverLog += data; });
const wait = async (fn, label) => {
  const deadline = Date.now() + 15000;
  while (Date.now() < deadline) {
    try { if (await fn()) return; } catch {}
    await new Promise(resolve => setTimeout(resolve, 80));
  }
  throw new Error('Timed out: ' + label + '\n' + serverLog);
};
const data = path => fetch(base + path).then(r => { assert.ok(r.ok, path); return r.json(); });
try {
  await wait(async () => (await fetch(base + '/api/health')).ok, 'service ready');
  browser = await chromium.launch({ executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE || undefined,
    args: process.env.PLAYWRIGHT_CHROMIUM_ARGS ? JSON.parse(process.env.PLAYWRIGHT_CHROMIUM_ARGS) : undefined });
  page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.goto(base);
  await page.getByRole('button', { name: 'Create a page', exact: true }).click();
  await page.getByRole('textbox', { name: 'Page name', exact: true }).fill('Work finishing elsewhere');
  await page.getByRole('textbox', { name: 'Where should we begin?', exact: true }).fill('WRITE_SLOW SLOW_AFTER_APPROVAL');
  await page.getByRole('button', { name: 'Create page', exact: true }).click();
  await page.getByRole('heading', { name: 'Work finishing elsewhere', exact: true }).waitFor();
  const workId = new URL(page.url()).pathname.split('/').pop();
  await page.getByRole('button', { name: 'Allow once', exact: true }).click();
  await wait(async () => (await data('/__fixture/status')).entered, 'held provider after actual file approval');
  const link = page.locator('aside.sidebar a[href="/page/' + workId + '"]');
  await link.locator('.working-dot').waitFor();
  const heldHome = new Promise(resolve => { releaseHome = resolve; });
  await page.route('**/api/pages/home', async route => {
    const response = await route.fetch();
    await heldHome;
    try { await route.fulfill({ response }); } catch { /* Browser closed during a failed check. */ }
  });
  await page.locator('aside.sidebar').getByRole('link', { name: 'Home', exact: true }).click();
  const home = page.getByRole('textbox', { name: 'Message Home', exact: true });
  await home.fill('Keep writing at Home while the other Work finishes.');
  assert.equal(await home.evaluate(el => el === document.activeElement), true);
  releaseHome();
  const before = await data('/__fixture/status');
  await link.locator('.working-dot').waitFor();
  await data('/__fixture/release');
  await wait(async () => (await data('/api/pages/' + workId)).page.status === 'idle', 'native Work completion');
  if (process.env.PER_PULSE_STATUS_OBSERVE === '1') {
    await page.waitForTimeout(4500);
    console.log(JSON.stringify({ observationOnly: true, nativeStatus: (await data('/api/pages/' + workId)).page.status,
      visibleWorkingMarkers: await link.locator('.working-dot').count(), accessibleName: await link.ariaSnapshot() }));
  } else {
    await wait(async () => await link.locator('.working-dot').count() === 0, 'inactive Work marker reflects native completion');
    assert.equal(new URL(page.url()).pathname, '/');
    assert.equal(await home.inputValue(), 'Keep writing at Home while the other Work finishes.');
    assert.equal(await home.evaluate(el => el === document.activeElement), true);
    assert.equal((await data('/__fixture/status')).requests, before.requests);
    assert.deepEqual(errors, []);
    console.log(JSON.stringify({ checks: ['native Work completion clears its sidebar marker while Home is idle',
      'read-only status refresh retains Home route, draft and focus and makes no model request'], errors }));
  }
  if (screenshots) await page.screenshot({ path: join(screenshots, 'work-completed-from-home.png') });
} catch (error) {
  if (screenshots && page) await page.screenshot({ path: join(screenshots, 'workspace-status-failure.png') }).catch(() => {});
  throw error;
} finally {
  releaseHome();
  await browser?.close();
  const stopped = new Promise(resolve => server.once('exit', resolve));
  server.kill('SIGTERM');
  const force = setTimeout(() => server.kill('SIGKILL'), 10000);
  if (server.exitCode === null && server.signalCode === null) await stopped;
  clearTimeout(force);
}
