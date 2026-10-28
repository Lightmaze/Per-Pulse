/** Real browser + local World/Kernel + explicit transport fixture.
 * Copyright 2026 Lightmaze Contributors. Licensed under Apache-2.0.
 * No external model is contacted. This checks product mechanics only.
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
const directory = await mkdtemp(join(tmpdir(), 'per-pulse-browser-'));
const screenshots = process.env.PER_PULSE_SCREENSHOTS;
const socket = createServer();
await new Promise(resolve => socket.listen(0, '127.0.0.1', resolve));
const port = socket.address().port;
await new Promise(resolve => socket.close(resolve));
const base = `http://127.0.0.1:${port}`;
let serverLog = '';
const startServer = ({ offline = false } = {}) => {
  const child = spawn(process.env.PER_PULSE_PYTHON || join(root, '.venv/bin/python'), [
    join(root, 'tests/browser/posture_server.py'), '--data-dir', directory, '--port', String(port),
    ...(offline ? ['--offline'] : []),
  ], { cwd: root, stdio: ['ignore', 'pipe', 'pipe'] });
  child.stdout.on('data', data => { serverLog += data; });
  child.stderr.on('data', data => { serverLog += data; });
  return child;
};
const stopServer = async child => {
  if (child.exitCode !== null || child.signalCode !== null) return;
  child.kill('SIGTERM');
  const force = setTimeout(() => child.kill('SIGKILL'), 10000);
  await new Promise(resolve => child.once('exit', resolve));
  clearTimeout(force);
};
let server = startServer();
let browser;
const errors = [];
const wait = async (condition, description) => {
  const deadline = Date.now() + 15000;
  while (Date.now() < deadline) {
    if (await condition()) return;
    await new Promise(resolve => setTimeout(resolve, 60));
  }
  throw new Error(`Timed out: ${description}\n${serverLog}`);
};

try {
  await wait(async () => { try { return (await fetch(base + '/api/health')).ok; } catch { return false; } }, 'server ready');
  browser = await chromium.launch({ executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE || undefined,
    args: process.env.PLAYWRIGHT_CHROMIUM_ARGS ? JSON.parse(process.env.PLAYWRIGHT_CHROMIUM_ARGS) : undefined });
  const context = await browser.newContext({ viewport: { width: 1280, height: 900 } });
  const page = await context.newPage();
  page.on('pageerror', error => errors.push(error.message));
  await page.goto(base);
  await page.getByRole('textbox', { name: /^Message / }).waitFor();
  const openLessons = async () => {
    if (!(await page.getByRole('button', { name: 'Settings', exact: true }).isVisible())) {
      await page.getByRole('button', { name: 'Toggle navigation', exact: true }).click();
    }
    await page.getByRole('button', { name: 'Settings', exact: true }).click();
    await page.getByRole('button', { name: 'View this page’s lessons', exact: true }).click();
    await page.getByRole('dialog', { name: 'Lessons from the work', exact: true }).waitFor();
  };
  const close = () => page.getByRole('button', { name: 'Close dialog', exact: true }).click();
  const send = async (text, count) => {
    await page.getByRole('textbox', { name: /^Message / }).fill(text);
    await page.getByRole('button', { name: 'Send message', exact: true }).click();
    await wait(async () => await page.locator('.message-assistant').count() === count, 'assistant fixture response');
  };
  await openLessons();
  await page.getByText('No saved lessons in this page yet.', { exact: true }).waitFor();
  await close();
  await send('Build a practice product a visitor can try immediately.', 1);
  await openLessons();
  await page.getByText('No saved lessons in this page yet.', { exact: true }).waitFor();
  const learningTab = await context.newPage();
  await learningTab.goto(base);
  await learningTab.getByRole('textbox', { name: /^Message / }).fill('Let me try the interaction before asking for a profile.');
  await learningTab.getByRole('button', { name: 'Send message', exact: true }).click();
  await wait(async () => await learningTab.locator('.message-assistant').count() === 2, 'background correction result');
  await learningTab.close();
  await wait(async () => (await (await fetch(base + '/api/pages/home/posture')).json()).experiences.length === 1, 'settled learning');
  await page.getByRole('button', { name: 'Refresh lessons', exact: true }).click();
  const lesson = page.getByRole('article').filter({ has: page.getByRole('heading', { name: 'Direct interaction before optional setup', exact: true }) });
  await lesson.getByText('In use · v1', { exact: true }).waitFor();
  // Only the shareable abstraction is searchable: original encounters stay private.
  const finder = page.getByRole('searchbox', { name: 'Find a lesson', exact: true });
  await finder.fill('absent-lesson-marker-817');
  await page.getByText('No lessons match this search.', { exact: true }).waitFor();
  assert.equal(await lesson.count(), 0);
  await finder.fill('optional setup');
  await lesson.getByText('In use · v1', { exact: true }).waitFor();
  await finder.fill('twelve-field intake form');
  assert.equal(await lesson.count(), 0, 'private encounter text must not become a search index');
  await finder.fill('');
  await lesson.getByText('In use · v1', { exact: true }).waitFor();
  assert.match(await lesson.innerText(), /Test experience/);
  await lesson.getByText('When it applies', { exact: true }).click();
  assert.match(await lesson.innerText(), /AND \(The central value can be tried now OR A reversible sample is possible\)/);
  await lesson.getByText('Source experience', { exact: true }).click();
  assert.match(await lesson.innerText(), /twelve-field intake form/);
  if (screenshots) {
    await mkdir(screenshots, { recursive: true });
    await page.screenshot({ path: join(screenshots, 'posture-desktop.png') });
  }
  // Exercise the same sourced correction through the Chinese workspace UI.
  // The original lesson, encounter, source IDs, and user-authored content remain unchanged.
  await close();
  await page.getByRole('button', { name: 'Settings', exact: true }).click();
  await page.locator('#interface-language').selectOption('zh-CN');
  await page.getByRole('button', { name: '关闭对话框', exact: true }).click();
  await page.getByRole('button', { name: '设置', exact: true }).click();
  await page.getByRole('button', { name: '查看此页面的经验', exact: true }).click();
  await page.getByRole('dialog', { name: '工作中的经验', exact: true }).waitFor();
  await lesson.getByText('使用中 · v1', { exact: true }).waitFor();
  const chineseFinder = page.getByRole('searchbox', { name: '查找经验', exact: true });
  await chineseFinder.fill('optional setup');
  await lesson.getByText('使用中 · v1', { exact: true }).waitFor();
  await chineseFinder.fill('');
  await lesson.getByText('原始经历', { exact: true }).click();
  assert.match(await lesson.innerText(), /twelve-field intake form/);
  await lesson.getByRole('button', { name: '查看纠正', exact: true }).click();
  await page.getByRole('dialog').waitFor({ state: 'hidden' });
  await page.getByRole('button', { name: '设置', exact: true }).click();
  await page.locator('#interface-language').selectOption('en');
  await page.getByRole('button', { name: 'Close dialog', exact: true }).click();
  await page.getByRole('textbox', { name: /^Message / }).waitFor();
  const initialLesson = (await (await fetch(base + '/api/pages/home/posture')).json()).experiences[0];
  const revisionFeedback = 'When a practice session needs an existing booking, confirm that booking before starting. Keep optional profile questions after the first interaction.';
  await send(revisionFeedback, 3);
  await wait(async () => {
    const records = (await (await fetch(base + '/api/pages/home/posture')).json()).experiences;
    return records.length === 1 && records[0].id === initialLesson.id && records[0].revision === 2;
  }, 'ordinary correction revises the original lesson');
  await openLessons();
  await lesson.getByText('In use · v2', { exact: true }).waitFor();
  await lesson.getByText('When it applies', { exact: true }).click();
  await lesson.getByText('Confirm a required booking before participation.', { exact: true }).waitFor();
  await lesson.getByText('Source experience', { exact: true }).click();
  assert.match(await lesson.innerText(), /twelve-field intake form/);
  await lesson.getByText('Later feedback (1)', { exact: true }).click();
  await lesson.getByText(revisionFeedback, { exact: false }).waitFor();
  if (screenshots) await page.screenshot({ path: join(screenshots, 'posture-revised.png') });
  await lesson.getByRole('button', { name: 'Pause lesson', exact: true }).click();
  await lesson.getByText('Paused · v3', { exact: true }).waitFor();
  await stopServer(server);
  server = startServer();
  await wait(async () => { try { return (await fetch(base + '/api/health')).ok; } catch { return false; } }, 'server restarted');
  await page.reload();
  await openLessons();
  await lesson.getByText('Paused · v3', { exact: true }).waitFor();
  await lesson.getByText('Offer the practice interaction before optional setup, after any required booking check.', { exact: true }).waitFor();
  await lesson.getByRole('button', { name: 'Use again', exact: true }).click();
  await lesson.getByText('In use · v4', { exact: true }).waitFor();
  // The operator can correct the reusable abstraction itself without replacing
  // the source episode, opening a second journal, or changing shared authority.
  const beforeManualRefinement = (await (await fetch(base + '/api/pages/home/posture')).json()).experiences[0];
  const refinedLesson = 'Offer the practice interaction before optional setup, after any required booking check. Make the first step usable without optional profile questions.';
  const refinedExceptions = ['A required booking has not been confirmed.', 'The user explicitly requests a short registration form.'];
  const refinedShift = { ...beforeManualRefinement.shift,
    candidates: 'Offer a playable practice scene when free exploration is requested; retain short registration when it is the actual goal.',
    delivery: 'Ship a usable first practice exchange or, for explicit registration, a working minimal form.' };
  await lesson.getByRole('button', { name: 'Refine lesson', exact: true }).click();
  const newLesson = lesson.getByRole('textbox', { name: 'What should guide future work?', exact: true });
  const newExceptions = lesson.getByRole('textbox', { name: 'Where should this lesson not apply? (one per line)', exact: true });
  await newLesson.fill('');
  assert.equal(await lesson.getByRole('button', { name: 'Save refinement', exact: true }).isEnabled(), false);
  await newLesson.fill(refinedLesson);
  await newExceptions.fill(refinedExceptions.join('\n'));
  await lesson.getByText('Revise how this changes future choices', { exact: true }).click();
  const actionShift = lesson.getByRole('textbox', { name: 'Possible actions', exact: true });
  await actionShift.fill('');
  assert.equal(await lesson.getByRole('button', { name: 'Save refinement', exact: true }).isEnabled(), false);
  await actionShift.fill(refinedShift.candidates);
  await lesson.getByRole('textbox', { name: 'Ready to deliver', exact: true }).fill(refinedShift.delivery);
  const reportedRevision = 'The optional setup questions delayed the playable interaction in this Work.';
  await lesson.getByRole('textbox', { name: 'What prompted this change? (optional)', exact: true }).fill(reportedRevision);
  await lesson.getByRole('button', { name: 'Save refinement', exact: true }).click();
  await lesson.getByText('In use · v5', { exact: true }).waitFor();
  await lesson.getByText(refinedLesson, { exact: true }).waitFor();
  const afterManualRefinement = (await (await fetch(base + '/api/pages/home/posture')).json()).experiences[0];
  assert.equal(afterManualRefinement.id, beforeManualRefinement.id);
  assert.deepEqual(afterManualRefinement.source, beforeManualRefinement.source);
  assert.deepEqual(afterManualRefinement.encounter, beforeManualRefinement.encounter);
  assert.equal(afterManualRefinement.revision, beforeManualRefinement.revision + 1);
  assert.deepEqual(afterManualRefinement.counterexamples, refinedExceptions);
  assert.deepEqual(afterManualRefinement.shift, refinedShift);
  assert.equal(afterManualRefinement.feedback.length, beforeManualRefinement.feedback.length + 1);
  assert.equal(afterManualRefinement.feedback.at(-1).evidence, reportedRevision);
  assert.equal(afterManualRefinement.feedback.at(-1).source.kind, 'operator_described_refinement');
  await lesson.getByText('Later feedback (2)', { exact: true }).click();
  await lesson.getByText(reportedRevision, { exact: false }).waitFor();
  await lesson.getByRole('button', { name: 'Use in other pages', exact: true }).click();
  await lesson.getByText('In use · v6', { exact: true }).waitFor();
  await close();
  await page.getByRole('button', { name: 'Create a page', exact: true }).click();
  await page.getByRole('textbox', { name: 'Page name', exact: true }).fill('Registration tool');
  await page.getByRole('textbox', { name: 'Where should we begin?', exact: true }).fill('Maintain the standard registration fields.');
  await page.getByRole('button', { name: 'Create page', exact: true }).click();
  await page.getByRole('button', { name: 'Registration tool', exact: true }).waitFor();
  const unsentWorkDraft = 'Keep registration simple; review the original correction before changing the form.';
  await page.getByRole('textbox', { name: /^Message / }).fill(unsentWorkDraft);
  await openLessons();
  await lesson.getByText('Source experience', { exact: true }).click();
  assert.equal(await lesson.getByText('Later feedback (1)', { exact: true }).count(), 0);
  assert.equal(await lesson.getByText('twelve-field intake form', { exact: false }).count(), 0);
  await lesson.getByText(refinedLesson, { exact: true }).waitFor();
  assert.equal(await lesson.getByRole('button', { name: 'Pause lesson', exact: true }).count(), 0);
  assert.equal(await lesson.getByRole('button', { name: 'Refine lesson', exact: true }).count(), 0);
  const sharedView = (await (await fetch(base + '/api/pages/' + encodeURIComponent((await (await fetch(base + '/api/bootstrap')).json()).pages.find(item => item.title === 'Registration tool').id) + '/posture')).json()).experiences[0];
  assert.equal(sharedView.lesson, refinedLesson);
  assert.deepEqual(sharedView.counterexamples, refinedExceptions);
  assert.deepEqual(sharedView.shift, refinedShift);
  assert.equal(sharedView.source, undefined);
  assert.equal(sharedView.encounter, undefined);
  await lesson.getByRole('button', { name: 'Go to correction', exact: true }).click();
  await page.getByText('Let me try the interaction before asking for a profile.', { exact: true }).first().waitFor();
  // The source is Home, which must now offer a direct return to the receiving
  // Work. Reload on the source to prove the navigation origin survives refresh.
  await page.reload();
  await page.getByText('Let me try the interaction before asking for a profile.', { exact: true }).first().waitFor();
  await page.getByRole('button', { name: 'Back to conversation', exact: true }).click();
  await page.getByRole('button', { name: 'Registration tool', exact: true }).waitFor();
  assert.equal(await page.getByRole('textbox', { name: /^Message / }).inputValue(), unsentWorkDraft);
  await openLessons();
  await lesson.getByText('Source experience', { exact: true }).click();
  await lesson.getByRole('button', { name: 'Go to original result', exact: true }).click();
  await page.getByText('The first screen is a twelve-field intake form. [Explicit browser test fixture]', { exact: true }).waitFor();
  await page.getByRole('button', { name: 'Back to conversation', exact: true }).click();
  await page.getByRole('button', { name: 'Registration tool', exact: true }).waitFor();
  assert.equal(await page.getByRole('textbox', { name: /^Message / }).inputValue(), unsentWorkDraft);
  // Continue the original Home-side lesson management after proving both
  // sourced links return to the receiving Work without exposing raw feedback.
  await page.locator('.breadcrumbs').getByRole('button', { name: 'Home', exact: true }).click();
  await openLessons();
  const other = await page.context().newPage();
  await other.goto(base);
  await other.getByRole('button', { name: 'Settings', exact: true }).click();
  await other.getByRole('button', { name: 'View this page’s lessons', exact: true }).click();
  await other.getByRole('button', { name: 'Pause lesson', exact: true }).click();
  await other.getByText('Paused · v7', { exact: true }).waitFor();
  await other.close();
  await lesson.getByRole('button', { name: 'Pause lesson', exact: true }).click();
  await page.getByRole('alert').getByText('This lesson changed. Refresh it before saving.', { exact: false }).waitFor();
  await page.getByRole('button', { name: 'Refresh lessons', exact: true }).click();
  await lesson.getByText('Paused · v7', { exact: true }).waitFor();
  await lesson.getByRole('button', { name: 'Use again', exact: true }).click();
  await lesson.getByText('In use · v8', { exact: true }).waitFor();
  await lesson.getByRole('button', { name: 'Give feedback', exact: true }).click();
  assert.equal(await lesson.getByRole('button', { name: 'It hurt · pause', exact: true }).isEnabled(), false);
  await lesson.getByRole('textbox', { name: 'What changed in the result?', exact: true }).fill('A preview delayed the requested registration task.');
  await lesson.getByRole('button', { name: 'It hurt · pause', exact: true }).click();
  await lesson.getByText('Paused · v9', { exact: true }).waitFor();
  await lesson.getByText('Later feedback (3)', { exact: true }).click();
  assert.match(await lesson.innerText(), /A preview delayed/);
  await page.setViewportSize({ width: 390, height: 844 });
  const bounds = await page.getByRole('dialog').boundingBox();
  assert.ok(bounds && bounds.x >= 0 && bounds.x + bounds.width <= 390);
  if (screenshots) await page.screenshot({ path: join(screenshots, 'posture-mobile.png') });
  await page.keyboard.press('Escape');
  await page.getByRole('dialog').waitFor({ state: 'hidden' });
  await stopServer(server);
  await new Promise((resolve, reject) => {
    const seed = spawn(process.env.PER_PULSE_PYTHON || join(root, '.venv/bin/python'), [
      join(root, 'tests/browser/posture_server.py'), '--data-dir', directory, '--port', String(port),
      '--seed-later-messages', '505',
    ], { cwd: root, stdio: ['ignore', 'pipe', 'pipe'] });
    let output = '';
    seed.stdout.on('data', data => { output += data; });
    seed.stderr.on('data', data => { output += data; });
    seed.once('error', reject);
    seed.once('exit', code => code === 0 ? resolve() : reject(new Error(`Fixture history seed failed: ${output}`)));
  });
  server = startServer({ offline: true });
  await wait(async () => { try { return (await fetch(base + '/api/health')).ok; } catch { return false; } }, 'offline history reopened');
  await page.reload();
  await openLessons();
  await lesson.getByText('Source experience', { exact: true }).click();
  await lesson.getByRole('button', { name: 'Go to original result', exact: true }).click();
  await page.getByText('Showing the selected earlier source, then the most recent messages. Intervening messages are not loaded.', { exact: true }).waitFor();
  assert.equal(await page.locator('.message').count(), 501);
  await page.locator('.message').first().getByText('The first screen is a twelve-field intake form. [Explicit browser test fixture]', { exact: true }).waitFor();
  // The same bounded projection labels the selected source and the recent tail.
  const sourceIsInViewport = () => {
    const bounds = document.querySelector('.message')?.getBoundingClientRect();
    return bounds && bounds.top >= 0 && bounds.bottom <= window.innerHeight;
  };
  await page.waitForFunction(sourceIsInViewport);
  await page.reload();
  await page.getByText('Showing the selected earlier source, then the most recent messages. Intervening messages are not loaded.', { exact: true }).waitFor();
  assert.equal(await page.locator('.message').count(), 501);
  await page.waitForFunction(sourceIsInViewport);
  if (screenshots) await page.screenshot({ path: join(screenshots, 'posture-earlier-source.png') });
  await page.locator('.message').first().getByRole('button', { name: 'Link page', exact: true }).click();
  await page.getByRole('dialog').getByRole('option', { name: 'Registration tool', exact: false }).click();
  await page.getByRole('dialog').waitFor({ state: 'hidden' });
  await page.getByText('Showing the selected earlier source, then the most recent messages. Intervening messages are not loaded.', { exact: true }).waitFor();
  assert.equal(await page.locator('.message').count(), 501);

  // A retry uses the same refresh path as polling. Deliver its stale response
  // before the new navigation response in both directions on the same page.
  let readMode = 'normal';
  let heldReads = [];
  await page.route('**/api/pages/home*', async intercepted => {
    const request = intercepted.request();
    if (new URL(request.url()).pathname !== '/api/pages/home' || request.method() !== 'GET') {
      await intercepted.continue(); return;
    }
    if (readMode === 'fail') {
      readMode = 'normal';
      await intercepted.fulfill({ status: 503, contentType: 'application/json',
        body: JSON.stringify({ error: { code: 'browser_fixture_retry', message: 'Temporary browser fixture interruption.' } }) });
      return;
    }
    if (readMode === 'hold') {
      const response = await intercepted.fetch();
      const pending = { done: false };
      const gate = new Promise(resolve => { pending.release = resolve; });
      heldReads.push(pending);
      await gate;
      await intercepted.fulfill({ response });
      pending.done = true;
      return;
    }
    await intercepted.continue();
  });
  const openOldSource = async () => {
    await openLessons();
    await lesson.getByText('Source experience', { exact: true }).click();
    await lesson.getByRole('button', { name: 'Go to original result', exact: true }).click();
  };
  const openRecent = () => page.locator('.breadcrumbs').getByRole('button', { name: 'Home', exact: true }).click();
  for (const fromSource of [true, false]) {
    readMode = 'fail';
    await (fromSource ? openOldSource() : openRecent());
    await page.getByRole('alert').getByText('Temporary browser fixture interruption.', { exact: true }).waitFor();
    heldReads = []; readMode = 'hold';
    await page.getByRole('button', { name: 'Try again', exact: true }).click();
    await wait(() => heldReads.length === 1, 'old refresh request held');
    await (fromSource ? openRecent() : openOldSource());
    await wait(() => heldReads.length === 2, 'new navigation request held');
    heldReads[0].release();
    await wait(() => heldReads[0].done, 'old refresh response delivered');
    await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
    heldReads[1].release();
    await wait(() => heldReads[1].done, 'new navigation response delivered');
    readMode = 'normal';
    await wait(async () => await page.locator('.message').count() === (fromSource ? 500 : 501), 'latest navigation wins stale refresh');
    if (!fromSource) await page.waitForFunction(sourceIsInViewport);
  }
  await page.unroute('**/api/pages/home*');

  // A later Work can report the outcome of a shared lesson without returning
  // to the source to manage it. The source alone retains private feedback.
  await page.setViewportSize({ width: 1280, height: 900 });
  await page.locator('.breadcrumbs').getByRole('button', { name: 'Home', exact: true }).click();
  await openLessons();
  await lesson.getByRole('button', { name: 'Use again', exact: true }).click();
  await lesson.getByText('In use · v10', { exact: true }).waitFor();
  await close();
  await page.getByRole('button', { name: 'Registration tool', exact: true }).click();
  await page.getByRole('textbox', { name: /^Message / }).fill(unsentWorkDraft);
  await openLessons();
  await lesson.getByRole('button', { name: 'Give feedback', exact: true }).click();
  await lesson.getByText('Report what happened in this Work.', { exact: false }).waitFor();
  const receiverOutcome = 'The shared lesson added unnecessary onboarding in this registration Work.';
  await lesson.getByRole('textbox', { name: 'What changed in the result?', exact: true }).fill(receiverOutcome);
  await lesson.getByRole('button', { name: 'It hurt · pause', exact: true }).click();
  await lesson.getByText('Paused · v11', { exact: true }).waitFor();
  assert.equal(await lesson.getByRole('button', { name: 'Give feedback', exact: true }).count(), 0);
  const receiverPages = (await (await fetch(base + '/api/bootstrap')).json()).pages;
  const receiverId = receiverPages.find(item => item.title === 'Registration tool').id;
  const receivingLesson = (await (await fetch(base + '/api/pages/' + encodeURIComponent(receiverId) + '/posture')).json()).experiences[0];
  assert.equal(receivingLesson.canEdit, false);
  assert.equal(receivingLesson.canReport, false);
  assert.equal(receivingLesson.encounter, undefined);
  assert.equal(receivingLesson.feedback, undefined);
  assert.equal(receivingLesson.source, undefined);
  const originLesson = (await (await fetch(base + '/api/pages/home/posture')).json()).experiences[0];
  assert.equal(originLesson.revision, 11);
  assert.equal(originLesson.feedback.at(-1).evidence, receiverOutcome);
  assert.equal(originLesson.feedback.at(-1).source.page_id, receiverId);
  assert.equal(originLesson.feedback.at(-1).source.relation, 'shared_recipient');
  await close();
  assert.equal(await page.getByRole('textbox', { name: /^Message / }).inputValue(), unsentWorkDraft);
  // From the source journal, trace the later feedback to its actual Work,
  // then return to the original reading position without losing either draft.
  await page.locator('.breadcrumbs').getByRole('button', { name: 'Home', exact: true }).click();
  await openLessons();
  await lesson.getByText('Later feedback (4)', { exact: true }).click();
  await lesson.getByRole('button', { name: 'View reporting Work', exact: true }).click();
  await page.getByRole('button', { name: 'Registration tool', exact: true }).waitFor();
  assert.equal(await page.getByRole('textbox', { name: /^Message / }).inputValue(), unsentWorkDraft);
  await page.getByRole('button', { name: 'Back to conversation', exact: true }).click();
  await page.getByRole('button', { name: 'Home', exact: true }).waitFor();
  assert.deepEqual(errors, []);
  console.log('PASS: browser learning, ordinary and operator refinement of an attributed lesson, preserved source and boundaries across Work, restart and shared projection, concurrent revision recovery, receiving-Work outcome feedback without private source exposure, bilingual source navigation, and delayed-read races. Explicit synthetic transport only.');
} finally {
  await browser?.close();
  await stopServer(server);
  await rm(directory, { recursive: true, force: true });
}
