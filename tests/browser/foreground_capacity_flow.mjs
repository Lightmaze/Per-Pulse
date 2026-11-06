/** A busy background leaves room for a new Work through the actual browser.
 * Copyright 2026 Lightmaze Contributors. Licensed under Apache-2.0.
 * Replies are explicit synthetic loopback HTTP/SSE; World, Kernel and SQLite are real.
 */
import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { createServer } from 'node:http';
import { mkdir, mkdtemp, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { createRequire } from 'node:module';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '../..');
const { chromium } = createRequire(import.meta.url)(process.env.PLAYWRIGHT_MODULE || 'playwright');
const directory = await mkdtemp(join(tmpdir(), 'per-pulse-foreground-capacity-'));
const screenshots = process.env.PER_PULSE_CAPACITY_SCREENSHOTS;
if (screenshots) await mkdir(screenshots, { recursive: true });
const barrier = () => {
  let release;
  const promise = new Promise(resolve => { release = resolve; });
  return { promise, released: false, open() { this.released = true; release(); } };
};
const gates = { BACKGROUND_0: barrier(), BACKGROUND_1: barrier(), FOREGROUND_NOW: barrier() };
const requests = [], active = new Map(), maximum = new Map(), failures = [];
let server, browser, serverLog = '';
// This socket sees the actual ChatHTTPModelAdapter request before any reply bytes.
const provider = createServer(async (request, response) => {
  try {
    const chunks = [];
    for await (const chunk of request) chunks.push(chunk);
    const messages = JSON.parse(Buffer.concat(chunks).toString()).messages;
    const text = JSON.stringify(messages);
    const context = decodeURIComponent(request.url.split('/').at(-1));
    const marker = context.startsWith('work:') ? Object.keys(gates).find(key => text.includes(key)) : 'HOME';
    assert.ok(marker, 'request belongs to a known fixture context');
    const ordinal = requests.filter(item => item.marker === marker).length + 1;
    const held = marker === 'HOME' ? false : marker === 'FOREGROUND_NOW' ? ordinal === 1
      : messages.some(message => message.role === 'assistant');
    const record = { marker, context, ordinal, held, started: requests.length, finished: false };
    requests.push(record);
    active.set(marker, (active.get(marker) || 0) + 1);
    maximum.set(marker, Math.max(maximum.get(marker) || 0, active.get(marker)));
    if (held) await gates[marker].promise;
    const output = `Synthetic ${marker} result ${ordinal}.`;
    const events = output.split(/(?<= )/).map(token => ({
      choices: [{ index: 0, delta: { content: token }, finish_reason: null }],
    }));
    events.push({ choices: [{ index: 0, delta: {}, finish_reason: 'stop' }] });
    events.push({ choices: [], usage: { prompt_tokens: 80, completion_tokens: 8, total_tokens: 88 } });
    response.writeHead(200, { 'content-type': 'text/event-stream' });
    for (const event of events) response.write('data: ' + JSON.stringify(event) + '\n\n');
    response.end('data: [DONE]\n\n');
    record.finished = true;
    active.set(marker, active.get(marker) - 1);
  } catch (error) {
    failures.push(String(error));
    response.writeHead(500); response.end('Synthetic fixture failure');
  }
});
await new Promise(done => provider.listen(0, '127.0.0.1', done));
const endpoint = `http://127.0.0.1:${provider.address().port}/v1/chat/completions`;
const socket = createServer();
await new Promise(done => socket.listen(0, '127.0.0.1', done));
const port = socket.address().port;
await new Promise(done => socket.close(done));
const base = 'http://127.0.0.1:' + port;

// Only the existing runtime capacity is configured. The test endpoint observes
// public native scheduling/ledger state; it does not admit or complete work.
const fixture = String.raw`
import os
from pathlib import Path
from urllib.parse import quote
import uvicorn
import per_pulse.service as service_module
from per_pulse.app import create_app
from per_pulse.service import PulseSettings
from pulse_harness.adapters.chat_http import ChatHTTPConfig, ChatHTTPModelAdapter

base_config = service_module.RuntimeServiceConfig
service_module.RuntimeServiceConfig = lambda **kw: base_config(**kw, pulse_worker_capacity=2)
def factory(scope):
    return ChatHTTPModelAdapter(ChatHTTPConfig(
        os.environ['PER_PULSE_CAPACITY_ENDPOINT'] + '/' + quote(scope.context_id, safe=''), 'synthetic-capacity-browser',
        allow_local_http=True, no_auth_local=True, max_retries=0))
app = create_app(Path(os.environ['PER_PULSE_CAPACITY_DATA']), model_factory=factory,
    pulse=PulseSettings(enabled=True, spontaneous_rate=0, check_interval_seconds=0.02,
        refractory_seconds=0, max_propagation_depth=0))
@app.get('/__capacity/state')
def state(page_id: str = ''):
    runtime = app.state.pages.runtime
    result = {'capacity': runtime.scheduling_snapshot()['capacity']}
    if page_id:
        center = runtime.world.get_task_front(page_id).center_id
        ticks = runtime.causal_ledger.list_events(kind='spontaneous', center_id=center, limit=10)
        schedule = runtime.storage.get_center_schedule_state(center)
        result['background_decided'] = bool(ticks and schedule and
            schedule.last_decision_at >= ticks[0].created_at and
            schedule.decision.value in {'admitted', 'waiting'})
    return result
route = app.router.routes.pop()
index = next(i for i, r in enumerate(app.router.routes) if getattr(r, 'path', None) == '/{route:path}')
app.router.routes.insert(index, route)
uvicorn.run(app, host='127.0.0.1', port=int(os.environ['PER_PULSE_CAPACITY_PORT']), log_level='warning')
`;
server = spawn(process.env.PER_PULSE_PYTHON || join(root, '.venv/bin/python'), ['-c', fixture], {
  cwd: root, env: { ...process.env, PER_PULSE_CAPACITY_ENDPOINT: endpoint,
    PER_PULSE_CAPACITY_DATA: directory, PER_PULSE_CAPACITY_PORT: String(port) },
  stdio: ['ignore', 'pipe', 'pipe'],
});
server.stdout.on('data', chunk => { serverLog += chunk; });
server.stderr.on('data', chunk => { serverLog += chunk; });
const wait = async (fn, label, timeout = 20000) => {
  const deadline = Date.now() + timeout;
  while (Date.now() < deadline) {
    if (await fn()) return;
    await new Promise(done => setTimeout(done, 80));
  }
  throw new Error('Timed out: ' + label + '\n' + JSON.stringify(requests) + '\n' + serverLog);
};
const json = async path => {
  const response = await fetch(base + path);
  assert.ok(response.ok, path);
  return response.json();
};
const recorded = (marker, ordinal) => requests.find(item => item.marker === marker && item.ordinal === ordinal);
const settled = state => state.messages.filter(message => message.role === 'assistant' && message.status === 'settled');
const checks = [];
try {
  await wait(async () => { try { return (await fetch(base + '/api/health')).ok; } catch { return false; } }, 'service ready');
  browser = await chromium.launch({ executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE || undefined,
    args: process.env.PLAYWRIGHT_CHROMIUM_ARGS ? JSON.parse(process.env.PLAYWRIGHT_CHROMIUM_ARGS) : undefined });
  const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.goto(base);
  const create = async (title, instruction) => {
    await page.getByRole('button', { name: 'Create a page', exact: true }).click();
    await page.getByRole('textbox', { name: 'Page name', exact: true }).fill(title);
    await page.getByRole('textbox', { name: 'Where should we begin?', exact: true }).fill(instruction);
    await page.getByRole('button', { name: 'Create page', exact: true }).click();
    await page.getByRole('heading', { name: title, exact: true }).waitFor();
    return new URL(page.url()).pathname.split('/').pop();
  };
  const send = async text => {
    await page.getByRole('textbox', { name: /^Message / }).fill(text);
    await page.getByRole('button', { name: 'Send message', exact: true }).click();
  };
  const backgrounds = [];
  for (let index = 0; index < 2; index++) {
    const id = await create('Background ' + index, 'BACKGROUND_' + index);
    backgrounds.push(id);
    await wait(async () => settled(await json('/api/pages/' + id)).length === 1, 'background initial reply');
  }
  await page.getByLabel('Workspace navigation').getByRole('link', { name: 'Background 0', exact: true }).click();
  await send('/tick 1');
  await wait(() => recorded('BACKGROUND_0', 2), 'first background HTTP blocked');
  await page.getByLabel('Workspace navigation').getByRole('link', { name: 'Background 1', exact: true }).click();
  await send('/tick 1');
  await wait(async () => (await json('/__capacity/state?page_id=' + backgrounds[1])).background_decided,
    'actual scheduling decision for second background');
  assert.equal(recorded('BACKGROUND_1', 2), undefined, 'background cannot consume the foreground reserve');
  checks.push('capacity two holds one real background HTTP request and queues the next background');

  const foreground = await create('My immediate Work', 'FOREGROUND_NOW');
  await wait(() => recorded('FOREGROUND_NOW', 1), 'foreground HTTP before background release', 5000);
  assert.equal(gates.BACKGROUND_0.released, false);
  assert.equal(recorded('BACKGROUND_0', 2).finished, false);
  assert.equal(recorded('BACKGROUND_1', 2), undefined);
  const capacity = (await json('/__capacity/state')).capacity;
  assert.equal(capacity.worker_limit, 2);
  assert.equal(capacity.worker_running, 2);
  checks.push('creating a Work in the UI starts its actual HTTP request while the background stays blocked');

  await send('Continue this same Work after the first result.');
  await wait(async () => (await json('/api/pages/' + foreground)).messages.some(message =>
    message.content === 'Continue this same Work after the first result.'), 'same-context input saved');
  assert.equal(recorded('FOREGROUND_NOW', 2), undefined, 'same context cannot overlap its active request');
  gates.BACKGROUND_0.open();
  await wait(() => recorded('BACKGROUND_1', 2), 'queued background reuses released slot');
  assert.equal(gates.FOREGROUND_NOW.released, false);
  assert.equal(recorded('FOREGROUND_NOW', 1).finished, false);
  checks.push('releasing a background advances the next background while the foreground is still active');

  gates.FOREGROUND_NOW.open();
  await wait(async () => settled(await json('/api/pages/' + foreground)).length === 2, 'both serial foreground replies');
  const output = 'Synthetic FOREGROUND_NOW result 2.';
  const article = page.locator('.message-assistant').filter({ has: page.getByText(output, { exact: true }) });
  await article.waitFor();
  assert.ok(await article.isVisible());
  const messageId = await article.getAttribute('data-message-id');
  await article.getByRole('button', { name: 'Bring to Home', exact: true }).click();
  const dialog = page.getByRole('dialog', { name: 'Bring this back to Home.', exact: true });
  await dialog.getByRole('button', { name: 'Bring to Home', exact: true }).click();
  await dialog.waitFor({ state: 'hidden' });
  await page.getByRole('textbox', { name: 'Message Home', exact: true }).waitFor();
  const returned = page.locator('.message').filter({ has: page.getByText(output, { exact: true }) });
  await returned.waitFor();
  if (screenshots) await page.screenshot({ path: join(screenshots, 'foreground-capacity-return.png') });
  await returned.locator('.source-line').getByRole('link', { name: 'My immediate Work', exact: true }).click();
  await article.waitFor();
  assert.equal(new URL(page.url()).hash, '#message-' + messageId);
  const home = await json('/api/pages/home');
  assert.ok(home.messages.some(message => message.content === output && message.sourcePageId === foreground && message.sourceMessageId === messageId));
  checks.push('serial replies are visible and the selected result returns Home with its exact original source');

  gates.BACKGROUND_1.open();
  for (const id of backgrounds) {
    await wait(async () => (await json('/api/pages/' + id)).tick.status === 'complete', 'background run complete');
    assert.equal(settled(await json('/api/pages/' + id)).length, 2);
  }
  // No retry or failure is injected: each initial turn, requested step and
  // foreground input must produce one result, not a replay after slot release.
  for (const marker of Object.keys(gates)) {
    assert.equal(requests.filter(item => item.marker === marker).length, 2, marker + ' has no duplicate turn');
    assert.equal(maximum.get(marker), 1, marker + ' never runs concurrently with itself');
  }
  assert.ok(requests.every(item => item.finished));
  assert.deepEqual(failures, []);
  assert.deepEqual(errors, []);
  checks.push('all requested background steps finish once and every context remains serial');
  console.log(JSON.stringify({ passed: checks.length, checks, provider: 'explicit synthetic loopback HTTP/SSE fixture', requests }, null, 2));
} finally {
  for (const gate of Object.values(gates)) gate.open();
  await browser?.close();
  if (server.exitCode === null && server.signalCode === null) {
    const closed = new Promise(done => server.once('exit', done));
    server.kill('SIGTERM');
    const force = setTimeout(() => server.kill('SIGKILL'), 9000);
    await closed; clearTimeout(force);
  }
  await new Promise(done => provider.close(done));
  await rm(directory, { recursive: true, force: true });
}
