/** Concrete-example journey on the real World/Kernel with an explicit HTTP fixture.
 * These scripted choices establish product behavior, not model learning quality.
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
const require = createRequire(import.meta.url);
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const directory = await mkdtemp(join(tmpdir(), 'per-pulse-examples-'));
const screenshots = process.env.PER_PULSE_EXAMPLE_SCREENSHOTS;
if (screenshots) await mkdir(screenshots, { recursive: true });
const socket = createServer();
await new Promise(resolve => socket.listen(0, '127.0.0.1', resolve));
const port = socket.address().port;
await new Promise(resolve => socket.close(resolve));
const base = 'http://127.0.0.1:' + port;
const excerpt = 'Saved documents remain available. Search is slow.\nNext update: 09:00 UTC.';
const checks = [], errors = [];
let serverLog = '', browser, page;
const start = (offline = false) => {
  const child = spawn(process.env.PER_PULSE_PYTHON || join(root, '.venv/bin/python'), [
    join(root, 'tests/browser/example_server.py'), '--data-dir', directory, '--port', String(port),
    ...(offline ? ['--offline'] : []),
  ], { cwd: root, stdio: ['ignore', 'pipe', 'pipe'] });
  child.stdout.on('data', data => { serverLog += data; });
  child.stderr.on('data', data => { serverLog += data; });
  return child;
};
const stop = async child => {
  if (child.exitCode !== null || child.signalCode !== null) return;
  const closed = new Promise(resolve => child.once('exit', resolve));
  const force = setTimeout(() => child.kill('SIGKILL'), 10000);
  child.kill('SIGTERM'); await closed; clearTimeout(force);
};
const wait = async (fn, label) => {
  const end = Date.now() + 20000;
  while (Date.now() < end) {
    try { if (await fn()) return; } catch {}
    await new Promise(resolve => setTimeout(resolve, 80));
  }
  throw new Error('Timed out: ' + label + '\n' + serverLog);
};
const data = path => fetch(base + path).then(r => { assert.ok(r.ok); return r.json(); });
const experiences = id => data('/api/pages/' + id + '/posture').then(value => value.experiences);
const view = id => data('/api/pages/' + id);
let server = start();
try {
  await wait(async () => (await fetch(base + '/api/health')).ok, 'service ready');
  browser = await chromium.launch({ executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE || undefined,
    args: process.env.PLAYWRIGHT_CHROMIUM_ARGS ? JSON.parse(process.env.PLAYWRIGHT_CHROMIUM_ARGS) : undefined });
  const context = await browser.newContext({ viewport: { width: 1280, height: 900 }, permissions: ['clipboard-read', 'clipboard-write'] });
  page = await context.newPage();
  page.on('pageerror', e => errors.push(e.message));
  await page.goto(base);
  const close = () => page.getByRole('button', { name: /^(Close dialog|关闭对话框)$/ }).click();
  const openExperience = async () => {
    if (!await page.getByRole('button', { name: 'Settings', exact: true }).isVisible()) {
      await page.getByRole('button', { name: 'Toggle navigation', exact: true }).click();
    }
    await page.getByRole('button', { name: 'Settings', exact: true }).click();
    await page.getByRole('button', { name: 'View this page’s experience', exact: true }).click();
    await page.getByRole('dialog', { name: 'Lessons and examples', exact: true }).waitFor();
  };
  const create = async (title, instruction) => {
    await page.getByRole('button', { name: 'Create a page', exact: true }).click();
    await page.getByRole('textbox', { name: 'Page name', exact: true }).fill(title);
    await page.getByRole('textbox', { name: 'Where should we begin?', exact: true }).fill(instruction);
    await page.getByRole('button', { name: 'Create page', exact: true }).click();
    await page.getByRole('heading', { name: title, exact: true }).waitFor();
    const id = new URL(page.url()).pathname.split('/').pop();
    await wait(async () => (await view(id)).messages.some(m => m.role === 'assistant' && m.status === 'settled'), 'native reply');
    return id;
  };
  const send = async (content, id, count) => {
    await page.getByRole('textbox', { name: /^Message / }).fill(content);
    await page.getByRole('button', { name: 'Send message', exact: true }).click();
    await wait(async () => (await view(id)).messages.filter(m => m.role === 'assistant' && m.status === 'settled').length >= count, 'native follow-up reply');
  };

  const origin = await create('Notice source', 'Draft a customer notice. PRIVATE_USER_CONTEXT: source-only working notes.');
  const original = (await view(origin)).messages.find(m => m.role === 'assistant');
  const article = page.locator('[data-message-id="' + original.id + '"]');
  await article.waitFor();
  const draft = 'Unsent source draft remains in this Work.';
  await page.getByRole('textbox', { name: 'Message Notice source', exact: true }).fill(draft);
  // Rendered selections can span UI controls and Markdown structure. Such
  // a selection must never silently turn into an entire shareable example.
  await article.evaluate(node => {
    const range = document.createRange(); range.selectNodeContents(node);
    const selection = getSelection(); selection.removeAllRanges(); selection.addRange(range);
    const button = [...node.querySelectorAll('button')].find(element => element.textContent?.trim() === 'Save example');
    if (!button) throw new Error('Save example action was not found');
    button.click();
  });
  const unsafeEditor = page.getByRole('dialog', { name: 'Save example', exact: true });
  await unsafeEditor.waitFor();
  await unsafeEditor.getByRole('textbox', { name: 'Example name', exact: true }).fill('Must not silently copy');
  assert.equal(await unsafeEditor.getByRole('textbox', { name: 'Original passage', exact: true }).inputValue(), '');
  assert.equal(await unsafeEditor.getByRole('button', { name: 'Save example', exact: true }).isDisabled(), true);
  await unsafeEditor.getByText('The highlighted text is not a contiguous passage', { exact: false }).waitFor();
  await unsafeEditor.getByText('Show original reply for copying', { exact: true }).click();
  assert.equal(await unsafeEditor.getByRole('textbox', { name: 'Show original reply for copying', exact: true }).inputValue(), original.content);
  await unsafeEditor.getByRole('button', { name: 'Use entire original reply', exact: true }).click();
  assert.equal(await unsafeEditor.getByRole('textbox', { name: 'Original passage', exact: true }).inputValue(), original.content);
  await unsafeEditor.getByRole('button', { name: 'Cancel', exact: true }).click();
  await unsafeEditor.waitFor({ state: 'hidden' });
  assert.equal((await experiences(origin)).length, 0);
  checks.push('nonliteral selection cannot silently save an entire example; source expansion requires an explicit choice');

  await article.locator('.message-content p').first().evaluate(node => {
    const range = document.createRange(); range.selectNodeContents(node);
    const selection = getSelection(); selection.removeAllRanges(); selection.addRange(range);
  });
  await article.getByRole('button', { name: 'Save example', exact: true }).click();
  const editor = page.getByRole('dialog', { name: 'Save example', exact: true });
  assert.equal(await editor.getByRole('textbox', { name: 'Original passage', exact: true }).inputValue(), excerpt);
  await editor.getByRole('textbox', { name: 'Example name', exact: true }).fill('Notice wording to revisit');
  await editor.getByRole('textbox', { name: 'Original passage', exact: true }).fill('A rewritten version that never happened.');
  assert.equal(await editor.getByRole('button', { name: 'Save example', exact: true }).isDisabled(), true);
  await editor.getByText('This passage is not an exact part of the original reply.', { exact: true }).waitFor();
  await editor.getByRole('textbox', { name: 'Original passage', exact: true }).fill(excerpt);
  await editor.getByRole('textbox', { name: 'What should we notice? (optional)', exact: true }).fill('Notice the order of availability, disruption, and update time.');
  assert.equal(await editor.locator('#example-followup').inputValue(), '');
  await editor.getByRole('button', { name: 'Save example', exact: true }).click();
  const card = page.locator('.lesson').filter({ has: page.getByRole('heading', { name: 'Notice wording to revisit', exact: true }) });
  await card.waitFor();
  assert.equal(await card.locator('pre').textContent(), excerpt);
  await card.getByRole('button', { name: 'Copy original passage', exact: true }).click();
  await wait(async () => await page.evaluate(() => navigator.clipboard.readText()) === excerpt, 'exact example clipboard');
  await card.getByText('Source experience', { exact: true }).click();
  await card.getByText('No follow-up attached', { exact: true }).waitFor();
  const initial = (await experiences(origin))[0];
  assert.equal(initial.representation, 'example');
  assert.equal(initial.scope, 'context');
  assert.equal(initial.source.selected_excerpt.sha256.length, 64);
  assert.equal(initial.source.feedback_evidence_class, 'not_attached');
  assert.deepEqual(initial.feedback, []);
  for (const key of ['lesson', 'conditions', 'counterexamples', 'shift']) assert.equal(key in initial, false);
  checks.push('select, inspect and copy an exact native result without inventing a rule or evaluation');

  await card.getByRole('button', { name: 'Edit example note', exact: true }).click();
  const revisedNote = 'Compare the current service state before reusing the wording.';
  await card.getByRole('textbox', { name: 'What should we notice? (optional)', exact: true }).fill(revisedNote);
  await card.getByRole('textbox', { name: 'What prompted this change? (optional)', exact: true }).fill('PRIVATE_NOTE_REVIEW: a new service state needs fresh checking.');
  await card.getByRole('button', { name: 'Save refinement', exact: true }).click();
  await card.getByText('In use · v2', { exact: true }).waitFor();
  await card.getByRole('button', { name: 'Use in other pages', exact: true }).click();
  await card.getByText('In use · v3', { exact: true }).waitFor();
  await page.getByRole('searchbox', { name: 'Find experience', exact: true }).fill('PRIVATE_USER_CONTEXT');
  await page.getByText('No experience matches this search.', { exact: true }).waitFor();
  await page.getByRole('searchbox', { name: 'Find experience', exact: true }).fill('Search is slow');
  await card.waitFor();
  if (screenshots) await page.screenshot({ path: join(screenshots, 'example-desktop.png') });
  await close();
  assert.equal(await page.getByRole('textbox', { name: 'Message Notice source', exact: true }).inputValue(), draft);

  const receiver = await create('Later notice', 'FIND_SHARED_EXAMPLE: find a prior wording and compare it with this task.');
  await page.getByText('The exact example was available. [Explicit synthetic fixture]', { exact: true }).waitFor();
  const shared = (await experiences(receiver))[0];
  assert.equal(shared.excerpt, excerpt);
  assert.equal(shared.note, revisedNote);
  assert.deepEqual(shared.sourceAnchor, { resultMessageId: original.id });
  for (const key of ['encounter', 'source', 'feedback']) assert.equal(key in shared, false);
  const requests = await data('/__examples/requests');
  const recipientRequests = requests.filter(body => JSON.stringify(body.messages).includes('FIND_SHARED_EXAMPLE'));
  assert.ok(recipientRequests.length >= 2);
  assert.equal(/PRIVATE_(USER_CONTEXT|SURROUNDING_ACCOUNT|NOTE_REVIEW)/.test(JSON.stringify(recipientRequests)), false);
  assert.ok(recipientRequests.some(body => body.messages.some(m => m.role === 'tool' && m.content.includes(excerpt.replaceAll('\n', '\\n')))));
  await page.getByRole('textbox', { name: 'Message Later notice', exact: true }).fill('Keep my unsent receiving draft.');
  await openExperience();
  const receivedCard = page.locator('.lesson');
  assert.equal(await receivedCard.getByRole('button', { name: 'Edit example note', exact: true }).count(), 0);
  await receivedCard.getByText('Source experience', { exact: true }).click();
  await receivedCard.getByRole('button', { name: 'Go to original result', exact: true }).click();
  await article.waitFor();
  assert.equal(new URL(page.url()).hash, '#message-' + original.id);
  assert.equal(await page.getByRole('textbox', { name: 'Message Notice source', exact: true }).inputValue(), draft);
  await page.getByRole('button', { name: 'Back to conversation', exact: true }).click();
  assert.equal(await page.getByRole('textbox', { name: 'Message Later notice', exact: true }).inputValue(), 'Keep my unsent receiving draft.');
  checks.push('explicit sharing carries only the excerpt and note through native retrieval, with source navigation and both drafts intact');

  await openExperience();
  await receivedCard.getByRole('button', { name: 'Give feedback', exact: true }).click();
  await receivedCard.getByRole('textbox', { name: 'What changed in the result?', exact: true }).fill('The old update time is wrong for this incident. PRIVATE_RECIPIENT_REPORT.');
  await receivedCard.getByRole('button', { name: 'It hurt · pause', exact: true }).click();
  await receivedCard.getByText('Paused · v4', { exact: true }).waitFor();
  assert.equal((await experiences(receiver))[0].active, false);
  assert.equal((await experiences(origin))[0].feedback.at(-1).source.page_id, receiver);
  await close();
  checks.push('later recipient counterevidence pauses the example everywhere while retaining its original passage');

  const nativePage = await create('Native example capture', 'Draft a notice for native capture.');
  await send('KEEP_ORIGINAL_EXAMPLE: keep that exact wording without extracting a rule.', nativePage, 2);
  await wait(async () => (await experiences(nativePage)).some(item => item.title === 'A concrete service notice'), 'native example admission');
  let native = (await experiences(nativePage)).find(item => item.title === 'A concrete service notice');
  assert.equal(native.source.interpretation, 'model_selected_concrete_example');
  assert.equal(native.excerpt, excerpt);
  assert.equal(native.source.feedback_evidence_class, 'not_attached');
  const feedbackFile = 'Observed service update.md';
  const feedbackBytes = Buffer.from('# Later observation\r\n\r\nThe update time changed. 🌍\r\n');
  await page.getByRole('button', { name: 'Materials', exact: true }).click();
  const materials = page.getByRole('dialog', { name: 'Materials', exact: true });
  await materials.getByLabel('Choose a text file', { exact: true }).setInputFiles({ name: feedbackFile, mimeType: 'text/markdown', buffer: feedbackBytes });
  await materials.getByRole('button', { name: 'Upload file', exact: true }).click();
  await materials.getByRole('button', { name: 'Add to draft', exact: true }).click();
  await materials.waitFor({ state: 'hidden' });
  await send('REVISE_EXAMPLE_NOTE: the current service state and time must be checked first. 🌍', nativePage, 3);
  await wait(async () => (await experiences(nativePage)).find(item => item.id === native.id)?.revision === 2, 'native note revision');
  native = (await experiences(nativePage)).find(item => item.id === native.id);
  assert.equal(native.note, 'Check the current service state and update time before adapting this example.');
  assert.equal(native.excerpt, excerpt);
  assert.equal(native.feedback[0].source.kind, 'model_interpreted_user_revision');
  checks.push('actual native remember_example and note revision finish through the existing Kernel and journal');

  const feedbackSource = native.feedback[0].evidenceSource;
  assert.equal(feedbackSource.materials[0].name, feedbackFile);
  assert.ok(native.feedback[0].evidence.includes('[Attached workspace materials]'));
  const feedbackDraft = 'Keep this draft while tracing the later evidence.';
  await page.getByRole('textbox', { name: 'Message Native example capture', exact: true }).fill(feedbackDraft);
  await openExperience();
  const nativeCard = page.locator('.lesson').filter({ has: page.getByRole('heading', { name: 'A concrete service notice', exact: true }) });
  await nativeCard.getByText('Later feedback (1)', { exact: true }).click();
  const evidence = nativeCard.locator('.lesson-feedback-evidence');
  assert.ok((await evidence.textContent()).includes('🌍'));
  assert.ok((await evidence.textContent()).includes(feedbackFile));
  assert.ok(!(await evidence.textContent()).includes('[Attached workspace materials]'));
  await page.setViewportSize({ width: 390, height: 844 });
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
  await evidence.getByRole('button', { name: 'Open source message', exact: true }).scrollIntoViewIfNeeded();
  if (screenshots) await page.screenshot({ path: join(screenshots, 'example-feedback-mobile.png') });
  await evidence.getByRole('button', { name: 'Open source message', exact: true }).click();
  await page.getByRole('dialog', { name: 'Lessons and examples', exact: true }).waitFor({ state: 'hidden' });
  assert.equal(new URL(page.url()).hash, '#message-' + feedbackSource.messageId);
  assert.equal(await page.getByRole('textbox', { name: 'Message Native example capture', exact: true }).inputValue(), feedbackDraft);
  await page.locator('[data-message-id="' + feedbackSource.messageId + '"]').getByRole('button', { name: feedbackFile, exact: true }).click();
  const originalSource = page.getByRole('dialog', { name: 'Source files', exact: true });
  const savedFeedback = page.waitForEvent('download');
  await originalSource.getByRole('button', { name: 'Download original', exact: true }).click();
  const feedbackDownload = await savedFeedback;
  assert.equal(feedbackDownload.suggestedFilename(), feedbackFile);
  await feedbackDownload.saveAs(join(directory, 'feedback-original.md'));
  assert.deepEqual(await readFile(join(directory, 'feedback-original.md')), feedbackBytes);
  await close();
  await page.setViewportSize({ width: 1280, height: 900 });
  checks.push('later native feedback shows readable text and its attached source, opens the exact original message and downloads original bytes without losing the draft');

  await stop(server); server = start(true);
  await wait(async () => (await fetch(base + '/api/health')).ok, 'offline restart');
  await page.reload();
  await page.getByRole('textbox', { name: 'Message Native example capture', exact: true }).waitFor();
  await openExperience();
  assert.equal(await page.locator('.lesson').filter({ has: page.getByRole('heading', { name: 'A concrete service notice', exact: true }) }).locator('pre').textContent(), excerpt);
  assert.deepEqual((await experiences(nativePage)).find(item => item.id === native.id).feedback[0].evidenceSource, feedbackSource);
  await close();
  const nativeOriginal = (await view(nativePage)).messages.find(m => m.role === 'assistant');
  await page.locator('[data-message-id="' + nativeOriginal.id + '"]').getByRole('button', { name: 'Save example', exact: true }).click();
  await editor.getByRole('textbox', { name: 'Example name', exact: true }).fill('Reviewed later while offline');
  await editor.getByRole('textbox', { name: 'Original passage', exact: true }).fill(excerpt);
  const followupId = (await view(nativePage)).messages.find(m => m.role === 'user' && m.content.startsWith('REVISE_EXAMPLE_NOTE')).id;
  await editor.locator('#example-followup').selectOption(followupId);
  await editor.getByRole('button', { name: 'Save example', exact: true }).click();
  await page.getByRole('heading', { name: 'Reviewed later while offline', exact: true }).waitFor();
  const reviewed = (await experiences(nativePage)).find(item => item.title === 'Reviewed later while offline');
  assert.equal(reviewed.sourceAnchor.feedbackMessageId, followupId);
  assert.deepEqual(reviewed.feedback, []);
  checks.push('restart preserves native examples and permits offline capture with an attributed later follow-up');

  // A note is also a versioned draft: both a rejected save and a refresh must
  // preserve the input without letting it overwrite a concurrently changed note.
  const reviewedCard = page.locator('.lesson').filter({ has: page.getByRole('heading', { name: reviewed.title, exact: true }) });
  await reviewedCard.getByRole('button', { name: 'Edit example note', exact: true }).click();
  const noteField = reviewedCard.getByRole('textbox', { name: 'What should we notice? (optional)', exact: true });
  const unsentNote = 'Keep my unsent comparison of the notice wording.';
  await noteField.fill(unsentNote);
  const concurrentNote = 'A later editor checked the current service state.';
  const changed = await fetch(base + '/api/pages/' + nativePage + '/posture/experiences/' + reviewed.id, {
    method: 'PATCH', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ expectedRevision: reviewed.revision, changes: { note: concurrentNote } }),
  });
  assert.equal(changed.status, 200);
  await reviewedCard.getByRole('button', { name: 'Save refinement', exact: true }).click();
  await page.getByRole('alert').getByText('This experience changed. Refresh it before saving.', { exact: true }).waitFor();
  assert.equal(await noteField.inputValue(), unsentNote);
  await page.getByRole('button', { name: 'Refresh experience', exact: true }).click();
  await reviewedCard.getByText(concurrentNote, { exact: true }).waitFor();
  assert.equal(await noteField.inputValue(), unsentNote);
  assert.equal(await reviewedCard.getByRole('button', { name: 'Save refinement', exact: true }).isDisabled(), true);
  await reviewedCard.getByRole('button', { name: 'Reload latest version', exact: true }).click();
  assert.equal(await noteField.inputValue(), concurrentNote);
  await noteField.fill(unsentNote);
  await reviewedCard.getByRole('button', { name: 'Save refinement', exact: true }).click();
  await reviewedCard.getByText(unsentNote, { exact: true }).waitFor();
  const resolvedNote = (await experiences(nativePage)).find(item => item.id === reviewed.id);
  assert.equal(resolvedNote.revision, reviewed.revision + 2);
  assert.equal(resolvedNote.excerpt, reviewed.excerpt);
  assert.deepEqual(resolvedNote.source, reviewed.source);
  checks.push('conflicting note drafts survive rejection and refresh, require explicit reload, and keep the original example source');

  await page.setViewportSize({ width: 390, height: 844 });
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
  if (screenshots) await page.screenshot({ path: join(screenshots, 'example-mobile.png') });
  const languageTab = await context.newPage();
  await languageTab.goto(base);
  await languageTab.getByRole('button', { name: 'Settings', exact: true }).click();
  await languageTab.locator('#interface-language').selectOption('zh-CN');
  await page.getByRole('dialog', { name: '工作中的经验', exact: true }).waitFor();
  assert.equal(await page.locator('.lesson pre').first().textContent(), excerpt);
  if (screenshots) await page.screenshot({ path: join(screenshots, 'example-mobile-chinese.png') });
  await languageTab.close();
  checks.push('phone layout and Chinese interface preserve the original English example without overflow');
  assert.deepEqual(errors, []);
  console.log(JSON.stringify({ checks, count: checks.length, errors, modelEvidence: 'explicit_synthetic_transport_only' }, null, 2));
} catch (error) {
  if (screenshots && page) await page.screenshot({ path: join(screenshots, 'example-failure.png') }).catch(() => {});
  throw error;
} finally {
  await browser?.close(); await stop(server);
  await rm(directory, { recursive: true, force: true });
}
