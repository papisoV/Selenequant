// groups.js — group picker + chat list functions (Phase 3C)
// Extracted from app.js. Non-module: functions are global.
// Dependencies (resolved at call time):
//   - currentChat, allDialogs (state.js)
//   - api (api-client.js)
//   - esc, fmtChatTime (utils.js)
//   - showToast (render.js)
//   - loadGroupsView (crawl.js), filterChat (messages.js), loadMessages (messages.js)

async function openGroupModal(){
  const list=document.getElementById('groupList');
  list.innerHTML='<div style="padding:16px;text-align:center;color:var(--text-2)">加载中...</div>';
  document.getElementById('groupModal').style.display='flex';
  const r=await api('/api/dialogs');
  if(!r||r.error){list.innerHTML='<div style="padding:16px;text-align:center;color:var(--red)">'+esc(r?.error||'加载失败')+'</div>';return}
  allDialogs=r;const cfg=await api('/api/config');const existingIds=new Set((cfg?.groups||[]).map(g=>g.id||g.username));
  list.innerHTML=r.map(d=>{
    const sel=existingIds.has(d.id)?' selected':'';
    const kind=d.is_channel?'频道':'群组';
    return `<div class="modal-item${sel}" data-id="${d.id}" data-title="${esc(d.title)}" data-username="${esc(d.username||'')}" onclick="this.classList.toggle('selected')">
      <div class="modal-item-check">✓</div>
      <div class="modal-item-info"><div class="modal-item-title">${esc(d.title)}</div>
      <div class="modal-item-sub">${kind}${d.username?' · @'+d.username:''}${d.members?' · '+d.members+'人':''}</div></div></div>`;
  }).join('');
}
function closeGroupModal(){document.getElementById('groupModal').style.display='none'}
async function saveGroups(){
  const items=document.querySelectorAll('#groupList .modal-item.selected');
  // Preserve auto_catchup from existing config
  const cfg=await api('/api/config');
  const existingMap={};
  (cfg?.groups||[]).forEach(g=>{if(g.id)existingMap[g.id]=g.auto_catchup||false});
  const existingIds=new Set((cfg?.groups||[]).map(g=>g.id));
  const groups=Array.from(items).map(el=>{
    const id=parseInt(el.dataset.id);
    return{id,name:el.dataset.title,username:el.dataset.username||undefined,auto_catchup:existingMap[id]||false};
  }).filter(g=>g.id);
  if(!groups.length){showToast('请至少选择一个群组','error');return}
  // Track newly added group ids so we can auto-switch to one after save
  const newIds=groups.map(g=>g.id).filter(id=>!existingIds.has(id));
  const r=await api('/api/config/groups',{method:'PUT',body:JSON.stringify({groups})});
  if(r&&r.status==='updated'){
    closeGroupModal();
    loadGroupsView();
    // Auto-switch to a newly added group so the user immediately sees its messages
    // (rather than being left on page 1 of all-messages where the new group's
    // older messages are buried deep by time-desc sort).
    // filterChat itself calls loadChats + loadMessages + loadSenders, so we
    // don't need to call loadChats separately when switching.
    if(newIds.length>0){
      filterChat(newIds[0]);
      showToast('已添加 '+newIds.length+' 个群组，已切换至最新群组','success');
    }else{
      await loadChats();
      showToast('已保存','success');
    }
  }else showToast('保存失败','error');
}

async function loadChats(){
  const chats=await api('/api/chats');if(!chats)return;
  // Sort by last_msg_date desc (most recently active first), nulls last.
  // Parse as UTC (append 'Z') since DB stores UTC.
  chats.sort((a,b)=>{
    const parseTS=s=>s?new Date((s.includes('T')?s:s.replace(' ','T'))+'Z').getTime():0;
    return parseTS(b.last_msg_date)-parseTS(a.last_msg_date);
  });
  document.getElementById('chatList').innerHTML=chats.map(c=>{
    const typeTag=c.chat_type?`<span style="font-size:10px;color:var(--text-3);margin-left:4px">${c.chat_type==='channel'?'频道':'群组'}</span>`:'';
    const lastTime=fmtChatTime(c.last_msg_date);
    const lastTag=lastTime?`<span style="font-size:10px;color:var(--text-3);margin-left:6px">${lastTime}</span>`:'';
    return `<div class="chat-item${currentChat===c.chat_id?' active':''}" data-chat-id="${c.chat_id}" onclick="filterChat(${c.chat_id})">
      <div class="chat-dot"></div>
      <div class="chat-info"><div class="chat-name">${esc(c.chat_title||'ID:'+c.chat_id)}${typeTag}${lastTag}</div>
      <div class="chat-count">${c.msg_count}</div></div>
      <span class="chat-remove" onclick="event.stopPropagation();removeGroup(${c.chat_id})" title="删除">×</span></div>`;
  }).join('');
}

async function removeGroup(chatId){
  if(!confirm('确定删除该群组及其所有消息数据？'))return;
  const r=await api('/api/config/groups/'+chatId,{method:'DELETE'});
  if(r&&r.status==='removed'){loadChats();loadGroupsView();if(currentChat===chatId){currentChat=null;loadMessages(1)}showToast('已删除 '+r.messages_deleted+' 条消息','success')}
  else showToast(r?.error||'删除失败','error');
}
