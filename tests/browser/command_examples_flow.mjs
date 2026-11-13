/** Delivered command passage -> native example -> sourced return -> offline restart.
 * Model choices and Docker control are explicit fixtures. OS pipes, World,
 * Kernel, approvals, SQLite and Chromium are real.
 * Copyright 2026 Lightmaze Contributors. Licensed under Apache-2.0.
 */
import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { mkdtemp, mkdir, rm } from 'node:fs/promises';
import { createServer } from 'node:net';
import { tmpdir } from 'node:os';
import { delimiter, dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { createRequire } from 'node:module';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '../..');
const { chromium } = createRequire(import.meta.url)(process.env.PLAYWRIGHT_MODULE || 'playwright');
const directory = await mkdtemp(join(tmpdir(), 'per-pulse-command-examples-'));
const screenshots = process.env.PER_PULSE_COMMAND_EXAMPLE_SCREENSHOTS;
if (screenshots) await mkdir(screenshots, { recursive: true });
const socket = createServer();
await new Promise(done => socket.listen(0, '127.0.0.1', done));
const port = socket.address().port;
await new Promise(done => socket.close(done));
const base = `http://127.0.0.1:${port}`;
let server, browser, serverLog = '';
const checks = [], errors = [];
const check = text => { checks.push(text); console.log(`PASS: ${text}`); };
const start = (offline = false) => {
  const child = spawn(process.env.PER_PULSE_PYTHON || join(root, '.venv/bin/python'), [
    join(root, 'tests/browser/command_example_server.py'), '--data-dir', directory, '--port', String(port),
    ...(offline ? ['--offline'] : []),
  ], { cwd: root, env: { ...process.env, PYTHONPATH: [join(root, 'src'), process.env.PYTHONPATH].filter(Boolean).join(delimiter) }, stdio: ['ignore', 'pipe', 'pipe'] });
  child.stdout.on('data', data => { serverLog += data; });
  child.stderr.on('data', data => { serverLog += data; });
  return child;
};
const stop = async child => {
  if (!child || child.exitCode !== null || child.signalCode !== null) return;
  child.kill('SIGTERM');
  const force = setTimeout(() => child.kill('SIGKILL'), 10000);
  await new Promise(done => child.once('exit', done)); clearTimeout(force);
};
const wait = async (condition, label) => {
  const deadline = Date.now() + 30000;
  while (Date.now() < deadline) {
    try { if (await condition()) return; } catch {}
    await new Promise(done => setTimeout(done, 80));
  }
  throw new Error(`Timed out: ${label}\n${serverLog}`);
};
const json = async path => {
  const response = await fetch(base + path); assert.equal(response.status, 200); return response.json();
};
try {
  server = start();
  await wait(async () => (await fetch(base + '/api/health')).ok, 'native service');
  browser = await chromium.launch({ executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE || undefined,
    args: process.env.PLAYWRIGHT_CHROMIUM_ARGS ? JSON.parse(process.env.PLAYWRIGHT_CHROMIUM_ARGS) : undefined });
  const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
  page.on('pageerror', error => errors.push(error.message));
  await page.goto(base);
  const create = async (title, instruction) => {
    if (!await page.getByRole('button', { name: 'Create a page', exact: true }).isVisible()) {
      await page.getByRole('button', { name: 'Toggle navigation', exact: true }).click();
    }
    await page.getByRole('button', { name: 'Create a page', exact: true }).click();
    await page.getByRole('textbox', { name: 'Page name', exact: true }).fill(title);
    await page.getByRole('textbox', { name: 'Where should we begin?', exact: true }).fill(instruction);
    await page.getByRole('button', { name: 'Create page', exact: true }).click();
    await page.getByRole('heading', { name: title, exact: true }).waitFor();
    return new URL(page.url()).pathname.split('/').pop();
  };
  const openExperience = async () => {
    if (!await page.getByRole('button', { name: 'Settings', exact: true }).isVisible()) {
      await page.getByRole('button', { name: 'Toggle navigation', exact: true }).click();
    }
    await page.getByRole('button', { name: 'Settings', exact: true }).click();
    await page.getByRole('button', { name: 'View this page’s experience', exact: true }).click();
    await page.getByRole('dialog', { name: 'Lessons and examples', exact: true }).waitFor();
  };
  const source = await create('Command practice', 'COMMAND_EXAMPLE_FIXTURE:{"op":"run"}');
  await page.getByRole('button', { name: 'Allow once', exact: true }).click();
  await wait(async () => (await json(`/api/pages/${source}`)).execution.commandOutputs.items.length === 1
    && !(await json(`/api/pages/${source}`)).execution.canCancel, 'approved command exit');
  const command = (await json(`/api/pages/${source}`)).execution.commandOutputs.items[0];
  assert.equal(command.exitCode, 7);
  const composer = page.getByRole('textbox', { name: 'Message Command practice', exact: true });
  await composer.fill('COMMAND_EXAMPLE_FIXTURE:' + JSON.stringify({ op: 'read', action_id: command.actionId,
    stream: 'stderr', offset: 4000, limit: 8000, retain: { title: 'A retained command diagnosis', excerpt: 'STDERR_FINAL_DIAGNOSIS', note: 'Keep the observed failure available.' } }));
  await page.getByRole('button', { name: 'Send message', exact: true }).click();
  await wait(async () => (await json(`/api/pages/${source}/posture`)).experiences.length === 1, 'settled command example');
  const saved = (await json(`/api/pages/${source}/posture`)).experiences[0];
  assert.equal(saved.sourceKind, 'command'); assert.equal(saved.commandSource.exitCode, 7);
  assert.equal(saved.source.command_receipt_id.length > 0, true);
  assert.notEqual(saved.source.command_receipt_id, command.actionId);
  assert.equal(saved.feedback.length, 0); assert.equal(saved.encounter.outcome, undefined);
  assert.equal((await json('/__command_examples/evidence')).commands, 1);
  check('one approved command exits with 7; the model reads its tail and retains the delivered passage with distinct receipt identity');

  const sourceDraft = 'Unsent source draft: retain the actual command reading.';
  await composer.fill(sourceDraft);
  await openExperience();
  const dialog = page.getByRole('dialog', { name: 'Lessons and examples', exact: true });
  const example = dialog.getByRole('article').filter({ has: page.getByRole('heading', { name: saved.title, exact: true }) });
  await example.getByText('Saved from command output. Command exit code: 7.', { exact: true }).waitFor();
  assert.equal(await example.getByLabel('Original passage', { exact: true }).textContent(), saved.excerpt);
  await example.getByText('Source experience', { exact: true }).click();
  await example.getByRole('button', { name: 'Open output reading conversation', exact: true }).waitFor();
  if (screenshots) await page.screenshot({ path: join(screenshots, 'command-example-source.png') });
  await example.getByRole('button', { name: 'Open output reading conversation', exact: true }).click();
  await page.locator(`#message-${saved.sourceAnchor.resultMessageId}`).waitFor();
  await page.waitForFunction(id => document.activeElement?.id === id, `message-${saved.sourceAnchor.resultMessageId}`);
  assert.equal(await page.evaluate(() => document.activeElement?.id), `message-${saved.sourceAnchor.resultMessageId}`);
  check('experience labels the passage as command output and opens the actual reading conversation');

  const receiver = await create('Compare command experience', 'Continue with an existing observation. [Explicit fixture]');
  await wait(async () => (await json(`/api/pages/${receiver}`)).messages.some(item => item.role === 'assistant'), 'receiver ready');
  const receiverDraft = 'Unsent receiver draft: compare this command experience.';
  const receiverComposer = page.getByRole('textbox', { name: 'Message Compare command experience', exact: true });
  await receiverComposer.fill(receiverDraft);
  await openExperience();
  await example.getByText('Saved from command output. Command exit code: 7.', { exact: true }).waitFor();
  assert.equal(await example.getByText('diagnostic progress', { exact: false }).count(), 0);
  const shared = (await json(`/api/pages/${receiver}/posture`)).experiences[0];
  assert.equal(shared.source, undefined); assert.equal(shared.encounter, undefined);
  await example.getByText('Source experience', { exact: true }).click();
  // A source message can already be visible in the cached page while its
  // fresh view is loading. Delay that read so visibility cannot stand in for
  // completed source navigation and focus restoration.
  let releaseSourceRead;
  const sourceReadGate = new Promise(done => { releaseSourceRead = done; });
  let sourceReadHeld = false;
  const sourceReadPattern = new RegExp(`/api/pages/${source}(?:[?]|$)`);
  await page.route(sourceReadPattern, async route => {
    const response = await route.fetch();
    sourceReadHeld = true;
    await sourceReadGate;
    await route.fulfill({ response });
  });
  await example.getByRole('button', { name: 'Open output reading conversation', exact: true }).click();
  await wait(async () => sourceReadHeld, 'fresh source view held after navigation');
  await page.locator(`#message-${saved.sourceAnchor.resultMessageId}`).waitFor();
  assert.equal(await composer.inputValue(), sourceDraft);
  releaseSourceRead();
  await page.waitForFunction(id => document.activeElement?.id === id, `message-${saved.sourceAnchor.resultMessageId}`);
  assert.equal(await page.evaluate(() => document.activeElement?.id), `message-${saved.sourceAnchor.resultMessageId}`);
  assert.equal(new URL(page.url()).pathname.split('/').pop(), source);
  assert.equal(new URL(page.url()).hash, `#message-${saved.sourceAnchor.resultMessageId}`);
  assert.equal(await composer.inputValue(), sourceDraft);
  await page.unroute(sourceReadPattern);
  await page.getByRole('button', { name: 'Back to conversation', exact: true }).click();
  await page.getByRole('heading', { name: 'Compare command experience', exact: true }).waitFor();
  await page.waitForFunction(() => document.activeElement?.tagName === 'H1'
    && document.activeElement.textContent === 'Compare command experience');
  assert.equal(new URL(page.url()).pathname.split('/').pop(), receiver);
  assert.equal(await receiverComposer.inputValue(), receiverDraft);
  check('another page sees the selected passage and exit fact, follows its source, and returns without receiving surrounding output');

  await stop(server); server = start(true);
  await wait(async () => (await fetch(base + '/api/health')).ok, 'offline restart');
  await page.reload();
  await openExperience();
  await example.getByText('Saved from command output. Command exit code: 7.', { exact: true }).waitFor();
  assert.deepEqual((await json(`/api/pages/${source}/posture`)).experiences[0], saved);
  await page.setViewportSize({ width: 390, height: 844 });
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
  if (screenshots) await page.screenshot({ path: join(screenshots, 'command-example-mobile-offline.png') });
  assert.equal((await json('/__command_examples/evidence')).commands, 1);
  check('offline restart and phone layout retain exact experience and provenance without running the command again');

  // Reuse the exact retained command observation in a genuinely new Work after
  // restart. No model, shell command or previous Work transcript is replayed.
  await example.getByText('Source experience', { exact: true }).click();
  await example.getByRole('button', { name: 'Open output reading conversation', exact: true }).click();
  await page.getByRole('heading', { name: 'Command practice', exact: true }).waitFor();
  await openExperience();
  const sourceExample = page.getByRole('dialog', { name: 'Lessons and examples', exact: true })
    .getByRole('article').filter({ has: page.getByRole('heading', { name: saved.title, exact: true }) });
  await sourceExample.getByRole('button', { name: 'Start another Work from this passage', exact: true }).click();
  await page.getByRole('textbox', { name: 'Page name', exact: true }).fill('Command passage follow-up');
  await page.getByRole('textbox', { name: 'Where should we begin?', exact: true })
    .fill('Compare the saved command observation with the next task.');
  await page.getByRole('button', { name: 'Create page', exact: true }).click();
  await page.getByRole('heading', { name: 'Command passage follow-up', exact: true }).waitFor();
  const followup = new URL(page.url()).pathname.split('/').pop();
  const followupView = await json('/api/pages/' + followup);
  const opening = followupView.messages.find(message => message.role === 'user' && message.content.includes('COMMAND_EXPERIENCE_JSON'));
  assert.ok(opening, 'native follow-up contains a saved command observation');
  assert.ok(opening.content.includes(saved.id) && opening.content.includes(saved.excerpt));
  assert.ok(opening.content.includes('past_command_observation_not_reexecuted'));
  assert.ok(!opening.content.includes(sourceDraft), 'original private draft stays in the source Work');
  assert.equal((await json('/__command_examples/evidence')).commands, 1);
  const crossSource = await fetch(base + '/api/pages', { method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ title: 'Wrong source', instruction: 'Should be refused.',
      sourcePageId: followup, sourceExperienceId: saved.id }) });
  assert.equal(crossSource.status, 404, 'receiving Work cannot impersonate source Work');
  const paused = await fetch(base + '/api/pages/' + source + '/posture/experiences/' + saved.id, {
    method: 'PATCH', headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ expectedRevision: saved.revision, changes: { active: false } }),
  });
  assert.equal(paused.status, 200);
  const unavailable = await fetch(base + '/api/pages', { method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ title: 'Paused source', instruction: 'Should be refused.',
      sourcePageId: source, sourceExperienceId: saved.id }) });
  assert.equal(unavailable.status, 422, 'paused observation cannot seed a later Work');
  assert.deepEqual(errors, []);
  check('saved command passage starts a separate offline Work; foreign or paused reuse is denied');
  console.log(JSON.stringify({ passed: checks.length, checks, provider: 'explicit synthetic HTTP fixture' }));
} catch (error) {
  console.error(serverLog); throw error;
} finally {
  await browser?.close(); await stop(server); await rm(directory, { recursive: true, force: true });
}
