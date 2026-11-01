// signal.js — signal tab functions (Phase 3C)
// Extracted from app.js. Non-module: functions are global.
// Dependencies (resolved at call time):
//   - signalTrendChart, signalEventChart (state.js)
//   - api (api-client.js)
//   - esc, fmtTime (utils.js)

let _daemonStatusInterval=null;
let _daemonFailCount=0;

async function loadSignalTab(){
  const s=await api('/api/signal/stats');if(!s)return;
  const total=s.total||0;
  const dir=s.direction||{};
  const bullishPct=total?Math.round((dir.bullish||0)/total*100):0;
  const bearishPct=total?Math.round((dir.bearish||0)/total*100):0;
  document.getElementById('signalKpiTotal').textContent=total.toLocaleString();
  document.getElementById('signalKpiBullish').textContent=bullishPct+'%';
  document.getElementById('signalKpiBearish').textContent=bearishPct+'%';
  document.getElementById('signalKpiUrgency').textContent=s.avg_magnitude?Number(s.avg_magnitude).toFixed(2):'-';
  loadSignalTrendChart();
  loadSignalEventChart(s.event_types||{});
  loadSignalTable();
  checkDaemonStatus();
}

async function loadSignalTrendChart(){
  const d=await api('/api/signal/trend?days=30');if(!d||!d.trend)return;
  const labels=Object.keys(d.trend).sort();
  const avgDirection=labels.map(l=>(d.trend[l]&&d.trend[l].avg_direction||0));
  const avgMagnitude=labels.map(l=>(d.trend[l]&&d.trend[l].avg_magnitude||0));
  const counts=labels.map(l=>(d.trend[l]&&d.trend[l].count||0));
  if(signalTrendChart)signalTrendChart.destroy();
  const el=document.getElementById('signalTrendChart');if(!el)return;
  signalTrendChart=new Chart(el,{type:'line',data:{labels,datasets:[
    {label:'方向均值',data:avgDirection,borderColor:'#00e5ff',backgroundColor:'#00e5ff18',fill:true,tension:0.3,pointRadius:0,borderWidth:1.5,yAxisID:'y'},
    {label:'幅度均值',data:avgMagnitude,borderColor:'#00ff88',backgroundColor:'#00ff8818',fill:true,tension:0.3,pointRadius:0,borderWidth:1.5,yAxisID:'y'},
    {label:'消息数',data:counts,borderColor:'#ffb300',backgroundColor:'#ffb30018',fill:false,tension:0.3,pointRadius:0,borderWidth:1,borderDash:[3,3],yAxisID:'y1'},
  ]},options:{responsive:true,maintainAspectRatio:false,scales:{x:{ticks:{color:'#6b7a8d',font:{size:10}},grid:{color:'#1a1f2e'}},y:{ticks:{color:'#6b7a8d',font:{size:10}},grid:{color:'#1a1f2e'},min:-1,max:1},y1:{position:'right',ticks:{color:'#6b7a8d',font:{size:10}},grid:{display:false},min:0}},plugins:{legend:{labels:{color:'#a0aec0',font:{size:11}}}}}});
}

function loadSignalEventChart(eventTypes){
  const labels=Object.keys(eventTypes);
  const data=labels.map(l=>eventTypes[l]);
  const colors=['#00e5ff','#00ff88','#ffb300','#ff3d71','#a855f7','#f472b6','#38bdf8','#6b7a8d'];
  if(signalEventChart)signalEventChart.destroy();
  const el=document.getElementById('signalEventChart');if(!el)return;
  signalEventChart=new Chart(el,{type:'doughnut',data:{labels,datasets:[{data,backgroundColor:colors.slice(0,labels.length),borderWidth:0}]},options:{responsive:true,maintainAspectRatio:false,plugins:{legend:{position:'right',labels:{color:'#a0aec0',font:{size:11},padding:8}}}}});
}

function computeSignalScore(f){
  // Mirror SignalEngine._build_signal_payload formula:
  // direction * magnitude * confidence * (0.5 + 0.5 * urgency), range [-1,1]
  const d=Number(f.direction||0),m=Number(f.magnitude||0),u=Number(f.urgency||0),c=Number(f.confidence||0);
  if([d,m,u,c].some(x=>Number.isNaN(x)))return 0;
  return Math.round(d*m*c*(0.5+0.5*u)*10000)/10000;
}
function computeExpiresAt(f){
  // date + 2 * halflife_min, ISO string with tz suffix
  if(!f.date)return null;
  const d=new Date(f.date.endsWith('Z')?f.date:f.date+'Z');
  if(isNaN(d))return null;
  const hl=Number(f.halflife_min||0);
  if(!hl)return null;
  return new Date(d.getTime()+hl*2*60000);
}
function fmtExpiresRel(expiresAt){
  if(!expiresAt)return'-';
  const now=new Date(),exp=new Date(expiresAt);
  if(isNaN(exp))return'-';
  const diffMin=Math.round((exp-now)/60000);
  if(diffMin<0)return'<span style="color:#6b7a8d">已过期</span>';
  if(diffMin<60)return'剩 '+diffMin+' 分';
  const h=Math.floor(diffMin/60),m=diffMin%60;
  return'剩 '+h+'h'+m+'m';
}
async function loadSignalTable(){
  const d=await api('/api/signal/factors?page_size=50');if(!d||!d.items)return;
  const tbody=document.getElementById('signalTableBody');
  tbody.innerHTML=d.items.map(f=>{
    const dir=f.direction||0;
    const dirColor=dir>0.1?'#00ff88':dir<-0.1?'#ff3d71':'#6b7a8d';
    const dirLabel=dir>0.1?'利多':dir<-0.1?'利空':'中性';
    let symbols=[];
    try{symbols=JSON.parse(f.symbols||'[]')}catch(_){symbols=[]}
    const symbolsStr=symbols.join(',');
    const text=esc((f.text||'').slice(0,50));
    const date=fmtTime(f.date);
    const score=computeSignalScore(f);
    const scoreColor=score>0.05?'#00ff88':score<-0.05?'#ff3d71':'#6b7a8d';
    const scoreStr=(score>=0?'+':'')+score.toFixed(3);
    const expiresAt=computeExpiresAt(f);
    const expiresRel=fmtExpiresRel(expiresAt);
    return `<tr><td style="white-space:nowrap">${date}</td><td style="color:${dirColor}">${dirLabel} ${dir.toFixed(2)}</td><td>${f.event_type||'-'}</td><td>${(f.magnitude||0).toFixed(2)}</td><td>${(f.urgency||0).toFixed(2)}</td><td>${(f.confidence||0).toFixed(2)}</td><td style="color:${scoreColor}">${scoreStr}</td><td>${f.halflife_min||'-'}</td><td>${expiresRel}</td><td style="max-width:150px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${text}</td></tr>`;
  }).join('');
}

async function checkDaemonStatus(){
  try{
    const s=await api('/api/signal/daemon');
    if(!s){ _daemonFailCount++; if(_daemonFailCount>=3){ const pill=document.getElementById('signalDaemonPill'); if(pill){ pill.textContent='daemon: 错误 — 见日志'; pill.style.background='#ff3d7120'; } } return; }
    _daemonFailCount=0;
    const pill=document.getElementById('signalDaemonPill'); if(!pill)return;
    let text, bg;
    if(s.running){
      text=`daemon: 处理中 ${s.pending||0} 条`;
      bg='#00ff8820';
    } else if(s.last_batch_at){
      const t=(s.last_batch_at||'').slice(11,16);
      text=`daemon: 上次 ${s.last_batch_count||0} 条 @ ${t} — pending ${s.pending||0}`;
      bg='#1a1f2e';
    } else {
      text=`daemon: 待命 — pending ${s.pending||0}`;
      bg='#1a1f2e';
    }
    pill.textContent=text;
    pill.style.background=bg;
    pill.dataset.pending=s.pending||0;
    if(!_daemonStatusInterval){
      _daemonStatusInterval=setInterval(checkDaemonStatus,10000);
    }
  }catch(e){
    _daemonFailCount++;
    console.warn('checkDaemonStatus error:',e);
  }
}

// ===== 处理指定范围 (手动触发 batch) =====
let _signalProcessPollId=null;

async function toggleSignalProcessForm(){
  const form=document.getElementById('signalProcessForm');
  if(!form)return;
  const open=form.style.display==='none';
  if(open){
    // 填充群组下拉(只在首次打开时拉取)
    const sel=document.getElementById('signalProcessChat');
    if(sel.options.length<=1){
      const chats=await api('/api/chats');
      if(chats){
        chats.forEach(c=>{
          sel.insertAdjacentHTML('beforeend',
            `<option value="${c.chat_id}">${esc(c.chat_title||'ID:'+c.chat_id)} (${c.msg_count})</option>`);
        });
      }
    }
    form.style.display='flex';
  }else{
    form.style.display='none';
  }
}

async function startSignalProcess(){
  const chatRaw=document.getElementById('signalProcessChat').value;
  const dateFrom=document.getElementById('signalProcessDateFrom').value;
  const dateTo=document.getElementById('signalProcessDateTo').value;
  const overwrite=document.getElementById('signalProcessOverwrite').checked;
  const hint=document.getElementById('signalProcessHint');
  const btns=document.querySelectorAll('#signalProcessForm .btn-cyan');
  const btn=btns[0]; if(btn)btn.disabled=true;
  if(hint)hint.textContent='提交中...';
  try{
    const body={overwrite};
    if(chatRaw)body.chat_id=parseInt(chatRaw,10);
    if(dateFrom)body.date_from=dateFrom;
    if(dateTo)body.date_to=dateTo;
    const r=await api('/api/signal/process',{
      method:'POST',
      body:JSON.stringify(body),
    });
    if(!r){ if(hint)hint.textContent='提交失败 — 见日志'; btn.disabled=false; return; }
    if(r.error){
      if(hint)hint.textContent=r.error;
      btn.disabled=false;
      return;
    }
    if(hint)hint.textContent='已启动';
    _pollSignalProcess();
  }catch(e){
    if(hint)hint.textContent='异常: '+e.message;
    btn.disabled=false;
  }
}

function _pollSignalProcess(){
  const prog=document.getElementById('signalProgress');
  if(_signalProcessPollId)clearInterval(_signalProcessPollId);
  _signalProcessPollId=setInterval(async()=>{
    try{
      const s=await api('/api/signal/process/status');
      if(!s)return;
      if(s.running){
        if(prog)prog.textContent=`处理中 ${s.processed}/${s.total} (完成 ${s.completed}, 失败 ${s.failed})`;
      }else{
        if(_signalProcessPollId){clearInterval(_signalProcessPollId);_signalProcessPollId=null;}
        if(prog)prog.textContent='';
        const hint=document.getElementById('signalProcessHint');
        if(hint)hint.textContent=s.finished_at?`完成: ${s.completed}/${s.total} (失败 ${s.failed})`:'';
        // 收起表单 + 刷新表格 + 重置"开始处理"按钮
        const form=document.getElementById('signalProcessForm');
        if(form)form.style.display='none';
        const startBtns=document.querySelectorAll('#signalProcessForm .btn-cyan');
        if(startBtns[0])startBtns[0].disabled=false;
        loadSignalTab();
      }
    }catch(e){console.warn('poll error:',e);}
  },1500);
}
