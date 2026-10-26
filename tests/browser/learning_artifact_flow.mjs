/** One rendered-browser user journey on the actual World/Kernel/SQLite/Harness.
 * The HTTP model is a declared deterministic fixture, not evidence of model learning.
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
const directory = await mkdtemp(join(tmpdir(), 'per-pulse-learning-artifact-'));
const screenshots = process.env.PER_PULSE_JOURNEY_SCREENSHOTS;
const socket = createServer();
await new Promise(resolve => socket.listen(0, '127.0.0.1', resolve));
const port = socket.address().port;
await new Promise(resolve => socket.close(resolve));
const base = 'http://127.0.0.1:' + port;
const checks = [];
const errors = [];
let serverLog = '';
let browser;
const startServer = () => {
  const child = spawn(process.env.PER_PULSE_PYTHON || join(root, '.venv/bin/python'), [
    join(root, 'tests/browser/learning_artifact_server.py'),
    '--data-dir', directory, '--port', String(port),
  ], { cwd: root, stdio: ['ignore', 'pipe', 'pipe'] });
  child.stderr.on('data', data => { serverLog += data; });
  child.stdout.on('data', data => { serverLog += data; });
  return child;
};
const stopServer = async child => {
  if (child.exitCode !== null || child.signalCode !== null) return;
  child.kill('SIGTERM');
  const force = setTimeout(() => child.kill('SIGKILL'), 10000);
  await new Promise(resolve => child.once('exit', resolve));
  clearTimeout(force);
};
const wait = async (fn, label, timeout = 20000) => {
  const end = Date.now() + timeout;
  while (Date.now() < end) {
    try { if (await fn()) return; } catch {}
    await new Promise(resolve => setTimeout(resolve, 80));
  }
  throw new Error('Timed out: ' + label + '\n' + serverLog);
};
const status = () => fetch(base + '/__journey/status').then(r => r.json());
let server = startServer();
try {
  await wait(async () => (await fetch(base + '/api/health')).ok, 'service ready');
  browser = await chromium.launch({
    executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE || undefined,
    args: process.env.PLAYWRIGHT_CHROMIUM_ARGS
      ? JSON.parse(process.env.PLAYWRIGHT_CHROMIUM_ARGS) : undefined,
  });
  const context = await browser.newContext({ viewport: { width: 1280, height: 900 } });
  const page = await context.newPage();
  page.on('pageerror', e => errors.push(e.message));
  await page.goto(base);
  await page.getByRole('textbox', { name: 'Message Home', exact: true }).waitFor();
  const create = async (title, instruction) => {
    await page.getByRole('button', { name: 'Create a page', exact: true }).click();
    await page.getByRole('textbox', { name: 'Page name', exact: true }).fill(title);
    await page.getByRole('textbox', { name: 'Where should we begin?', exact: true }).fill(instruction);
    await page.getByRole('button', { name: 'Create page', exact: true }).click();
    await page.getByRole('heading', { name: title, exact: true }).waitFor();
    return new URL(page.url()).pathname.split('/').pop();
  };
  const openLessons = async () => {
    await page.getByRole('button', { name: 'Settings', exact: true }).click();
    await page.getByRole('button', { name: 'View this page’s lessons', exact: true }).click();
    await page.getByRole('dialog', { name: 'Lessons from the work', exact: true }).waitFor();
  };
  const closeDialog = () => page.getByRole('button', { name: 'Close dialog', exact: true }).click();

  const originalId = await create('Original product brief', 'DESIGN_FIRST_VIEW');
  await page.getByText('The first screen is a twelve-field intake form. [Synthetic browser fixture]',
    { exact: true }).waitFor();
  await page.getByRole('textbox', { name: /^Message / }).fill(
    'The intake form hides the product. Let me try its central interaction before optional profile questions.');
  await page.getByRole('button', { name: 'Send message', exact: true }).click();
  await wait(async () => {
    const response = await fetch(base + '/api/pages/' + originalId + '/posture');
    return response.ok && (await response.json()).experiences.length === 1;
  }, 'ordinary correction admitted as a native sourced experience');
  await openLessons();
  const lesson = page.getByRole('article').filter({
    has: page.getByRole('heading', { name: 'First usable interaction', exact: true }),
  });
  await lesson.getByText('In use · v1', { exact: true }).waitFor();
  await lesson.getByText('Source experience', { exact: true }).click();
  assert.match(await lesson.innerText(), /twelve-field intake form/);
  checks.push('ordinary Work correction forms an inspectable sourced experience');
  await closeDialog();

  const beforeId = await create('Uninformed delivery', 'BUILD_PRACTICE_ARTIFACT');
  await page.getByText('Your approval is needed', { exact: true }).waitFor();
  assert.match(await page.locator('body').innerText(), /intake\.txt/);
  assert.equal((await status()).files.includes('intake.txt'), false);
  await page.getByRole('button', { name: 'Decline', exact: true }).click();
  await wait(async () => !(await status()).files.includes('intake.txt'),
    'baseline denied action leaves no file');
  checks.push('unshared experience cannot change another Work and denied action cannot write');

  await page.goto(base + '/page/' + originalId);
  await openLessons();
  await lesson.getByRole('button', { name: 'Use in other pages', exact: true }).click();
  await lesson.getByText('In use · v2', { exact: true }).waitFor();
  await closeDialog();
  const learnedId = await create('Informed delivery', 'BUILD_PRACTICE_ARTIFACT');
  await page.getByText('Your approval is needed', { exact: true }).waitFor();
  assert.match(await page.locator('body').innerText(), /practice\.txt/);
  assert.equal((await status()).files.includes('practice.txt'), false);
  const report = await status();
  assert.equal(report.tasks.length >= 2, true);
  assert.equal(report.tasks[0].hasLesson, false);
  assert.equal(report.tasks.at(-1).hasLesson, true);
  assert.equal(report.tasks.at(-1).leaksCorrection, false);
  assert.equal(report.tasks.at(-1).leaksOriginal, false);
  checks.push('scoped abstract learning changes next proposed file, not raw private source');

  // The same pending action and local composer must survive Chinese activity
  // inspection, then the user can still approve exactly the original action.
  const draft = 'Unsent delivery note: return to this exact Work position.';
  await page.locator('.composer textarea').fill(draft);
  await page.getByRole('button', { name: 'Settings', exact: true }).click();
  await page.locator('#interface-language').selectOption('zh-CN');
  await page.getByRole('button', { name: '关闭对话框', exact: true }).click();
  await page.getByRole('button', { name: '活动', exact: true }).click();
  await page.getByRole('dialog', { name: '活动', exact: true }).waitFor();
  await page.getByRole('button', { name: '关闭对话框', exact: true }).click();
  await page.getByRole('button', { name: '设置', exact: true }).click();
  await page.locator('#interface-language').selectOption('en');
  await closeDialog();
  assert.equal(await page.locator('.composer textarea').inputValue(), draft);
  await page.getByRole('button', { name: 'Allow once', exact: true }).click();
  await page.getByRole('button', { name: /practice\.txt/ }).waitFor();
  await page.getByRole('button', { name: /practice\.txt/ }).click();
  await page.getByText('A playable first interaction.', { exact: true }).waitFor();
  assert.equal((await status()).files.includes('practice.txt'), true);
  checks.push('bilingual Pulse visit preserves approval, real file and unsent Work draft');
  if (screenshots) {
    await mkdir(screenshots, { recursive: true });
    await page.screenshot({ path: join(screenshots, 'journey-approved-artifact.png') });
  }

  const result = page.locator('.message-assistant').filter({
    hasText: 'The file task finished. [Synthetic browser fixture]',
  }).last();
  await result.getByRole('button', { name: 'Bring to Home', exact: true }).click();
  const dialog = page.getByRole('dialog', { name: 'Bring this back to Home.', exact: true });
  await dialog.waitFor();
  await dialog.getByRole('button', { name: 'Bring to Home', exact: true }).click();
  await page.getByRole('heading', { name: 'A place to keep going.', exact: true }).waitFor();
  const homeResult = page.locator('.message-user').filter({
    hasText: 'The file task finished. [Synthetic browser fixture]',
  }).last();
  const origin = homeResult.locator('.source-line a.session-link');
  await origin.waitFor();
  const href = await origin.getAttribute('href');
  assert.match(href, /#message-/);
  await origin.click();
  await page.getByRole('heading', { name: 'Informed delivery', exact: true }).waitFor();
  assert.equal(new URL(page.url()).pathname.endsWith('/' + learnedId), true);
  assert.equal(await page.locator('.composer textarea').inputValue(), draft);
  checks.push('selected original Work result returns to Home with a precise source, then restores Work draft');

  await stopServer(server);
  server = startServer();
  await wait(async () => (await fetch(base + '/api/health')).ok, 'same World after process restart');
  await page.reload();
  await page.getByRole('heading', { name: 'Informed delivery', exact: true }).waitFor();
  assert.equal(await page.locator('.composer textarea').inputValue(), draft);
  const persistent = await (await fetch(base + '/api/pages/home')).json();
  assert.equal(persistent.messages.some(m => m.sourcePageId === learnedId), true);
  const persistedLesson = await (await fetch(base + '/api/pages/' + learnedId + '/posture')).json();
  assert.equal(persistedLesson.experiences.length, 1);
  checks.push('same Work, shared abstract lesson and attributed Home return persist after restart');
  assert.deepEqual(errors, []);
  console.log(JSON.stringify({
    passed: checks.length, checks, sourceWork: originalId, comparisonWork: beforeId,
    resultWork: learnedId,
    provider: 'explicit synthetic HTTP model; native World/Kernel/SQLite/Posture/Harness/files',
  }));
} catch (error) {
  console.error(error.stack, '\nSERVER:\n', serverLog);
  process.exitCode = 1;
} finally {
  await browser?.close();
  await stopServer(server);
  await rm(directory, { recursive: true, force: true });
}
