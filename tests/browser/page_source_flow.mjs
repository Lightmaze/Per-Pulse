// Copyright 2026 Lightmaze Contributors. Licensed under Apache-2.0.
// The server uses an explicit HTTP fixture, never a real model or user workspace.
import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { mkdtemp } from 'node:fs/promises';
import { createServer } from 'node:net';
import { tmpdir } from 'node:os';
import { join, resolve, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
import { createRequire } from 'node:module';
const root = resolve(dirname(fileURLToPath(import.meta.url)), '../..');
const require = createRequire(import.meta.url);
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const directory = await mkdtemp(join(tmpdir(), 'per-pulse-source-'));
const socket = createServer();
await new Promise(r => socket.listen(0, '127.0.0.1', r));
const port = socket.address().port;
await new Promise(r => socket.close(r));
const base = `http://127.0.0.1:${port}`;
let log = '', browser;
const errors = [];
const startServer = dataDir => {
  const child = spawn(process.env.PER_PULSE_PYTHON || join(root, '.venv/bin/python'),
    [join(root, 'tests/browser/example_server.py'), '--data-dir', dataDir, '--port', String(port)],
    { cwd: root, env: { ...process.env, PYTHONPATH: join(root, 'src') }, windowsHide: true });
  child.stdout.on('data', d => log += d);
  child.stderr.on('data', d => log += d);
  return child;
};
const stopServer = async child => {
  if (child.exitCode === null && child.signalCode === null) {
    const stopped = new Promise(r => child.once('exit', r));
    child.kill();
    await stopped;
  }
};
let server = startServer(directory);
const wait = async (fn, label) => {
  for (let i = 0; i < 250; i++) { try { if (await fn()) return; } catch {} await new Promise(r => setTimeout(r, 100)); }
  throw new Error(`${label}\n${log}`);
};
const data = async path => { const r = await fetch(base + path); assert.ok(r.ok); return r.json(); };
try {
  await wait(async () => (await fetch(base + '/api/health')).ok, 'server ready');
  browser = await chromium.launch({ executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE || undefined });
  let context = await browser.newContext({ viewport: { width: 1280, height: 900 }, locale: 'en-US' });
  let page = await context.newPage();
  page.on('pageerror', e => errors.push(e.message));
  page.on('console', m => { if (m.type() === 'error') errors.push(m.text()); });
  // Delay the actual native bootstrap until the person has entered a Work.
  // Do not substitute a synthetic bootstrap response or a timed sleep.
  const startupCount = (await data('/api/bootstrap')).pages.filter(p => p.kind === 'work').length;
  let releaseBootstrap, observeBootstrap;
  const bootstrapGate = new Promise(r => { releaseBootstrap = r; });
  const bootstrapRequested = new Promise(r => { observeBootstrap = r; });
  await page.route('**/api/bootstrap', async route => {
    observeBootstrap();
    await bootstrapGate;
    await route.continue();
  });
  const startupTitle = 'Work drafted during startup';
  const startupInstruction = 'Prepare a concise practical source-aware plan.';
  try {
    await page.goto(base, { waitUntil: 'domcontentloaded' });
    await bootstrapRequested;
    await page.locator('.pages-label .icon-button').click();
    const startupDialog = page.getByRole('dialog');
    await startupDialog.locator('#new-page-title').fill(startupTitle);
    await startupDialog.locator('#new-page-focus').fill(startupInstruction);
    assert.equal(await startupDialog.locator('button[type="submit"]').isDisabled(), true);
    assert.equal((await data('/api/bootstrap')).pages.filter(p => p.kind === 'work').length, startupCount);
  } finally {
    releaseBootstrap();
  }
  const startupDialog = page.getByRole('dialog');
  await wait(async () => await startupDialog.isVisible()
    && await startupDialog.locator('button[type="submit"]').isEnabled(), 'startup draft stays open after bootstrap');
  assert.equal(await startupDialog.locator('#new-page-title').inputValue(), startupTitle);
  assert.equal(await startupDialog.locator('#new-page-focus').inputValue(), startupInstruction);
  await startupDialog.locator('button[type="submit"]').click();
  await page.getByRole('heading', { name: startupTitle, exact: true }).waitFor();
  const startupId = new URL(page.url()).pathname.split('/').pop();
  const startupWork = await data('/api/pages/' + startupId);
  assert.equal(startupWork.page.sourcePageId, 'home');
  assert.ok(startupWork.messages.some(m => m.role === 'user' && m.content.includes(startupInstruction)));
  assert.equal((await data('/api/bootstrap')).pages.filter(p => p.kind === 'work').length, startupCount + 1);
  await page.unroute('**/api/bootstrap');
  await page.locator('.main-nav a[href="/"]').click();
  await page.getByRole('heading', { name: 'A place to keep going.', exact: true }).waitFor();
  // A real connection failure before identity is known must allow a person
  // to keep editing and explicitly retry the same startup draft.
  let failBootstrap, observeFailure;
  const failureGate = new Promise(r => { failBootstrap = r; });
  const failureRequested = new Promise(r => { observeFailure = r; });
  let firstFailure = true;
  await page.route('**/api/bootstrap', async route => {
    if (!firstFailure) return route.continue();
    firstFailure = false;
    observeFailure();
    await failureGate;
    await route.abort('failed');
  });
  await page.reload({ waitUntil: 'domcontentloaded' });
  await failureRequested;
  const reconnectTitle = 'Work recovered after startup failure';
  const reconnectInstruction = 'Finish the plan using the original user intent.';
  try {
    await page.locator('.pages-label .icon-button').click();
    await page.locator('#new-page-title').fill(reconnectTitle);
    await page.locator('#new-page-focus').fill(reconnectInstruction);
  } finally {
    failBootstrap();
  }
  const reconnectDialog = page.getByRole('dialog');
  await reconnectDialog.getByRole('alert').waitFor();
  assert.equal(await reconnectDialog.locator('button[type="submit"]').isDisabled(), true);
  await reconnectDialog.getByRole('button', { name: 'Try again', exact: true }).click();
  await wait(async () => await reconnectDialog.locator('button[type="submit"]').isEnabled(), 'startup retry reconnects');
  assert.equal(await reconnectDialog.locator('#new-page-title').inputValue(), reconnectTitle);
  assert.equal(await reconnectDialog.locator('#new-page-focus').inputValue(), reconnectInstruction);
  await reconnectDialog.locator('button[type="submit"]').click();
  await page.getByRole('heading', { name: reconnectTitle, exact: true }).waitFor();
  const recovered = await data('/api/pages/' + new URL(page.url()).pathname.split('/').pop());
  assert.equal(recovered.page.sourcePageId, 'home');
  assert.ok(recovered.messages.some(m => m.role === 'user' && m.content.includes(reconnectInstruction)));
  assert.equal((await data('/api/bootstrap')).pages.filter(p => p.kind === 'work').length, startupCount + 2);
  await page.unroute('**/api/bootstrap');
  await page.locator('.main-nav a[href="/"]').click();
  await page.getByRole('heading', { name: 'A place to keep going.', exact: true }).waitFor();
  await page.reload();
  assert.equal(await page.title(), 'Per Pulse');
  await page.getByRole('textbox', { name: /^Message / }).fill('Draft a synthetic customer notice.');
  await page.getByRole('button', { name: 'Send message', exact: true }).click();
  let original;
  await wait(async () => { original = (await data('/api/pages/home')).messages.find(m => m.role === 'assistant' && m.status === 'settled'); return original; }, 'Home reply');
  const source = page.locator(`[data-message-id="${original.id}"]`);
  await source.getByRole('button', { name: 'New page', exact: true }).click();
  const dialog = page.getByRole('dialog');
  assert.equal(await dialog.locator('#new-page-source').inputValue(), original.content);
  assert.equal(await dialog.getByRole('textbox', { name: 'Your request', exact: true }).inputValue(), 'Continue from this source.');
  assert.match(await dialog.locator('label[for="new-page-source"]').innerText(), /Per Pulse/);
  await dialog.getByRole('textbox', { name: 'Page name', exact: true }).fill('Source attribution check');
  await dialog.getByRole('textbox', { name: 'Your request', exact: true }).fill('Make a source-aware plan.');
  await dialog.getByRole('button', { name: 'Create page', exact: true }).click();
  await page.getByRole('heading', { name: 'Source attribution check', exact: true }).waitFor();
  const targetUrl = page.url();
  const target = new URL(targetUrl).pathname.split('/').pop();
  const view = await data('/api/pages/' + target);
  const first = view.messages.find(m => m.pageSource);
  assert.equal(first.role, 'user'); assert.equal(first.pageSource.role, 'assistant');
  const card = page.locator(`[data-message-id="${first.id}"]`);
  assert.equal(await card.locator('.message-label').innerText(), 'Source and request');
  assert.equal(await card.locator('.transfer-presentation-source .transfer-presentation-label').innerText(), 'Per Pulse');
  assert.equal(await card.locator('.transfer-presentation-request .message-content').innerText(), 'Make a source-aware plan.');
  const link = card.locator('.source-line a');
  assert.match(await link.getAttribute('href'), new RegExp(original.id));
  await link.click();
  await page.locator(`[data-message-id="${original.id}"]`).waitFor();
  assert.equal(new URL(page.url()).pathname, '/');
  assert.equal(new URL(page.url()).hash, '#message-' + original.id);
  await page.goto(targetUrl); await card.waitFor();
  await page.reload(); await card.waitFor();
  await page.getByRole('button', { name: 'Find a message', exact: true }).click();
  await page.getByLabel('Words in this conversation').fill('source-aware plan');
  await page.getByRole('button', { name: 'Find', exact: true }).click();
  const result = page.locator(`[data-search-message-id="${first.id}"]`);
  await result.waitFor();
  assert.equal(await result.locator('.transfer-presentation-source .transfer-presentation-label').innerText(), 'Per Pulse');
  await page.getByRole('button', { name: 'Close dialog', exact: true }).click();
  if (process.env.PER_PULSE_SOURCE_SCREENSHOT) await page.screenshot({ path: process.env.PER_PULSE_SOURCE_SCREENSHOT, fullPage: false });
  await page.setViewportSize({ width: 390, height: 844 });
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
  assert.equal(await card.locator('.transfer-presentation-source .transfer-presentation-label').innerText(), 'Per Pulse');
  assert.equal(await page.locator('vite-error-overlay').count(), 0);
  if (process.env.PER_PULSE_SOURCE_SCREENSHOT) await page.screenshot({ path: process.env.PER_PULSE_SOURCE_SCREENSHOT.replace('.png', '-mobile.png'), fullPage: false });
  // Switching the interface language changes only the editable request template,
  // never the quoted author, source bytes, or stored provenance.
  await page.evaluate(() => localStorage.setItem('per-pulse.ui.language.v1', 'zh-CN'));
  await page.goto(base);
  await page.locator(`[data-message-id="${original.id}"]`).waitFor();
  await page.locator(`[data-message-id="${original.id}"]`).getByRole('button', { name: '新建页面', exact: true }).click();
  const chineseDialog = page.getByRole('dialog');
  assert.equal(await chineseDialog.locator('#new-page-source').inputValue(), original.content);
  assert.equal(await chineseDialog.getByRole('textbox', { name: '你的请求', exact: true }).inputValue(), '请根据这段来源继续工作。');
  await chineseDialog.getByRole('textbox', { name: '页面名称' }).fill('中文来源工作');
  await chineseDialog.getByRole('button', { name: '创建页面' }).click();
  await page.getByRole('heading', { name: '中文来源工作', exact: true }).waitFor();
  const chineseId = new URL(page.url()).pathname.split('/').pop();
  const chineseView = await data('/api/pages/' + chineseId);
  const chineseFirst = chineseView.messages.find(m => m.pageSource);
  assert.equal(chineseFirst.pageSource.excerpt, original.content);
  assert.equal(chineseFirst.pageSource.instruction, '请根据这段来源继续工作。');
  await page.locator(`[data-message-id="${chineseFirst.id}"]`).locator('.message-label').getByText('来源与请求').waitFor();
  await page.reload();
  await page.locator(`[data-message-id="${chineseFirst.id}"]`).locator('.message-label').getByText('来源与请求').waitFor();
  // A real native Work may be admitted while the browser loses the response.
  // A full browser context restart discards the dialog and sessionStorage.
  // The original request ID and digest must survive in origin-local storage.
  await page.evaluate(() => localStorage.setItem('per-pulse.ui.language.v1', 'en'));
  await page.goto(base);
  const workCount = async () => (await data('/api/bootstrap')).pages.filter(p => p.kind === 'work').length;
  const priorCount = await workCount();
  let admitted = null, dropped = 0;
  await page.route('**/api/pages', async route => {
    if (route.request().method() !== 'POST' || dropped) return route.continue();
    const response = await route.fetch();
    assert.equal(response.status(), 201, await response.text());
    admitted = await response.json();
    dropped++;
    await route.abort('failed');
  });
  const openNewWork = async () => {
    // On a phone the Create action is inside the collapsed navigation.
    // Use the actual mobile route instead of clicking a hidden sidebar button.
    const createAction = page.locator('.pages-label .icon-button');
    if (!await createAction.isVisible()) {
      await page.locator('.topbar .nav-toggle').click();
    }
    await createAction.click();
    await page.locator('#new-page-title').fill('Lost create receipt');
    await page.locator('#new-page-focus').fill('Prepare one practical source-aware deliverable.');
  };
  await openNewWork();
  await page.getByRole('button', { name: 'Create page', exact: true }).click();
  await wait(async () => !!admitted && await workCount() === priorCount + 1, 'first native Work admitted');
  await page.getByRole('dialog').getByRole('alert').waitFor();
  const pendingIntents = await page.evaluate(() => Object.entries(localStorage).filter(([key]) =>
    key.startsWith('per-pulse-create-pending:')));
  assert.equal(pendingIntents.length, 1, 'lost response retains exactly one pending Create identity');
  assert.deepEqual(Object.keys(JSON.parse(pendingIntents[0][1])).sort(), ['fingerprint', 'requestId'],
    'browser persists no source text or Work content');
  // Restore only browser-persistent storage in a new context; sessionStorage,
  // the original tab, its interceptors and all component state are destroyed.
  const browserStorage = await context.storageState();
  await context.close();
  context = await browser.newContext({ viewport: { width: 390, height: 844 }, locale: 'en-US',
    storageState: browserStorage });
  page = await context.newPage();
  page.on('pageerror', e => errors.push(e.message));
  page.on('console', m => { if (m.type() === 'error') errors.push(m.text()); });
  await page.goto(base);
  assert.equal(await page.evaluate(() => Object.keys(sessionStorage).filter(k =>
    k.startsWith('per-pulse-create-pending:')).length), 0, 'new tab has no pending sessionStorage');
  // The user can complete a different Work before retrying the lost first
  // receipt. Its confirmed identity must not overwrite the first pending one.
  await openNewWork();
  await page.locator('#new-page-title').fill('Separate Work while offline');
  await page.locator('#new-page-focus').fill('Complete a different legitimate task.');
  await page.getByRole('button', { name: 'Create page', exact: true }).click();
  await page.getByRole('heading', { name: 'Separate Work while offline', exact: true }).waitFor();
  assert.equal(await workCount(), priorCount + 2);
  await page.goto(base);
  assert.equal(await page.evaluate(() => Object.keys(localStorage).filter(k =>
    k.startsWith('per-pulse-create-pending:')).length), 1,
    'another confirmed Work must preserve the first uncertain request');
  await openNewWork();
  await page.getByRole('button', { name: 'Create page', exact: true }).click();
  await page.getByRole('heading', { name: 'Lost create receipt', exact: true }).waitFor();
  assert.equal(dropped, 1, 'only the first native HTTP response was lost');
  assert.equal(await workCount(), priorCount + 2, 'retry must not create a third Work');
  assert.equal(page.url().split('/').pop(), admitted.page.id);
  const actualWork = await data('/api/pages/' + admitted.page.id);
  assert.equal(actualWork.messages[0].id, admitted.initialMessageId);
  assert.equal(await page.evaluate(() => Object.keys(localStorage).filter(k =>
    k.startsWith('per-pulse-create-pending:')).length), 0, 'confirmed create clears pending browser identity');
  // A confirmed Work must not permanently collapse a later intentional Work
  // with identical title and request into the recovered original.
  await page.goto(base);
  await openNewWork();
  await page.getByRole('button', { name: 'Create page', exact: true }).click();
  await page.getByRole('heading', { name: 'Lost create receipt', exact: true }).waitFor();
  assert.equal(await workCount(), priorCount + 3, 'intentional new Create gets a separate native Work');
  assert.notEqual(page.url().split('/').pop(), admitted.page.id);

  // Keep the real browser dialog open while a different native World takes
  // over the same loopback address. No synthetic bootstrap or write response.
  await page.setViewportSize({ width: 1280, height: 900 });
  await page.goto(base);
  await page.getByRole('heading', { name: 'A place to keep going.', exact: true }).waitFor();
  const originalWorld = await data('/api/bootstrap');
  const homeComposer = tab => tab.getByRole('textbox', { name: /^Message / });
  const scrollTop = tab => tab.locator('.workspace-main').evaluate(node => node.scrollTop);
  const scrollTo = (tab, position) => tab.locator('.workspace-main').evaluate((node, top) => {
    node.scrollTop = top;
  }, position);
  const positionKey = world => 'per-pulse.ui.v2.' + encodeURIComponent(world) + '.position.home';
  const storedPosition = (tab, world) => tab.evaluate(key => Number(localStorage.getItem(key)), positionKey(world));
  const addReadingHistory = async (tab, marker) => {
    const historyText = Array.from({ length: 80 }, (_, i) =>
      marker + ' — reading paragraph ' + (i + 1) + '. Review the source before preparing a result.').join('\n\n');
    await homeComposer(tab).fill(historyText);
    await tab.getByRole('button', { name: 'Send message', exact: true }).click();
    let userMessage;
    await wait(async () => {
      const messages = (await data('/api/pages/home')).messages;
      const index = messages.findIndex(m => m.role === 'user' && m.content === historyText);
      if (index < 0) return false;
      userMessage = messages[index];
      return messages.slice(index + 1).some(m => m.role === 'assistant' && m.status === 'settled');
    }, marker + ' settled native reading history');
    await tab.locator(`[data-message-id="${userMessage.id}"]`).waitFor();
    return userMessage.id;
  };
  // The old tab has a genuine browser Back return coordinate, not merely a
  // per-World localStorage preference. The replacement has its own long Home.
  const originalReadingId = await addReadingHistory(page, 'Original World reading material');
  const originalDraft = 'Unsent original World note';
  await homeComposer(page).fill(originalDraft);
  const originalPosition = 640;
  await scrollTo(page, originalPosition);
  await wait(async () => Math.abs(await scrollTop(page) - originalPosition) < 3, 'original reading coordinate');
  await page.locator(`.page-nav a[href="/page/${target}"]`).click();
  await page.getByRole('heading', { name: 'Source attribution check', exact: true }).waitFor();
  await page.goBack();
  await page.locator(`[data-message-id="${originalReadingId}"]`).waitFor();
  await wait(async () => Math.abs(await scrollTop(page) - originalPosition) < 3, 'browser Back restores original reading coordinate');
  assert.equal(await page.evaluate(() => history.state.scrollPosition), originalPosition);
  assert.equal(await homeComposer(page).inputValue(), originalDraft);
  await openNewWork();
  const staleTitle = 'Original workspace confidential draft';
  const stalePurpose = 'Complete this original workspace task only.';
  await page.locator('#new-page-title').fill(staleTitle);
  await page.locator('#new-page-focus').fill(stalePurpose);
  const staleDialog = page.getByRole('dialog');
  await wait(async () => await staleDialog.locator('button[type="submit"]').isEnabled(), 'old World create ready');
  await stopServer(server);
  const replacementDirectory = await mkdtemp(join(tmpdir(), 'per-pulse-source-replacement-'));
  server = startServer(replacementDirectory);
  await wait(async () => (await fetch(base + '/api/health')).ok, 'replacement native server ready');
  const replacement = await data('/api/bootstrap');
  assert.notEqual(replacement.workspaceId, originalWorld.workspaceId);
  const replacementCount = replacement.pages.filter(p => p.kind === 'work').length;
  // A different tab has already been working in the replacement World.
  // Its native history, draft and actual scroll preference must win over
  // the old tab's transient browser Back position when it reconnects.
  const replacementReader = await context.newPage();
  await replacementReader.setViewportSize({ width: 1280, height: 900 });
  await replacementReader.goto(base);
  const replacementReadingId = await addReadingHistory(replacementReader, 'Replacement World reading material');
  const replacementDraft = 'Unsent replacement World note';
  await homeComposer(replacementReader).fill(replacementDraft);
  const replacementPosition = 160;
  await scrollTo(replacementReader, replacementPosition);
  await wait(async () => Math.abs(await scrollTop(replacementReader) - replacementPosition) < 3
    && Math.abs(await storedPosition(replacementReader, replacement.workspaceId) - replacementPosition) < 3,
    'replacement World has its own persisted reading position');
  await replacementReader.close();
  await page.bringToFront();
  const refusal = page.waitForResponse(response => response.url() === base + '/api/pages'
    && response.request().method() === 'POST');
  await staleDialog.locator('button[type="submit"]').click();
  const rejected = await refusal;
  assert.equal(rejected.status(), 409);
  assert.equal((await rejected.json()).error.code, 'workspace_changed');
  await staleDialog.getByRole('alert').waitFor();
  assert.equal(await staleDialog.locator('#new-page-title').inputValue(), staleTitle);
  assert.equal(await staleDialog.locator('#new-page-focus').inputValue(), stalePurpose);
  assert.equal(await staleDialog.locator('button[type="submit"]').isDisabled(), true);
  assert.equal(await workCount(), replacementCount, 'stale browser intent must not create a Work in the replacement World');
  assert.ok(!(await data('/api/pages/home')).messages.some(m => (m.content ?? '').includes(stalePurpose)));
  await staleDialog.getByRole('button', { name: 'Try again', exact: true }).click();
  await staleDialog.waitFor({ state: 'hidden' });
  await page.locator(`[data-message-id="${replacementReadingId}"]`).waitFor();
  assert.equal(await page.locator(`[data-message-id="${originalReadingId}"]`).count(), 0);
  assert.equal(await homeComposer(page).inputValue(), replacementDraft);
  await wait(async () => Math.abs(await scrollTop(page) - replacementPosition) < 3,
    'explicit reconnect restores replacement reading position rather than stale browser Back coordinate');
  assert.equal(await page.evaluate(() => history.state.scrollPosition), originalPosition,
    'old browser history remains intact but cannot steer a different World');
  await openNewWork();
  const freshTitle = 'New workspace deliberate task';
  const freshPurpose = 'Prepare the new workspace plan.';
  await page.locator('#new-page-title').fill(freshTitle);
  await page.locator('#new-page-focus').fill(freshPurpose);
  await page.getByRole('dialog').locator('button[type="submit"]').click();
  await page.getByRole('heading', { name: freshTitle, exact: true }).waitFor();
  const freshId = new URL(page.url()).pathname.split('/').pop();
  await wait(async () => (await data('/api/pages/' + freshId)).messages.some(m => m.role === 'assistant' && m.status === 'settled'), 'new World Work completes');
  const fresh = await data('/api/pages/' + freshId);
  assert.equal(fresh.workspaceId, replacement.workspaceId);
  assert.ok(fresh.messages.some(m => m.role === 'user' && m.content.includes(freshPurpose)));
  assert.equal(await workCount(), replacementCount + 1);
  assert.ok(!(await data('/api/bootstrap')).pages.some(p => p.title === staleTitle));
  await page.locator('.main-nav a[href="/"]').click();
  await page.locator(`[data-message-id="${replacementReadingId}"]`).waitFor();
  assert.equal(await homeComposer(page).inputValue(), replacementDraft);
  await stopServer(server);
  server = startServer(directory);
  await wait(async () => (await fetch(base + '/api/health')).ok, 'original World restarted');
  assert.equal((await data('/api/bootstrap')).workspaceId, originalWorld.workspaceId);
  await page.goto(base);
  await page.locator(`[data-message-id="${originalReadingId}"]`).waitFor();
  assert.equal(await homeComposer(page).inputValue(), originalDraft);
  assert.equal(await page.locator(`[data-message-id="${replacementReadingId}"]`).count(), 0);

  assert.deepEqual(errors.filter(error => !/net::ERR_FAILED|Failed to fetch|409 \(Conflict\)/.test(error)), []);
  console.log(JSON.stringify({ passed: true, checks: ['startup Create preserves title and purpose through native bootstrap and creates exactly one Work', 'startup connection failure retains intent through explicit retry', 'stale Create cannot write into a replacement native World; explicit reconnect permits a fresh completed Work', 'browser Back reading coordinate cannot override a different native World; both unsent drafts survive reconnect and restart', 'Home source author', 'separate request editor',
    'native user identity', 'rendered author', 'source anchor', 'reload', 'search attribution',
    'mobile overflow', 'lost HTTP create receipt after native admission',
    'closed tab and browser-context restart', 'interleaved second Work preserves original pending identity',
    'original Work recovery without a duplicate',
    'no private content in pending storage', 'pending identity cleared',
    'confirmed Create allows intentional new Work', 'no unexpected page errors'], base, directory }));
} finally {
  await browser?.close();
  await stopServer(server);
}