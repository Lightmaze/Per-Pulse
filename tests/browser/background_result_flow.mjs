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
  const createWork = async title => {
    const id = (await post('/api/pages', { title, instruction: 'Begin this local Work.' }, 201)).page.id;
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

  assert.deepEqual(errors, []);
  console.log(JSON.stringify({ checks, errors, viewports: ['1280x900', '390x844'], modelEvidence: 'explicit_synthetic_transport_only' }, null, 2));
} catch (error) {
  if (screenshots && page) await page.screenshot({ path: join(screenshots, 'background-result-failure.png') }).catch(() => {});
  throw error;
} finally {
  await browser?.close(); await stop(server); await rm(directory, { recursive: true, force: true });
}
