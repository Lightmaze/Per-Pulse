// Copyright 2026 Lightmaze Contributors. Licensed under Apache-2.0.
// The server uses an explicit HTTP fixture, never a real model or user workspace.
import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { mkdtemp } from 'node:fs/promises';
import { createServer } from 'node:net';
import { tmpdir } from 'node:os';
import { join, resolve, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
import { createRequire } from 'node:module';
const root = resolve(dirname(fileURLToPath(import.meta.url)), '../..');
const require = createRequire(import.meta.url);
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const directory = await mkdtemp(join(tmpdir(), 'per-pulse-source-'));
const socket = createServer();
await new Promise(r => socket.listen(0, '127.0.0.1', r));
const port = socket.address().port;
await new Promise(r => socket.close(r));
const base = `http://127.0.0.1:${port}`;
let log = '', browser;
const errors = [];
const server = spawn(process.env.PER_PULSE_PYTHON || join(root, '.venv/bin/python'),
  [join(root, 'tests/browser/example_server.py'), '--data-dir', directory, '--port', String(port)],
  { cwd: root, env: { ...process.env, PYTHONPATH: join(root, 'src') }, windowsHide: true });
server.stdout.on('data', d => log += d);
server.stderr.on('data', d => log += d);
const wait = async (fn, label) => {
  for (let i = 0; i < 250; i++) { try { if (await fn()) return; } catch {} await new Promise(r => setTimeout(r, 100)); }
  throw new Error(`${label}\n${log}`);
};
const data = async path => { const r = await fetch(base + path); assert.ok(r.ok); return r.json(); };
try {
  await wait(async () => (await fetch(base + '/api/health')).ok, 'server ready');
  browser = await chromium.launch({ executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE || undefined });
  const context = await browser.newContext({ viewport: { width: 1280, height: 900 }, locale: 'en-US' });
  const page = await context.newPage();
  page.on('pageerror', e => errors.push(e.message));
  page.on('console', m => { if (m.type() === 'error') errors.push(m.text()); });
  await page.goto(base);
  assert.equal(await page.title(), 'Per Pulse');
  await page.getByRole('textbox', { name: /^Message / }).fill('Draft a synthetic customer notice.');
  await page.getByRole('button', { name: 'Send message', exact: true }).click();
  let original;
  await wait(async () => { original = (await data('/api/pages/home')).messages.find(m => m.role === 'assistant' && m.status === 'settled'); return original; }, 'Home reply');
  const source = page.locator(`[data-message-id="${original.id}"]`);
  await source.getByRole('button', { name: 'New page', exact: true }).click();
  const dialog = page.getByRole('dialog');
  assert.equal(await dialog.locator('#new-page-source').inputValue(), original.content);
  assert.equal(await dialog.getByRole('textbox', { name: 'Your request', exact: true }).inputValue(), 'Continue from this source.');
  assert.match(await dialog.locator('label[for="new-page-source"]').innerText(), /Per Pulse/);
  await dialog.getByRole('textbox', { name: 'Page name', exact: true }).fill('Source attribution check');
  await dialog.getByRole('textbox', { name: 'Your request', exact: true }).fill('Make a source-aware plan.');
  await dialog.getByRole('button', { name: 'Create page', exact: true }).click();
  await page.getByRole('heading', { name: 'Source attribution check', exact: true }).waitFor();
  const targetUrl = page.url();
  const target = new URL(targetUrl).pathname.split('/').pop();
  const view = await data('/api/pages/' + target);
  const first = view.messages.find(m => m.pageSource);
  assert.equal(first.role, 'user'); assert.equal(first.pageSource.role, 'assistant');
  const card = page.locator(`[data-message-id="${first.id}"]`);
  assert.equal(await card.locator('.message-label').innerText(), 'Source and request');
  assert.equal(await card.locator('.transfer-presentation-source .transfer-presentation-label').innerText(), 'Per Pulse');
  assert.equal(await card.locator('.transfer-presentation-request .message-content').innerText(), 'Make a source-aware plan.');
  const link = card.locator('.source-line a');
  assert.match(await link.getAttribute('href'), new RegExp(original.id));
  await link.click();
  await page.locator(`[data-message-id="${original.id}"]`).waitFor();
  assert.equal(new URL(page.url()).pathname, '/');
  assert.equal(new URL(page.url()).hash, '#message-' + original.id);
  await page.goto(targetUrl); await card.waitFor();
  await page.reload(); await card.waitFor();
  await page.getByRole('button', { name: 'Find a message', exact: true }).click();
  await page.getByLabel('Words in this conversation').fill('source-aware plan');
  await page.getByRole('button', { name: 'Find', exact: true }).click();
  const result = page.locator(`[data-search-message-id="${first.id}"]`);
  await result.waitFor();
  assert.equal(await result.locator('.transfer-presentation-source .transfer-presentation-label').innerText(), 'Per Pulse');
  await page.getByRole('button', { name: 'Close dialog', exact: true }).click();
  if (process.env.PER_PULSE_SOURCE_SCREENSHOT) await page.screenshot({ path: process.env.PER_PULSE_SOURCE_SCREENSHOT, fullPage: false });
  await page.setViewportSize({ width: 390, height: 844 });
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
  assert.equal(await card.locator('.transfer-presentation-source .transfer-presentation-label').innerText(), 'Per Pulse');
  assert.equal(await page.locator('vite-error-overlay').count(), 0);
  if (process.env.PER_PULSE_SOURCE_SCREENSHOT) await page.screenshot({ path: process.env.PER_PULSE_SOURCE_SCREENSHOT.replace('.png', '-mobile.png'), fullPage: false });
  assert.deepEqual(errors, []);
  console.log(JSON.stringify({ passed: true, checks: ['Home source author', 'separate request editor', 'native user identity', 'rendered author', 'source anchor', 'reload', 'search attribution', 'mobile overflow', 'no page errors'], base, directory }));
} finally {
  await browser?.close();
  if (server.exitCode === null) { const stopped = new Promise(r => server.once('exit', r)); server.kill(); await stopped; }
}