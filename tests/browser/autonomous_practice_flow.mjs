/** Synthetic model decisions on the actual World, Kernel, files and restart path.
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
const directory = await mkdtemp(join(tmpdir(), 'per-pulse-autonomous-'));
const output = process.env.PER_PULSE_AUTONOMOUS_SCREENSHOTS || await mkdtemp(join(tmpdir(), 'per-pulse-autonomous-browser-'));
await mkdir(output, { recursive: true });
const socket = createServer();
await new Promise(done => socket.listen(0, '127.0.0.1', done));
const port = socket.address().port;
await new Promise(done => socket.close(done));
const base = `http://127.0.0.1:${port}`;
let serverLog = '';
const start = () => {
  const child = spawn(process.env.PER_PULSE_PYTHON || join(root, '.venv/bin/python'), [
    join(root, 'tests/browser/autonomous_practice_server.py'), '--data-dir', directory, '--port', String(port),
  ], { cwd: root, env: { ...process.env, PYTHONPATH: join(root, 'src') }, stdio: ['ignore', 'pipe', 'pipe'] });
  child.stderr.on('data', data => { serverLog += data; });
  return child;
};
const stop = async child => {
  if (child.exitCode !== null || child.signalCode !== null) return;
  await new Promise(done => { child.once('exit', done); child.kill('SIGTERM'); });
};
const get = async path => (await fetch(base + path)).json();
const wait = async (read, ready, label) => {
  const end = Date.now() + 20000;
  while (Date.now() < end) {
    try { const value = await read(); if (ready(value)) return value; } catch {}
    await new Promise(done => setTimeout(done, 80));
  }
  throw new Error(`Timed out: ${label}\n${serverLog}`);
};
const checks = [];
const errors = [];
let server = start();
let browser;
try {
  await wait(() => fetch(base + '/api/health'), value => value.ok, 'native server');
  const fresh = await get('/__autonomous/status');
  assert.equal(fresh.requests, 0);
  assert.deepEqual(fresh.departures, []);
  browser = await chromium.launch({ executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE || undefined,
    args: process.env.PLAYWRIGHT_CHROMIUM_ARGS ? JSON.parse(process.env.PLAYWRIGHT_CHROMIUM_ARGS) : undefined });
  const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
  page.on('pageerror', error => errors.push(error.message));
  await page.goto(base);
  await page.getByRole('button', { name: 'Activity', exact: true }).click();
  await page.getByRole('spinbutton', { name: 'Requests per week', exact: true }).fill('1');
  await page.getByRole('button', { name: 'Start autonomous practice', exact: true }).click();
  await wait(() => get('/api/pulse/weekly-usage'), value => value.usage.consumed === 1 && value.usage.remaining === 0, 'first request exhausts allowance');
  await wait(() => get('/__autonomous/status'), value => value.departures[0]?.status === 'uncertain', 'original turn retains its partial outcome');
  await page.getByText('Waiting for autonomous allowance', { exact: true }).waitFor();
  await page.getByRole('spinbutton', { name: 'Requests per week', exact: true }).fill('20');
  await page.getByRole('button', { name: 'Save limit', exact: true }).click();
  const pages = await wait(() => get('/api/bootstrap'), value => value.pages.some(p => p.title === 'A small practice'), 'model-created Work');
  const work = pages.pages.find(p => p.title === 'A small practice').id;
  await wait(() => get('/api/pages/' + work), value => value.artifacts.length === 1 && value.page.status === 'idle', 'native file and completed Work');
  const home = await wait(() => get('/api/pages/home'), value => value.messages.filter(m => m.role === 'assistant').length >= 2, 'native report and Home response');
  const complete = await get('/__autonomous/status');
  const usage = await wait(() => get('/api/pulse/weekly-usage'), value => value.usage.consumed === complete.requests
    && value.usage.states.completed === complete.requests, 'unified physical request accounting');
  assert.equal(complete.departures.length, 1);
  assert.equal(complete.departures[0].source, 'self');
  assert.equal(complete.departures[0].parent, null);
  assert.deepEqual(complete.userRoots, []);
  assert.ok(usage.usage.consumed >= 6 && usage.usage.consumed <= 20);
  checks.push('one explicit enable starts from SELF without a user message and reaches a native Work, file and report within one allowance');
  await page.getByRole('button', { name: 'Close dialog', exact: true }).click();
  assert.match(await page.locator('#message-' + complete.departures[0].id).getAttribute('class'), /message-system/);
  const report = home.messages.find(message => message.activation === 'work_report');
  assert.ok(report?.sourceMessageId && report.sourcePageId === work);
  await page.locator('#message-' + report.id).locator('.source-line a.session-link').click();
  await page.getByRole('heading', { name: 'A small practice', exact: true }).waitFor();
  assert.equal(await page.locator('.message-user').count(), 0);
  await page.locator('.message-system').filter({ hasText: 'AUTONOMOUS_PRACTICE_FIXTURE' }).waitFor();
  await page.getByRole('button', { name: /practice\.txt/ }).click();
  await page.getByText('A persisted result from explicitly synthetic autonomous practice.', { exact: true }).waitFor();
  await page.screenshot({ path: join(output, 'autonomous-file-desktop.png') });
  await page.getByRole('button', { name: /practice\.txt/ }).click();
  checks.push('Home shows the SELF origin as workspace activity and the actual report opens its producing Work and exact file');
  const draft = 'Keep this practice draft across pause and restart.';
  await page.getByRole('textbox', { name: /^Message / }).fill(draft);
  await page.getByRole('button', { name: 'Activity', exact: true }).click();
  await page.getByRole('button', { name: 'Pause autonomous practice', exact: true }).click();
  await page.getByText('Autonomous model requests are disabled.', { exact: true }).waitFor();
  await page.getByRole('button', { name: 'Close dialog', exact: true }).click();
  await page.getByRole('button', { name: 'Settings', exact: true }).click();
  await page.locator('#interface-language').selectOption('zh-CN');
  await page.getByRole('button', { name: '关闭对话框', exact: true }).click();
  await page.getByRole('button', { name: '活动', exact: true }).click();
  await page.getByRole('button', { name: '开始自主实践', exact: true }).click();
  await wait(() => get('/api/pulse/weekly-usage'), value => value.usage.enabled, 'Chinese resume');
  assert.equal((await get('/__autonomous/status')).requests, complete.requests);
  assert.equal((await get('/__autonomous/status')).departures[0].id, complete.departures[0].id);
  await page.getByRole('button', { name: '暂停自主实践', exact: true }).click();
  await page.getByText('自主模型请求目前未启用。', { exact: true }).waitFor();
  checks.push('English and Chinese pause and resume keep the same first departure and retain consumed allowance');
  await stop(server);
  server = start();
  await wait(() => fetch(base + '/api/health'), value => value.ok, 'restarted native server');
  await page.reload();
  await page.locator('.composer textarea').waitFor();
  assert.equal(await page.locator('.composer textarea').inputValue(), draft);
  const retained = await get('/api/pulse/weekly-usage');
  assert.equal(retained.usage.enabled, false);
  assert.equal(retained.usage.consumed, usage.usage.consumed);
  const restarted = await get('/__autonomous/status');
  assert.equal(restarted.requests, 0);
  assert.equal(restarted.departures[0].id, complete.departures[0].id);
  await page.setViewportSize({ width: 390, height: 844 });
  await page.getByRole('button', { name: /practice\.txt/ }).click();
  await page.getByText('A persisted result from explicitly synthetic autonomous practice.', { exact: true }).waitFor();
  assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth));
  await page.screenshot({ path: join(output, 'autonomous-file-mobile.png') });
  checks.push('SQLite restart preserves pause, usage, Work draft, SELF identity and the real file with no replay on mobile');
  assert.deepEqual(errors, []);
  console.log(JSON.stringify({ passed: checks.length, checks, output,
    modelEvidence: 'explicit synthetic HTTP decisions; production World/Kernel/SQLite/files/accounting' }));
} finally {
  await browser?.close();
  await stop(server);
  await rm(directory, { recursive: true, force: true });
}
