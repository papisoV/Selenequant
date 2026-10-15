// messages.js — message list / search / reply functions (Phase 3C)
// Extracted from app.js. Non-module: functions are global.
// Dependencies (resolved at call time):
//   - currentChat, currentPage, pageSize, totalMessages, expandedRow,
//     searchTimer, _msgReqSeq, _msgAbort (state.js)
//   - api (api-client.js)
//   - esc, fmtTime (utils.js)
//   - renderPagination (render.js)
//   - loadChats (groups.js)

function debounceSearch(){clearTimeout(searchTimer);searchTimer=setTimeout(searchMessages,300)}

async function loadSenders(){
  if(!currentChat)return;const senders=await api('/api/senders?chat_id='+currentChat);if(!senders)return;
  const sel=document.getElementById('senderFilter');const cur=sel.value;
  sel.innerHTML='<option value="">全部发送者</option>'+senders.map(s=>`<option value="${s.sender_id}"${s.sender_id==cur?' selected':''}>${esc(s.sender_name||'ID:'+s.sender_id)} (${s.msg_count})</option>`).join('');
}

async function loadMessages(page=1){
  currentPage=page;const seq=++_msgReqSeq;
  if(_msgAbort){_msgAbort.abort();_msgAbort=null}
  const ctrl=new AbortController();_msgAbort=ctrl;
  const params=new URLSearchParams({page,size:pageSize});
  if(currentChat)params.set('chat_id',currentChat);
  const kw=document.getElementById('searchKeyword').value.trim();if(kw)params.set('keyword',kw);
  const sid=document.getElementById('senderFilter').value;if(sid)params.set('sender_id',sid);
  const df=document.getElementById('searchDateFrom').value;if(df)params.set('date_from',df);
  const dt=document.getElementById('searchDateTo').value;if(dt)params.set('date_to',dt);
  const data=await api('/api/messages?'+params,{signal:ctrl.signal});
  if(!data)return;
  if(seq!==_msgReqSeq)return; // stale response, discard
  totalMessages=data.total||0;const msgs=data.messages||[];
  const tbody=document.getElementById('msgBody');
  if(!msgs.length){tbody.innerHTML='<tr><td colspan="4" style="text-align:center;padding:40px;color:var(--text-3)">暂无消息。请添加群组并开始爬取。</td></tr>'}
  else{tbody.innerHTML=msgs.map(m=>{
    const t=esc(m.text||'');const ts=t.length>80?t.slice(0,80)+'...':t;
    const fullDate=fmtTime(m.date);
    const today=new Date();const todayStr=today.getFullYear()+'-'+String(today.getMonth()+1).padStart(2,'0')+'-'+String(today.getDate()).padStart(2,'0');
    const msgDay=m.date?fmtTime(m.date).slice(0,10):'';
    const displayDate=msgDay===todayStr?fullDate.slice(11):fullDate;
    const med=m.media_type?{'photo':'图','video':'视频','document':'文档','sticker':'贴纸','audio':'音频','voice':'语音','webpage':'链接','contact':'联系人'}[m.media_type]||'媒体':(m.has_media?'文件':'');
    const medTag=med?`<span class="col-media">${med}</span>`:'';
    const editTag=m.is_edited?'<span style="color:var(--text-3);font-size:10px;margin-right:3px">已编辑</span>':'';
    const reply=m.reply_to_msg_id?`<span class="col-reply" onclick="event.stopPropagation();loadReply(${m.reply_to_msg_id},this)">↩${m.reply_to_msg_id}</span> `:'';
    const fwd=m.forward_from?`<span class="col-forward">转发: ${esc(m.forward_from)}</span> `:'';
    const chatTag=!currentChat&&m.chat_title?`<span class="col-chat-badge">${esc(m.chat_title)}</span> `:'';
    return `<tr data-id="${m.id}" onclick="toggleRow(this)">
      <td class="col-time" title="${fullDate}">${displayDate}</td>
      <td class="col-sender">${esc(m.sender_name||m.sender_username||'-')}</td>
      <td class="col-content">${chatTag}${editTag}${reply}${fwd}${ts}</td>
      <td>${medTag}</td></tr>`;
  }).join('')}
  renderPagination(data.total||0,data.page||1,data.page_size||pageSize);
}

function toggleRow(tr){
  if(expandedRow===tr.dataset.id){tr.classList.remove('expanded');const f=tr.querySelector('.col-content-full');if(f)f.remove();const t=tr.querySelector('.col-content');if(t)t.style.display='';expandedRow=null;return}
  document.querySelectorAll('tr.expanded').forEach(r=>{r.classList.remove('expanded');const f=r.querySelector('.col-content-full');if(f)f.remove();const t=r.querySelector('.col-content');if(t)t.style.display=''});
  tr.classList.add('expanded');expandedRow=tr.dataset.id;
  const textEl=tr.querySelector('.col-content');const fullText=textEl.innerText;
  const div=document.createElement('div');div.className='col-content-full';div.textContent=fullText;
  textEl.style.display='none';textEl.parentNode.appendChild(div);
}

async function loadReply(msgId,el){
  const r=await api('/api/messages/'+msgId+'/reply');
  if(!r||r.error){el.textContent='未找到';return}
  const div=document.createElement('div');
  div.style.cssText='margin-top:4px;padding:6px;background:var(--bg-0);border:1px solid var(--border);font-size:var(--fs-xs);line-height:1.5;color:var(--text-1);max-width:400px';
  div.innerHTML='<b>'+esc(r.sender_name||'?')+'</b> <span style="color:var(--text-3)">'+fmtTime(r.date)+'</span><br>'+esc(r.text||'').slice(0,300);
  el.after(div);el.onclick=()=>div.remove();
}

function filterChat(chatId){
  currentChat=chatId;currentPage=1;
  document.querySelectorAll('.chat-all-item,.chat-item').forEach(el=>el.classList.remove('active'));
  if(chatId===null)document.querySelector('.chat-all-item').classList.add('active');
  else document.querySelectorAll('.chat-item').forEach(el=>{if(el.dataset.chatId==chatId)el.classList.add('active')});
  loadChats();loadMessages(1);loadSenders();
}
function searchMessages(){currentPage=1;loadMessages(1)}
function clearSearch(){document.getElementById('searchKeyword').value='';document.getElementById('senderFilter').value='';document.getElementById('searchDateFrom').value='';document.getElementById('searchDateTo').value='';searchMessages()}
