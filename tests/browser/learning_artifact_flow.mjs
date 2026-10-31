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
let diagnosticWork = null;
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
    await page.getByRole('button', { name: 'View this page’s experience', exact: true }).click();
    await page.getByRole('dialog', { name: 'Lessons and examples', exact: true }).waitFor();
  };
  const closeDialog = () => page.getByRole('button', { name: 'Close dialog', exact: true }).click();

  const originalId = await create('Original product brief', 'DESIGN_FIRST_VIEW');
  diagnosticWork = originalId;
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

  // The user explicitly asks for a form. The shared experience reaches the
  // native expert fork but should not displace that adequate simple solution.
  const registrationId = await create('Required registration', 'BUILD_FIXED_REGISTRATION_ARTIFACT');
  await page.getByText('Your approval is needed', { exact: true }).waitFor();
  assert.match(await page.locator('body').innerText(), /registration[.]txt/);
  const fixed = await status();
  assert.equal(fixed.fixedReviews.some(item => item.hasLesson
    && !item.leaksCorrection && !item.leaksOriginal), true);
  assert.equal(fixed.fixedMain.at(-1)?.hasLesson, false);
  assert.equal(fixed.files.includes('registration.txt'), false);
  await page.getByRole('button', { name: 'Allow once', exact: true }).click();
  await page.getByRole('button', { name: /registration[.]txt/ }).waitFor();
  await page.getByRole('button', { name: /registration[.]txt/ }).click();
  await page.getByText('Required registration fields.', { exact: true }).waitFor();
  assert.equal((await status()).files.includes('registration.txt'), true);
  checks.push('shared experience preserves an explicitly required form and approved real file');

  // Only the original Work can pause this shared lesson. The next ordinary
  // Work must lose its influence without receiving the private source history.
  await page.goto(base + '/page/' + originalId);
  await openLessons();
  await lesson.getByRole('button', { name: 'Pause lesson', exact: true }).click();
  await lesson.getByText('Paused · v3', { exact: true }).waitFor();
  await closeDialog();
  const withdrawnId = await create('After withdrawal', 'BUILD_PRACTICE_ARTIFACT');
  await page.getByText('Your approval is needed', { exact: true }).waitFor();
  assert.match(await page.locator('body').innerText(), /intake[.]txt/);
  const afterWithdrawal = await status();
  assert.equal(afterWithdrawal.tasks.at(-1)?.hasLesson, false);
  assert.equal(afterWithdrawal.tasks.at(-1)?.leaksCorrection, false);
  assert.equal(afterWithdrawal.tasks.at(-1)?.leaksOriginal, false);
  // Inactive history stays inspectable in the receiving Work; eligibility
  // for automatic action materials is separately fail-closed.
  const pausedProjection = await (await fetch(base + '/api/pages/' + withdrawnId + '/posture')).json();
  assert.equal(pausedProjection.experiences.length, 1);
  assert.equal(pausedProjection.experiences[0].active, false);
  assert.equal(pausedProjection.experiences[0].canEdit, false);
  assert.equal('encounter' in pausedProjection.experiences[0], false);
  await page.getByRole('button', { name: 'Decline', exact: true }).click();
  checks.push('source withdrawal removes influence from another Work without executing its file');

  await stopServer(server);
  server = startServer();
  await wait(async () => (await fetch(base + '/api/health')).ok, 'withdrawn lesson restart');
  const sourceAfterRestart = await (await fetch(base + '/api/pages/' + originalId + '/posture')).json();
  assert.equal(sourceAfterRestart.experiences[0].active, false);
  const targetAfterRestart = await (await fetch(base + '/api/pages/' + withdrawnId + '/posture')).json();
  assert.equal(targetAfterRestart.experiences.length, 1);
  assert.equal(targetAfterRestart.experiences[0].active, false);
  assert.equal('encounter' in targetAfterRestart.experiences[0], false);
  checks.push('inactive audit visibility, revoked influence and privacy survive SQLite restart');

  // A user may carry one real settled result into an existing Work without
  // copying the source transcript or losing either Work's unfinished draft.
  await page.goto(base + '/page/' + beforeId);
  await page.locator('.composer textarea').fill('Receiver draft stays here.');
  await page.goto(base + '/page/' + originalId);
  await page.locator('.composer textarea').fill('Original draft stays here.');
  const selectedSource = page.locator('.message-assistant').filter({
    hasText: 'The first screen is a twelve-field intake form. [Synthetic browser fixture]',
  }).first();
  // A DOM selection can contain rendered labels/Markdown not contiguous in
  // the native result. It must never silently expand into the full source.
  await page.evaluate(() => {
    const article = [...document.querySelectorAll('.message-assistant')]
      .find(node => node.textContent?.includes('The first screen is a twelve-field intake form. [Synthetic browser fixture]'));
    if (!article) throw new Error('Original settled result was not found');
    const selection = window.getSelection();
    const range = document.createRange();
    range.selectNodeContents(article);
    selection.removeAllRanges();
    selection.addRange(range);
    const button = [...article.querySelectorAll('button')]
      .find(node => node.textContent?.trim() === 'Use in another Work');
    if (!button) throw new Error('Result handoff action was not found');
    button.click();
  });
  const mismatchDialog = page.getByRole('dialog', { name: 'Carry this result into another Work', exact: true });
  await mismatchDialog.waitFor();
  assert.equal(await mismatchDialog.locator('#transfer-work-excerpt').inputValue(), '');
  await mismatchDialog.locator('#transfer-work-instruction').fill('Use only an exact quote.');
  assert.equal(await mismatchDialog.getByRole('button', { name: 'Send to Work', exact: true }).isDisabled(), true);
  await mismatchDialog.locator('#transfer-work-excerpt').fill('A fabricated quote');
  assert.equal(await mismatchDialog.getByRole('button', { name: 'Send to Work', exact: true }).isDisabled(), true);
  await mismatchDialog.getByRole('button', { name: 'Use entire source result', exact: true }).click();
  assert.match(await mismatchDialog.locator('#transfer-work-excerpt').inputValue(), /twelve-field intake form/);
  assert.equal(await mismatchDialog.getByRole('button', { name: 'Send to Work', exact: true }).isEnabled(), true);
  await mismatchDialog.getByRole('button', { name: 'Cancel', exact: true }).click();
  await page.evaluate(() => window.getSelection()?.removeAllRanges());
  checks.push('nonliteral rendered selection cannot silently disclose an entire Work result');

  // A completed native handoff must not steal Home/another Work if the user
  // navigates while the request is still in flight. Its event still persists
  // in the intended recipient, and both drafts remain locally owned.
  // Make a same-document Home → Work history entry. Browser Back from the
  // modal will then exercise the real popstate route without reloading the app
  // or aborting its pending native request.
  await page.locator('nav.breadcrumbs .crumb-ancestor').first().click();
  await page.getByRole('textbox', { name: 'Message Home', exact: true }).waitFor();
  await page.locator('nav.page-nav a.nav-row').filter({ hasText: 'Original product brief' }).click();
  await page.getByRole('heading', { name: 'Original product brief', exact: true }).waitFor();
  const delayedTransferUrl = `**/api/pages/${originalId}/transfer`;
  let releaseTransfer;
  let notifyIntercepted;
  const interceptedTransfer = new Promise(resolve => { notifyIntercepted = resolve; });
  const transferGate = new Promise(resolve => { releaseTransfer = resolve; });
  await page.route(delayedTransferUrl, async route => {
    notifyIntercepted();
    await transferGate;
    await route.continue();
  });
  await selectedSource.getByRole('button', { name: 'Use in another Work', exact: true }).click();
  const pendingDialog = page.getByRole('dialog', { name: 'Carry this result into another Work', exact: true });
  await pendingDialog.locator('#transfer-work-target').selectOption(beforeId);
  await pendingDialog.locator('#transfer-work-instruction').fill('Carry this finding without moving my current page.');
  await pendingDialog.getByRole('button', { name: 'Send to Work', exact: true }).click();
  await interceptedTransfer;
  await page.goBack();
  await page.getByRole('textbox', { name: 'Message Home', exact: true }).waitFor();
  const lateResponse = page.waitForResponse(response =>
    response.url().includes(`/api/pages/${originalId}/transfer`) && response.request().method() === 'POST');
  releaseTransfer();
  assert.equal((await lateResponse).status(), 201); // Native transfer creates a sourced user event.
  await wait(async () => {
    const recipient = await (await fetch(base + '/api/pages/' + beforeId)).json();
    return recipient.messages.some(message => message.sourcePageId === originalId &&
      message.content?.includes('Carry this finding without moving my current page.'));
  }, 'late sourced transfer persists in intended Work');
  await page.waitForTimeout(200);
  assert.equal(new URL(page.url()).pathname, '/', 'late transfer must not hijack the current Home route');
  await page.unroute(delayedTransferUrl);
  await page.goto(base + '/page/' + originalId);
  await page.getByRole('heading', { name: 'Original product brief', exact: true }).waitFor();
  assert.equal(await page.locator('.composer textarea').inputValue(), 'Original draft stays here.');
  checks.push('late settled transfer stays in receiving Work without hijacking user navigation');

  await selectedSource.getByRole('button', { name: 'Use in another Work', exact: true }).click();
  const transferDialog = page.getByRole('dialog', { name: 'Carry this result into another Work', exact: true });
  await transferDialog.waitFor();
  await transferDialog.locator('#transfer-work-target').selectOption(beforeId);
  assert.match(await transferDialog.locator('#transfer-work-excerpt').inputValue(), /twelve-field intake form/);
  await transferDialog.locator('#transfer-work-instruction').fill('Use this precise finding to improve the next draft.');
  await transferDialog.getByRole('button', { name: 'Send to Work', exact: true }).click();
  await page.getByRole('heading', { name: 'Uninformed delivery', exact: true }).waitFor();
  assert.equal(await page.locator('.composer textarea').inputValue(), 'Receiver draft stays here.');
  const imported = page.locator('.message-user').filter({
    hasText: 'Selected excerpt from another Work',
  }).last();
  const sourceLink = imported.locator('.source-line a.session-link');
  await sourceLink.waitFor();
  assert.match(await imported.innerText(), /Use this precise finding to improve the next draft/);
  assert.ok(!(await imported.innerText()).includes('The intake form hides the product'),
    'Only the selected result, not the private correction, reaches the receiving Work');
  await sourceLink.click();
  await page.getByRole('heading', { name: 'Original product brief', exact: true }).waitFor();
  assert.equal(await page.locator('.composer textarea').inputValue(), 'Original draft stays here.');
  const sourceResults = (await (await fetch(base + '/api/pages/' + beforeId)).json()).messages;
  assert.ok(sourceResults.some(m => m.sourcePageId === originalId && m.sourceMessageId),
    'The native receiving message preserves the original Work result source');
  checks.push('explicit settled Work excerpt changes another Work with exact source and both drafts intact');
  assert.deepEqual(errors, []);
  console.log(JSON.stringify({
    passed: checks.length, checks, sourceWork: originalId, comparisonWork: beforeId,
    resultWork: learnedId,
    provider: 'explicit synthetic HTTP model; native World/Kernel/SQLite/Posture/Harness/files',
  }));
} catch (error) {
  console.error(error.stack, '\nSERVER:\n', serverLog);
  const diagnostics = {};
  for (const [key, path] of [
    ['fixture', '/__journey/status'],
    ...(diagnosticWork ? [
      ['work', '/api/pages/' + diagnosticWork],
      ['execution', '/api/pages/' + diagnosticWork + '/execution'],
      ['posture', '/api/pages/' + diagnosticWork + '/posture'],
    ] : []),
  ]) {
    try { diagnostics[key] = await (await fetch(base + path)).json(); }
    catch (fetchError) { diagnostics[key] = String(fetchError); }
  }
  console.error('Synthetic fixture diagnostics:', JSON.stringify(diagnostics).slice(0, 16000));
  process.exitCode = 1;
} finally {
  await browser?.close();
  await stopServer(server);
  await rm(directory, { recursive: true, force: true });
}
