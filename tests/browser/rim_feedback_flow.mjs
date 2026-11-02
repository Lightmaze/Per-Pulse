/** A source-bound user journey: native RIM choice, actual browser consent,
 * later Work choice, withdrawal, immutable historical source and return.
 * Explicit synthetic HTTP model transport, never a learning-quality result.
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
const directory = await mkdtemp(join(tmpdir(), 'per-pulse-rim-browser-'));
const screenshots = process.env.PER_PULSE_RIM_SCREENSHOTS;
const socket = createServer();
await new Promise(done => socket.listen(0, '127.0.0.1', done));
const port = socket.address().port;
await new Promise(done => socket.close(done));
const base = 'http://127.0.0.1:' + port;
let serverLog = '';
let server;
let browser;
const checks = [];
const errors = [];
const start = () => {
  const child = spawn(process.env.PER_PULSE_PYTHON || join(root, '.venv/bin/python'), [
    join(root, 'tests/browser/rim_feedback_server.py'),
    '--data-dir', directory, '--port', String(port),
  ], { cwd: root, stdio: ['ignore', 'pipe', 'pipe'] });
  child.stdout.on('data', chunk => { serverLog += chunk; });
  child.stderr.on('data', chunk => { serverLog += chunk; });
  return child;
};
const stop = async child => {
  if (!child || child.exitCode !== null || child.signalCode !== null) return;
  child.kill('SIGTERM');
  const force = setTimeout(() => child.kill('SIGKILL'), 10000);
  await new Promise(done => child.once('exit', done));
  clearTimeout(force);
};
const wait = async (fn, label, timeout = 35000) => {
  const until = Date.now() + timeout;
  while (Date.now() < until) {
    try { if (await fn()) return; } catch {}
    await new Promise(done => setTimeout(done, 90));
  }
  throw new Error('Timed out: ' + label + '\n' + serverLog.slice(-7000));
};
const get = async path => {
  const response = await fetch(base + path);
  if (!response.ok) throw new Error(path + ': HTTP ' + response.status + ' ' + await response.text());
  return response.json();
};
const choices = id => get('/api/pages/' + id + '/team/choices');
const feedback = (id, record) => get('/api/pages/' + id + '/team/choices/' + record + '/feedback');

server = start();
try {
  await wait(async () => (await fetch(base + '/api/health')).ok, 'seeded native host', 50000);
  const { works, provider } = await get('/__rim/fixture');
  assert.equal(provider, 'explicit synthetic HTTP transport');
  const source = (await choices(works.source)).choices;
  assert.equal(source.length, 1);
  assert.equal(source[0].recordId, works.record);
  assert.equal(source[0].outcome, 'adopted');
  assert.ok(source[0].resultMessageId);
  assert.equal((await feedback(works.source, works.record)).revision, 0);
  checks.push('original Work owns a completed native adaptive result and an immutable human rating');

  browser = await chromium.launch({
    executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE || undefined,
    args: process.env.PLAYWRIGHT_CHROMIUM_ARGS
      ? JSON.parse(process.env.PLAYWRIGHT_CHROMIUM_ARGS) : undefined,
  });
  const context = await browser.newContext({ viewport: { width: 1280, height: 900 } });
  const page = await context.newPage();
  page.on('pageerror', error => errors.push(error.message));
  const openDecisions = async () => {
    await page.getByRole('button', { name: 'How this Work chose collaborators', exact: true }).click();
    await page.locator('.collaborator-choice').first().waitFor();
  };
  const openFeedback = async () => {
    await page.getByRole('button', { name: 'Guidance from this result', exact: true }).first().click();
    await page.locator('.collaborator-feedback textarea').first().waitFor();
  };
  const editor = () => page.locator('.collaborator-feedback').first();
  const submit = async () => {
    await editor().getByRole('button', { name: 'Review and save guidance', exact: true }).click();
    await editor().getByRole('button', { name: 'Confirm', exact: true }).click();
  };
  const sendChoice = async id => {
    await page.goto(base + '/page/' + id);
    await page.locator('.composer textarea').fill('Select one collaborator with prior work outcomes');
    await page.getByRole('button', { name: 'Send message', exact: true }).click();
    await wait(async () => {
      const result = (await choices(id)).choices;
      return result.length === 1 && Boolean(result[0].resultMessageId);
    }, 'native choice delivered for ' + id);
    return (await choices(id)).choices[0];
  };

  await page.goto(base + '/page/' + works.source);
  await openDecisions();
  await openFeedback();
  await editor().getByText('No guidance saved. A result assessment does not grant sharing consent.').waitFor();
  await editor().locator('textarea').fill('Check recorded cache timing before proposing a fix.');
  await submit();
  await editor().getByText('Saved for this Work only.').waitFor();
  assert.equal((await feedback(works.source, works.record)).current.scope, 'work');
  checks.push('actual browser saves Work-local guidance without silent cross-Work consent');

  const before = await sendChoice(works.baseline);
  assert.deepEqual(before.evidence, []);
  checks.push('a different native Work excludes rated but unshared history');

  await page.goto(base + '/page/' + works.source);
  await openDecisions();
  await openFeedback();
  await editor().getByText('Saved for this Work only.').waitFor();
  await editor().locator('select').selectOption('collaborator');
  await editor().getByRole('button', { name: 'Review and save guidance', exact: true }).click();
  await editor().getByText('Share this note with the same collaborator in other Works?', { exact: false }).waitFor();
  assert.equal((await feedback(works.source, works.record)).revision, 1,
    'review step must not mutate native consent');
  await editor().getByRole('button', { name: 'Confirm', exact: true }).click();
  await editor().getByText('Saved for this collaborator across Works.').waitFor();
  const sharedNote = await feedback(works.source, works.record);
  assert.equal(sharedNote.revision, 2);
  assert.equal(sharedNote.current.scope, 'collaborator');
  assert.equal(sharedNote.current.withdrawn, false);
  checks.push('second confirmation grants exact collaborator scope through real native CAS');

  const later = await sendChoice(works.shared);
  assert.equal(later.evidence.length, 1);
  assert.equal(later.evidence[0].recordId, works.record);
  assert.equal(later.evidence[0].sourceWorkId, works.source);
  assert.ok(later.evidence[0].sourceMessageId);
  await openDecisions();
  await page.locator('.collaborator-choice details summary').click();
  await page.getByText('Previously rated: used', { exact: true }).waitFor();
  const sourceLink = page.locator('.collaborator-choice details a.session-link').first();
  await sourceLink.waitFor();
  assert.match(await sourceLink.innerText(), /Original collaborator outcome/);
  await page.locator('.composer textarea').fill('Leave my later Work draft in place.');
  await sourceLink.click();
  await page.getByRole('heading', { name: 'Original collaborator outcome', exact: true }).waitFor();
  checks.push('another completed Work visibly links native selection to rated source and returns there');

  // A source return must work with the same editor in Chinese, not merely
  // leave translated strings in the locale bundle.
  await page.getByRole('button', { name: 'Settings', exact: true }).click();
  await page.locator('#interface-language').selectOption('zh-CN');
  await page.getByRole('button', { name: '关闭对话框', exact: true }).click();
  await page.getByRole('button', { name: '查看这项工作的协作者选择', exact: true }).click();
  await page.getByRole('button', { name: '这份成果留下的协作经验', exact: true }).click();
  await editor().getByText('已允许同一协作者跨工作使用。', { exact: true }).waitFor();
  await page.getByRole('button', { name: '设置', exact: true }).click();
  await page.locator('#interface-language').selectOption('en');
  await page.getByRole('button', { name: 'Close dialog', exact: true }).click();
  await editor().getByText('Saved for this collaborator across Works.').waitFor();
  checks.push('same source-bound guidance survives Chinese and English user navigation');
  await editor().getByRole('button', { name: 'Withdraw guidance', exact: true }).click();
  await editor().getByText('Withdraw this guidance from future use?', { exact: false }).waitFor();
  await editor().getByRole('button', { name: 'Confirm', exact: true }).click();
  await editor().getByText('This guidance has been withdrawn for future use.').waitFor();
  const revoked = await feedback(works.source, works.record);
  assert.equal(revoked.revision, 3);
  assert.equal(revoked.current.withdrawn, true);
  checks.push('withdrawal is an operator-confirmed native revision');

  const future = await sendChoice(works.withdrawn);
  assert.deepEqual(future.evidence, []);
  assert.equal((await choices(works.shared)).choices[0].evidence[0].recordId, works.record);
  await page.goto(base + '/page/' + works.shared);
  assert.equal(await page.locator('.composer textarea').inputValue(), 'Leave my later Work draft in place.');
  await openDecisions();
  await page.locator('.collaborator-choice details summary').click();
  await page.getByText('Previously rated: used', { exact: true }).waitFor();
  checks.push('revoke excludes only future Work, preserving past choice evidence and the receiving draft');

  if (screenshots) {
    await mkdir(screenshots, { recursive: true });
    await page.screenshot({ path: join(screenshots, 'prior-choice-with-original-evidence.png') });
  }
  await stop(server);
  server = start();
  await wait(async () => (await fetch(base + '/api/health')).ok, 'native host restart');
  assert.equal((await feedback(works.source, works.record)).current.withdrawn, true);
  assert.equal((await choices(works.shared)).choices[0].evidence[0].recordId, works.record);
  assert.deepEqual((await choices(works.withdrawn)).choices[0].evidence, []);
  checks.push('single World SQLite restart retains withdrawal and prior historical choice');

  assert.deepEqual(errors, []);
  console.log(JSON.stringify({ passed: checks.length, checks, works,
    provider: 'explicit synthetic HTTP; actual World/Kernel/SQLite and rendered browser' }));
} catch (error) {
  console.error(error.stack, '\nSERVER:\n', serverLog.slice(-12000));
  process.exitCode = 1;
} finally {
  await browser?.close();
  await stop(server);
  await rm(directory, { recursive: true, force: true });
}
