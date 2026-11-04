/** A failed turn says why, offers Send again, and status text follows the interface language.
 * Copyright 2026 Lightmaze Contributors. Licensed under Apache-2.0.
 * Model replies are a synthetic HTTP fixture; World, Kernel and SQLite are real.
 */
import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { createServer } from 'node:net';
import { createHash, randomBytes } from 'node:crypto';
import { mkdir } from 'node:fs/promises';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { createRequire } from 'node:module';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '../..');
const { chromium } = createRequire(import.meta.url)(process.env.PLAYWRIGHT_MODULE || 'playwright');
const screenshots = process.env.PER_PULSE_FAILURE_SCREENSHOTS;
if (screenshots) await mkdir(screenshots, { recursive: true });
const socket = createServer();
await new Promise(done => socket.listen(0, '127.0.0.1', done));
const port = socket.address().port;
await new Promise(done => socket.close(done));
const base = 'http://127.0.0.1:' + port;
let browser, serverLog = '';
const server = spawn(process.env.PER_PULSE_PYTHON || join(root, '.venv/bin/python'), [join(root, 'tests/browser/failure_server.py')], {
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
const checks = [];
try {
  await wait(async () => (await fetch(base + '/api/health')).ok, 'service ready');
  browser = await chromium.launch({ executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE || undefined,
    args: process.env.PLAYWRIGHT_CHROMIUM_ARGS ? JSON.parse(process.env.PLAYWRIGHT_CHROMIUM_ARGS) : undefined });
  const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.goto(base);
  const composer = page.getByRole('textbox', { name: /^Message / });
  await composer.fill('What should we do first?');
  await page.getByRole('button', { name: 'Send message', exact: true }).click();
  const activity = page.locator('.page-activity');
  await activity.getByText('Turn interrupted', { exact: true }).waitFor();
  await activity.getByText('The model service had an error. Send again in a moment.', { exact: true }).waitFor();
  // Nothing is in doubt after a model error, so no separate Stop is needed.
  assert.equal(await page.getByRole('button', { name: 'Stop this turn', exact: true }).count(), 0);
  checks.push('a failed turn says why it stopped');
  if (screenshots) await page.screenshot({ path: join(screenshots, 'failure-reason.png') });

  await activity.getByRole('button', { name: 'Send again', exact: true }).click();
  await page.locator('.message-assistant').getByText('Synthetic fixture: answered.').waitFor();
  await wait(async () => await activity.getByRole('button', { name: 'Send again', exact: true }).count() === 0, 'Send again hidden');
  checks.push('Send again gets the answer; the page is not stuck after one model error');

  const zh = await browser.newPage({ viewport: { width: 390, height: 844 }, locale: 'zh-CN' });
  zh.on('pageerror', error => errors.push(error.message));
  await fetch(base + '/__failure/next', { method: 'POST' });
  await zh.goto(base);
  await zh.getByRole('textbox').first().fill('再试一个问题。');
  await zh.keyboard.press('Enter');
  const zhActivity = zh.locator('.page-activity');
  await zhActivity.getByText('本轮已中断', { exact: true }).waitFor();
  await zhActivity.getByText('模型服务出错了。请稍后再发送一次。', { exact: true }).waitFor();
  await zhActivity.getByRole('button', { name: '再发送一次', exact: true }).waitFor();
  const overflow = await zh.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  assert.ok(overflow <= 1, 'no horizontal scroll on a phone');
  if (screenshots) await zh.screenshot({ path: join(screenshots, 'failure-reason-zh.png') });
  checks.push('status text and the reason follow the Chinese interface on a phone');


  // The retry is a *new* native user message, but it must keep the original
  // Work link and attach each verified source file exactly once. A synthetic
  // provider failure drives this browser journey; the World/Kernel/SQLite and
  // material receipt are the real application services.
  const bootstrap = await (await page.request.get(base + '/api/bootstrap')).json();
  const createdResponse = await page.request.post(base + '/api/pages', {
    data: { title: 'Source Work', instruction: 'Keep this source material.', sourcePageId: 'home' },
  });
  assert.equal(createdResponse.status(), 201, await createdResponse.text());
  const sourceWorkId = (await createdResponse.json()).page.id;
  await wait(async () => {
    const response = await page.request.get(base + '/api/pages/' + sourceWorkId);
    return response.ok() && !['queued', 'working'].includes((await response.json()).page.status);
  }, 'source Work creation settled');

  const bytes = Buffer.from('Original notes for the next Work.\n', 'utf8');
  const uploadId = randomBytes(16).toString('hex');
  const digest = createHash('sha256').update(bytes).digest('hex');
  const upload = await page.request.post(base + '/api/pages/home/materials?name=source-notes.md', {
    data: bytes, headers: {
      'Content-Type': 'application/octet-stream',
      'X-Per-Pulse-World': bootstrap.workspaceId,
      'X-Per-Pulse-Upload-Id': uploadId,
      'X-Per-Pulse-File-SHA256': digest,
    },
  });
  assert.equal(upload.status(), 201, await upload.text());
  const content = 'Continue Source Work using the attached original notes.';
  await fetch(base + '/__failure/next', { method: 'POST' });
  const first = await page.request.post(base + '/api/pages/home/messages', {
    data: { content, references: [{ targetPageId: sourceWorkId, label: 'Source Work' }], materials: [uploadId] },
  });
  assert.equal(first.status(), 202, await first.text());
  await wait(async () => {
    const response = await page.request.get(base + '/api/pages/home');
    return response.ok() && (await response.json()).page.status === 'error';
  }, 'linked material turn failed');
  await page.reload();
  await page.locator('.page-activity').getByRole('button', { name: 'Send again', exact: true }).waitFor();
  const [retryRequest] = await Promise.all([
    page.waitForRequest(request => request.method() === 'POST' && request.url() === base + '/api/pages/home/messages'),
    page.locator('.page-activity').getByRole('button', { name: 'Send again', exact: true }).click(),
  ]);
  assert.deepEqual(retryRequest.postDataJSON(), {
    content, references: [{ targetPageId: sourceWorkId, label: 'Source Work' }], materials: [uploadId],
  }, 'the browser retries source-verified input, not flattened original text');
  await wait(async () => {
    const response = await page.request.get(base + '/api/pages/home');
    if (!response.ok()) return false;
    const view = await response.json();
    return view.messages.filter(message => message.role === 'user' && message.content.startsWith(content)).length >= 2
      && !['queued', 'working'].includes(view.page.status);
  }, 'linked material retry finished');
  const finalView = await (await page.request.get(base + '/api/pages/home')).json();
  const attempts = finalView.messages.filter(message => message.role === 'user' && message.content.startsWith(content));
  assert.equal(attempts.length, 2);
  const repeated = attempts[1];
  assert.equal((repeated.content.match(/\[Attached workspace materials\]/g) || []).length, 1,
    'retry must not duplicate the native material footer');
  assert.ok(repeated.materialSuffixLength > 0, 'native material envelope remains verified');
  assert.deepEqual(repeated.references.map(ref => ({ targetPageId: ref.targetPageId, label: ref.label })),
    [{ targetPageId: sourceWorkId, label: 'Source Work' }]);
  assert.ok(repeated.materials.some(material => material.id === uploadId));
  checks.push('failed Work source link and native file survive Send again exactly once');

  assert.deepEqual(errors, []);
  console.log(JSON.stringify({ passed: checks.length, checks, provider: 'explicit synthetic HTTP fixture' }));
} finally {
  await browser?.close();
  server.kill('SIGTERM');
}
