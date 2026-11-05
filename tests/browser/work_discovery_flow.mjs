/** Existing Home reads discover native Work without manual refresh or new polling.
 * Provider choices are an explicit synthetic HTTP fixture; World/Kernel/UI are real.
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
const directory = await mkdtemp(join(tmpdir(), 'per-pulse-work-discovery-'));
const screenshots = process.env.PER_PULSE_WORK_DISCOVERY_SCREENSHOTS;
if (screenshots) await mkdir(screenshots, { recursive: true });
const socket = createServer();
await new Promise(done => socket.listen(0, '127.0.0.1', done));
const port = socket.address().port;
await new Promise(done => socket.close(done));
const base = `http://127.0.0.1:${port}`;
const title = 'A discovered source check';
const checks = [], errors = [];
let server, serverLog = '', browser, page, releaseRead = () => {}, releaseReconnect = () => {};
const start = (offline = false) => {
  const child = spawn(process.env.PER_PULSE_PYTHON || join(root, '.venv/bin/python'), [
    join(root, 'tests/browser/work_discovery_server.py'), '--data-dir', directory,
    '--port', String(port), ...(offline ? ['--offline'] : []),
  ], { cwd: root, stdio: ['ignore', 'pipe', 'pipe'] });
  child.stdout.on('data', data => { serverLog += data; });
  child.stderr.on('data', data => { serverLog += data; });
  return child;
};
const stop = async child => {
  if (!child || child.exitCode !== null || child.signalCode !== null) return;
  const stopped = new Promise(done => child.once('exit', done));
  child.kill('SIGTERM');
  const force = setTimeout(() => child.kill('SIGKILL'), 10000);
  await stopped;
  clearTimeout(force);
};
const wait = async (probe, label, timeout = 20000) => {
  const deadline = Date.now() + timeout;
  while (Date.now() < deadline) {
    try { if (await probe()) return; } catch {}
    await new Promise(done => setTimeout(done, 75));
  }
  throw new Error('Timed out: ' + label + '\n' + serverLog);
};
const get = async path => { const response = await fetch(base + path); assert.ok(response.ok); return response.json(); };
const shot = async name => { if (screenshots) await page.screenshot({ path: join(screenshots, name + '.png') }); };
try {
  server = start();
  await wait(async () => (await fetch(base + '/api/health')).ok, 'native service');
  const identity = await get('/api/bootstrap');
  browser = await chromium.launch({ executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE || undefined,
    args: process.env.PLAYWRIGHT_CHROMIUM_ARGS ? JSON.parse(process.env.PLAYWRIGHT_CHROMIUM_ARGS) : undefined });
  page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
  page.on('pageerror', error => errors.push(error.message));
  let bootstrapReads = 0;
  page.on('request', request => { if (new URL(request.url()).pathname === '/api/bootstrap') bootstrapReads++; });
  await page.goto(base);
  const composer = page.locator('.composer textarea');
  await composer.waitFor();
  await wait(() => bootstrapReads >= 2, 'existing initial workspace reads');
  const initialReads = bootstrapReads;
  const navigate = async name => {
    const navigation = page.getByLabel('Workspace navigation');
    if (!await navigation.isVisible()) await page.getByRole('button', { name: 'Toggle navigation', exact: true }).click();
    await navigation.getByRole('link', { name, exact: true }).click();
  };
  const send = async content => {
    await composer.fill(content);
    await page.getByRole('button', { name: 'Send message', exact: true }).click();
  };
  await send('CREATE_WORK_DISCOVERY: Open one focused Work for this source.');
  let workId;
  await wait(async () => {
    const created = (await get('/__work-discovery/status')).creates;
    workId = created[0]?.workId;
    return created.length === 1 && created[0].status === 'delivered'
      && (await get('/api/pages/' + workId)).page.status === 'idle'
      && (await get('/api/pages/home')).page.status === 'idle';
  }, 'fast native create and scoped reply settle');
  const modelRequests = (await get('/__work-discovery/status')).requests;
  const homeDraft = 'Keep this Home draft while the sidebar discovers the Work.';
  await composer.fill(homeDraft);
  const workLink = page.getByLabel('Workspace navigation').getByRole('link', { name: title, exact: true });
  await workLink.waitFor({ timeout: 20000 });
  assert.equal(new URL(page.url()).pathname, '/');
  assert.equal(await composer.inputValue(), homeDraft);
  assert.ok(await composer.evaluate(element => element === document.activeElement));
  assert.equal(bootstrapReads, initialReads + 1, 'discovery reuses the next existing Home read');
  assert.equal((await get('/__work-discovery/status')).requests, modelRequests);
  await shot('work-discovery-home-desktop');
  checks.push('A native Work that already finished appears in the sidebar through the existing Home read, with no manual refresh, extra read, model call or lost draft/focus.');

  await navigate(title);
  await page.getByText('The focused check finished. [Explicit synthetic fixture]', { exact: true }).waitFor();
  const workDraft = 'Keep this focused Work draft.';
  await composer.fill(workDraft);
  let intercepted;
  const captured = new Promise(done => { intercepted = done; });
  const held = new Promise(done => { releaseRead = done; });
  await page.route('**/api/bootstrap', async route => {
    const response = await route.fetch();
    intercepted(); await held;
    await route.fulfill({ response }).catch(() => {});
  }, { times: 1 });
  await navigate('Home');
  await captured;
  await send('HOLD_DISCOVERY_REPLY: Continue Home while an older workspace read is pending.');
  await wait(async () => (await get('/__work-discovery/status')).entered, 'real Home reply barrier');
  const homeActivity = page.locator('.page-activity .activity-indicator');
  await wait(async () => await homeActivity.innerText() === 'Working', 'the current Home view is working');
  await composer.fill(homeDraft);
  const lateRead = page.waitForResponse(response => new URL(response.url()).pathname === '/api/bootstrap');
  releaseRead(); await lateRead;
  await page.waitForTimeout(200);
  assert.equal(await homeActivity.innerText(), 'Working', 'an old idle summary cannot roll back the new Home turn');
  assert.equal(await composer.inputValue(), homeDraft);
  assert.ok(await composer.evaluate(element => element === document.activeElement));
  await fetch(base + '/__work-discovery/release', { method: 'POST' });
  await wait(async () => await homeActivity.innerText() === 'Ready', 'Home settles');
  checks.push('A delayed Home workspace observation cannot overwrite a newer local working state or disturb its draft and focus.');

  await navigate(title);
  await page.route('**/api/bootstrap', route => route.fulfill({ status: 503, contentType: 'application/json', body: '{"error":"explicit read failure"}' }), { times: 1 });
  const failingRead = page.waitForResponse(response => new URL(response.url()).pathname === '/api/bootstrap');
  await navigate('Home');
  const failedRead = await failingRead;
  assert.equal(failedRead.status(), 503);
  assert.equal(await workLink.count(), 1);
  assert.equal(await composer.inputValue(), homeDraft);
  await navigate(title);
  const recoveryRead = page.waitForResponse(response => new URL(response.url()).pathname === '/api/bootstrap' && response.ok());
  await navigate('Home'); await recoveryRead;
  assert.equal(await workLink.count(), 1);
  checks.push('A failed workspace read preserves known pages and drafts; the next existing Home observation remains usable.');

  await navigate(title);
  await stop(server); server = start(true);
  await wait(async () => (await fetch(base + '/api/health')).ok, 'real same-World restart');
  const restarted = await get('/api/bootstrap');
  assert.equal(restarted.workspaceId, identity.workspaceId);
  assert.notEqual(restarted.serviceInstanceId, identity.serviceInstanceId);
  const restartRead = page.waitForResponse(response => new URL(response.url()).pathname === '/api/bootstrap');
  await navigate('Home');
  assert.equal((await (await restartRead).json()).serviceInstanceId, restarted.serviceInstanceId);
  await page.locator('.sync-notice').waitFor();
  assert.equal(await page.locator('.provider-indicator').innerText(), 'AI connected', 'background reads cannot silently adopt another process');
  let reconnectCaptured;
  const reconnectCapturedRead = new Promise(done => { reconnectCaptured = done; });
  const reconnectHeld = new Promise(done => { releaseReconnect = done; });
  let reconnectReads = 0;
  const reconnectRoute = async route => {
    const response = await route.fetch();
    if (++reconnectReads === 1) {
      reconnectCaptured(); await reconnectHeld;
    }
    await route.fulfill({ response }).catch(() => {});
  };
  await page.route('**/api/bootstrap', reconnectRoute);
  await page.getByRole('button', { name: 'Refresh updates', exact: true }).click();
  await reconnectCapturedRead;
  // Remounting Home uses its existing immediate observation while the explicit
  // reconnect remains pending. Its old expected identity rejects the new one.
  await navigate(title);
  const overlappingRead = page.waitForResponse(response => new URL(response.url()).pathname === '/api/bootstrap');
  await navigate('Home');
  assert.equal((await (await overlappingRead).json()).serviceInstanceId, restarted.serviceInstanceId);
  await page.waitForTimeout(100);
  assert.equal(await page.locator('.provider-indicator').innerText(), 'AI connected');
  const reconnectedRead = page.waitForResponse(response => new URL(response.url()).pathname === '/api/bootstrap');
  releaseReconnect(); await reconnectedRead;
  await page.unroute('**/api/bootstrap', reconnectRoute);
  await page.getByRole('textbox', { name: 'Message Home', exact: true }).waitFor();
  await page.getByText('The held Home reply finished. [Explicit synthetic fixture]', { exact: true }).waitFor();
  await wait(async () => (await page.locator('.provider-indicator').innerText()) === 'AI disconnected', 'explicit reconnect adopts the restarted service');
  await wait(async () => await page.locator('.sync-notice').count() === 0, 'reconnect recovers the current page view and clears the notice');
  assert.equal(await composer.inputValue(), homeDraft);
  checks.push('A scheduled Home observation overlapping explicit reconnect cannot suppress adoption of the new service or leave the current page unavailable.');
  await page.setViewportSize({ width: 390, height: 844 });
  await navigate(title);
  await page.getByText('The focused check finished. [Explicit synthetic fixture]', { exact: true }).waitFor();
  assert.equal(await composer.inputValue(), workDraft);
  assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
  await composer.scrollIntoViewIfNeeded();
  await shot('work-discovery-restarted-mobile');
  assert.equal((await get('/__work-discovery/status')).requests, 0);
  checks.push('A real service restart is fenced until explicit refresh; the discovered Work, result and drafts remain available offline on a phone.');
  assert.deepEqual(errors, []);
  console.log(JSON.stringify({ checks, errors, viewports: ['1280x900', '390x844'], modelEvidence: 'explicit_synthetic_transport_only' }, null, 2));
} catch (error) {
  if (screenshots && page) await page.screenshot({ path: join(screenshots, 'work-discovery-failure.png') }).catch(() => {});
  throw error;
} finally {
  releaseRead(); releaseReconnect();
  await browser?.close(); await stop(server);
  await rm(directory, { recursive: true, force: true });
}
