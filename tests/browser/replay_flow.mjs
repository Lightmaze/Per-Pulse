/** A readable native replay and subsequent ordinary practice, with scripted model choices.
 * Copyright 2026 Lightmaze Contributors. Licensed under Apache-2.0.
 */
import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { mkdtemp, mkdir, readFile, rm, writeFile } from 'node:fs/promises';
import { createServer } from 'node:net';
import { tmpdir } from 'node:os';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { createRequire } from 'node:module';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '../..');
const require = createRequire(import.meta.url);
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const directory = await mkdtemp(join(tmpdir(), 'per-pulse-replay-'));
const screenshots = process.env.PER_PULSE_REPLAY_SCREENSHOTS;
const focused = process.env.PER_PULSE_FOCUSED_REPLAY === '1';
const focus = 'Could a visitor inspect one example before registration?';
if (screenshots) await mkdir(screenshots, { recursive: true });
const socket = createServer();
await new Promise(resolve => socket.listen(0, '127.0.0.1', resolve));
const port = socket.address().port;
await new Promise(resolve => socket.close(resolve));
const base = 'http://127.0.0.1:' + port;
const excerpt = 'A tentative alternative: let a visitor inspect one example before registration.';
const meaning = 'Saved from a tentative interpretation of earlier material. Its origin stays attached when shared.';
const checks = [], errors = [];
let serverLog = '', browser, page;
const start = (offline = false) => {
  const child = spawn(process.env.PER_PULSE_PYTHON || join(root, '.venv/bin/python'), [
    join(root, 'tests/browser/replay_server.py'), '--data-dir', directory, '--port', String(port),
    ...(offline ? ['--offline'] : []),
    ...(focused ? ['--focused'] : []),
  ], { cwd: root, stdio: ['ignore', 'pipe', 'pipe'] });
  child.stdout.on('data', data => { serverLog += data; });
  child.stderr.on('data', data => { serverLog += data; });
  return child;
};
const stop = async child => {
  if (child.exitCode !== null || child.signalCode !== null) return;
  const closed = new Promise(resolve => child.once('exit', resolve));
  const force = setTimeout(() => child.kill('SIGKILL'), 10000);
  child.kill('SIGTERM'); await closed; clearTimeout(force);
};
const wait = async (fn, label) => {
  const end = Date.now() + 20000;
  while (Date.now() < end) {
    try { if (await fn()) return; } catch {}
    await new Promise(resolve => setTimeout(resolve, 80));
  }
  throw new Error('Timed out: ' + label + '\n' + serverLog);
};
const data = path => fetch(base + path).then(r => { assert.ok(r.ok); return r.json(); });
const view = id => data('/api/pages/' + id);
const fixture = action => fetch(base + '/__replay/' + action, { method: 'POST' }).then(r => assert.ok(r.ok));
let server = start();
try {
  await wait(async () => (await fetch(base + '/api/health')).ok, 'service ready');
  browser = await chromium.launch({ executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE || undefined,
    args: process.env.PLAYWRIGHT_CHROMIUM_ARGS ? JSON.parse(process.env.PLAYWRIGHT_CHROMIUM_ARGS) : undefined });
  const context = await browser.newContext({ viewport: { width: 1280, height: 900 }, acceptDownloads: true,
    permissions: ['clipboard-read', 'clipboard-write'] });
  page = await context.newPage();
  page.on('pageerror', e => errors.push(e.message));
  await page.goto(base);
  const close = () => page.getByRole('button', { name: /^(Close dialog|关闭对话框)$/ }).click();
  const create = async title => {
    await page.getByRole('button', { name: 'Create a page', exact: true }).click();
    await page.getByRole('textbox', { name: 'Page name', exact: true }).fill(title);
    await page.getByRole('textbox', { name: 'Where should we begin?', exact: true }).fill('Explore a first design. [Explicit synthetic fixture]');
    await page.getByRole('button', { name: 'Create page', exact: true }).click();
    await page.getByRole('heading', { name: title, exact: true }).waitFor();
    const id = new URL(page.url()).pathname.split('/').pop();
    await wait(async () => (await view(id)).messages.some(m => m.role === 'assistant'), 'native first reply');
    return id;
  };
  const send = async content => {
    await page.getByRole('textbox', { name: /^Message / }).fill(content);
    await page.getByRole('button', { name: 'Send message', exact: true }).click();
  };
  const experience = async () => {
    await page.getByRole('button', { name: 'Settings', exact: true }).click();
    await page.getByRole('button', { name: 'View this page’s experience', exact: true }).click();
    await page.getByRole('dialog', { name: 'Lessons and examples', exact: true }).waitFor();
  };
  const origin = await create('Reconsider a design');
  const original = (await view(origin)).messages.find(m => m.role === 'assistant');
  await send('RECONSIDER_SAVED_DESIGN');
  await page.getByText('Reconsidering saved material', { exact: true }).waitFor();
  await page.getByText(focused
    ? 'This replay uses the declared focus and selected messages. Other conversation and experience advice return next turn.'
    : 'Reading selected earlier messages. File and command actions are unavailable until this turn ends.', { exact: true }).waitFor();
  assert.equal((await view(origin)).execution.replay.sourceCount, 1);
  if (focused) assert.equal((await view(origin)).execution.replay.focus, focus);
  const draft = 'Keep this unsent thought while I inspect the original.';
  await page.getByRole('textbox', { name: 'Message Reconsider a design', exact: true }).fill(draft);
  if (screenshots) await page.screenshot({ path: join(screenshots, 'replay-working.png') });
  await fixture('release');
  await page.getByText(excerpt, { exact: true }).waitFor();
  await wait(async () => (await view(origin)).execution.replay === null, 'replay ended');
  const replayed = (await view(origin)).messages.at(-1);
  const article = page.locator('[data-message-id="' + replayed.id + '"]');
  await article.getByText('Reconsidered saved material', { exact: true }).waitFor();
  assert.equal(await article.locator('.message-content').textContent(), excerpt);
  await article.getByRole('button', { name: 'Copy original message', exact: true }).click();
  await wait(async () => await page.evaluate(() => navigator.clipboard.readText()) === replayed.content, 'original copied with provenance prefix');
  checks.push('the real entry changes the visible activity; final interpretation stays labelled while raw copying preserves its full source prefix');

  await article.getByText('Original material', { exact: true }).click();
  if (focused) await article.getByText('Declared focus: ' + focus, { exact: true }).waitFor();
  await article.getByRole('link', { name: 'Original reply 1', exact: true }).click();
  await wait(() => page.url().endsWith('#message-' + original.id), 'original source anchor');
  assert.equal(await page.getByRole('textbox', { name: 'Message Reconsider a design', exact: true }).inputValue(), draft);
  await page.getByRole('button', { name: 'Back to conversation', exact: true }).click();
  await wait(() => page.url().endsWith('#message-' + replayed.id), 'return to the interpretation');
  assert.equal(await page.getByRole('textbox', { name: 'Message Reconsider a design', exact: true }).inputValue(), draft);
  if (screenshots) await page.screenshot({ path: join(screenshots, 'replay-sources.png') });
  checks.push('verified native source opens and returns to the exact interpretation without losing the Work draft');

  await article.getByRole('button', { name: 'Save example', exact: true }).click();
  const editor = page.getByRole('dialog', { name: 'Save example', exact: true });
  await editor.getByText(meaning, { exact: true }).waitFor();
  await editor.getByRole('textbox', { name: 'Example name', exact: true }).fill('A possibility to test');
  await editor.getByRole('textbox', { name: 'Original passage', exact: true }).fill(excerpt);
  await editor.getByRole('button', { name: 'Save example', exact: true }).click();
  await page.getByRole('heading', { name: 'A possibility to test', exact: true }).waitFor();
  await page.getByText(meaning, { exact: true }).waitFor();
  await page.getByRole('button', { name: 'Use in other pages', exact: true }).click();
  await page.getByText('In use · v2', { exact: true }).waitFor();
  await close();
  const receiver = await create('Try a possibility');
  await experience();
  await page.getByText(meaning, { exact: true }).waitFor();
  assert.equal(await page.locator('.saved-example pre').textContent(), excerpt);
  const shared = (await data('/api/pages/' + receiver + '/posture')).experiences[0];
  assert.equal(shared.sourceRegime, 'replay');
  assert.equal('source' in shared, false);
  await close();
  checks.push('an excerpt without its text prefix still retains the tentative origin in another Work without copying the private selected sources');

  await page.goto(base + '/page/' + origin + '#message-' + replayed.id);
  await article.getByRole('button', { name: 'Bring to Home', exact: true }).click();
  const bring = page.getByRole('dialog', { name: 'Bring this back to Home.', exact: true });
  await bring.getByRole('textbox', { name: 'What would you like to bring back?', exact: true }).fill(excerpt);
  await bring.getByRole('button', { name: 'Bring to Home', exact: true }).click();
  await page.getByText('Selected interpretation', { exact: true }).waitFor();
  const home = (await view('home')).messages.find(m => m.sourceMessageId === replayed.id);
  assert.ok(home.content.startsWith('[Selected from a replay interpretation; not a new external observation.]'));
  assert.equal(home.replay.selected, true);
  checks.push('bringing only the selected interpretation Home keeps its origin in both the browser and the actual model input');

  await page.goto(base + '/page/' + origin);
  await page.getByRole('textbox', { name: 'Message Reconsider a design', exact: true }).waitFor();
  await send('TRY_ORDINARY_ACTION');
  await page.getByRole('button', { name: 'Allow once', exact: true }).click();
  await page.getByText('The ordinary practice file was written after approval.', { exact: true }).waitFor();
  assert.equal((await view(origin)).execution.replay, null);
  assert.equal(await readFile(join(directory, 'workspace', 'practice.txt'), 'utf8'), 'The next ordinary turn can work.\n');
  checks.push('the next ordinary turn regains file capability and writes only after its own actual approval');

  await fixture('hold');
  await send('RECONSIDER_SAVED_DESIGN');
  await page.getByText('Reconsidering saved material', { exact: true }).waitFor();
  await page.getByRole('button', { name: 'Stop this turn', exact: true }).click();
  await page.getByText('This turn was stopped', { exact: true }).waitFor();
  await fixture('release');
  await stop(server); server = start(true);
  await wait(async () => (await fetch(base + '/api/health')).ok, 'offline restart');
  await page.reload();
  await page.getByText('Reconsidered saved material', { exact: true }).waitFor();
  assert.equal((await view(origin)).messages.filter(m => m.role === 'assistant').length, 3);
  assert.equal((await view(origin)).execution.replay, null);
  checks.push('stopping ends the activity and restart preserves settled provenance without inventing a late reply');

  await page.setViewportSize({ width: 390, height: 844 });
  const languageTab = await context.newPage();
  await languageTab.goto(base);
  await languageTab.getByRole('button', { name: 'Settings', exact: true }).click();
  await languageTab.locator('#interface-language').selectOption('zh-CN');
  await article.getByText('对已存材料的重新思考', { exact: true }).waitFor();
  await article.getByText('原始材料', { exact: true }).click();
  await article.getByRole('link', { name: '原始回复 1', exact: true }).waitFor();
  if (focused) await article.getByText('本次聚焦问题：' + focus, { exact: true }).waitFor();
  await article.scrollIntoViewIfNeeded();
  assert.equal(await article.locator('.message-content').textContent(), excerpt);
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
  if (screenshots) await page.screenshot({ path: join(screenshots, 'replay-mobile-chinese.png') });
  await languageTab.close();
  checks.push('mobile Chinese controls keep the original English content and its source links intact');
  assert.deepEqual(errors, []);
  console.log(JSON.stringify({ checks, count: checks.length, focused, errors, modelEvidence: 'explicit_synthetic_transport_only' }, null, 2));
} catch (error) {
  if (screenshots) {
    const pageId = page ? new URL(page.url()).pathname.split('/').pop() || 'home' : 'home';
    const snapshot = await fetch(base + '/api/pages/' + encodeURIComponent(pageId), {
      signal: AbortSignal.timeout(2000),
    }).then(async response => ({ status: response.status, body: await response.json() }))
      .catch(problem => ({ error: String(problem) }));
    await Promise.allSettled([
      writeFile(join(screenshots, 'replay-server.log'), serverLog),
      writeFile(join(screenshots, 'replay-failure-view.json'), JSON.stringify(snapshot, null, 2)),
      ...(page ? [page.screenshot({ path: join(screenshots, 'replay-failure.png') })] : []),
    ]);
  }
  throw error;
} finally {
  await browser?.close(); await stop(server);
  await rm(directory, { recursive: true, force: true });
}
