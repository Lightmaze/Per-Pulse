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
  // A true browser context keeps both tabs in the same browser-local storage scope.
  // browser.newPage() creates an owned context that forbids context.newPage().
  const context = await browser.newContext({ viewport: { width: 1280, height: 900 } });
  const page = await context.newPage();
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

  await create('Review draft', 'REVIEW_DRAFT');
  await page.locator('.composer textarea').fill('Keep this review draft.');
  await create('Launch plan', 'REVISE_PLAN');
  const files = page.getByRole('region', { name: 'Files from this page', exact: true });
  await files.getByRole('button', { name: /^plan\.md/ }).waitFor();
  assert.equal(await page.getByRole('button', { name: 'Allow once', exact: true }).count(), 0, 'no approval prompt');
  assert.match((await json('/__undo/files'))['plan.md'], /Monday/);
  checks.push('a file change inside the workspace applies without an approval prompt');

  if (screenshots) await page.screenshot({ path: join(screenshots, 'undo-before.png') });
  const receiverId = (await json('/api/bootstrap')).pages.find(item => item.title === 'Review draft')?.id;
  assert.ok(receiverId, 'real receiving Work exists');
  const beforeReceiver = (await json('/api/pages/' + receiverId)).messages.map(message => message.id);
  // The other Work was visited in this tab, so the UI has an older in-memory
  // copy. A second tab edits its unsent text before this file handoff.
  // Attaching a verified artifact must not silently resurrect the old draft.
  const receiverEditor = await page.context().newPage();
  await receiverEditor.goto(base + '/page/' + receiverId);
  await receiverEditor.getByRole('textbox', { name: 'Message Review draft', exact: true })
    .fill('Updated review draft from another tab.');
  await receiverEditor.close();
  await files.getByRole('button', { name: /^plan\.md/ }).click();
  await files.getByRole('button', { name: 'Add the full file to another page’s draft', exact: true }).click();
  await page.getByRole('dialog', { name: 'Add the full file to another page’s draft' })
    .getByRole('option', { name: 'Review draft' }).click();
  await page.getByRole('heading', { name: 'Review draft', exact: true }).waitFor();
  const receivingDraft = await page.locator('.composer textarea').inputValue();
  assert.match(receivingDraft, /Updated review draft from another tab\./);
  assert.doesNotMatch(receivingDraft, /Keep this review draft\./);
  assert.match(receivingDraft, /Verified file from Launch plan: plan\.md/);
  await page.locator('.composer-materials').getByText('plan.md', { exact: true }).waitFor();
  assert.deepEqual((await json('/api/pages/' + receiverId)).messages.map(message => message.id), beforeReceiver,
    'attaching a verified file must not silently run the receiving Work');
  checks.push('a verified file enters another Work without replacing the newer unsent draft from another tab');
  await page.getByRole('button', { name: 'Send message', exact: true }).click();
  await wait(async () => (await json('/api/pages/' + receiverId)).messages.some(message =>
    message.role === 'user' && message.content.includes('Verified file from Launch plan: plan.md')
      && message.materials?.some(material => material.name === 'plan.md')), 'receiving Work gets native file material');
  checks.push('the receiving Work explicitly sends the complete file with its original source');
  await page.locator('nav.page-nav').getByRole('link', { name: 'Launch plan', exact: true }).click();
  await page.getByRole('heading', { name: 'Launch plan', exact: true }).waitFor();
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
