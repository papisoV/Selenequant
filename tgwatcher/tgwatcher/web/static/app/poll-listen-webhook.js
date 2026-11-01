// poll-listen-webhook.js — auto-poll, listener, webhook (Phase 3C)
// Extracted from app.js. Non-module: functions are global.
// Dependencies (resolved at call time):
//   - autoPollState, autoPollTimer, listenerState, webhookState (state.js)
//   - api (api-client.js)
//   - esc (utils.js)
//   - showToast, refreshAutoPollUI, refreshListenerBadge, refreshWebhookBadge (render.js)

async function loadAutoPollState(){
  const r=await api('/api/crawl/auto-poll');if(!r)return;
  autoPollState=r;refreshAutoPollUI();
  if(!autoPollTimer)autoPollTimer=setInterval(refreshAutoPollUI,1000);
}

async function loadListenState(){
  const r=await api('/api/listen/status');if(!r)return;
  listenerState=r;refreshListenerBadge();
}

async function loadWebhookState(){
  const r=await api('/api/webhook/config');if(!r)return;
  webhookState=r;refreshWebhookBadge();
}

async function testWebhook(){
  const r=await api('/api/webhook/test',{method:'POST',body:JSON.stringify({})});
  if(!r)return;
  if(r.status==='sent'){
    const ok=(r.results||[]).filter(x=>x.ok).length;
    const total=(r.results||[]).length;
    showToast('测试发送：'+ok+'/'+total+' 端点成功',ok===total?'success':'error');
  }else{
    showToast('无可用 webhook 端点','error');
  }
  loadWebhookState();
}

async function openAutoPollModal(){
  const list=document.getElementById('autoPollList');
  document.getElementById('autoPollModal').style.display='flex';
  list.innerHTML='<div style="padding:16px;text-align:center;color:var(--text-2)">加载中...</div>';
  await loadAutoPollState();
  if(!autoPollState||autoPollState.length===0){list.innerHTML='<div style="padding:16px;text-align:center;color:var(--text-2)">没有已监控的群组</div>';return}
  const fmtInterval=(i)=>i<60?i+'s':(i%60===0?(i/60)+'min':Math.round(i/60*10)/10+'min');
  list.innerHTML=autoPollState.map(s=>{
    return `<div class="modal-item" data-chat-id="${s.chat_id}" style="cursor:default">
      <div class="modal-item-check" style="background:${s.enabled?'var(--cyan)':'transparent'};color:${s.enabled?'var(--bg-0)':'transparent'};border-color:${s.enabled?'var(--cyan)':'var(--border)'}">✓</div>
      <div class="modal-item-info"><div class="modal-item-title">${esc(s.name)}</div><div class="modal-item-sub">${s.enabled?'已启用':'已关闭'}</div></div>
      <label class="toggle-switch" style="margin-right:10px"><input type="checkbox" ${s.enabled?'checked':''} onchange="updateAutoPoll(${s.chat_id},'enabled',this.checked)"><span class="toggle-slider"></span></label>
      <input type="number" class="filter-input" min="5" max="3600" step="1" value="${s.interval_seconds}" style="width:80px;text-align:right" onkeydown="if(event.key==='Enter'){this.blur()}" onblur="updateAutoPoll(${s.chat_id},'interval',this.value)" /><span style="margin-left:4px;color:var(--text-2);font-size:var(--fs-sm)">秒</span>
    </div>`;
  }).join('');
}

function closeAutoPollModal(){document.getElementById('autoPollModal').style.display='none'}

async function updateAutoPoll(chat_id,field,value){
  if(field==='interval'){
    const v=parseInt(value);
    if(!Number.isFinite(v)||v<5||v>3600){
      showToast('间隔必须为 5-3600 秒','error');
      // Reset just the offending input's value without re-rendering the modal (which would steal focus).
      const s=autoPollState.find(x=>x.chat_id===chat_id);
      if(s){
        const modal=document.getElementById('autoPollModal');
        if(modal.style.display==='flex'){
          const inp=modal.querySelector(`.modal-item[data-chat-id="${chat_id}"] input[type="number"]`);
          if(inp)inp.value=s.interval_seconds;
        }
      }
      return
    }
  }
  const body={};body[field==='enabled'?'enabled':'interval_seconds']=field==='enabled'?value===true:parseInt(value);
  const r=await api('/api/crawl/auto-poll/'+chat_id,{method:'PATCH',body:JSON.stringify(body)});
  if(!r||r.error){showToast(r?.error||'更新失败','error');return}
  showToast('已更新','success');
  await loadAutoPollState();
  if(document.getElementById('autoPollModal').style.display==='flex')openAutoPollModal();
}
