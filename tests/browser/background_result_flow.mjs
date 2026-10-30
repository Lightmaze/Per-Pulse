/** Read a real spontaneous result from Home and Activity without creating work.
 * Explicit TEST_DOUBLE transport; native World, Kernel and browser persistence.
 * Copyright 2026 Lightmaze Contributors. Licensed under Apache-2.0.
 */
import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { mkdtemp, mkdir, rm } from 'node:fs/promises';
import { createServer } from 'node:net';
import { tmpdir } from 'node:os';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { createRequire } from 'node:module';
const root = resolve(dirname(fileURLToPath(import.meta.url)), '../..');
const { chromium } = createRequire(import.meta.url)(process.env.PLAYWRIGHT_MODULE || 'playwright');
const directory = await mkdtemp(join(tmpdir(), 'per-pulse-background-result-'));
const screenshots = process.env.PER_PULSE_ATTENTION_SCREENSHOTS;
if (screenshots) await mkdir(screenshots, { recursive: true });
const socket = createServer();
await new Promise(done => socket.listen(0, '127.0.0.1', done));
const port = socket.address().port;
await new Promise(done => socket.close(done));
const base = `http://127.0.0.1:${port}`;
let serverLog = '', browser, page, server;
const errors = [], checks = [];
const start = () => {
  const child = spawn(process.env.PER_PULSE_PYTHON || join(root, '.venv/bin/python'), [
    join(root, 'tests/browser/attention_server.py'), '--data-dir', directory,
    '--port', String(port), '--spontaneous',
  ], { cwd: root, stdio: ['ignore', 'pipe', 'pipe'] });
  child.stdout.on('data', value => { serverLog += value; });
  child.stderr.on('data', value => { serverLog += value; });
  return child;
};
const stop = async child => {
  if (!child || child.exitCode !== null || child.signalCode !== null) return;
  child.kill('SIGTERM');
  const force = setTimeout(() => child.kill('SIGKILL'), 10000);
  await new Promise(done => child.once('exit', done));
  clearTimeout(force);
};
const wait = async (probe, label, timeout = 20000) => {
  const until = Date.now() + timeout;
  while (Date.now() < until) {
    try { if (await probe()) return; } catch {}
    await new Promise(done => setTimeout(done, 75));
  }
  throw new Error(`Timed out: ${label}\n${serverLog}`);
};
const get = async path => (await (await fetch(base + path)).json());
try {
  server = start();
  await wait(async () => (await fetch(base + '/api/health')).ok, 'native server');
  const response = await fetch(base + '/api/pages', { method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ title: 'Continuing observation', instruction: 'An explicitly synthetic bounded observation.' }) });
  assert.equal(response.status, 201);
  const work = (await response.json()).page.id;
  let activation;
  await wait(async () => {
    activation = (await get('/api/pulse')).recentActivations.find(item => item.pageId === work);
    return activation?.status === 'settled' && activation.resultMessageId;
  }, 'one settled spontaneous answer');
  assert.equal(activation.messageId, null);
  const originalMessages = (await get('/api/pages/' + work)).messages;
  assert.equal(originalMessages.filter(message => message.role === 'assistant').length, 2);
  browser = await chromium.launch({ executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE || undefined,
    args: process.env.PLAYWRIGHT_CHROMIUM_ARGS ? JSON.parse(process.env.PLAYWRIGHT_CHROMIUM_ARGS) : undefined });
  page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
  page.on('pageerror', error => errors.push(error.message));
  await page.goto(base);
  const homeDraft = 'Keep this Home draft while reviewing the background answer.';
  await page.getByRole('textbox', { name: 'Message Home', exact: true }).fill(homeDraft);
  await page.locator('.home-continuation-open').waitFor();
  if (screenshots) await page.screenshot({ path: join(screenshots, 'background-result-home.png') });
  await page.locator('.home-continuation-source').click();
  await wait(() => new URL(page.url()).hash === '#message-' + activation.sourceEventId, 'exact original source');
  const workDraft = 'Continue from this exact observation.';
  await page.locator('.composer textarea').fill(workDraft);
  await page.locator('.return-to-thread').click();
  await page.locator('.home-continuation-open').waitFor();
  assert.equal(await page.locator('.composer textarea').inputValue(), homeDraft);
  await page.locator('.home-continuation-open').click();
  await wait(() => new URL(page.url()).hash === '#message-' + activation.resultMessageId, 'exact background answer');
  assert.equal(await page.locator('.composer textarea').inputValue(), workDraft);
  checks.push('Home opens the original source and separate background answer while keeping both drafts.');
  await page.getByRole('button', { name: 'Activity', exact: true }).click();
  await page.getByRole('button', { name: 'Continuing observation · Open result', exact: true }).click();
  await wait(() => new URL(page.url()).hash === '#message-' + activation.resultMessageId, 'Activity answer');
  await page.getByRole('button', { name: 'Settings', exact: true }).click();
  await page.locator('#interface-language').selectOption('zh-CN');
  await page.keyboard.press('Escape');
  await page.setViewportSize({ width: 390, height: 844 });
  await page.getByRole('button', { name: '活动', exact: true }).click();
  await page.getByRole('button', { name: 'Continuing observation · 打开结果', exact: true }).click();
  assert.equal(new URL(page.url()).hash, '#message-' + activation.resultMessageId);
  assert.equal(await page.locator('.composer textarea').inputValue(), workDraft);
  assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
  if (screenshots) await page.screenshot({ path: join(screenshots, 'background-result-mobile-zh.png') });
  await page.locator('.return-to-thread').click();
  await wait(() => new URL(page.url()).pathname === '/', 'return to Home');
  assert.equal(await page.locator('.composer textarea').inputValue(), homeDraft);
  assert.equal(await page.locator('.home-continuation-open').count(), 0);
  checks.push('Activity opens the same answer in English and Chinese; visiting it does not resurrect an older Home item.');
  await stop(server); server = start();
  await wait(async () => (await fetch(base + '/api/health')).ok, 'reopen same World');
  await page.goto(base + '/page/' + work + '#message-' + activation.resultMessageId);
  await page.locator('#message-' + activation.resultMessageId).waitFor();
  assert.equal(await page.locator('.composer textarea').inputValue(), workDraft);
  const reopened = await get('/api/pulse');
  assert.equal(reopened.recentActivations[0].resultMessageId, activation.resultMessageId);
  assert.equal(reopened.budget.used, 1);
  assert.deepEqual((await get('/api/pages/' + work)).messages, originalMessages);
  checks.push('Restart preserves the exact result, draft and single admission without creating another turn.');
  assert.deepEqual(errors, []);
  console.log(JSON.stringify({ checks, errors, viewports: ['1280x900', '390x844'] }, null, 2));
} catch (error) {
  if (screenshots && page) await page.screenshot({ path: join(screenshots, 'background-result-failure.png') }).catch(() => {});
  throw error;
} finally {
  await browser?.close(); await stop(server); await rm(directory, { recursive: true, force: true });
}
