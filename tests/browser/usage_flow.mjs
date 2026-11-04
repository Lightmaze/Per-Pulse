/** A page shows the tokens its model service reported.
 * Copyright 2026 Lightmaze Contributors. Licensed under Apache-2.0.
 * Model replies are a synthetic HTTP fixture; World, Kernel and SQLite are real.
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
const screenshots = process.env.PER_PULSE_USAGE_SCREENSHOTS;
if (screenshots) await mkdir(screenshots, { recursive: true });
const socket = createServer();
await new Promise(done => socket.listen(0, '127.0.0.1', done));
const port = socket.address().port;
await new Promise(done => socket.close(done));
const base = 'http://127.0.0.1:' + port;
let browser, serverLog = '';
const server = spawn(process.env.PER_PULSE_PYTHON || join(root, '.venv/bin/python'), [join(root, 'tests/browser/usage_server.py')], {
  cwd: root, env: { ...process.env, PER_PULSE_QA_PORT: String(port) }, stdio: ['ignore', 'pipe', 'pipe'],
});
server.stdout.on('data', chunk => { serverLog += chunk; });
server.stderr.on('data', chunk => { serverLog += chunk; });
const wait = async (fn, label, timeout = 20000) => {
  const deadline = Date.now() + timeout;
  while (Date.now() < deadline) {
    try { if (await fn()) return; } catch {}
    await new Promise(done => setTimeout(done, 80));
  }
  throw new Error('Timed out: ' + label + '\n' + serverLog);
};
const checks = [];
try {
  await wait(async () => (await fetch(base + '/api/health')).ok, 'service ready');
  browser = await chromium.launch({ executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE || undefined,
    args: process.env.PLAYWRIGHT_CHROMIUM_ARGS ? JSON.parse(process.env.PLAYWRIGHT_CHROMIUM_ARGS) : undefined });
  const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.goto(base);
  assert.equal(await page.locator('.page-usage').count(), 0);
  await page.getByRole('textbox', { name: /^Message / }).fill('Draft a short plan.');
  await page.getByRole('button', { name: 'Send message', exact: true }).click();
  await page.locator('.message-assistant').getByText('Synthetic fixture. The plan has two steps.').waitFor();
  const summary = page.getByText('1.5K tokens used on this page', { exact: true });
  await summary.waitFor();
  checks.push('the page shows the tokens its model service reported, and nothing before');

  await summary.click();
  const details = page.locator('.page-usage');
  assert.match(await details.innerText(), /Sent to the model\s+1,200 · 1,000 from cache/);
  assert.match(await details.innerText(), /Written by the model\s+300/);
  assert.match(await details.innerText(), /Latest turn\s+1,500/);
  checks.push('opening it shows exact sent, cached and written tokens');
  if (screenshots) await page.screenshot({ path: join(screenshots, 'page-usage-open.png') });

  await page.setViewportSize({ width: 390, height: 844 });
  await page.waitForTimeout(200);
  assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth), 'no sideways scroll on a phone');
  checks.push('the usage line fits a phone width');

  assert.deepEqual(errors, []);
  console.log(JSON.stringify({ passed: checks.length, checks, provider: 'explicit synthetic HTTP fixture' }));
} finally {
  await browser?.close();
  server.kill('SIGTERM');
}
