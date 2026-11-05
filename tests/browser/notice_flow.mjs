/** Chat -> native Notice -> large reading -> source -> close -> durable history.
 * World/Kernel/SQLite and browser actions are real. HTTP model choices are an
 * explicitly synthetic fixture; no external provider is contacted.
 * Copyright 2026 Lightmaze Contributors. Licensed under Apache-2.0.
 */
import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { mkdtemp, mkdir, rm, writeFile } from 'node:fs/promises';
import { createServer } from 'node:net';
import { tmpdir } from 'node:os';
import { delimiter, dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { createRequire } from 'node:module';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '../..');
const { chromium } = createRequire(import.meta.url)(process.env.PLAYWRIGHT_MODULE || 'playwright');
const directory = await mkdtemp(join(tmpdir(), 'per-pulse-notice-browser-'));
const screenshots = process.env.PER_PULSE_NOTICE_SCREENSHOTS;
if (screenshots) await mkdir(screenshots, { recursive: true });
const socket = createServer();
await new Promise(done => socket.listen(0, '127.0.0.1', done));
const port = socket.address().port;
await new Promise(done => socket.close(done));
const base = `http://127.0.0.1:${port}`;
let serverLog = '', browser, page, requestTotal = 0;
const errors = [], checks = [], fixtureChecks = [];
const start = () => {
  const child = spawn(process.env.PER_PULSE_PYTHON || join(root, '.venv/bin/python'), [
    join(root, 'tests/browser/notice_server.py'), '--data-dir', directory, '--port', String(port),
  ], { cwd: root, env: { ...process.env, PYTHONPATH: [join(root, 'src'), process.env.PYTHONPATH].filter(Boolean).join(delimiter) },
    stdio: ['ignore', 'pipe', 'pipe'] });
  child.stdout.on('data', data => { serverLog += data; });
  child.stderr.on('data', data => { serverLog += data; });
  return child;
};
const stop = async child => {
  if (child.exitCode !== null || child.signalCode !== null) return;
  child.kill('SIGTERM');
  const force = setTimeout(() => child.kill('SIGKILL'), 10000);
  await new Promise(done => child.once('exit', done));
  clearTimeout(force);
};
const wait = async (fn, label, timeout = 20000) => {
  const deadline = Date.now() + timeout;
  while (Date.now() < deadline) {
    try { if (await fn()) return; } catch { /* service starting or view changing */ }
    await new Promise(done => setTimeout(done, 75));
  }
  throw new Error(`Timed out: ${label}\n${serverLog}`);
};
const json = async path => {
  const response = await fetch(base + path);
  assert.ok(response.ok, path + ': ' + response.status);
  return response.json();
};
const evidence = async () => {
  const current = await json('/__notice/evidence');
  assert.deepEqual(current.failures, [], 'synthetic HTTP assertions all passed');
  return current;
};
const checkpoint = async () => {
  const current = await evidence();
  fixtureChecks.push(...current.checks);
  requestTotal += current.requests;
};
let server = start();
try {
  await wait(async () => (await fetch(base + '/api/health')).ok, 'World/Kernel ready');
  browser = await chromium.launch({
    executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE || undefined,
    args: process.env.PLAYWRIGHT_CHROMIUM_ARGS ? JSON.parse(process.env.PLAYWRIGHT_CHROMIUM_ARGS) : undefined,
  });
  page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
  page.on('pageerror', error => errors.push(error.message));
  page.on('console', message => { if (message.type() === 'error') errors.push(message.text()); });
  await page.goto(base);
  assert.equal(await page.title(), 'Per Pulse');
  await page.getByRole('textbox', { name: 'Message Home', exact: true }).waitFor();
  assert.equal(await page.locator('vite-error-overlay, nextjs-portal').count(), 0);
  checks.push('Per Pulse loads with meaningful Home controls and no framework overlay');

  const view = id => json('/api/pages/' + encodeURIComponent(id));
  const composer = page.getByRole('textbox', { name: /^Message / });
  const send = async (id, content, responseText) => {
    const previous = (await view(id)).messages.filter(message => message.role === 'assistant').length;
    await composer.fill(content);
    await page.getByRole('button', { name: 'Send message', exact: true }).click();
    await wait(async () => {
      await evidence();
      const current = await view(id);
      return current.messages.filter(message => message.role === 'assistant').length === previous + 1 &&
        current.messages.at(-1)?.content.includes(responseText);
    }, 'saved assistant reply: ' + responseText);
    await page.locator('.message-assistant').getByText(responseText, { exact: false }).last().waitFor();
  };
  const create = async title => {
    await page.getByRole('button', { name: 'Create a page', exact: true }).click();
    await page.getByRole('textbox', { name: 'Page name', exact: true }).fill(title);
    await page.getByRole('textbox', { name: 'Where should we begin?', exact: true })
      .fill('Start this independent Work. [Authored browser fixture]');
    await page.getByRole('button', { name: 'Create page', exact: true }).click();
    await page.getByRole('heading', { name: title, exact: true }).waitFor();
    const id = new URL(page.url()).pathname.split('/').pop();
    await wait(async () => (await view(id)).messages.some(message => message.role === 'assistant'), 'initial Work reply');
    return id;
  };

  const owner = await create('Notice source Work');
  const registration = 'REGISTER_LOCAL_NOTICE: Keep this observation while reading the large source. [Authored browser fixture]';
  await send(owner, registration, 'Registered the local notice and read the large source.');
  const original = (await view(owner)).messages.find(message => message.content === registration);
  assert.ok(original?.id, 'the actual user instruction is saved in the owning Work');
  const registered = await evidence();
  assert.ok(registered.source.registration_message_id && registered.source.action_id && registered.source.turn_id);
  assert.equal(registered.source.event_id, original.id, 'Notice source is the actual saved triggering instruction');
  checks.push('chat records a native Notice and reads more than 24,000 source characters across actual tool continuations');

  await send(owner, 'CHECK_AFTER_WINDOW: Inspect the retained notice and its original registration.',
    'The notice survived context clipping; its original registration is readable.');
  const read = (await evidence()).last_read;
  assert.equal(read.notice.source.registration_message_id, read.source_passage.source.message_id);
  assert.ok(read.notice.created_at && read.notice.updated_at);
  checks.push('a later model turn gets the bounded Notice after original tool results leave the context window, then reads native source history');
  const pendingIdle = (await evidence()).requests;
  await new Promise(done => setTimeout(done, 500));
  assert.equal((await evidence()).requests, pendingIdle, 'a pending Notice schedules no model call with the default disabled Pulse policy');

  const draft = 'Keep this unsent Work draft while I inspect the notice source.';
  await composer.fill(draft);
  await page.getByRole('button', { name: 'Find a message', exact: true }).click();
  const finder = page.getByRole('dialog', { name: 'Find a message', exact: true });
  await finder.getByRole('searchbox', { name: 'Words in this conversation', exact: true }).fill('REGISTER_LOCAL_NOTICE');
  await finder.getByRole('button', { name: 'Find', exact: true }).click();
  const found = finder.locator('[data-search-message-id="' + original.id + '"]');
  await found.waitFor();
  await found.getByRole('button', { name: 'View in conversation', exact: true }).click();
  await finder.waitFor({ state: 'hidden' });
  await page.locator('#message-' + original.id).waitFor();
  assert.equal(decodeURIComponent(new URL(page.url()).hash), '#message-' + original.id);
  assert.equal(await composer.inputValue(), draft);
  if (screenshots) await page.screenshot({ path: join(screenshots, 'notice-original-source-desktop.png') });
  checks.push('the existing message finder opens the exact original instruction without losing the Work draft');

  await checkpoint();
  await stop(server); server = start();
  await wait(async () => (await fetch(base + '/api/health')).ok, 'restarted World/Kernel with active Notice');
  await page.reload();
  await page.locator('#message-' + original.id).waitFor();
  assert.equal(await composer.inputValue(), draft);
  await send(owner, 'CHECK_ACTIVE_RESTART: Inspect the pending notice again.', 'The active notice survived restart.');
  assert.equal((await evidence()).notice_id, registered.notice_id);
  checks.push('process restart preserves the active Notice, original source location and browser draft');

  const sibling = await create('Independent sibling Work');
  await send(sibling, 'PROBE_SIBLING_NOTICE: Check this Work using the native notice operations.',
    'Sibling Work cannot list, read, or close the private notice.');
  checks.push('a sibling Work cannot list, read, close or receive the owning Work notice');
  await page.goto(base + '/page/' + encodeURIComponent(owner));
  await composer.waitFor();
  assert.equal(await composer.inputValue(), '', 'the last explicit send consumed only the owning draft');

  await send(owner, 'CLOSE_LOCAL_NOTICE: Finish that observation.', 'Closed the local notice; its history is retained.');
  await send(owner, 'CHECK_CLOSED_NOTICE: Inspect the closed history.', 'Closed history and source remain readable after restart.');
  checks.push('stale revision cannot close the Notice; a confirmed close stops the immediate and later reminders while retaining its record');

  const closedDraft = 'The closed notice still has a readable source.';
  await composer.fill(closedDraft);
  await checkpoint();
  await stop(server); server = start();
  await wait(async () => (await fetch(base + '/api/health')).ok, 'restarted World/Kernel with closed Notice');
  await page.reload();
  await composer.waitFor();
  assert.equal(await composer.inputValue(), closedDraft);
  await send(owner, 'CHECK_CLOSED_NOTICE: Inspect after the second process restart.',
    'Closed history and source remain readable after restart.');
  checks.push('second restart retains closed history and its source, with no active reminder');

  await page.setViewportSize({ width: 390, height: 844 });
  await page.getByRole('button', { name: 'Find a message', exact: true }).click();
  await finder.getByRole('searchbox', { name: 'Words in this conversation', exact: true }).fill('REGISTER_LOCAL_NOTICE');
  await finder.getByRole('button', { name: 'Find', exact: true }).click();
  await found.waitFor();
  assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
  assert.ok(await finder.evaluate(node => node.scrollWidth <= node.clientWidth));
  if (screenshots) await page.screenshot({ path: join(screenshots, 'notice-original-source-phone.png') });
  await page.keyboard.press('Escape');
  await finder.waitFor({ state: 'hidden' });
  checks.push('the original registration remains findable on a phone after closure and restart');

  const beforeIdle = (await evidence()).requests;
  await new Promise(done => setTimeout(done, 500));
  assert.equal((await evidence()).requests, beforeIdle, 'recording or retaining a Notice schedules no model call');
  checks.push('idle Notice state schedules no provider request');
  assert.deepEqual(errors, [], 'no browser runtime or console errors');
  await checkpoint();
  console.log(JSON.stringify({ passed: checks.length, checks, httpModelRequests: requestTotal,
    httpFixtureChecks: fixtureChecks, provider: 'explicit synthetic HTTP fixture', viewports: ['1280x900', '390x844'] }));
} catch (error) {
  if (screenshots) {
    await writeFile(join(screenshots, 'notice-server.log'), serverLog);
    await writeFile(join(screenshots, 'notice-failure-evidence.json'), JSON.stringify(await json('/__notice/evidence').catch(() => ({})), null, 2));
    if (page) await page.screenshot({ path: join(screenshots, 'notice-failure.png') }).catch(() => {});
  }
  throw error;
} finally {
  await browser?.close();
  await stop(server);
  await rm(directory, { recursive: true, force: true });
}
