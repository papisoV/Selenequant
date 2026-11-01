// export.js — message export modal (Phase 3C)
// Extracted from app.js. Non-module: functions are global.
// Dependencies (resolved at call time):
//   - currentChat, _exportPreviewTimer, authToken, API (state.js)
//   - api (api-client.js)
//   - esc (utils.js)
//   - showToast (render.js)

async function openExportModal(){
  const chats=await api('/api/chats');
  const sel=document.getElementById('exportGroupSelect');
  sel.innerHTML='<option value="">全部群组</option>';
  if(chats){
    chats.forEach(c=>{
      sel.innerHTML+=`<option value="${c.chat_id}">${esc(c.chat_title||'ID:'+c.chat_id)} (${c.msg_count})</option>`;
    });
  }
  if(currentChat){
    sel.value=currentChat;
  }
  setExportQuickDate('7d');
  document.getElementById('exportModal').style.display='flex';
}

function closeExportModal(){
  document.getElementById('exportModal').style.display='none';
}

function _localDateStr(d){
  const y=d.getFullYear(),m=String(d.getMonth()+1).padStart(2,'0'),day=String(d.getDate()).padStart(2,'0');
  return `${y}-${m}-${day}`;
}

function setExportQuickDate(preset){
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
  document.getElementById('exportDateFrom').value=_localDateStr(from);
  document.getElementById('exportDateTo').value=_localDateStr(to);
  document.querySelectorAll('.export-quick-btn').forEach(b=>b.classList.toggle('active',b.dataset.preset===preset));
  updateExportPreview();
}

function clearExportQuickDate(){
  document.querySelectorAll('.export-quick-btn').forEach(b=>b.classList.remove('active'));
}

async function updateExportPreview(){
  clearTimeout(_exportPreviewTimer);
  _exportPreviewTimer=setTimeout(async()=>{
    const params=_buildExportQueryParams(1);
    const data=await api('/api/messages?'+params);
    const el=document.getElementById('exportPreview');
    if(data){
      const fmt=document.querySelector('input[name="exportFormat"]:checked')?.value||'json';
      el.textContent=`将导出 ${data.total} 条消息 (${fmt.toUpperCase()})`;
    }else{
      el.textContent='查询失败';
    }
  },300);
}

function _buildExportQueryParams(size){
  const params=new URLSearchParams();
  const chatId=document.getElementById('exportGroupSelect').value;
  if(chatId)params.set('chat_id',chatId);
  const df=document.getElementById('exportDateFrom').value;
  if(df)params.set('date_from',df);
  const dt=document.getElementById('exportDateTo').value;
  if(dt)params.set('date_to',dt);
  params.set('page','1');
  params.set('size',String(size||1));
  return params;
}

async function doExport(){
  const fmt=document.querySelector('input[name="exportFormat"]:checked')?.value||'json';
  const params=new URLSearchParams({format:fmt});
  const chatId=document.getElementById('exportGroupSelect').value;
  if(chatId)params.set('chat_id',chatId);
  const df=document.getElementById('exportDateFrom').value;
  if(df)params.set('date_from',df);
  const dt=document.getElementById('exportDateTo').value;
  if(dt)params.set('date_to',dt);
  if(!df&&!dt){
    showToast('请选择日期范围','error');return;
  }
  const r=await fetch(API+'/api/messages/export?'+params,{headers:{'Authorization':'Bearer '+authToken}});
  if(!r.ok){showToast('导出失败','error');return}
  const blob=await r.blob();
  const a=document.createElement('a');a.href=URL.createObjectURL(blob);
  const ext=fmt==='markdown'?'md':fmt;
  a.download='tgwatcher_export.'+ext;a.click();
  closeExportModal();
  showToast('已导出 '+fmt.toUpperCase(),'success');
}
