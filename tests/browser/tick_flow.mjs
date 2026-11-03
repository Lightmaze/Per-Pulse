/** /tick in the composer: a page continues for exactly N steps, and stops on request.
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
const screenshots = process.env.PER_PULSE_TICK_SCREENSHOTS;
if (screenshots) await mkdir(screenshots, { recursive: true });
const socket = createServer();
await new Promise(done => socket.listen(0, '127.0.0.1', done));
const port = socket.address().port;
await new Promise(done => socket.close(done));
const base = 'http://127.0.0.1:' + port;
let browser, serverLog = '';
const server = spawn(process.env.PER_PULSE_PYTHON || join(root, '.venv/bin/python'), [join(root, 'tests/browser/tick_server.py')], {
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
const json = (path, init) => fetch(base + path, init).then(r => { assert.ok(r.ok, path); return r.json(); });
const checks = [];
try {
  await wait(async () => (await fetch(base + '/api/health')).ok, 'service ready');
  browser = await chromium.launch({ executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE || undefined,
    args: process.env.PLAYWRIGHT_CHROMIUM_ARGS ? JSON.parse(process.env.PLAYWRIGHT_CHROMIUM_ARGS) : undefined });
  const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  const composer = page.getByRole('textbox', { name: /^Message / });
  const send = () => page.getByRole('button', { name: 'Send message', exact: true }).click();
  const replies = () => page.locator('.message-assistant').count();

  await page.goto(base);
  await composer.fill('/tick 3');
  await send();
  await page.getByText('Send a message first, then continue after its reply.', { exact: true }).waitFor();
  assert.equal(await composer.inputValue(), '/tick 3', 'a refused command keeps the draft');
  checks.push('a page without a finished reply explains how to start');

  await composer.fill('Think about the launch plan.');
  await send();
  await wait(async () => await replies() === 1, 'first reply');
  await composer.fill('/tick 0');
  await send();
  await page.getByText('Use /tick with a number from 1 to 256, or /tick stop.', { exact: true }).waitFor();
  assert.equal((await json('/__tick/requests')).requests.length, 1, 'an invalid count makes no request');

  await composer.fill('/tick 3');
  await send();
  await page.getByText('Continuing for 3 steps', { exact: true }).waitFor();
  assert.equal(await composer.inputValue(), '', 'a started command clears the draft');
  await page.locator('.tick-status').waitFor();
  if (screenshots) await page.screenshot({ path: join(screenshots, 'tick-running.png') });
  await wait(async () => await replies() === 4, 'three steps reply');
  await wait(async () => await page.locator('.tick-status').count() === 0, 'run finishes quietly');
  await new Promise(done => setTimeout(done, 600));
  const recorded = (await json('/__tick/requests')).requests;
  assert.equal(recorded.length, 4, 'exactly three more steps');
  for (const step of recorded.slice(1)) {
    assert.deepEqual(step.at(-1), { role: 'user', content: '' }, 'a step carries no new instruction');
    assert.match(JSON.stringify(step), /Think about the launch plan\./);
    assert.doesNotMatch(JSON.stringify(step), /\/tick/);
  }
  assert.equal(await page.locator('.message-user').count(), 1, 'steps are not shown as your messages');
  checks.push('/tick 3 continues the page exactly three times from its own history');

  await json('/__tick/delay', { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ seconds: 0.6 }) });
  await composer.fill('/tick 20');
  await send();
  await page.getByText(/Continuing · \d+ of 20/).waitFor();
  await page.locator('.tick-status').getByRole('button', { name: 'Stop', exact: true }).click();
  await page.getByText('Stopped', { exact: true }).waitFor();
  await wait(async () => await page.locator('.tick-status').count() === 0, 'stopped run hides its status');
  const view = await json('/api/pages/home');
  assert.equal(view.tick.status, 'stopped');
  assert.equal(view.tick.reason, 'operator');
  // A step already running may finish; a queued step is cancelled.
  await new Promise(done => setTimeout(done, 1500));
  const afterStop = (await json('/__tick/requests')).requests.length;
  await new Promise(done => setTimeout(done, 1500));
  assert.equal((await json('/__tick/requests')).requests.length, afterStop, 'no step after Stop');
  assert.ok(afterStop <= 4 + 2);
  checks.push('Stop ends the run before its next step');

  await page.setViewportSize({ width: 390, height: 844 });
  await composer.fill('/tick 2');
  await send();
  await page.locator('.tick-status').waitFor();
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  assert.ok(overflow <= 1, 'no horizontal scroll on a phone');
  if (screenshots) await page.screenshot({ path: join(screenshots, 'tick-mobile.png') });
  await composer.fill('I will take it from here.');
  await send();
  await wait(async () => (await json('/api/pages/home')).tick.status === 'stopped', 'your message ends the run');
  assert.equal((await json('/api/pages/home')).tick.reason, 'user_message');
  checks.push('your own message ends a run; the status fits a phone screen');

  assert.deepEqual(errors, []);
  console.log(JSON.stringify({ passed: checks.length, checks, provider: 'explicit synthetic HTTP fixture' }));
} finally {
  await browser?.close();
  server.kill('SIGTERM');
}
