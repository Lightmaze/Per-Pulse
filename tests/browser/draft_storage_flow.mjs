/** Actual browser storage failures with a native, explicit example workspace.
 * Copyright 2026 Lightmaze Contributors. Licensed under Apache-2.0.
 */
import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { mkdtemp, rm } from 'node:fs/promises';
import { createServer } from 'node:net';
import { tmpdir } from 'node:os';
import { join, resolve, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
import { createRequire } from 'node:module';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '../..');
const { chromium } = createRequire(import.meta.url)(process.env.PLAYWRIGHT_MODULE || 'playwright');
const directory = await mkdtemp(join(tmpdir(), 'per-pulse-draft-storage-'));
const socket = createServer();
await new Promise(done => socket.listen(0, '127.0.0.1', done));
const port = socket.address().port;
await new Promise(done => socket.close(done));
const base = `http://127.0.0.1:${port}`;
const server = spawn(process.env.PER_PULSE_PYTHON || join(root, '.venv/bin/python'),
  [join(root, 'tests/browser/example_server.py'), '--data-dir', directory, '--port', String(port)],
  { cwd: root, env: { ...process.env, PYTHONPATH: join(root, 'src') }, stdio: ['ignore', 'pipe', 'pipe'] });
let log = '', browser;
server.stdout.on('data', data => log += data);
server.stderr.on('data', data => log += data);
const wait = async (fn, label) => {
  const deadline = Date.now() + 20000;
  while (Date.now() < deadline) {
    try { if (await fn()) return; } catch {}
    await new Promise(done => setTimeout(done, 80));
  }
  throw Error('Timed out: ' + label + '\n' + log);
};
try {
  await wait(async () => (await fetch(base + '/api/health')).ok, 'fixture ready');
  const response = await fetch(base + '/api/pages', { method: 'POST',
    headers: { 'Content-Type': 'application/json', Origin: base },
    body: JSON.stringify({ title: 'Other draft', instruction: 'An explicit example Work.' }) });
  assert.equal(response.status, 201, await response.text());
  const bootstrap = await (await fetch(base + '/api/bootstrap')).json();
  const other = bootstrap.pages.find(page => page.title === 'Other draft');
  assert.ok(other);
  browser = await chromium.launch({ executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE || undefined });
  const context = await browser.newContext({ viewport: { width: 1280, height: 900 } });
  await context.addInitScript(() => {
    window.__failDraftWrites = false;
    const set = Storage.prototype.setItem, remove = Storage.prototype.removeItem;
    Storage.prototype.setItem = function(key, value) {
      if (window.__failDraftWrites && key.includes('.draft.')) throw new DOMException('Synthetic full storage', 'QuotaExceededError');
      return set.call(this, key, value);
    };
    Storage.prototype.removeItem = function(key) {
      if (window.__failDraftWrites && key.includes('.draft.')) throw new DOMException('Synthetic restricted storage', 'SecurityError');
      return remove.call(this, key);
    };
    Object.defineProperty(navigator, 'clipboard', { configurable: true, value: {
      writeText: async value => { window.__copiedDraft = value; },
    } });
  });
  const page = await context.newPage();
  const errors = [], sends = [];
  page.on('pageerror', error => errors.push(error.message));
  page.on('request', request => { if (request.method() === 'POST' && /\/messages$/.test(request.url())) sends.push(request.url()); });
  await page.goto(base);
  const input = () => page.getByRole('textbox', { name: 'Message Home', exact: true });
  await input().fill('An older saved thought.');
  await page.evaluate(() => window.__failDraftWrites = true);
  const current = 'Keep this unsaved thought: café 山川 🌍.';
  await input().fill(current);
  const notice = page.locator('.composer-draft-notice');
  await notice.getByText('This draft is only in this tab. Copy the text or save it before refreshing.', { exact: true }).waitFor();
  await notice.getByRole('button', { name: 'Copy draft text', exact: true }).click();
  await wait(async () => (await page.evaluate(() => window.__copiedDraft)) === current, 'copy current unsaved text');
  await page.locator(`.sidebar a[href="/page/${other.id}"]`).click();
  await page.getByRole('textbox', { name: 'Message Other draft', exact: true }).waitFor();
  assert.equal(await notice.count(), 0, 'Home storage failure does not appear in another Work');
  await page.locator('.sidebar a[href="/"]').filter({ hasText: 'Home' }).click();
  assert.equal(await input().inputValue(), current);
  await notice.waitFor();
  await page.evaluate(() => window.__failDraftWrites = false);
  await notice.getByRole('button', { name: 'Try saving again', exact: true }).click();
  await wait(async () => await notice.count() === 0, 'save retry clears failure');
  await page.reload();
  assert.equal(await input().inputValue(), current);
  await page.evaluate(() => window.__failDraftWrites = true);
  await input().fill('');
  await notice.getByText('The saved browser draft could not be cleared. An older draft may reappear after refreshing.', { exact: true }).waitFor();
  await page.evaluate(() => window.__failDraftWrites = false);
  await notice.getByRole('button', { name: 'Try saving again', exact: true }).click();
  await wait(async () => await notice.count() === 0, 'clear retry succeeds');
  await page.reload();
  assert.equal(await input().inputValue(), '');
  assert.deepEqual(errors, []);
  assert.deepEqual(sends, [], 'copy and browser-save retry never submit a model message');
  console.log(JSON.stringify({ passed: 7, checks: ['write failure visible', 'copy exact current text',
    'current draft and notice survive in-app navigation', 'failure scoped to its page',
    'retry persists through reload', 'failed clear explicit and recoverable', 'no message submission or page error'] }));
} finally {
  await browser?.close();
  if (server.exitCode === null && server.signalCode === null) {
    const exited = new Promise(done => server.once('exit', done));
    server.kill('SIGTERM');
    await Promise.race([exited, new Promise(done => setTimeout(() => { server.kill('SIGKILL'); done(); }, 5000))]);
  }
  await rm(directory, { recursive: true, force: true, maxRetries: 10, retryDelay: 200 });
}
