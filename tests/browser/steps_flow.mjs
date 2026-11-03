/** While a turn works, the page lists the steps it has taken.
 * Copyright 2026 Lightmaze Contributors. Licensed under Apache-2.0.
 * Model replies are a synthetic HTTP fixture; tools, World, Kernel and SQLite are real.
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
const screenshots = process.env.PER_PULSE_STEPS_SCREENSHOTS;
if (screenshots) await mkdir(screenshots, { recursive: true });
const socket = createServer();
await new Promise(done => socket.listen(0, '127.0.0.1', done));
const port = socket.address().port;
await new Promise(done => socket.close(done));
const base = 'http://127.0.0.1:' + port;
let browser, serverLog = '';
const server = spawn(process.env.PER_PULSE_PYTHON || join(root, '.venv/bin/python'), [join(root, 'tests/browser/steps_server.py')], {
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
  await page.getByRole('textbox', { name: /^Message / }).fill('Read the notes and the plan.');
  await page.getByRole('button', { name: 'Send message', exact: true }).click();
  const steps = page.getByRole('list', { name: 'Steps in this turn', exact: true });
  await steps.getByText('Read notes.md', { exact: true }).waitFor();
  await steps.getByText('Read plan.md', { exact: true }).waitFor();
  assert.deepEqual(await steps.locator('li > span:first-child').allTextContents(), ['Read notes.md', 'Read plan.md']);
  checks.push('the steps a turn has taken are listed while it works');
  if (screenshots) await page.screenshot({ path: join(screenshots, 'steps-working.png') });

  await fetch(base + '/__steps/release', { method: 'POST' });
  await page.locator('.message-assistant').getByText('Synthetic fixture: both files are read.').waitFor();
  await wait(async () => await steps.count() === 0, 'steps hidden after the turn');
  checks.push('the list goes away when the turn finishes');

  assert.deepEqual(errors, []);
  console.log(JSON.stringify({ passed: checks.length, checks, provider: 'explicit synthetic HTTP fixture' }));
} finally {
  await browser?.close();
  server.kill('SIGTERM');
}
