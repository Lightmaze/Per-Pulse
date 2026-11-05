/** Quiet native interpretation revision, exact sources and restart in the UI.
 * Synthetic HTTP choices; actual Kernel, experience journal and browser.
 * Copyright 2026 Lightmaze Contributors. Licensed under Apache-2.0.
 */
import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { mkdtemp, mkdir, writeFile, rm } from 'node:fs/promises';
import { createServer } from 'node:net';
import { tmpdir } from 'node:os';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { createRequire } from 'node:module';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '../..');
const { chromium } = createRequire(import.meta.url)(process.env.PLAYWRIGHT_MODULE || 'playwright');
const directory = await mkdtemp(join(tmpdir(), 'per-pulse-example-revision-'));
const screenshots = process.env.PER_PULSE_REVISION_SCREENSHOTS;
if (screenshots) await mkdir(screenshots, { recursive: true });
const socket = createServer();
await new Promise(done => socket.listen(0, '127.0.0.1', done));
const port = socket.address().port;
await new Promise(done => socket.close(done));
const base = `http://127.0.0.1:${port}`;
let browser, page, logs = '';
const errors = [], checks = [];
const start = (offline = false) => {
  const child = spawn(process.env.PER_PULSE_PYTHON || join(root, '.venv/bin/python'), [
    join(root, 'tests/browser/example_revision_server.py'), '--data-dir', directory, '--port', String(port),
    ...(offline ? ['--offline'] : []),
  ], { cwd: root, env: { ...process.env, PYTHONPATH: join(root, 'src') }, stdio: ['ignore', 'pipe', 'pipe'] });
  child.stdout.on('data', data => { logs += data; });
  child.stderr.on('data', data => { logs += data; });
  return child;
};
const stop = async child => {
  if (child.exitCode !== null || child.signalCode !== null) return;
  const exited = new Promise(done => child.once('exit', done));
  const force = setTimeout(() => child.kill('SIGKILL'), 10000);
  child.kill('SIGTERM'); await exited; clearTimeout(force);
};
const wait = async (fn, label) => {
  const deadline = Date.now() + 25000;
  while (Date.now() < deadline) {
    if (await fn().catch(() => false)) return;
    await new Promise(done => setTimeout(done, 80));
  }
  throw new Error(`Timed out: ${label}\n${logs}`);
};
const api = async path => {
  const result = await fetch(base + path);
  assert.ok(result.ok, path); return result.json();
};
const view = () => api('/api/pages/home');
const experience = async () => (await api('/api/pages/home/posture')).experiences[0];
let server = start();
try {
  await wait(async () => (await fetch(base + '/api/health')).ok, 'service');
  const passage = 'Map complete source characters, then compare the selected span.';
  await writeFile(join(directory, 'workspace', 'spans.txt'), passage);
  browser = await chromium.launch({ executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE || undefined,
    args: process.env.PLAYWRIGHT_CHROMIUM_ARGS ? JSON.parse(process.env.PLAYWRIGHT_CHROMIUM_ARGS) : undefined });
  page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
  page.on('pageerror', error => errors.push(error.message));
  await page.goto(base);
  const composer = () => page.getByRole('textbox', { name: /^Message / });
  const send = async text => {
    await composer().fill(text);
    await page.getByRole('button', { name: 'Send message', exact: true }).click();
  };
  const open = async () => {
    if (!(await page.getByRole('button', { name: 'Settings', exact: true }).isVisible())) {
      await page.getByRole('button', { name: 'Toggle navigation', exact: true }).click();
    }
    await page.getByRole('button', { name: 'Settings', exact: true }).click();
    await page.getByRole('button', { name: 'View this page’s experience', exact: true }).click();
  };
  const close = () => page.getByRole('button', { name: 'Close dialog', exact: true }).click();
  const dialog = () => page.getByRole('dialog', { name: 'Lessons and examples', exact: true });
  await send('READING_EXAMPLE_FIXTURE:' + JSON.stringify({ op: 'open', path: 'spans.txt',
    retain: { title: 'Character spans', excerpt: 'complete source characters', note: 'Count folded character positions.' } }));
  await wait(async () => Boolean(await experience()), 'observed example saved');
  const before = await experience();
  const originalUserIds = (await view()).messages.filter(m => m.role === 'user').map(m => m.id);
  await open();
  await dialog().getByRole('heading', { name: before.title, exact: true }).waitFor();
  assert.equal(await dialog().locator('.saved-example code').textContent(), before.excerpt);
  await close();
  await send('/tick 1');
  await wait(async () => (await experience()).revision === 2, 'quiet native refinement');
  await wait(async () => (await view()).tick.status === 'complete', 'step complete');
  const after = await experience();
  assert.deepEqual((await view()).messages.filter(m => m.role === 'user').map(m => m.id), originalUserIds);
  assert.deepEqual(after.source, before.source);
  assert.deepEqual(after.encounter, before.encounter);
  assert.equal(after.excerpt, before.excerpt);
  assert.deepEqual(after.feedback, []);
  assert.equal(after.revisionSource.kind, 'model_reinterpreted_example');
  assert.equal(after.revisionSource.selector_evidence_class, 'TEST_DOUBLE');
  checks.push('a quiet native step refines the title and note without another user message or changing the original observation');

  const draft = 'Keep comparing the original source.';
  await composer().fill(draft);
  await open();
  await dialog().getByRole('heading', { name: after.title, exact: true }).waitFor();
  await dialog().getByText(after.note, { exact: true }).waitFor();
  await dialog().getByText('Source experience', { exact: true }).click();
  await dialog().getByText('Pulse refined this interpretation. Test interpretation.', { exact: true }).waitFor();
  assert.equal(await dialog().getByText('Later feedback', { exact: false }).count(), 0);
  if (screenshots) {
    await dialog().getByText('Pulse refined this interpretation. Test interpretation.', { exact: true }).scrollIntoViewIfNeeded();
    await page.screenshot({ path: join(screenshots, 'example-refinement-desktop.png') });
  }
  await dialog().getByRole('button', { name: 'Open refinement conversation', exact: true }).click();
  await wait(async () => decodeURIComponent(new URL(page.url()).hash) === '#message-' + after.revisionSource.resultMessageId,
    'actual refinement result');
  await page.locator('#message-' + after.revisionSource.resultMessageId).waitFor({ state: 'visible' });
  await page.waitForFunction(id => document.activeElement?.id === id,
    'message-' + after.revisionSource.resultMessageId);
  assert.equal(await composer().inputValue(), draft);
  await open();
  await dialog().getByText('Source experience', { exact: true }).click();
  await dialog().getByRole('button', { name: 'Open reading conversation', exact: true }).click();
  await page.locator('#message-' + before.sourceAnchor.resultMessageId).waitFor({ state: 'visible' });
  await page.waitForFunction(id => document.activeElement?.id === id,
    'message-' + before.sourceAnchor.resultMessageId);
  assert.equal(await composer().inputValue(), draft);
  checks.push('source details attribute Pulse’s interpretation and open its actual completed turn while preserving the draft');

  await stop(server); server = start(true);
  await wait(async () => (await fetch(base + '/api/health')).ok, 'offline restart');
  assert.deepEqual(await experience(), after);
  await page.reload();
  await composer().waitFor();
  await page.setViewportSize({ width: 390, height: 844 });
  await open();
  await dialog().getByText('Source experience', { exact: true }).click();
  await dialog().getByText('Pulse refined this interpretation. Test interpretation.', { exact: true }).waitFor();
  assert.equal(await dialog().locator('.saved-example code').textContent(), before.excerpt);
  assert.ok(await dialog().evaluate(element => element.scrollWidth <= element.clientWidth + 1));
  if (screenshots) {
    await dialog().getByText('Pulse refined this interpretation. Test interpretation.', { exact: true }).scrollIntoViewIfNeeded();
    await page.screenshot({ path: join(screenshots, 'example-refinement-mobile.png') });
  }
  await dialog().getByRole('button', { name: 'Edit example note', exact: true }).click();
  await dialog().getByRole('textbox', { name: 'What should we notice? (optional)', exact: true }).fill('The owner’s current interpretation.');
  await dialog().getByRole('button', { name: 'Save refinement', exact: true }).click();
  await wait(async () => (await experience()).revision === 3, 'operator note revision');
  assert.equal((await experience()).revisionSource, undefined);
  assert.deepEqual((await experience()).source, before.source);
  assert.deepEqual((await experience()).feedback, []);
  await dialog().getByText('The owner’s current interpretation.', { exact: true }).waitFor();
  // The native revision can settle before React paints the cleared source
  // attribution. Wait for that specific UI state, not an arbitrary delay.
  await dialog().getByText('Pulse refined this interpretation. Test interpretation.', { exact: true }).waitFor({ state: 'hidden' });
  assert.equal(await dialog().getByText('Pulse refined this interpretation. Test interpretation.', { exact: true }).count(), 0);
  checks.push('restart retains the exact source and native actor; a later owner edit clears current Pulse attribution and fits a phone');
  assert.deepEqual(errors, []);
  console.log(JSON.stringify({ checks, pageErrors: errors, provider: 'explicit synthetic HTTP fixture',
    viewports: ['1280x900', '390x844'] }, null, 2));
} finally {
  await browser?.close(); await stop(server); await rm(directory, { recursive: true, force: true });
}
