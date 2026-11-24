/** A reply appears while the model is still writing it.
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
const screenshots = process.env.PER_PULSE_LIVE_SCREENSHOTS;
if (screenshots) await mkdir(screenshots, { recursive: true });
const socket = createServer();
await new Promise(done => socket.listen(0, '127.0.0.1', done));
const port = socket.address().port;
await new Promise(done => socket.close(done));
const base = 'http://127.0.0.1:' + port;
let browser, serverLog = '';
const server = spawn(process.env.PER_PULSE_PYTHON || join(root, '.venv/bin/python'), [join(root, 'tests/browser/live_reply_server.py')], {
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
  await page.getByRole('textbox', { name: /^Message / }).fill('Draft a short plan.');
  await page.getByRole('button', { name: 'Send message', exact: true }).click();

  const live = page.getByRole('article', { name: 'Reply being written', exact: true });
  // Markdown renders the numbered steps as a list, so match the item text.
  await live.getByText('Read the notes.', { exact: true }).waitFor();
  assert.equal(await page.locator('.message-assistant').count(), 0);
  assert.equal(await live.getAttribute('aria-busy'), 'true');
  checks.push('the reply so far is shown while the model is still writing');
  if (screenshots) await page.screenshot({ path: join(screenshots, 'live-reply-writing.png') });

  await fetch(base + '/__live/release', { method: 'POST' });
  const settled = page.locator('.message-assistant');
  await settled.getByText('Write the summary.', { exact: true }).waitFor();
  await wait(async () => await live.count() === 0, 'live reply replaced by the saved reply');
  assert.equal(await settled.count(), 1);
  assert.match(await settled.innerText(), /Read the notes\.[\s\S]*Write the summary\./);
  checks.push('the saved reply replaces it, once, when the model finishes');

  await page.getByRole('textbox', { name: /^Message / }).fill('REFUSAL_FIXTURE');
  await page.getByRole('button', { name: 'Send message', exact: true }).click();
  const refusal = page.locator('.message-assistant').getByText(
    'Synthetic refusal fixture. I cannot complete this request.', { exact: true });
  await refusal.waitFor();
  checks.push('the service refusal appears as a readable reply');
  await page.reload();
  await refusal.waitFor();
  checks.push('the saved refusal remains readable after reopening the page');

  assert.deepEqual(errors, []);
  console.log(JSON.stringify({ passed: checks.length, checks, provider: 'explicit synthetic HTTP fixture' }));
} finally {
  await browser?.close();
  server.kill('SIGTERM');
}
