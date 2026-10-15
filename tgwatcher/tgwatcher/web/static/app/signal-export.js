// signal-export.js — signal export modal (Phase 3C)
// Extracted from app.js. Non-module: functions are global.
// Dependencies (resolved at call time):
//   - _signalExportPreviewTimer, authToken, API (state.js)
//   - api (api-client.js)
//   - esc, _localDateStr (utils.js / export.js)
//   - showToast (render.js)

async function openSignalExportModal(){
  const chats=await api('/api/chats');
  const sel=document.getElementById('signalExportGroupSelect');
  sel.innerHTML='<option value="">全部群组</option>';
  if(chats){
    chats.forEach(c=>{
      sel.innerHTML+=`<option value="${c.chat_id}">${esc(c.chat_title||'ID:'+c.chat_id)} (${c.msg_count})</option>`;
    });
  }
  setSignalExportQuickDate('7d');
  document.getElementById('signalExportModal').style.display='flex';
}

function closeSignalExportModal(){
  document.getElementById('signalExportModal').style.display='none';
}

function setSignalExportQuickDate(preset){
  const now=new Date();
  const today=new Date(now.getFullYear(),now.getMonth(),now.getDate());
  let from,to;
  switch(preset){
    case 'today': from=today;to=today; break;
    case 'yesterday': from=new Date(today);from.setDate(from.getDate()-1);to=new Date(today);to.setDate(to.getDate()-1); break;
    case '7d': from=new Date(today);from.setDate(from.getDate()-6);to=today; break;
    case '30d': from=new Date(today);from.setDate(from.getDate()-29);to=today; break;
    case 'this_month': from=new Date(now.getFullYear(),now.getMonth(),1);to=today; break;
    case 'last_month': from=new Date(now.getFullYear(),now.getMonth()-1,1);to=new Date(now.getFullYear(),now.getMonth(),0); break;
    default: return;
  }
  document.getElementById('signalExportDateFrom').value=_localDateStr(from);
  document.getElementById('signalExportDateTo').value=_localDateStr(to);
  document.querySelectorAll('#signalExportModal .export-quick-btn').forEach(b=>b.classList.toggle('active',b.dataset.preset===preset));
  updateSignalExportPreview();
}

function clearSignalExportQuickDate(){
  document.querySelectorAll('#signalExportModal .export-quick-btn').forEach(b=>b.classList.remove('active'));
}

async function updateSignalExportPreview(){
  clearTimeout(_signalExportPreviewTimer);
  _signalExportPreviewTimer=setTimeout(async()=>{
    const el=document.getElementById('signalExportPreview');
    const df=document.getElementById('signalExportDateFrom').value;
    const dt=document.getElementById('signalExportDateTo').value;
    if(!df&&!dt){el.textContent='请选择日期范围';return}
    const fmt=document.querySelector('input[name="signalExportFormat"]:checked')?.value||'json';
    // Use export endpoint with count_only to get count
    const params=new URLSearchParams();
    params.set('format','json');
    params.set('count_only','true');
    const chatId=document.getElementById('signalExportGroupSelect').value;
    if(chatId)params.set('chat_id',chatId);
    if(df)params.set('date_from',df);
    if(dt)params.set('date_to',dt);
    try{
      const data=await api('/api/signals/export?'+params);
      if(data && data.count!==undefined){
        el.textContent=`将导出 ${data.count} 条分析结果 (${fmt.toUpperCase()})`;
      }else if(data && data.error){
        el.textContent=data.error;
      }else{
        el.textContent='查询失败';
      }
    }catch(e){
      el.textContent='查询失败';
    }
  },300);
}

function _buildSignalExportParams(){
  const params=new URLSearchParams();
  const chatId=document.getElementById('signalExportGroupSelect').value;
  if(chatId)params.set('chat_id',chatId);
  const df=document.getElementById('signalExportDateFrom').value;
  if(df)params.set('date_from',df);
  const dt=document.getElementById('signalExportDateTo').value;
  if(dt)params.set('date_to',dt);
  const source=document.querySelector('input[name="signalExportSource"]:checked')?.value;
  if(source)params.set('llm_model',source);
  const filter=document.querySelector('input[name="signalExportFilter"]:checked')?.value;
  if(filter)params.set('is_signal',filter==='signal'?'true':'false');
  return params;
}

async function doSignalExport(){
  const fmt=document.querySelector('input[name="signalExportFormat"]:checked')?.value||'json';
  const params=_buildSignalExportParams();
  params.set('format',fmt);
  const df=document.getElementById('signalExportDateFrom').value;
  const dt=document.getElementById('signalExportDateTo').value;
  if(!df&&!dt){
    showToast('请选择日期范围','error');return;
  }
  const r=await fetch(API+'/api/signals/export?'+params,{headers:{'Authorization':'Bearer '+authToken}});
  if(!r.ok){showToast('导出失败','error');return}
  const blob=await r.blob();
  const a=document.createElement('a');a.href=URL.createObjectURL(blob);
  const ext=fmt==='markdown'?'md':fmt==='sqlite'?'db':fmt;
  const prefix=fmt==='sqlite'?'tg_factors':'signals_export';
  a.download=prefix+'.'+ext;a.click();
  closeSignalExportModal();
  showToast('已导出信号分析 ('+fmt.toUpperCase()+')','success');
}
