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
// In distribution acceptance, isolate Python imports from the checkout: the
// native service and Kernel must come from the installed wheel, while this
// script supplies only the explicitly synthetic HTTP provider fixture.
const installedPython = process.env.PER_PULSE_INSTALLED_PYTHON;
const startServer = () => {
  const child = spawn(installedPython || process.env.PER_PULSE_PYTHON || join(root, '.venv/bin/python'), [
    ...(installedPython ? ['-I'] : []),
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
  await page.getByRole('button', { name: 'Activity', exact: true }).click();
  const allowance = page.getByRole('region', { name: 'Autonomous provider requests', exact: true });
  await allowance.getByText('Autonomous model requests are disabled.', { exact: true }).waitFor();
  await allowance.getByRole('spinbutton', { name: 'Requests per week', exact: true }).fill('3');
  await allowance.getByRole('button', { name: 'Save limit', exact: true }).click();
  assert.equal((await fetch(base + '/api/pulse/weekly-usage', {
    method: 'PATCH', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ enabled: true, weeklyLimit: 3 }),
  })).status, 200);
  await page.reload();
  await page.getByRole('button', { name: 'Activity', exact: true }).click();
  await allowance.getByText('Used 0 of 3 requests this week (0 reserved, 3 available).', { exact: true }).waitFor();
  assert.equal((await (await fetch(base + '/api/pulse/weekly-usage')).json()).usage.enabled, true);
  checks.push('rendered Activity saves a finite limit and reads an explicitly enabled autonomous allowance');
  await page.reload();
  await page.getByRole('button', { name: 'Activity', exact: true }).click();
  await allowance.getByText('Used 0 of 3 requests this week (0 reserved, 3 available).', { exact: true }).waitFor();
  await allowance.getByRole('button', { name: 'Pause autonomous practice', exact: true }).click();
  await allowance.getByText('Autonomous model requests are disabled.', { exact: true }).waitFor();
  const heldAllowance = (await (await fetch(base + '/api/pulse/weekly-usage')).json()).usage;
  assert.equal(heldAllowance.enabled, false);
  assert.equal(heldAllowance.weekly_limit, 3);
  assert.equal(heldAllowance.consumed, 0);
  checks.push('allowance survives reload and a person can stop new sends without erasing its limit');
  await page.getByRole('button', { name: 'Close dialog', exact: true }).click();
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
  await lesson.getByRole('button', { name: 'Keep in this page', exact: true }).click();
  await lesson.getByText('In use · v2', { exact: true }).waitFor();
  assert.equal(await lesson.getByRole('button', { name: 'Start a Work from this saved experience', exact: true }).count(), 0,
    'A locally held correction cannot be a cross-Work starting point.');
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
  await lesson.getByText('In use · v3', { exact: true }).waitFor();
  const selectedCorrection = (await (await fetch(base + '/api/pages/' + originalId + '/posture')).json()).experiences[0];
  assert.equal(selectedCorrection.source.interpretation, 'model_proposed_lesson_from_user_followup');
  await lesson.getByRole('button', { name: 'Start a Work from this saved experience', exact: true }).click();
  await page.locator('#new-page-title').fill('Correction-guided follow-up');
  await page.locator('#new-page-focus').fill('Review the initial interaction in a fresh Work.');
  await page.getByRole('button', { name: 'Create page', exact: true }).click();
  await page.getByRole('heading', { name: 'Correction-guided follow-up', exact: true }).waitFor();
  const selectedWorkId = new URL(page.url()).pathname.split('/').pop();
  const selectedView = await (await fetch(base + '/api/pages/' + selectedWorkId)).json();
  const selectedInput = selectedView.messages.find(message => message.content?.includes('LESSON_EXPERIENCE_JSON')).content;
  const selectedMaterial = JSON.parse(selectedInput.split('LESSON_EXPERIENCE_JSON\n')[1]
    .split('\n\nMy request for this Work:')[0]);
  assert.equal(selectedMaterial.id, selectedCorrection.id);
  assert.equal(selectedMaterial.revision, selectedCorrection.revision);
  assert.deepEqual(selectedMaterial.conditions, selectedCorrection.conditions);
  assert.deepEqual(selectedMaterial.counterexamples, selectedCorrection.counterexamples);
  assert.deepEqual(selectedMaterial.shift, selectedCorrection.shift);
  assert.equal(selectedMaterial.record_type, selectedCorrection.record_type);
  assert.equal(selectedView.page.sourcePageId, originalId);
  assert.deepEqual(selectedView.sourceExperience, {
    id: selectedCorrection.id, revision: selectedCorrection.revision,
    currentRevision: selectedCorrection.revision, status: 'current',
  });
  await page.locator('.source-experience-state').getByText(
    'This Work began from saved experience v' + selectedCorrection.revision + '.', { exact: false },
  ).waitFor();
  assert.equal(await page.getByRole('link', { name: 'Review source Work', exact: true }).count(), 1);
  assert.ok(!selectedInput.includes('The intake form hides the product.'),
    'The user correction remains in its original Work.');
  assert.ok(!selectedInput.includes('The first screen is a twelve-field intake form.'),
    'The earlier private result remains in its original Work.');
  await page.goto(base + '/page/' + originalId);
  checks.push('rendered lesson-to-Work handoff carries attributed conditions but not the original conversation');
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

  // Carry the actual successful native file action, not just its matching
  // pathname, to Home. The reference is a verified unsent material, and Back
  // must recover the Work draft and the original opened result.
  const originalArtifact = (await (await fetch(base + '/api/pages/' + learnedId)).json())
    .artifacts.find(item => item.path === 'practice.txt');
  assert.ok(originalArtifact?.id);
  await page.getByRole('button', { name: 'Add full file to Home draft', exact: true }).click();
  await page.getByRole('textbox', { name: 'Message Home', exact: true }).waitFor();
  const attachedHome = (await (await fetch(base + '/api/pages/home/materials')).json()).materials;
  const sourcedFile = attachedHome.find(item => item.originArtifactId === originalArtifact.id);
  assert.ok(sourcedFile);
  assert.equal(sourcedFile.originArtifactPageId, learnedId);
  assert.equal(sourcedFile.sha256, originalArtifact.sha256);
  assert.match(await page.locator('.composer textarea').inputValue(), /practice[.]txt/);
  const homeUnsentDraft = await page.locator('.composer textarea').inputValue();
  // Inspect the exact file and native origin before sending anything from Home.
  await page.getByRole('button', { name: 'Inspect practice.txt before sending', exact: true }).click();
  const attachedPreview = page.getByRole('dialog', { name: 'Source files', exact: true });
  await attachedPreview.getByText('A playable first interaction.', { exact: true }).waitFor();
  assert.match(await attachedPreview.innerText(), /Produced in Informed delivery/);
  assert.match(await attachedPreview.innerText(), new RegExp(originalArtifact.id.slice(0, 12)));
  assert.equal(await attachedPreview.getByRole('button', {
    name: 'Open the Work that produced this file', exact: true,
  }).isVisible(), true);
  await attachedPreview.getByRole('button', { name: 'Close dialog', exact: true }).click();
  assert.equal(await page.locator('.composer textarea').inputValue(), homeUnsentDraft);
  checks.push('unsent Home attachment opens exact file and producing action without sending or losing draft');
  await page.goBack();
  await page.getByRole('heading', { name: 'Informed delivery', exact: true }).waitFor();
  assert.equal(await page.locator('.composer textarea').inputValue(), draft);
  checks.push('generated output reaches Home draft with native action provenance and Work focus restored');

  if (screenshots) {
    await mkdir(screenshots, { recursive: true });
    await page.screenshot({ path: join(screenshots, 'journey-approved-artifact.png') });
  }

  const result = page.locator('.message-assistant').filter({
    hasText: 'The file task finished. [Synthetic browser fixture]',
  }).last();
  // A rendered selection spanning formatting and controls is not verbatim
  // source text. The Home return must not silently disclose the full reply.
  // Locator.evaluate waits for the settled result to appear; an immediate
  // page.evaluate can race the last native response and miss this message.
  await result.evaluate(article => {
    const selection = window.getSelection();
    const range = document.createRange();
    range.selectNodeContents(article);
    selection.removeAllRanges();
    selection.addRange(range);
    const button = [...article.querySelectorAll('button')]
      .find(node => node.textContent?.trim() === 'Bring to Home');
    if (!button) throw new Error('Home return action was not found');
    button.click();
  });
  const dialog = page.getByRole('dialog', { name: 'Bring this back to Home.', exact: true });
  await dialog.waitFor();
  assert.equal(await dialog.locator('#return-content').inputValue(), '');
  assert.equal(await dialog.getByRole('button', { name: 'Bring to Home', exact: true }).isDisabled(), true);
  await dialog.getByRole('button', { name: 'Use entire source result', exact: true }).click();
  assert.match(await dialog.locator('#return-content').inputValue(), /The file task finished/);
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
  checks.push('nonliteral Home selection requires explicit full-result consent, then restores sourced Work draft');

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
  await lesson.getByText('Paused · v4', { exact: true }).waitFor();
  await closeDialog();
  await page.goto(base + '/page/' + selectedWorkId);
  await page.getByRole('heading', { name: 'Correction-guided follow-up', exact: true }).waitFor();
  await page.getByText(
    'The selected experience (v3) was paused or kept local.', { exact: false },
  ).waitFor();
  const selectedAfterPause = await (await fetch(base + '/api/pages/' + selectedWorkId)).json();
  assert.deepEqual(selectedAfterPause.sourceExperience, {
    id: selectedCorrection.id, revision: 3, currentRevision: 4, status: 'withdrawn',
  });
  await page.getByRole('link', { name: 'Review source Work', exact: true }).click();
  await page.getByRole('heading', { name: 'Original product brief', exact: true }).waitFor();
  checks.push('withdrawn source is flagged in its already-created Work and links back to its owner');
  const withdrawnId = await create('After withdrawal', 'BUILD_PRACTICE_ARTIFACT');
  await page.getByText('Your approval is needed', { exact: true }).waitFor();
  assert.match(await page.locator('body').innerText(), /intake[.]txt/);
  const afterWithdrawal = await status();
  assert.equal(afterWithdrawal.tasks.at(-1)?.hasLesson, false);
  assert.equal(afterWithdrawal.tasks.at(-1)?.leaksCorrection, false);
  assert.equal(afterWithdrawal.tasks.at(-1)?.leaksOriginal, false);
  // An unreported receiving Work does not retain withdrawn guidance in
  // its experience pane. Only the original Work can inspect the inactive
  // history; earlier recipients with their own feedback retain that record.
  const pausedProjection = await (await fetch(base + '/api/pages/' + withdrawnId + '/posture')).json();
  assert.equal(pausedProjection.experiences.length, 0);
  await page.getByRole('button', { name: 'Decline', exact: true }).click();
  checks.push('source withdrawal removes influence from another Work without executing its file');

  await stopServer(server);
  server = startServer();
  await wait(async () => (await fetch(base + '/api/health')).ok, 'withdrawn lesson restart');
  const sourceAfterRestart = await (await fetch(base + '/api/pages/' + originalId + '/posture')).json();
  assert.equal(sourceAfterRestart.experiences[0].active, false);
  const selectedAfterRestart = await (await fetch(base + '/api/pages/' + selectedWorkId)).json();
  assert.equal(selectedAfterRestart.sourceExperience.status, 'withdrawn');
  assert.equal(selectedAfterRestart.sourceExperience.currentRevision, 4);
  await page.goto(base + '/page/' + selectedWorkId);
  await page.getByText('The selected experience (v3) was paused or kept local.', { exact: false }).waitFor();
  const targetAfterRestart = await (await fetch(base + '/api/pages/' + withdrawnId + '/posture')).json();
  assert.equal(targetAfterRestart.experiences.length, 0);
  checks.push('source-owned inactive audit, no revoked recipient visibility, and privacy survive SQLite restart');

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
  // the native result. It is kept as the person selected it, marked edited.
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
      .find(node => node.textContent?.trim() === 'Use in another page');
    if (!button) throw new Error('Result handoff action was not found');
    button.click();
  });
  const mismatchDialog = page.getByRole('dialog', { name: 'Carry this result into another page', exact: true });
  await mismatchDialog.waitFor();
  const editedNote = mismatchDialog.getByText('Edited from the original. The link to its source stays.', { exact: true });
  const sendButton = mismatchDialog.getByRole('button', { name: 'Send to page', exact: true });
  assert.match(await mismatchDialog.locator('#transfer-work-excerpt').inputValue(), /twelve-field intake form/);
  await editedNote.waitFor();
  await mismatchDialog.locator('#transfer-work-instruction').fill('Use this note.');
  assert.equal(await sendButton.isEnabled(), true);
  await mismatchDialog.locator('#transfer-work-excerpt').fill('');
  assert.equal(await sendButton.isDisabled(), true, 'an empty excerpt cannot be sent');
  await mismatchDialog.getByRole('button', { name: 'Use entire source result', exact: true }).click();
  assert.match(await mismatchDialog.locator('#transfer-work-excerpt').inputValue(), /twelve-field intake form/);
  assert.equal(await editedNote.count(), 0, 'the exact result is not marked edited');
  assert.equal(await sendButton.isEnabled(), true);
  await mismatchDialog.getByRole('button', { name: 'Cancel', exact: true }).click();
  await page.evaluate(() => window.getSelection()?.removeAllRanges());
  checks.push('a rendered selection is offered as an edited excerpt; the exact result is one click away');

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
  await selectedSource.getByRole('button', { name: 'Use in another page', exact: true }).click();
  const pendingDialog = page.getByRole('dialog', { name: 'Carry this result into another page', exact: true });
  await pendingDialog.locator('#transfer-work-target').selectOption(beforeId);
  await pendingDialog.locator('#transfer-work-instruction').fill('Carry this finding without moving my current page.');
  await pendingDialog.getByRole('button', { name: 'Send to page', exact: true }).click();
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

  // Home return has the same native-settlement/foreground-navigation boundary
  // as a Work handoff. A user may leave during the POST: Home must receive the
  // exact sourced result, while their newer route and unfinished draft stay put.
  await page.locator('nav.breadcrumbs .crumb-ancestor').first().click();
  await page.getByRole('textbox', { name: 'Message Home', exact: true }).waitFor();
  const pendingHomeDraft = 'Continue writing in Home while a source return settles.';
  await page.locator('.composer textarea').fill(pendingHomeDraft);
  await page.locator('nav.page-nav a.nav-row').filter({ hasText: 'Original product brief' }).click();
  await page.getByRole('heading', { name: 'Original product brief', exact: true }).waitFor();
  const delayedReturnUrl = `**/api/pages/${originalId}/return`;
  let releaseReturn;
  let notifyReturnIntercepted;
  const interceptedReturn = new Promise(resolve => { notifyReturnIntercepted = resolve; });
  const returnGate = new Promise(resolve => { releaseReturn = resolve; });
  await page.route(delayedReturnUrl, async route => {
    notifyReturnIntercepted();
    await returnGate;
    await route.continue();
  });
  await selectedSource.getByRole('button', { name: 'Bring to Home', exact: true }).click();
  const pendingReturnDialog = page.getByRole('dialog', { name: 'Bring this back to Home.', exact: true });
  await pendingReturnDialog.locator('#return-content')
    .fill('The first screen is a twelve-field intake form. [Synthetic browser fixture]');
  await pendingReturnDialog.getByRole('button', { name: 'Bring to Home', exact: true }).click();
  await interceptedReturn;
  await page.goBack();
  await page.getByRole('textbox', { name: 'Message Home', exact: true }).waitFor();
  const lateHomeResponse = page.waitForResponse(response =>
    response.url().includes(`/api/pages/${originalId}/return`) && response.request().method() === 'POST');
  releaseReturn();
  assert.equal((await lateHomeResponse).status(), 201);
  await wait(async () => {
    const homeView = await (await fetch(base + '/api/pages/home')).json();
    return homeView.messages.some(message => message.sourcePageId === originalId
      && message.content?.includes('The first screen is a twelve-field intake form.'));
  }, 'late native return persists with source in Home');
  await page.waitForTimeout(200);
  assert.equal(new URL(page.url()).pathname, '/', 'late Home return must not hijack a newer route');
  assert.equal(await page.locator('.composer textarea').inputValue(), pendingHomeDraft,
    'The Home draft survives the completed return');
  await page.unroute(delayedReturnUrl);
  await page.goto(base + '/page/' + originalId);
  await page.getByRole('heading', { name: 'Original product brief', exact: true }).waitFor();
  assert.equal(await page.locator('.composer textarea').inputValue(), 'Original draft stays here.');
  checks.push('late native Home return keeps its source without stealing the active route or drafts');

  // A committed Home input with a lost HTTP receipt must not produce a second
  // native stimulus when the same return dialog retries. The original source
  // link, Home draft and Work A draft remain usable.
  await selectedSource.getByRole('button', { name: 'Bring to Home', exact: true }).click();
  const retryReturnDialog = page.getByRole('dialog', { name: 'Bring this back to Home.', exact: true });
  const retryReturnText = 'Home return retry must keep a single sourced input. [Synthetic browser fixture]';
  await retryReturnDialog.locator('#return-content').fill(retryReturnText);
  const retryReturnUrl = `**/api/pages/${originalId}/return`;
  const returnAttempts = [];
  await page.route(retryReturnUrl, async route => {
    returnAttempts.push(route.request().postDataJSON());
    if (returnAttempts.length === 1) {
      const committed = await route.fetch();
      assert.equal(committed.status(), 201);
      await route.fulfill({ status: 503, contentType: 'application/json',
        body: '{"error":{"code":"network_error","message":"The Home receipt was lost. Try again."}}' });
    } else await route.continue();
  });
  await retryReturnDialog.getByRole('button', { name: 'Bring to Home', exact: true }).click();
  await retryReturnDialog.locator('.form-error').waitFor();
  await retryReturnDialog.getByRole('button', { name: 'Bring to Home', exact: true }).click();
  await page.getByRole('heading', { name: 'A place to keep going.', exact: true }).waitFor();
  await page.unroute(retryReturnUrl);
  assert.equal(returnAttempts.length, 2);
  assert.ok(returnAttempts[0].requestId
    && returnAttempts[0].requestId === returnAttempts[1].requestId,
  'A retry keeps the same native Home return identity');
  const retriedHome = (await (await fetch(base + '/api/pages/home')).json()).messages
    .filter(message => message.content.includes(retryReturnText) && message.sourcePageId === originalId);
  assert.equal(retriedHome.length, 1, 'One native Home input survives the lost receipt and retry');
  assert.equal(retriedHome[0].sourceMessageId, returnAttempts[0].messageId);
  assert.equal(retriedHome[0].references.filter(ref => ref.kind === 'return').length, 1);
  assert.equal(await page.locator('.composer textarea').inputValue(), pendingHomeDraft);
  await page.goto(base + '/page/' + originalId);
  await page.getByRole('heading', { name: 'Original product brief', exact: true }).waitFor();
  assert.equal(await page.locator('.composer textarea').inputValue(), 'Original draft stays here.');
  checks.push('lost Home return HTTP receipt retries one native input and retains source and both drafts');

  // Ordinary composer Send (not a specialized return/transfer dialog) must
  // survive native admission followed by a lost HTTP response and browser
  // restart. The original Work and Home continue from one input each.
  const ordinaryWorkId = await create('Ordinary send continuation', 'Prepare an ordinary Work task.');
  const ordinaryWorkText = 'Keep the corrected source and finish one ordinary Work decision.';
  const ordinaryWorkAttempts = [];
  let notifyOrdinaryWorkLost;
  const ordinaryWorkLost = new Promise(resolve => { notifyOrdinaryWorkLost = resolve; });
  const ordinaryWorkUrl = `**/api/pages/${ordinaryWorkId}/messages`;
  await page.route(ordinaryWorkUrl, async route => {
    ordinaryWorkAttempts.push(route.request().postDataJSON());
    if (ordinaryWorkAttempts.length === 1) {
      const committed = await route.fetch();
      assert.equal(committed.status(), 202);
      await route.fulfill({ status: 503, contentType: 'application/json',
        body: '{"error":{"code":"network_error","message":"Ordinary Work receipt lost. Try again."}}' });
      notifyOrdinaryWorkLost();
    } else await route.continue();
  });
  await page.locator('.composer textarea').fill(ordinaryWorkText);
  await page.getByRole('button', { name: 'Send message', exact: true }).click();
  await ordinaryWorkLost;
  assert.equal(await page.locator('.composer textarea').inputValue(), ordinaryWorkText);
  await page.reload();
  await page.getByRole('heading', { name: 'Ordinary send continuation', exact: true }).waitFor();
  assert.equal(await page.locator('.composer textarea').inputValue(), ordinaryWorkText);
  await page.getByRole('button', { name: 'Send message', exact: true }).click();
  await wait(async () => (await page.locator('.composer textarea').inputValue()) === '',
    'ordinary Work retry clears the confirmed draft');
  await page.unroute(ordinaryWorkUrl);
  assert.equal(ordinaryWorkAttempts.length, 2);
  assert.ok(ordinaryWorkAttempts[0].requestId
    && ordinaryWorkAttempts[0].requestId === ordinaryWorkAttempts[1].requestId,
    'A reloaded Work draft retries the original native identity');
  const ordinaryWorkMessages = (await (await fetch(base + '/api/pages/' + ordinaryWorkId)).json()).messages;
  assert.equal(ordinaryWorkMessages.filter(message => message.role === 'user'
    && message.content === ordinaryWorkText).length, 1);
  checks.push('ordinary Work Send recovers its native input after 503 and browser restart, without duplicate work');

  await page.goto(base + '/');
  await page.getByRole('textbox', { name: 'Message Home', exact: true }).waitFor();
  const ordinaryHomeText = 'Continue in Home once after a network receipt disappears.';
  const ordinaryHomeAttempts = [];
  let notifyOrdinaryHomeLost;
  const ordinaryHomeLost = new Promise(resolve => { notifyOrdinaryHomeLost = resolve; });
  const ordinaryHomeUrl = '**/api/pages/home/messages';
  await page.route(ordinaryHomeUrl, async route => {
    ordinaryHomeAttempts.push(route.request().postDataJSON());
    if (ordinaryHomeAttempts.length === 1) {
      const committed = await route.fetch();
      assert.equal(committed.status(), 202);
      await route.fulfill({ status: 503, contentType: 'application/json',
        body: '{"error":{"code":"network_error","message":"Ordinary Home receipt lost. Try again."}}' });
      notifyOrdinaryHomeLost();
    } else await route.continue();
  });
  await page.locator('.composer textarea').fill(ordinaryHomeText);
  await page.getByRole('button', { name: 'Send message', exact: true }).click();
  await ordinaryHomeLost;
  await page.reload();
  await page.getByRole('textbox', { name: 'Message Home', exact: true }).waitFor();
  assert.equal(await page.locator('.composer textarea').inputValue(), ordinaryHomeText);
  await page.getByRole('button', { name: 'Send message', exact: true }).click();
  await wait(async () => (await page.locator('.composer textarea').inputValue()) === '',
    'ordinary Home retry clears the confirmed draft');
  await page.unroute(ordinaryHomeUrl);
  assert.equal(ordinaryHomeAttempts.length, 2);
  assert.ok(ordinaryHomeAttempts[0].requestId
    && ordinaryHomeAttempts[0].requestId === ordinaryHomeAttempts[1].requestId);
  const ordinaryHomeMessages = (await (await fetch(base + '/api/pages/home')).json()).messages;
  // Home may carry verified draft attachments; native material footers extend
  // the visible instruction without changing the one accepted user event.
  assert.equal(ordinaryHomeMessages.filter(message => message.role === 'user'
    && message.content.startsWith(ordinaryHomeText)).length, 1);
  await page.goto(base + '/page/' + originalId);
  await page.getByRole('heading', { name: 'Original product brief', exact: true }).waitFor();
  assert.equal(await page.locator('.composer textarea').inputValue(), 'Original draft stays here.');
  checks.push('ordinary Home Send retries once after 503/reload and preserves the unrelated source Work draft');

  await selectedSource.getByRole('button', { name: 'Use in another page', exact: true }).click();
  const transferDialog = page.getByRole('dialog', { name: 'Carry this result into another page', exact: true });
  await transferDialog.waitFor();
  await transferDialog.locator('#transfer-work-target').selectOption(beforeId);
  assert.match(await transferDialog.locator('#transfer-work-excerpt').inputValue(), /twelve-field intake form/);
  await transferDialog.locator('#transfer-work-instruction').fill('Use this precise finding to improve the next draft.');
  // The first request reaches the native World, but its HTTP response is
  // deliberately lost. Repeating Send in the same dialog must reuse the
  // original request identity, not schedule a second Work input.
  const retryTransferUrl = `**/api/pages/${originalId}/transfer`;
  const transferAttempts = [];
  await page.route(retryTransferUrl, async route => {
    transferAttempts.push(route.request().postDataJSON());
    if (transferAttempts.length === 1) {
      const committed = await route.fetch();
      assert.equal(committed.status(), 201);
      await route.fulfill({ status: 503, contentType: 'application/json',
        body: '{"error":{"code":"network_error","message":"The transfer response was lost. Try again."}}' });
    } else await route.continue();
  });
  await transferDialog.getByRole('button', { name: 'Send to page', exact: true }).click();
  await transferDialog.locator('.form-error').waitFor();
  await transferDialog.getByRole('button', { name: 'Send to page', exact: true }).click();
  await page.getByRole('heading', { name: 'Uninformed delivery', exact: true }).waitFor();
  await page.unroute(retryTransferUrl);
  assert.equal(transferAttempts.length, 2);
  assert.ok(transferAttempts[0].requestId && transferAttempts[0].requestId === transferAttempts[1].requestId,
    'A retry keeps one native transfer identity');
  assert.equal(await page.locator('.composer textarea').inputValue(), 'Receiver draft stays here.');
  const imported = page.locator('.message-user').filter({
    has: page.locator('.transfer-presentation'),
  }).last();
  const sourceLink = imported.locator('.source-line a.session-link');
  await sourceLink.waitFor();
  const sourceSection = imported.locator('.transfer-presentation-source');
  const requestSection = imported.locator('.transfer-presentation-request');
  assert.match(await sourceSection.innerText(), /Selected source excerpt/);
  assert.match(await sourceSection.innerText(), /twelve-field intake form/);
  assert.match(await requestSection.innerText(), /Your request for this page/);
  assert.match(await requestSection.innerText(), /Use this precise finding to improve the next draft/);
  assert.ok(!(await imported.innerText()).includes('SOURCE_EXCERPT_JSON'),
    'Rendered transfer must not expose raw transport JSON to the reader');
  assert.ok(!(await imported.innerText()).includes('The intake form hides the product'),
    'Only the selected result, not the private correction, reaches the receiving Work');
  await page.getByRole('button', { name: 'Settings', exact: true }).click();
  await page.locator('#interface-language').selectOption('zh-CN');
  await page.getByRole('button', { name: '关闭对话框', exact: true }).click();
  assert.match(await sourceSection.innerText(), /选定的来源原文/);
  assert.match(await requestSection.innerText(), /你对这项工作的请求/);
  assert.equal(await page.locator('.composer textarea').inputValue(), 'Receiver draft stays here.');
  await page.getByRole('button', { name: '设置', exact: true }).click();
  await page.locator('#interface-language').selectOption('en');
  await closeDialog();
  await sourceLink.click();
  await page.getByRole('heading', { name: 'Original product brief', exact: true }).waitFor();
  assert.equal(await page.locator('.composer textarea').inputValue(), 'Original draft stays here.');
  const sourceResults = (await (await fetch(base + '/api/pages/' + beforeId)).json()).messages;
  assert.equal(sourceResults.filter(m => m.sourcePageId === originalId
    && m.sourceMessageId === transferAttempts[0].messageId
    && m.content.includes('Use this precise finding to improve the next draft.')).length, 1,
    'A lost HTTP response and retry must not enqueue the same receiving instruction twice');
  assert.ok(sourceResults.some(m => m.sourcePageId === originalId && m.sourceMessageId),
    'The native receiving message preserves the original Work result source');
  checks.push('retrying a lost transfer response keeps one sourced Work input and both drafts intact');
  // A later Work can be created directly from an edited result without first
  // building an empty destination, with one original native source and turn.
  await selectedSource.getByRole('button', { name: 'Use in another page', exact: true }).click();
  const newWorkHandoff = page.getByRole('dialog', { name: 'Carry this result into another page', exact: true });
  const handoffExcerpt = (await newWorkHandoff.locator('#transfer-work-excerpt').inputValue())
    + '\n\nReviewer note: show the central interaction first.';
  const handoffInstruction = 'Produce the next usable onboarding draft from this edited result.';
  await newWorkHandoff.locator('#transfer-work-excerpt').fill(handoffExcerpt);
  await newWorkHandoff.locator('#transfer-work-instruction').fill(handoffInstruction);
  await newWorkHandoff.getByRole('button', { name: 'Create a new page from this result', exact: true }).click();
  const fromResultCreate = page.getByRole('dialog', { name: 'Give this a little room.', exact: true });
  await fromResultCreate.waitFor();
  assert.equal(await fromResultCreate.locator('#new-page-source').inputValue(), handoffExcerpt);
  assert.equal(await fromResultCreate.locator('#new-page-focus').inputValue(), handoffInstruction);
  await fromResultCreate.locator('#new-page-title').fill('Follow-up from selected result');
  await fromResultCreate.getByRole('button', { name: 'Create page', exact: true }).click();
  await page.getByRole('heading', { name: 'Follow-up from selected result', exact: true }).waitFor();
  const newWorkId = new URL(page.url()).pathname.split('/').pop();
  await wait(async () => {
    const response = await fetch(base + '/api/pages/' + newWorkId);
    return response.ok && (await response.json()).messages.some(message => message.role === 'assistant');
  }, 'new Work receives a normal native model turn from its selected result');
  const createdWorkView = await (await fetch(base + '/api/pages/' + newWorkId)).json();
  assert.equal(createdWorkView.page.sourcePageId, originalId);
  assert.ok(createdWorkView.messages.some(message => message.role === 'user'
    && message.content.includes(handoffInstruction)
    && message.content.includes('Reviewer note: show the central interaction first.')
    && message.content.includes('"edited": true')),
  'The first native user message includes the edited result with its original source');
  assert.ok(!createdWorkView.messages.some(message => message.role === 'user'
    && message.content.includes('The intake form hides the product')),
  'Private correction text stays in the originating Work');
  await page.reload();
  await page.getByRole('heading', { name: 'Follow-up from selected result', exact: true }).waitFor();
  await page.getByRole('button', { name: 'Back to conversation', exact: true }).click();
  await page.getByRole('heading', { name: 'Original product brief', exact: true }).waitFor();
  assert.equal(await page.locator('.composer textarea').inputValue(), 'Original draft stays here.');
  checks.push('Result → new Work: edited source and instruction persist through native first turn, reload and source return');
  // A Home-created file used to be stranded: the file-result action for
  // another Work was hidden in Home, despite the native reference API accepting
  // a verified Home producer. Exercise the complete ordinary route.
  await page.goto(base + '/');
  await page.getByRole('textbox', { name: 'Message Home', exact: true }).fill('BUILD_HOME_FILE_FOR_WORK');
  await page.getByRole('button', { name: 'Send message', exact: true }).click();
  await page.getByText('Your approval is needed', { exact: true }).waitFor();
  assert.equal((await status()).files.includes('home-reference.txt'), false);
  await page.getByRole('button', { name: 'Allow once', exact: true }).click();
  const homeFileButton = page.locator('.page-artifacts .artifact-open').filter({ hasText: 'home-reference.txt' });
  await homeFileButton.waitFor();
  const homeFile = (await (await fetch(base + '/api/pages/home')).json())
    .artifacts.find(item => item.path === 'home-reference.txt');
  assert.ok(homeFile?.id, 'the source must be a successful Home Kernel file action');
  await homeFileButton.click();
  await page.getByText('Home reference: check the original file before drafting.', { exact: true }).waitFor();
  await page.getByRole('button', { name: 'Add the full file to another page’s draft', exact: true }).click();
  const homeFilePicker = page.getByRole('dialog', { name: 'Add the full file to another page’s draft', exact: true });
  await homeFilePicker.getByRole('option', { name: /Uninformed delivery/ }).click();
  await page.getByRole('heading', { name: 'Uninformed delivery', exact: true }).waitFor();
  const receivingDraft = await page.locator('.composer textarea').inputValue();
  assert.match(receivingDraft, /Receiver draft stays here/);
  assert.match(receivingDraft, /home-reference[.]txt/);
  const receivedMaterials = (await (await fetch(base + '/api/pages/' + beforeId + '/materials')).json()).materials;
  const homeSourceReceipt = receivedMaterials.find(item => item.originArtifactId === homeFile.id);
  assert.equal(homeSourceReceipt?.originArtifactPageId, 'home');
  assert.equal(homeSourceReceipt?.sha256, homeFile.sha256);
  await page.getByRole('button', { name: 'Inspect home-reference.txt before sending', exact: true }).click();
  const homeSourcePreview = page.getByRole('dialog', { name: 'Source files', exact: true });
  await homeSourcePreview.getByText('Home reference: check the original file before drafting.', { exact: true }).waitFor();
  await homeSourcePreview.getByRole('button', { name: 'Close dialog', exact: true }).click();
  assert.equal(await page.locator('.composer textarea').inputValue(), receivingDraft);
  checks.push('the Home Kernel file is version-verified in a second Work draft, with its original action identity');

  // Sending is still the person's choice; the fixture chooses an actual
  // native reading followed by a real approved change using observed bytes.
  await page.locator('.composer textarea').fill(receivingDraft + '\n\nUSE_HOME_FILE_IN_WORK');
  await page.getByRole('button', { name: 'Send message', exact: true }).click();
  await page.getByText('Your approval is needed', { exact: true }).waitFor();
  assert.equal((await status()).files.includes('reviewed-home-reference.txt'), false);
  await page.getByRole('button', { name: 'Allow once', exact: true }).click();
  const reviewedFileButton = page.locator('.page-artifacts .artifact-open').filter({ hasText: 'reviewed-home-reference.txt' });
  await reviewedFileButton.waitFor();
  await reviewedFileButton.click();
  await page.getByText('Work used the verified Home source:', { exact: false }).waitFor();
  assert.equal((await status()).files.includes('reviewed-home-reference.txt'), true);
  await page.getByRole('button', { name: 'Add full file to Home draft', exact: true }).click();
  await page.getByRole('textbox', { name: 'Message Home', exact: true }).waitFor();
  assert.match(await page.locator('.composer textarea').inputValue(), /reviewed-home-reference[.]txt/);
  const reviewedFile = (await (await fetch(base + '/api/pages/' + beforeId)).json()).artifacts
    .find(item => item.path === 'reviewed-home-reference.txt');
  const homeAfterReturn = (await (await fetch(base + '/api/pages/home/materials')).json()).materials;
  assert.ok(homeAfterReturn.some(item => item.originArtifactId === reviewedFile?.id
    && item.originArtifactPageId === beforeId), 'the completed file retains its producing Work action');
  await stopServer(server);
  server = startServer();
  await wait(async () => (await fetch(base + '/api/health')).ok, 'Home to Work artifact after restart');
  await page.reload();
  await page.getByRole('textbox', { name: 'Message Home', exact: true }).waitFor();
  assert.match(await page.locator('.composer textarea').inputValue(), /reviewed-home-reference[.]txt/);
  assert.equal((await (await fetch(base + '/api/pages/' + beforeId)).json())
    .artifacts.some(item => item.id === reviewedFile?.id), true);
  checks.push('explicit Home-to-Work source reading, approved new file, return to Home and restart all survive');

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
