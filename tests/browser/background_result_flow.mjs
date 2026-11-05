/** Read and retain a real spontaneous result from Home and Activity.
 * Explicit TEST_DOUBLE transport; native World, Kernel and browser persistence.
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
const { chromium } = createRequire(import.meta.url)(process.env.PLAYWRIGHT_MODULE || 'playwright');
const directory = await mkdtemp(join(tmpdir(), 'per-pulse-background-result-'));
const screenshots = process.env.PER_PULSE_ATTENTION_SCREENSHOTS;
if (screenshots) await mkdir(screenshots, { recursive: true });
const socket = createServer();
await new Promise(done => socket.listen(0, '127.0.0.1', done));
const port = socket.address().port;
await new Promise(done => socket.close(done));
const base = `http://127.0.0.1:${port}`;
let serverLog = '', browser, page, server;
const errors = [], checks = [];
const start = (disablePulse = false) => {
  const child = spawn(process.env.PER_PULSE_PYTHON || join(root, '.venv/bin/python'), [
    join(root, 'tests/browser/attention_server.py'), '--data-dir', directory,
    '--port', String(port), '--spontaneous', '--posture', ...(disablePulse ? ['--no-pulse'] : []),
  ], { cwd: root, stdio: ['ignore', 'pipe', 'pipe'] });
  child.stdout.on('data', value => { serverLog += value; });
  child.stderr.on('data', value => { serverLog += value; });
  return child;
};
const stop = async child => {
  if (!child || child.exitCode !== null || child.signalCode !== null) return;
  child.kill('SIGTERM');
  const force = setTimeout(() => child.kill('SIGKILL'), 10000);
  await new Promise(done => child.once('exit', done));
  clearTimeout(force);
};
const wait = async (probe, label, timeout = 20000) => {
  const until = Date.now() + timeout;
  while (Date.now() < until) {
    try { if (await probe()) return; } catch {}
    await new Promise(done => setTimeout(done, 75));
  }
  throw new Error(`Timed out: ${label}\n${serverLog}`);
};
const get = async path => (await (await fetch(base + path)).json());
const experiences = id => get('/api/pages/' + id + '/posture').then(value => value.experiences);
try {
  server = start();
  await wait(async () => (await fetch(base + '/api/health')).ok, 'native server');
  const response = await fetch(base + '/api/pages', { method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ title: 'Continuing observation', instruction: 'An explicitly synthetic bounded observation.' }) });
  assert.equal(response.status, 201);
  const work = (await response.json()).page.id;
  let activation;
  await wait(async () => {
    activation = (await get('/api/pulse')).recentActivations.find(item => item.pageId === work);
    return activation?.status === 'settled' && activation.resultMessageId;
  }, 'one settled spontaneous answer');
  assert.equal(activation.messageId, null);
  const originalMessages = (await get('/api/pages/' + work)).messages;
  assert.equal(originalMessages.filter(message => message.role === 'assistant').length, 2);
  const backgroundResult = originalMessages.find(message => message.id === activation.resultMessageId);
  assert.equal(backgroundResult.role, 'assistant');
  browser = await chromium.launch({ executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE || undefined,
    args: process.env.PLAYWRIGHT_CHROMIUM_ARGS ? JSON.parse(process.env.PLAYWRIGHT_CHROMIUM_ARGS) : undefined });
  page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
  page.on('pageerror', error => errors.push(error.message));
  await page.goto(base);
  const homeDraft = 'Keep this Home draft while reviewing the background answer.';
  await page.getByRole('textbox', { name: 'Message Home', exact: true }).fill(homeDraft);
  await page.locator('.home-continuation-open').waitFor();
  if (screenshots) await page.screenshot({ path: join(screenshots, 'background-result-home.png') });
  await page.locator('.home-continuation-source').click();
  await wait(() => new URL(page.url()).hash === '#message-' + activation.sourceEventId, 'exact original source');
  const workDraft = 'Continue from this exact observation.';
  await page.locator('.composer textarea').fill(workDraft);
  await page.locator('.return-to-thread').click();
  await page.locator('.home-continuation-open').waitFor();
  assert.equal(await page.locator('.composer textarea').inputValue(), homeDraft);
  await page.locator('.home-continuation-open').click();
  await wait(() => new URL(page.url()).hash === '#message-' + activation.resultMessageId, 'exact background answer');
  assert.equal(await page.locator('.composer textarea').inputValue(), workDraft);
  checks.push('Home opens the original source and separate background answer while keeping both drafts.');

  await page.locator('#message-' + activation.resultMessageId).getByRole('button', { name: 'Save example', exact: true }).click();
  const editor = page.getByRole('dialog', { name: 'Save example', exact: true });
  await editor.getByRole('textbox', { name: 'Example name', exact: true }).fill('An observed background result');
  assert.equal(await editor.getByRole('textbox', { name: 'Original passage', exact: true }).inputValue(), backgroundResult.content);
  assert.equal(await editor.locator('#example-followup').inputValue(), '');
  assert.equal(await editor.locator('#example-followup option').count(), 1);
  await editor.getByRole('button', { name: 'Save example', exact: true }).click();
  const exampleCard = page.locator('.lesson').filter({ has: page.getByRole('heading', { name: 'An observed background result', exact: true }) });
  await exampleCard.waitFor();
  assert.equal(await exampleCard.locator('pre').textContent(), backgroundResult.content);
  await exampleCard.getByText('Source experience', { exact: true }).click();
  await exampleCard.getByText('Internal continuation without new input text.', { exact: true }).waitFor();
  await exampleCard.getByText('No follow-up attached', { exact: true }).waitFor();
  const savedExample = (await experiences(work)).find(item => item.title === 'An observed background result');
  assert.deepEqual(savedExample.sourceAnchor, { resultMessageId: activation.resultMessageId });
  assert.equal(savedExample.record_type, 'constructed_fixture');
  assert.equal(savedExample.source.provider_evidence_class, 'TEST_DOUBLE');
  assert.equal(savedExample.source.feedback_evidence_class, 'not_attached');
  assert.equal(savedExample.source.situation_source.event_id, activation.eventId);
  assert.equal(savedExample.source.situation_source.turn_id, savedExample.source.original_turn_id);
  assert.equal(savedExample.source.situation_source.kind, 'spontaneous');
  assert.equal(savedExample.source.situation_source.source, 'self');
  assert.equal(savedExample.source.situation_source.input_text, null);
  assert.equal(savedExample.source.situation_source.context_pulse, 'reentry');
  assert.equal('outcome' in savedExample.encounter, false);
  assert.deepEqual(savedExample.feedback, []);
  assert.equal('situation' in (savedExample.encounterSources ?? {}), false);
  assert.deepEqual((await get('/api/pages/' + work)).messages, originalMessages);
  if (screenshots) await page.screenshot({ path: join(screenshots, 'background-example-desktop.png') });
  await page.setViewportSize({ width: 390, height: 844 });
  await exampleCard.getByText('Internal continuation without new input text.', { exact: true }).scrollIntoViewIfNeeded();
  assert.ok(await page.getByRole('dialog', { name: 'Lessons and examples', exact: true }).evaluate(el => el.scrollWidth <= el.clientWidth));
  if (screenshots) await page.screenshot({ path: join(screenshots, 'background-example-mobile-en.png') });
  await page.setViewportSize({ width: 1280, height: 900 });
  await page.getByRole('button', { name: 'Close dialog', exact: true }).click();
  assert.equal(await page.locator('.composer textarea').inputValue(), workDraft);
  checks.push('Save example retains the actual contentless Pulse result and native trigger without adding a user message or follow-up.');

  await page.getByRole('button', { name: 'Activity', exact: true }).click();
  await page.getByRole('button', { name: 'Continuing observation · Open result', exact: true }).click();
  await wait(() => new URL(page.url()).hash === '#message-' + activation.resultMessageId, 'Activity answer');
  await page.getByRole('button', { name: 'Settings', exact: true }).click();
  await page.locator('#interface-language').selectOption('zh-CN');
  await page.keyboard.press('Escape');
  await page.setViewportSize({ width: 390, height: 844 });
  await page.getByRole('button', { name: '活动', exact: true }).click();
  await page.getByRole('button', { name: 'Continuing observation · 打开结果', exact: true }).click();
  assert.equal(new URL(page.url()).hash, '#message-' + activation.resultMessageId);
  assert.equal(await page.locator('.composer textarea').inputValue(), workDraft);
  assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
  if (screenshots) await page.screenshot({ path: join(screenshots, 'background-result-mobile-zh.png') });
  await page.locator('.return-to-thread').click();
  await wait(() => new URL(page.url()).pathname === '/', 'return to Home');
  assert.equal(await page.locator('.composer textarea').inputValue(), homeDraft);
  assert.equal(await page.locator('.home-continuation-open').count(), 0);
  checks.push('Activity opens the same answer in English and Chinese; visiting it does not resurrect an older Home item.');
  await stop(server); server = start(true);
  await wait(async () => (await fetch(base + '/api/health')).ok, 'reopen same World');
  await page.goto(base + '/page/' + work + '#message-' + activation.resultMessageId);
  await page.locator('#message-' + activation.resultMessageId).waitFor();
  assert.equal(await page.locator('.composer textarea').inputValue(), workDraft);
  const reopened = await get('/api/pulse');
  assert.equal(reopened.enabled, false, 'restarted World must not admit more autonomous pulses');
  assert.equal(reopened.recentActivations[0].resultMessageId, activation.resultMessageId);
  assert.equal(reopened.budget.used, 1);
  assert.deepEqual((await get('/api/pages/' + work)).messages, originalMessages);
  checks.push('Restart with Pulse disabled preserves the exact result, draft and single prior admission without new autonomous turns.');

  if (!await page.getByRole('button', { name: '设置', exact: true }).isVisible()) {
    await page.getByRole('button', { name: '展开或收起导航', exact: true }).click();
  }
  await page.getByRole('button', { name: '设置', exact: true }).click();
  await page.getByRole('button', { name: '查看此页面的经验', exact: true }).click();
  await exampleCard.waitFor();
  assert.equal(await exampleCard.locator('pre').textContent(), backgroundResult.content);
  await exampleCard.getByText('原始经历', { exact: true }).click();
  await exampleCard.getByText('Internal continuation without new input text.', { exact: true }).waitFor();
  await exampleCard.getByText('未附后续反馈', { exact: true }).waitFor();
  const reopenedExample = (await experiences(work)).find(item => item.id === savedExample.id);
  assert.deepEqual(reopenedExample.source, savedExample.source);
  assert.deepEqual(reopenedExample.encounter, savedExample.encounter);
  assert.ok(await page.getByRole('dialog', { name: '工作中的经验', exact: true }).evaluate(el => el.scrollWidth <= el.clientWidth));
  if (screenshots) await page.screenshot({ path: join(screenshots, 'background-example-mobile-zh.png') });
  await exampleCard.getByRole('button', { name: '查看原始结果', exact: true }).click();
  await page.locator('#message-' + activation.resultMessageId).waitFor();
  assert.equal(new URL(page.url()).hash, '#message-' + activation.resultMessageId);
  assert.equal(await page.locator('.composer textarea').inputValue(), workDraft);
  assert.deepEqual((await get('/api/pages/' + work)).messages, originalMessages);
  checks.push('The saved spontaneous example and its exact source survive restart, Chinese phone inspection and return to the original result.');

  // Browser-only presentation fixture: each link resolves to a real native
  // assistant message from a separate Work; ONLY the extra Pulse observation
  // records are synthetic to exercise Home's fourth-result disclosure. This
  // does not claim five real background activations or provider learning.
  const otherResults = [];
  for (let index = 0; index < 5; index++) {
    const created = await fetch(base + '/api/pages', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ title: 'Result ' + index, instruction: 'Test a normal Work reply.' }),
    });
    assert.equal(created.status, 201);
    const pageId = (await created.json()).page.id;
    let resultMessageId;
    await wait(async () => {
      const messages = (await get('/api/pages/' + pageId)).messages;
      resultMessageId = messages.find(message => message.role === 'assistant' && message.content.includes('Synthetic fixture response.'))?.id;
      return Boolean(resultMessageId);
    }, 'native result for Work ' + index);
    otherResults.push({
      eventId: 'browser-only-local-result-' + index,
      centerId: pageId, pageId, messageId: null, resultMessageId,
      kind: 'spontaneous', status: 'settled', createdAt: '2026-11-03T15:00:00Z',
      sourceEventId: null, sourceMessageId: null,
      sourceCenterId: null, sourcePageId: null, deferredReason: null,
    });
  }
  await page.route('**/api/pulse/observation', async route => {
    const upstream = await route.fetch();
    const observation = await upstream.json();
    observation.pulse.recentActivations = [...otherResults, ...observation.pulse.recentActivations];
    await route.fulfill({ response: upstream, json: observation });
  });
  await page.goto(base);
  const disabledObservation = await get('/api/pulse/observation');
  assert.equal(disabledObservation.pulse.enabled, false);
  assert.equal(disabledObservation.pulse.recentActivations[0].resultMessageId, activation.resultMessageId);
  const localResults = page.locator('#home-local-results .home-continuation-open');
  await wait(async () => await localResults.count() === 3, 'quiet preview of three local results');
  const reveal = page.getByRole('button', { name: '查看当前可见的 5 项局部结果' });
  await reveal.click();
  assert.equal(await page.locator('button[aria-controls="home-local-results"]').getAttribute('aria-expanded'), 'true');
  assert.equal(await localResults.count(), 5);
  await localResults.nth(4).click();
  await wait(() => new URL(page.url()).hash === '#message-' + otherResults[4].resultMessageId,
    'open fifth verified native message from expanded Home');
  await page.locator('#message-' + otherResults[4].resultMessageId).waitFor();
  await page.locator('.return-to-thread').click();
  await wait(() => new URL(page.url()).pathname === '/', 'return from expanded result to Home');
  assert.equal(await page.locator('.composer textarea').inputValue(), homeDraft);
  await wait(async () => await localResults.count() === 3, 'quiet preview after reading fifth result');
  await page.getByRole('button', { name: '查看当前可见的 4 项局部结果' }).click();
  assert.equal(await localResults.count(), 4, 'reading one result does not hide the other four');
  assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
  checks.push('With autonomous Pulse actually disabled, Chinese mobile Home still opens an earlier native result and expands five source-verified Work messages; drafts and receipts persist.');
  assert.deepEqual(errors, []);
  console.log(JSON.stringify({ checks, errors, viewports: ['1280x900', '390x844'], modelEvidence: 'explicit_synthetic_transport_only' }, null, 2));
} catch (error) {
  if (screenshots && page) await page.screenshot({ path: join(screenshots, 'background-result-failure.png') }).catch(() => {});
  throw error;
} finally {
  await browser?.close(); await stop(server); await rm(directory, { recursive: true, force: true });
}
