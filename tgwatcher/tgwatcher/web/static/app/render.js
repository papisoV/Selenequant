// render.js — DOM rendering functions (Phase 3B)
// Extracted from app.js. Non-module: functions are global.
// Dependencies (resolved at call time):
//   - crawlRunning, autoPollState, listenerState, webhookState (app.js globals)
//   - fmtTime, _fmtDuration (utils.js)
//   - loadCrawlStatus (app.js)

function renderPagination(total,page,size){
  const pages=Math.ceil(total/size)||1;
  document.getElementById('pageInfo').textContent=`${total} 条 · ${page}/${pages} 页`;
  const btns=[];const start=Math.max(1,page-3);const end=Math.min(pages,page+3);
  if(page>1)btns.push(`<span class="page-btn" onclick="loadMessages(${page-1})">‹</span>`);
  for(let i=start;i<=end;i++)btns.push(`<span class="page-btn${i===page?' active':''}" onclick="loadMessages(${i})">${i}</span>`);
  if(page<pages)btns.push(`<span class="page-btn" onclick="loadMessages(${page+1})">›</span>`);
  document.getElementById('pageBtns').innerHTML=btns.join('');
}

function updateCrawlUI(s){
  crawlRunning=s.running;
  const dot=document.getElementById('crawlBarDot');
  const status=document.getElementById('crawlBarStatus');
  const modeEl=document.getElementById('crawlBarMode');
  const detail=document.getElementById('crawlBarDetail');
  const counts=document.getElementById('crawlBarCounts');
  const meta=document.getElementById('crawlBarMeta');
  const progress=document.getElementById('crawlBarProgress');
  const btnStart=document.getElementById('btnStart');
  const btnStop=document.getElementById('btnStop');

  const modeLabels={'incremental':'增量','full':'全量','date_range':'日期范围','catchup':'补爬'};
  dot.className='crawl-bar__dot'+(s.running?' running':'')+(s.error?' error':'');
  if(s.running){
    status.textContent='爬取中';
    modeEl.textContent=modeLabels[s.mode]||s.mode;modeEl.style.display='';
    btnStart.style.display='none';btnStop.style.display='';
    hideDateRangePanel();

    const totalGroups=s.total_groups||1;
    const completedGroups=s.completed_groups||0;
    const groupIdx=s.current_group_index||0;
    const pct=Math.round(((completedGroups)+(s.current_group_fetched?0.5:0))/totalGroups*100);
    progress.style.width=Math.min(pct,100)+'%';
    progress.classList.add('running');

    // Detail: group progress
    const groupProgress=groupIdx>0?`[${groupIdx}/${totalGroups}] `:'';
    detail.textContent=groupProgress+(s.current_group||'—');

    // Counts: fetched/saved + per-group
    const totalFetched=s.total_fetched||0;
    const totalSaved=s.total_saved||0;
    const gf=s.current_group_fetched||0;
    const gs=s.current_group_saved||0;
    let countsText=`↑${totalFetched} ↓${totalSaved}`;
    if(gf>0)countsText+=`  (本组: ${gs}/${gf})`;
    counts.textContent=countsText;

    // Meta: speed + elapsed + ETA
    const speed=s.speed||0;
    const elapsed=s.elapsed_seconds||0;
    const eta=s.eta_seconds||0;
    const parts=[];
    if(elapsed>0)parts.push(_fmtDuration(elapsed));
    if(speed>0)parts.push(`${speed}条/分`);
    if(eta>0)parts.push('剩余'+_fmtDuration(eta));
    meta.textContent=parts.join(' · ');

    // Update detail panel
    updateCrawlDetail(s);
  }else{
    status.textContent=s.error?'错误':'空闲';
    modeEl.style.display='';btnStart.style.display='';btnStop.style.display='none';
    progress.style.width='0%';progress.classList.remove('running');
    if(s.error)detail.textContent='错误: '+s.error.slice(0,60);
    else if(s.last_crawl_at)detail.textContent='上次: '+fmtTime(s.last_crawl_at);
    else detail.textContent='暂无爬取数据';
    counts.textContent='';
    meta.textContent='';

    // Hide detail panel when crawl stops
    const dp=document.getElementById('crawlDetail');
    if(dp.style.display!=='none')updateCrawlDetail(s);
  }
  const connDot=document.getElementById('connDot');
  connDot.className='conn-dot'+(s.running?' running':'');
  const topDot=document.getElementById('crawlTopDot');
  if(topDot)topDot.className='conn-dot'+(s.running?' running':'')+(s.error?' error':'');
  const topStatus=document.getElementById('crawlTopStatus');
  if(topStatus)topStatus.textContent=status.textContent;
  const topMode=document.getElementById('crawlTopMode');
  if(topMode){topMode.textContent=modeEl.textContent;topMode.style.display=modeEl.style.display}
}

function updateCrawlDetail(s){
  const modeLabels={'incremental':'增量','full':'全量','date_range':'日期范围','catchup':'补爬','idle':'—'};
  document.getElementById('cdKpiMode').textContent=modeLabels[s.mode]||s.mode||'—';
  const gi=s.current_group_index||0,tg=s.total_groups||0;
  document.getElementById('cdKpiGroup').textContent=gi>0?`${gi}/${tg}`:'—';
  document.getElementById('cdKpiFetched').textContent=(s.total_fetched||0).toLocaleString();
  document.getElementById('cdKpiSaved').textContent=(s.total_saved||0).toLocaleString();
  const speed=s.speed||0;
  document.getElementById('cdKpiSpeed').textContent=speed>0?speed+'条/分':'—';
  document.getElementById('cdKpiElapsed').textContent=s.elapsed_seconds>0?_fmtDuration(s.elapsed_seconds):'—';
  document.getElementById('cdKpiEta').textContent=s.eta_seconds>0?_fmtDuration(s.eta_seconds):'—';

  const totalGroups=s.total_groups||1;
  const completed=s.completed_groups||0;
  const gf=s.current_group_fetched||0;
  const pct=Math.round((completed+(gf?0.5:0))/totalGroups*100);
  document.getElementById('cdProgressFill').style.width=Math.min(pct,100)+'%';
  document.getElementById('cdProgressPct').textContent=Math.min(pct,100)+'%';

  document.getElementById('cdCurrentGroup').textContent=s.current_group||'—';
  const gf2=s.current_group_fetched||0,gs2=s.current_group_saved||0;
  document.getElementById('cdCurrentCounts').textContent=gf2>0?`拉取 ${gf2} · 保存 ${gs2}`:'';
}

function refreshAutoPollUI(){
  const el=document.getElementById('autoPollNext');if(!el)return;
  const active=autoPollState.filter(s=>s.enabled);
  if(active.length===0){el.style.display='none';return}
  // pick the soonest-expiring one
  const next=active.reduce((a,b)=>(a.remaining_seconds||0)<(b.remaining_seconds||0)?a:b);
  el.style.display='inline';
  const sec=Math.max(0,Math.floor(next.remaining_seconds||0));
  el.textContent='⟳ '+next.name+' '+sec+'s';
}

function refreshListenerBadge(d){
  if(d)listenerState=d;
  const el=document.getElementById('listenerBadge');if(!el)return;
  if(listenerState.enabled){
    const names=listenerState.groups||[];
    el.style.display='inline';
    el.style.color='var(--green)';
    el.textContent='● 实时监听 '+(names.length?names.map(g=>g.name||g).join(', '):'');
  }else{
    el.style.display='inline';
    el.style.color='var(--text-2)';
    el.textContent='○ 实时监听 关';
  }
}

function refreshWebhookBadge(d){
  if(d&&d.url){
    // Failure event from SSE
    const el=document.getElementById('webhookBadge');if(!el)return;
    el.style.display='inline';
    el.style.color='var(--red)';
    el.textContent='✗ Webhook '+d.url+' 失败 x'+(d.fail_count||1);
    return;
  }
  const el=document.getElementById('webhookBadge');if(!el)return;
  if(webhookState.enabled){
    el.style.display='inline';
    el.style.color='var(--cyan)';
    const ok=(webhookState.endpoints||[]).filter(e=>e.enabled).length;
    el.textContent='↪ Webhook '+ok+' 端点';
  }else{
    el.style.display='none';
  }
}

// ===== TOAST =====
function showToast(msg,type='info'){
  const t=document.createElement('div');t.className='toast toast-'+type;t.textContent=msg;
  document.body.appendChild(t);setTimeout(()=>t.remove(),5000);
}
