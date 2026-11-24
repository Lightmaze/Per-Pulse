import {parseSubtitles, cuesAt, MAX_FILE_BYTES} from './captions.mjs';
import {mountConversation} from './conversation.mjs';
const $=id=>document.getElementById(id);
let video=$('video'), captionTrack=null, captionLoad=0, source=null, homeScope=null;
let trackStream=null, ac=null, node=null, objectURL=null, callback=0, base=0, lastT=0, lastSample=-1, epoch=0;
let queue=[], inflight=null, periodic=null, viewURLs=[], currentMoment=null, generation=0, skipped=0, lastPresented=0, received=0;
let selecting=false, sending=false, starting=false, filename='';
const token=$('token');
try { token.value=sessionStorage.getItem('pulse.api.startup-token:v1')||'';
  const accent=localStorage.getItem('pulse.settings.accent:v1');
  if(['blue','violet','green'].includes(accent))document.documentElement.dataset.accent=accent;
}catch{}
const small=document.createElement('canvas');small.width=96;small.height=54;
const smallctx=small.getContext('2d',{willReadFrequently:true});
const still=document.createElement('canvas'),stillctx=still.getContext('2d');
const encode=bytes=>{let s='';for(let i=0;i<bytes.length;i+=8192)s+=String.fromCharCode(...bytes.subarray(i,i+8192));return btoa(s);};
const time=t=>`${Math.floor(Math.max(0,t)/60).toString().padStart(2,'0')}:${Math.floor(Math.max(0,t)%60).toString().padStart(2,'0')}`;
function message(target,text){target.textContent=text;target.hidden=!text;}
function error(e){message($('error'),e.message||String(e));if($('setup').open)message($('setup-error'),e.message||String(e));}
function status(text){$('status').textContent=text;}
async function request(path,body,method='POST'){
  if(!token.value.trim())throw Error('请在共享设置中连接到你的 P。');
  const r=await fetch(path,{method,headers:{Authorization:'Bearer '+token.value.trim(),...(body?{'Content-Type':'application/json'}:{})},body:body?JSON.stringify(body):undefined,cache:'no-store',signal:AbortSignal.timeout(15000)});
  if(!r.ok){const data=await r.json().catch(()=>({detail:'HTTP '+r.status}));const e=new Error(typeof data.detail==='string'?data.detail:'请求未完成，请查看 Home。');e.status=r.status;throw e;}
  return r;
}
const api=(path,body,method)=>request('/v1/life'+path,body,method);
const chat=mountConversation({request:async(...args)=>(await request(...args)).json()});
function refreshControls(){
  $('ask').disabled=sending||selecting||(!$('question').value.trim())||(!source&&!homeScope);
  $('recent').disabled=!source||selecting;$('attach-recent').disabled=!source||selecting;
  $('stop').disabled=!source||starting;
}
function showSetup(){ $('connection').open=!token.value.trim();message($('setup-error'),'');$('setup').showModal(); }
$('open-video').onclick=showSetup;$('open-screen').onclick=showSetup;$('settings').onclick=showSetup;$('reopen-video').onclick=showSetup;
for(const button of document.querySelectorAll('[data-close]'))button.onclick=()=>$(button.dataset.close).close();
for(const dialog of document.querySelectorAll('dialog'))dialog.addEventListener('click',event=>{if(event.target===dialog){const r=dialog.getBoundingClientRect();if(event.clientX<r.left||event.clientX>r.right||event.clientY<r.top||event.clientY>r.bottom)dialog.close();}});
function openChat(){document.body.classList.remove('chat-collapsed');$('chat-toggle').setAttribute('aria-expanded','true');$('chat-toggle').setAttribute('aria-label','收起对话');}
$('chat-toggle').onclick=()=>{const collapsed=document.body.classList.toggle('chat-collapsed');$('chat-toggle').setAttribute('aria-expanded',String(!collapsed));$('chat-toggle').setAttribute('aria-label',collapsed?'展开对话':'收起对话');};
function viewport(){const v=window.visualViewport;document.documentElement.style.setProperty('--app-height',`${v?.height||innerHeight}px`);document.body.classList.toggle('keyboard-open',innerWidth<=700&&document.activeElement===$('question')&&(v?.height||innerHeight)<600);}
window.visualViewport?.addEventListener('resize',viewport);window.addEventListener('resize',viewport);
$('question').addEventListener('focus',viewport);$('question').addEventListener('blur',viewport);viewport();
$('question').addEventListener('input',()=>{const q=$('question');q.style.height='auto';q.style.height=Math.min(q.scrollHeight,innerWidth<=700?90:142)+'px';refreshControls();});
$('question').addEventListener('keydown',e=>{if(e.key==='Enter'&&!e.shiftKey&&!e.isComposing&&innerWidth>700){e.preventDefault();if(!$('ask').disabled)$('composer').requestSubmit();}});
function enqueue(sample){if(queue.length>=32){queue.shift();skipped++;}queue.push(sample);}
async function flush(){
  if(inflight)return inflight;if(!source||!queue.length)return;
  const id=source.source_id,gen=generation,batch=queue.splice(0,12);
  const job=(async()=>{try{const result=await(await api(`/sources/${id}/samples`,{samples:batch})).json();
    if(gen!==generation)return;received=result.last_t;
    $('coverage').textContent=`已接收 ${result.observed_frames} 帧 · 音频片段 ${result.retained_audio_chunks} · 缺口 ${result.gaps} · 本机跳过 ${skipped}`;
    $('diagnostics').textContent=JSON.stringify(result,null,2);
  }catch(e){if(gen===generation)error(e);}finally{if(inflight===job)inflight=null;}})();inflight=job;return job;
}
function sample(now,metadata){
  if(!source)return;const t=(now-base)/1000;
  if(metadata.presentedFrames&&lastPresented&&metadata.presentedFrames>lastPresented+1)skipped+=metadata.presentedFrames-lastPresented-1;
  lastPresented=metadata.presentedFrames||0;
  if(t-lastSample>=1/24-.004&&video.readyState>=2&&!video.seeking){
    lastSample=t;lastT=t;
    try{smallctx.drawImage(video,0,0,96,54);const rgba=smallctx.getImageData(0,0,96,54).data;
      const rgb=new Uint8Array(96*54*3);for(let i=0,j=0;i<rgba.length;i+=4){rgb[j++]=rgba[i];rgb[j++]=rgba[i+1];rgb[j++]=rgba[i+2];}
      still.width=Math.min(512,video.videoWidth||512);still.height=Math.max(1,Math.round(still.width*(video.videoHeight||288)/(video.videoWidth||512)));
      if(still.height>960)still.height=960;stillctx.drawImage(video,0,0,still.width,still.height);
      const jpeg=still.toDataURL('image/jpeg',.6).split(',')[1];let captions;
      if(source.model_captions_permitted&&captionTrack){const cues=cuesAt(captionTrack,metadata.mediaTime);captions={track_id:captionTrack.id,cues};$('live-caption').textContent=cues.map(c=>c.text).join('\n');$('live-caption').hidden=!cues.length;}
      enqueue({kind:'frame',t,epoch,media_t:source.kind==='local_video'?metadata.mediaTime:null,rgb:encode(rgb),...(jpeg.length<87000?{jpeg}:{}),...(captions?{captions}:{})});
      if(queue.length>=10)void flush();
    }catch(e){error(e);void stop();return;}
  }
  callback=video.requestVideoFrameCallback(sample);
}
async function audioInput(media){
  const local=media===video;if(!local&&!media.getAudioTracks().length)return;
  ac=new AudioContext();await ac.audioWorklet.addModule('/life/assets/audio.js');await ac.resume();
  const audioBase=(performance.now()-base)/1000-ac.currentTime;
  const input=local?ac.createMediaElementSource(video):ac.createMediaStreamSource(media);
  node=new AudioWorkletNode(ac,'life-pcm');node.port.onmessage=event=>{if(!source)return;const {t,pcm}=event.data;enqueue({kind:'audio',t:Math.max(0,audioBase+t),epoch,pcm:encode(new Uint8Array(pcm.buffer))});};
  input.connect(node);const silent=ac.createGain();silent.gain.value=0;node.connect(silent).connect(ac.destination);if(local)input.connect(ac.destination);
}
async function begin(kind,media){
  const gen=++generation;queue=[];inflight=null;skipped=0;lastPresented=0;epoch=0;lastSample=-1;lastT=0;received=0;clearMoment();base=performance.now();
  if($('caption-share').checked&&(!captionTrack||kind!=='local_video'))throw Error('请先选择字幕；字幕接入目前支持本地视频。');
  const response=await(await api('/sources',{kind,share_images:$('vision').checked,share_captions:$('caption-share').checked})).json();
  if(gen!==generation){await api('/sources/'+response.source_id,null,'DELETE');return;}
  source={...response,kind};homeScope=response.scope;
  for(const id of ['screen','local','vision','caption-file','caption-share'])$(id).disabled=true;
  message($('error'),'');
  try{await audioInput(media);}catch(e){if(ac){await ac.close();ac=null;}error(Error('画面可用，但声音未接入：'+e.message));}
  if(gen!==generation)return;
  video.hidden=false;$('empty-player').hidden=true;$('source-name').textContent=kind==='local_video'?filename:'共享的窗口';
  $('source-note').textContent=(response.model_images_permitted?'画面可按需发送给 P':'画面仅供预览')+(response.model_captions_permitted?' · 字幕已允许读取':'');
  await video.play();video.addEventListener('seeking',()=>{epoch++;});callback=video.requestVideoFrameCallback(sample);periodic=setInterval(()=>void flush(),250);
  status('共享中');$('composer-note').textContent='';$('setup').close();$('reopen-video').hidden=true;refreshControls();
  if(media!==video)media.getVideoTracks()[0].addEventListener('ended',()=>void stop(),{once:true});
}
function clearMoment(){
  $('question').placeholder='和 P 说点什么…';
  viewURLs.forEach(URL.revokeObjectURL);viewURLs=[];currentMoment=null;$('moment').hidden=true;$('frames').replaceChildren();$('reference-image').removeAttribute('src');$('reference-image').hidden=true;
  $('caption-lines').replaceChildren();$('caption-review').hidden=true;$('audio').pause();$('audio').removeAttribute('src');$('audio').hidden=true;
  if($('evidence').open)$('evidence').close();
}
async function stop({unload=false}={}){
  const previous=source,oldURL=objectURL,oldTime=video.currentTime,playing=!video.paused;source=null;generation++;captionLoad++;captionTrack=null;
  $('caption-file').value='';$('caption-share').checked=false;$('caption-file-info').textContent='选择与视频同步的 UTF-8 SRT／WebVTT。';$('live-caption').textContent='';$('live-caption').hidden=true;
  clearInterval(periodic);video.cancelVideoFrameCallback?.(callback);queue=[];inflight=null;trackStream?.getTracks().forEach(t=>t.stop());trackStream=null;node?.disconnect();node=null;
  video.pause();if(ac){await ac.close().catch(()=>{});ac=null;}video.srcObject=null;
  const next=video.cloneNode(false);video.replaceWith(next);video=next;
  // A ended share need not end local playback. Restore a fresh audio-capable
  // element; none of its subsequent frames/audio are sent without a new begin.
  if(oldURL&&!unload){video.src=oldURL;video.hidden=false;video.onloadedmetadata=()=>{video.currentTime=oldTime;if(playing)void video.play().catch(()=>{});};}
  else {if(oldURL)URL.revokeObjectURL(oldURL);objectURL=null;video.removeAttribute('src');video.load();video.hidden=true;$('empty-player').hidden=false;}
  clearMoment();for(const id of ['screen','local','vision','caption-file','caption-share'])$(id).disabled=false;
  status('已停止共享');$('source-note').textContent=oldURL&&!unload?'视频留在本机，不再发送画面和声音。':'共享已结束，已有交流仍在右侧。';$('reopen-video').hidden=false;
  $('composer-note').textContent=homeScope?'可以继续聊天；新片刻需要重新共享。':'打开视频后，就可以带着片刻提问。';refreshControls();
  if(previous)try{await api('/sources/'+previous.source_id,null,'DELETE');}catch(e){status('已停止 · 清理未确认');error(Error('本机已停止发送。宿主缓存清理尚未确认，会按保留期限过期。'));}
}
$('caption-file').onchange=async()=>{const file=$('caption-file').files?.[0],load=++captionLoad;captionTrack=null;$('caption-share').checked=false;
  if(!file){$('caption-file-info').textContent='未选择字幕';return;}
  try{if(source)throw Error('请结束当前共享后更换字幕。');if(file.size>MAX_FILE_BYTES)throw Error('字幕文件不能超过 1 MiB。');
    const raw=await file.arrayBuffer(),parsed=parseSubtitles(new TextDecoder('utf-8',{fatal:true}).decode(raw));const hash=await crypto.subtle.digest('SHA-256',raw);
    if(load!==captionLoad||source)return;captionTrack={...parsed,id:Array.from(new Uint8Array(hash),b=>b.toString(16).padStart(2,'0')).join('')};
    $('caption-file-info').textContent=`已读取 ${parsed.cues.length} 段字幕，仅在本页等待播放；尚未共享。`;message($('setup-error'),'');
  }catch(e){if(load===captionLoad){$('caption-file-info').textContent='字幕未载入';error(e);}}
};
$('local').onclick=()=>{if(!token.value.trim()){$('connection').open=true;token.focus();error(Error('请先输入工作台的启动令牌。'));return;}$('file').click();};
$('file').onchange=async()=>{const file=$('file').files?.[0];$('file').value='';if(!file||starting)return;starting=true;
  try{if(!video.requestVideoFrameCallback)throw Error('当前浏览器不支持视频帧回调。');if(objectURL)URL.revokeObjectURL(objectURL);
    filename=file.name;objectURL=URL.createObjectURL(file);video.muted=false;video.src=objectURL;
    await new Promise((resolve,reject)=>{video.onloadedmetadata=resolve;video.onerror=()=>reject(Error('这个视频格式无法播放，请换一份文件。'));});await begin('local_video',video);
  }catch(e){await stop({unload:true});error(e);}finally{starting=false;refreshControls();}
};
$('screen').onclick=async()=>{if(starting)return;starting=true;try{if(!token.value.trim())throw Error('请先输入启动令牌。');if(!video.requestVideoFrameCallback)throw Error('浏览器不支持视频帧回调。');if($('caption-share').checked)throw Error('字幕接入只支持本地视频，请先取消字幕分享。');
    trackStream=await navigator.mediaDevices.getDisplayMedia({video:{frameRate:{ideal:24,max:30}},audio:true});if(objectURL){URL.revokeObjectURL(objectURL);objectURL=null;}
    video.muted=true;video.srcObject=trackStream;if(video.readyState<1)await new Promise(resolve=>video.onloadedmetadata=resolve);await begin('shared_display',trackStream);
  }catch(e){await stop({unload:true});error(e);}finally{starting=false;refreshControls();}};
$('stop').onclick=()=>void stop();
function momentRange(data){const frames=data.frames||[],a=frames[0],b=frames.at(-1);const sameEpoch=a&&b&&a.epoch===b.epoch;return sameEpoch&&Number.isFinite(a.media_t)&&Number.isFinite(b.media_t)?`${time(a.media_t)} — ${time(b.media_t)}`:`刚才 ${Math.max(1,Math.round(data.end-data.start))} 秒`;}
async function selectMoment({focus=true}={}){
  const id=source?.source_id;if(!id||selecting)return null;const end=lastT,gen=generation;selecting=true;refreshControls();
  try{
    for(let i=0;i<5&&source?.source_id===id&&(inflight||queue.some(s=>s.t<=end));i++)await flush();
    const data=await(await api(`/sources/${id}/moments`,{end:Math.min(end,received),seconds:10})).json();if(gen!==generation)return null;
    clearMoment();currentMoment=data;$('moment').hidden=false;$('question').placeholder='聊聊这个片刻…';const range=momentRange(data);$('moment-range').textContent=range;$('review-range').textContent=range;
    const captions=data.captions;$('caption-review').hidden=!data.model_captions_permitted;
    if(data.model_captions_permitted){for(const line of captions.segments||[]){const p=document.createElement('p'),at=document.createElement('span');at.className='caption-time';at.textContent=time(line.first_observed_media_t);p.append(at,document.createTextNode(line.text));$('caption-lines').append(p);}
      $('caption-info').textContent=`字幕采样 ${captions.observed_samples||0} 次；未展示 ${captions.omitted_segments||0} 段。${captions.evicted_samples?'较早采样已过期。':''}${!captions.segments?.length?'这一段没有留存的字幕，不代表没有对白。':''}`;}
    $('summary').textContent=`捕获 ${data.observed_start.toFixed(2)}—${data.observed_end.toFixed(2)} 秒；${data.captured_frames} 帧中选取 ${data.selected_frames} 帧，缺口 ${data.gap_count}。`;
    await Promise.all(data.frames.map(async(frame,i)=>{const r=await api(`/moments/${data.moment_id}/frames/${i}`,null,'GET');const blob=await r.blob();if(gen!==generation||currentMoment!==data)return;
      const url=URL.createObjectURL(blob);viewURLs.push(url);const figure=document.createElement('figure'),img=document.createElement('img'),caption=document.createElement('figcaption');figure.dataset.index=String(i);img.src=url;img.alt=`实际捕获帧 ${i+1}`;caption.textContent=Number.isFinite(frame.media_t)?time(frame.media_t):`${frame.capture_t.toFixed(2)}s`;figure.append(img,caption);$('frames').append(figure);
      for(const node of [...$('frames').children].sort((a,b)=>Number(a.dataset.index)-Number(b.dataset.index)))$('frames').append(node);
      if(i===0){$('reference-image').src=url;$('reference-image').hidden=false;}
    }));
    if(gen!==generation||currentMoment!==data)return null;
    if(data.audio_available){const r=await api(`/moments/${data.moment_id}/audio`,null,'GET');const blob=await r.blob();if(gen!==generation||currentMoment!==data)return null;const url=URL.createObjectURL(blob);viewURLs.push(url);$('audio').src=url;$('audio').hidden=false;}
    $('audioInfo').textContent=data.audio_available?`音频覆盖 ${Math.round((data.audio.coverage_fraction||0)*100)}%${(data.audio.coverage_fraction||0)<1?'，缺口以静音表示':''}。`:'这段没有可回听的声音。';
    openChat();if(focus)$('question').focus();return data;
  }catch(e){if(gen===generation){clearMoment();error(e);}return null;}finally{selecting=false;refreshControls();}
}
$('recent').onclick=()=>void selectMoment();$('attach-recent').onclick=()=>void selectMoment();
$('review').onclick=()=>$('evidence').showModal();$('clear-moment').onclick=()=>{clearMoment();$('question').focus();};
$('composer').onsubmit=async event=>{
  event.preventDefault();const text=$('question').value.trim();if(!text||sending||selecting||(!source&&!homeScope))return;
  sending=true;refreshControls();message($('send-error'),'');
  try{
    // A removed reference stays removed. Ordinary conversation does not
    // silently capture/send another moment just because a source is active.
    const moment=currentMoment,id=source?.source_id;
    const reply=moment&&id?await(await api(`/sources/${id}/ask`,{moment_id:moment.moment_id,text})).json():await(await request(`/engrams/${encodeURIComponent(homeScope.engram_id)}/inject`,{content:text,source:'user'})).json();
    const receipt=reply.receipt||reply.event_id;if(typeof receipt!=='string')throw Error('发送结果未确认，请在 Home 核实。');
    chat.add({id:receipt,text,range:moment?momentRange(moment):''});openChat();
    if($('question').value.trim()===text){$('question').value='';$('question').style.height='auto';}
    if(currentMoment===moment)clearMoment();
  }catch(e){message($('send-error'),e.status&&e.status<500?`没有发出：${e.message}`:'发送结果未确认。请先在 Home 查看，避免重复发送；草稿仍在。');}
  finally{sending=false;refreshControls();}
};
window.addEventListener('pagehide',()=>{trackStream?.getTracks().forEach(t=>t.stop());source=null;queue=[];clearInterval(periodic);ac?.close();viewURLs.forEach(URL.revokeObjectURL);if(objectURL)URL.revokeObjectURL(objectURL);});
refreshControls();
