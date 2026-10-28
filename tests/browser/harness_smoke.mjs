import { createRequire } from 'node:module';
import { spawn } from 'node:child_process';
import path from 'node:path';
import os from 'node:os';
import { fileURLToPath } from 'node:url';
import fs from 'node:fs/promises';
const require = createRequire(import.meta.url);
const { chromium } = require(process.env.PER_PULSE_PLAYWRIGHT_PATH || 'playwright');
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const output = process.env.PER_PULSE_QA_OUTPUT || await fs.mkdtemp(path.join(os.tmpdir(), 'per-pulse-qa-'));
await fs.mkdir(output, {recursive:true});
const server = spawn(process.env.PER_PULSE_TEST_PYTHON || 'python3', [root+'/tests/browser/fixture_server.py'], {cwd:root, env:{...process.env,PYTHONPATH:root+'/src'}, stdio:['ignore','pipe','pipe']});
let serverLog = '';
server.stderr.on('data', d=>serverLog += d.toString());
const checks = [];
let browser;
const base = 'http://127.0.0.1:'+(process.env.PER_PULSE_QA_PORT || '8146');
const wait = async (f, label, timeout=15000) => {
  const end=Date.now()+timeout;
  while (Date.now()<end) { try { if(await f()) return; } catch {} await new Promise(r=>setTimeout(r,100)); }
  throw new Error('Timed out: '+label);
};
const check = (name, condition) => { if (!condition) throw new Error(name); checks.push(name); };
try {
  await wait(async()=> (await fetch(base+'/api/health')).ok,'server ready');
  browser = await chromium.launch({executablePath:process.env.PER_PULSE_CHROMIUM_PATH || undefined,headless:true,args:['--no-sandbox','--disable-dev-shm-usage']});
  const page=await browser.newPage({viewport:{width:1440,height:1000}});
  const errors=[];
  page.on('pageerror',e=>errors.push(e.message));
  page.on('console',m=>{ if(m.type()==='error') errors.push(m.text()); });
  await page.goto(base);
  await page.getByRole('heading',{level:1}).waitFor();
  check('identity and nonblank Home',await page.title()==='Per Pulse');
  const create = async (title,instruction) => {
    await page.getByRole('button',{name:'Create a page',exact:true}).click();
    await page.getByLabel('Page name',{exact:true}).fill(title);
    await page.getByLabel('Where should we begin?',{exact:true}).fill(instruction);
    await page.getByRole('button',{name:'Create page',exact:true}).click();
    await page.getByRole('heading',{name:title,exact:true}).waitFor();
    await page.getByText('Your approval is needed',{exact:true}).waitFor({timeout:15000});
  };
  await create('Approved note','WRITE_ONE');
  const approvedId=new URL(page.url()).pathname.split('/').pop();
  await page.screenshot({path:output+'/qa-approval-desktop.png',fullPage:false});
  check('approval prevents premature file effect',!(await (await fetch(base+'/__fixture/status')).json()).files.includes('approved.txt'));
  await page.getByRole('button',{name:'Allow once',exact:true}).click();
  await page.getByRole('button',{name:/approved.txt/}).waitFor({timeout:15000});
  await page.getByRole('button',{name:/approved.txt/}).click();
  await page.getByText('A real file from a synthetic model fixture.',{exact:true}).waitFor();
  check('approved effect has a readable source-linked artifact',true);
  await page.screenshot({path:output+'/qa-artifact-desktop.png',fullPage:false});
  // The native file action and the Work's completed message must return to Home
  // with a usable source link, not as an unattributed copied conversation.
  const approvedResult = page.locator('.message-assistant').filter({hasText:'Synthetic model fixture: this page has finished its turn.'}).last();
  await approvedResult.getByRole('button',{name:'Bring to Home',exact:true}).click();
  const returnDialog = page.getByRole('dialog',{name:'Bring this back to Home.',exact:true});
  await returnDialog.waitFor();
  await returnDialog.getByRole('button',{name:'Bring to Home',exact:true}).click();
  await page.getByRole('heading',{name:'A place to keep going.',exact:true}).waitFor();
  const sourcedReturn = page.locator('.message-user').filter({hasText:'Synthetic model fixture: this page has finished its turn.'}).last();
  // A return contains both its precise originating message link and a separate
  // page reference. Follow the provenance link, not the generic page shortcut.
  const sourceLink = sourcedReturn.locator('.source-line a.session-link');
  await sourceLink.waitFor();
  check('selected Work result returns to Home with its original message source',
        (await sourceLink.getAttribute('href')).includes('#message-'));
  await sourceLink.click();
  await page.getByRole('heading',{name:'Approved note',exact:true}).waitFor();
  check('Home source link returns to the original Work',new URL(page.url()).pathname.endsWith('/'+approvedId));
  await create('Declined note','WRITE_DECLINE');
  const declinedId = new URL(page.url()).pathname.split('/').pop();
  // An approval blocked in a sibling Work must become discoverable in Home,
  // Home reads the identity-bound observation endpoint, not the old /api/pulse
  // state path. Hold the exact request used by Home to prove that a slow
  // background read cannot hide an actionable foreground Work.
  // even when the unrelated Pulse activity read is still awaiting a response.
  // The review still happens only in the original context.
  let releasePulseRead = () => {};
  const heldPulseRead = new Promise(resolve => { releasePulseRead = resolve; });
  let pulseReadIntercepted = false;
  await page.route('**/api/pulse/observation', async route => {
    pulseReadIntercepted = true;
    await heldPulseRead;
    try { await route.continue(); } catch { /* The old Home read may be aborted. */ }
  });
  await page.locator('aside.sidebar').getByRole('link',{name:'Home',exact:true}).click();
  await wait(async()=>pulseReadIntercepted,'stalled read of background activity');
  const needsWork = page.getByRole('region',{name:'Work needing attention',exact:true});
  await needsWork.getByRole('button',{name:'Open Declined note',exact:true}).waitFor({timeout:15000});
  check('foreground Home review is independent of stalled Pulse and does not preapprove',
    !(await (await fetch(base+'/__fixture/status')).json()).files.includes('declined.txt'));
  releasePulseRead();
  await page.unroute('**/api/pulse/observation');
  await needsWork.getByRole('button',{name:'Open Declined note',exact:true}).click();
  await page.getByRole('heading',{name:'Declined note',exact:true}).waitFor();
  check('Home review opens the original Work approval and retains native authority',
    new URL(page.url()).pathname.endsWith('/'+declinedId)
    && await page.getByRole('button',{name:'Allow once',exact:true}).count()===1);
  // One Work's pending approval must not hold the whole continuing subject.
  await page.locator('aside.sidebar').getByRole('link',{name:'Home',exact:true}).click();
  await page.getByRole('textbox',{name:'Message Home',exact:true}).fill('Home can answer while another Work awaits approval.');
  const repliesBeforeApproval = await page.locator('.message-assistant').count();
  await page.getByRole('button',{name:'Send message',exact:true}).click();
  await wait(async()=>await page.locator('.message-assistant').count()>repliesBeforeApproval,
             'foreground Home response while sibling approval is pending');
  check('foreground Home stays usable while a sibling Work awaits approval',true);
  await page.goto(base+'/page/'+declinedId);
  await page.getByRole('button',{name:'Decline',exact:true}).waitFor();
  await page.getByRole('button',{name:'Decline',exact:true}).click();
  await wait(async()=>!(await page.getByRole('button',{name:'Allow once',exact:true}).count()),'declined approval clears');
  check('declined effect did not create a file',!(await (await fetch(base+'/__fixture/status')).json()).files.includes('declined.txt'));
  await page.locator('aside.sidebar').getByRole('link',{name:'Home',exact:true}).click();
  await wait(async()=>await page.getByRole('button',{name:'Open Declined note',exact:true}).count()===0,
             'settled approval disappears from Home review');
  check('Home clears the review doorway after original Work settlement',true);
  await create('Slow continuation','WRITE_SLOW SLOW_AFTER_APPROVAL');
  // Locale is presentation state. A Work draft and exact pending approval must
  // survive changing language without remounting its continuing context.
  // The label changes with the locale; the exact underlying Work draft must not.
  const slowDraft = page.locator('.composer textarea');
  await slowDraft.fill('Unsent draft: keep this context and approval.');
  await page.getByRole('button',{name:'Settings',exact:true}).click();
  await page.locator('#interface-language').selectOption('zh-CN');
  await page.getByRole('dialog',{name:'你的工作区',exact:true}).waitFor();
  check('interface language switches independently of reply language',
    await page.evaluate(()=>document.documentElement.lang)==='zh-CN'
    && await page.locator('#response-language').inputValue()==='auto');
  await page.getByRole('button',{name:'关闭对话框',exact:true}).click();
  await page.getByRole('button',{name:'仅允许本次',exact:true}).waitFor();
  check('pending native approval and unsent Work draft survive Chinese switch',
    await slowDraft.inputValue()==='Unsent draft: keep this context and approval.');
  // A fresh bilingual Home projects the same pending Work, without carrying
  // its private approval contents or its unsent draft into Home.
  await page.locator('aside.sidebar').getByRole('link',{name:'主页',exact:true}).click();
  const chineseReview = page.getByRole('region',{name:'需要处理的工作',exact:true});
  await chineseReview.getByRole('button',{name:'打开 Slow continuation',exact:true}).waitFor({timeout:15000});
  check('Chinese Home makes the original blocked Work discoverable',true);
  await chineseReview.getByRole('button',{name:'打开 Slow continuation',exact:true}).click();
  await page.getByRole('button',{name:'仅允许本次',exact:true}).waitFor();
  check('Chinese Home reentry preserves the Work approval and unsent draft',
    await slowDraft.inputValue()==='Unsent draft: keep this context and approval.');
  await page.getByRole('button',{name:'活动',exact:true}).click();
  await page.getByRole('dialog',{name:'活动',exact:true}).waitFor();
  await page.getByRole('heading',{name:'后台活动',exact:true}).waitFor();
  check('same Pulse activity route is reachable in Chinese',true);
  await page.getByRole('button',{name:'关闭对话框',exact:true}).click();
  await page.getByRole('button',{name:'设置',exact:true}).click();
  await page.locator('#interface-language').selectOption('en');
  await page.getByRole('button',{name:'Close dialog',exact:true}).click();
  check('original approval identity and draft persist after returning to English',
    await page.getByRole('button',{name:'Allow once',exact:true}).count()===1
    && await slowDraft.inputValue()==='Unsent draft: keep this context and approval.');
  // Deliver a deliberately obsolete (but structurally valid) approval snapshot
  // only after Stop wins. The backend still executes the original native action;
  // the browser route changes only response timing and the stale projection.
  const slowId = new URL(page.url()).pathname.split('/').pop();
  const preStopSnapshot = await (await fetch(base+'/api/pages/'+slowId)).json();
  let releaseOldApproval;
  const holdOldApproval = new Promise(resolve => { releaseOldApproval = resolve; });
  let oldApprovalIntercepted = false;
  const approvalRoute = `**/api/pages/${slowId}/execution/actions/*`;
  await page.route(approvalRoute, async route => {
    const upstream = await route.fetch();
    const receipt = await upstream.json();
    oldApprovalIntercepted = true;
    await holdOldApproval;
    await route.fulfill({response: upstream, json: {...receipt, view: preStopSnapshot}});
  });
  await page.getByRole('button',{name:'Allow once',exact:true}).click();
  await wait(async()=>(await (await fetch(base+'/__fixture/status')).json()).entered,'slow continuation entered');
  check('stop stays enabled while approval response is pending',await page.getByRole('button',{name:'Stop this turn',exact:true}).isEnabled());
  await page.getByRole('button',{name:'Stop this turn',exact:true}).click();
  await page.getByText('This turn was stopped',{exact:true}).waitFor({timeout:10000});
  await fetch(base+'/__fixture/release');
  await wait(async()=>oldApprovalIntercepted,'late approval response captured',20000);
  const lateApproval = page.waitForResponse(response => response.url().includes('/api/pages/'+slowId+'/execution/actions/'));
  releaseOldApproval();
  await lateApproval;
  // Let the fulfilled request resolve through fetch/React before checking the view.
  await page.waitForTimeout(350);
  check('late Allow receipt cannot resurrect stopped approval',
    await page.getByText('This turn was stopped',{exact:true}).isVisible()
    && await page.getByRole('button',{name:'Allow once',exact:true}).count()===0
    && await slowDraft.inputValue()==='Unsent draft: keep this context and approval.');
  await page.unroute(approvalRoute);
  check('UI cancels the exact continuing turn',true);
  await page.locator('aside.sidebar').getByRole('link',{name:'Home',exact:true}).click();
  await page.getByRole('textbox',{name:'Message Home',exact:true}).fill('Home keeps working.');
  const repliesBeforeCancellation = await page.locator('.message-assistant').count();
  await page.getByRole('button',{name:'Send message',exact:true}).click();
  await wait(async()=>await page.locator('.message-assistant').count()>repliesBeforeCancellation,
             'foreground Home response after sibling cancellation');
  check('Home remains usable after sibling cancellation',true);
  await create('Completed effect with response failure','WRITE_REDACTED');
  await page.getByRole('button',{name:'Allow once',exact:true}).click();
  await page.getByText('The action completed, but the follow-up response could not finish. Do not repeat the action.',{exact:true}).waitFor();
  await page.getByRole('button',{name:/redacted.txt/}).click();
  await page.getByText('Sensitive values have been hidden.',{exact:true}).waitFor();
  check('completed effect survives continuation failure with truthful UI and redacted preview',!(await page.textContent('body')).includes('password=abc'));
  await create('Long report','WRITE_LARGE');
  const largeId = new URL(page.url()).pathname.split('/').pop();
  await page.getByRole('button',{name:'Allow once',exact:true}).click();
  await page.locator('.message-assistant').getByText('Synthetic model fixture: this page has finished its turn.',{exact:true}).waitFor();
  const largeArtifact = page.getByRole('button',{name:/large-report.txt/});
  await largeArtifact.waitFor();
  await largeArtifact.click();
  await page.getByText(/^FINAL An explicitly synthetic long report/).waitFor();
  const largeSnapshot = await (await fetch(base+'/api/pages/'+largeId+'/artifacts')).json();
  check('large native diff preserves a complete file receipt and successful continuation',
    largeSnapshot.artifacts.length===1 && !largeSnapshot.truncated
    && !(await page.textContent('body')).includes('follow-up response could not finish'));
  await page.screenshot({path:output+'/qa-large-artifact-desktop.png',fullPage:false});
  const largeDraft = 'Keep this draft beside the confirmed long report.';
  await page.locator('.composer textarea').fill(largeDraft);
  await page.reload();
  await page.getByRole('heading',{name:'Long report',exact:true}).waitFor();
  await page.getByRole('button',{name:/large-report.txt/}).click();
  await page.getByText(/^FINAL An explicitly synthetic long report/).waitFor();
  check('large result retains its exact action and Work draft after reload',
    (await (await fetch(base+'/api/pages/'+largeId+'/artifacts')).json()).artifacts[0].id===largeSnapshot.artifacts[0].id
    && await page.locator('.composer textarea').inputValue()===largeDraft);
  const mobile=await browser.newPage({viewport:{width:390,height:844},deviceScaleFactor:1,isMobile:true,hasTouch:true});
  await mobile.goto(base+'/page/'+approvedId);
  await mobile.getByRole('heading',{name:'Approved note',exact:true}).waitFor();
  await mobile.getByRole('button',{name:/approved.txt/}).click();
  await mobile.getByText('A real file from a synthetic model fixture.',{exact:true}).waitFor();
  check('mobile has no horizontal clipping',await mobile.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
  await mobile.screenshot({path:output+'/qa-artifact-mobile.png',fullPage:false});
  await mobile.goto(base+'/page/'+largeId);
  await mobile.getByRole('heading',{name:'Long report',exact:true}).waitFor();
  await mobile.getByRole('button',{name:/large-report.txt/}).click();
  await mobile.getByText(/^FINAL An explicitly synthetic long report/).waitFor();
  check('large file preview fits the mobile page',await mobile.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
  await mobile.screenshot({path:output+'/qa-large-artifact-mobile.png',fullPage:false});
  check('no relevant console errors or framework overlays',errors.length===0);
  await fs.writeFile(output+'/qa-browser-results.json',JSON.stringify({checks,errors,viewports:['1440x1000','390x844'],provider:'explicit synthetic HTTP transport; real World, Kernel, approval and file effects',browser:await browser.version()},null,2));
  console.log(JSON.stringify({passed:checks.length,checks,output}));
} catch (error) {
  console.error(error.stack,serverLog);
  process.exitCode=1;
} finally {
  if(browser) await browser.close();
  server.kill('SIGTERM');
  await Promise.race([new Promise(r=>server.on('exit',r)),new Promise(r=>setTimeout(r,5000))]);
  if(server.exitCode===null) server.kill('SIGKILL');
}
