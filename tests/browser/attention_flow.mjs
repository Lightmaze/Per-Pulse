/** Chromium acceptance for sourced native model attention in a single Context Body.
 * A real local World/Kernel and SQLite run with explicitly synthetic HTTP
 * model responses. No live provider or autonomous usefulness claim.
 * Copyright 2026 Lightmaze Contributors. Licensed under Apache-2.0.
 */
import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { mkdtemp, mkdir, rm } from 'node:fs/promises';
import { createServer } from 'node:net';
import { tmpdir } from 'node:os';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { createRequire } from 'node:module';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '../..');
const require = createRequire(import.meta.url);
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const directory = await mkdtemp(join(tmpdir(), 'per-pulse-attention-browser-'));
const screenshots = process.env.PER_PULSE_ATTENTION_SCREENSHOTS;
const socket = createServer();
await new Promise(done => socket.listen(0, '127.0.0.1', done));
const port = socket.address().port;
await new Promise(done => socket.close(done));
const base = `http://127.0.0.1:${port}`;
let serverLog = '';
const startServer = () => {
  const child = spawn(process.env.PER_PULSE_PYTHON || join(root, '.venv/bin/python'), [
    join(root, 'tests/browser/attention_server.py'),
    '--data-dir', directory, '--port', String(port),
  ], { cwd: root, stdio: ['ignore', 'pipe', 'pipe'] });
  child.stdout.on('data', data => { serverLog += data; });
  child.stderr.on('data', data => { serverLog += data; });
  return child;
};
const stopServer = async child => {
  if (child.exitCode !== null || child.signalCode !== null) return;
  child.kill('SIGTERM');
  const force = setTimeout(() => child.kill('SIGKILL'), 10000);
  await new Promise(done => child.once('exit', done));
  clearTimeout(force);
};
const wait = async (condition, label, timeout = 15000) => {
  const deadline = Date.now() + timeout;
  while (Date.now() < deadline) {
    try { if (await condition()) return; } catch { /* server starting or UI changing */ }
    await new Promise(done => setTimeout(done, 75));
  }
  throw new Error(`Timed out: ${label}\n${serverLog}`);
};
let server = startServer();
let browser;
const errors = [];
const checks = [];

try {
  await wait(async () => (await fetch(base + '/api/health')).ok, 'World/Kernel HTTP ready');
  browser = await chromium.launch({
    executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE || undefined,
    args: process.env.PLAYWRIGHT_CHROMIUM_ARGS
      ? JSON.parse(process.env.PLAYWRIGHT_CHROMIUM_ARGS) : undefined,
  });
  const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
  page.on('pageerror', error => errors.push(error.message));
  await page.goto(base);
  await page.getByRole('textbox', { name: 'Message Home', exact: true }).waitFor();

  const homePrompt = 'RETAIN_ATTENTION_GOAL: make the visitor experience usable before asking for a profile.';
  await page.getByRole('textbox', { name: 'Message Home', exact: true }).fill(homePrompt);
  await page.getByRole('button', { name: 'Send message', exact: true }).click();
  await wait(async () => (await (await fetch(base + '/api/pulse')).json())
    .retainedAttention.some(item => item.pageId === 'home' && item.note.includes('visitor can actually')),
  'model action and World turn both settled');
  const state = (await (await fetch(base + '/api/pulse')).json());
  const thought = state.retainedAttention.find(item => item.pageId === 'home');
  assert.equal(thought.note, 'Check that a visitor can actually try the experience.');
  assert.equal(thought.drive, 0.72);
  assert.equal(thought.sourcePageId, 'home');
  assert.equal(thought.sourceMessageId, thought.sourceEventId);
  assert.ok(thought.sourceActionId && thought.sourceTurnId);
  const original = (await (await fetch(base + '/api/pages/home?sourceMessageId=' +
    encodeURIComponent(thought.sourceMessageId))).json());
  assert.equal(original.messages.find(message => message.id === thought.sourceMessageId)?.content, homePrompt);
  checks.push('original-model-action source proven by World message');

  await page.getByRole('button', { name: 'Activity', exact: true }).click();
  await page.getByRole('dialog', { name: 'Activity', exact: true }).waitFor();
  await page.getByRole('heading', { name: "This page's retained thought", exact: true }).waitFor();
  await page.getByText(thought.note, { exact: true }).waitFor();
  await page.getByText('Local drive: 0.72', { exact: true }).waitFor();
  await page.getByRole('button', { name: 'Open original message', exact: true }).waitFor();
  await page.getByText(/Background token admission envelope:/).waitFor();
  assert.equal(state.providerTokenBudget?.limit, 128);
  assert.equal(state.providerTokenBudget?.charged, 0);
  checks.push('local thought, strength, source and explicit background token boundary appear in Activity');
  if (screenshots) {
    await mkdir(screenshots, { recursive: true });
    await page.screenshot({ path: join(screenshots, 'retained-home-en.png') });
  }
  await page.getByRole('button', { name: 'Close dialog', exact: true }).click();

  // Work has a different local history and cannot display Home's retained note.
  await page.getByRole('button', { name: 'Create a page', exact: true }).click();
  await page.getByRole('textbox', { name: 'Page name', exact: true }).fill('Independent design work');
  await page.getByRole('textbox', { name: 'Where should we begin?', exact: true })
    .fill('Evaluate a separate work item without borrowing Home private context.');
  await page.getByRole('button', { name: 'Create page', exact: true }).click();
  await page.getByRole('heading', { name: 'Independent design work', exact: true }).waitFor();
  const workId = new URL(page.url()).pathname.split('/').pop();
  await page.getByRole('textbox', { name: /^Message / }).fill('Unsent Work draft, keep me here.');
  await page.getByRole('button', { name: 'Activity', exact: true }).click();
  await page.getByText('No model-retained thought is recorded for this page.', { exact: true }).waitFor();
  assert.equal(await page.getByText(thought.note, { exact: true }).count(), 0);
  await page.getByRole('button', { name: 'Close dialog', exact: true }).click();
  checks.push('other local Work cannot see Home thought');
  await page.locator('.sidebar .main-nav a').click();

  // The original message and IDs remain literal when viewing in Chinese.
  await page.getByRole('button', { name: 'Settings', exact: true }).click();
  await page.locator('#interface-language').selectOption('zh-CN');
  await page.getByRole('button', { name: '关闭对话框', exact: true }).click();
  await page.getByRole('button', { name: '活动', exact: true }).click();
  await page.getByRole('heading', { name: '此页面保留的思考', exact: true }).waitFor();
  await page.getByText(/后台 token 准入额度/).waitFor();
  await page.getByText(thought.note, { exact: true }).waitFor();
  await page.getByRole('button', { name: '打开原始消息', exact: true }).click();
  await wait(async () => decodeURIComponent(new URL(page.url()).hash) ===
    '#message-' + thought.sourceMessageId, 'source link anchors exact Home message');
  await page.getByText(homePrompt, { exact: true }).waitFor();
  checks.push('Chinese Activity returns to exact original source without translation or lost navigation');

  // Both stored attention and its source survive a process restart.
  await stopServer(server);
  server = startServer();
  await wait(async () => (await fetch(base + '/api/health')).ok, 'restarted World/Kernel');
  await page.reload();
  await page.getByRole('button', { name: '活动', exact: true }).click();
  await page.getByText(thought.note, { exact: true }).waitFor();
  await page.getByRole('button', { name: '打开原始消息', exact: true }).waitFor();
  await page.getByRole('button', { name: '关闭对话框', exact: true }).click();
  checks.push('attention and original message persist across restart');

  await page.goto(base + '/page/' + encodeURIComponent(workId));
  await page.locator('.composer textarea').waitFor();
  assert.equal(await page.locator('.composer textarea').inputValue(),
    'Unsent Work draft, keep me here.');
  checks.push('Work draft survives source navigation, language change and restart');

  // A second original model tool action releases the note, not a synthetic UI toggle.
  await page.locator('.sidebar .main-nav a').click();
  await page.locator('.composer textarea').fill('RELEASE_LOCAL_THOUGHT: that observation is complete.');
  await page.getByRole('button', { name: '发送消息', exact: true }).click();
  await wait(async () => (await (await fetch(base + '/api/pulse')).json())
    .retainedAttention.some(item => item.pageId === 'home' && item.note === '' && item.drive === 0
      && item.sourceEventId !== thought.sourceEventId),
  'release tool and original World turn settled');
  await page.getByRole('button', { name: '活动', exact: true }).click();
  await page.getByText('模型已释放先前保留的思考。', { exact: true }).waitFor();
  checks.push('successful native model release clears only the retained local thought');
  await page.getByRole('button', { name: '关闭对话框', exact: true }).click();

  // Two *real* local Works and their original causal relationship. A scripted
  // HTTP model generates text, but the World/Kernel performs the propagation.
  // Verify the rendered Activity opens each actual message, not merely its
  // page, and the user's draft still belongs to the receiving Work.
  const createLocal = async (title, instruction) => {
    const response = await fetch(base + '/api/pages', {
      method: 'POST', headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ title, instruction }),
    });
    assert.equal(response.status, 201, await response.text());
    return (await response.json()).page.id;
  };
  const localSource = await createLocal('Actual source Work', 'Keep original evidence in this Work.');
  const localReceiver = await createLocal('Actual receiving Work', 'Continue in this distinct local Work.');
  for (const id of [localSource, localReceiver]) {
    await wait(async () => (await (await fetch(base + '/api/pages/' + id)).json())
      .messages.some(message => message.role === 'assistant'),
    'original local Work model turn settles: ' + id);
  }
  const connection = await fetch(base + '/api/pulse/connections', {
    method: 'POST', headers: { 'content-type': 'application/json' },
    body: JSON.stringify({
      sourcePageId: localSource, targetPageId: localReceiver,
      kind: 'excitatory', weight: 0.9,
    }),
  });
  assert.equal(connection.status, 201, await connection.text());
  const newInput = await fetch(base + '/api/pages/' + localSource + '/messages', {
    method: 'POST', headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ content: 'A new source finding to propagate.' }),
  });
  assert.equal(newInput.status, 202, await newInput.text());
  let propagated;
  await wait(async () => {
    const current = (await (await fetch(base + '/api/pulse')).json()).recentActivations;
    propagated = current.find(item => item.pageId === localReceiver && item.sourcePageId === localSource
      && item.status === 'settled' && item.messageId === item.eventId
      && item.sourceMessageId === item.sourceEventId);
    return Boolean(propagated);
  }, 'native causal result with both original message anchors', 20000);
  assert.notEqual(propagated.eventId, propagated.sourceEventId);

  await page.goto(base + '/page/' + encodeURIComponent(localReceiver));
  await page.getByRole('heading', { name: 'Actual receiving Work', exact: true }).waitFor();
  await page.locator('.composer textarea').fill('Still composing after exploring causal sources.');
  await page.getByRole('button', { name: '活动', exact: true }).click();
  await page.getByRole('button', { name: 'Actual receiving Work · 打开局部消息', exact: true }).click();
  await wait(async () => decodeURIComponent(new URL(page.url()).hash) ===
    '#message-' + propagated.messageId, 'receiver's exact native propagation event');
  await page.locator('#message-' + propagated.messageId).waitFor();
  checks.push('local activation opens its exact receiving Work message');

  await page.getByRole('button', { name: '活动', exact: true }).click();
  await page.getByRole('button', { name: 'Actual source Work · 打开原始消息', exact: true }).click();
  await wait(async () => decodeURIComponent(new URL(page.url()).hash) ===
    '#message-' + propagated.sourceMessageId, 'original source result, not another Work');
  await page.locator('#message-' + propagated.sourceMessageId).waitFor();
  checks.push('native source event opens original Work with validated causal identity');

  await page.goto(base + '/page/' + encodeURIComponent(localReceiver));
  await page.locator('.composer textarea').waitFor();
  assert.equal(await page.locator('.composer textarea').inputValue(),
    'Still composing after exploring causal sources.');
  checks.push('causal source inspection preserves the receiving Work draft');

  assert.deepEqual(errors, []);
  console.log(JSON.stringify({ passed: checks.length, checks,
    provider: 'explicit synthetic HTTP transport; real local World/Kernel/SQLite' }));
} catch (error) {
  console.error(error.stack, serverLog);
  process.exitCode = 1;
} finally {
  if (browser) await browser.close();
  await stopServer(server);
  await rm(directory, { recursive: true, force: true });
}
