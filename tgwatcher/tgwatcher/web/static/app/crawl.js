// crawl.js — groups view + crawl control (Phase 3C)
// Extracted from app.js. Non-module: functions are global.
// Dependencies (resolved at call time):
//   - crawlRunning, autoPollState (state.js)
//   - api (api-client.js)
//   - esc, fmtTime, _localDateStr (utils.js / export.js)
//   - showToast, updateCrawlUI, updateCrawlDetail (render.js)
//   - loadChats, loadMessages, removeGroup (groups.js / messages.js)

async function loadGroupsView(){
  const chats=await api('/api/chats');if(!chats)return;
  document.getElementById('groupsBody').innerHTML=chats.map(c=>{
    const checked=c.auto_catchup?'checked':'';
    const listenChecked=c.auto_listen?'checked':'';
    return `<tr>
    <td style="font-weight:500">${esc(c.chat_title||'ID:'+c.chat_id)}</td>
    <td style="font-family:var(--font-mono);font-size:var(--fs-xs);color:var(--text-2)">${c.chat_type==='channel'?'频道':c.chat_type||'-'}</td>
    <td class="col-num">${c.members||'-'}</td>
    <td class="col-num">${c.msg_count}</td>
    <td class="col-time">${fmtTime(c.last_msg_date)}</td>
    <td><label class="toggle-switch"><input type="checkbox" ${checked} onchange="toggleAutoCatchup(${c.chat_id},this.checked)"><span class="toggle-slider"></span></label></td>
    <td><label class="toggle-switch"><input type="checkbox" ${listenChecked} onchange="toggleAutoListen(${c.chat_id},this.checked)"><span class="toggle-slider"></span></label></td>
    <td><button class="btn btn-danger" style="font-size:var(--fs-xs);padding:1px 6px" onclick="removeGroup(${c.chat_id})">✕</button></td>
  </tr>`}).join('');
}

async function toggleAutoCatchup(chatId,enabled){
  const r=await api('/api/config/groups/'+chatId+'/auto_catchup',{method:'PATCH',body:JSON.stringify({auto_catchup:enabled})});
  if(!r||r.error){showToast(r?.error||'更新失败','error');loadGroupsView()}
  else showToast(enabled?'已启用自动补爬':'已关闭自动补爬','success');
}

async function toggleAutoListen(chatId,enabled){
  const r=await api('/api/config/groups/'+chatId+'/auto_listen',{method:'PATCH',body:JSON.stringify({auto_listen:enabled})});
  if(!r||r.error){showToast(r?.error||'更新失败','error');loadGroupsView();return}
  if(enabled){
    showToast(r.listener_running?'已启用实时监听（listener 已在运行）':'已启用实时监听，listener 启动中...','success');
  }else{
    showToast('已关闭实时监听','success');
  }
}

async function purgeAllData(){
  if(!confirm('确定清空所有消息数据？此操作不可恢复！'))return;
  const r=await api('/api/data/purge',{method:'POST'});
  if(r&&r.status==='purged'){showToast('已清空 '+r.messages_deleted+' 条消息','success');loadChats();loadMessages(1);loadGroupsView()}
  else showToast('清空失败','error');
}

function toggleCrawlMenu(){document.getElementById('crawlMenu').classList.toggle('show')}
document.addEventListener('click',e=>{if(!e.target.closest('.crawl-dd'))document.getElementById('crawlMenu').classList.remove('show')});

async function startCrawl(mode){
  document.getElementById('crawlMenu').classList.remove('show');
  if(mode==='date_range'){showDateRangePanel();return}
  const r=await api('/api/crawl/start',{method:'POST',body:JSON.stringify({mode})});
  if(r&&r.status==='started'){loadCrawlStatus();document.getElementById('crawlDetail').style.display='block';loadCrawlStatus()}
  else showToast(r?.error||'启动失败','error');
}

function showDateRangePanel(){
  document.getElementById('crawlMenu').classList.remove('show');
  const panel=document.getElementById('crawlDatePanel');
  panel.style.display='flex';
  const to=new Date();const from=new Date();from.setDate(from.getDate()-7);
  document.getElementById('crawlDateTo').value=_localDateStr(to);
  document.getElementById('crawlDateFrom').value=_localDateStr(from);
}

function hideDateRangePanel(){
  document.getElementById('crawlDatePanel').style.display='none';
}

async function startCrawlDateRange(){
  const from=document.getElementById('crawlDateFrom').value;
  const to=document.getElementById('crawlDateTo').value;
  if(!from||!to){showToast('请选择起止日期','error');return}
  if(from>to){showToast('起始日期不能晚于结束日期','error');return}
  const r=await api('/api/crawl/start',{method:'POST',body:JSON.stringify({mode:'date_range',offset_date:from,until_date:to})});
  if(r&&r.status==='started'){hideDateRangePanel();document.getElementById('crawlDetail').style.display='block';loadCrawlStatus()}else showToast(r?.error||'启动失败','error');
}

async function stopCrawl(){
  await api('/api/crawl/stop',{method:'POST'});
  showToast('正在停止爬取...','info');
  for(let i=0;i<10;i++){
    await new Promise(r=>setTimeout(r,1000));
    const s=await api('/api/crawl/status');
    if(s&&!s.running){updateCrawlUI(s);return}
  }
  loadCrawlStatus();
}

async function loadCrawlStatus(){const s=await api('/api/crawl/status');if(!s)return;updateCrawlUI(s)}

function toggleCrawlDetail(){
  const dp=document.getElementById('crawlDetail');
  const show=dp.style.display==='none';
  dp.style.display=show?'block':'none';
  if(show){
    const s=crawlRunning?null:null;
    loadCrawlStatus();
  }
}
