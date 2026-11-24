/** Read older native history and open its original source in the ordinary UI.
 * Copyright 2026 Lightmaze Contributors. Licensed under Apache-2.0.
 * All long-history content is an explicit authored fixture. No model is used.
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
const require = createRequire(import.meta.url);
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const directory = await mkdtemp(join(tmpdir(), 'per-pulse-history-'));
const screenshots = process.env.PER_PULSE_HISTORY_SCREENSHOTS;
if (screenshots) await mkdir(screenshots, { recursive: true });
const socket = createServer();
await new Promise(resolve => socket.listen(0, '127.0.0.1', resolve));
const port = socket.address().port;
await new Promise(resolve => socket.close(resolve));
const base = 'http://127.0.0.1:' + port;
const checks = [];
const errors = [];
let serverLog = '';
let browser;
let page;
const server = spawn(process.env.PER_PULSE_PYTHON || join(root, '.venv/bin/python'), [
  join(root, 'tests/browser/history_server.py'), '--data-dir', directory, '--port', String(port),
], { cwd: root, stdio: ['ignore', 'pipe', 'pipe'] });
server.stderr.on('data', data => { serverLog += data; });
server.stdout.on('data', data => { serverLog += data; });
const wait = async (fn, label, timeout = 20000) => {
  const deadline = Date.now() + timeout;
  while (Date.now() < deadline) {
    try { if (await fn()) return; } catch {}
    await new Promise(resolve => setTimeout(resolve, 80));
  }
  throw new Error('Timed out: ' + label + '\n' + serverLog);
};
try {
  await wait(async () => (await fetch(base + '/api/health')).ok, 'native fixture ready', 40000);
  const fixture = await (await fetch(base + '/__history/fixture')).json();
  browser = await chromium.launch({
    executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE || undefined,
    args: process.env.PLAYWRIGHT_CHROMIUM_ARGS ? JSON.parse(process.env.PLAYWRIGHT_CHROMIUM_ARGS) : undefined,
  });
  const context = await browser.newContext({ viewport: { width: 1280, height: 900 } });
  page = await context.newPage();
  page.on('pageerror', error => errors.push(error.message));
  await page.goto(base);
  await page.getByRole('navigation', { name: 'Pages', exact: true })
    .getByRole('link', { name: /^Field notebook/ }).click();
  await page.getByRole('heading', { name: 'Field notebook', exact: true }).waitFor();
  const draft = 'Keep this unsent question while I read the earlier field notes.';
  const composer = page.getByRole('textbox', { name: 'Message Field notebook', exact: true });
  await composer.fill(draft);
  const open = page.getByRole('button', { name: 'Earlier messages', exact: true });
  const dialog = page.getByRole('dialog', { name: 'Earlier messages', exact: true });
  const historyIds = () => dialog.locator('[data-history-message-id]').evaluateAll(nodes => nodes.map(node => node.dataset.historyMessageId));

  // A failed read can be retried without changing or submitting the draft.
  await page.route('**/api/pages/*/messages?*', route => route.fulfill({
    status: 503, contentType: 'application/json', body: JSON.stringify({ error: { code: 'network_error', message: 'Deliberate history-read outage.' } }),
  }), { times: 1 });
  await open.click();
  await dialog.getByRole('alert').waitFor();
  await dialog.getByRole('button', { name: 'Try again', exact: true }).click();
  await wait(async () => (await historyIds()).length === 100, 'first older range');
  assert.deepEqual(await historyIds(), fixture.messageIds.slice(-600, -500));
  assert.equal(await composer.inputValue(), draft);
  assert.doesNotMatch(await dialog.innerText(), /SIBLING_ONLY_PRIVATE_HISTORY|HOME_ONLY_HISTORY/);
  if (screenshots) await page.screenshot({ path: join(screenshots, 'history-desktop.png') });
  checks.push('failed older-history reads retry without a write, lost draft or another Work transcript');

  const arrived = await fetch(base + '/api/pages/' + fixture.workId + '/messages', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ content: 'New explicit input while the old range is open.' }),
  });
  assert.equal(arrived.status, 202);
  const allOlder = [...await historyIds()];
  const previousRanges = [];
  while (await dialog.getByRole('button', { name: 'Older messages', exact: true }).isEnabled()) {
    const previous = await historyIds();
    previousRanges.push(previous);
    await dialog.getByRole('button', { name: 'Older messages', exact: true }).click();
    await wait(async () => { const ids = await historyIds(); return ids.length && ids[0] !== previous[0]; }, 'next older range');
    allOlder.unshift(...await historyIds());
  }
  assert.deepEqual(allOlder, fixture.messageIds.slice(0, -500));
  await dialog.getByText('This is the beginning of the conversation.', { exact: true }).waitFor();
  await dialog.getByRole('button', { name: 'Newer messages', exact: true }).click();
  await wait(async () => (await historyIds())[0] === previousRanges.at(-1)[0], 'newer range reread');
  assert.deepEqual(await historyIds(), previousRanges.at(-1));
  await dialog.getByRole('button', { name: 'Older messages', exact: true }).click();
  await wait(async () => (await historyIds())[0] === fixture.messageIds[0], 'beginning reread');
  checks.push('all 621 older messages remain reachable exactly once while a new message arrives');

  await dialog.locator(`[data-history-message-id="${fixture.linkedMessageId}"]`)
    .getByRole('button', { name: 'View in conversation', exact: true }).click();
  await dialog.waitFor({ state: 'hidden' });
  const source = page.locator(`[data-message-id="${fixture.linkedMessageId}"]`);
  await source.waitFor();
  await page.waitForFunction(id => document.activeElement?.getAttribute('data-message-id') === id, fixture.linkedMessageId);
  assert.equal(new URL(page.url()).hash, '#message-' + fixture.linkedMessageId);
  assert.equal(await composer.inputValue(), draft);
  await source.getByRole('link', { name: 'Sibling notes', exact: true }).click();
  await page.getByRole('heading', { name: 'Sibling notes', exact: true }).waitFor();
  await page.getByRole('button', { name: 'Back to conversation', exact: true }).click();
  await source.waitFor();
  assert.equal(await composer.inputValue(), draft);
  await page.reload();
  await source.waitFor();
  assert.equal(await composer.inputValue(), draft);
  checks.push('opening an old message reuses its exact source, reference return, focus and persisted draft');

  // The reader must fit a phone and retain authored text in both languages.
  await page.setViewportSize({ width: 390, height: 844 });
  await open.click();
  await wait(async () => (await historyIds()).length === 100, 'mobile reader');
  const bounds = await dialog.boundingBox();
  assert.ok(bounds.x >= 0 && bounds.x + bounds.width <= 390);
  assert.ok(bounds.y >= 0 && bounds.y + bounds.height <= 844);
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
  const body = dialog.locator('.history-messages');
  await body.evaluate(node => { node.scrollTop = 300; });
  assert.ok(await body.evaluate(node => node.scrollTop) > 0);
  if (screenshots) await page.screenshot({ path: join(screenshots, 'history-mobile.png') });
  await page.keyboard.press('Escape');
  await dialog.waitFor({ state: 'hidden' });
  assert.equal(await open.evaluate(node => document.activeElement === node), true);
  await page.setViewportSize({ width: 1280, height: 900 });
  await page.getByRole('button', { name: 'Settings', exact: true }).click();
  await page.getByRole('combobox', { name: 'Interface language', exact: true }).selectOption('zh-CN');
  await page.getByRole('button', { name: '关闭对话框', exact: true }).click();
  await page.getByRole('button', { name: '较早的消息', exact: true }).click();
  const chinese = page.getByRole('dialog', { name: '较早的消息', exact: true });
  await wait(async () => await chinese.locator('[data-history-message-id]').count() === 100, 'Chinese reader');
  assert.match(await chinese.innerText(), /Field note/);
  await chinese.getByRole('button', { name: '更早的消息', exact: true }).waitFor();
  await page.keyboard.press('Escape');
  checks.push('mobile scrolling and keyboard focus work in English and Chinese without translating authored history');

  // A late response from the departed Work must not become another page's
  // older history. Browser Back bypasses the app's ordinary navigate wrapper.
  await page.getByRole('button', { name: '较早的消息', exact: true }).click();
  await wait(async () => await chinese.locator('[data-history-message-id]').count() === 100, 'history before delayed read');
  let release;
  let held = false;
  await page.route('**/api/pages/*/messages?*', async route => {
    const response = await route.fetch();
    await new Promise(resolve => { release = resolve; held = true; });
    await route.fulfill({ response }).catch(() => {});
  }, { times: 1 });
  await chinese.getByRole('button', { name: '更早的消息', exact: true }).click();
  await wait(() => held, 'history response held');
  await page.goBack();
  await wait(() => new URL(page.url()).pathname === '/', 'browser Back returns Home');
  await chinese.waitFor({ state: 'hidden' });
  release();
  await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
  assert.equal(await page.locator('[data-history-message-id]').count(), 0);
  assert.deepEqual(errors, []);
  checks.push('browser Back closes a pending old-Work reader and discards its delayed response');
  console.log(JSON.stringify({ passed: checks.length, checks, provider: 'none; authored fixtures, real native history' }));
} catch (error) {
  if (page && screenshots) await page.screenshot({ path: join(screenshots, 'history-failure.png') }).catch(() => {});
  throw error;
} finally {
  await browser?.close();
  if (server.exitCode === null && server.signalCode === null) {
    server.kill('SIGTERM');
    const force = setTimeout(() => server.kill('SIGKILL'), 10000);
    await new Promise(resolve => server.once('exit', resolve));
    clearTimeout(force);
  }
  await rm(directory, { recursive: true, force: true });
}
