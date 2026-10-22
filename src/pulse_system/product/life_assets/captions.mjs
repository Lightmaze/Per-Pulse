/** Bounded plain-text SRT/WebVTT cues. No network, DOM insertion, or model call. */
export const MAX_FILE_BYTES = 1024 * 1024;
const bytes = text => new TextEncoder().encode(text).length;
const fail = message => { throw Error(message); };
function clock(value) {
  const m = /^(?:(\d{2,}):)?([0-5]\d):([0-5]\d)[.,](\d{3})$/.exec(value);
  if (!m) fail('字幕时间格式不支持；请使用 SRT 或 WebVTT。');
  const seconds = Number(m[1] || 0) * 3600 + Number(m[2]) * 60 + Number(m[3]) + Number(m[4]) / 1000;
  if (!Number.isFinite(seconds) || seconds > 1e9) fail('字幕时间超出范围。');
  return seconds;
}
function plainText(value) {
  if (/<(?:\d{2,}:)?\d\d:\d\d[.,]\d{3}>/.test(value))
    fail('暂不支持逐字时间戳字幕；请转换为普通分段字幕。');
  const entities = {'&amp;':'&', '&lt;':'<', '&gt;':'>', '&nbsp;':' ', '&lrm;':'\u200e', '&rlm;':'\u200f'};
  return value.replace(/<v(?:\.[^ >]+)*[ \t]+([^>]+)>/g, '[$1] ')
    .replace(/<\/?(?:b|i|u|c|v|lang|ruby|rt)(?:[. ][^>]*)?>/g, '')
    .replace(/&(amp|lt|gt|nbsp|lrm|rlm);/g, name => entities[name]).trim();
}
export function parseSubtitles(input) {
  if (typeof input !== 'string' || !input.trim() || input.includes('\0') || bytes(input) > MAX_FILE_BYTES)
    fail('字幕须为不超过 1 MiB 的 UTF-8 文本。');
  const normalized = input.replace(/^\uFEFF/, '').replace(/\r\n?/g, '\n').trim();
  const blocks = normalized.split(/\n[ \t]*\n/);
  const webvtt = /^WEBVTT(?:[ \t]|\n|$)/.test(normalized);
  if (webvtt) {
    const header = blocks.shift();
    if (/X-TIMESTAMP-MAP|-->/.test(header)) fail('字幕头部或时间映射不支持。');
  }
  const cues = [];
  for (const block of blocks) {
    if (webvtt && /^(NOTE(?:[ \t\n]|$)|STYLE(?:\n|$)|REGION(?:\n|$))/.test(block)) continue;
    const lines = block.split('\n');
    if (!lines[0].includes('-->')) lines.shift(); // identifier, never used as authority
    const timing = /^(\S+)[ \t]+-->[ \t]+(\S+)(?:[ \t]+.*)?$/.exec(lines.shift() || '');
    if (!timing) fail('存在无法读取的字幕段，未导入部分结果。');
    const start = clock(timing[1]), end = clock(timing[2]);
    const text = plainText(lines.join('\n'));
    if (end <= start || !text || bytes(text) > 4096) fail('字幕段为空、过长或时间范围无效。');
    if (cues.length >= 10000) fail('字幕超过 10,000 段。');
    cues.push({id:cues.length, start, end, text});
  }
  if (!cues.length) fail('没有可读取的字幕段。');
  cues.sort((a,b) => a.start-b.start || a.id-b.id);
  // Validate simultaneous payload bounds before playback; do not silently omit voices.
  const edges = cues.flatMap(cue => [{t:cue.start,n:1,size:bytes(cue.text)}, {t:cue.end,n:-1,size:-bytes(cue.text)}]);
  edges.sort((a,b) => a.t-b.t || a.n-b.n);
  let count=0, size=0;
  for (const edge of edges) {
    count+=edge.n; size+=edge.size;
    if (count>8 || size>4096) fail('同时显示的字幕超过当前接入上限。');
  }
  let maximum=0;
  const prefixEnd = cues.map(cue => (maximum=Math.max(maximum,cue.end)));
  return {cues, prefixEnd, format:webvtt?'webvtt':'srt'};
}
export function cuesAt(track, mediaTime) {
  if (!Number.isFinite(mediaTime) || mediaTime < 0) fail('播放时间无效。');
  let lo=0, hi=track.cues.length;
  while(lo<hi) { const mid=(lo+hi)>>>1; if(track.cues[mid].start<=mediaTime) lo=mid+1; else hi=mid; }
  const active=[];
  for(let i=lo-1;i>=0 && track.prefixEnd[i]>mediaTime;i--)
    if(track.cues[i].end>mediaTime) active.push(track.cues[i]);
  return active.reverse().map(cue=>({...cue}));
}
