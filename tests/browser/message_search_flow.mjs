/** Find original messages in a long native conversation; authored fixture only.
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
const require = createRequire(import.meta.url);
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const directory = await mkdtemp(join(tmpdir(), 'per-pulse-message-search-'));
const screenshots = process.env.PER_PULSE_HISTORY_SCREENSHOTS;
if (screenshots) await mkdir(screenshots, { recursive: true });
const socket = createServer();
await new Promise(resolve => socket.listen(0, '127.0.0.1', resolve));
const port = socket.address().port;
await new Promise(resolve => socket.close(resolve));
const base = 'http://127.0.0.1:' + port;
const checks = [], errors = [];
let serverLog = '', browser, page;
const server = spawn(process.env.PER_PULSE_PYTHON || join(root, '.venv/bin/python'), [
  join(root, 'tests/browser/history_server.py'), '--data-dir', directory, '--port', String(port),
], { cwd: root, stdio: ['ignore', 'pipe', 'pipe'] });
server.stderr.on('data', data => { serverLog += data; });
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
  page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
  page.on('pageerror', error => errors.push(error.message));
  await page.goto(base);
  await page.getByRole('navigation', { name: 'Pages', exact: true }).getByRole('link', { name: /^Field notebook/ }).click();
  await page.getByRole('heading', { name: 'Field notebook', exact: true }).waitFor();
  const draft = 'Keep this unsent question while I find the original evidence.';
  await page.locator('.composer textarea').fill(draft);
  const open = page.getByRole('button', { name: 'Find a message', exact: true });
  await open.click();
  const dialog = page.getByRole('dialog', { name: 'Find a message', exact: true });
  const input = dialog.getByRole('searchbox', { name: 'Words in this conversation', exact: true });
  assert.equal(await input.evaluate(node => document.activeElement === node), true);
  const ids = () => dialog.locator('[data-search-message-id]').evaluateAll(nodes => nodes.map(node => node.dataset.searchMessageId));
  const find = async query => {
    await input.fill(query);
    await dialog.getByRole('button', { name: 'Find', exact: true }).click();
  };
  await page.route('**/api/pages/*/messages/search?*', route => route.fulfill({
    status: 503, contentType: 'application/json', body: JSON.stringify({ error: { code: 'network_error', message: 'Deliberate message-search outage.' } }),
  }), { times: 1 });
  await find('Field note 0011');
  await dialog.getByRole('alert').waitFor();
  await dialog.getByRole('button', { name: 'Try again', exact: true }).click();
  await wait(async () => (await ids())[0] === fixture.linkedMessageId, 'original older match after retry');
  assert.equal((await ids()).length, 1);
  await dialog.getByRole('button', { name: 'View in conversation', exact: true }).click();
  await dialog.waitFor({ state: 'hidden' });
  await wait(async () => await page.evaluate(id => document.activeElement?.id === `message-${id}`, fixture.linkedMessageId), 'exact original message focus');
  assert.equal(await page.locator('.composer textarea').inputValue(), draft);
  await page.reload();
  await page.locator(`[data-message-id="${fixture.linkedMessageId}"]`).waitFor();
  assert.equal(await page.locator('.composer textarea').inputValue(), draft);
  checks.push('search retries, finds an old source, restores exact message focus and preserves the draft after reload');

  await open.click();
  await find('HOME_ONLY_HISTORY');
  await dialog.getByText('No messages match “HOME_ONLY_HISTORY” in this conversation.', { exact: true }).waitFor();
  await find('SIBLING_ONLY_PRIVATE_HISTORY');
  await dialog.getByText('No messages match “SIBLING_ONLY_PRIVATE_HISTORY” in this conversation.', { exact: true }).waitFor();
  await find('Field note');
  await wait(async () => (await ids()).length === 100, 'first matching range');
  assert.deepEqual(await ids(), fixture.messageIds.slice(-100).reverse());
  const sent = await fetch(base + `/api/pages/${fixture.workId}/messages`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ content: 'Field note added during search.' }),
  });
  assert.equal(sent.status, 202);
  await dialog.getByRole('button', { name: 'Older matches', exact: true }).click();
  await wait(async () => (await ids())[0] === fixture.messageIds.at(-101), 'stable older matching range');
  assert.deepEqual(await ids(), fixture.messageIds.slice(-200, -100).reverse());
  if (screenshots) await page.screenshot({ path: join(screenshots, 'message-search-desktop.png') });
  checks.push('search stays in the original Work and uses stable older ranges while new input arrives');

  let release, held = false;
  await page.route('**/api/pages/*/messages/search?q=Field%20note%2005*', async route => {
    const response = await route.fetch();
    await new Promise(resolve => { release = resolve; held = true; });
    await route.fulfill({ response }).catch(() => {});
  }, { times: 1 });
  await find('Field note 05');
  await wait(() => held, 'first query held');
  await find('Field note 0007');
  await wait(async () => (await ids())[0] === fixture.messageIds[8], 'replacement query');
  release();
  await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
  assert.deepEqual(await ids(), [fixture.messageIds[8]]);
  await page.keyboard.press('Escape');
  await dialog.waitFor({ state: 'hidden' });
  assert.equal(await open.evaluate(node => document.activeElement === node), true);
  checks.push('a delayed old query cannot replace current results, and Escape restores keyboard focus');

  await page.setViewportSize({ width: 390, height: 844 });
  await open.click();
  await find('Field note 0011');
  await wait(async () => (await ids())[0] === fixture.linkedMessageId, 'mobile match');
  assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
  assert.ok(await dialog.evaluate(node => node.scrollWidth <= node.clientWidth));
  if (screenshots) await page.screenshot({ path: join(screenshots, 'message-search-mobile.png') });
  await find('x'.repeat(200));
  await wait(async () => await dialog.getByRole('status').filter({hasText:'No messages match'}).count() === 1, 'long literal query');
  assert.ok(await dialog.evaluate(node => node.scrollWidth <= node.clientWidth));
  assert.ok(await dialog.locator('.history-messages').evaluate(node => node.scrollWidth <= node.clientWidth));
  await page.keyboard.press('Escape');
  await page.setViewportSize({ width: 1280, height: 900 });
  // Opening another saved source URL creates a real browser history entry in
  // the same Work. The in-app source picker intentionally replaces its anchor.
  await page.goto(base + `/page/${fixture.workId}#message-${fixture.messageIds[10]}`);
  await page.locator(`[data-message-id="${fixture.messageIds[10]}"]`).waitFor();
  await page.getByRole('button', { name: 'Settings', exact: true }).click();
  await page.locator('#interface-language').selectOption('zh-CN');
  await page.getByRole('button', { name: '关闭对话框', exact: true }).click();
  await page.getByRole('button', { name: '查找消息', exact: true }).click();
  const chinese = page.getByRole('dialog', { name: '查找消息', exact: true });
  await chinese.getByRole('searchbox', { name: '这段对话中的文字', exact: true }).fill('Field note 0011');
  await chinese.getByRole('button', { name: '查找', exact: true }).click();
  await wait(async () => await chinese.locator('[data-search-message-id]').count() === 1, 'Chinese search');
  assert.match(await chinese.innerText(), /Field note 0011/);
  checks.push('phone layout and Chinese controls preserve the original authored message content');

  held = false;
  await page.route('**/api/pages/*/messages/search?*', async route => {
    const response = await route.fetch();
    await new Promise(resolve => { release = resolve; held = true; });
    await route.fulfill({ response }).catch(() => {});
  }, { times: 1 });
  await chinese.getByRole('searchbox').fill('Field note 0009');
  await chinese.getByRole('button', { name: '查找', exact: true }).click();
  await wait(() => held, 'query held before same-page Back');
  await page.goBack();
  assert.equal(new URL(page.url()).pathname, `/page/${fixture.workId}`);
  await chinese.waitFor({ state: 'hidden' });
  release();
  await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
  assert.equal(await page.locator('[data-search-message-id]').count(), 0);
  assert.equal(await page.locator('.composer textarea').inputValue(), draft);
  assert.deepEqual(errors, []);
  checks.push('Browser Back within the same Work closes a pending reader and discards its response');
  console.log(JSON.stringify({ passed: checks.length, checks, provider: 'none; authored fixtures, native search and source navigation' }));
} catch (error) {
  if (page && screenshots) await page.screenshot({ path: join(screenshots, 'message-search-failure.png') }).catch(() => {});
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
