/** One native World region through two Works, user approval and restart.
 * Only model decisions are synthetic. Browser, API, Kernel, journal, file,
 * source attribution, original approval and SQLite restart are real.
 * Copyright 2026 Lightmaze Contributors. Licensed under Apache-2.0.
 */
import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { mkdtemp, rm } from 'node:fs/promises';
import { createServer } from 'node:net';
import { tmpdir } from 'node:os';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { createRequire } from 'node:module';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '../..');
const { chromium } = createRequire(import.meta.url)(process.env.PLAYWRIGHT_MODULE || 'playwright');
const directory = await mkdtemp(join(tmpdir(), 'per-pulse-habitat-region-'));
const socket = createServer();
await new Promise(done => socket.listen(0, '127.0.0.1', done));
const port = socket.address().port;
await new Promise(done => socket.close(done));
const base = 'http://127.0.0.1:' + port;
const first = 'The first visit left a real note.\n';
const second = 'The second Work added a distinct note.\n';
let server, browser, page, serverLog = '';
const errors = [], checks = [];
const start = () => {
  const child = spawn(process.env.PER_PULSE_PYTHON || join(root, '.venv/bin/python'), [
    join(root, 'tests/browser/habitat_region_server.py'),
    '--data-dir', directory, '--port', String(port),
  ], { cwd: root, stdio: ['ignore', 'pipe', 'pipe'] });
  child.stdout.on('data', d => { serverLog += d; });
  child.stderr.on('data', d => { serverLog += d; });
  return child;
};
const stop = async child => {
  if (!child || child.exitCode !== null || child.signalCode !== null) return;
  const exited = new Promise(done => child.once('exit', done));
  child.kill('SIGTERM');
  const force = setTimeout(() => child.kill('SIGKILL'), 10000);
  await exited;
  clearTimeout(force);
};
const wait = async (fn, label) => {
  const until = Date.now() + 25000;
  while (Date.now() < until) {
    try { if (await fn()) return; } catch {}
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
const evidence = () => get('/__habitat/status');
const latestHomeRead = async marker => {
  const rows = (await evidence()).modelObservations
    .filter(x => x.prompt.includes(marker) && x.results.length);
  assert.ok(rows.length, 'Home must have read a native tool result');
  return rows.at(-1).results.at(-1).output;
};
const pendingAction = async id => (await view(id)).execution.pendingActions?.[0];
const navigate = async title => {
  const navigation = page.getByLabel('Workspace navigation');
  if (!await navigation.isVisible()) {
    await page.getByRole('button', { name: 'Toggle navigation', exact: true }).click();
  }
  await navigation.getByRole('link', { name: title, exact: true }).click();
};
const makeWork = async (title, instruction) => {
  await page.getByRole('button', { name: 'Create a page', exact: true }).click();
  await page.getByRole('textbox', { name: 'Page name', exact: true }).fill(title);
  await page.getByRole('textbox', { name: 'Where should we begin?', exact: true }).fill(instruction);
  await page.getByRole('button', { name: 'Create page', exact: true }).click();
  await wait(() => new URL(page.url()).pathname.includes('/page/'), 'new Work route');
  const id = new URL(page.url()).pathname.split('/').pop();
  assert.ok(id && id !== 'home');
  return id;
};
const approve = async id => {
  const allow = page.getByRole('button', { name: 'Allow once', exact: true });
  await allow.waitFor();
  await wait(async () => allow.isEnabled() && Boolean((await pendingAction(id))?.canResolve),
             'original pending action is user-resolvable');
  const action = await pendingAction(id);
  assert.equal(action.toolName, 'habitat_write');
  await allow.click();
  await wait(async () => (await view(id)).execution.status === 'idle', 'original Work completes');
  return action.actionId;
};
const send = async text => {
  await page.locator('.composer textarea').fill(text);
  await page.getByRole('button', { name: 'Send message', exact: true }).click();
};
try {
  server = start();
  await wait(async () => (await fetch(base + '/api/health')).ok, 'native World starts');
  browser = await chromium.launch({ executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE || undefined,
    args: process.env.PLAYWRIGHT_CHROMIUM_ARGS ? JSON.parse(process.env.PLAYWRIGHT_CHROMIUM_ARGS) : undefined });
  page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
  page.on('pageerror', e => errors.push(e.message));
  await page.goto(base);
  await page.getByRole('textbox', { name: 'Message Home', exact: true }).waitFor();
  await send('HABITAT_HOME_BEGIN: I want to revisit the same garden from separate Works.');
  await wait(async () => (await view('home')).messages.some(x => x.role === 'assistant' && x.status === 'settled'),
             'original Home conversation');

  const workA = await makeWork('Garden first visit', 'HABITAT_WORK_A_WRITE: Record the first observation.');
  await wait(async () => Boolean((await pendingAction(workA))?.canResolve), 'Work A approval');
  assert.equal((await evidence()).exists, false, 'pending Habitat write has zero physical effects');
  const actionA = await approve(workA);
  let current = await evidence();
  assert.equal(current.content, first);
  assert.equal(current.writerIndex.source.kernelActionId, actionA);
  assert.ok(current.writerIndex.source.contextId.startsWith('work:'));
  const firstContext = current.writerIndex.source.contextId;
  const workAHistory = (await view(workA)).messages;
  checks.push('Work A writes only after the user grants the original native Kernel action.');

  await navigate('Home');
  await send('HABITAT_HOME_VERIFY_A: Observe the same region and original author.');
  await wait(async () => (await evidence()).modelObservations.some(x =>
    x.prompt.includes('HABITAT_HOME_VERIFY_A') && x.results.length), 'Home observes Work A');
  let homeRead = await latestHomeRead('HABITAT_HOME_VERIFY_A');
  assert.equal(homeRead.current, first);
  assert.equal(homeRead.lastVerifiedWrite.source.contextId, firstContext);
  checks.push('Home reads the real file and matches the previous Work writer by digest and source.');

  const workB = await makeWork('Garden return visit', 'HABITAT_WORK_B_CONTINUE: Read, then append one observation.');
  await wait(async () => Boolean((await pendingAction(workB))?.canResolve), 'Work B after read');
  assert.equal((await evidence()).content, first, 'Work B must not append before user approval');
  const actionB = await approve(workB);
  current = await evidence();
  assert.equal(current.content, first + second);
  assert.equal(current.writerIndex.source.kernelActionId, actionB);
  assert.ok(current.writerIndex.source.contextId.startsWith('work:'));
  assert.notEqual(current.writerIndex.source.contextId, firstContext);
  const workBHistory = (await view(workB)).messages;
  const originalWorld = current.worldId;
  checks.push('Work B reads the same World region then appends exactly once after a second approval.');

  await stop(server); server = start();
  await wait(async () => (await fetch(base + '/api/health')).ok, 'same World reopens after restart');
  await page.reload();
  await page.locator('.composer textarea').waitFor();
  current = await evidence();
  assert.equal(current.worldId, originalWorld);
  assert.equal(current.content, first + second, 'restarting must not replay the append');
  assert.deepEqual((await view(workA)).messages, workAHistory);
  assert.deepEqual((await view(workB)).messages, workBHistory);
  await navigate('Home');
  await send('HABITAT_HOME_VERIFY_RESTART: Read the garden and return to its source.');
  await wait(async () => (await evidence()).modelObservations.some(x =>
    x.prompt.includes('HABITAT_HOME_VERIFY_RESTART') && x.results.length), 'Home reads after restart');
  homeRead = await latestHomeRead('HABITAT_HOME_VERIFY_RESTART');
  assert.equal(homeRead.current, first + second);
  assert.equal(homeRead.lastVerifiedWrite.source.kernelActionId, actionB);
  assert.equal(homeRead.lastVerifiedWrite.effectReceipt.correlation_id, actionB);
  assert.equal(homeRead.source.contextId, '');
  assert.equal((await evidence()).content, first + second);
  checks.push('SQLite restart retains the exact file, the original Work histories and Home-to-writer provenance.');
  assert.deepEqual(errors, []);
  console.log(JSON.stringify({ checks, errors, modelEvidence: 'explicit synthetic HTTP choices only' }, null, 2));
} finally {
  await browser?.close();
  await stop(server);
  await rm(directory, { recursive: true, force: true });
}
