// ui.js — panel toggle, theme, connection check, init (Phase 3C)
// Extracted from app.js. Non-module: functions are global.
// Dependencies (resolved at call time):
//   - _connCheckPending, authToken (state.js)
//   - api (api-client.js)
//   - showToast (render.js)
//   - loadChats (groups.js), loadMessages (messages.js),
//     loadCrawlStatus (crawl.js), connectSSE (sse.js),
//     loadTrendChart, loadHeatmap, loadComparisonChart (dashboard.js)

function togglePanel(){
  const p=document.getElementById('leftPanel');p.classList.toggle('collapsed');
  const btn=p.querySelector('.panel-toggle');btn.textContent=p.classList.contains('collapsed')?'›':'‹';
}

function toggleTheme(){
  const html=document.documentElement;const isLight=html.getAttribute('data-theme')==='light';
  html.setAttribute('data-theme',isLight?'':'light');
  localStorage.setItem('tgwatcher_theme',isLight?'dark':'light');
  const txt=isLight?'☀':'🌙';
  document.getElementById('themeBtn').textContent=txt;
  const tb2=document.getElementById('themeBtn2');if(tb2)tb2.textContent=txt;
  if(document.getElementById('viewDashboard').classList.contains('active')){loadTrendChart();loadHeatmap();loadComparisonChart()}
}
(function(){const t=localStorage.getItem('tgwatcher_theme');if(t==='light'){document.documentElement.setAttribute('data-theme','light');document.addEventListener('DOMContentLoaded',()=>{document.getElementById('themeBtn').textContent='🌙';const tb2=document.getElementById('themeBtn2');if(tb2)tb2.textContent='🌙'})}})();

async function checkConnection(){
  if(_connCheckPending)return;_connCheckPending=true;
  const connDot=document.getElementById('connDot');
  try{const r=await api('/api/login/status');
    if(r&&r.logged_in)connDot.className='conn-dot connected';
    else connDot.className='conn-dot';
  }catch(e){connDot.className='conn-dot'}
  finally{_connCheckPending=false}
}

async function initApp(){
  if(typeof Chart==='undefined')showToast('Chart.js 加载失败 — 仪表盘不可用','error');
  await loadChats();await loadMessages(1);await loadCrawlStatus();
  checkConnection();connectSSE();
}
