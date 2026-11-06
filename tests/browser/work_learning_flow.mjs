/** Real Work graph learning, content delivery and operator removal in Chromium.
 * Only the model HTTP transport is synthetic; World, Kernel and learning are real.
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
const directory = await mkdtemp(join(tmpdir(), 'per-pulse-work-learning-'));
const screenshots = process.env.PER_PULSE_WORK_LEARNING_SCREENSHOTS;
const socket = createServer();
await new Promise(done => socket.listen(0, '127.0.0.1', done));
const port = socket.address().port;
await new Promise(done => socket.close(done));
const base = 'http://127.0.0.1:' + port;
let serverLog = '', browser;
const start = () => {
  const child = spawn(process.env.PER_PULSE_PYTHON || join(root, '.venv/bin/python'), [
    join(root, 'tests/browser/work_learning_server.py'), '--data-dir', directory, '--port', String(port),
  ], { cwd: root, stdio: ['ignore', 'pipe', 'pipe'] });
  child.stdout.on('data', data => { serverLog += data; });
  child.stderr.on('data', data => { serverLog += data; });
  return child;
};
const stop = async child => {
  if (child.exitCode !== null || child.signalCode !== null) return;
  child.kill('SIGTERM');
  const timer = setTimeout(() => child.kill('SIGKILL'), 10000);
  await new Promise(done => child.once('exit', done));
  clearTimeout(timer);
};
const wait = async (condition, label) => {
  const deadline = Date.now() + 20000;
  while (Date.now() < deadline) {
    try { if (await condition()) return; } catch {}
    await new Promise(done => setTimeout(done, 75));
  }
  throw new Error('Timed out: ' + label + '\n' + serverLog);
};
const json = async path => { const response = await fetch(base + path); assert.ok(response.ok); return response.json(); };
const answerCount = async id => (await json('/api/pages/' + id)).messages.filter(m => m.role === 'assistant').length;
let server = start();
const checks = [];
try {
  await wait(async () => (await fetch(base + '/api/health')).ok, 'native service');
  assert.equal((await fetch(base + '/api/pulse/weekly-usage', {
    method: 'PATCH', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ enabled: true, weeklyLimit: 100 }),
  })).status, 200);
  browser = await chromium.launch({ executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE || undefined,
    args: process.env.PLAYWRIGHT_CHROMIUM_ARGS ? JSON.parse(process.env.PLAYWRIGHT_CHROMIUM_ARGS) : undefined });
  const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.goto(base);
  const create = async (title, instruction) => {
    // Bootstrap actual native histories; exercise the new Activity flow below.
    const response = await fetch(base + '/api/pages', { method: 'POST',
      headers: { 'content-type': 'application/json' }, body: JSON.stringify({ title, instruction }) });
    assert.equal(response.status, 201);
    const id = (await response.json()).page.id;
    await wait(async () => await answerCount(id) === 1, 'first local answer');
    return id;
  };
  const a = await create('Learning source', 'WORK_A_HISTORY');
  const b = await create('Learning receiver', 'WORK_B_HISTORY');
  const isEdge = edge => edge.sourcePageId === a && edge.targetPageId === b;
  await wait(async () => (await json('/api/pulse')).connections.some(isEdge), 'learned connection');
  const initial = await json('/api/pulse');
  assert.equal(initial.connections.find(isEdge).origin, 'learned');
  assert.equal((await json('/__work/requests')).engramCount, 1);
  assert.equal(Object.keys(initial.spectrum.factors).length, 2);
  checks.push('two native Work histories learn a connection and get separate spectrum slots under one Engram');

  await page.goto(base + '/page/' + a);
  const composer = page.locator('.composer textarea');
  await page.locator('.message-assistant').first().waitFor();
  await composer.fill('Keep this draft while inspecting activity.');
  await page.getByRole('button', { name: 'Activity', exact: true }).click();
  let outgoing = page.getByRole('region', { name: 'Connections from this page', exact: true });
  await outgoing.getByText('Learned from activity', { exact: true }).waitFor();
  await outgoing.getByRole('button', { name: 'Learning receiver', exact: true }).waitFor();
  if (screenshots) { await mkdir(screenshots, { recursive: true }); await page.screenshot({ path: join(screenshots, 'work-learning-en.png') }); }
  await page.getByRole('button', { name: 'Close dialog', exact: true }).click();
  assert.equal(await composer.inputValue(), 'Keep this draft while inspecting activity.');
  await composer.fill('A_NEW_INPUT');
  await page.getByRole('button', { name: 'Send message', exact: true }).click();
  await wait(async () => await answerCount(b) === 2, 'native learned content propagation');
  const requests = (await json('/__work/requests')).requests;
  const receiver = JSON.stringify(requests.at(-1));
  assert.match(receiver, /WORK_B_HISTORY/);
  assert.match(receiver, /Synthetic A finding/);
  assert.doesNotMatch(receiver, /WORK_A_HISTORY|A_NEW_INPUT/);
  checks.push('Activity labels the learned edge; its next output reaches the receiver model with only the receiver history');

  await page.getByRole('button', { name: 'Activity', exact: true }).click();
  await outgoing.getByRole('button', { name: 'Remove', exact: true }).click();
  await wait(async () => !(await json('/api/pulse')).connections.some(isEdge), 'operator removal');
  await page.getByRole('button', { name: 'Close dialog', exact: true }).click();
  await composer.fill('Keep this after restart.');
  await stop(server); server = start();
  await wait(async () => (await fetch(base + '/api/health')).ok, 'restarted service');
  await page.reload();
  await composer.waitFor();
  assert.equal(await composer.inputValue(), 'Keep this after restart.');
  assert.equal((await json('/api/pulse')).connections.some(isEdge), false);
  const countBefore = await answerCount(a);
  const response = await fetch(base + '/api/pages/' + a + '/messages', {
    method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ content: 'A correction after restart.' }),
  });
  assert.equal(response.status, 202);
  await wait(async () => await answerCount(a) === countBefore + 1, 'new activity after restart');
  assert.equal((await json('/api/pulse')).connections.some(isEdge), false);
  assert.equal(await answerCount(b), 2);
  checks.push('removal, draft and learned identity survive restart; later activity cannot recreate the removed connection');

  await page.getByRole('button', { name: 'Settings', exact: true }).click();
  await page.locator('#interface-language').selectOption('zh-CN');
  await page.getByRole('button', { name: '关闭对话框', exact: true }).click();
  await page.setViewportSize({ width: 390, height: 844 });
  await page.getByRole('button', { name: '活动', exact: true }).click();
  outgoing = page.getByRole('region', { name: '此页面的计算连接', exact: true });
  await outgoing.getByText('页面可以从活动中学到连接。移除的连接会保持关闭，直到你重新连接。', { exact: true }).waitFor();
  assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
  if (screenshots) await page.screenshot({ path: join(screenshots, 'work-learning-zh-mobile.png') });
  await outgoing.locator('#pulse-target').selectOption(b);
  await outgoing.getByRole('button', { name: '连接', exact: true }).click();
  await wait(async () => (await json('/api/pulse')).connections.some(isEdge), 'explicit reconnect');
  assert.equal((await json('/api/pulse')).connections.find(isEdge).origin, 'configured');
  assert.equal(await answerCount(b), 2, 'reconnect must not replay old source outputs');
  await page.getByRole('button', { name: '关闭对话框', exact: true }).click();
  await composer.fill('Continue A after reconnect.');
  await page.getByRole('button', { name: '发送消息', exact: true }).click();
  await wait(async () => await answerCount(b) === 3, 'reconnected native content delivery');
  checks.push('Chinese mobile Activity reconnects the pages explicitly; only a later result propagates');
  assert.deepEqual(errors, []);
  console.log(JSON.stringify({ passed: checks.length, checks, provider: 'explicit synthetic HTTP fixture' }));
} catch (error) {
  console.error(serverLog);
  throw error;
} finally {
  await browser?.close();
  await stop(server);
  await rm(directory, { recursive: true, force: true });
}
