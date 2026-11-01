// dashboard.js — dashboard tab + digest + charts (Phase 3C)
// Extracted from app.js. Non-module: functions are global.
// Dependencies (resolved at call time):
//   - trendChart, comparisonChart (state.js)
//   - api (api-client.js)
//   - CHART_COLORS, _isLightTheme, _chartColors, _chartOpts, _chartScales,
//     fmtTime, fmtTimeShort (utils.js)
//   - loadSignalTab (signal.js)

function switchTab(tab){
  if(tab!=='signal' && _daemonStatusInterval){
    clearInterval(_daemonStatusInterval);
    _daemonStatusInterval=null;
  }
  document.querySelectorAll('.tab-btn').forEach(b=>b.classList.toggle('active',b.dataset.tab===tab));
  document.getElementById('viewMessages').classList.toggle('active',tab==='messages');
  document.getElementById('viewDashboard').classList.toggle('active',tab==='dashboard');
  document.getElementById('viewGroups').classList.toggle('active',tab==='groups');
  document.getElementById('viewSignal').classList.toggle('active',tab==='signal');
  document.getElementById('viewDigest').classList.toggle('active',tab==='digest');
  if(tab==='dashboard')loadDashboardTab();
  if(tab==='groups')loadGroupsView();
  if(tab==='signal')loadSignalTab();
  if(tab==='digest'){loadDigestTab();clearDigestUpdateFlag();}
}

async function loadDigestTab(){
  const latest=await api('/api/digest/latest');
  const latestEl=document.getElementById('digestLatest');
  if(latest&&latest.summary){
    latestEl.textContent=latest.summary;
    latestEl.style.color='var(--text-0)';
  }else{
    latestEl.innerHTML='<span style="color:var(--text-2)">尚无摘要。点击"生成新摘要"开始。</span>';
  }
  const hist=await api('/api/digest/history?limit=20');
  const histEl=document.getElementById('digestHistory');
  if(!hist||!hist.length){
    histEl.innerHTML='<span style="color:var(--text-2);font-size:var(--fs-sm)">无历史摘要</span>';
    return;
  }
  histEl.innerHTML=hist.map(d=>`
    <div style="padding:8px 10px;border:1px solid var(--border);border-radius:4px;cursor:pointer;background:var(--bg-2)" onclick="showDigestInLatest(this)" data-summary="${d.summary.replace(/"/g,'&quot;')}">
      <div style="font-size:var(--fs-xs);color:var(--text-2)">${d.created_at?d.created_at.slice(0,16).replace('T',' '):''} · ${d.signal_count}条信号 · ${d.from_at?d.from_at.slice(5,16).replace('T',' '):''} → ${d.to_at?d.to_at.slice(5,16).replace('T',' '):''}</div>
      <div style="font-size:var(--fs-sm);color:var(--text-1);margin-top:2px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${d.summary.slice(0,80)}...</div>
    </div>
  `).join('');
}
function showDigestInLatest(el){
  const s=el.dataset.summary;
  const latestEl=document.getElementById('digestLatest');
  latestEl.textContent=s;
  latestEl.style.color='var(--text-0)';
}
async function generateDigest(){
  const btn=document.getElementById('btnDigestGenerate');
  const status=document.getElementById('digestStatus');
  const lookbackSelect=document.getElementById('digestLookback');
  const lookbackHours=lookbackSelect?parseInt(lookbackSelect.value):0;
  btn.disabled=true;btn.textContent='生成中...';
  status.textContent=lookbackHours>0?`调用 LLM 中（${lookbackHours}h 窗口，约 10-30 秒）`:'调用 LLM 中（增量窗口，约 5-30 秒）';
  try{
    // Digest generation calls LLM (max_tokens=1024, free-form prose) — easily
    // exceeds the 15s timeout in api(). Use a dedicated 90s fetch window.
    const ctrl=new AbortController();
    const timer=setTimeout(()=>ctrl.abort(),90000);
    const body=lookbackHours>0?JSON.stringify({lookback_hours:lookbackHours}):'{}';
    const r=await fetch(API+'/api/digest/generate',{
      method:'POST',
      headers:{'Content-Type':'application/json','Authorization':'Bearer '+authToken},
      body,
      signal:ctrl.signal,
    }).finally(()=>clearTimeout(timer));
    if(r.status===401){authToken='';localStorage.removeItem('tgwatcher_token');location.reload();return;}
    if(!r.ok){
      const errBody=await r.json().catch(()=>({}));
      status.textContent='生成失败: '+(errBody.error||('HTTP '+r.status));
      return;
    }
    const result=await r.json();
    if(!result||!result.summary){status.textContent='生成失败: 空响应';return;}
    const latestEl=document.getElementById('digestLatest');
    latestEl.textContent=result.summary;latestEl.style.color='var(--text-0)';
    status.textContent=`生成完毕 · ${result.signal_count}条信号 · ${result.from_at?result.from_at.slice(5,16).replace('T',' '):''} → ${result.to_at?result.to_at.slice(5,16).replace('T',' '):''}`;
    loadDigestTab();
  }catch(e){
    status.textContent='生成失败: '+(e?.message||e);
  }finally{
    btn.disabled=false;btn.textContent='生成新摘要';
  }
}

async function loadDashboardTab(){
  const s=await api('/api/stats');if(!s)return;
  const days=s.earliest_message&&s.latest_message?Math.max(1,Math.round((new Date(s.latest_message+'Z')-new Date(s.earliest_message+'Z'))/86400000)):1;
  const avg=s.total_messages?Math.round(s.total_messages/days):0;
  document.getElementById('kpiTotal').textContent=(s.total_messages||0).toLocaleString();
  document.getElementById('kpiChats').textContent=s.monitored_chats||'0';
  document.getElementById('kpiAvgDay').textContent=avg.toLocaleString();
  const cs=await api('/api/crawl/status');
  const lastCrawl=cs?.last_crawl_at||s.latest_message;
  document.getElementById('kpiLastCrawl').textContent=lastCrawl?fmtTimeShort(lastCrawl):'--:--';
  loadTrendChart();loadHeatmap();loadComparisonChart();
}

async function loadTrendChart(){
  const days=parseInt(document.getElementById('trendPeriod')?.value)||30;
  const d=await api('/api/stats/trend?days='+days);if(!d||!d.labels)return;
  const datasets=(d.datasets||[]).map((ds,i)=>({label:ds.chat_title,data:ds.data,borderColor:CHART_COLORS[i%CHART_COLORS.length],backgroundColor:CHART_COLORS[i%CHART_COLORS.length]+'18',fill:true,tension:0.3,pointRadius:0,borderWidth:1.5}));
  if(trendChart)trendChart.destroy();const el=document.getElementById('trendChart');if(!el)return;
  trendChart=new Chart(el,{type:'line',data:{labels:d.labels,datasets},options:{..._chartOpts(),scales:_chartScales()}});
}

async function loadHeatmap(){
  const d=await api('/api/stats/heatmap');if(!d||!d.data)return;
  const canvas=document.getElementById('heatmapCanvas');if(!canvas)return;
  const ctx=canvas.getContext('2d');
  const W=canvas.width=canvas.parentElement.clientWidth||600,H=canvas.height=180;
  const cellW=Math.floor(W/25),cellH=Math.floor(H/8),maxC=(d.data&&d.data.length)?Math.max(1,...d.data.map(e=>(e.count||0))):1;
  const light=_isLightTheme();
  const labelColor=light?'#718096':'#4a5568';
  const intenseTextColor=light?'#1a2030':'#e8ecf1';
  ctx.clearRect(0,0,W,H);
  const dayLabels=['日','一','二','三','四','五','六'];
  ctx.font='10px Inter';ctx.fillStyle=labelColor;
  for(let dow=0;dow<7;dow++)ctx.fillText(dayLabels[dow],2,dow*cellH+cellH/2+3);
  for(const e of d.data){
    const x=(e.hour+1)*cellW,y=e.dow*cellH,intensity=e.count/maxC;
    const fillColor=light?`rgba(0,119,182,${0.06+intensity*0.94})`:`rgba(0,229,255,${0.08+intensity*0.92})`;
    ctx.fillStyle=fillColor;ctx.fillRect(x,y,cellW-1,cellH-1);
    if(intensity>0.5){ctx.fillStyle=intenseTextColor;ctx.font='9px JetBrains Mono';ctx.fillText(e.count,x+2,y+cellH/2+3)}
  }
  ctx.font='9px JetBrains Mono';ctx.fillStyle=labelColor;
  for(let h=0;h<24;h+=2)ctx.fillText(h+'时',(h+1)*cellW,7*cellH+12);
}

async function loadComparisonChart(){
  const d=await api('/api/stats/comparison');if(!d||!d.groups)return;
  const top=d.groups.slice(0,10);
  if(comparisonChart)comparisonChart.destroy();const el=document.getElementById('comparisonChart');if(!el)return;
  const c=_chartColors();
  comparisonChart=new Chart(el,{type:'bar',data:{labels:top.map(g=>(g.chat_title||g.chat_id+'').slice(0,20)),datasets:[
    {label:'消息数',data:top.map(g=>(g.msg_count||0)),backgroundColor:'#00e5ff33',borderColor:'#00e5ff',borderWidth:1},
    {label:'发送者',data:top.map(g=>(g.active_senders||0)),backgroundColor:'#00ff8833',borderColor:'#00ff88',borderWidth:1},
  ]},options:{..._chartOpts(),indexAxis:'y',scales:{x:{grid:{color:c.grid},ticks:{color:c.tick,font:{family:"'JetBrains Mono'",size:10}},border:{color:c.border}},y:{grid:{color:c.grid},ticks:{color:c.legend,font:{size:11}},border:{color:c.border}}}}});
}
