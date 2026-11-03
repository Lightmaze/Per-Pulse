/** File changes apply without an approval prompt, and Undo puts the file back.
 * Copyright 2026 Lightmaze Contributors. Licensed under Apache-2.0.
 * Model replies are a synthetic HTTP fixture; file effects, checkpoints, World, Kernel and SQLite are real.
 */
import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { createServer } from 'node:net';
import { mkdir } from 'node:fs/promises';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { createRequire } from 'node:module';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '../..');
const { chromium } = createRequire(import.meta.url)(process.env.PLAYWRIGHT_MODULE || 'playwright');
const screenshots = process.env.PER_PULSE_UNDO_SCREENSHOTS;
if (screenshots) await mkdir(screenshots, { recursive: true });
const socket = createServer();
await new Promise(done => socket.listen(0, '127.0.0.1', done));
const port = socket.address().port;
await new Promise(done => socket.close(done));
const base = 'http://127.0.0.1:' + port;
let browser, serverLog = '';
const server = spawn(process.env.PER_PULSE_PYTHON || join(root, '.venv/bin/python'), [join(root, 'tests/browser/undo_server.py')], {
  cwd: root, env: { ...process.env, PER_PULSE_QA_PORT: String(port) }, stdio: ['ignore', 'pipe', 'pipe'],
});
server.stdout.on('data', chunk => { serverLog += chunk; });
server.stderr.on('data', chunk => { serverLog += chunk; });
const wait = async (fn, label, timeout = 20000) => {
  const deadline = Date.now() + timeout;
  while (Date.now() < deadline) {
    try { if (await fn()) return; } catch {}
    await new Promise(done => setTimeout(done, 80));
  }
  throw new Error('Timed out: ' + label + '\n' + serverLog);
};
const json = (path, init) => fetch(base + path, init).then(r => { assert.ok(r.ok, path); return r.json(); });
const checks = [];
try {
  await wait(async () => (await fetch(base + '/api/health')).ok, 'service ready');
  browser = await chromium.launch({ executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE || undefined,
    args: process.env.PLAYWRIGHT_CHROMIUM_ARGS ? JSON.parse(process.env.PLAYWRIGHT_CHROMIUM_ARGS) : undefined });
  const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  const create = async (title, instruction) => {
    await page.getByRole('button', { name: 'Create a page', exact: true }).click();
    await page.getByRole('textbox', { name: 'Page name', exact: true }).fill(title);
    await page.getByRole('textbox', { name: 'Where should we begin?', exact: true }).fill(instruction);
    await page.getByRole('button', { name: 'Create page', exact: true }).click();
    await page.getByRole('heading', { name: title, exact: true }).waitFor();
  };
  await page.goto(base);

  await create('Launch plan', 'REVISE_PLAN');
  const files = page.getByRole('region', { name: 'Files from this page', exact: true });
  await files.getByRole('button', { name: /^plan\.md/ }).waitFor();
  assert.equal(await page.getByRole('button', { name: 'Allow once', exact: true }).count(), 0, 'no approval prompt');
  assert.match((await json('/__undo/files'))['plan.md'], /Monday/);
  checks.push('a file change inside the workspace applies without an approval prompt');

  if (screenshots) await page.screenshot({ path: join(screenshots, 'undo-before.png') });
  await files.getByRole('button', { name: 'Undo', exact: true }).click();
  await files.getByRole('button', { name: /^plan\.md Undone/ }).waitFor();
  assert.equal(await files.getByRole('button', { name: 'Undo', exact: true }).count(), 0);
  assert.equal((await json('/__undo/files'))['plan.md'], '# Launch plan\n\nShip on Friday.\n');
  checks.push('Undo puts the file back and marks the change as undone');
  if (screenshots) await page.screenshot({ path: join(screenshots, 'undo-after.png') });

  await create('Summary', 'WRITE_SUMMARY');
  await files.getByRole('button', { name: /^summary\.md/ }).waitFor();
  await json('/__undo/edit-summary', { method: 'POST' });
  await files.getByRole('button', { name: 'Undo', exact: true }).click();
  await files.getByRole('alert').getByText('This file changed after this change. Undo the later change first.', { exact: true }).waitFor();
  assert.equal((await json('/__undo/files'))['summary.md'], 'Edited by hand.\n');
  checks.push('Undo leaves a later edit alone and says why');

  await page.setViewportSize({ width: 390, height: 844 });
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  assert.ok(overflow <= 1, 'no horizontal scroll on a phone');
  if (screenshots) await page.screenshot({ path: join(screenshots, 'undo-mobile.png'), fullPage: true });
  checks.push('the file list with Undo fits a phone screen');

  assert.deepEqual(errors, []);
  console.log(JSON.stringify({ passed: checks.length, checks, provider: 'explicit synthetic HTTP fixture' }));
} finally {
  await browser?.close();
  server.kill('SIGTERM');
}
