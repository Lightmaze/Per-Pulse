/** Plain JSON replies survive continued Home conversation and a process restart.
 * Copyright 2026 Lightmaze Contributors. Licensed under Apache-2.0.
 * Synthetic HTTP replies only; native World, Kernel and SQLite are real.
 */
import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { createServer } from 'node:net';
import { mkdtemp, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { createRequire } from 'node:module';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '../..');
const { chromium } = createRequire(import.meta.url)(process.env.PLAYWRIGHT_MODULE || 'playwright');
const directory = await mkdtemp(join(tmpdir(), 'per-pulse-plain-json-'));
const socket = createServer();
await new Promise(done => socket.listen(0, '127.0.0.1', done));
const port = socket.address().port;
await new Promise(done => socket.close(done));
const base = 'http://127.0.0.1:' + port;
const text = '{"text":"","tool_calls":[{"name":"demo"}]}';
let browser, server, serverLog = '';
const start = () => {
  const child = spawn(process.env.PER_PULSE_PYTHON || join(root, '.venv/bin/python'), [join(root, 'tests/browser/plain_json_reply_server.py')], {
    cwd: root, env: { ...process.env, PYTHONPATH: join(root, 'src'), PYTHONDONTWRITEBYTECODE: '1',
      PER_PULSE_QA_PORT: String(port), PER_PULSE_QA_DATA_DIR: directory }, stdio: ['ignore', 'pipe', 'pipe'],
  });
  child.stdout.on('data', chunk => { serverLog += chunk; });
  child.stderr.on('data', chunk => { serverLog += chunk; });
  return child;
};
const stop = async child => {
  if (!child || child.exitCode !== null || child.signalCode !== null) return;
  const exited = new Promise(done => child.once('exit', done));
  // Graceful shutdown releases the native runtime lease before the next process.
  const response = await fetch(base + '/__fixture/plain-json/stop', {
    method: 'POST', signal: AbortSignal.timeout(20000),
  });
  assert.ok(response.ok, 'native runtime shutdown completed');
  let timer;
  try {
    await Promise.race([exited, new Promise((_, reject) => { timer = setTimeout(() => { child.kill(); reject(Error('Fixture process did not stop')); }, 10000); })]);
  } finally { clearTimeout(timer); }
};
const wait = async (fn, label) => {
  const deadline = Date.now() + 20000;
  while (Date.now() < deadline) {
    try { if (await fn()) return; } catch {}
    await new Promise(done => setTimeout(done, 80));
  }
  throw Error('Timed out: ' + label + '\n' + serverLog);
};
const json = async path => {
  const response = await fetch(base + path);
  assert.ok(response.ok, path);
  return response.json();
};
const view = () => json('/api/pages/home');
const status = () => json('/__fixture/plain-json/status');
try {
  server = start();
  await wait(async () => (await fetch(base + '/api/health')).ok, 'native fixture ready');
  const firstGeneration = (await status()).generation;
  browser = await chromium.launch({ executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE || undefined });
  const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
  const errors = [], externalRequests = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.route('**/*', route => {
    if (route.request().url().startsWith(base + '/')) return route.continue();
    externalRequests.push(route.request().url());
    return route.abort();
  });
  await page.goto(base);
  const send = async (content, expectedCount) => {
    await page.getByRole('textbox', { name: 'Message Home', exact: true }).fill(content);
    await page.getByRole('button', { name: 'Send message', exact: true }).click();
    await wait(async () => {
      const current = await view();
      return current.page.status === 'idle' && current.messages.filter(row => row.role === 'assistant').length === expectedCount;
    }, 'saved reply ' + expectedCount);
    const current = await view();
    assert.deepEqual(current.messages.filter(row => row.role === 'assistant').map(row => row.content), Array(expectedCount).fill(text));
    await wait(async () => await page.locator('.message-assistant').count() === expectedCount, 'rendered replies');
    for (const message of await page.locator('.message-assistant').all()) {
      await message.getByText(text, { exact: true }).waitFor();
    }
  };
  await send('Show a plain JSON example. [Synthetic browser fixture]', 1);
  await send('Continue after the JSON. [Synthetic browser fixture]', 2);
  const before = await status();
  assert.equal(before.requests.length, 2);
  assert.ok(before.requests[1].messages.some(row => row.role === 'assistant' && row.content === text));
  // Close only this isolated page before stopping its server, not any user browser.
  await page.goto('about:blank');
  await stop(server);
  server = start();
  await wait(async () => (await status()).generation !== firstGeneration, 'same native database reopened in new process');
  assert.equal((await status()).requests.length, 0, 'restoration does not generate a model request');
  assert.deepEqual((await view()).messages.filter(row => row.role === 'assistant').map(row => row.content), [text, text]);
  await page.goto(base);
  await wait(async () => await page.locator('.message-assistant').count() === 2, 'restored replies visible');
  await send('Continue after restart. [Synthetic browser fixture]', 3);
  const after = await status();
  assert.equal(after.requests.length, 1);
  assert.equal(after.requests[0].messages.filter(row => row.role === 'assistant' && row.content === text).length, 2);
  assert.deepEqual(errors, []);
  assert.deepEqual(externalRequests, []);
  console.log(JSON.stringify({ checks: ['exact plain JSON rendered in Home', 'second message continues', 'new process reads same native database', 'third message continues after restart', 'plain JSON stays text on provider wire', 'no page errors'], syntheticModelRequests: 3, externalRequests: 0 }));
} finally {
  await browser?.close();
  await stop(server);
  await rm(directory, { recursive: true, force: true, maxRetries: 10, retryDelay: 200 });
}
