/** Native SELF -> web observation -> file/report -> source -> restart/recall.
 * Model and website HTTP are explicit synthetic fixtures. TLS is tested separately.
 * Copyright 2026 Lightmaze Contributors. Licensed under Apache-2.0.
 */
import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { mkdtemp, mkdir, rm, writeFile } from 'node:fs/promises';
import { createServer } from 'node:net';
import { tmpdir } from 'node:os';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { createRequire } from 'node:module';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '../..');
const { chromium } = createRequire(import.meta.url)(process.env.PLAYWRIGHT_MODULE || 'playwright');
const directory = await mkdtemp(join(tmpdir(), 'per-pulse-web-reading-'));
const output = process.env.PER_PULSE_WEB_SCREENSHOTS || await mkdtemp(join(tmpdir(), 'per-pulse-web-screenshots-'));
await mkdir(output, { recursive: true });
const socket = createServer();
await new Promise(done => socket.listen(0, '127.0.0.1', done));
const port = socket.address().port;
await new Promise(done => socket.close(done));
const base = `http://127.0.0.1:${port}`, sourceUrl = 'https://reading.example/field-notes';
let serverLog = '', browser, page;
const checks = [], errors = [];
const installedPython = process.env.PER_PULSE_INSTALLED_PYTHON;
const start = () => {
  const environment = { ...process.env };
  if (installedPython) {
    delete environment.PYTHONPATH;
    delete environment.PYTHONHOME;
    for (const key of Object.keys(environment)) if (key.startsWith('P_PULSE_')) delete environment[key];
  } else environment.PYTHONPATH = join(root, 'src');
  const child = spawn(installedPython || process.env.PER_PULSE_PYTHON || join(root, '.venv/bin/python'), [
    join(root, 'tests/browser/web_read_server.py'), '--data-dir', directory, '--port', String(port),
    ...(installedPython ? ['--installed'] : []),
  ], { cwd: installedPython ? directory : root, env: environment, stdio: ['ignore', 'pipe', 'pipe'] });
  child.stderr.on('data', data => { serverLog += data; });
  return child;
};
const stop = async child => {
  if (child.exitCode !== null || child.signalCode !== null) return;
  const exited = new Promise(done => child.once('exit', done));
  child.kill('SIGTERM');
  const force = setTimeout(() => child.kill('SIGKILL'), 10000);
  await exited; clearTimeout(force);
};
const get = async path => { const response = await fetch(base + path); assert.ok(response.ok); return response.json(); };
const wait = async (read, ready, label) => {
  const end = Date.now() + 30000;
  while (Date.now() < end) {
    try { const value = await read(); if (ready(value)) return value; } catch {}
    await new Promise(done => setTimeout(done, 80));
  }
  throw new Error(`Timed out: ${label}\n${serverLog}`);
};
let server = start();
try {
  await wait(() => fetch(base + '/api/health'), value => value.ok, 'native server');
  browser = await chromium.launch({ executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE || undefined,
    args: process.env.PLAYWRIGHT_CHROMIUM_ARGS ? JSON.parse(process.env.PLAYWRIGHT_CHROMIUM_ARGS) : undefined });
  const context = await browser.newContext({ viewport: { width: 1280, height: 900 } });
  await context.route(sourceUrl, route => route.fulfill({ contentType: 'text/html',
    body: '<h1>Explicit synthetic website fixture</h1><p>Original field observation: café 山川 🌍.</p>' }));
  page = await context.newPage(); page.on('pageerror', error => errors.push(error.message));
  await page.goto(base);
  await page.getByRole('button', { name: 'Activity', exact: true }).click();
  await page.getByRole('spinbutton', { name: 'Requests per week', exact: true }).fill('20');
  await page.getByRole('button', { name: 'Start autonomous practice', exact: true }).click();
  const bootstrap = await wait(() => get('/api/bootstrap'), value => value.pages.some(p => p.title === 'Reading expedition'), 'native Work');
  const work = bootstrap.pages.find(p => p.title === 'Reading expedition').id;
  const view = await wait(() => get('/api/pages/' + work), value => value.page.status === 'idle' && value.artifacts.length === 1, 'saved sourced file');
  const source = view.execution.webSources.items[0];
  assert.equal(source.url, sourceUrl); assert.equal(source.truncated, true);
  const home = await wait(() => get('/api/pages/home'), value => value.messages.some(m => m.sourcePageId === work), 'Home report');
  const status = await get('/__web/status');
  assert.equal(status.installed, Boolean(installedPython));
  const usage = await wait(() => get('/api/pulse/weekly-usage'), value => value.usage.consumed === status.requests
    && value.usage.states.completed === status.requests, 'all model requests accounted');
  assert.equal(status.webRequests, 1);
  checks.push('one global enable reaches native SELF, Work, a partial web observation, a real file and a sourced Home report under weekly accounting');
  await page.getByRole('button', { name: 'Pause autonomous practice', exact: true }).click();
  // Closing Activity must not trap the user behind a pending native weekly update.
  // The native operation continues, so verify its outcome before navigating.
  await page.getByRole('button', { name: 'Close dialog', exact: true }).click();
  await page.locator('.pulse-activity-dialog').waitFor({ state: 'detached' });
  await wait(() => get('/api/pulse/weekly-usage'),
    value => value.usage.enabled === false, 'autonomous practice paused');
  const report = home.messages.find(m => m.sourcePageId === work);
  await page.locator('#message-' + report.id).locator('.source-line a.session-link').click();
  await page.getByRole('heading', { name: 'Reading expedition', exact: true }).waitFor();
  await page.locator('.web-sources summary').click();
  await page.getByText("Only part of this page's text was read.", { exact: true }).waitFor();
  const link = page.locator('.web-sources a');
  assert.equal(await link.getAttribute('href'), sourceUrl);
  assert.equal(await link.getAttribute('rel'), 'noopener noreferrer');
  const popupPromise = context.waitForEvent('page'); await link.click();
  const popup = await popupPromise; await popup.waitForLoadState();
  assert.equal(popup.url(), sourceUrl); await popup.close();
  await page.getByRole('button', { name: 'View saved text', exact: true }).click();
  const saved = page.locator('.web-observation-text');
  await saved.waitFor();
  assert.match(await saved.textContent(), /Original field observation: café 山川 🌍/);
  const originalReceipt = await get('/api/pages/' + work + '/execution/web/' + source.actionId);
  assert.equal(await saved.textContent(), originalReceipt.content);
  assert.equal(originalReceipt.source.url, sourceUrl);
  assert.equal(originalReceipt.textEnd - originalReceipt.textStart, [...originalReceipt.content].length);
  assert.equal((await get('/__web/status')).webRequests, 1);
  const selectedWebPassage = 'Original field observation: café 山川 🌍.';
  await page.getByRole('textbox', { name: 'Exact original passage' }).fill(selectedWebPassage);
  await page.getByRole('textbox', { name: 'What should later work notice? (optional)' })
    .fill('Compare this observed version with the later task.');
  // Hold the actual POST so a slow save cannot make an edited passage look persisted.
  let saveEntered;
  let releaseSave;
  const saveIntercepted = new Promise(resolve => { saveEntered = resolve; });
  const savedResponseHeld = new Promise(resolve => { releaseSave = resolve; });
  const savePattern = '**/api/pages/*/posture/web-examples';
  await page.route(savePattern, async route => {
    saveEntered();
    await savedResponseHeld;
    await route.continue();
  });
  await page.getByRole('button', { name: 'Keep this passage' }).click();
  try {
    await saveIntercepted;
    assert.equal(await page.getByRole('textbox', { name: 'Exact original passage' }).isDisabled(), true);
    assert.equal(await page.getByRole('textbox', { name: 'What should later work notice? (optional)' }).isDisabled(), true);
    assert.equal(await page.getByRole('button', { name: 'Continue in a new Work' }).count(), 0);
  } finally {
    releaseSave();
  }
  await page.getByText('Saved for your later Works.', { exact: false }).waitFor();
  await page.unroute(savePattern);
  assert.equal(await page.getByRole('textbox', { name: 'Exact original passage' }).isEnabled(), true);
  const retained = (await get('/api/pages/' + work + '/posture')).experiences.find(
    item => item.sourceKind === 'web' && item.excerpt === selectedWebPassage);
  assert.ok(retained);
  assert.equal(retained.source.web_receipt_id, source.actionId);
  assert.equal(retained.source.capture_kind, 'operator_selected_web_example');
  assert.equal((await get('/__web/status')).webRequests, 1);
  checks.push('the user retains an exact passage from the observed webpage in native Posture without new model or website requests');
  await page.getByRole('button', { name: 'Review saved experience', exact: true }).click();
  await page.getByRole('dialog', { name: 'Lessons and examples' }).waitFor();
  await page.getByRole('button', { name: 'Close dialog', exact: true }).click();
  await page.getByRole('button', { name: 'Continue in a new Work', exact: true }).click();
  await page.getByText('This saved passage will accompany your new Work', { exact: false }).waitFor();
  await page.locator('#new-page-title').fill('Follow-up comparison');
  await page.locator('#new-page-focus').fill('Use the retained field observation for a new task: compare a second field note.');
  await page.getByRole('button', { name: 'Create page', exact: true }).click();
  await page.getByRole('heading', { name: 'Follow-up comparison', exact: true }).waitFor();
  const followup = await wait(() => get('/api/bootstrap'),
    value => value.pages.some(p => p.title === 'Follow-up comparison'), 'follow-up Work');
  const followupId = followup.pages.find(p => p.title === 'Follow-up comparison').id;
  const followupView = await wait(() => get('/api/pages/' + followupId),
    value => value.messages.some(m => m.content === 'The follow-up Work completed a separate field-note comparison.'),
    'follow-up Work finished');
  assert.equal(followupView.page.sourcePageId, work);
  assert.equal(followupView.page.sourceMessageId, null, 'source Work has no invented source message');
  const firstInput = followupView.messages.find(m => m.role === 'user');
  assert.ok(firstInput.content.includes('WEB_EXPERIENCE_JSON'));
  assert.ok(firstInput.content.includes(retained.id));
  assert.ok(firstInput.content.includes(selectedWebPassage));
  assert.ok(firstInput.content.includes('Compare this observed version with the later task.'));
  assert.ok(firstInput.content.includes('My request for this Work:'));
  assert.ok(!firstInput.content.includes(sourceUrl), 'do not copy the source Work private URL');
  assert.ok((await get('/api/pages/' + followupId + '/posture')).experiences.some(item => item.id === retained.id));
  assert.equal((await get('/__web/status')).webRequests, 1, 'new Work reuses retained experience without refetching');
  await page.getByText('The follow-up Work completed a separate field-note comparison.', { exact: true }).waitFor();
  await page.locator('.message-assistant').last().getByRole('button', { name: 'Bring to Home', exact: true }).click();
  await page.getByRole('dialog', { name: 'Bring this back to Home.' }).waitFor();
  await page.getByRole('dialog').getByRole('button', { name: 'Bring to Home', exact: true }).click();
  const returned = await wait(() => get('/api/pages/home'), value => value.messages.some(
    m => m.sourcePageId === followupId && m.content.includes('The follow-up Work completed a separate field-note comparison.')),
    'follow-up result returns to Home');
  assert.ok(returned.messages.some(m => m.sourcePageId === followupId));
  checks.push('a saved observation opens its experience view and a user-authored later Work, then returns its sourced result to Home');
  await page.goto(base + '/page/' + work);
  await page.getByRole('heading', { name: 'Reading expedition', exact: true }).waitFor();
  await page.screenshot({ path: join(output, 'web-source-desktop.png') });
  checks.push('the browser opens the exact retained webpage text and source version alongside the original HTTPS link, without a second network read');
  const draft = 'Keep this reading draft.';
  await page.getByRole('textbox', { name: /^Message / }).fill(draft);
  await page.goto('about:blank'); await stop(server); server = start();
  await wait(() => fetch(base + '/api/health'), value => value.ok, 'restart');
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto(base + '/page/' + work);
  const composer = page.getByRole('textbox', { name: /^Message / }); await composer.waitFor();
  assert.equal(await composer.inputValue(), draft);
  assert.deepEqual((await get('/api/pages/' + work)).execution.webSources.items, [source]);
  assert.equal((await get('/__web/status')).requests, 0);
  assert.equal((await get('/__web/status')).webRequests, 0);
  await composer.fill('Recall the original webpage from saved history.');
  await page.getByRole('button', { name: 'Send message', exact: true }).click();
  await wait(() => get('/api/pages/' + work), value => value.messages.at(-1).content === 'Recovered the original saved webpage observation.', 'history recall');
  await page.getByText('Recovered the original saved webpage observation.', { exact: true }).waitFor();
  assert.equal((await get('/__web/status')).webRequests, 0);
  assert.equal((await get('/api/pulse/weekly-usage')).usage.consumed, usage.usage.consumed);
  await page.locator('.web-sources summary').click();
  await page.getByRole('button', { name: 'View saved text', exact: true }).click();
  await page.locator('.web-observation-text').waitFor();
  assert.equal(await page.locator('.web-observation-text').textContent(), originalReceipt.content);
  assert.equal((await get('/__web/status')).webRequests, 0);
  assert.ok((await get('/api/pages/' + work + '/posture')).experiences.some(
    item => item.id === retained.id && item.excerpt === selectedWebPassage));
  await page.locator('.web-sources').scrollIntoViewIfNeeded();
  assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
  await page.screenshot({ path: join(output, 'web-source-mobile.png') });
  checks.push('restart preserves the source and unsent draft; mobile recall reads the original history without refetching or autonomous spending');
  // A saved passage remains usable as a source after restart, without saving it again.
  const savedAfterRestart = (await get('/api/pages/' + work + '/posture')).experiences.filter(
    item => item.sourceKind === 'web' && item.excerpt === selectedWebPassage);
  assert.equal(savedAfterRestart.length, 1);
  await page.locator('.nav-toggle').click();
  await page.getByRole('button', { name: 'Settings', exact: true }).click();
  await page.getByRole('button', { name: 'View this page’s experience', exact: true }).click();
  await page.getByRole('dialog', { name: 'Lessons and examples' }).waitFor();
  await page.getByRole('button', { name: 'Start another Work from this passage', exact: true }).click();
  await page.getByText('This saved passage will accompany your new Work', { exact: false }).waitFor();
  await page.locator('#new-page-title').fill('Resumed field comparison');
  await page.locator('#new-page-focus').fill('Use the retained field observation for a new task: compare a third field note.');
  await page.getByRole('button', { name: 'Create page', exact: true }).click();
  const resumed = await wait(() => get('/api/bootstrap'),
    value => value.pages.some(p => p.title === 'Resumed field comparison'), 'continued Work after restart');
  const resumedId = resumed.pages.find(p => p.title === 'Resumed field comparison').id;
  const resumedView = await wait(() => get('/api/pages/' + resumedId),
    value => value.messages.some(m => m.content === 'The follow-up Work completed a separate field-note comparison.'),
    'resumed Work result');
  assert.equal(resumedView.page.sourcePageId, work);
  const resumedInput = resumedView.messages.find(m => m.role === 'user');
  assert.ok(resumedInput.content.includes(retained.id));
  assert.ok(resumedInput.content.includes(selectedWebPassage));
  assert.ok(resumedInput.content.includes('Compare this observed version with the later task.'));
  assert.ok(!resumedInput.content.includes(sourceUrl));
  assert.equal((await get('/api/pages/' + work + '/posture')).experiences.filter(
    item => item.id === retained.id).length, 1, 'continuation does not save a duplicate experience');
  assert.equal((await get('/__web/status')).webRequests, 0, 'restart continuation does not refetch webpage');
  checks.push('saved web experiences can start a new native Work after restart without resaving or refetching');
  await page.goto(base + '/page/' + work);
  await page.getByRole('heading', { name: 'Reading expedition', exact: true }).waitFor();
  await composer.fill('Read an unsupported webpage.');
  await page.getByRole('button', { name: 'Send message', exact: true }).click();
  await page.getByText('This URL uses HTTP. Try a public HTTPS text page.', { exact: true }).waitFor();
  assert.equal((await get('/__web/status')).webRequests, 0);
  assert.deepEqual((await get('/api/pages/' + work)).execution.webSources.items, [source]);
  await composer.fill('Recall the original webpage from saved history.');
  await page.getByRole('button', { name: 'Send message', exact: true }).click();
  await wait(() => get('/api/pages/' + work), value => value.page.status === 'idle'
    && value.messages.filter(m => m.content === 'Recovered the original saved webpage observation.').length === 2, 'continue after unreadable URL');
  checks.push('an unreadable URL has a useful visible reason; the existing source survives and the next message can continue');
  assert.deepEqual(errors, []);
  console.log(JSON.stringify({ passed: checks.length, checks, installed: Boolean(installedPython), provider: 'synthetic model and website HTTP', output }));
} catch (error) {
  await writeFile(join(output, 'web-server.log'), serverLog);
  if (page) await page.screenshot({ path: join(output, 'web-failure.png') }).catch(() => {});
  throw error;
} finally {
  await browser?.close(); await stop(server); await rm(directory, { recursive: true, force: true });
}
