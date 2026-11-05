/** Real file -> clipped tool result -> scoped history recall -> durable source.
 * HTTP model choices are an explicit synthetic fixture. World, Kernel, tools,
 * persistence, file bytes and browser actions are real.
 * Copyright 2026 Lightmaze Contributors. Licensed under Apache-2.0.
 */
import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { mkdtemp, mkdir, readFile, rm, writeFile } from 'node:fs/promises';
import { createServer } from 'node:net';
import { tmpdir } from 'node:os';
import { delimiter, dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { createRequire } from 'node:module';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '../..');
const { chromium } = createRequire(import.meta.url)(process.env.PLAYWRIGHT_MODULE || 'playwright');
const directory = await mkdtemp(join(tmpdir(), 'per-pulse-tool-history-'));
const screenshots = process.env.PER_PULSE_TOOL_HISTORY_SCREENSHOTS;
if (screenshots) await mkdir(screenshots, { recursive: true });
const socket = createServer();
await new Promise(done => socket.listen(0, '127.0.0.1', done));
const port = socket.address().port;
await new Promise(done => socket.close(done));
const base = `http://127.0.0.1:${port}`;
const archived = 'ARCHIVED_TOOL_ONLY_C82', current = 'CURRENT_FILE_ONLY_D97';
let serverLog = '', browser, page, requests = 0, restarting = false;
const checks = [], httpChecks = [], errors = [], restartNetworkErrors = [];
const start = () => {
  const child = spawn(process.env.PER_PULSE_PYTHON || join(root, '.venv/bin/python'), [
    join(root, 'tests/browser/tool_history_server.py'), '--data-dir', directory, '--port', String(port),
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
  const result = await json('/__tool_history/evidence');
  assert.deepEqual(result.failures, [], 'all synthetic HTTP assertions passed');
  return result;
};
const checkpoint = async () => {
  const result = await evidence();
  requests += result.requests;
  httpChecks.push(...result.checks);
};
let server = start();
try {
  await wait(async () => (await fetch(base + '/api/health')).ok, 'World/Kernel ready');
  browser = await chromium.launch({ executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE || undefined,
    args: process.env.PLAYWRIGHT_CHROMIUM_ARGS ? JSON.parse(process.env.PLAYWRIGHT_CHROMIUM_ARGS) : undefined });
  page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
  page.on('pageerror', error => errors.push(error.message));
  page.on('console', message => {
    if (message.type() !== 'error') return;
    if (restarting && message.text() === 'Failed to load resource: net::ERR_CONNECTION_REFUSED' &&
        message.location().url.startsWith(base + '/')) restartNetworkErrors.push(message.location().url);
    else errors.push(message.text());
  });
  await page.goto(base);
  assert.equal(await page.title(), 'Per Pulse');
  assert.equal(new URL(page.url()).origin, base);
  await page.getByRole('textbox', { name: 'Message Home', exact: true }).waitFor();
  assert.equal(await page.locator('vite-error-overlay, nextjs-portal').count(), 0);
  checks.push('Per Pulse Home renders meaningful controls without a framework overlay');

  const view = id => json('/api/pages/' + encodeURIComponent(id));
  const composer = page.getByRole('textbox', { name: /^Message / });
  const send = async (id, content, answer) => {
    const before = (await view(id)).messages.filter(message => message.role === 'assistant').length;
    await composer.fill(content);
    await page.getByRole('button', { name: 'Send message', exact: true }).click();
    await wait(async () => {
      await evidence();
      const next = await view(id);
      return next.messages.filter(message => message.role === 'assistant').length === before + 1 &&
        next.messages.at(-1)?.content.includes(answer);
    }, 'saved assistant reply: ' + answer);
    await page.locator('.message-assistant').getByText(answer, { exact: false }).last().waitFor();
  };
  const create = async title => {
    await page.getByRole('button', { name: 'Create a page', exact: true }).click();
    await page.getByRole('textbox', { name: 'Page name', exact: true }).fill(title);
    await page.getByRole('textbox', { name: 'Where should we begin?', exact: true })
      .fill('Begin this independent discussion. [Authored browser fixture]');
    await page.getByRole('button', { name: 'Create page', exact: true }).click();
    await page.getByRole('heading', { name: title, exact: true }).waitFor();
    const id = new URL(page.url()).pathname.split('/').pop();
    await wait(async () => (await view(id)).messages.some(message => message.role === 'assistant'), 'opening Work reply');
    return id;
  };
  const fillWindow = async id => {
    for (let index = 0; index < 4; index++) {
      await send(id, `HISTORY_WINDOW_FILLER ${index}: ` + 'Continue the current field discussion. '.repeat(24), 'Continuation received.');
    }
  };

  const owner = await create('Tool history source Work');
  const instruction = 'CAPTURE_TOOL_HISTORY: Read observations.txt once and keep its original receipt.';
  await send(owner, instruction, 'Read the file and kept the original tool receipt.');
  const original = (await view(owner)).messages.find(message => message.content === instruction);
  const captured = await evidence();
  assert.ok(original?.id && captured.original_row?.message_id);
  assert.equal(captured.original_event_id, original.id, 'the saved tool turn belongs to this exact original user instruction');
  assert.equal(captured.original_unchanged, true);
  assert.equal(captured.read_file_actions, 1);
  assert.equal(captured.read_file_invocations, 1);
  assert.equal((await view(owner)).messages.some(message => message.content.includes(archived)), false);
  checks.push('ordinary chat runs read_file once; the private marker stays in the persisted tool receipt');

  await writeFile(join(directory, 'workspace/observations.txt'), current + '\nThe physical file has changed.\n');
  await fillWindow(owner);
  await send(owner, 'RECALL_TOOL_HISTORY: Find and read the original saved file result in this Work.',
    'Recovered archived file content: ' + archived);
  const recovered = await evidence();
  assert.match(recovered.recovered_content, new RegExp(archived));
  assert.ok(recovered.passage_count >= 3);
  assert.equal(recovered.last_source.message_id, captured.original_row.message_id);
  assert.equal(recovered.read_file_actions, 1);
  assert.equal(recovered.read_file_invocations, 1);
  assert.equal(recovered.original_unchanged, true);
  assert.match(await readFile(join(directory, 'workspace/observations.txt'), 'utf8'), new RegExp(current));
  assert.doesNotMatch(await readFile(join(directory, 'workspace/observations.txt'), 'utf8'), new RegExp(archived));
  checks.push('after real clipping and file replacement, role=tool history recovers the old bytes with Unicode pagination and no repeated read');
  await page.locator('.message-assistant').getByText('Recovered archived file content: ' + archived, { exact: false }).last().scrollIntoViewIfNeeded();
  if (screenshots) await page.screenshot({ path: join(screenshots, 'tool-history-recovered-desktop.png') });

  // Remove the first recalled summary from the request before the restart check.
  // The fixture rejects any pre-recall request that still contains its marker.
  await fillWindow(owner);
  const draft = 'Keep this unsent draft while reopening the original instruction.';
  await composer.fill(draft);
  await checkpoint();
  restarting = true;
  await stop(server); server = start();
  await wait(async () => (await fetch(base + '/api/health')).ok, 'restarted native World/Kernel');
  await page.reload();
  await composer.waitFor();
  restarting = false;
  assert.equal(await composer.inputValue(), draft);
  await send(owner, 'RECALL_TOOL_HISTORY: Read the original result again after this process restart.',
    'Recovered archived file content: ' + archived);
  assert.equal((await evidence()).original_unchanged, true);
  assert.equal((await evidence()).read_file_actions, 1);
  assert.equal((await evidence()).read_file_invocations, 1);
  checks.push('process restart preserves the original row, native history access and unsent browser draft');

  const sibling = await create('Independent tool history Work');
  await send(sibling, 'PROBE_OTHER_WORK: Try the saved original result from another Work.',
    'This independent Work cannot read the original tool result.');
  checks.push('another Work cannot search or directly read the private original tool message');

  await page.goto(base + '/page/' + encodeURIComponent(owner));
  await composer.waitFor();
  await composer.fill(draft);
  await page.setViewportSize({ width: 390, height: 844 });
  await page.locator('.message-assistant').getByText('Recovered archived file content: ' + archived, { exact: false }).last().scrollIntoViewIfNeeded();
  assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
  if (screenshots) await page.screenshot({ path: join(screenshots, 'tool-history-recovered-phone.png') });
  await page.getByRole('button', { name: 'Find a message', exact: true }).click();
  const finder = page.getByRole('dialog', { name: 'Find a message', exact: true });
  await finder.getByRole('searchbox', { name: 'Words in this conversation', exact: true }).fill('CAPTURE_TOOL_HISTORY');
  await finder.getByRole('button', { name: 'Find', exact: true }).click();
  const found = finder.locator(`[data-search-message-id="${original.id}"]`);
  await found.waitFor();
  assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
  assert.ok(await finder.evaluate(node => node.scrollWidth <= node.clientWidth));
  if (screenshots) await page.screenshot({ path: join(screenshots, 'tool-history-source-phone.png') });
  await found.getByRole('button', { name: 'View in conversation', exact: true }).click();
  await finder.waitFor({ state: 'hidden' });
  await page.locator('#message-' + original.id).waitFor();
  assert.equal(decodeURIComponent(new URL(page.url()).hash), '#message-' + original.id);
  assert.equal(await composer.inputValue(), draft);
  await page.reload();
  await page.locator('#message-' + original.id).waitFor();
  assert.equal(await composer.inputValue(), draft);
  assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
  checks.push('on a phone the ordinary finder opens the exact original instruction and keeps source position and draft after reload');

  const final = await evidence();
  assert.equal(final.read_file_actions, 1);
  assert.equal(final.read_file_invocations, 1);
  assert.equal(final.original_unchanged, true);
  assert.deepEqual(errors, [], 'no browser runtime or console errors');
  await checkpoint();
  console.log(JSON.stringify({ passed: checks.length, checks, httpModelRequests: requests, httpFixtureChecks: httpChecks,
    readFileActions: final.read_file_actions, readFileInvocations: final.read_file_invocations,
    expectedRestartNetworkErrors: restartNetworkErrors.length,
    provider: 'explicit synthetic HTTP fixture', viewports: ['1280x900', '390x844'] }));
} catch (error) {
  if (screenshots) {
    await writeFile(join(screenshots, 'tool-history-server.log'), serverLog);
    await writeFile(join(screenshots, 'tool-history-failure-evidence.json'), JSON.stringify(await json('/__tool_history/evidence').catch(() => ({})), null, 2));
    if (page) await page.screenshot({ path: join(screenshots, 'tool-history-failure.png') }).catch(() => {});
  }
  throw error;
} finally {
  await browser?.close();
  await stop(server);
  await rm(directory, { recursive: true, force: true });
}
