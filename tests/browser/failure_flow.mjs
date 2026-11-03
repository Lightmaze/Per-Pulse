/** A failed turn says why, offers Send again, and status text follows the interface language.
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
const screenshots = process.env.PER_PULSE_FAILURE_SCREENSHOTS;
if (screenshots) await mkdir(screenshots, { recursive: true });
const socket = createServer();
await new Promise(done => socket.listen(0, '127.0.0.1', done));
const port = socket.address().port;
await new Promise(done => socket.close(done));
const base = 'http://127.0.0.1:' + port;
let browser, serverLog = '';
const server = spawn(process.env.PER_PULSE_PYTHON || join(root, '.venv/bin/python'), [join(root, 'tests/browser/failure_server.py')], {
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
  const composer = page.getByRole('textbox', { name: /^Message / });
  await composer.fill('What should we do first?');
  await page.getByRole('button', { name: 'Send message', exact: true }).click();
  const activity = page.locator('.page-activity');
  await activity.getByText('Turn interrupted', { exact: true }).waitFor();
  await activity.getByText('The model service had an error. Send again in a moment.', { exact: true }).waitFor();
  // Nothing is in doubt after a model error, so no separate Stop is needed.
  assert.equal(await page.getByRole('button', { name: 'Stop this turn', exact: true }).count(), 0);
  checks.push('a failed turn says why it stopped');
  if (screenshots) await page.screenshot({ path: join(screenshots, 'failure-reason.png') });

  await activity.getByRole('button', { name: 'Send again', exact: true }).click();
  await page.locator('.message-assistant').getByText('Synthetic fixture: answered.').waitFor();
  await wait(async () => await activity.getByRole('button', { name: 'Send again', exact: true }).count() === 0, 'Send again hidden');
  checks.push('Send again gets the answer; the page is not stuck after one model error');

  const zh = await browser.newPage({ viewport: { width: 390, height: 844 }, locale: 'zh-CN' });
  zh.on('pageerror', error => errors.push(error.message));
  await fetch(base + '/__failure/next', { method: 'POST' });
  await zh.goto(base);
  await zh.getByRole('textbox').first().fill('再试一个问题。');
  await zh.keyboard.press('Enter');
  const zhActivity = zh.locator('.page-activity');
  await zhActivity.getByText('本轮已中断', { exact: true }).waitFor();
  await zhActivity.getByText('模型服务出错了。请稍后再发送一次。', { exact: true }).waitFor();
  await zhActivity.getByRole('button', { name: '再发送一次', exact: true }).waitFor();
  const overflow = await zh.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  assert.ok(overflow <= 1, 'no horizontal scroll on a phone');
  if (screenshots) await zh.screenshot({ path: join(screenshots, 'failure-reason-zh.png') });
  checks.push('status text and the reason follow the Chinese interface on a phone');

  assert.deepEqual(errors, []);
  console.log(JSON.stringify({ passed: checks.length, checks, provider: 'explicit synthetic HTTP fixture' }));
} finally {
  await browser?.close();
  server.kill('SIGTERM');
}
