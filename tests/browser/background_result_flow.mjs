/** Read and retain a real spontaneous result from Home and Activity.
 * Explicit TEST_DOUBLE transport; native World, Kernel and browser persistence.
 * Copyright 2026 Lightmaze Contributors. Licensed under Apache-2.0.
 */
import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { mkdtemp, mkdir, readFile, rm } from 'node:fs/promises';
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
const installedWheel = process.env.PER_PULSE_INSTALLED_WHEEL === '1';
const start = (disablePulse = false, spontaneous = true, dataDir = directory, deliverables = false) => {
  const child = spawn(process.env.PER_PULSE_PYTHON || join(root, '.venv/bin/python'), [
    ...(installedWheel ? ['-I'] : []),
    join(root, 'tests/browser/attention_server.py'), '--data-dir', dataDir,
    '--port', String(port), ...(spontaneous ? ['--spontaneous'] : []), '--posture', ...(disablePulse ? ['--no-pulse'] : []), ...(deliverables ? ['--deliverables'] : []),
  ], { cwd: installedWheel ? directory : root, stdio: ['ignore', 'pipe', 'pipe'] });
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
  assert.equal((await fetch(base + '/api/pulse/weekly-usage', {
    method: 'PATCH', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ enabled: true, weeklyLimit: 100 }),
  })).status, 200);
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
  // A failed background read must not turn a known result into an empty Home,
  // or leave an unverified source link actionable. Refresh is read-only.
  await page.route('**/api/pulse/observation', route => route.fulfill({
    status: 503, contentType: 'application/json', body: '{"error":"explicit observation outage"}',
  }), { times: 1 });
  await page.waitForResponse(response => new URL(response.url()).pathname === '/api/pulse/observation'
    && response.status() === 503, { timeout: 18000 });
  await page.getByRole('heading', { name: 'Recent updates unavailable', exact: true }).waitFor();
  assert.equal(await page.locator('.home-continuation-open').count(), 0);
  assert.equal(await page.locator('.home-continuation-source').count(), 0);
  await page.getByText('Last known work (read-only)', { exact: true }).waitFor();
  await page.getByRole('button', { name: 'Refresh', exact: true }).click();
  await page.locator('.home-continuation-open').waitFor();
  assert.equal(await page.getByRole('heading', { name: 'Recent updates unavailable' }).count(), 0);
  checks.push('A failed native Home observation shows read-only last-known work and recovers with explicit refresh, without a model request.');

  // Presentation-only status fixture: a fresh World/Work list can say that a
  // page needs review even if the independent Pulse observation is down.
  // Opening the page does not approve an action or synthesize a Kernel effect.
  const attentionProjection = async route => {
    const upstream = await route.fetch();
    const bootstrap = await upstream.json();
    bootstrap.pages = bootstrap.pages.map(entry => entry.id === work
      ? { ...entry, status: 'needs_attention' } : entry);
    await route.fulfill({ response: upstream, json: bootstrap });
  };
  await page.route('**/api/bootstrap', attentionProjection);
  await page.route('**/api/pulse/observation', route => route.fulfill({
    status: 503, contentType: 'application/json', body: '{"error":"synthetic Pulse-only outage"}',
  }), { times: 1 });
  await page.goto(base);
  await page.getByText(
    'Background results could not be checked. Pages needing your decision below come from the current Work list and can still be opened.',
    { exact: true },
  ).waitFor();
  await page.getByRole('heading', { name: 'Pages needing attention', exact: true }).waitFor();
  assert.equal(await page.locator('#home-local-results .home-continuation-open').count(), 0,
    'unverified Pulse result links must stay hidden while only the Work list is fresh');
  assert.equal(await page.locator('.home-continuation-source').count(), 0,
    'unverified Pulse source links must also stay hidden');
  await page.getByRole('button', { name: 'Open Continuing observation', exact: true }).click();
  await wait(() => new URL(page.url()).pathname === '/page/' + work,
    'current Work opens during independent Pulse observation failure');
  await page.unroute('**/api/bootstrap', attentionProjection);
  await page.goto(base);
  await page.locator('.home-continuation-open').waitFor();
  await wait(async () => await page.getByRole('heading', { name: 'Pages needing attention' }).count() === 0,
    'native fresh Work list replaces the temporary review projection');
  checks.push('A Pulse-only outage keeps current Work review navigable without exposing unverified background results.');

  // The archive and quiet preview must not duplicate the same visible card.
  await page.getByRole('button', { name: 'Browse saved results', exact: true }).click();
  await page.getByText('No more saved results to show.', { exact: true }).waitFor();
  assert.equal(await page.locator('#home-local-results .home-continuation-open').count(), 1);
  assert.equal(await page.locator('ol[aria-label="Saved results"] .home-continuation-open').count(), 0);

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

  // A read receipt only removes the quiet card. Reopen the exact native answer
  // from Home even while the recent window contains just this activation.
  assert.equal((await get('/api/pulse')).recentActivations.length, 1);
  await page.getByRole('button', { name: '浏览已保存的结果', exact: true }).click();
  const savedResult = page.locator('ol[aria-label="已保存的结果"] .home-continuation-open');
  await savedResult.waitFor();
  assert.equal(await savedResult.count(), 1);
  assert.equal(await page.locator('#home-local-results .home-continuation-open').count(), 0);
  await savedResult.click();
  await wait(() => new URL(page.url()).hash === '#message-' + activation.resultMessageId, 'reopen read result from Home');
  await page.locator('#message-' + activation.resultMessageId).waitFor();
  assert.equal(await page.locator('.composer textarea').inputValue(), workDraft);
  assert.deepEqual((await get('/api/pages/' + work)).messages, originalMessages);
  checks.push('A read background answer remains explicitly browsable before the recent window fills, without a duplicate quiet card or new message.');

  await stop(server); server = start(true);
  await wait(async () => (await fetch(base + '/api/health')).ok, 'reopen same World');
  await page.goto(base);
  assert.equal(await page.locator('.composer textarea').inputValue(), homeDraft);
  await page.getByRole('button', { name: '浏览已保存的结果', exact: true }).click();
  await savedResult.waitFor();
  assert.equal(await savedResult.count(), 1);
  await savedResult.click();
  await wait(() => new URL(page.url()).hash === '#message-' + activation.resultMessageId, 'reopen saved result after restart');
  await page.locator('#message-' + activation.resultMessageId).waitFor();
  assert.equal(await page.locator('.composer textarea').inputValue(), workDraft);
  const reopened = await get('/api/pulse');
  assert.equal(reopened.enabled, false, 'restarted World must not admit more autonomous pulses');
  assert.equal(reopened.recentActivations[0].resultMessageId, activation.resultMessageId);
  assert.equal(reopened.budget.used, 1);
  assert.deepEqual((await get('/api/pages/' + work)).messages, originalMessages);
  checks.push('Restart with Pulse disabled permits Home to reopen the saved exact result and preserves both drafts and the single prior admission without new autonomous turns.');

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
  // Browser-only window fixture: pretend twenty newer activation records
  // pushed the genuine earlier answer out of the native recent-20 preview.
  // The older answer returned by /api/pulse/results remains an actual
  // source-verified Kernel result; no extra model turns are claimed here.
  await page.unroute('**/api/pulse/observation');
  await page.route('**/api/pulse/observation', async route => {
    const upstream = await route.fetch();
    const observation = await upstream.json();
    observation.pulse.recentActivations = Array.from({ length: 20 }, (_, index) => ({
      ...otherResults[0], eventId: 'synthetic-newer-window-' + index,
    }));
    await route.fulfill({ response: upstream, json: observation });
  });
  await page.goto(base);
  await page.getByRole('button', { name: '浏览已保存的结果', exact: true }).click();
  const olderResult = page.locator('ol[aria-label="已保存的结果"] .home-continuation-open');
  await olderResult.waitFor();
  assert.equal(await olderResult.count(), 1);
  assert.deepEqual((await get('/api/pages/' + work)).messages, originalMessages);
  await olderResult.click();
  await wait(() => new URL(page.url()).hash === '#message-' + activation.resultMessageId,
    'paged earlier result returns to its native message');
  assert.equal(await page.locator('.composer textarea').inputValue(), workDraft);
  checks.push('Chinese Home reopens a durable original answer after a synthetic later-activity window evicts it from the recent preview.');

  // Independently exercise real propagation with a fresh isolated test World.
  await page.close();
  await stop(server);
  const propagationDir = join(directory, 'propagation');
  server = start(false, false, propagationDir);
  await wait(async () => (await fetch(base + '/api/health')).ok, 'native propagation World');
  const post = async (path, body, status, method = 'POST') => {
    const response = await fetch(base + path, {
      method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
    });
    assert.equal(response.status, status);
    return response.json();
  };
  await post('/api/pulse/weekly-usage', { enabled: true, weeklyLimit: 100 }, 200, 'PATCH');
  const createWork = async (title, instruction = 'Begin this local Work.') => {
    const id = (await post('/api/pages', { title, instruction }, 201)).page.id;
    await wait(async () => (await get('/api/pages/' + id)).messages.some(
      message => message.role === 'assistant'), 'initial Work answer');
    return id;
  };
  const sender = await createWork('Propagation source');
  const receiver = await createWork('Receiving propagation');
  await post('/api/pulse/connections', { sourcePageId: sender, targetPageId: receiver, weight: 0.9 }, 201);
  await post('/api/pages/' + sender + '/messages', { content: 'A new observation.' }, 202);
  let propagated;
  await wait(async () => {
    propagated = (await get('/api/pulse')).recentActivations.find(item =>
      item.kind === 'propagation' && item.pageId === receiver);
    return propagated?.status === 'settled' && propagated.resultMessageId;
  }, 'settled native propagation answer');
  assert.equal(propagated.messageId, propagated.eventId);
  assert.notEqual(propagated.resultMessageId, propagated.messageId);
  const received = (await get('/api/pages/' + receiver)).messages;
  assert.equal(received.find(message => message.id === propagated.messageId).role, 'system');
  assert.equal(received.find(message => message.id === propagated.resultMessageId).role, 'assistant');
  page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
  page.on('pageerror', error => errors.push(error.message));
  await page.goto(base);
  const propagationDraft = 'Keep this draft while reading the propagated result.';
  await page.locator('.composer textarea').fill(propagationDraft);
  const receivingCard = page.locator('#home-local-results li').filter({ hasText: 'Receiving propagation' });
  await receivingCard.locator('.home-continuation-source').click();
  await wait(() => new URL(page.url()).hash === '#message-' + propagated.sourceMessageId, 'original propagation source');
  assert.equal(new URL(page.url()).pathname, '/page/' + sender);
  await page.locator('.return-to-thread').click();
  await receivingCard.locator('.home-continuation-open').click();
  await wait(() => new URL(page.url()).hash === '#message-' + propagated.resultMessageId, 'completed propagation answer');
  await page.locator('#message-' + propagated.resultMessageId).waitFor();
  const receiverDraft = 'Continue with this answer.';
  await page.locator('.composer textarea').fill(receiverDraft);
  await page.locator('.return-to-thread').click();
  assert.equal(await page.locator('.composer textarea').inputValue(), propagationDraft);
  await page.getByRole('button', { name: 'Browse saved results', exact: true }).click();
  const savedPropagation = page.locator('ol[aria-label="Saved results"] li').filter({ hasText: 'Receiving propagation' });
  await savedPropagation.locator('.home-continuation-open').click();
  await wait(() => new URL(page.url()).hash === '#message-' + propagated.resultMessageId, 'same saved propagation answer');
  assert.equal(await page.locator('.composer textarea').inputValue(), receiverDraft);
  assert.deepEqual((await get('/api/pages/' + receiver)).messages, received);
  await stop(server); server = start(true, false, propagationDir, true);
  await wait(async () => (await fetch(base + '/api/health')).ok, 'restart propagated World');
  await page.goto(base);
  assert.equal(await page.locator('.composer textarea').inputValue(), propagationDraft);
  await page.getByRole('button', { name: 'Browse saved results', exact: true }).click();
  await savedPropagation.locator('.home-continuation-open').click();
  await wait(() => new URL(page.url()).hash === '#message-' + propagated.resultMessageId, 'saved propagation after restart');
  assert.equal(await page.locator('.composer textarea').inputValue(), receiverDraft);
  assert.deepEqual((await get('/api/pages/' + receiver)).messages, received);
  assert.equal((await get('/api/pulse')).budget.used, 1);
  checks.push('Native propagation opens the completed answer, not the incoming system message; source, drafts, saved answer and budget survive restart.');

  // Adopt the saved answer into Home, then use only the selected passage
  // in a new independent Work that produces an actual downloadable file.
  const originalAnswer = page.locator('#message-' + propagated.resultMessageId);
  await originalAnswer.getByRole('button', { name: 'Bring to Home', exact: true }).click();
  await page.getByRole('dialog').getByRole('button', { name: 'Bring to Home', exact: true }).click();
  await wait(() => new URL(page.url()).pathname === '/', 'adopt saved Pulse answer');
  let adoption;
  await wait(async () => {
    adoption = (await get('/api/pages/home')).messages.find(row =>
      row.sourcePageId === receiver && row.sourceMessageId === propagated.resultMessageId);
    return adoption;
  }, 'persistent Home adoption of completed answer');
  const adoptedArticle = page.locator('#message-' + adoption.id);
  const chosen = 'The local turn completed.';
  assert.ok(adoption.content.includes(chosen));
  const newPage = adoptedArticle.getByRole('button', { name: 'New page', exact: true });
  await newPage.click();
  assert.ok((await page.getByRole('dialog').locator('#new-page-source').inputValue()).includes('Synthetic fixture response.'));
  await page.getByRole('dialog').getByRole('button', { name: 'Cancel', exact: true }).click();
  await adoptedArticle.locator('.message-content p').first().evaluate((node, chosen) => {
    const text = node.firstChild;
    const start = text.textContent.indexOf(chosen);
    if (start < 0) throw new Error('Selected passage missing from rendered Home adoption');
    const range = document.createRange();
    range.setStart(text, start); range.setEnd(text, start + chosen.length);
    const selection = window.getSelection();
    selection.removeAllRanges(); selection.addRange(range);
  }, chosen);
  assert.equal(await page.evaluate(() => window.getSelection().toString()), chosen);
  // Use a trusted browser click, including the mouse-down focus transition.
  // A synthetic element.click() would miss selection loss in the real UI.
  await newPage.click();
  const create = page.getByRole('dialog');
  assert.equal(await create.locator('#new-page-source').inputValue(), chosen);
  await create.getByRole('textbox', { name: 'Page name', exact: true }).fill('Adopted Pulse observation');
  await create.getByRole('textbox', { name: 'Your request', exact: true })
    .fill('Write the adopted observation to a Markdown file and report it Home.');
  await create.getByRole('button', { name: 'Create page', exact: true }).click();
  await page.getByRole('heading', { name: 'Adopted Pulse observation', exact: true }).waitFor();
  const later = decodeURIComponent(new URL(page.url()).pathname.split('/').pop());
  let delivery;
  await wait(async () => {
    const snapshot = await get('/api/pages/' + later);
    const source = snapshot.messages.find(row => row.pageSource);
    if (!source || snapshot.page.status !== 'idle') return false;
    assert.equal(source.pageSource.excerpt, chosen);
    assert.equal(source.sourcePageId, 'home');
    assert.equal(source.sourceMessageId, adoption.id);
    delivery = (await get('/api/pages/home')).messages.find(row =>
      row.sourcePageId === later && row.reportedArtifact?.path === 'pulse-observation.md');
    return delivery;
  }, 'native derived Work file reported Home', 30000);
  await page.locator('.main-nav').getByRole('link', { name: 'Home', exact: true }).click();
  const delivered = page.locator('#message-' + delivery.id);
  await delivered.getByRole('button', { name: 'Open verified file' }).click();
  await delivered.getByText(chosen, { exact: true }).waitFor();
  const downloadEvent = page.waitForEvent('download');
  await delivered.getByRole('button', { name: 'Download file', exact: true }).click();
  const bytes = await readFile(await (await downloadEvent).path());
  assert.equal(bytes.toString('utf8'), '# Adopted observation\n\n' + chosen + '\n');
  await stop(server); server = start(true, false, propagationDir, true);
  await wait(async () => (await fetch(base + '/api/health')).ok, 'restart derived Work');
  await page.goto(base);
  await delivered.getByRole('button', { name: 'Open verified file' }).click();
  const again = page.waitForEvent('download');
  await delivered.getByRole('button', { name: 'Download file', exact: true }).click();
  assert.deepEqual(await readFile(await (await again).path()), bytes);
  await adoptedArticle.locator('.source-line a.session-link').click();
  await wait(() => new URL(page.url()).hash === '#message-' + propagated.resultMessageId, 'original Pulse answer remains reachable');
  assert.equal(await page.locator('.composer textarea').inputValue(), receiverDraft);
  assert.equal((await get('/api/pulse')).budget.used, 1);
  checks.push('A saved Pulse answer is adopted Home, only the chosen passage enters a later Work, and its actual delivered file and original source survive a cold restart.');

  // No selected source or prior Work ID is handed to this ordinary later
  // Work. The explicit fixture chooses the native find tool and derives its
  // file and report source from the actual returned historical answer.
  const independent = (await post('/api/pages', {
    title: 'Independent background brief',
    instruction: 'Prepare a Markdown brief from the earlier completed background observation.',
  }, 201)).page.id;
  let independentDelivery;
  try {
    await wait(async () => {
      independentDelivery = (await get('/api/pages/home')).messages.find(row =>
        row.sourcePageId === independent && row.reportedArtifact?.path === 'background-brief.md');
      return independentDelivery;
    }, 'independent Work discovers and uses completed Pulse answer', 30000);
  } catch (error) {
    // This isolated synthetic World is safe to print. Keep the failed Work's
    // native status and tool/message evidence instead of only a Home timeout.
    console.error('Independent Work snapshot:', JSON.stringify(
      await get('/api/pages/' + independent).catch(() => null)));
    throw error;
  }
  assert.ok(independentDelivery.content.includes('Verified earlier Work excerpt'));
  assert.ok(independentDelivery.content.includes('Receiving propagation'));
  assert.ok(independentDelivery.content.includes(received.find(row => row.id === propagated.resultMessageId).content));
  await page.locator('.main-nav').getByRole('link', { name: 'Home', exact: true }).click();
  const independentCard = page.locator('#message-' + independentDelivery.id);
  await independentCard.getByRole('button', { name: 'Open verified file' }).click();
  const briefDownload = page.waitForEvent('download');
  await independentCard.getByRole('button', { name: 'Download file', exact: true }).click();
  const briefBytes = await readFile(await (await briefDownload).path());
  assert.equal(briefBytes.toString('utf8'), '# Background brief\n\n' +
    received.find(row => row.id === propagated.resultMessageId).content + '\n');
  await stop(server); server = start(true, false, propagationDir, true);
  await wait(async () => (await fetch(base + '/api/health')).ok, 'restart independent brief');
  await page.goto(base);
  await independentCard.getByRole('button', { name: 'Open verified file' }).click();
  const reopenedBrief = page.waitForEvent('download');
  await independentCard.getByRole('button', { name: 'Download file', exact: true }).click();
  assert.deepEqual(await readFile(await (await reopenedBrief).path()), briefBytes);
  assert.equal((await get('/api/pulse')).budget.used, 1);
  checks.push('An ordinary later Work finds a completed Pulse answer without a selected source, reports its verified excerpt with an actual file, and retains both after restart.');

  // A normal task names a subject, not an earlier observation or Work ID.
  // The fixture must encounter an attributed cue in the actual first model
  // request before choosing find; two comparison tasks must work without one.
  // Three newer matching Works have ordinary answers, but no settled Pulse
  // answer. They must not displace the older completed result when the user
  // describes its subject in a different word order.
  for (const title of ['Propagation while receiving', 'While receiving', 'Propagation']) {
    await createWork(title);
  }
  const statusOutputs = [];
  for (const topic of ['receiving propagation', 'propagation while receiving',
    'propagation source', 'propagation monitoring', 'garden irrigation']) {
    const statusWork = (await post('/api/pages', {
      title: 'Status brief: ' + topic,
      instruction: 'Prepare a Markdown status brief for ' + topic + '.',
    }, 201)).page.id;
    const path = 'status-' + topic.replaceAll(' ', '-') + '.md';
    let statusDelivery;
    await wait(async () => {
      statusDelivery = (await get('/api/pages/home')).messages.find(row =>
        row.sourcePageId === statusWork && row.reportedArtifact?.path === path);
      return statusDelivery;
    }, 'ordinary status brief: ' + topic, 30000);
    const related = ['receiving propagation', 'propagation while receiving'].includes(topic);
    assert.equal(statusDelivery.content.includes('Verified earlier Work excerpt'), related);
    if (related) assert.ok(statusDelivery.content.includes('Receiving propagation'));
    const card = page.locator('#message-' + statusDelivery.id);
    await card.getByRole('button', { name: 'Open verified file' }).click();
    const statusDownload = page.waitForEvent('download');
    await card.getByRole('button', { name: 'Download file', exact: true }).click();
    const bytes = await readFile(await (await statusDownload).path());
    assert.equal(bytes.toString('utf8'), '# Status brief\n\n' +
      (related ? received.find(row => row.id === propagated.resultMessageId).content
        : 'Verify the current state before drawing conclusions.') + '\n');
    statusOutputs.push({ id: statusDelivery.id, bytes });
  }
  await stop(server); server = start(true, false, propagationDir, true);
  await wait(async () => (await fetch(base + '/api/health')).ok, 'restart status briefs');
  await page.goto(base);
  for (const output of statusOutputs) {
    const card = page.locator('#message-' + output.id);
    await card.getByRole('button', { name: 'Open verified file' }).click();
    const download = page.waitForEvent('download');
    await card.getByRole('button', { name: 'Download file', exact: true }).click();
    assert.deepEqual(await readFile(await (await download).path()), output.bytes);
  }
  assert.equal((await get('/api/pulse')).budget.used, 1);
  checks.push('A normal topic request encounters a completed Pulse result before choosing a tool; a reordered topic finds the older answer despite three newer unfinished matching Works. An owned Work without a result, a one-word overlap and an unrelated topic create fresh unsourced files; all five delivered files survive restart.');

  // Competing results from the same Work, not a fabricated history. A
  // separate isolated test World gets exactly two genuine propagation turns.
  await page.close();
  await stop(server);
  const decisionDir = join(directory, 'competing-results');
  server = start(false, false, decisionDir, true);
  await wait(async () => (await fetch(base + '/api/health')).ok, 'competing results World');
  await post('/api/pulse/weekly-usage', { enabled: true, weeklyLimit: 100 }, 200, 'PATCH');
  const battery = await createWork('电池', 'Set the test operating mode to offline.');
  const answers = {};
  const preamble = 'Calibration record retained. '.repeat(6);
  const expectedAnswers = {
    offline: preamble + 'Offline battery operation: use stored readings. 离线运行时使用已保存的读数。',
    mains: preamble + 'Mains battery operation: use outlet checks. 市电运行时检查插座。',
  };
  assert.equal(expectedAnswers.offline.slice(0, 120), expectedAnswers.mains.slice(0, 120));
  for (const mode of ['offline', 'mains']) {
    if (mode === 'mains') {
      await post('/api/pages/' + battery + '/messages', { content: 'Set the test operating mode to mains.' }, 202);
      await wait(async () => {
        const snapshot = await get('/api/pages/' + battery);
        return snapshot.page.status === 'idle' && snapshot.messages.some(row =>
          row.role === 'assistant' && row.content === expectedAnswers.mains);
      }, 'receiver mode changed');
    }
    const source = await createWork('Independent ' + mode + ' observation');
    await post('/api/pulse/connections', { sourcePageId: source, targetPageId: battery, weight: 0.9 }, 201);
    await post('/api/pages/' + source + '/messages', { content: 'A new ' + mode + ' observation.' }, 202);
    let activation;
    await wait(async () => {
      activation = (await get('/api/pulse')).recentActivations.find(item =>
        item.kind === 'propagation' && item.pageId === battery &&
        item.resultMessageId && item.resultMessageId !== answers.offline?.id);
      return activation?.status === 'settled';
    }, 'settled ' + mode + ' battery answer');
    const answer = (await get('/api/pages/' + battery)).messages.find(row => row.id === activation.resultMessageId);
    assert.equal(answer.role, 'assistant');
    assert.equal(answer.content, expectedAnswers[mode]);
    answers[mode] = answer;
  }
  assert.notEqual(answers.offline.id, answers.mains.id);
  assert.equal((await get('/api/pulse')).budget.used, 2);
  await stop(server); server = start(true, false, decisionDir, true);
  await wait(async () => (await fetch(base + '/api/health')).ok, 'reopen competing results World');
  page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
  page.on('pageerror', error => errors.push(error.message));
  await page.goto(base);
  const decisionOutputs = [];
  const decisionCases = [
    ['offline', 'offline', 'Prepare a Markdown decision brief for 电池 under offline constraints.'],
    ['mains', 'mains', 'Prepare a Markdown decision brief for 电池 under mains constraints.'],
    ['rain', 'rain', 'Prepare a Markdown decision brief for garden irrigation under rain constraints.'],
    ['offline', 'offline-zh', '请为电池离线运行编写决策简报。'],
    ['mains', 'mains-zh', '请为电池市电运行编写决策简报。'],
    ['rain', 'rain-zh', '请为花园灌溉编写决策简报。'],
  ];
  decisionCases.push(
    ['offline', 'revised', 'Prepare a Markdown decision brief for 电池 under offline constraints in decision-revised.md.'],
    ['offline', 'outcome', 'Prepare a Markdown decision brief for 电池 under offline constraints in decision-outcome.md.'],
    ['offline', 'withdrawn', 'Prepare a Markdown decision brief for 电池 under offline constraints in decision-withdrawn.md.'],
  );
  const correctionAdvice = 'Check the timestamp of stored readings before relying on the historical answer.';
  const outcomeAdvice = 'If the stored reading has no timestamp, obtain a fresh reading before relying on it.';
  const outcomeNote = 'For offline battery work, verify the timestamp of stored readings before use. If it is missing, obtain a fresh reading.';
  const reportedOutcome = 'The delivered brief checks the timestamp but does not say what to do when it is missing.';
  let correctedExample, revisedWorkId, revisedResultId;
  for (const [mode, filename, instruction] of decisionCases) {
    if (filename === 'revised') {
      // Follow the actual Activity entry into its receiving Work. The local
      // propagation message and the settled answer are distinct source rows.
      await page.getByRole('button', { name: 'Activity', exact: true }).click();
      const activity = page.locator('.pulse-activity-list li').filter({ hasText: 'Independent offline observation' });
      await activity.getByRole('button', { name: '电池 · Open local message', exact: true }).click();
      await wait(() => new URL(page.url()).pathname === '/page/' + battery, 'offline propagation Work');
      const original = page.locator('#message-' + answers.offline.id);
      await original.waitFor();
      await original.getByRole('button', { name: 'Save example', exact: true }).click();
      const save = page.getByRole('dialog', { name: 'Save example', exact: true });
      assert.equal(await save.getByRole('textbox', { name: 'Original passage', exact: true }).inputValue(), answers.offline.content);
      await save.getByRole('textbox', { name: 'Example name', exact: true }).fill('Offline battery reading procedure');
      await save.getByRole('textbox', { name: 'What should we notice? (optional)', exact: true }).fill('For offline battery work, use stored readings.');
      await save.getByRole('button', { name: 'Save example', exact: true }).click();
      const lesson = page.locator('.lesson').filter({ has: page.getByRole('heading', { name: 'Offline battery reading procedure', exact: true }) });
      await lesson.getByText('In use · v1', { exact: true }).waitFor();
      const saved = (await experiences(battery)).find(row => row.title === 'Offline battery reading procedure');
      assert.equal(saved.scope, 'subject');
      assert.equal(saved.revision, 1);
      assert.deepEqual(saved.sourceAnchor, { resultMessageId: answers.offline.id });
      await lesson.getByRole('button', { name: 'Edit example note', exact: true }).click();
      await lesson.getByRole('textbox', { name: 'What should we notice? (optional)', exact: true }).fill('For offline battery work, verify the timestamp of stored readings before use.');
      await lesson.getByRole('textbox', { name: 'What prompted this change? (optional)', exact: true }).fill('The stored readings in this example could be out of date.');
      await lesson.getByRole('button', { name: 'Save refinement', exact: true }).click();
      await lesson.getByText('In use · v2', { exact: true }).waitFor();
      correctedExample = (await experiences(battery)).find(row => row.id === saved.id);
      assert.equal(correctedExample.revision, 2);
      assert.deepEqual(correctedExample.source, saved.source);
      assert.deepEqual(correctedExample.encounter, saved.encounter);
      assert.equal(correctedExample.feedback.at(-1).source.kind, 'operator_described_refinement');
      await lesson.getByText('Source experience', { exact: true }).click();
      await lesson.getByRole('button', { name: 'Go to original result', exact: true }).click();
      await wait(() => new URL(page.url()).hash === '#message-' + answers.offline.id, 'saved original Pulse answer');
      await page.getByRole('dialog', { name: 'Lessons and examples', exact: true }).waitFor({ state: 'hidden' });
    } else if (filename === 'outcome') {
      // Report the limitation of the actual delivered file from its receiving
      // Work. The operator anchors the report to that Work's settled reply.
      await page.getByRole('navigation', { name: 'Pages', exact: true })
        .getByRole('link', { name: /^Decision brief: revised/ }).click();
      await wait(() => new URL(page.url()).pathname === '/page/' + revisedWorkId, 'receiving Work');
      const receiverComposer = page.getByRole('textbox', { name: 'Message Decision brief: revised', exact: true });
      const receiverDraft = 'Keep this draft while reviewing the missing fallback.';
      await receiverComposer.fill(receiverDraft);
      await page.getByRole('button', { name: 'Settings', exact: true }).click();
      await page.getByRole('button', { name: 'View this page’s experience', exact: true }).click();
      const lesson = page.locator('.lesson').filter({ has: page.getByRole('heading', { name: 'Offline battery reading procedure', exact: true }) });
      await lesson.getByText('In use · v2', { exact: true }).waitFor();
      await lesson.getByRole('button', { name: 'Give feedback', exact: true }).click();
      await lesson.getByRole('textbox', { name: 'What changed in the result?', exact: true }).fill(reportedOutcome);
      const picker = lesson.getByRole('combobox', { name: 'Link a result from this page (optional)', exact: true });
      await picker.selectOption(revisedResultId);
      await lesson.getByRole('button', { name: 'No clear benefit', exact: true }).click();
      await lesson.getByText('In use · v3', { exact: true }).waitFor();
      const receiving = (await experiences(revisedWorkId)).find(row => row.id === correctedExample.id);
      assert.equal(receiving.canEdit, false);
      assert.equal(receiving.feedback.length, 1);
      assert.equal(receiving.feedback[0].source.result_message_id, revisedResultId);
      assert.equal(receiving.feedback[0].source.page_id, revisedWorkId);
      assert.equal(receiving.feedback[0].evidence, reportedOutcome);
      await page.getByRole('button', { name: 'Close dialog', exact: true }).click();
      // The source owner can inspect the linked actual result and return.
      await page.getByRole('button', { name: 'Activity', exact: true }).click();
      const activity = page.locator('.pulse-activity-list li').filter({ hasText: 'Independent offline observation' });
      await activity.getByRole('button', { name: '电池 · Open local message', exact: true }).click();
      await wait(() => new URL(page.url()).pathname === '/page/' + battery, 'source after receiving feedback');
      await page.getByRole('button', { name: 'Settings', exact: true }).click();
      await page.getByRole('button', { name: 'View this page’s experience', exact: true }).click();
      await lesson.getByText('In use · v3', { exact: true }).waitFor();
      const owner = (await experiences(battery)).find(row => row.id === correctedExample.id);
      assert.equal(owner.feedback.at(-1).source.relation, 'shared_recipient');
      assert.equal(owner.feedback.at(-1).source.result_message_id, revisedResultId);
      assert.equal(owner.feedback.at(-1).revision, 2);
      assert.deepEqual(owner.source, correctedExample.source);
      await lesson.getByText('Later feedback (2)', { exact: true }).click();
      await lesson.getByText(reportedOutcome, { exact: false }).waitFor();
      await lesson.getByRole('button', { name: 'View reported result', exact: true }).click();
      await wait(() => new URL(page.url()).pathname === '/page/' + revisedWorkId && new URL(page.url()).hash === '#message-' + encodeURIComponent(revisedResultId), 'exact receiving outcome');
      await page.locator('#message-' + revisedResultId).waitFor();
      assert.equal(await receiverComposer.inputValue(), receiverDraft);
      await page.getByRole('button', { name: 'Back to conversation', exact: true }).click();
      await wait(() => new URL(page.url()).pathname === '/page/' + battery, 'return to original procedure');
      await page.getByRole('button', { name: 'Settings', exact: true }).click();
      await page.getByRole('button', { name: 'View this page’s experience', exact: true }).click();
      await lesson.getByRole('button', { name: 'Edit example note', exact: true }).click();
      await lesson.getByRole('textbox', { name: 'What should we notice? (optional)', exact: true }).fill(outcomeNote);
      await lesson.getByRole('textbox', { name: 'What prompted this change? (optional)', exact: true }).fill(reportedOutcome);
      await lesson.getByRole('button', { name: 'Save refinement', exact: true }).click();
      await lesson.getByText('In use · v4', { exact: true }).waitFor();
      const refined = (await experiences(battery)).find(row => row.id === correctedExample.id);
      assert.deepEqual(refined.source, correctedExample.source);
      assert.equal(refined.feedback.at(-2).source.result_message_id, revisedResultId);
      assert.equal(refined.note, outcomeNote);
      await page.getByRole('button', { name: 'Close dialog', exact: true }).click();
    } else if (filename === 'withdrawn') {
      // Reopen the source from the actual Activity list and withdraw with a
      // distinct attributed reason, rather than silently pausing the example.
      await page.getByRole('button', { name: 'Activity', exact: true }).click();
      const activity = page.locator('.pulse-activity-list li').filter({ hasText: 'Independent offline observation' });
      await activity.getByRole('button', { name: '电池 · Open local message', exact: true }).click();
      await wait(() => new URL(page.url()).pathname === '/page/' + battery, 'return to source Work');
      await page.getByRole('button', { name: 'Settings', exact: true }).click();
      await page.getByRole('button', { name: 'View this page’s experience', exact: true }).click();
      const lesson = page.locator('.lesson').filter({ has: page.getByRole('heading', { name: 'Offline battery reading procedure', exact: true }) });
      await lesson.getByText('In use · v4', { exact: true }).waitFor();
      await lesson.getByRole('button', { name: 'Give feedback', exact: true }).click();
      const withdraw = lesson.getByRole('button', { name: 'Withdraw from use', exact: true });
      assert.equal(await withdraw.isDisabled(), true);
      const reason = 'Withdraw this reusable procedure until a new operating check is available.';
      await lesson.getByRole('textbox', { name: 'What changed in the result?', exact: true }).fill(reason);
      await withdraw.click();
      await lesson.getByText('Paused · v5', { exact: true }).waitFor();
      const retired = (await experiences(battery)).find(row => row.id === correctedExample.id);
      assert.equal(retired.active, false);
      assert.equal(retired.revision, 5);
      assert.deepEqual(retired.source, correctedExample.source);
      assert.equal(retired.feedback.at(-1).verdict, 'withdrawn');
      assert.equal(retired.feedback.at(-1).evidence, reason);
      await lesson.getByText('Later feedback (4)', { exact: true }).click();
      await lesson.getByText(reason, { exact: false }).waitFor();
      await page.getByRole('button', { name: 'Close dialog', exact: true }).click();
    }
    if (['revised', 'outcome', 'withdrawn'].includes(filename)) {
      await stop(server); server = start(true, false, decisionDir, true);
      await wait(async () => (await fetch(base + '/api/health')).ok, 'reopen after experience ' + filename);
      await page.goto(base);
      assert.equal((await get('/api/pages/' + battery)).messages.find(row =>
        row.id === answers.offline.id).content, answers.offline.content);
      assert.equal((await get('/api/pulse')).budget.used, 2);
    }
    let id;
    if (['revised', 'outcome', 'withdrawn'].includes(filename)) {
      await page.getByRole('button', { name: 'Create a page', exact: true }).click();
      await page.getByRole('textbox', { name: 'Page name', exact: true }).fill('Decision brief: ' + filename);
      await page.getByRole('textbox', { name: 'Where should we begin?', exact: true }).fill(instruction);
      await page.getByRole('button', { name: 'Create page', exact: true }).click();
      await page.getByRole('heading', { name: 'Decision brief: ' + filename, exact: true }).waitFor();
      id = decodeURIComponent(new URL(page.url()).pathname.split('/').pop());
    } else {
      id = (await post('/api/pages', {
        title: 'Decision brief: ' + filename, instruction,
      }, 201)).page.id;
    }
    const path = 'decision-' + filename + '.md';
    let delivery;
    await wait(async () => {
      delivery = (await get('/api/pages/home')).messages.find(row =>
        row.sourcePageId === id && row.reportedArtifact?.path === path);
      return delivery;
    }, 'competing result decision brief: ' + filename, 30000);
    assert.equal(delivery.content.includes('Verified earlier Work excerpt'), Boolean(answers[mode]));
    if (answers[mode]) {
      assert.ok(delivery.content.includes(answers[mode].id));
      assert.ok(delivery.content.includes(answers[mode].content));
      assert.ok(!delivery.content.includes(answers[mode === 'offline' ? 'mains' : 'offline'].content));
    }
    if (filename === 'revised') {
      revisedWorkId = id;
      await wait(async () => {
        const snapshot = await get('/api/pages/' + id);
        revisedResultId = snapshot.messages.find(row => row.role === 'assistant' && row.status === 'settled')?.id;
        return snapshot.page.status === 'idle' && revisedResultId;
      }, 'settled receiving result for delivered file');
    }
    if (['revised', 'outcome', 'withdrawn'].includes(filename)) {
      await page.locator('.main-nav').getByRole('link', { name: 'Home', exact: true }).click();
    }
    const card = page.locator('#message-' + delivery.id);
    await card.getByRole('button', { name: 'Open verified file' }).click();
    const download = page.waitForEvent('download');
    await card.getByRole('button', { name: 'Download file', exact: true }).click();
    const bytes = await readFile(await (await download).path());
    assert.equal(bytes.toString('utf8'), '# Decision brief\n\n' +
      (answers[mode]?.content ?? 'Check the current evidence.') + '\n' +
      (filename === 'revised' ? '\n' + correctionAdvice + '\n' : filename === 'outcome' ? '\n' + outcomeAdvice + '\n' : ''));
    if (['revised', 'outcome', 'withdrawn'].includes(filename)) {
      const eligible = (await experiences(id)).find(row => row.id === correctedExample.id);
      assert.equal(Boolean(eligible), filename !== 'withdrawn');
      if (eligible) assert.equal(eligible.revision, filename === 'revised' ? 2 : 4);
    }
    decisionOutputs.push({ id: delivery.id, bytes });
  }
  await stop(server); server = start(true, false, decisionDir, true);
  await wait(async () => (await fetch(base + '/api/health')).ok, 'restart decision briefs');
  await page.goto(base);
  for (const output of decisionOutputs) {
    const card = page.locator('#message-' + output.id);
    await card.getByRole('button', { name: 'Open verified file' }).click();
    const download = page.waitForEvent('download');
    await card.getByRole('button', { name: 'Download file', exact: true }).click();
    assert.deepEqual(await readFile(await (await download).path()), output.bytes);
  }
  assert.equal((await get('/api/pulse')).budget.used, 2);
  checks.push('English and fully Chinese ordinary tasks distinguish two settled answers with identical 120-character prefixes after restart: offline selects the older answer, mains selects the newer answer, and unrelated tasks make fresh files; exact source event IDs, all six file bytes and Pulse allowance survive.');

  checks.push('A person reports a limitation of a delivered file from its receiving Work, attaches the actual settled result, follows that exact report from the source procedure and returns without losing the receiving draft. The refined source yields a different later file before withdrawal; original answers, all nine delivered files and allowance survive restart.');

  assert.deepEqual(errors, []);
  console.log(JSON.stringify({ checks, errors, viewports: ['1280x900', '390x844'], modelEvidence: 'explicit_synthetic_transport_only' }, null, 2));
} catch (error) {
  if (screenshots && page) await page.screenshot({ path: join(screenshots, 'background-result-failure.png') }).catch(() => {});
  throw error;
} finally {
  await browser?.close(); await stop(server); await rm(directory, { recursive: true, force: true });
}
