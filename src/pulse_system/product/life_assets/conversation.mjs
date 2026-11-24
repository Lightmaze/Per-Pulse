/** A view of original Home receipts, never a second agent/session or fake reply. */
export const ACTIVE = new Set(['queued','running','open','model_streaming','waiting_approval','settling']);
export function replyState(status, nativeStatus=null) {
  if(status==='uncertain'&&nativeStatus==='cancelled')return '回应已停止；结果需在 Home 核实。';
  return ({queued:'已收到，等待 P 接着处理。',running:'P 正在回应…',open:'P 正在回应…',model_streaming:'P 正在回应…',settling:'正在完成这一轮…',waiting_approval:'这一步需要你批准。',failed:'这次没有完成。',uncertain:'执行状态需要在 Home 核实。',blocked:'这次回应被阻塞了。',cancelled:'已停止回应。',succeeded:''})[status] ?? '正在核实回应状态…';
}
export function parseReply(data, id) {
  if (!data || data.event_id!==id || typeof data.engram_id!=='string' || typeof data.status!=='string' || typeof data.text!=='string' || typeof data.partial!=='boolean') throw Error('回应与原问题不匹配。');
  return data;
}
/** Preserve the reader's intention when the player, composer or viewport resizes. */
export function followConversationTail(list, latest, Resize = globalThis.ResizeObserver) {
  const nearBottom=()=>list.scrollHeight-list.scrollTop-list.clientHeight<70;
  let pinned=true,width=list.clientWidth,height=list.clientHeight;
  function update(follow) {
    pinned=follow;
    if(follow){list.scrollTop=list.scrollHeight;latest.hidden=true;}
    else latest.hidden=false;
  }
  function onScroll() {
    // A layout resize is not an intentional scroll away from the last reply.
    if(width!==list.clientWidth||height!==list.clientHeight)return;
    pinned=nearBottom();latest.hidden=pinned;
  }
  function onResize() {
    width=list.clientWidth;height=list.clientHeight;
    if(pinned)update(true);
    else latest.hidden=nearBottom();
  }
  const jump=()=>update(true);
  list.addEventListener('scroll',onScroll,{passive:true});
  latest.addEventListener('click',jump);
  const observer=typeof Resize==='function'?new Resize(onResize):null;
  observer?.observe(list);
  return {nearBottom,update,dispose(){observer?.disconnect();list.removeEventListener('scroll',onScroll);latest.removeEventListener('click',jump);}};
}
export function mountConversation({request}) {
  const list=document.getElementById('messages'), empty=document.getElementById('conversation-empty'), latest=document.getElementById('latest');
  const entries=[];let polling=false,timer=null,disposed=false;
  const tail=followConversationTail(list,latest),nearBottom=tail.nearBottom,updateScroll=tail.update;
  function add({id,text,range=''}) {
    empty.hidden=true;
    const box=document.createElement('article');box.className='exchange';box.dataset.eventId=id;
    const question=document.createElement('p');question.className='question-bubble';question.textContent=text;
    const context=document.createElement('p');context.className='question-context';context.textContent=range;
    const label=document.createElement('div');label.className='reply-label';const mark=document.createElement('span');mark.className='small-mark';mark.textContent='P';label.append(mark);
    const content=document.createElement('p');content.className='reply-text';
    const state=document.createElement('p');state.className='reply-state';state.setAttribute('role','status');state.textContent=replyState('queued');
    const actions=document.createElement('div');actions.className='reply-actions';
    const cancel=document.createElement('button');cancel.type='button';cancel.textContent='停止回应';cancel.hidden=true;
    const recover=document.createElement('button');recover.type='button';recover.textContent='重新连接';recover.hidden=true;
    const home=document.createElement('a');home.textContent='在 Home 处理';home.href='/';home.target='_blank';home.rel='noopener';home.hidden=true;
    actions.append(cancel,recover,home);box.append(question,context,label,content,state,actions);list.append(box);
    const entry={id,box,content,state,cancel,recover,home,scope:null,active:true,errors:0};entries.push(entry);
    // This page has bounded observers; earlier entries remain readable in Home.
    if(entries.length>12){const index=entries.findIndex(e=>!e.active);if(index>=0){const [old]=entries.splice(index,1);old.box.remove();}}
    cancel.onclick=async()=>{
      if(!entry.scope)return;cancel.disabled=true;
      try {const s=entry.scope;await request(`/v1/harness/sessions/${encodeURIComponent(s.session_id)}/control`,{...s,operation:'cancel'});state.textContent='已请求停止，正在确认…';}
      catch(e){state.textContent=e.message;home.hidden=false;}
      finally{cancel.disabled=false;void poll();}
    };
    recover.onclick=()=>{entry.errors=0;entry.active=true;recover.hidden=true;void poll();};
    updateScroll(true);void poll();return entry;
  }
  async function poll(){
    if(polling||disposed)return;clearTimeout(timer);polling=true;
    try {await Promise.all(entries.filter(e=>e.active).map(async entry=>{
      try {
        const data=parseReply(await request('/v1/life/replies/'+encodeURIComponent(entry.id),null,'GET'),entry.id);
        if(disposed)return;
        const follow=nearBottom(),before=entry.box.textContent;
        entry.content.textContent=data.text;
        entry.state.textContent=replyState(data.status,data.native_status)+(data.truncated?' 完整内容在 Home。':'');
        entry.scope=data.control_scope;entry.active=ACTIVE.has(data.status);entry.errors=0;
        entry.cancel.hidden=!data.control_scope;entry.recover.hidden=true;
        entry.home.hidden=!(data.status==='waiting_approval'||data.truncated||['failed','uncertain','blocked'].includes(data.status));
        if(entry.box.textContent!==before)updateScroll(follow);
      }catch(e){
        if(disposed)return;entry.errors++;entry.state.textContent='暂时没有连上 P。问题已经发出，请勿重复发送。';
        entry.cancel.hidden=true;entry.recover.hidden=false;entry.home.hidden=false;
        if(entry.errors>=3)entry.active=false;
      }
    }));}finally{polling=false;if(!disposed&&entries.some(e=>e.active))timer=setTimeout(poll,800);}
  }
  function dispose(){disposed=true;clearTimeout(timer);tail.dispose();}
  window.addEventListener('pagehide',dispose,{once:true});
  return {add,dispose};
}
