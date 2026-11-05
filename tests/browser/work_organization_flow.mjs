/** Home organizes native Work while file writes and commands stay disabled.
 * Only provider choices are an explicit synthetic HTTP fixture.
 * World, Kernel, work outbox, conversation UI and restart persistence are real.
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
const directory = await mkdtemp(join(tmpdir(), 'per-pulse-work-organization-'));
const screenshots = process.env.PER_PULSE_WORK_ORGANIZATION_SCREENSHOTS;
if (screenshots) await mkdir(screenshots, { recursive: true });
const socket = createServer();
await new Promise(done => socket.listen(0, '127.0.0.1', done));
const port = socket.address().port;
await new Promise(done => socket.close(done));
const base = `http://127.0.0.1:${port}`;
const title = 'A focused source check';
const firstNote = 'The initial source check is available. [Explicit synthetic fixture]';
const laterNote = 'The same Work checked the second case. [Explicit synthetic fixture]';
let serverLog = '', server, browser, page;
const errors = [], checks = [];
const start = (offline = false) => {
  const child = spawn(process.env.PER_PULSE_PYTHON || join(root, '.venv/bin/python'), [
    join(root, 'tests/browser/work_organization_server.py'), '--data-dir', directory,
    '--port', String(port), '--no-workspace-actions', ...(offline ? ['--offline'] : []),
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
const wait = async (probe, label) => {
  const deadline = Date.now() + 20000;
  while (Date.now() < deadline) {
    try { if (await probe()) return; } catch {}
    await new Promise(done => setTimeout(done, 75));
  }
  throw new Error('Timed out: ' + label + '\n' + serverLog);
};
const get = async path => {
  const response = await fetch(base + path);
  assert.ok(response.ok, path);
  return response.json();
};
const view = id => get('/api/pages/' + id);
const evidence = () => get('/__work-organization/status');
const shot = async name => { if (screenshots) await page.screenshot({ path: join(screenshots, name + '.png') }); };

try {
  server = start();
  await wait(async () => (await fetch(base + '/api/health')).ok, 'native service');
  browser = await chromium.launch({ executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE || undefined,
    args: process.env.PLAYWRIGHT_CHROMIUM_ARGS ? JSON.parse(process.env.PLAYWRIGHT_CHROMIUM_ARGS) : undefined });
  page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
  page.on('pageerror', error => errors.push(error.message));
  await page.goto(base);
  await page.getByRole('textbox', { name: 'Message Home', exact: true }).waitFor();
  assert.equal(await page.title(), 'Per Pulse');
  assert.equal((await get('/api/bootstrap')).provider.workspaceActions, false);
  const composer = page.locator('.composer textarea');
  const send = async text => {
    await composer.fill(text);
    await page.getByRole('button', { name: 'Send message', exact: true }).click();
  };
  const navigate = async name => {
    const navigation = page.getByLabel('Workspace navigation');
    if (!await navigation.isVisible()) await page.getByRole('button', { name: 'Toggle navigation', exact: true }).click();
    await navigation.getByRole('link', { name, exact: true }).click();
  };

  await send('CREATE_WORK_BROWSER: Open a focused Work to inspect this question.');
  let work;
  await wait(async () => {
    work = (await get('/api/bootstrap')).pages.find(item => item.title === title);
    return Boolean(work);
  }, 'Home native create reaches one real Work');
  await wait(async () => (await view(work.id)).messages.some(item => item.role === 'assistant' && item.status === 'settled'), 'first Work result');
  await wait(async () => (await view('home')).messages.some(item => item.content.includes(firstNote) && item.status === 'settled'), 'first selected report reaches Home');
  assert.equal(new URL(page.url()).pathname, '/', 'native Work scheduling keeps the user in Home');
  assert.equal(await page.getByRole('button', { name: 'Allow once', exact: true }).count(), 0);
  const firstWork = await view(work.id);
  const homeDraft = 'Keep this Home draft while I inspect the original Work.';
  await composer.fill(homeDraft);
  // The current sidebar lists its last workspace snapshot. Use the existing
  // read-only refresh control to discover a Work created by the model.
  await page.getByRole('button', { name: 'Settings', exact: true }).click();
  await page.getByRole('button', { name: 'Check connection', exact: true }).click();
  await page.getByText('Connection checked.', { exact: true }).waitFor();
  await page.getByRole('button', { name: 'Close dialog', exact: true }).click();
  assert.equal(await composer.inputValue(), homeDraft);
  await navigate(title);
  await page.getByText('The first scoped check is complete. [Explicit synthetic fixture]', { exact: true }).waitFor();
  const workDraft = 'Keep my unsent Work comparison.';
  await composer.fill(workDraft);
  await shot('work-organization-first-desktop');
  await navigate('Home');
  assert.equal(await composer.inputValue(), homeDraft);
  checks.push('An ordinary Home message creates and runs one native Work with file actions disabled; an explicit workspace refresh reveals it without approval or losing the Home draft.');

  await send('CONTINUE_WORK_BROWSER: Continue the same focused Work with one more case.');
  await wait(async () => (await view(work.id)).messages.filter(item => item.role === 'assistant' && item.status === 'settled').length === 2, 'send continues the original Work');
  await wait(async () => (await view('home')).messages.some(item => item.content.includes(laterNote) && item.status === 'settled'), 'continued selected report reaches Home');
  const continuedWork = await view(work.id);
  assert.deepEqual(continuedWork.messages.slice(0, firstWork.messages.length), firstWork.messages);
  assert.deepEqual((await get('/api/bootstrap')).pages.filter(item => item.kind === 'work').map(item => item.id), [work.id]);
  const native = (await evidence()).nativeRequests;
  assert.equal(native.filter(item => item.op === 'create').length, 1);
  assert.equal(native.filter(item => item.op === 'send').length, 1);
  assert.equal(native.filter(item => item.op === 'report').length, 2);
  assert.ok(native.every(item => item.status === 'delivered' && item.workId === work.id));
  const reports = native.filter(item => item.op === 'report');
  for (const report of reports) {
    assert.equal(report.reportOrigin.work_id, work.id);
    assert.equal(report.reportOrigin.source_turn_id, report.sourceTurnId);
    assert.ok(report.sourceContext.startsWith('work:'));
    assert.ok(continuedWork.messages.some(item => item.id === report.sourceResultMessageId && item.role === 'assistant'));
    assert.ok((await view('home')).messages.some(item => item.id === report.eventId && item.content.includes(report.reportOrigin.note)));
  }
  await page.getByText(laterNote, { exact: false }).waitFor();
  await composer.fill(homeDraft);
  await shot('work-organization-report-home-desktop');
  await navigate(title);
  assert.equal(await composer.inputValue(), workDraft);
  await page.getByText('The second scoped check is complete. [Explicit synthetic fixture]', { exact: true }).waitFor();
  await navigate('Home');
  assert.equal(await composer.inputValue(), homeDraft);
  checks.push('Home lists and sends to the original Work; both selected reports carry verified native outbox, Work turn and result provenance.');

  const homeBeforeRestart = await view('home');
  await stop(server); server = start(true);
  await wait(async () => (await fetch(base + '/api/health')).ok, 'same World reopens offline');
  await page.reload();
  await composer.waitFor();
  assert.equal(await composer.inputValue(), homeDraft);
  assert.equal((await view('home')).provider.workspaceActions, false);
  assert.equal((await view('home')).provider.configured, false);
  assert.deepEqual((await view('home')).messages, homeBeforeRestart.messages);
  assert.deepEqual((await view(work.id)).messages, continuedWork.messages);
  assert.deepEqual((await evidence()).nativeRequests, native);
  assert.deepEqual((await evidence()).requests, []);
  await page.setViewportSize({ width: 390, height: 844 });
  await navigate(title);
  await page.getByText('The second scoped check is complete. [Explicit synthetic fixture]', { exact: true }).waitFor();
  assert.equal(await composer.inputValue(), workDraft);
  assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
  await page.getByText('The second scoped check is complete. [Explicit synthetic fixture]', { exact: true }).scrollIntoViewIfNeeded();
  await shot('work-organization-restarted-work-mobile');
  await navigate('Home');
  await page.getByText(laterNote, { exact: false }).waitFor();
  assert.equal(await composer.inputValue(), homeDraft);
  assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
  await page.getByText(laterNote, { exact: false }).scrollIntoViewIfNeeded();
  await shot('work-organization-restarted-home-mobile');
  checks.push('The original Work, both results, native report sources and both drafts survive offline restart and phone navigation without another model call.');
  assert.deepEqual(errors, []);
  console.log(JSON.stringify({ checks, errors, viewports: ['1280x900', '390x844'], modelEvidence: 'explicit_synthetic_transport_only' }, null, 2));
} catch (error) {
  if (screenshots && page) await page.screenshot({ path: join(screenshots, 'work-organization-failure.png') }).catch(() => {});
  throw error;
} finally {
  await browser?.close();
  await stop(server);
  await rm(directory, { recursive: true, force: true });
}
