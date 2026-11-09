/** Original, complete arguments remain reviewable when text contains a marker.
 * Native approval and file effects; synthetic HTTP model choices only.
 * Copyright 2026 Lightmaze Contributors. Licensed under Apache-2.0.
 */
import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { createServer } from 'node:net';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { createRequire } from 'node:module';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '../..');
const { chromium } = createRequire(import.meta.url)(process.env.PLAYWRIGHT_MODULE || 'playwright');
const socket = createServer();
await new Promise(done => socket.listen(0, '127.0.0.1', done));
const port = socket.address().port;
await new Promise(done => socket.close(done));
const base = `http://127.0.0.1:${port}`;
const server = spawn(process.env.PER_PULSE_PYTHON || join(root, '.venv/bin/python'),
  [join(root, 'tests/browser/literal_approval_server.py')], { cwd: root,
    env: { ...process.env, PER_PULSE_QA_PORT: String(port) }, stdio: ['ignore', 'pipe', 'pipe'] });
let log = '', browser;
server.stdout.on('data', data => { log += data; });
server.stderr.on('data', data => { log += data; });
const wait = async (fn, label) => {
  const end = Date.now() + 20000;
  while (Date.now() < end) {
    try { if (await fn()) return; } catch {}
    await new Promise(done => setTimeout(done, 80));
  }
  throw Error(`Timed out: ${label}\n${log}`);
};
try {
  await wait(async () => (await fetch(base + '/api/health')).ok, 'service ready');
  browser = await chromium.launch({ executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE || undefined });
  const page = await browser.newPage();
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.goto(base);
  await page.getByRole('button', { name: 'Create a page', exact: true }).click();
  await page.getByRole('textbox', { name: 'Page name', exact: true }).fill('Literal approval');
  await page.getByRole('textbox', { name: 'Where should we begin?', exact: true }).fill('WRITE_LITERAL_APPROVAL');
  await page.getByRole('button', { name: 'Create page', exact: true }).click();
  const allow = page.getByRole('button', { name: 'Allow once', exact: true });
  await allow.waitFor();
  await wait(() => allow.isEnabled(), 'original action is ready for a decision');
  const id = new URL(page.url()).pathname.split('/').pop();
  const view = () => fetch(`${base}/api/pages/${id}`).then(response => response.json());
  let current = await view();
  assert.equal(current.execution.pendingActions[0].canResolve, true);
  assert.equal(current.artifacts.length, 0, 'no file effect before approval');
  await allow.click();
  await wait(async () => (await view()).execution.status === 'idle', 'approved original Work finished');
  current = await view();
  assert.equal(current.artifacts.length, 1);
  assert.equal(current.artifacts[0].path, 'literal-approval.txt');
  await page.getByRole('button', { name: /literal-approval\.txt/ }).click();
  await page.getByRole('region', { name: 'Files from this page', exact: true })
    .getByText('The literal [REDACTED] marker is part of this file.', { exact: false }).waitFor();
  assert.deepEqual(errors, []);
  console.log(JSON.stringify({ passed: 4, checks: ['complete literal arguments are approvable',
    'no file before approval', 'one approved file from original Work', 'exact literal content visible'],
    modelEvidence: 'explicit synthetic HTTP only' }));
} finally {
  await browser?.close();
  const exited = new Promise(done => server.once('exit', done));
  server.kill('SIGTERM');
  const force = setTimeout(() => server.kill('SIGKILL'), 10000);
  if (server.exitCode === null && server.signalCode === null) await exited;
  clearTimeout(force);
}
