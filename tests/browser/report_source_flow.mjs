/** Native Work report -> Home source -> exact Work result -> original Home position.
 * Copyright 2026 Lightmaze Contributors. Licensed under Apache-2.0.
 * HTTP model choices are explicit synthetic fixtures; execution and storage are real.
 */
import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { mkdtemp, mkdir, readFile, rm } from 'node:fs/promises';
import { createHash } from 'node:crypto';
import { createServer } from 'node:net';
import { tmpdir } from 'node:os';
import { delimiter, dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { createRequire } from 'node:module';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '../..');
const { chromium } = createRequire(import.meta.url)(process.env.PLAYWRIGHT_MODULE || 'playwright');
const directory = await mkdtemp(join(tmpdir(), 'per-pulse-report-source-'));
const screenshots = process.env.PER_PULSE_REPORT_SOURCE_SCREENSHOTS;
if (screenshots) await mkdir(screenshots, { recursive: true });
const socket = createServer();
await new Promise(done => socket.listen(0, '127.0.0.1', done));
const port = socket.address().port;
await new Promise(done => socket.close(done));
const base = `http://127.0.0.1:${port}`;
const title = 'Native report source Work';
const resultText = 'The original detailed source result. [Explicit synthetic HTTP fixture]';
let serverLog = '', browser, page, restarting = false;
const checks = [], errors = [], externalRequests = [], restartNetworkErrors = [];
const start = (offline = false) => {
  const child = spawn(process.env.PER_PULSE_PYTHON || join(root, '.venv/bin/python'), [
    join(root, 'tests/browser/report_source_server.py'), '--data-dir', directory, '--port', String(port),
    ...(offline ? ['--offline'] : []),
  ], { cwd: root, env: { ...process.env,
    // Installed-wheel checks must load production modules from the wheel,
    // not silently import checkout/src before site-packages.
    PYTHONPATH: [
      ...(process.env.PER_PULSE_INSTALLED_WHEEL === '1' ? [] : [join(root, 'src')]),
      process.env.PYTHONPATH,
    ].filter(Boolean).join(delimiter) },
    stdio: ['ignore', 'pipe', 'pipe'] });
  child.stdout.on('data', data => { serverLog += data; });
  child.stderr.on('data', data => { serverLog += data; });
  return child;
};
const stop = async child => {
  if (child.exitCode !== null || child.signalCode !== null) return;
  child.kill('SIGTERM');
  const force = setTimeout(() => child.kill('SIGKILL'), 10000);
  await new Promise(done => child.once('exit', done));
  clearTimeout(force);
};
const wait = async (fn, label, timeout = 20000) => {
  const deadline = Date.now() + timeout;
  while (Date.now() < deadline) {
    try { if (await fn()) return; } catch { /* service starting or view changing */ }
    await new Promise(done => setTimeout(done, 75));
  }
  throw new Error(`Timed out: ${label}\n${serverLog}`);
};
const json = async path => {
  const response = await fetch(base + path);
  assert.ok(response.ok, path + ': ' + response.status);
  return response.json();
};
const view = id => json('/api/pages/' + encodeURIComponent(id));
const status = () => json('/__report-source/status');
let server = start();
try {
  await wait(async () => (await fetch(base + '/api/health')).ok, 'native service');
  browser = await chromium.launch({ executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE || undefined,
    args: process.env.PLAYWRIGHT_CHROMIUM_ARGS ? JSON.parse(process.env.PLAYWRIGHT_CHROMIUM_ARGS) : undefined });
  page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
  page.on('pageerror', error => errors.push(error.message));
  page.on('console', message => {
    if (message.type() !== 'error') return;
    if (restarting && message.text() === 'Failed to load resource: net::ERR_CONNECTION_REFUSED' &&
        message.location().url.startsWith(base + '/')) restartNetworkErrors.push(message.location().url);
    else errors.push(message.text());
  });
  page.on('request', request => { if (!request.url().startsWith(base)) externalRequests.push(request.url()); });
  await page.goto(base);
  await page.getByRole('textbox', { name: 'Message Home', exact: true }).waitFor();
  await page.getByRole('button', { name: 'Create a page', exact: true }).click();
  await page.getByRole('textbox', { name: 'Page name', exact: true }).fill(title);
  await page.getByRole('textbox', { name: 'Where should we begin?', exact: true })
    .fill('Write report-delivery.md in this Work; report it on a later turn. [Authored fixture request]');
  await page.getByRole('button', { name: 'Create page', exact: true }).click();
  await page.getByRole('heading', { name: title, exact: true }).waitFor();
  const workId = decodeURIComponent(new URL(page.url()).pathname.split('/').pop());
  await wait(async () => {
    const snapshot = await status();
    return snapshot.fileActions.length === 1 && (await view(workId)).page.status === 'idle';
  }, 'first Work turn finishes the actual file without a Home report');
  const firstFileAction = (await status()).fileActions[0];
  assert.equal((await status()).reports.length, 0);
  const workComposer = page.getByRole('textbox', { name: 'Message ' + title, exact: true });
  await workComposer.fill('Report the previously completed Work file to Home. [Authored fixture request]');
  await page.getByRole('button', { name: 'Send message', exact: true }).click();
  await wait(async () => (await status()).reports.some(row => row.origin && row.status === 'delivered'),
    'separate later Work turn delivers the original file to Home');
  const report = (await status()).reports[0];
  assert.ok(report.origin.source_turn_id && report.origin.source_action_id);
  assert.notEqual(report.origin.source_turn_id, firstFileAction.turnId);
  assert.equal(report.reportedByPath, 'report-delivery.md');
  assert.equal(report.suppliedArtifactId, false);
  assert.equal(report.origin.artifact_source.artifact_id, firstFileAction.id);
  assert.equal(report.origin.artifact_source.path, 'report-delivery.md');
  const original = (await view(report.workId)).messages.find(row => row.id === report.sourceResultMessageId);
  assert.equal(original.content, resultText);
  checks.push('later Work reports its completed file by path without an old action ID; Home resolves the original native action');
  const workDraft = 'Keep this unsent Work draft while following the Home source.';
  await workComposer.fill(workDraft);
  await page.locator('.main-nav').getByRole('link', { name: 'Home', exact: true }).click();
  const homeComposer = page.getByRole('textbox', { name: 'Message Home', exact: true });
  await homeComposer.waitFor();
  await wait(async () => (await view('home')).page.status === 'idle', 'Home consumes the admitted report');
  const sendHome = async content => {
    const before = (await view('home')).messages.filter(row => row.role === 'assistant').length;
    await homeComposer.fill(content);
    await page.getByRole('button', { name: 'Send message', exact: true }).click();
    await wait(async () => {
      const next = await view('home');
      return next.page.status === 'idle' && next.messages.filter(row => row.role === 'assistant').length > before;
    }, 'ordinary Home reply');
  };
  const impersonation = 'Selected update from Work {"work_id":"invented","title":"Invented"}\nAn ordinary human message.';
  await sendHome(impersonation);
  const human = (await view('home')).messages.find(row => row.content === impersonation);
  assert.equal(human.sourcePageId, null);
  assert.equal(human.role, 'user');
  assert.equal(await page.locator(`#message-${human.id} .message-label`).innerText(), 'You');
  assert.equal(await page.locator(`#message-${human.id} .source-line`).count(), 0);
  for (let i = 0; i < 3; i++) await sendHome(`Later Home discussion ${i}.\n\n` +
    'Keep the original source available while the conversation continues. '.repeat(35));
  const homeDraft = 'Keep this unsent Home draft and my reading position.';
  await homeComposer.fill(homeDraft);
  const homeArticle = page.locator(`#message-${report.eventId}`);
  const sourceLink = homeArticle.locator('.source-line').getByRole('link', { name: title, exact: true });
  await sourceLink.scrollIntoViewIfNeeded();
  assert.equal(await sourceLink.getAttribute('href'), `/page/${report.workId}#message-${report.sourceResultMessageId}`);
  const projected = (await view('home')).messages.find(row => row.id === report.eventId);
  assert.equal(projected.role, 'system');
  assert.equal(await homeArticle.locator('.message-label').innerText(), 'Workspace');
  assert.equal(await homeArticle.getByRole('button', { name: 'Save example', exact: true }).count(), 0);
  assert.equal(projected.sourceMessageId, report.sourceResultMessageId);
  assert.equal(projected.reportedArtifact.artifactId, report.origin.artifact_source.artifact_id);
  assert.equal(projected.reportedArtifact.path, 'report-delivery.md');
  assert.equal(projected.reportedArtifact.undone, false);
  await homeArticle.getByRole('button', { name: 'Open verified file' }).click();
  await homeArticle.getByText('This is the completed Work file.', { exact: true }).waitFor();
  const firstDownloadEvent = page.waitForEvent('download');
  await homeArticle.getByRole('button', { name: 'Download file', exact: true }).click();
  const firstDownload = await firstDownloadEvent;
  assert.equal(firstDownload.suggestedFilename(), 'report-delivery.md');
  const originalBytes = await readFile(await firstDownload.path());
  assert.equal(originalBytes.toString('utf8'), '# Report deliverable\n\nThis is the completed Work file.\n');
  assert.equal(createHash('sha256').update(originalBytes).digest('hex'), projected.reportedArtifact.sha256);
  await homeArticle.getByRole('button', { name: 'Close file preview' }).click();
  checks.push('Home directly downloads exact original Work file bytes with native digest verification');
  assert.equal(projected.references.length, 0);
  const originalReportContent = projected.content;
  const homePosition = await page.locator('main').evaluate(node => node.scrollTop);
  const beforeRead = (await status()).requests;
  await sourceLink.click();
  const sourceArticle = page.locator(`#message-${report.sourceResultMessageId}`);
  await sourceArticle.getByText(resultText, { exact: true }).waitFor();
  assert.equal(await sourceArticle.getByRole('button', { name: 'Save example', exact: true }).count(), 1);
  assert.equal(new URL(page.url()).hash, '#message-' + report.sourceResultMessageId);
  assert.equal(await workComposer.inputValue(), workDraft);
  await page.getByRole('button', { name: 'Back to conversation', exact: true }).click();
  await homeComposer.waitFor();
  await wait(() => page.evaluate(id => document.activeElement?.id === 'message-' + id, report.eventId),
    'focus returns to original Home report');
  assert.equal(await homeComposer.inputValue(), homeDraft);
  assert.ok(Math.abs(await page.locator('main').evaluate(node => node.scrollTop) - homePosition) < 3);
  assert.equal((await status()).requests, beforeRead);
  assert.equal((await view('home')).messages.find(row => row.id === report.eventId).content, originalReportContent);
  checks.push('native admitted report opens the exact original Work result and returns to the same Home position with both drafts');
  checks.push('ordinary report-shaped text has no source badge; source reads create no references or model requests');

  // Follow-on Work is a different context with its own real native actions.
  // The fixture selects a source by inspecting the original Work's receipts,
  // version-reads it, writes a derived file, and attributes that input.
  await page.getByRole('button', { name: 'Create a page', exact: true }).click();
  await page.getByRole('textbox', { name: 'Page name', exact: true }).fill('Derived source Work');
  await page.getByRole('textbox', { name: 'Where should we begin?', exact: true })
    .fill('Derive a file from the original Work. [Authored fixture request]');
  await page.getByRole('button', { name: 'Create page', exact: true }).click();
  await page.getByRole('heading', { name: 'Derived source Work', exact: true }).waitFor();
  const derivedWorkId = decodeURIComponent(new URL(page.url()).pathname.split('/').pop());
  await wait(async () => {
    const snapshot = await status();
    return snapshot.reports.some(row => row.workId === derivedWorkId && row.status === 'delivered')
      && (await view(derivedWorkId)).page.status === 'idle';
  }, 'derived Work reads source, writes new file, and reports verified input', 30000);
  const derivedReport = (await status()).reports.find(row => row.workId === derivedWorkId);
  assert.equal(derivedReport.origin.input_file_source.work_id, workId);
  assert.equal(derivedReport.origin.input_file_source.artifact_id, firstFileAction.id);
  assert.equal(derivedReport.origin.input_file_source.sha256,
    createHash('sha256').update(originalBytes).digest('hex'));
  await page.locator('.main-nav').getByRole('link', { name: 'Home', exact: true }).click();
  await homeComposer.waitFor();
  const derivedArticle = page.locator(`#message-${derivedReport.eventId}`);
  const inputCard = derivedArticle.locator('[aria-label="Verified source file read for this result"]');
  const outputCard = derivedArticle.locator('[aria-label="File delivered from Work"]');
  await inputCard.waitFor();
  assert.equal(await outputCard.count(), 1);
  const derivedProjection = (await view('home')).messages.find(row => row.id === derivedReport.eventId);
  assert.equal(derivedProjection.inputArtifact.pageId, workId);
  assert.equal(derivedProjection.inputArtifact.artifactId, firstFileAction.id);
  assert.equal(derivedProjection.reportedArtifact.path, 'derived-delivery.md');
  await inputCard.getByRole('button', { name: 'Open verified file' }).click();
  await inputCard.getByText('This is the completed Work file.', { exact: true }).waitFor();
  const sourceDownloadEvent = page.waitForEvent('download');
  await inputCard.getByRole('button', { name: 'Download file' }).click();
  assert.deepEqual(await readFile(await (await sourceDownloadEvent).path()), originalBytes);
  await inputCard.getByRole('button', { name: 'Close file preview' }).click();
  await outputCard.getByRole('button', { name: 'Open verified file' }).click();
  await outputCard.getByText('Based on the verified source Work file.', { exact: true }).waitFor();
  const derivedDownloadEvent = page.waitForEvent('download');
  await outputCard.getByRole('button', { name: 'Download file' }).click();
  const derivedBytes = await readFile(await (await derivedDownloadEvent).path());
  assert.equal(derivedBytes.toString('utf8'),
    '# Derived deliverable\n\nBased on the verified source Work file.\n');
  assert.equal(createHash('sha256').update(derivedBytes).digest('hex'),
    derivedProjection.reportedArtifact.sha256);
  await outputCard.getByRole('button', { name: 'Close file preview' }).click();
  await inputCard.getByRole('link', { name: 'Open original source Work' }).click();
  await page.getByRole('heading', { name: title, exact: true }).waitFor();
  await page.getByRole('button', { name: 'Back to conversation', exact: true }).click();
  await homeComposer.waitFor();
  assert.equal(await homeComposer.inputValue(), homeDraft);
  checks.push('second real Work inspects, version-reads, derives and reports a source; Home opens both file bytes and returns to the same position');

  if (screenshots) await page.screenshot({ path: join(screenshots, 'report-source-home-desktop.png') });

  restarting = true;
  await stop(server); server = start(true);
  await wait(async () => (await fetch(base + '/api/health')).ok, 'offline restarted World');
  await page.reload();
  await homeComposer.waitFor();
  restarting = false;
  await page.setViewportSize({ width: 390, height: 844 });
  await sourceLink.scrollIntoViewIfNeeded();
  await sourceLink.click();
  await sourceArticle.getByText(resultText, { exact: true }).waitFor();
  assert.equal(await workComposer.inputValue(), workDraft);
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
  if (screenshots) await page.screenshot({ path: join(screenshots, 'report-source-result-phone.png') });
  await page.getByRole('button', { name: 'Back to conversation', exact: true }).click();
  await homeComposer.waitFor();
  assert.equal(await homeComposer.inputValue(), homeDraft);
  assert.equal((await status()).requests, 0);
  assert.equal((await status()).reports.find(row => row.workId === report.workId)
    .origin.source_action_id, report.origin.source_action_id);
  assert.equal((await view('home')).messages.find(row => row.id === report.eventId).reportedArtifact.artifactId,
    report.origin.artifact_source.artifact_id);
  await homeArticle.getByRole('button', { name: 'Open verified file' }).click();
  await homeArticle.getByText('This is the completed Work file.', { exact: true }).waitFor();
  const restartedDownloadEvent = page.waitForEvent('download');
  await homeArticle.getByRole('button', { name: 'Download file', exact: true }).click();
  const restartedDownload = await restartedDownloadEvent;
  assert.deepEqual(await readFile(await restartedDownload.path()), originalBytes);
  await homeArticle.getByRole('button', { name: 'Close file preview' }).click();
  checks.push('offline SQLite restart preserves direct Home download of the same original file');
  const restartedDerived = (await view('home')).messages.find(row => row.id === derivedReport.eventId);
  assert.equal(restartedDerived.inputArtifact.artifactId, firstFileAction.id);
  assert.equal(restartedDerived.reportedArtifact.path, 'derived-delivery.md');
  await inputCard.getByRole('button', { name: 'Open verified file' }).click();
  await inputCard.getByText('This is the completed Work file.', { exact: true }).waitFor();
  await inputCard.getByRole('button', { name: 'Close file preview' }).click();
  await outputCard.getByRole('button', { name: 'Open verified file' }).click();
  await outputCard.getByText('Based on the verified source Work file.', { exact: true }).waitFor();
  await outputCard.getByRole('button', { name: 'Close file preview' }).click();
  checks.push('offline SQLite restart preserves distinct verified input and output previews');
  assert.equal(await homeArticle.locator('.message-label').innerText(), 'Workspace');
  checks.push('offline process restart retains exact native provenance and source navigation on a phone viewport');

  // A person bringing a selected result Home remains the speaker of that new
  // input, even though it links the same Work as the native automatic report.
  await sourceLink.scrollIntoViewIfNeeded();
  await sourceLink.click();
  await sourceArticle.getByRole('button', { name: 'Bring to Home', exact: true }).click();
  const selectedText = 'I selected this original Work result for Home.';
  const dialog = page.getByRole('dialog', { name: 'Bring this back to Home.', exact: true });
  await dialog.getByRole('textbox', { name: 'What would you like to bring back?', exact: true }).fill(selectedText);
  await dialog.getByRole('button', { name: 'Bring to Home', exact: true }).click();
  await homeComposer.waitFor();
  const selected = (await view('home')).messages.find(row => row.content === selectedText);
  assert.equal(selected.role, 'user');
  assert.equal(selected.sourceMessageId, report.sourceResultMessageId);
  assert.equal(await page.locator(`#message-${selected.id} .message-label`).innerText(), 'You');
  assert.equal(await homeArticle.locator('.message-label').innerText(), 'Workspace');
  checks.push('verified automatic reports are Workspace messages; human prefix text and selected Home returns stay You');
  // Undo the real producing action after restart. The old report is still
  // historical, but its file can no longer be opened as a current deliverable.
  await sourceLink.click();
  await page.locator('.page-artifacts').getByRole('button', { name: 'Undo', exact: true }).click();
  await wait(async () => (await view('home')).messages.find(row => row.id === report.eventId)
    ?.reportedArtifact?.undone === true, 'reported original file is undone');
  await page.getByRole('button', { name: 'Back to conversation', exact: true }).click();
  await homeComposer.waitFor();
  await homeArticle.getByRole('button', { name: 'Open verified file' }).click();
  await homeArticle.getByText('Undone', { exact: true }).waitFor();
  assert.equal(await homeArticle.getByRole('button', { name: 'Download file', exact: true }).count(), 0);
  const finalDerived = (await view('home')).messages.find(row => row.id === derivedReport.eventId);
  assert.equal(finalDerived.inputArtifact.undone, true);
  assert.equal(finalDerived.reportedArtifact.undone, false);
  await inputCard.getByRole('button', { name: 'Open verified file' }).click();
  await inputCard.getByText('Undone', { exact: true }).waitFor();
  assert.equal(await inputCard.getByRole('button', { name: 'Download file' }).count(), 0);
  await outputCard.getByRole('button', { name: 'Open verified file' }).click();
  await outputCard.getByText('Based on the verified source Work file.', { exact: true }).waitFor();
  checks.push('undoing the source invalidates input preview without deleting the derived output');
  checks.push('Undo after restart invalidates Home preview and download without erasing report provenance');
  assert.deepEqual(errors, []);
  assert.deepEqual(externalRequests, []);
  console.log(JSON.stringify({ checks, errors, externalRequests, restartNetworkErrors,
    viewports: ['1280x900', '390x844'] }, null, 2));
} catch (error) {
  if (screenshots && page) await page.screenshot({ path: join(screenshots, 'report-source-failure.png') }).catch(() => {});
  throw error;
} finally {
  await browser?.close();
  await stop(server);
  await rm(directory, { recursive: true, force: true });
}
